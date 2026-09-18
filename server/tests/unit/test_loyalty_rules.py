"""TC-L01 ~ TC-L04：购买奖励的唯一来源、退款减法重算、负账面与并发入账。

这些用例完全不依赖"其他模块是否就绪"：商务快照通过 ``loyalty.ports.order_snapshot``
注入替身，时间通过替换 ``service.now_ms`` 控制。
"""

from __future__ import annotations

import contextlib
import pathlib
import threading
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
import sqlalchemy as sa

from app.modules.content.models import SiteSetting
from app.modules.contracts import AwardRequest
from app.modules.identity.models import User
from app.modules.loyalty import ports as loyalty_ports
from app.modules.loyalty import repository as loyalty_repo
from app.modules.loyalty import service as loyalty_service
from app.modules.loyalty.models import PointAdjustment, PointAward, PointLedger
from app.platform import db as platform_db
from app.platform.config import get_settings
from app.platform.errors import AppError
from app.platform.ids import new_id, now_ms
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
    """独立测试库。

    ``DATA_DIR`` 直接用 ``server/var`` 自身并配一个唯一的库文件名：本会话的沙箱
    不允许向新建的子目录写入，因此不新建目录，只新建文件。
    """
    import importlib

    SERVER_VAR.mkdir(parents=True, exist_ok=True)
    database = SERVER_VAR / f"test-loyalty-{uuid.uuid4().hex}.db"
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
            # 文件仍被其他线程占用时留给系统清理
            with contextlib.suppress(PermissionError):
                pathlib.Path(f"{database}{suffix}").unlink(missing_ok=True)


