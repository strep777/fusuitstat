"""委托模块集成测试：真实 HTTP 契约 + 真实 SQLite 文件库 + 真实跨模块端口。

覆盖 13 文档用例：TC-C01（草稿版本、重复提交、终态）、TC-C02（过期/旧版报价、
两标签只生成一个订单）、TC-C04（两人争最后名额）、TC-C05（旧版阶段确认 409）、
TC-A04（用户 A 读用户 B 的询价/制作单/阶段返回 404）、公开排期不泄露身份信息。

应用装配走 ``app.main.create_app``（真实路由与错误形状）；商务模块由另一位工程师
并行交付，本文件在需要隔离时用 ``sys.modules`` 替身，其余用例直接使用真实商务端口，
以验证跨模块签名一致。

环境说明：本文件自带 ``commission_env`` 夹具，用默认权限在
``server/.pytest-commissions/`` 下创建独立数据目录与独立 SQLite 文件，并执行
``get_settings.cache_clear()`` / ``reset_engine()`` / ``init_database()``，
隔离语义与 ``tests/conftest.py`` 的 ``settings`` 夹具一致，但不依赖共享的
``tmp_path`` 基准目录（该基准目录在本会话的受限文件沙箱下曾不可访问）。
"""

from __future__ import annotations

import os
import shutil
import sys
import types
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

SERVER_DIR = Path(__file__).resolve().parents[2]
ORIGIN = "http://127.0.0.1:5173"
SESSION_COOKIE = "dsh_session"

os.environ["APP_ENV"] = "test"
os.environ["PUBLIC_ORIGIN"] = ORIGIN

from app.modules.commissions import service as svc  # noqa: E402
from app.modules.commissions.models import (  # noqa: E402
    ChangeRequest,
    CommissionProject,
    Quote,
    ScheduleApplication,
    ScheduleRound,
    SlotReservation,
)
from app.modules.commissions.schemas import (  # noqa: E402
    InquiryCreate,
    QuoteLineItemInput,
    QuotePublishInput,
    ScheduleApplicationInput,
    ScheduleRoundInput,
)
from app.modules.content.models import ContentEntry  # noqa: E402
from app.modules.identity.models import User  # noqa: E402
from app.modules.identity.service import issue_session  # noqa: E402
from app.modules.media.models import MediaAsset  # noqa: E402
from app.platform.config import get_settings  # noqa: E402
from app.platform.db import get_session_factory, init_database, reset_engine  # noqa: E402
from app.platform.errors import AppError  # noqa: E402
from app.platform.ids import add_days_ms, iso, new_id, now_ms  # noqa: E402
from app.platform.security import encrypt_pii, phone_lookup  # noqa: E402

STAFF_ID = "stf_integration_staff"
_PHONE_SEQ = {"value": 0}
BASE = get_settings().api_prefix


# ------------------------------------------------------------------ 环境与应用
@pytest.fixture
def commission_env() -> Iterator[Path]:
    root = SERVER_DIR / ".pytest-commissions" / uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=True)
    os.environ["DATA_DIR"] = str(root)
    os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{(root / 'commissions-http.db').as_posix()}"
    os.environ["SQLITE_SYNCHRONOUS"] = "NORMAL"
    get_settings.cache_clear()
    reset_engine()
    init_database()
    try:
        yield root
    finally:
        reset_engine()
        get_settings.cache_clear()
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def app(commission_env: Path) -> Any:
    from app.main import create_app

    return create_app(get_settings())


@pytest.fixture
def client(app: Any) -> Iterator[Any]:
    from fastapi.testclient import TestClient

    with TestClient(app, base_url=ORIGIN) as test_client:
        yield test_client


@pytest.fixture
def db(commission_env: Path) -> Iterator[Any]:
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def payments_stub(monkeypatch: pytest.MonkeyPatch, db: Any) -> dict[str, Any]:
    """支付端口替身：用实收汇总控制“尾款是否结清”。"""
    state: dict[str, Any] = {"paid_cents": 0, "refunded_applied_cents": 0}

    def order_paid_summary(session: Any, *, order_id: str) -> dict[str, Any]:
        return {
            "paid_cents": state["paid_cents"],
            "refunded_applied_cents": state["refunded_applied_cents"],
            "allocations": [],
        }

    package = types.ModuleType("app.modules.payments")
    package.__path__ = []  # type: ignore[attr-defined]
    ports = types.ModuleType("app.modules.payments.ports")
    ports.order_paid_summary = order_paid_summary  # type: ignore[attr-defined]
    package.ports = ports  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "app.modules.payments", package)
    monkeypatch.setitem(sys.modules, "app.modules.payments.ports", ports)
    return state


# ------------------------------------------------------------------ 主体与数据
@dataclass
class Actor:
    user_id: str
    token: str
    csrf: str

    def headers(self, **extra: str) -> dict[str, str]:
        headers = {
            "Origin": ORIGIN,
            "Cookie": f"{SESSION_COOKIE}={self.token}",
            "X-CSRF-Token": self.csrf,
        }
        headers.update(extra)
        return headers


