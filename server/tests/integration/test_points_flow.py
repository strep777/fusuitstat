"""积分 / 反馈 / 通知的端到端 HTTP 流程（本人数据、幂等、申诉、奖励入账）。

走真实装配的应用（``app.main.create_app``）、真实会话 Cookie 与 CSRF：
写请求额外带受信 Origin（10 文档要求写请求同时校验 Origin/Referer）。
"""

from __future__ import annotations

import contextlib
import pathlib
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from app.modules.feedback import service as feedback_service
from app.modules.feedback.models import Feedback
from app.modules.identity.models import User
from app.modules.loyalty import ports as loyalty_ports
from app.modules.loyalty import repository as loyalty_repo
from app.modules.loyalty import service as loyalty_service
from app.modules.loyalty.models import PointAward, PointLedger
from app.platform import db as platform_db
from app.platform.config import get_settings
from app.platform.ids import new_id, now_ms
from app.platform.reliability import enqueue
from app.platform.security import encrypt_pii, mask_phone, normalize_phone, phone_lookup

TEST_ORIGIN = "http://127.0.0.1:5173"
SERVER_VAR = pathlib.Path(__file__).resolve().parents[2] / "var"
DAY_MS = 86_400_000

_MODEL_MODULES = (
    "app.platform.reliability",
    "app.modules.identity.models",
    "app.modules.media.models",
    "app.modules.notifications.models",
    "app.modules.content.models",
    "app.modules.catalog.models",
    "app.modules.loyalty.models",
    "app.modules.feedback.models",
)


@pytest.fixture()
def env(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """独立测试库：只新建库文件，不新建目录（本会话沙箱对新建目录有限制）。"""
    import importlib

    SERVER_VAR.mkdir(parents=True, exist_ok=True)
    database = SERVER_VAR / f"test-flow-{uuid.uuid4().hex}.db"
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATA_DIR", str(SERVER_VAR))
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{database.as_posix()}")
    monkeypatch.setenv("PUBLIC_ORIGIN", TEST_ORIGIN)
    monkeypatch.setenv("SQLITE_SYNCHRONOUS", "NORMAL")
    get_settings.cache_clear()
    platform_db.reset_engine()
    for name in _MODEL_MODULES:
        importlib.import_module(name)
    platform_db.init_database()
    try:
        yield get_settings()
    finally:
        platform_db.reset_engine()
        get_settings.cache_clear()
        for suffix in ("", "-wal", "-shm"):
            with contextlib.suppress(PermissionError):
                pathlib.Path(f"{database}{suffix}").unlink(missing_ok=True)


@pytest.fixture()
def client(env: Any) -> Iterator[TestClient]:
    from app.main import create_app

    with TestClient(
        create_app(get_settings()),
        base_url="http://testserver",
        headers={"Origin": TEST_ORIGIN},
    ) as test_client:
        yield test_client


@pytest.fixture()
def db(env: Any) -> Iterator[Any]:
    session = platform_db.get_session_factory()()
    try:
        yield session
    finally:
        session.close()


class FakeCommerce:
    """商务快照替身（键与 commerce.ports 一致），用于制造可发放的订单奖励。"""

    def __init__(self) -> None:
        self.orders: dict[str, dict[str, Any]] = {}

    def add_delivered(self, order_id: str, *, user_id: str, goods_cents: int) -> None:
        from app.platform.ids import iso

        completed_at = now_ms() - 8 * DAY_MS  # 交付已满 7 天，可直接入账
        self.orders[order_id] = {
            "order_id": order_id,
            "user_id": user_id,
            "status": "COMPLETED",
            "currency": "CNY",
            "goods_cents": goods_cents,
            "shipping_cents": 2_000,
            "discount_cents": 0,
            "total_cents": goods_cents + 2_000,
            "paid_goods_cents": goods_cents,
            "paid_shipping_cents": 2_000,
            "refunded_goods_cents": 0,
            "refunded_shipping_cents": 0,
            "eligible_net_cents": goods_cents,
            "completed_at": iso(completed_at),
            "has_open_after_sales": False,
        }

    def snapshot(self, session: Any, order_id: str) -> dict[str, Any]:
        from app.platform.errors import AppError

        row = self.orders.get(order_id)
        if row is None:
            raise AppError("RESOURCE_NOT_FOUND")
        return dict(row)


@pytest.fixture()
def commerce(monkeypatch: pytest.MonkeyPatch) -> FakeCommerce:
    fake = FakeCommerce()
    monkeypatch.setattr(loyalty_ports, "order_snapshot", fake.snapshot)
    return fake


@pytest.fixture()
def login(client: TestClient) -> Callable[..., dict[str, str]]:
    """短信验证码登录（测试环境返回 dev_hint），返回 user_id 与 CSRF token。"""

    def _login(phone: str = "+8613800000001") -> dict[str, str]:
        anonymous = client.get("/api/v1/session/csrf").json()["csrf_token"]
        challenge = client.post(
            "/api/v1/auth/sms/challenges",
            json={"phone": phone, "purpose": "login"},
            headers={"X-CSRF-Token": anonymous},
        )
        assert challenge.status_code == 200, challenge.text
        payload = challenge.json()
        verify = client.post(
            "/api/v1/auth/sms/verify",
            json={
                "challenge_id": payload["challenge_id"],
                "phone": phone,
                "code": payload["dev_hint"],
                "terms_version": "terms-1",
                "privacy_version": "privacy-1",
                "accepted": True,
            },
        )
        assert verify.status_code == 200, verify.text
        body = verify.json()
        return {"user_id": body["user"]["id"], "csrf_token": body["csrf_token"], "phone": phone}

    return _login


def write_headers(csrf_token: str, *, idempotency_key: str | None = None) -> dict[str, str]:
    headers = {"X-CSRF-Token": csrf_token, "Origin": TEST_ORIGIN}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    return headers


def feedback_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "category": "bug",
        "title": "结算页优惠券无法使用",
        "body": "输入优惠券后提示无效，重复三次都一样，附上截图，希望尽快修复。",
        "page_path": "/checkout?token=secret-value&step=2",
    }
    payload.update(overrides)
    return payload