@pytest.fixture()
def db(env: Any) -> Iterator[Any]:
    session = platform_db.get_session_factory()()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def clock(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """可控时钟：替换积分服务的 ``now_ms``（账户/账本时间随之确定）。"""
    box = {"now": 1_700_000_000_000}
    monkeypatch.setattr(loyalty_service, "now_ms", lambda: box["now"])
    return box


class FakeCommerce:
    """商务快照替身：键与 ``commerce.ports.order_reward_snapshot`` 保持一致。"""

    def __init__(self) -> None:
        self.orders: dict[str, dict[str, Any]] = {}

    def add(
        self,
        order_id: str,
        *,
        user_id: str,
        goods_cents: int,
        shipping_cents: int = 0,
        refunded_goods_cents: int = 0,
        completed_at: int | None = None,
        status: str = "COMPLETED",
        open_after_sales: bool = False,
    ) -> None:
        paid_goods = goods_cents
        refunded_goods = min(refunded_goods_cents, paid_goods)
        self.orders[order_id] = {
            "order_id": order_id,
            "user_id": user_id,
            "status": status,
            "currency": "CNY",
            "goods_cents": goods_cents,
            "shipping_cents": shipping_cents,
            "discount_cents": 0,
            "total_cents": goods_cents + shipping_cents,
            "paid_goods_cents": paid_goods,
            "paid_shipping_cents": shipping_cents,
            "refunded_goods_cents": refunded_goods,
            "refunded_shipping_cents": 0,
            "eligible_net_cents": max(0, paid_goods - refunded_goods),
            "completed_at": _iso(completed_at),
            "has_open_after_sales": open_after_sales,
        }

    def snapshot(self, session: Any, order_id: str) -> dict[str, Any]:
        row = self.orders.get(order_id)
        if row is None:
            raise AppError("RESOURCE_NOT_FOUND")
        return dict(row)


def _iso(moment: int | None) -> str | None:
    from app.platform.ids import iso

    return iso(moment)


@pytest.fixture()
def commerce(monkeypatch: pytest.MonkeyPatch) -> FakeCommerce:
    fake = FakeCommerce()
    monkeypatch.setattr(loyalty_ports, "order_snapshot", fake.snapshot)
    return fake


def make_user(session: Any, *, phone: str = "13800000001", nickname: str = "测试星友") -> str:
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


def recompute(
    session: Any,
    *,
    user_id: str,
    order_id: str,
    revision: int = 1,
    eligible: int = 0,
    available_at: int | None = None,
) -> dict[str, Any]:
    return loyalty_service.recompute_award(
        session,
        AwardRequest(
            user_id=user_id,
            source_type="order",
            source_id=order_id,
            source_revision=revision,
            eligible_amount_cents=eligible,
            rule_version="",
            available_at=available_at if available_at is not None else now_ms(),
            label=f"订单 {order_id}",
        ),
    )


def emit_award_due(
    session: Any, *, order_id: str, user_id: str, reason: str = "installment_paid", available_at: int | None = None
) -> Any:
    """模拟商务模块登记 ``points.award_due`` 事件（worker 触发的真实入口）。"""
    from app.platform.reliability import enqueue

    return enqueue(
        session,
        event_type="points.award_due",
        aggregate_id=order_id,
        payload={"order_id": order_id, "user_id": user_id, "reason": reason},
        available_at=available_at,
    )


def ledger_rows(session: Any, user_id: str) -> list[PointLedger]:
    return list(
        session.scalars(
            sa.select(PointLedger).where(PointLedger.user_id == user_id).order_by(PointLedger.created_at)
        )
    )


def only_award(session: Any) -> PointAward:
    awards = list(session.scalars(sa.select(PointAward)))
    assert len(awards) == 1, f"奖励来源必须唯一，实际 {len(awards)} 条"
    return awards[0]


def denominator_cents(session: Any) -> int:
    return loyalty_service.purchase_policy(session)["denominator_yuan"] * 100


# ------------------------------------------------------------------ TC-L01
def test_tc_l01_split_payments_create_single_award(db: Any, commerce: FakeCommerce, clock: dict[str, int]) -> None:
    """定制分次支付（预付款/尾款/加项）只产生一个奖励来源。"""
    user_id = make_user(db)
    unit = denominator_cents(db)
    commerce.add(
        "ord_split",
        user_id=user_id,
        goods_cents=unit * 200,
        shipping_cents=2_000,
        completed_at=None,
        status="ACTIVE",
    )
    for revision in (1, 2, 3):  # 预付款、尾款、加项支付分别触发事件
        recompute(db, user_id=user_id, order_id="ord_split", revision=revision)
    db.commit()

    award = only_award(db)
    assert award.target_points == 200, "商品实付 200 分；运费不计奖"
    assert award.status == "PENDING", "未完成交付不得入账"
    assert award.posted_points == 0
    assert ledger_rows(db, user_id) == []
    view = loyalty_service.account_view(db, user_id=user_id)
    assert view["pending_points"] == 200
    assert view["posted_balance"] == 0

    # 交付后满 7 天才入账；重复的到点调度不会重复发奖
    commerce.orders["ord_split"]["completed_at"] = _iso(clock["now"])
    clock["now"] += 7 * DAY_MS + 1
    assert loyalty_service.post_due_awards(db, now=clock["now"]) == 1
    assert loyalty_service.post_due_awards(db, now=clock["now"] + 1_000) == 0
    db.commit()
    assert only_award(db).posted_points == 200
    assert loyalty_service.account_view(db, user_id=user_id)["posted_balance"] == 200
    assert len(ledger_rows(db, user_id)) == 1


def test_tc_l01_shipping_never_rewarded(db: Any, commerce: FakeCommerce) -> None:
    """运费不计奖励：净额取商品分摊而非订单总额。"""
    user_id = make_user(db)
    unit = denominator_cents(db)
    commerce.add("ord_ship", user_id=user_id, goods_cents=unit * 3, shipping_cents=unit * 2)
    recompute(db, user_id=user_id, order_id="ord_ship")
    db.commit()
    assert only_award(db).target_points == 3


# ------------------------------------------------------------------ TC-L02
def test_tc_l02_repeated_small_refunds_subtract_not_sum(
    db: Any, commerce: FakeCommerce, clock: dict[str, int]
) -> None:
    """09 文档「商品 199 分、退款 50 分再退 50 分 → 目标 1、1、0」的等比场景。

    每实付 ``denominator_yuan`` 元 1 积分，因此取 ``2 * unit - 1`` 分（=199 分在
    100 分/积分下的等比放大），两次各退半个单位。必须用减法重算，否则每次向下取整
    都会漏扣。
    """
    user_id = make_user(db)
    unit = denominator_cents(db)
    goods = unit * 2 - 1
    commerce.add("ord_199", user_id=user_id, goods_cents=goods, completed_at=clock["now"])
    emit_award_due(db, order_id="ord_199", user_id=user_id, available_at=clock["now"])
    db.commit()

    clock["now"] += 7 * DAY_MS + 1  # 交付后 7 天
    assert loyalty_service.post_due_awards(db, now=clock["now"]) == 1
    db.commit()
    assert only_award(db).posted_points == 1, "floor((2U-1)/U) = 1"
    assert len(ledger_rows(db, user_id)) == 1

    # 第一次退款：目标仍为 1，不产生新流水
    commerce.orders["ord_199"]["refunded_goods_cents"] = unit // 2
    commerce.orders["ord_199"]["eligible_net_cents"] = goods - unit // 2
    result = recompute(db, user_id=user_id, order_id="ord_199", revision=2)
    db.commit()
    assert result["target_points"] == 1
    assert result["delta"] == 0
    assert len(ledger_rows(db, user_id)) == 1, "目标未变化时不得写流水"

    # 第二次退款：目标变 0，追加差额 -1（若逐次累加会漏掉这次扣回）
    commerce.orders["ord_199"]["refunded_goods_cents"] = unit
    commerce.orders["ord_199"]["eligible_net_cents"] = max(0, goods - unit)
    result = recompute(db, user_id=user_id, order_id="ord_199", revision=3)
    db.commit()
    assert result["target_points"] == 0
    assert result["delta"] == -1
    rows = ledger_rows(db, user_id)
    assert [row.delta for row in rows] == [1, -1]
    assert [row.entry_type for row in rows] == ["AWARD", "AWARD_CLAWBACK"]
    assert loyalty_service.account_view(db, user_id=user_id)["posted_balance"] == 0


def test_tc_l02_partial_refund_appends_difference(db: Any, commerce: FakeCommerce, clock: dict[str, int]) -> None:
    """部分退款：已入账则追加差额（09 文档「目标 800；追加 -200」的等比场景）。"""
    user_id = make_user(db)
    unit = denominator_cents(db)
    goods = unit * 200
    commerce.add("ord_partial", user_id=user_id, goods_cents=goods, completed_at=clock["now"])
    emit_award_due(db, order_id="ord_partial", user_id=user_id, available_at=clock["now"])
    db.commit()
    clock["now"] += 7 * DAY_MS + 1
    assert loyalty_service.post_due_awards(db, now=clock["now"]) == 1
    db.commit()
    assert loyalty_service.account_view(db, user_id=user_id)["posted_balance"] == 200

    refund = unit * 40
    commerce.orders["ord_partial"]["refunded_goods_cents"] = refund
    commerce.orders["ord_partial"]["eligible_net_cents"] = goods - refund
    result = recompute(db, user_id=user_id, order_id="ord_partial", revision=2)
    db.commit()
    assert result["target_points"] == 160
    assert result["delta"] == -40
    assert loyalty_service.account_view(db, user_id=user_id)["posted_balance"] == 160
    assert loyalty_service.verify_account_consistency(db, user_id=user_id)["consistent"] is True


def test_tc_l02_no_per_order_cap(db: Any, commerce: FakeCommerce) -> None:
    """单笔无上限：09 文档的 2000 分上限是已被 C13 否决的旧推荐值。"""
    user_id = make_user(db)
    unit = denominator_cents(db)
    commerce.add("ord_big", user_id=user_id, goods_cents=unit * 6_000)
    recompute(db, user_id=user_id, order_id="ord_big")
    db.commit()
    assert only_award(db).target_points == 6_000 > 2_000


def test_tc_l02_open_after_sale_blocks_release(db: Any, commerce: FakeCommerce, clock: dict[str, int]) -> None:
    """存在未决售后时不入账，待售后结论后再发。"""
    user_id = make_user(db)
    unit = denominator_cents(db)
    commerce.add(
        "ord_after",
        user_id=user_id,
        goods_cents=unit * 10,
        completed_at=clock["now"] - 30 * DAY_MS,
        open_after_sales=True,
    )
    recompute(db, user_id=user_id, order_id="ord_after")
    db.commit()
    award = only_award(db)
    assert award.status == "PENDING"
    assert award.blocked_reason
    assert loyalty_service.post_due_awards(db, now=clock["now"]) == 0
    assert loyalty_service.account_view(db, user_id=user_id)["pending_points"] == 10

    commerce.orders["ord_after"]["has_open_after_sales"] = False
    assert loyalty_service.post_due_awards(db, now=clock["now"]) == 1
    db.commit()
    assert loyalty_service.account_view(db, user_id=user_id)["posted_balance"] == 10


def test_tc_l02_cancelled_order_targets_zero(db: Any, commerce: FakeCommerce, clock: dict[str, int]) -> None:
    """取消订单目标为 0；已入账部分全额扣回。"""
    user_id = make_user(db)
    unit = denominator_cents(db)
    commerce.add("ord_cancel", user_id=user_id, goods_cents=unit * 50, completed_at=clock["now"])
    emit_award_due(db, order_id="ord_cancel", user_id=user_id, available_at=clock["now"])
    db.commit()
    clock["now"] += 7 * DAY_MS + 1
    assert loyalty_service.post_due_awards(db, now=clock["now"]) == 1
    db.commit()
    assert loyalty_service.account_view(db, user_id=user_id)["posted_balance"] == 50

    commerce.orders["ord_cancel"]["status"] = "CANCELLED"
    commerce.orders["ord_cancel"]["refunded_goods_cents"] = unit * 50
    commerce.orders["ord_cancel"]["eligible_net_cents"] = 0
    result = recompute(db, user_id=user_id, order_id="ord_cancel", revision=2)
    db.commit()
    assert result["target_points"] == 0
    assert loyalty_service.account_view(db, user_id=user_id)["posted_balance"] == 0


# ------------------------------------------------------------------ TC-L03
def test_tc_l03_negative_balance_after_spending_then_refund(
    db: Any, commerce: FakeCommerce, clock: dict[str, int]
) -> None:
    """积分花掉后再全额退款：账面可为负，可用不为负，退款照常完成。"""
    user_id = make_user(db)
    unit = denominator_cents(db)
    goods = unit * 200  # 200 分
    commerce.add("ord_spend", user_id=user_id, goods_cents=goods, completed_at=clock["now"])
    emit_award_due(db, order_id="ord_spend", user_id=user_id, available_at=clock["now"])
    db.commit()
    clock["now"] += 7 * DAY_MS + 1
    assert loyalty_service.post_due_awards(db, now=clock["now"]) == 1
    db.commit()

    # 用户先花掉 199 分（C15 未开放，用有审批的调整单表达“已消费”）
    adjustment = loyalty_service.create_adjustment(
        db,
        user_id=user_id,
        delta_points=-199,
        reason="按用户申请人工核销已消费积分",
        requested_by="stf_requester",
    )
    db.commit()
    applied = loyalty_service.approve_adjustment(db, adjustment_id=adjustment.id, approver_id="stf_approver")
    db.commit()
    assert applied["posted_balance"] == 1

    # 全额退款：扣回 200，账面 -199；可用 0，不要求用户付现
    commerce.orders["ord_spend"]["refunded_goods_cents"] = goods
    commerce.orders["ord_spend"]["eligible_net_cents"] = 0
    result = recompute(db, user_id=user_id, order_id="ord_spend", revision=2)
    db.commit()
    view = loyalty_service.account_view(db, user_id=user_id)
    assert result["delta"] == -200
    assert view["posted_balance"] == -199
    assert view["available_points"] == 0, "可用额度不得为负"
    assert view["pending_points"] == 0
    # 账本仍是权威记录：缓存 = Σ delta，且没有 UPDATE/DELETE
    consistency = loyalty_service.verify_account_consistency(db, user_id=user_id)
    assert consistency["consistent"] is True
    assert consistency["ledger_total"] == -199
    assert len(ledger_rows(db, user_id)) == 3  # +200 / -199 / -200


# ------------------------------------------------------------------ TC-L04
def test_tc_l04_repeated_events_post_once(db: Any, commerce: FakeCommerce, clock: dict[str, int]) -> None:
    """收款重复通知 10 次：奖励只有一个，净奖励不变，且流水合计等于账户缓存。"""
    user_id = make_user(db)
    unit = denominator_cents(db)
    commerce.add("ord_repeat", user_id=user_id, goods_cents=unit * 120, completed_at=clock["now"])
    clock["now"] += 7 * DAY_MS + 1
    for _ in range(10):
        recompute(db, user_id=user_id, order_id="ord_repeat", revision=1)
    db.commit()
    assert only_award(db).posted_points == 120
    assert len(ledger_rows(db, user_id)) == 1
    consistency = loyalty_service.verify_account_consistency(db, user_id=user_id)
    assert consistency["posted_balance"] == 120 == consistency["ledger_total"]


def test_tc_l04_concurrent_recompute_posts_once(db: Any, commerce: FakeCommerce, clock: dict[str, int]) -> None:
    """两个连接并发重算同一来源：只入账一次，缓存与流水一致。"""
    user_id = make_user(db)
    unit = denominator_cents(db)
    commerce.add("ord_race", user_id=user_id, goods_cents=unit * 80, completed_at=clock["now"])
    clock["now"] += 7 * DAY_MS + 1
    db.commit()

    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def worker() -> None:
        def run() -> dict[str, Any]:
            with platform_db.uow(immediate=True) as unit_of_work:
                return recompute(unit_of_work.session, user_id=user_id, order_id="ord_race", revision=1)

        try:
            barrier.wait(timeout=10)  # 先对齐两个线程，再各自开写事务
            platform_db.retry_on_busy(run, attempts=4)
        except BaseException as exc:  # noqa: BLE001 - 测试需要看到并发异常
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, f"并发重算不应报错：{errors}"
    db.expire_all()
    assert only_award(db).posted_points == 80
    rows = ledger_rows(db, user_id)
    assert len(rows) == 1, "并发下只允许一条来源流水"
    consistency = loyalty_service.verify_account_consistency(db, user_id=user_id)
    assert consistency["consistent"] is True
    assert consistency["posted_balance"] == 80


# ------------------------------------------------------------------ 规则版本与端口边界
def test_rule_version_is_not_retroactive(db: Any, commerce: FakeCommerce) -> None:
    """规则版本在下单时保存：新版本只影响新订单，老奖励不重算。"""
    old_rule = loyalty_service.publish_rule(
        db,
        kind="purchase",
        parameters={"denominator_yuan": 5, "release_delay_days": 7, "shipping_excluded": True},
        published_by="stf_owner",
    )
    user_id = make_user(db)
    unit = 500
    commerce.add("ord_old", user_id=user_id, goods_cents=unit * 100)
    recompute(db, user_id=user_id, order_id="ord_old")
    db.commit()
    assert only_award(db).rule_version == old_rule.rule_version
    assert only_award(db).target_points == 100

    # 后台改规则：写站点设置 + 发布新版本
    db.add(
        SiteSetting(
            id=new_id("content"),
            key="loyalty.rules",
            value={"denominator_yuan": 10, "release_delay_days": 7, "shipping_excluded": True},
            group="loyalty",
        )
    )
    new_rule = loyalty_service.publish_rule(
        db,
        kind="purchase",
        parameters={"denominator_yuan": 10, "release_delay_days": 7, "shipping_excluded": True},
        published_by="stf_owner",
    )
    db.commit()
    assert new_rule.rule_version != old_rule.rule_version

    # 老订单按原版本重算，目标不变（不回溯）
    result = recompute(db, user_id=user_id, order_id="ord_old", revision=2)
    assert result["target_points"] == 100
    # 新订单按新版本：每 10 元 1 分
    commerce.add("ord_new", user_id=user_id, goods_cents=unit * 100)
    recompute(db, user_id=user_id, order_id="ord_new")
    db.commit()
    new_award = db.scalars(sa.select(PointAward).where(PointAward.source_id == "ord_new")).one()
    assert new_award.rule_version == new_rule.rule_version
    assert new_award.target_points == 50
    # 已发布版本不可修改：同参数重复发布返回既有版本
    assert (
        loyalty_service.publish_rule(db, kind="purchase", parameters=dict(new_rule.parameters)).rule_version
        == new_rule.rule_version
    )


def test_provider_unavailable_is_isolated(db: Any, monkeypatch: pytest.MonkeyPatch, clock: dict[str, int]) -> None:
    """商务模块未就绪时抛 PROVIDER_UNAVAILABLE，且到点入账跳过而不炸 worker。"""
    user_id = make_user(db)

    def missing(session: Any, order_id: str) -> dict[str, Any]:
        raise AppError("PROVIDER_UNAVAILABLE", "商务模块尚未就绪")

    monkeypatch.setattr(loyalty_ports, "order_snapshot", missing)
    with pytest.raises(AppError) as excinfo:
        recompute(db, user_id=user_id, order_id="ord_missing")
    assert excinfo.value.code == "PROVIDER_UNAVAILABLE"
    assert loyalty_service.post_due_awards(db, now=clock["now"]) == 0


def test_feedback_levels_map_to_c14_points(db: Any) -> None:
    """C14 分级：minor=5 / normal=20 / major=50（按等级给分，不按金额折算）。"""
    assert loyalty_service.target_points_for_feedback(db, level="minor") == 5
    assert loyalty_service.target_points_for_feedback(db, level="normal") == 20
    assert loyalty_service.target_points_for_feedback(db, level="major") == 50
    with pytest.raises(AppError) as excinfo:
        loyalty_service.target_points_for_feedback(db, level="unknown")
    assert excinfo.value.code == "VALIDATION_ERROR"


def test_adjustment_requires_separate_approver_and_reason(db: Any) -> None:
    """C21：申请与批准分离，入账只追加流水，重复批准不重复入账。"""
    user_id = make_user(db)
    row = loyalty_service.create_adjustment(
        db, user_id=user_id, delta_points=30, reason="补发漏发的活动奖励", requested_by="stf_a"
    )
    db.commit()
    with pytest.raises(AppError) as excinfo:
        loyalty_service.approve_adjustment(db, adjustment_id=row.id, approver_id="stf_a")
    assert excinfo.value.code == "FORBIDDEN"

    applied = loyalty_service.approve_adjustment(db, adjustment_id=row.id, approver_id="stf_b")
    db.commit()
    assert applied["status"] == "APPLIED"
    assert applied["posted_balance"] == 30
    again = loyalty_service.approve_adjustment(db, adjustment_id=row.id, approver_id="stf_b")
    db.commit()
    assert again["applied"] is False
    assert len(ledger_rows(db, user_id)) == 1
    assert db.get(PointAdjustment, row.id).status == "APPLIED"
    with pytest.raises(AppError) as excinfo:
        loyalty_service.create_adjustment(
            db, user_id=user_id, delta_points=0, reason="零分值不应被接受", requested_by="stf_a"
        )
    assert excinfo.value.code == "VALIDATION_ERROR"


def test_redemption_is_closed_until_c15(db: Any) -> None:
    """C15 未确认：兑换冻结接口必须拒绝，不宣传未上线权益。"""
    user_id = make_user(db)
    with pytest.raises(AppError) as excinfo:
        loyalty_service.reserve_points(db, user_id=user_id, redemption_id="rdm_1", points=10)
    assert excinfo.value.code == "FEATURE_DISABLED"
    assert loyalty_service.reservations_enabled(db) is False


def test_ledger_serialization_never_leaks_foreign_content(db: Any, commerce: FakeCommerce) -> None:
    """流水只给来源编号与业务描述，不含他人反馈正文。"""
    user_id = make_user(db)
    unit = denominator_cents(db)
    commerce.add("ord_view", user_id=user_id, goods_cents=unit * 4)
    recompute(db, user_id=user_id, order_id="ord_view")
    db.commit()
    items, total = loyalty_service.ledger_view(db, user_id=user_id, limit=10, offset=0)
    assert total == 0 and items == [], "未入账前没有流水"

    row = PointLedger(
        id=new_id("ledger"),
        account_id=loyalty_repo.ensure_account(db, user_id=user_id).id,
        user_id=user_id,
        delta=20,
        entry_type="AWARD",
        source_type="feedback",
        source_id="fbk_other",
        source_revision=1,
        rule_version="points-test",
        description="有效反馈奖励：有效可复现功能缺陷 20 星绒币",
        label="反馈奖励（fbk_other）",
    )
    db.add(row)
    db.flush()
    payload = loyalty_service.serialize_ledger_entry(row)
    assert payload["source"]["id"] == "fbk_other"
    assert "summary" in payload["source"]
    assert payload["appeal"]["available"] is True
    assert set(payload) >= {"delta", "description", "occurred_at", "adjustment_id"}