def login(db: Any, nickname: str = "星友") -> Actor:
    _PHONE_SEQ["value"] += 1
    phone = f"+8613902{_PHONE_SEQ['value']:06d}"
    user = User(
        id=new_id("user"),
        phone_ciphertext=encrypt_pii(phone),
        phone_lookup=phone_lookup(phone),
        phone_masked="+8613****0000",
        nickname=nickname,
    )
    db.add(user)
    db.flush()
    _row, token, csrf = issue_session(db, user=user, client_ip="127.0.0.1")
    db.commit()
    return Actor(user_id=user.id, token=token, csrf=csrf)


def make_series(db: Any) -> str:
    entry = ContentEntry(
        id=new_id("content"),
        kind="series",
        slug=f"series-{uuid.uuid4().hex[:8]}",
        title="测试系列",
        status="published",
        publish_at=now_ms(),
    )
    db.add(entry)
    db.commit()
    return entry.id


def make_asset(db: Any, owner_id: str) -> str:
    asset = MediaAsset(
        id=new_id("asset"),
        owner_id=owner_id,
        purpose="design",
        visibility="private",
        object_key=f"design/{uuid.uuid4().hex}.png",
        mime="image/png",
        scan_status="ready",
    )
    db.add(asset)
    db.commit()
    return asset.id


def inquiry_body(series_id: str, asset_id: str, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "character_name": "逐星绒龙",
        "species": "龙",
        "series_id": series_id,
        "suit_type": "full",
        "design_asset_ids": [asset_id],
        "requirements": "需要可拆卸尾巴。",
        "budget_min_cents": 300000,
        "budget_max_cents": 600000,
        "rights_declaration": True,
    }
    body.update(overrides)
    return body


def create_submitted_inquiry(client: Any, db: Any, actor: Actor, series_id: str, asset_id: str) -> str:
    created = client.post(
        f"{BASE}/inquiries", json=inquiry_body(series_id, asset_id), headers=actor.headers()
    )
    assert created.status_code == 201, created.text
    inquiry_id = created.json()["id"]
    submitted = client.post(f"{BASE}/inquiries/{inquiry_id}/submit", headers=actor.headers())
    assert submitted.status_code == 200, submitted.text
    return inquiry_id


def publish_quote(db: Any, *, inquiry_id: str, total_cents: int = 500000) -> Quote:
    quote = svc.publish_quote(
        db,
        inquiry_id=inquiry_id,
        actor_id=STAFF_ID,
        payload=QuotePublishInput(
            line_items=[QuoteLineItemInput(label="全装制作", quantity=1, unit_price_cents=total_cents)],
            expires_at=iso(add_days_ms(7)),
        ),
    )
    db.commit()
    return quote


def make_round(db: Any, *, capacity: int, **kw: Any) -> ScheduleRound:
    row = svc.create_schedule_round(
        db,
        actor_id=STAFF_ID,
        payload=ScheduleRoundInput(
            title="2026 春季档期",
            start_at=iso(add_days_ms(5)),
            end_at=iso(add_days_ms(35)),
            capacity=capacity,
            stage_label="全装档期",
            **kw,
        ),
    )
    db.commit()
    return row


def select_application(db: Any, *, round_row: ScheduleRound, user_id: str, inquiry_id: str) -> None:
    application = svc.apply_to_round(
        db,
        user_id=user_id,
        round_id=round_row.id,
        payload=ScheduleApplicationInput(inquiry_id=inquiry_id),
    )
    svc.decide_application(
        db, application_id=application.id, actor_id=STAFF_ID, decision="SELECTED", reason="筛选通过"
    )
    db.commit()


def error_code(response: Any) -> str:
    return response.json()["error"]["code"]


def fresh(db: Any) -> Any:
    """丢弃本会话缓存，读到其他连接（HTTP 请求）已提交的数据。"""
    db.rollback()
    db.expire_all()
    return db


def accept_payload(quote: Quote, **overrides: Any) -> dict[str, Any]:
    body = {
        "quote_version": quote.quote_version,
        "terms_version": quote.terms_version,
        "accepted": True,
    }
    body.update(overrides)
    return body