def make_user(session: Any, *, phone: str = "13800000009", nickname: str = "另一位星友") -> str:
    normalized = normalize_phone(phone)
    user = User(
        id=new_id("user"),
        phone_ciphertext=encrypt_pii(normalized),
        phone_lookup=phone_lookup(normalized),
        phone_masked=mask_phone(normalized),
        nickname=nickname,
    )
    session.add(user)
    session.flush()
    return user.id


def test_points_and_feedback_flow_end_to_end(
    client: TestClient, db: Any, login: Callable[..., dict[str, str]], commerce: FakeCommerce
) -> None:
    # 1) 未登录：本人数据一律 401
    anonymous = client.get("/api/v1/points/account")
    assert anonymous.status_code == 401
    assert anonymous.json()["error"]["code"] == "AUTH_REQUIRED"

    account = login()
    user_id = account["user_id"]
    csrf = account["csrf_token"]

    # 2) 提交反馈：201 + 只有编号/状态/奖励状态，带幂等键重放返回同一条
    created = client.post(
        "/api/v1/feedback",
        json=feedback_payload(),
        headers=write_headers(csrf, idempotency_key="fbk-key-1"),
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert set(body) == {"id", "status", "reward_status"}
    assert body["status"] == "SUBMITTED" and body["reward_status"] == "PENDING_REVIEW"

    replayed = client.post(
        "/api/v1/feedback",
        json=feedback_payload(),
        headers=write_headers(csrf, idempotency_key="fbk-key-1"),
    )
    assert replayed.status_code == 201
    assert replayed.json()["id"] == body["id"]
    db.expire_all()
    assert db.scalar(sa.select(sa.func.count()).select_from(Feedback)) == 1

    # 3) 详情：query 敏感值被丢弃，奖励状态可见，申诉入口存在
    detail = client.get(f"/api/v1/feedback/{body['id']}")
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["page_path"] == "/checkout"
    assert payload["reward_status"] == "PENDING_REVIEW"
    assert payload["appeal_endpoint"].endswith(f"/feedback/{body['id']}/appeals")
    assert payload["messages"] == []

    # 4) 补充说明 + 申诉
    message = client.post(
        f"/api/v1/feedback/{body['id']}/messages",
        json={"body": "补充：仅在 Chrome 复现，控制台有报错。"},
        headers=write_headers(csrf),
    )
    assert message.status_code == 201, message.text
    assert message.json()["visibility"] == "user"
    appeal = client.post(
        f"/api/v1/feedback/{body['id']}/appeals",
        json={"reason": "补充了新的复现视频，请复核奖励结论是否合理。"},
        headers=write_headers(csrf),
    )
    assert appeal.status_code == 201, appeal.text
    assert appeal.json()["status"] == "SUBMITTED"

    # 5) 订单奖励通过 worker 路径入账（商务只登记事件，积分模块重算）
    unit = loyalty_service.purchase_policy(db)["denominator_yuan"] * 100
    commerce.add_delivered("ord_flow", user_id=user_id, goods_cents=unit * 200)
    enqueue(
        db,
        event_type="points.award_due",
        aggregate_id="ord_flow",
        payload={"order_id": "ord_flow", "user_id": user_id, "reason": "installment_paid"},
    )
    db.commit()
    assert loyalty_service.post_due_awards(db, now=now_ms()) == 1
    db.commit()
    assert db.scalar(sa.select(sa.func.count()).select_from(PointAward)) == 1

    # 6) 账户与流水接口
    points = client.get("/api/v1/points/account")
    assert points.status_code == 200
    assert set(points.json()) == {
        "posted_balance",
        "reserved_points",
        "available_points",
        "pending_points",
        "rules_version",
    }
    assert points.json()["posted_balance"] == 200
    assert points.json()["available_points"] == 200
    assert points.json()["pending_points"] == 0

    ledger = client.get("/api/v1/points/ledger")
    assert ledger.status_code == 200
    page = ledger.json()
    assert page["has_more"] is False and page["next_cursor"] is None
    assert len(page["items"]) == 1
    entry = page["items"][0]
    assert entry["delta"] == 200
    assert entry["description"]
    assert entry["source"]["type"] == "order" and entry["source"]["id"] == "ord_flow"
    assert entry["occurred_at"] and entry["appeal"]["endpoint"].endswith("/points/appeals")
    assert entry["adjustment_id"] is None

    # 7) 流水申诉：本人可提，重复申诉返回同一条
    appeal_points = client.post(
        "/api/v1/points/appeals",
        json={"ledger_id": entry["id"], "reason": "这笔奖励的金额与订单实付不一致，请复核。"},
        headers=write_headers(csrf),
    )
    assert appeal_points.status_code == 201, appeal_points.text
    assert appeal_points.json()["status"] == "SUBMITTED"
    assert appeal_points.json()["ledger_id"] == entry["id"]

    # 8) 审核反馈并发奖（后台命令；路由在 admin_routes.py）
    reviewed = feedback_service.review_feedback(
        db,
        feedback_id=body["id"],
        reviewer_id="stf_reviewer",
        result="RESOLVED",
        reason="已确认并修复，奖励 20 星绒币。",
        reward_level="normal",
        criteria="有完整复现步骤与截图，属有效可复现缺陷。",
    )
    db.commit()
    assert reviewed["reward_status"] == "GRANTED" and reviewed["reward_points"] == 20

    refreshed = client.get("/api/v1/points/account").json()
    assert refreshed["posted_balance"] == 220
    detail_after = client.get(f"/api/v1/feedback/{body['id']}").json()
    assert detail_after["reward_status"] == "GRANTED"
    assert detail_after["reward_points"] == 20
    assert detail_after["messages"][0]["visibility"] == "user"

    # 9) 通知：分类筛选、单条已读、全部已读
    notifications = client.get("/api/v1/notifications", params={"limit": 50})
    assert notifications.status_code == 200
    listing = notifications.json()
    assert listing["unread_count"] >= 2
    assert all(item["status"] == "UNREAD" for item in listing["items"])
    filtered = client.get("/api/v1/notifications", params={"category": "loyalty"})
    assert filtered.status_code == 200
    assert all(item["category"] == "loyalty" for item in filtered.json()["items"])

    first_id = listing["items"][0]["id"]
    read = client.post(f"/api/v1/notifications/{first_id}/read", headers=write_headers(csrf))
    assert read.status_code == 200, read.text
    assert read.json()["unread_count"] == listing["unread_count"] - 1
    read_all = client.post("/api/v1/notifications/read-all", headers=write_headers(csrf))
    assert read_all.status_code == 200
    assert read_all.json()["unread_count"] == 0
    assert client.get("/api/v1/notifications").json()["unread_count"] == 0

    # 10) 账本与账户缓存一致（积分是钱：缓存必须可被账本重算）
    consistency = loyalty_service.verify_account_consistency(db, user_id=user_id)
    assert consistency["consistent"] is True
    assert consistency["ledger_total"] == 220
    assert len(list(db.scalars(sa.select(PointLedger)))) == 2


def test_other_user_data_is_invisible(
    client: TestClient, db: Any, login: Callable[..., dict[str, str]], commerce: FakeCommerce
) -> None:
    """按 ID 查询必须带 user_id 过滤：非本人一律 404，且不泄露内容。"""
    first = login("+8613800000001")
    first_feedback = client.post(
        "/api/v1/feedback", json=feedback_payload(), headers=write_headers(first["csrf_token"])
    ).json()
    second = login("+8613800000002")  # 换号登录会覆盖会话 cookie

    detail = client.get(f"/api/v1/feedback/{first_feedback['id']}")
    assert detail.status_code == 404
    assert detail.json()["error"]["code"] == "RESOURCE_NOT_FOUND"
    listing = client.get("/api/v1/feedback").json()
    assert listing["items"] == []

    ledger_id = new_id("ledger")
    db.add(
        PointLedger(
            id=ledger_id,
            account_id=loyalty_repo.ensure_account(db, user_id=first["user_id"]).id,
            user_id=first["user_id"],
            delta=10,
            entry_type="ADJUSTMENT",
            source_type="adjustment",
            source_id="pad_other",
            source_revision=1,
            rule_version="points-test",
            description="后台调整：他人流水",
            label="调整单 pad_other",
        )
    )
    db.commit()
    assert client.get("/api/v1/points/ledger").json()["items"] == []
    appeal = client.post(
        "/api/v1/points/appeals",
        json={"ledger_id": ledger_id, "reason": "这不是我的流水，不应该能申诉。"},
        headers=write_headers(second["csrf_token"]),
    )
    assert appeal.status_code == 404
    my_account = client.get("/api/v1/points/account").json()
    assert my_account["posted_balance"] == 0, "他人流水不得影响本人账户"
    assert client.post(
        "/api/v1/notifications/ntf_not_mine/read", headers=write_headers(second["csrf_token"])
    ).status_code == 404


def test_feedback_validation_and_duplicate_over_http(
    client: TestClient, login: Callable[..., dict[str, str]]
) -> None:
    account = login("+8613800000003")
    csrf = account["csrf_token"]

    too_long = client.post(
        "/api/v1/feedback",
        json=feedback_payload(title="长" * 101),
        headers=write_headers(csrf),
    )
    assert too_long.status_code == 422
    assert too_long.json()["error"]["code"] == "VALIDATION_ERROR"

    too_short = client.post(
        "/api/v1/feedback", json=feedback_payload(body="太短了"), headers=write_headers(csrf)
    )
    assert too_short.status_code == 422

    bad_category = client.post(
        "/api/v1/feedback", json=feedback_payload(category="praise"), headers=write_headers(csrf)
    )
    assert bad_category.status_code == 422

    absolute_path = client.post(
        "/api/v1/feedback",
        json=feedback_payload(page_path="https://evil.example/phish"),
        headers=write_headers(csrf),
    )
    assert absolute_path.status_code == 422

    ok = client.post("/api/v1/feedback", json=feedback_payload(), headers=write_headers(csrf))
    assert ok.status_code == 201
    duplicate = client.post(
        "/api/v1/feedback",
        json=feedback_payload(title="结算页优惠券无法使用"),
        headers=write_headers(csrf),
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "FEEDBACK_DUPLICATE"


def test_feedback_hourly_limit_over_http(
    client: TestClient, login: Callable[..., dict[str, str]]
) -> None:
    """每小时的提交上限（默认 3 条）：超限返回 429 FEEDBACK_LIMIT_EXCEEDED。"""
    csrf = login("+8613800000004")["csrf_token"]
    for index, path in enumerate(("/orders", "/cart", "/account")):
        created = client.post(
            "/api/v1/feedback",
            json=feedback_payload(title=f"第 {index} 个问题", page_path=path),
            headers=write_headers(csrf),
        )
        assert created.status_code == 201, created.text

    limited = client.post(
        "/api/v1/feedback",
        json=feedback_payload(title="第四个问题", page_path="/profile"),
        headers=write_headers(csrf),
    )
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "FEEDBACK_LIMIT_EXCEEDED"