# ------------------------------------------------------------------ TC-C01
def test_inquiry_http_flow_is_versioned_and_idempotent(client: Any, db: Any) -> None:
    actor = login(db)
    series_id = make_series(db)
    asset_id = make_asset(db, actor.user_id)

    # 幂等键：重复创建返回同一条询价
    first = client.post(
        f"{BASE}/inquiries",
        json=inquiry_body(series_id, asset_id),
        headers=actor.headers(**{"Idempotency-Key": "draft-1"}),
    )
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["status"] == "DRAFT"
    assert body["version"] == 1
    assert body["current_revision"] == 1
    assert body["revisions"][0]["revision"] == 1

    repeat = client.post(
        f"{BASE}/inquiries",
        json=inquiry_body(series_id, asset_id),
        headers=actor.headers(**{"Idempotency-Key": "draft-1"}),
    )
    assert repeat.status_code == 201
    assert repeat.json()["id"] == body["id"]

    # 版本冲突 409
    conflict = client.patch(
        f"{BASE}/inquiries/{body['id']}",
        json={"version": 99, "species": "狼"},
        headers=actor.headers(),
    )
    assert conflict.status_code == 409
    assert error_code(conflict) == "VERSION_CONFLICT"

    patched = client.patch(
        f"{BASE}/inquiries/{body['id']}",
        json={"version": 1, "species": "狼", "requirements": "追加磁吸角。"},
        headers=actor.headers(),
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["version"] == 2
    assert patched.json()["species"] == "狼"

    submitted = client.post(f"{BASE}/inquiries/{body['id']}/submit", headers=actor.headers())
    assert submitted.status_code == 200
    assert submitted.json()["status"] == "SUBMITTED"
    assert submitted.json()["submitted_at"] is not None

    # 重复提交返回同一对象
    resubmitted = client.post(f"{BASE}/inquiries/{body['id']}/submit", headers=actor.headers())
    assert resubmitted.status_code == 200
    assert resubmitted.json() == submitted.json()

    listed = client.get(f"{BASE}/inquiries", headers=actor.headers())
    assert listed.status_code == 200
    payload = listed.json()
    assert payload["has_more"] is False
    assert [item["id"] for item in payload["items"]] == [body["id"]]

    withdrawn = client.post(f"{BASE}/inquiries/{body['id']}/withdraw", headers=actor.headers())
    assert withdrawn.status_code == 200
    assert withdrawn.json()["status"] == "WITHDRAWN"
    # 终态不可修改
    blocked = client.patch(
        f"{BASE}/inquiries/{body['id']}",
        json={"version": withdrawn.json()["version"], "species": "狐"},
        headers=actor.headers(),
    )
    assert blocked.status_code == 409
    assert error_code(blocked) == "STATE_CONFLICT"


def test_inquiry_form_validation_is_enforced_server_side(client: Any, db: Any) -> None:
    actor = login(db)
    series_id = make_series(db)
    asset_id = make_asset(db, actor.user_id)

    no_rights = client.post(
        f"{BASE}/inquiries",
        json=inquiry_body(series_id, asset_id, rights_declaration=False),
        headers=actor.headers(),
    )
    assert no_rights.status_code == 422
    assert error_code(no_rights) == "VALIDATION_ERROR"

    bad_budget = client.post(
        f"{BASE}/inquiries",
        json=inquiry_body(series_id, asset_id, budget_min_cents=900000, budget_max_cents=100000),
        headers=actor.headers(),
    )
    assert bad_budget.status_code == 422

    unpublished = ContentEntry(
        id=new_id("content"),
        kind="series",
        slug=f"draft-{uuid.uuid4().hex[:8]}",
        title="未发布系列",
        status="draft",
    )
    db.add(unpublished)
    db.commit()
    bad_series = client.post(
        f"{BASE}/inquiries",
        json=inquiry_body(unpublished.id, asset_id),
        headers=actor.headers(),
    )
    assert bad_series.status_code == 422

    # 提交时必须有设定图
    created = client.post(
        f"{BASE}/inquiries",
        json=inquiry_body(series_id, asset_id, design_asset_ids=[]),
        headers=actor.headers(),
    )
    assert created.status_code == 201
    missing_assets = client.post(
        f"{BASE}/inquiries/{created.json()['id']}/submit", headers=actor.headers()
    )
    assert missing_assets.status_code == 422
    assert error_code(missing_assets) == "VALIDATION_ERROR"

    # 多余字段直接拒绝（不允许前端塞 user_id 或金额）
    injected = client.post(
        f"{BASE}/inquiries",
        json=inquiry_body(series_id, asset_id, user_id="usr_attacker"),
        headers=actor.headers(),
    )
    assert injected.status_code == 422


# ------------------------------------------------------------------ TC-C02
def test_tc_c02_accept_quote_end_to_end_with_real_commerce(client: Any, db: Any) -> None:
    """真实商务端口：接受报价 → 唯一订单 + 制作单 + 名额预留 + 条款留证。"""
    actor = login(db)
    series_id = make_series(db)
    asset_id = make_asset(db, actor.user_id)
    inquiry_id = create_submitted_inquiry(client, db, actor, series_id, asset_id)
    quote = publish_quote(db, inquiry_id=inquiry_id, total_cents=640000)

    quotes = client.get(f"{BASE}/inquiries/{inquiry_id}/quotes", headers=actor.headers())
    assert quotes.status_code == 200
    assert quotes.json()["items"][0]["quote_version"] == quote.quote_version
    assert quotes.json()["items"][0]["total_cents"] == 640000
    assert quotes.json()["items"][0]["line_items"][0]["amount_cents"] == 640000

    accepted = client.post(
        f"{BASE}/quotes/{quote.id}/accept",
        json=accept_payload(quote),
        headers=actor.headers(**{"Idempotency-Key": "accept-tab-1"}),
    )
    assert accepted.status_code == 201, accepted.text
    body = accepted.json()
    assert body["order_id"].startswith("ord_")
    assert body["installment_id"]
    assert body["amount_cents"] == 640000
    assert body["prepayment_cents"] == 320000, "预付款比例来自 commission_policy"
    assert body["currency"] == "CNY"
    assert body["project_id"].startswith("prj_")

    # 第二个标签页（不同幂等键）重复接受：返回同一订单，不产生第二笔订单
    again = client.post(
        f"{BASE}/quotes/{quote.id}/accept",
        json=accept_payload(quote),
        headers=actor.headers(**{"Idempotency-Key": "accept-tab-2"}),
    )
    assert again.status_code == 201, again.text
    assert again.json()["order_id"] == body["order_id"]
    assert (
        db.execute(sa.text("SELECT COUNT(*) FROM orders")).scalar() == 1
    ), "同一报价只生成一个订单"

    project = client.get(f"{BASE}/commission-projects/{body['project_id']}", headers=actor.headers())
    assert project.status_code == 200, project.text
    detail = project.json()
    assert detail["order_id"] == body["order_id"]
    assert detail["status"] == "QUEUED"
    assert detail["terms_version"] == quote.terms_version

    # 合同留证：条款版本 + 摘要哈希 + 报价关联
    consent = db.execute(
        sa.text(
            "SELECT purpose, document_version, content_hash, resource_id FROM consents "
            "WHERE user_id = :uid AND purpose = 'commission_rights'"
        ),
        {"uid": actor.user_id},
    ).one()
    assert consent.document_version == quote.terms_version
    assert consent.resource_id == quote.id
    assert len(consent.content_hash) == 64

    detail_row = client.get(f"{BASE}/inquiries/{inquiry_id}", headers=actor.headers()).json()
    assert detail_row["status"] == "ACCEPTED"
    assert detail_row["accepted_quote_id"] == quote.id
    assert detail_row["project_id"] == body["project_id"]


def test_tc_c02_stale_and_expired_quote_over_http(client: Any, db: Any) -> None:
    actor = login(db)
    series_id = make_series(db)
    asset_id = make_asset(db, actor.user_id)
    inquiry_id = create_submitted_inquiry(client, db, actor, series_id, asset_id)

    first = publish_quote(db, inquiry_id=inquiry_id, total_cents=400000)
    second = publish_quote(db, inquiry_id=inquiry_id, total_cents=520000)
    assert db.get(Quote, first.id).status == "SUPERSEDED"

    stale = client.post(
        f"{BASE}/quotes/{first.id}/accept",
        json=accept_payload(first),
        headers=actor.headers(),
    )
    assert stale.status_code == 409
    assert error_code(stale) == "QUOTE_CHANGED"

    wrong_version = client.post(
        f"{BASE}/quotes/{second.id}/accept",
        json=accept_payload(second, quote_version=1),
        headers=actor.headers(),
    )
    assert wrong_version.status_code == 409
    assert error_code(wrong_version) == "QUOTE_CHANGED"

    second.expires_at = now_ms() - 1000
    db.commit()
    expired = client.post(
        f"{BASE}/quotes/{second.id}/accept",
        json=accept_payload(second),
        headers=actor.headers(),
    )
    assert expired.status_code == 409
    assert error_code(expired) == "QUOTE_EXPIRED"

    second.expires_at = add_days_ms(7)
    db.commit()
    wrong_terms = client.post(
        f"{BASE}/quotes/{second.id}/accept",
        json=accept_payload(second, terms_version="commission-999"),
        headers=actor.headers(),
    )
    assert wrong_terms.status_code == 409
    assert error_code(wrong_terms) == "QUOTE_CHANGED"

    not_accepted = client.post(
        f"{BASE}/quotes/{second.id}/accept",
        json=accept_payload(second, accepted=False),
        headers=actor.headers(),
    )
    assert not_accepted.status_code == 422
    assert db.execute(sa.text("SELECT COUNT(*) FROM orders")).scalar() == 0


def test_c19_minor_commission_requires_guardian_consent_over_http(client: Any, db: Any) -> None:
    actor = login(db)
    series_id = make_series(db)
    asset_id = make_asset(db, actor.user_id)
    created = client.post(
        f"{BASE}/inquiries",
        json=inquiry_body(series_id, asset_id, guardian_required=True),
        headers=actor.headers(),
    )
    inquiry_id = created.json()["id"]
    client.post(f"{BASE}/inquiries/{inquiry_id}/submit", headers=actor.headers())
    quote = publish_quote(db, inquiry_id=inquiry_id)

    blocked = client.post(
        f"{BASE}/quotes/{quote.id}/accept", json=accept_payload(quote), headers=actor.headers()
    )
    assert blocked.status_code == 409
    assert error_code(blocked) == "STATE_CONFLICT"
    assert "监护" in blocked.json()["error"]["message"]

    consented = client.post(
        f"{BASE}/me/consents",
        json={
            "purpose": "miner_guardian",
            "document_version": "guardian-1",
            "accepted": True,
        },
        headers=actor.headers(),
    )
    if consented.status_code == 404:
        # 本模块测试应用未装配身份路由时，直接写同意记录（语义等价）。
        from app.modules.identity.service import record_consent

        record_consent(db, user_id=actor.user_id, purpose="miner_guardian", version="guardian-1")
        db.commit()
    else:
        assert consented.status_code == 201, consented.text

    accepted = client.post(
        f"{BASE}/quotes/{quote.id}/accept", json=accept_payload(quote), headers=actor.headers()
    )
    assert accepted.status_code == 201, accepted.text


# ------------------------------------------------------------------ TC-C04
def test_tc_c04_two_users_last_slot_over_http(client: Any, db: Any) -> None:
    first = login(db, "甲")
    second = login(db, "乙")
    series_id = make_series(db)
    first_asset = make_asset(db, first.user_id)
    second_asset = make_asset(db, second.user_id)
    round_row = make_round(db, capacity=1)
    first_inquiry = create_submitted_inquiry(client, db, first, series_id, first_asset)
    second_inquiry = create_submitted_inquiry(client, db, second, series_id, second_asset)
    select_application(db, round_row=round_row, user_id=first.user_id, inquiry_id=first_inquiry)
    select_application(db, round_row=round_row, user_id=second.user_id, inquiry_id=second_inquiry)
    first_quote = publish_quote(db, inquiry_id=first_inquiry)
    second_quote = publish_quote(db, inquiry_id=second_inquiry)

    won = client.post(
        f"{BASE}/quotes/{first_quote.id}/accept",
        json=accept_payload(first_quote),
        headers=first.headers(),
    )
    assert won.status_code == 201, won.text

    lost = client.post(
        f"{BASE}/quotes/{second_quote.id}/accept",
        json=accept_payload(second_quote),
        headers=second.headers(),
    )
    assert lost.status_code == 409, lost.text
    assert error_code(lost) == "NO_CAPACITY"

    fresh(db)
    counted = db.get(ScheduleRound, round_row.id)
    assert counted.reserved_count + counted.committed_count <= counted.capacity
    assert (counted.reserved_count, counted.committed_count) == (1, 0)
    assert (
        db.scalar(
            sa.select(sa.func.count())
            .select_from(CommissionProject)
            .where(CommissionProject.inquiry_id.in_([first_inquiry, second_inquiry]))
        )
        == 1
    )
    reservation = db.scalar(sa.select(SlotReservation))
    assert reservation is not None
    assert reservation.round_id == round_row.id
    assert reservation.expires_at > now_ms()
    # 未入选/未付款的申请不会占用名额
    assert (
        db.scalar(
            sa.select(sa.func.count())
            .select_from(ScheduleApplication)
            .where(ScheduleApplication.round_id == round_row.id)
        )
        == 2
    )


def test_schedule_rounds_public_payload_has_no_identity(client: Any, db: Any) -> None:
    actor = login(db)
    series_id = make_series(db)
    asset_id = make_asset(db, actor.user_id)
    round_row = make_round(db, capacity=4)
    inquiry_id = create_submitted_inquiry(client, db, actor, series_id, asset_id)
    select_application(db, round_row=round_row, user_id=actor.user_id, inquiry_id=inquiry_id)

    response = client.get(f"{BASE}/schedule-rounds")
    assert response.status_code == 200
    payload = response.json()
    assert payload["items"], payload
    item = payload["items"][0]
    assert set(item) == {
        "id",
        "title",
        "mode",
        "status",
        "stage_label",
        "timezone",
        "start_at",
        "end_at",
        "opens_at",
        "closes_at",
        "accepting",
        "remaining_bucket",
    }, "公开排期只展示匿名余量摘要、时区、开始与截止"
    assert item["timezone"] == "Asia/Shanghai"
    assert item["remaining_bucket"] in {"plenty", "limited", "full", "closed"}
    raw = response.text
    for forbidden in ("phone", "1380", "手机号", "nickname", "order_no", "收件", "地址"):
        assert forbidden not in raw


def test_schedule_application_is_idempotent_and_limited(client: Any, db: Any) -> None:
    actor = login(db)
    series_id = make_series(db)
    asset_id = make_asset(db, actor.user_id)
    round_row = make_round(db, capacity=6)
    inquiry_ids = [
        create_submitted_inquiry(client, db, actor, series_id, asset_id) for _ in range(4)
    ]

    first = client.post(
        f"{BASE}/schedule-rounds/{round_row.id}/applications",
        json={"inquiry_id": inquiry_ids[0], "note": "希望春季档"},
        headers=actor.headers(**{"Idempotency-Key": "apply-0"}),
    )
    assert first.status_code == 201, first.text
    assert first.json()["status"] == "SUBMITTED"

    duplicate = client.post(
        f"{BASE}/schedule-rounds/{round_row.id}/applications",
        json={"inquiry_id": inquiry_ids[0]},
        headers=actor.headers(**{"Idempotency-Key": "apply-0"}),
    )
    assert duplicate.status_code == 201
    assert duplicate.json()["id"] == first.json()["id"]

    for index in (1, 2):
        ok = client.post(
            f"{BASE}/schedule-rounds/{round_row.id}/applications",
            json={"inquiry_id": inquiry_ids[index]},
            headers=actor.headers(),
        )
        assert ok.status_code == 201

    limited = client.post(
        f"{BASE}/schedule-rounds/{round_row.id}/applications",
        json={"inquiry_id": inquiry_ids[3]},
        headers=actor.headers(),
    )
    assert limited.status_code == 409
    assert error_code(limited) == "STATE_CONFLICT"

    closed = make_round(db, capacity=2, status="CLOSED")
    unavailable = client.post(
        f"{BASE}/schedule-rounds/{closed.id}/applications",
        json={"inquiry_id": inquiry_ids[3]},
        headers=actor.headers(),
    )
    assert unavailable.status_code == 409
    assert error_code(unavailable) == "SCHEDULE_CLOSED"


# ------------------------------------------------------------------ TC-C05 与制作
def test_tc_c05_measurements_and_stage_confirmation_over_http(client: Any, db: Any) -> None:
    actor = login(db)
    series_id = make_series(db)
    asset_id = make_asset(db, actor.user_id)
    inquiry_id = create_submitted_inquiry(client, db, actor, series_id, asset_id)
    quote = publish_quote(db, inquiry_id=inquiry_id)
    accepted = client.post(
        f"{BASE}/quotes/{quote.id}/accept", json=accept_payload(quote), headers=actor.headers()
    )
    assert accepted.status_code == 201, accepted.text
    project_id = accepted.json()["project_id"]

    measurement = client.post(
        f"{BASE}/commission-projects/{project_id}/measurements",
        json={"payload": {"height_cm": 175.5, "head_cm": 40.0}, "unit_system": "metric"},
        headers=actor.headers(),
    )
    assert measurement.status_code == 201, measurement.text
    assert measurement.json()["version"] == 1
    assert measurement.json()["measurements"]["height_cm"] == 175.5
    assert measurement.json()["flagged_fields"] == ["head_cm"], "越界只提示，不拒绝"

    stored = db.execute(
        sa.text("SELECT encrypted_payload FROM measurements WHERE project_id = :pid"),
        {"pid": project_id},
    ).scalar()
    assert "175.5" not in stored and "height_cm" not in stored, "量体正文加密存储"

    stage = svc.upsert_production_stage(
        db,
        project_id=project_id,
        actor_id=STAFF_ID,
        stage_code="DESIGN_CONFIRMING",
        description="第一版设定分解",
        media_asset_ids=["ast_v1"],
        needs_customer_confirmation=True,
    )
    db.commit()
    updated = svc.upsert_production_stage(
        db,
        project_id=project_id,
        actor_id=STAFF_ID,
        stage_code="DESIGN_CONFIRMING",
        description="第二版设定分解",
        media_asset_ids=["ast_v2"],
    )
    db.commit()
    assert updated.revision == 2

    stale = client.post(
        f"{BASE}/production-stages/{stage.id}/confirm",
        json={"revision": 1, "accepted": True},
        headers=actor.headers(),
    )
    assert stale.status_code == 409, stale.text
    assert error_code(stale) == "VERSION_CONFLICT"

    confirmed = client.post(
        f"{BASE}/production-stages/{stage.id}/confirm",
        json={"revision": 2, "accepted": True},
        headers=actor.headers(),
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["confirmed_revision"] == 2
    assert confirmed.json()["approved_by"] == actor.user_id

    detail = client.get(f"{BASE}/commission-projects/{project_id}", headers=actor.headers())
    assert detail.status_code == 200
    body = detail.json()
    assert len(body["measurements"]) == 1
    assert body["measurements"][0]["measurements"]["height_cm"] == 175.5
    assert [item["revision"] for item in body["stages"]] == [2]
    assert body["stages"][0]["confirmed_revision"] == 2


def test_stage_change_and_change_acceptance_over_http(client: Any, db: Any) -> None:
    actor = login(db)
    series_id = make_series(db)
    asset_id = make_asset(db, actor.user_id)
    inquiry_id = create_submitted_inquiry(client, db, actor, series_id, asset_id)
    quote = publish_quote(db, inquiry_id=inquiry_id, total_cents=500000)
    accepted = client.post(
        f"{BASE}/quotes/{quote.id}/accept", json=accept_payload(quote), headers=actor.headers()
    )
    assert accepted.status_code == 201, accepted.text
    project_id = accepted.json()["project_id"]
    stage = svc.upsert_production_stage(
        db,
        project_id=project_id,
        actor_id=STAFF_ID,
        stage_code="PRODUCING",
        description="主体完成",
        needs_customer_confirmation=True,
    )
    db.commit()

    requested = client.post(
        f"{BASE}/production-stages/{stage.id}/changes",
        json={"revision": stage.revision, "description": "希望尾巴再长 20cm"},
        headers=actor.headers(),
    )
    assert requested.status_code == 200, requested.text
    assert requested.json()["status"] == "SUBMITTED"
    change_row = fresh(db).get(ChangeRequest, requested.json()["id"])
    assert change_row.requested_by_type == "customer", "客户请求修改记录发起方类型"
    assert change_row.delta_cents == 0, "客户请求修改不产生金额"
    # 客户请求修改不改动原价
    assert db.get(CommissionProject, project_id).total_cents == 500000

    proposal = svc.propose_change(
        db,
        project_id=project_id,
        actor_id=STAFF_ID,
        scope="addition",
        description="尾巴加长 20cm",
        delta_cents=80000,
        schedule_delta_days=10,
        terms_version=quote.terms_version,
    )
    db.commit()

    accepted_change = client.post(
        f"{BASE}/production-stages/{stage.id}/changes",
        json={
            "revision": stage.revision,
            "change_request_id": proposal.id,
            "accept": True,
        },
        headers=actor.headers(),
    )
    assert accepted_change.status_code == 200, accepted_change.text
    body = accepted_change.json()
    assert body["status"] == "ACCEPTED"
    assert body["installment_id"], "商务端口就绪时必须生成单独补款应付项"
    installments = db.execute(
        sa.text("SELECT COUNT(*) FROM installments WHERE kind = 'change'")
    ).scalar()
    assert installments == 1, "一次变更只生成一个补款应付项"
    # 旧合同金额与条款快照不改
    project = db.get(CommissionProject, project_id)
    assert project.total_cents == 500000
    assert project.terms_version == quote.terms_version
    # 已接受版本不可覆盖
    repeat = client.post(
        f"{BASE}/production-stages/{stage.id}/changes",
        json={"revision": stage.revision, "change_request_id": proposal.id, "accept": True},
        headers=actor.headers(),
    )
    assert repeat.status_code == 200
    assert repeat.json()["version"] == body["version"]


def test_shipping_blocked_until_balance_settled(client: Any, db: Any, payments_stub: dict[str, Any]) -> None:
    actor = login(db)
    series_id = make_series(db)
    asset_id = make_asset(db, actor.user_id)
    inquiry_id = create_submitted_inquiry(client, db, actor, series_id, asset_id)
    quote = publish_quote(db, inquiry_id=inquiry_id, total_cents=400000)
    accepted = client.post(
        f"{BASE}/quotes/{quote.id}/accept", json=accept_payload(quote), headers=actor.headers()
    )
    assert accepted.status_code == 201
    project_id = accepted.json()["project_id"]

    for status in ("MEASUREMENT_PENDING", "DESIGN_CONFIRMING", "MATERIALS_READY", "PRODUCING",
                   "CUSTOMER_REVIEW", "READY_TO_SHIP"):
        svc.advance_project_stage(db, project_id=project_id, to_status=status, actor_id=STAFF_ID)
    db.commit()

    # 只收到预付款（160000 < 400000）不能发货
    payments_stub["paid_cents"] = 160000
    with pytest.raises(AppError) as unpaid:
        svc.advance_project_stage(db, project_id=project_id, to_status="SHIPPED", actor_id=STAFF_ID)
    assert unpaid.value.code == "STATE_CONFLICT"
    db.rollback()
    assert db.get(CommissionProject, project_id).status == "READY_TO_SHIP"

    payments_stub["paid_cents"] = 400000
    shipped = svc.advance_project_stage(
        db, project_id=project_id, to_status="SHIPPED", actor_id=STAFF_ID
    )
    db.commit()
    assert shipped.status == "SHIPPED"
    assert shipped.shipped_at is not None


# ------------------------------------------------------------------ 越权与防护
def test_tc_a04_cross_user_access_returns_404(client: Any, db: Any) -> None:
    owner = login(db, "所有人")
    stranger = login(db, "陌生人")
    series_id = make_series(db)
    asset_id = make_asset(db, owner.user_id)
    inquiry_id = create_submitted_inquiry(client, db, owner, series_id, asset_id)
    quote = publish_quote(db, inquiry_id=inquiry_id)
    accepted = client.post(
        f"{BASE}/quotes/{quote.id}/accept", json=accept_payload(quote), headers=owner.headers()
    )
    assert accepted.status_code == 201
    project_id = accepted.json()["project_id"]
    stage = svc.upsert_production_stage(
        db,
        project_id=project_id,
        actor_id=STAFF_ID,
        stage_code="DESIGN_CONFIRMING",
        needs_customer_confirmation=True,
    )
    db.commit()

    for response in (
        client.get(f"{BASE}/inquiries/{inquiry_id}", headers=stranger.headers()),
        client.patch(
            f"{BASE}/inquiries/{inquiry_id}",
            json={"version": 1, "species": "狼"},
            headers=stranger.headers(),
        ),
        client.post(f"{BASE}/inquiries/{inquiry_id}/withdraw", headers=stranger.headers()),
        client.get(f"{BASE}/inquiries/{inquiry_id}/quotes", headers=stranger.headers()),
        client.get(f"{BASE}/commission-projects/{project_id}", headers=stranger.headers()),
        client.post(
            f"{BASE}/commission-projects/{project_id}/measurements",
            json={"payload": {"height_cm": 170.0}},
            headers=stranger.headers(),
        ),
        client.post(
            f"{BASE}/production-stages/{stage.id}/confirm",
            json={"revision": stage.revision, "accepted": True},
            headers=stranger.headers(),
        ),
    ):
        assert response.status_code == 404, response.text
        assert error_code(response) == "RESOURCE_NOT_FOUND"

    # 换随机 ID 也无法绕过归属过滤
    random_id = "inq_" + uuid.uuid4().hex
    assert (
        client.get(f"{BASE}/inquiries/{random_id}", headers=stranger.headers()).status_code == 404
    )
    assert (
        client.get(f"{BASE}/commission-projects/prj_{uuid.uuid4().hex}", headers=owner.headers()).status_code
        == 404
    )


def test_write_guard_requires_csrf_and_trusted_origin(client: Any, db: Any) -> None:
    actor = login(db)
    series_id = make_series(db)
    asset_id = make_asset(db, actor.user_id)

    no_csrf = client.post(
        f"{BASE}/inquiries",
        json=inquiry_body(series_id, asset_id),
        headers={"Origin": ORIGIN, "Cookie": f"{SESSION_COOKIE}={actor.token}"},
    )
    assert no_csrf.status_code == 403
    assert error_code(no_csrf) == "CSRF_INVALID"

    no_origin = client.post(
        f"{BASE}/inquiries",
        json=inquiry_body(series_id, asset_id),
        headers={"Cookie": f"{SESSION_COOKIE}={actor.token}", "X-CSRF-Token": actor.csrf},
    )
    assert no_origin.status_code == 403
    assert error_code(no_origin) == "CSRF_INVALID"

    anonymous = client.get(f"{BASE}/inquiries", headers={"Origin": ORIGIN})
    assert anonymous.status_code == 401
    assert error_code(anonymous) == "AUTH_REQUIRED"


def test_project_read_is_owner_scoped_and_maker_path_is_service_level(client: Any, db: Any) -> None:
    """公开路由只认客户本人；分配装师读取由 service 端口提供（admin/工作流使用）。

    依赖层 ``resolve_principal`` 对 ``/api/v1`` 非 admin 路径只解析客户会话，
    因此员工读取制作单不会从公开路由进入；这里同时验证两条边界。
    """
    owner = login(db, "所有人")
    series_id = make_series(db)
    asset_id = make_asset(db, owner.user_id)
    inquiry_id = create_submitted_inquiry(client, db, owner, series_id, asset_id)
    quote = publish_quote(db, inquiry_id=inquiry_id)
    accepted = client.post(
        f"{BASE}/quotes/{quote.id}/accept", json=accept_payload(quote), headers=owner.headers()
    )
    project_id = accepted.json()["project_id"]
    svc.assign_maker(db, project_id=project_id, maker_id=STAFF_ID, actor_id=STAFF_ID)
    db.commit()

    allowed = client.get(f"{BASE}/commission-projects/{project_id}", headers=owner.headers())
    assert allowed.status_code == 200
    stranger = login(db, "无关客户")
    denied = client.get(f"{BASE}/commission-projects/{project_id}", headers=stranger.headers())
    assert denied.status_code == 404

    # 分配装师路径（service 层）：只有被分配的员工可读
    assigned = svc.project_for_actor(db, project_id=project_id, user_id=None, maker_id=STAFF_ID)
    assert assigned.id == project_id
    with pytest.raises(AppError) as not_assigned:
        svc.project_for_actor(db, project_id=project_id, user_id=None, maker_id="stf_other")
    assert not_assigned.value.code == "RESOURCE_NOT_FOUND"


def test_project_summary_for_order_port(db: Any) -> None:
    """订单页读取制作进度的端口：只返回进度，不含量体与私密素材。"""
    from app.modules.commissions import ports as commission_ports

    actor = login(db)
    series_id = make_series(db)
    asset_id = make_asset(db, actor.user_id)
    inquiry = svc.create_inquiry(
        db,
        user_id=actor.user_id,
        payload=InquiryCreate(
            character_name="逐星绒龙",
            species="龙",
            series_id=series_id,
            design_asset_ids=[asset_id],
            rights_declaration=True,
        ),
    )
    svc.submit_inquiry(db, user_id=actor.user_id, inquiry_id=inquiry.id)
    db.commit()
    quote = publish_quote(db, inquiry_id=inquiry.id)
    order, project = svc.accept_quote_for_user(
        db,
        user_id=actor.user_id,
        quote_id=quote.id,
        quote_version=quote.quote_version,
        terms_version=quote.terms_version,
        accepted=True,
    )
    db.commit()

    summary = commission_ports.project_for_order(db, order_id=order.order_id)
    assert summary is not None
    assert summary["project_id"] == project.id
    assert summary["status"] == "QUEUED"
    assert "measurements" not in summary and "payload" not in summary
    assert commission_ports.commit_slot_on_prepayment(db, order_id="ord_unknown") is False
    assert commission_ports.release_slot_on_order_closed(db, order_id="ord_unknown", reason="无预留") is False
