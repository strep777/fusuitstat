"""委托模块领域规则单测：状态机、报价版本、名额并发、量体、阶段与变更单。

覆盖 13 文档用例：TC-C01（草稿版本与重复提交、终态不可越权修改）、
TC-C02（过期/旧版报价被拒、同一报价只生成一个订单）、TC-C04（两人争最后名额）、
TC-C05（旧版阶段确认 409）、C19（未成年委托监护人同意）、越权（404）。

环境说明：本文件自带 ``commission_env`` 夹具，用默认权限在
``server/.pytest-commissions/`` 下创建独立数据目录与独立 SQLite 文件，并执行
``get_settings.cache_clear()`` / ``reset_engine()`` / ``init_database()``，
隔离语义与 ``tests/conftest.py`` 的 ``settings`` 夹具一致，但不依赖共享的
``tmp_path`` 基准目录（该基准目录在本会话的受限文件沙箱下曾不可访问）。
"""

from __future__ import annotations

import shutil
import sys
import types
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

SERVER_DIR = Path(__file__).resolve().parents[2]

from app.modules.commissions import ports as commission_ports  # noqa: E402
from app.modules.commissions import repository as repo  # noqa: E402
from app.modules.commissions import service as svc  # noqa: E402
from app.modules.commissions.models import (  # noqa: E402
    MAX_APPLICATIONS_PER_ROUND,
    CommissionProject,
    Inquiry,
    Measurement,
    ProductionStage,
    Quote,
    ScheduleApplication,
    ScheduleRound,
    SlotReservation,
)
from app.modules.commissions.schemas import (  # noqa: E402
    InquiryCreate,
    InquiryPatch,
    InquirySupplementInput,
    MeasurementInput,
    QuoteLineItemInput,
    QuotePublishInput,
    ScheduleApplicationInput,
    ScheduleRoundInput,
    StageChangeInput,
    StageConfirmInput,
)
from app.modules.content.models import ContentEntry  # noqa: E402
from app.modules.contracts import CreatedOrder, OrderTotals  # noqa: E402
from app.modules.identity.models import User  # noqa: E402
from app.modules.identity.service import record_consent  # noqa: E402
from app.modules.media.models import MediaAsset  # noqa: E402
from app.modules.notifications.models import Delivery, Notification  # noqa: E402
from app.platform.config import get_settings  # noqa: E402
from app.platform.db import get_session_factory, init_database, reset_engine  # noqa: E402
from app.platform.errors import AppError  # noqa: E402
from app.platform.ids import add_days_ms, add_minutes_ms, iso, new_id, now_ms  # noqa: E402
from app.platform.reliability import AuditLog, OutboxEvent  # noqa: E402
from app.platform.security import encrypt_pii, phone_lookup  # noqa: E402

STAFF_ID = "stf_unit_test_staff"
_PHONE_SEQ = {"value": 0}


# ------------------------------------------------------------------ 夹具
@pytest.fixture
def commission_env() -> Iterator[Path]:
    """隔离的测试数据目录、配置缓存与引擎（不使用 tmp_path）。"""
    root = SERVER_DIR / ".pytest-commissions" / uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=True)
    import os

    os.environ["APP_ENV"] = "test"
    os.environ["DATA_DIR"] = str(root)
    os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{(root / 'commissions.db').as_posix()}"
    os.environ["PUBLIC_ORIGIN"] = "http://127.0.0.1:5173"
    # 测试库不需要金融级耐久性；NORMAL 与 conftest 的 settings 夹具保持一致。
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
def db(commission_env: Path) -> Iterator[Any]:
    """直连数据库的会话：用于准备数据、调用 service 与断言行内状态。"""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


class CommerceStub:
    """商务端口替身：记录建单/补款调用，便于断言“只生成一个订单”。"""

    def __init__(self) -> None:
        self.orders: list[dict[str, Any]] = []
        self.change_installments: list[dict[str, Any]] = []
        self.settled_orders: set[str] = set()

    def mark_settled(self, order_id: str) -> None:
        self.settled_orders.add(order_id)


@pytest.fixture
def commerce(monkeypatch: pytest.MonkeyPatch, db: Any) -> Iterator[CommerceStub]:
    """注入商务模块端口替身；``app.modules.commerce`` 由另一位工程师并行交付。"""
    stub = CommerceStub()

    def create_commission_order(
        session: Any,
        *,
        user_id: str,
        quote_id: str,
        quote_version: int,
        total_cents: int,
        prepayment_cents: int,
        currency: str,
        terms_version: str,
        inquiry_id: str,
        slot_reservation_id: str | None,
        idempotency_key: str,
    ) -> CreatedOrder:
        order_id = new_id("order")
        stub.orders.append(
            {
                "order_id": order_id,
                "quote_id": quote_id,
                "user_id": user_id,
                "total_cents": total_cents,
                "prepayment_cents": prepayment_cents,
                "currency": currency,
            }
        )
        return CreatedOrder(
            order_id=order_id,
            order_no=f"SO{len(stub.orders):08d}",
            status="PENDING_PAYMENT",
            installment_id=new_id("installment"),
            totals=OrderTotals(
                goods_cents=total_cents,
                discount_cents=0,
                shipping_cents=0,
                total_cents=total_cents,
                currency=currency,
            ),
            expires_at=now_ms() + 1_800_000,
        )

    package = types.ModuleType("app.modules.commerce")
    package.__path__ = []  # type: ignore[attr-defined]
    ports = types.ModuleType("app.modules.commerce.ports")
    ports.create_commission_order = create_commission_order  # type: ignore[attr-defined]
    package.ports = ports  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "app.modules.commerce", package)
    monkeypatch.setitem(sys.modules, "app.modules.commerce.ports", ports)

    # 支付端口替身：实收汇总用于尾款结清判定（宁可不发货，也不能跳过尾款校验）。
    def order_paid_summary(session: Any, *, order_id: str) -> dict[str, Any]:
        matched = next((item for item in stub.orders if item["order_id"] == order_id), None)
        if matched is None:
            return {"paid_cents": 0, "refunded_applied_cents": 0, "allocations": []}
        paid = matched["total_cents"] if order_id in stub.settled_orders else matched["prepayment_cents"]
        return {"paid_cents": paid, "refunded_applied_cents": 0, "allocations": []}

    payments_package = types.ModuleType("app.modules.payments")
    payments_package.__path__ = []  # type: ignore[attr-defined]
    payments_ports = types.ModuleType("app.modules.payments.ports")
    payments_ports.order_paid_summary = order_paid_summary  # type: ignore[attr-defined]
    payments_package.ports = payments_ports  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "app.modules.payments", payments_package)
    monkeypatch.setitem(sys.modules, "app.modules.payments.ports", payments_ports)
    yield stub


# ------------------------------------------------------------------ 数据准备
def make_user(db: Any, nickname: str = "星友") -> str:
    _PHONE_SEQ["value"] += 1
    phone = f"+8613801{_PHONE_SEQ['value']:06d}"
    user = User(
        id=new_id("user"),
        phone_ciphertext=encrypt_pii(phone),
        phone_lookup=phone_lookup(phone),
        phone_masked="+8613****0000",
        nickname=nickname,
    )
    db.add(user)
    db.flush()
    return user.id


def make_series(db: Any, *, status: str = "published") -> str:
    entry = ContentEntry(
        id=new_id("content"),
        kind="series",
        slug=f"series-{uuid.uuid4().hex[:8]}",
        title="测试系列",
        status=status,
        publish_at=now_ms(),
    )
    db.add(entry)
    db.flush()
    return entry.id


def make_asset(db: Any, owner_id: str, *, ready: bool = True, purpose: str = "design") -> str:
    asset = MediaAsset(
        id=new_id("asset"),
        owner_id=owner_id,
        purpose=purpose,
        visibility="private",
        object_key=f"design/{uuid.uuid4().hex}.png",
        mime="image/png",
        scan_status="ready" if ready else "pending",
    )
    db.add(asset)
    db.flush()
    return asset.id


def make_inquiry(db: Any, *, user_id: str, series_id: str, asset_id: str, **overrides: Any) -> Inquiry:
    payload = {
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
    payload.update(overrides)
    inquiry = svc.create_inquiry(db, user_id=user_id, payload=InquiryCreate(**payload))
    db.commit()
    return inquiry


def submitted_inquiry(db: Any, *, user_id: str, series_id: str, asset_id: str, **overrides: Any) -> Inquiry:
    inquiry = make_inquiry(db, user_id=user_id, series_id=series_id, asset_id=asset_id, **overrides)
    svc.submit_inquiry(db, user_id=user_id, inquiry_id=inquiry.id)
    db.commit()
    return inquiry


def publish_quote(
    db: Any,
    *,
    inquiry: Inquiry,
    total_cents: int = 500000,
    prepayment_cents: int | None = None,
    terms_version: str | None = None,
    expires_in_days: int | None = None,
) -> Quote:
    policy = commission_ports.commission_policy(db)
    quote = svc.publish_quote(
        db,
        inquiry_id=inquiry.id,
        actor_id=STAFF_ID,
        payload=QuotePublishInput(
            line_items=[
                QuoteLineItemInput(label="全装制作", quantity=1, unit_price_cents=total_cents),
            ],
            prepayment_cents=prepayment_cents,
            terms_version=terms_version,
            expires_at=iso(add_days_ms(expires_in_days))
            if expires_in_days is not None
            else iso(add_days_ms(int(policy["quote_valid_days"]))),
        ),
    )
    db.commit()
    return quote


def make_round(db: Any, *, capacity: int, mode: str = "application", status: str = "OPEN", **kw: Any) -> ScheduleRound:
    row = svc.create_schedule_round(
        db,
        actor_id=STAFF_ID,
        payload=ScheduleRoundInput(
            title="2026 春季档期",
            mode=mode,
            status=status,
            start_at=iso(add_days_ms(10)),
            end_at=iso(add_days_ms(40)),
            capacity=capacity,
            stage_label="全装档期",
            **kw,
        ),
    )
    db.commit()
    return row


def select_application(db: Any, *, round_row: ScheduleRound, user_id: str, inquiry: Inquiry) -> ScheduleApplication:
    application = svc.apply_to_round(
        db,
        user_id=user_id,
        round_id=round_row.id,
        payload=ScheduleApplicationInput(inquiry_id=inquiry.id),
    )
    svc.decide_application(
        db, application_id=application.id, actor_id=STAFF_ID, decision="SELECTED", reason="人工筛选通过"
    )
    db.commit()
    return application


def accepted_project(db: Any, *, commerce: CommerceStub, user_id: str, series_id: str, asset_id: str) -> tuple[Any, CommissionProject]:
    """完整走到“已接受报价 + 制作单”的准备函数。"""
    inquiry = submitted_inquiry(db, user_id=user_id, series_id=series_id, asset_id=asset_id)
    quote = publish_quote(db, inquiry=inquiry)
    order, project = svc.accept_quote_for_user(
        db,
        user_id=user_id,
        quote_id=quote.id,
        quote_version=quote.quote_version,
        terms_version=quote.terms_version,
        accepted=True,
    )
    db.commit()
    return order, project


def refresh(db: Any) -> Any:
    """结束只读事务，保证读到其他连接已提交的数据。"""
    db.rollback()
    return db


# ------------------------------------------------------------------ TC-C01
def test_tc_c01_draft_version_repeat_submit_and_terminal_state(db: Any) -> None:
    user_id = make_user(db)
    series_id = make_series(db)
    asset_id = make_asset(db, user_id)
    db.commit()

    inquiry = make_inquiry(db, user_id=user_id, series_id=series_id, asset_id=asset_id)
    assert inquiry.status == "DRAFT"
    assert inquiry.version == 1
    assert inquiry.current_revision == 1, "草稿也有明确版本，便于冲突检测"
    assert repo.max_revision(db, inquiry_id=inquiry.id) == 1

    # 版本不符必须 409，不能静默覆盖。
    with pytest.raises(AppError) as conflict:
        svc.update_inquiry(
            db, user_id=user_id, inquiry_id=inquiry.id, patch=InquiryPatch(version=99, species="狼")
        )
    assert conflict.value.code == "VERSION_CONFLICT"

    updated = svc.update_inquiry(
        db, user_id=user_id, inquiry_id=inquiry.id, patch=InquiryPatch(version=1, species="狼")
    )
    assert updated.version == 2
    assert updated.current_revision == 1, "草稿阶段只保留最新内容，不产生新版本"
    db.commit()

    submitted = svc.submit_inquiry(db, user_id=user_id, inquiry_id=inquiry.id)
    db.commit()
    assert submitted.status == "SUBMITTED"
    assert submitted.submitted_at is not None
    revision_after_submit = submitted.current_revision

    # 重复提交返回同一对象：状态、版本与提交时间都不变。
    again = svc.submit_inquiry(db, user_id=user_id, inquiry_id=inquiry.id)
    db.commit()
    assert again.id == submitted.id
    assert again.version == submitted.version
    assert again.submitted_at == submitted.submitted_at
    assert again.current_revision == revision_after_submit
    assert refresh(db).scalar(
        sa.select(sa.func.count()).select_from(Inquiry).where(Inquiry.id == inquiry.id)
    ) == 1

    # 提交后修改产生新版本，不覆盖工作人员已见版本。
    supplemented = svc.add_supplement(
        db,
        user_id=user_id,
        inquiry_id=inquiry.id,
        payload=InquirySupplementInput(version=again.version, requirements="追加磁吸角说明。"),
    )
    db.commit()
    assert supplemented.current_revision == revision_after_submit + 1
    assert repo.max_revision(db, inquiry_id=supplemented.id) == revision_after_submit + 1

    withdrawn = svc.withdraw_inquiry(db, user_id=user_id, inquiry_id=inquiry.id)
    db.commit()
    assert withdrawn.status == "WITHDRAWN"

    # 终态不可越权修改：编辑与提交被拒绝。
    for call in (
        lambda: svc.update_inquiry(
            db, user_id=user_id, inquiry_id=inquiry.id, patch=InquiryPatch(version=withdrawn.version, species="狐")
        ),
        lambda: svc.submit_inquiry(db, user_id=user_id, inquiry_id=inquiry.id),
    ):
        with pytest.raises(AppError) as terminal:
            call()
        assert terminal.value.code == "STATE_CONFLICT"
    # 重复撤回是幂等的：返回同一对象，而不是报错或产生第二份记录。
    again_withdrawn = svc.withdraw_inquiry(db, user_id=user_id, inquiry_id=inquiry.id)
    db.commit()
    assert again_withdrawn.id == inquiry.id
    assert again_withdrawn.status == "WITHDRAWN"
    db.rollback()


def test_inquiry_state_machine_paths(db: Any) -> None:
    user_id = make_user(db)
    series_id = make_series(db)
    asset_id = make_asset(db, user_id)
    db.commit()

    needs_info = submitted_inquiry(db, user_id=user_id, series_id=series_id, asset_id=asset_id)
    svc.mark_needs_info(db, inquiry_id=needs_info.id, actor_id=STAFF_ID, reason="请补充设定图背面")
    db.commit()
    assert refresh(db).get(Inquiry, needs_info.id).status == "NEEDS_INFO"

    # NEEDS_INFO → SUBMITTED（客户补齐）
    resubmitted = svc.submit_inquiry(db, user_id=user_id, inquiry_id=needs_info.id)
    db.commit()
    assert resubmitted.status == "SUBMITTED"

    # SUBMITTED → QUOTED → SUBMITTED（要求重新评估）
    quote = publish_quote(db, inquiry=needs_info)
    assert refresh(db).get(Inquiry, needs_info.id).status == "QUOTED"
    requoted = svc.add_supplement(
        db,
        user_id=user_id,
        inquiry_id=needs_info.id,
        payload=InquirySupplementInput(version=refresh(db).get(Inquiry, needs_info.id).version, note="要求重新评估"),
    )
    db.commit()
    assert requoted.status == "SUBMITTED"
    assert quote.status == "PUBLISHED", "旧报价保留为历史版本，不删除"

    # SUBMITTED → REJECTED 必须说明原因
    with pytest.raises(AppError) as no_reason:
        svc.reject_inquiry(db, inquiry_id=needs_info.id, actor_id=STAFF_ID, reason="  ")
    assert no_reason.value.code == "VALIDATION_ERROR"
    rejected = svc.reject_inquiry(db, inquiry_id=needs_info.id, actor_id=STAFF_ID, reason="档期已满")
    db.commit()
    assert rejected.status == "REJECTED"
    with pytest.raises(AppError) as conflict:
        svc.withdraw_inquiry(db, user_id=user_id, inquiry_id=needs_info.id)
    assert conflict.value.code == "STATE_CONFLICT"
    db.rollback()


def test_quoted_inquiry_expires_when_quote_expires(db: Any) -> None:
    user_id = make_user(db)
    series_id = make_series(db)
    asset_id = make_asset(db, user_id)
    db.commit()

    inquiry = submitted_inquiry(db, user_id=user_id, series_id=series_id, asset_id=asset_id)
    quote = publish_quote(db, inquiry=inquiry, expires_in_days=1)
    # 把有效期改到过去，模拟到期任务扫描到的行。
    quote.expires_at = now_ms() - 1000
    db.commit()

    assert svc.expire_quotes(db, moment=now_ms()) == 1
    db.commit()
    assert refresh(db).get(Quote, quote.id).status == "EXPIRED"
    assert refresh(db).get(Inquiry, inquiry.id).status == "EXPIRED"
    db.rollback()


def test_inquiry_form_validation_rules(db: Any) -> None:
    user_id = make_user(db)
    other_id = make_user(db, "别人")
    series_id = make_series(db)
    draft_series = make_series(db, status="draft")
    asset_id = make_asset(db, user_id)
    foreign_asset = make_asset(db, other_id)
    pending_asset = make_asset(db, user_id, ready=False)
    db.commit()

    def create(**overrides: Any) -> Any:
        payload = {
            "character_name": "逐星绒龙",
            "species": "龙",
            "series_id": series_id,
            "design_asset_ids": [asset_id],
            "rights_declaration": True,
        }
        payload.update(overrides)
        return svc.create_inquiry(db, user_id=user_id, payload=InquiryCreate(**payload))

    with pytest.raises(AppError) as rights:
        create(rights_declaration=False)
    assert rights.value.code == "VALIDATION_ERROR"
    assert "rights_declaration" in rights.value.fields

    with pytest.raises(AppError) as budget:
        create(budget_min_cents=900000, budget_max_cents=100000)
    assert budget.value.code == "VALIDATION_ERROR"

    with pytest.raises(AppError) as series:
        create(series_id=draft_series)
    assert series.value.code == "VALIDATION_ERROR"

    with pytest.raises(AppError) as foreign:
        create(design_asset_ids=[foreign_asset])
    assert foreign.value.code == "UPLOAD_NOT_READY"

    with pytest.raises(AppError) as pending:
        create(design_asset_ids=[pending_asset])
    assert pending.value.code == "UPLOAD_NOT_READY"

    # 提交时必须有 1–3 张设定图
    inquiry = create(design_asset_ids=[])
    db.commit()
    with pytest.raises(AppError) as no_assets:
        svc.submit_inquiry(db, user_id=user_id, inquiry_id=inquiry.id)
    assert no_assets.value.code == "VALIDATION_ERROR"
    db.rollback()


# ------------------------------------------------------------------ TC-C02
def test_tc_c02_stale_and_expired_quote_are_rejected(db: Any, commerce: CommerceStub) -> None:
    user_id = make_user(db)
    series_id = make_series(db)
    asset_id = make_asset(db, user_id)
    db.commit()

    inquiry = submitted_inquiry(db, user_id=user_id, series_id=series_id, asset_id=asset_id)
    first = publish_quote(db, inquiry=inquiry, total_cents=400000)
    second = publish_quote(db, inquiry=inquiry, total_cents=520000)
    assert second.quote_version == 2
    assert refresh(db).get(Quote, first.id).status == "SUPERSEDED", "发布新版本把旧版设为 SUPERSEDED"

    # 旧版本被拒绝
    with pytest.raises(AppError) as changed:
        svc.accept_quote_for_user(
            db,
            user_id=user_id,
            quote_id=first.id,
            quote_version=first.quote_version,
            terms_version=first.terms_version,
            accepted=True,
        )
    assert changed.value.code == "QUOTE_CHANGED"

    # 当前版本号不符也被拒绝
    with pytest.raises(AppError) as stale:
        svc.accept_quote_for_user(
            db,
            user_id=user_id,
            quote_id=second.id,
            quote_version=1,
            terms_version=second.terms_version,
            accepted=True,
        )
    assert stale.value.code == "QUOTE_CHANGED"

    # 过期报价被拒绝
    second.expires_at = now_ms() - 1
    db.commit()
    with pytest.raises(AppError) as expired:
        svc.accept_quote_for_user(
            db,
            user_id=user_id,
            quote_id=second.id,
            quote_version=second.quote_version,
            terms_version=second.terms_version,
            accepted=True,
        )
    assert expired.value.code == "QUOTE_EXPIRED"

    # 条款版本不符被拒绝
    second.expires_at = add_days_ms(7)
    db.commit()
    with pytest.raises(AppError) as terms:
        svc.accept_quote_for_user(
            db,
            user_id=user_id,
            quote_id=second.id,
            quote_version=second.quote_version,
            terms_version="commission-999",
            accepted=True,
        )
    assert terms.value.code == "QUOTE_CHANGED"

    # 未勾选同意条款被拒绝
    with pytest.raises(AppError) as not_accepted:
        svc.accept_quote_for_user(
            db,
            user_id=user_id,
            quote_id=second.id,
            quote_version=second.quote_version,
            terms_version=second.terms_version,
            accepted=False,
        )
    assert not_accepted.value.code == "VALIDATION_ERROR"
    assert commerce.orders == []
    db.rollback()


def test_tc_c02_two_tabs_accept_creates_single_order(db: Any, commerce: CommerceStub) -> None:
    user_id = make_user(db)
    series_id = make_series(db)
    asset_id = make_asset(db, user_id)
    db.commit()

    inquiry = submitted_inquiry(db, user_id=user_id, series_id=series_id, asset_id=asset_id)
    quote = publish_quote(db, inquiry=inquiry)

    # 条件 UPDATE 的 rowcount 语义：第一个连接拿到 1，第二个拿到 0。
    assert svc.claim_quote(db, quote=quote, moment=now_ms()) is True
    db.commit()
    assert svc.claim_quote(db, quote=refresh(db).get(Quote, quote.id), moment=now_ms()) is False
    db.rollback()
    # 还原为可接受状态，继续验证完整接受流程。
    quote = refresh(db).get(Quote, quote.id)
    quote.status = "PUBLISHED"
    quote.accepted_at = None
    db.commit()

    first_order, project = svc.accept_quote_for_user(
        db,
        user_id=user_id,
        quote_id=quote.id,
        quote_version=quote.quote_version,
        terms_version=quote.terms_version,
        accepted=True,
        idempotency_key="tab-1",
    )
    db.commit()

    # 第二个标签页（不同幂等键）重复接受：返回同一订单，不再建单。
    second_order, second_project = svc.accept_quote_for_user(
        db,
        user_id=user_id,
        quote_id=quote.id,
        quote_version=quote.quote_version,
        terms_version=quote.terms_version,
        accepted=True,
        idempotency_key="tab-2",
    )
    db.commit()

    assert first_order.order_id == second_order.order_id
    assert second_project.id == project.id
    assert len(commerce.orders) == 1, "同一报价只生成一个订单"
    assert refresh(db).scalar(
        sa.select(sa.func.count()).select_from(CommissionProject).where(CommissionProject.quote_id == quote.id)
    ) == 1
    accepted = refresh(db).get(Quote, quote.id)
    assert accepted.status == "ACCEPTED"
    assert accepted.order_id == first_order.order_id
    assert accepted.contract_snapshot["order_id"] == first_order.order_id
    assert refresh(db).get(Inquiry, inquiry.id).status == "ACCEPTED"
    db.rollback()


def test_accept_records_terms_consent_and_snapshot(db: Any, commerce: CommerceStub) -> None:
    user_id = make_user(db)
    series_id = make_series(db)
    asset_id = make_asset(db, user_id)
    db.commit()

    order, project = accepted_project(
        db, commerce=commerce, user_id=user_id, series_id=series_id, asset_id=asset_id
    )
    consent = refresh(db).execute(
        sa.text(
            "SELECT purpose, document_version, content_hash, resource_type, resource_id "
            "FROM consents WHERE user_id = :uid"
        ),
        {"uid": user_id},
    ).one()
    assert consent.purpose == "commission_rights"
    assert consent.resource_type == "quote"
    assert consent.document_version == "commission-1"
    assert len(consent.content_hash) == 64, "留证必须记录条款摘要哈希"
    snapshot = refresh(db).get(CommissionProject, project.id)
    assert snapshot.order_id == order.order_id
    assert snapshot.balance_due_at is not None
    assert snapshot.total_cents == 500000
    assert snapshot.prepayment_cents == 250000, "预付款比例来自 commission_policy（50%）"
    db.rollback()


def test_quote_ownership_and_ownership_leak_protection(db: Any, commerce: CommerceStub) -> None:
    owner_id = make_user(db)
    stranger_id = make_user(db, "陌生人")
    series_id = make_series(db)
    asset_id = make_asset(db, owner_id)
    db.commit()

    inquiry = submitted_inquiry(db, user_id=owner_id, series_id=series_id, asset_id=asset_id)
    quote = publish_quote(db, inquiry=inquiry)

    with pytest.raises(AppError) as not_found:
        svc.accept_quote_for_user(
            db,
            user_id=stranger_id,
            quote_id=quote.id,
            quote_version=quote.quote_version,
            terms_version=quote.terms_version,
            accepted=True,
        )
    assert not_found.value.code == "RESOURCE_NOT_FOUND"
    assert commerce.orders == []

    with pytest.raises(AppError) as cross_inquiry:
        svc.accept_quote_for_user(
            db,
            user_id=owner_id,
            quote_id=quote.id,
            inquiry_id="inq_not_exists",
            quote_version=quote.quote_version,
            terms_version=quote.terms_version,
            accepted=True,
        )
    assert cross_inquiry.value.code == "RESOURCE_NOT_FOUND"
    db.rollback()


def test_c19_guardian_consent_required_for_minor_commission(db: Any, commerce: CommerceStub) -> None:
    user_id = make_user(db)
    series_id = make_series(db)
    asset_id = make_asset(db, user_id)
    db.commit()

    inquiry = submitted_inquiry(
        db, user_id=user_id, series_id=series_id, asset_id=asset_id, guardian_required=True
    )
    quote = publish_quote(db, inquiry=inquiry)

    with pytest.raises(AppError) as blocked:
        svc.accept_quote_for_user(
            db,
            user_id=user_id,
            quote_id=quote.id,
            quote_version=quote.quote_version,
            terms_version=quote.terms_version,
            accepted=True,
        )
    assert blocked.value.code == "STATE_CONFLICT"
    assert "未成年委托需要监护人同意与额外签署监护人条款。" in str(blocked.value)
    db.rollback()

    record_consent(db, user_id=user_id, purpose="miner_guardian", version="guardian-1")
    db.commit()
    order, project = svc.accept_quote_for_user(
        db,
        user_id=user_id,
        quote_id=quote.id,
        quote_version=quote.quote_version,
        terms_version=quote.terms_version,
        accepted=True,
    )
    db.commit()
    assert order.order_id
    assert project.status == "QUEUED"
    db.rollback()


# ------------------------------------------------------------------ TC-C04
def test_tc_c04_capacity_blocks_second_accept(db: Any, commerce: CommerceStub) -> None:
    first_user = make_user(db)
    second_user = make_user(db, "第二位")
    series_id = make_series(db)
    first_asset = make_asset(db, first_user)
    second_asset = make_asset(db, second_user)
    db.commit()

    round_row = make_round(db, capacity=1)
    first_inquiry = submitted_inquiry(db, user_id=first_user, series_id=series_id, asset_id=first_asset)
    second_inquiry = submitted_inquiry(db, user_id=second_user, series_id=series_id, asset_id=second_asset)
    select_application(db, round_row=round_row, user_id=first_user, inquiry=first_inquiry)
    select_application(db, round_row=round_row, user_id=second_user, inquiry=second_inquiry)
    first_quote = publish_quote(db, inquiry=first_inquiry)
    second_quote = publish_quote(db, inquiry=second_inquiry)

    order, project = svc.accept_quote_for_user(
        db,
        user_id=first_user,
        quote_id=first_quote.id,
        quote_version=first_quote.quote_version,
        terms_version=first_quote.terms_version,
        accepted=True,
    )
    db.commit()
    reserved = refresh(db).get(ScheduleRound, round_row.id)
    assert (reserved.reserved_count, reserved.committed_count) == (1, 0)
    reservation = refresh(db).scalar(
        sa.select(SlotReservation).where(SlotReservation.order_id == order.order_id)
    )
    assert reservation.state == "RESERVED"
    assert reservation.expires_at > now_ms(), "预留用于付款，带 slot_hold_minutes 期限"

    with pytest.raises(AppError) as no_capacity:
        svc.accept_quote_for_user(
            db,
            user_id=second_user,
            quote_id=second_quote.id,
            quote_version=second_quote.quote_version,
            terms_version=second_quote.terms_version,
            accepted=True,
        )
    assert no_capacity.value.code == "NO_CAPACITY"
    db.rollback()

    row = refresh(db).get(ScheduleRound, round_row.id)
    assert row.reserved_count + row.committed_count <= row.capacity
    assert refresh(db).scalar(
        sa.select(sa.func.count()).select_from(CommissionProject).where(CommissionProject.id == project.id)
    ) == 1
    assert len(commerce.orders) == 1
    db.rollback()


def test_capacity_check_constraint_rejects_overbooking(db: Any) -> None:
    round_row = make_round(db, capacity=1)
    with pytest.raises(IntegrityError):
        db.execute(
            sa.update(ScheduleRound).where(ScheduleRound.id == round_row.id).values(reserved_count=2)
        )
        db.flush()
    db.rollback()
    assert refresh(db).get(ScheduleRound, round_row.id).reserved_count == 0
    db.rollback()


def test_two_threads_compete_for_last_slot(commission_env: Path, commerce: CommerceStub) -> None:
    """真实并发：两个写事务争最后一个名额，只有一个能占用成功。"""
    _ = commerce
    setup = get_session_factory()()
    try:
        first_user = make_user(setup)
        second_user = make_user(setup, "并发乙")
        series_id = make_series(setup)
        first_asset = make_asset(setup, first_user)
        second_asset = make_asset(setup, second_user)
        setup.commit()
        round_row = make_round(setup, capacity=1)
        first_inquiry = submitted_inquiry(
            setup, user_id=first_user, series_id=series_id, asset_id=first_asset
        )
        second_inquiry = submitted_inquiry(
            setup, user_id=second_user, series_id=series_id, asset_id=second_asset
        )
        select_application(setup, round_row=round_row, user_id=first_user, inquiry=first_inquiry)
        select_application(setup, round_row=round_row, user_id=second_user, inquiry=second_inquiry)
        first_quote = publish_quote(setup, inquiry=first_inquiry)
        second_quote = publish_quote(setup, inquiry=second_inquiry)
        round_id = round_row.id
        requests = [
            (first_user, first_quote.id, first_quote.quote_version, first_quote.terms_version),
            (second_user, second_quote.id, second_quote.quote_version, second_quote.terms_version),
        ]
    finally:
        setup.close()

    def attempt(index: int) -> str:
        session = get_session_factory()()
        try:
            # 写路径以 BEGIN IMMEDIATE 打开事务（08/04 文档），避免退化锁竞争。
            session.execute(sa.text("BEGIN IMMEDIATE"))
            user_id, quote_id, version, terms = requests[index]
            try:
                svc.accept_quote_for_user(
                    session,
                    user_id=user_id,
                    quote_id=quote_id,
                    quote_version=version,
                    terms_version=terms,
                    accepted=True,
                )
            except AppError as exc:
                session.rollback()
                return exc.code
            session.commit()
            return "OK"
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, (0, 1)))

    assert sorted(outcomes) == ["NO_CAPACITY", "OK"], outcomes
    check = get_session_factory()()
    try:
        row = check.get(ScheduleRound, round_id)
        assert row.reserved_count + row.committed_count == 1
        assert int(
            check.scalar(
                sa.select(sa.func.count())
                .select_from(SlotReservation)
                .where(SlotReservation.round_id == round_id)
            )
            or 0
        ) == 1
    finally:
        check.close()


def test_slot_expire_then_release_returns_capacity(db: Any, commerce: CommerceStub) -> None:
    user_id = make_user(db)
    series_id = make_series(db)
    asset_id = make_asset(db, user_id)
    db.commit()

    round_row = make_round(db, capacity=1)
    inquiry = submitted_inquiry(db, user_id=user_id, series_id=series_id, asset_id=asset_id)
    select_application(db, round_row=round_row, user_id=user_id, inquiry=inquiry)
    quote = publish_quote(db, inquiry=inquiry)
    order, _project = svc.accept_quote_for_user(
        db,
        user_id=user_id,
        quote_id=quote.id,
        quote_version=quote.quote_version,
        terms_version=quote.terms_version,
        accepted=True,
    )
    db.commit()

    reservation = refresh(db).scalar(
        sa.select(SlotReservation).where(SlotReservation.order_id == order.order_id)
    )
    reservation.expires_at = now_ms() - 1000
    db.commit()

    # 预留超时进入关闭核对（不做自动释放），并登记 outbox 事件
    assert svc.expire_slot_reservations(db, moment=now_ms()) == 1
    db.commit()
    assert refresh(db).get(SlotReservation, reservation.id).state == "CLOSING"
    events = list(
        refresh(db).scalars(
            sa.select(OutboxEvent).where(OutboxEvent.event_type == "schedule.slot_expiring")
        )
    )
    assert events and events[0].payload["order_id"] == order.order_id

    # 核对确认无在途付款后释放，容量回收
    assert svc.release_slot_for_order(db, order_id=order.order_id, reason="渠道无在途付款") is True
    db.commit()
    released = refresh(db).get(ScheduleRound, round_row.id)
    assert (released.reserved_count, released.committed_count) == (0, 0)
    assert svc.release_slot_for_order(db, order_id=order.order_id, reason="重复释放") is False
    db.rollback()


def test_slot_commit_on_prepayment_is_final(db: Any, commerce: CommerceStub) -> None:
    user_id = make_user(db)
    series_id = make_series(db)
    asset_id = make_asset(db, user_id)
    db.commit()

    round_row = make_round(db, capacity=1)
    inquiry = submitted_inquiry(db, user_id=user_id, series_id=series_id, asset_id=asset_id)
    select_application(db, round_row=round_row, user_id=user_id, inquiry=inquiry)
    quote = publish_quote(db, inquiry=inquiry)
    order, _project = svc.accept_quote_for_user(
        db,
        user_id=user_id,
        quote_id=quote.id,
        quote_version=quote.quote_version,
        terms_version=quote.terms_version,
        accepted=True,
    )
    db.commit()

    # 已付预付款转正式占用（committed_count）
    assert svc.commit_slot_on_prepayment(db, order_id=order.order_id) is True
    db.commit()
    counted = refresh(db).get(ScheduleRound, round_row.id)
    assert (counted.reserved_count, counted.committed_count) == (0, 1)
    assert svc.commit_slot_on_prepayment(db, order_id=order.order_id) is False, "重复核销不再计数"
    db.rollback()

    # 已正式占用的名额不能因关单自动释放
    with pytest.raises(AppError) as committed:
        svc.release_slot_for_order(db, order_id=order.order_id, reason="关单")
    assert committed.value.code == "STATE_CONFLICT"
    db.rollback()
    still = refresh(db).get(ScheduleRound, round_row.id)
    assert (still.reserved_count, still.committed_count) == (0, 1)
    db.rollback()


# ------------------------------------------------------------------ 档期申请（C11）
def test_application_limit_and_idempotency(db: Any) -> None:
    user_id = make_user(db)
    series_id = make_series(db)
    asset_id = make_asset(db, user_id)
    db.commit()

    round_row = make_round(db, capacity=5)
    inquiries = [
        submitted_inquiry(db, user_id=user_id, series_id=series_id, asset_id=asset_id)
        for _ in range(MAX_APPLICATIONS_PER_ROUND + 1)
    ]
    applications = [
        svc.apply_to_round(
            db,
            user_id=user_id,
            round_id=round_row.id,
            payload=ScheduleApplicationInput(inquiry_id=inquiry.id),
            idempotency_key=f"apply-{index}",
        )
        for index, inquiry in enumerate(inquiries[:MAX_APPLICATIONS_PER_ROUND])
    ]
    db.commit()
    assert len(applications) == MAX_APPLICATIONS_PER_ROUND

    # 同轮同询价重复提交返回原对象（幂等）
    duplicate = svc.apply_to_round(
        db,
        user_id=user_id,
        round_id=round_row.id,
        payload=ScheduleApplicationInput(inquiry_id=inquiries[0].id),
        idempotency_key="apply-0",
    )
    assert duplicate.id == applications[0].id

    with pytest.raises(AppError) as limited:
        svc.apply_to_round(
            db,
            user_id=user_id,
            round_id=round_row.id,
            payload=ScheduleApplicationInput(inquiry_id=inquiries[MAX_APPLICATIONS_PER_ROUND].id),
        )
    assert limited.value.code == "STATE_CONFLICT"
    db.rollback()


def test_application_window_and_rush_flag(db: Any) -> None:
    user_id = make_user(db)
    series_id = make_series(db)
    asset_id = make_asset(db, user_id)
    db.commit()
    inquiry = submitted_inquiry(db, user_id=user_id, series_id=series_id, asset_id=asset_id)

    closed = make_round(db, capacity=3, status="CLOSED")
    with pytest.raises(AppError) as schedule_closed:
        svc.apply_to_round(
            db,
            user_id=user_id,
            round_id=closed.id,
            payload=ScheduleApplicationInput(inquiry_id=inquiry.id),
        )
    assert schedule_closed.value.code == "SCHEDULE_CLOSED"

    future = make_round(db, capacity=3, opens_at=iso(add_days_ms(1)), closes_at=iso(add_days_ms(2)))
    with pytest.raises(AppError) as not_open:
        svc.apply_to_round(
            db,
            user_id=user_id,
            round_id=future.id,
            payload=ScheduleApplicationInput(inquiry_id=inquiry.id),
        )
    assert not_open.value.code == "SCHEDULE_CLOSED"

    # C11 限时申请默认关闭：入口隐藏且后端拒绝
    with pytest.raises(AppError) as disabled:
        make_round(db, capacity=3, mode="rush")
    assert disabled.value.code == "FEATURE_DISABLED"
    db.rollback()


def test_round_public_summary_is_anonymous(db: Any) -> None:
    user_id = make_user(db)
    series_id = make_series(db)
    asset_id = make_asset(db, user_id)
    db.commit()
    round_row = make_round(db, capacity=2)
    inquiry = submitted_inquiry(db, user_id=user_id, series_id=series_id, asset_id=asset_id)
    select_application(db, round_row=round_row, user_id=user_id, inquiry=inquiry)
    quote = publish_quote(db, inquiry=inquiry)
    svc.accept_quote_for_user(
        db,
        user_id=user_id,
        quote_id=quote.id,
        quote_version=quote.quote_version,
        terms_version=quote.terms_version,
        accepted=True,
    )
    db.commit()

    dto = svc.round_public_dto(refresh(db).get(ScheduleRound, round_row.id))
    payload = dto.model_dump()
    assert payload["remaining_bucket"] == "limited", "余量只给粗粒度摘要"
    assert payload["timezone"] == "Asia/Shanghai"
    assert payload["start_at"] and payload["end_at"]
    # 公开摘要不含任何申请人身份、联系方式或订单信息
    for forbidden in ("phone", "phone_masked", "user_id", "nickname", "order_id", "applicants"):
        assert forbidden not in payload
    assert "capacity" not in payload and "reserved_count" not in payload
    db.rollback()


# ------------------------------------------------------------------ TC-C05
def test_tc_c05_confirming_stale_stage_revision_conflicts(db: Any, commerce: CommerceStub) -> None:
    user_id = make_user(db)
    other_user = make_user(db, "越权用户")
    series_id = make_series(db)
    asset_id = make_asset(db, user_id)
    db.commit()

    _order, project = accepted_project(
        db, commerce=commerce, user_id=user_id, series_id=series_id, asset_id=asset_id
    )
    stage = svc.upsert_production_stage(
        db,
        project_id=project.id,
        actor_id=STAFF_ID,
        stage_code="DESIGN_CONFIRMING",
        description="第一版设定分解",
        media_asset_ids=["ast_v1"],
        needs_customer_confirmation=True,
    )
    db.commit()
    assert stage.revision == 1
    assert stage.status == "AWAITING_CUSTOMER"

    # 装师上传新阶段图 → 新 revision
    updated = svc.upsert_production_stage(
        db,
        project_id=project.id,
        actor_id=STAFF_ID,
        stage_code="DESIGN_CONFIRMING",
        description="第二版设定分解（调整了角部结构）",
        media_asset_ids=["ast_v2"],
    )
    db.commit()
    assert updated.revision == 2

    # 客户确认旧版 → 409
    with pytest.raises(AppError) as stale:
        svc.confirm_stage(
            db, user_id=user_id, stage_id=stage.id, payload=StageConfirmInput(revision=1)
        )
    assert stale.value.code == "VERSION_CONFLICT"
    db.rollback()

    # 旧版确认不写入任何确认信息
    unchanged = refresh(db).get(ProductionStage, stage.id)
    assert unchanged.confirmed_revision is None
    assert unchanged.approved_by is None

    # 越权用户不能确认
    with pytest.raises(AppError) as not_found:
        svc.confirm_stage(
            db, user_id=other_user, stage_id=stage.id, payload=StageConfirmInput(revision=2)
        )
    assert not_found.value.code == "RESOURCE_NOT_FOUND"

    # 客户确认当前版本：绑定 revision 与确认人
    confirmed = svc.confirm_stage(
        db, user_id=user_id, stage_id=stage.id, payload=StageConfirmInput(revision=2)
    )
    db.commit()
    assert confirmed.confirmed_revision == 2
    assert confirmed.approved_by == user_id, "确认人恒为当前登录客户，员工不能代签"
    assert confirmed.status == "CONFIRMED"
    db.rollback()


def test_stage_change_request_creates_change_without_overwriting_price(db: Any, commerce: CommerceStub) -> None:
    user_id = make_user(db)
    series_id = make_series(db)
    asset_id = make_asset(db, user_id)
    db.commit()

    _order, project = accepted_project(
        db, commerce=commerce, user_id=user_id, series_id=series_id, asset_id=asset_id
    )
    stage = svc.upsert_production_stage(
        db,
        project_id=project.id,
        actor_id=STAFF_ID,
        stage_code="PRODUCING",
        description="主体完成",
        needs_customer_confirmation=True,
    )
    db.commit()

    change = svc.request_stage_change(
        db,
        user_id=user_id,
        stage_id=stage.id,
        payload=StageChangeInput(revision=stage.revision, description="希望增加尾巴长度 20cm"),
    )
    db.commit()
    assert change.status == "SUBMITTED"
    assert change.requested_by_type == "customer"
    assert refresh(db).get(ProductionStage, stage.id).status == "CHANGE_REQUESTED"

    # 员工提出加项变更：明确新金额、延期与条款版本
    proposal = svc.propose_change(
        db,
        project_id=project.id,
        actor_id=STAFF_ID,
        scope="addition",
        description="尾巴加长 20cm",
        delta_cents=80000,
        schedule_delta_days=14,
        terms_version="commission-1",
    )
    db.commit()
    assert proposal.new_total_cents == refresh(db).get(CommissionProject, project.id).total_cents + 80000

    accepted = svc.accept_change_request(
        db, user_id=user_id, change_request_id=proposal.id, project_id=project.id
    )
    db.commit()
    assert accepted.status == "ACCEPTED"
    assert accepted.accepted_at is not None

    # 已接受版本不可覆盖：重复接受返回同一行（版本与金额不变）
    again = svc.accept_change_request(
        db, user_id=user_id, change_request_id=proposal.id, project_id=project.id
    )
    db.commit()
    assert again.version == accepted.version
    assert again.delta_cents == 80000
    stored = refresh(db).get(CommissionProject, project.id)
    assert stored.total_cents == 500000, "原合同金额不被覆写"
    assert stored.estimated_delivery is not None, "延期写入预计交付时间"
    db.rollback()


def test_change_acceptance_records_outbox_when_commerce_port_missing(db: Any) -> None:
    """商务端口不可用时只落变更单并登记 order.change_accepted，绝不静默加价。"""
    from app.modules.commissions import ports as commission_ports

    user_id = make_user(db)
    series_id = make_series(db)
    asset_id = make_asset(db, user_id)
    db.commit()
    inquiry = submitted_inquiry(db, user_id=user_id, series_id=series_id, asset_id=asset_id)
    quote = publish_quote(db, inquiry=inquiry)
    project = CommissionProject(
        id=new_id("project"),
        order_id=new_id("order"),
        inquiry_id=inquiry.id,
        quote_id=quote.id,
        user_id=user_id,
        status="PRODUCING",
        currency="CNY",
        total_cents=quote.total_cents,
        prepayment_cents=quote.prepayment_cents,
        terms_version=quote.terms_version,
    )
    db.add(project)
    db.flush()
    proposal = svc.propose_change(
        db,
        project_id=project.id,
        actor_id=STAFF_ID,
        scope="addition",
        description="增加发光配件",
        delta_cents=30000,
        terms_version="commission-1",
    )
    db.commit()

    original = commission_ports.create_change_installment

    def unavailable(*args: Any, **kwargs: Any) -> None:
        return None

    commission_ports.create_change_installment = unavailable  # type: ignore[assignment]
    try:
        accepted = svc.accept_change_request(
            db, user_id=user_id, change_request_id=proposal.id, project_id=project.id
        )
        db.commit()
    finally:
        commission_ports.create_change_installment = original  # type: ignore[assignment]

    assert accepted.status == "ACCEPTED"
    assert accepted.installment_id is None
    events = list(
        refresh(db).scalars(
            sa.select(OutboxEvent).where(OutboxEvent.event_type == "order.change_accepted")
        )
    )
    assert len(events) == 1
    assert events[0].payload["change_request_id"] == proposal.id
    assert events[0].payload["amount_cents"] == 30000
    db.rollback()


# ------------------------------------------------------------------ 制作单推进
class EsignSandboxOverride:
    """把电子签渠道换成沙箱实例的上下文管理器（签署回调走真实验签路径）。"""

    def __init__(self) -> None:
        from app.adapters.esign.sandbox import SandboxEsignGateway
        from app.platform.config import Settings

        self.gateway = SandboxEsignGateway(
            Settings(
                _env_file=None,
                app_env="test",
                public_origin="http://127.0.0.1:5173",
                esign_provider="sandbox",
                esign_webhook_secret="esign-webhook-test-0123456789",
            )  # type: ignore[arg-type]
        )

    def __enter__(self) -> Any:
        from app.adapters.esign import set_esign_gateway_override

        set_esign_gateway_override(self.gateway)
        return self.gateway

    def __exit__(self, *_exc: object) -> None:
        from app.adapters.esign import reset_esign_gateway, set_esign_gateway_override

        set_esign_gateway_override(None)
        reset_esign_gateway()


def sign_contract_for_test(db: Any, *, project_id: str, gateway: Any) -> None:
    """把该制作单的合同签署任务标记为已签署（C07 开工门禁的测试前置）。

    通过**真实回调路径**完成：渠道签发 → 平台回调 → 验签 → 状态机，不直接改数据库字段。
    """
    from app.modules.commissions import esign_service

    envelope = esign_service.envelope_for_project(db, project_id=project_id)
    if envelope is None:
        return
    roles = [str(item["role"]) for item in (envelope.signers or [])]
    headers, body = gateway.sign_notification(document_no=envelope.id, signed_roles=roles)
    result = esign_service.apply_esign_notification(
        db, headers=headers, raw_body=body, provider="sandbox"
    )
    assert result["accepted"] and result["status"] == "SIGNED", result
    # 必须提交：签署状态是后续断言的前置条件，不能随后续 rollback 一起回滚。
    db.commit()


def test_project_advance_requires_sequential_and_settled_balance(db: Any, commerce: CommerceStub) -> None:
    user_id = make_user(db)
    series_id = make_series(db)
    asset_id = make_asset(db, user_id)
    db.commit()
    # C07：接受报价后会自动登记合同签署任务；本用例关注状态机与尾款，先把合同签掉。
    # 沙箱渠道要在**接受报价之前**装上：签署任务由当时的渠道实例签发，事后换渠道实例
    # 会得到 unknown_document（这本身就是"回调必须对应真实签署任务"的保护）。
    with EsignSandboxOverride() as gateway:
        order, project = accepted_project(
            db, commerce=commerce, user_id=user_id, series_id=series_id, asset_id=asset_id
        )
        sign_contract_for_test(db, project_id=project.id, gateway=gateway)

    with pytest.raises(AppError) as skip:
        svc.advance_project_stage(db, project_id=project.id, to_status="PRODUCING", actor_id=STAFF_ID)
    assert skip.value.code == "STATE_CONFLICT"
    db.rollback()

    for status in ("MEASUREMENT_PENDING", "DESIGN_CONFIRMING", "MATERIALS_READY", "PRODUCING",
                   "CUSTOMER_REVIEW", "READY_TO_SHIP"):
        svc.advance_project_stage(db, project_id=project.id, to_status=status, actor_id=STAFF_ID)
    db.commit()
    assert refresh(db).get(CommissionProject, project.id).status == "READY_TO_SHIP"

    # 尾款未结清不能发货（支付端口不可用 → 保守拒绝）
    with pytest.raises(AppError) as unpaid:
        svc.advance_project_stage(db, project_id=project.id, to_status="SHIPPED", actor_id=STAFF_ID)
    assert unpaid.value.code == "STATE_CONFLICT"
    assert "尾款" in str(unpaid.value)
    db.rollback()

    # 尾款结清后允许发货
    commerce.mark_settled(order.order_id)
    shipped = svc.advance_project_stage(
        db, project_id=project.id, to_status="SHIPPED", actor_id=STAFF_ID
    )
    db.commit()
    assert shipped.status == "SHIPPED"
    assert shipped.shipped_at is not None
    completed = svc.advance_project_stage(
        db, project_id=project.id, to_status="COMPLETED", actor_id=STAFF_ID
    )
    db.commit()
    assert completed.status == "COMPLETED"
    db.rollback()


def test_stage_delay_records_reason_and_notification(db: Any, commerce: CommerceStub) -> None:
    user_id = make_user(db)
    series_id = make_series(db)
    asset_id = make_asset(db, user_id)
    db.commit()
    _order, project = accepted_project(
        db, commerce=commerce, user_id=user_id, series_id=series_id, asset_id=asset_id
    )
    stage = svc.upsert_production_stage(
        db,
        project_id=project.id,
        actor_id=STAFF_ID,
        stage_code="PRODUCING",
        description="主体制作",
        needs_customer_confirmation=False,
    )
    db.commit()

    with pytest.raises(AppError) as no_reason:
        svc.delay_stage(
            db, stage_id=stage.id, actor_id=STAFF_ID, estimated_at=add_days_ms(20), reason=" "
        )
    assert no_reason.value.code == "VALIDATION_ERROR"
    db.rollback()

    delayed = svc.delay_stage(
        db,
        stage_id=stage.id,
        actor_id=STAFF_ID,
        estimated_at=add_days_ms(20),
        reason="材料到货延迟",
    )
    db.commit()
    assert delayed.delay_reason == "材料到货延迟"
    assert delayed.status == "DELAYED"
    assert refresh(db).get(CommissionProject, project.id).estimated_delivery == delayed.estimated_at
    events = list(
        refresh(db).scalars(
            sa.select(OutboxEvent).where(OutboxEvent.event_type == "notification.dispatch")
        )
    )
    assert any(event.payload.get("source_event") == "production.stage_delayed" for event in events)
    db.rollback()


# ------------------------------------------------------------------ 量体
def test_measurement_is_encrypted_versioned_and_owned(db: Any, commerce: CommerceStub) -> None:
    user_id = make_user(db)
    stranger_id = make_user(db, "陌生人")
    series_id = make_series(db)
    asset_id = make_asset(db, user_id)
    db.commit()
    _order, project = accepted_project(
        db, commerce=commerce, user_id=user_id, series_id=series_id, asset_id=asset_id
    )

    first = svc.create_measurement(
        db,
        user_id=user_id,
        project_id=project.id,
        payload=MeasurementInput(payload={"height_cm": 175.5, "chest_cm": 98.0}),
    )
    db.commit()
    assert first.version == 1
    assert "175.5" not in first.encrypted_payload and "height" not in first.encrypted_payload
    assert svc.load_measurements(first)["height_cm"] == 175.5

    second = svc.create_measurement(
        db,
        user_id=user_id,
        project_id=project.id,
        payload=MeasurementInput(payload={"height_cm": 176.0, "head_cm": 40.0}),
    )
    db.commit()
    assert second.version == 2
    assert second.flagged_fields == ["head_cm"], "越界只做异常提示，不拒绝特殊体型"

    with pytest.raises(AppError) as negative:
        svc.create_measurement(
            db,
            user_id=user_id,
            project_id=project.id,
            payload=MeasurementInput(payload={"height_cm": -1.0}),
        )
    assert negative.value.code == "VALIDATION_ERROR"
    db.rollback()

    with pytest.raises(AppError) as not_found:
        svc.create_measurement(
            db,
            user_id=stranger_id,
            project_id=project.id,
            payload=MeasurementInput(payload={"height_cm": 170.0}),
        )
    assert not_found.value.code == "RESOURCE_NOT_FOUND"
    db.rollback()

    stored = list(
        refresh(db).scalars(sa.select(Measurement).where(Measurement.project_id == project.id))
    )
    assert len(stored) == 2
    assert all(row.consent_id for row in stored), "量体数据留证同意记录"
    consents = refresh(db).execute(
        sa.text("SELECT COUNT(*) FROM consents WHERE purpose = 'measurement_data'")
    ).scalar()
    assert consents == 2
    db.rollback()


def test_project_access_is_owner_or_assigned_maker(db: Any, commerce: CommerceStub) -> None:
    user_id = make_user(db)
    stranger_id = make_user(db, "陌生人")
    series_id = make_series(db)
    asset_id = make_asset(db, user_id)
    db.commit()
    _order, project = accepted_project(
        db, commerce=commerce, user_id=user_id, series_id=series_id, asset_id=asset_id
    )

    assert svc.project_for_actor(
        db, project_id=project.id, user_id=user_id, maker_id=None
    ).id == project.id
    svc.assign_maker(db, project_id=project.id, maker_id=STAFF_ID, actor_id=STAFF_ID)
    db.commit()
    assert svc.project_for_actor(
        db, project_id=project.id, user_id=None, maker_id=STAFF_ID
    ).id == project.id
    for kwargs in (
        {"user_id": stranger_id, "maker_id": None},
        {"user_id": None, "maker_id": "stf_other_maker"},
        {"user_id": None, "maker_id": None},
    ):
        with pytest.raises(AppError) as not_found:
            svc.project_for_actor(db, project_id=project.id, **kwargs)
        assert not_found.value.code == "RESOURCE_NOT_FOUND"
    db.rollback()


# ------------------------------------------------------------------ 审计与留痕
def test_audit_and_outbox_are_written_in_same_transaction(db: Any, commerce: CommerceStub) -> None:
    user_id = make_user(db)
    series_id = make_series(db)
    asset_id = make_asset(db, user_id)
    db.commit()
    inquiry = submitted_inquiry(db, user_id=user_id, series_id=series_id, asset_id=asset_id)
    publish_quote(db, inquiry=inquiry)
    accepted_project(db, commerce=commerce, user_id=user_id, series_id=series_id, asset_id=asset_id)

    actions = {
        row.action
        for row in refresh(db).scalars(sa.select(AuditLog).where(AuditLog.actor_id == user_id))
    }
    assert {"inquiry.create", "inquiry.submit", "quote.accept"} <= actions
    event_types = {row.event_type for row in refresh(db).scalars(sa.select(OutboxEvent))}
    assert {"quote.published", "quote.accepted"} <= event_types
    # 通知在同一事务内登记（客户可见的报价发布与制作节点通知）
    assert refresh(db).scalar(sa.select(sa.func.count()).select_from(Notification)) >= 1
    deliveries = list(refresh(db).scalars(sa.select(Delivery)))
    assert len(deliveries) == 2
    assert all(row.status == "FAILED" and row.last_error == "RECIPIENT_UNVERIFIED" for row in deliveries)
    db.rollback()


def test_prepayment_and_balance_amounts_come_from_policy(db: Any, commerce: CommerceStub) -> None:
    """预付款比例与尾款期限来自 commission_policy，不写死。"""
    from app.modules.content import repository as content_repo
    from app.modules.content.service import ensure_default_settings, update_setting

    _ = commerce
    ensure_default_settings(db)
    db.commit()
    row = content_repo.get_setting(db, "commission.policy")
    update_setting(
        db,
        key="commission.policy",
        value={"prepayment_percent": 30, "balance_due_days": 3, "terms_version": "commission-2"},
        version=row.version,
        actor_id=STAFF_ID,
        reason="单元测试调整规则",
    )
    db.commit()

    user_id = make_user(db)
    series_id = make_series(db)
    asset_id = make_asset(db, user_id)
    db.commit()
    inquiry = submitted_inquiry(db, user_id=user_id, series_id=series_id, asset_id=asset_id)
    quote = publish_quote(db, inquiry=inquiry, total_cents=1000000, terms_version="commission-2")
    assert quote.prepayment_cents == 300000, "预付款比例按配置 30% 计算"
    assert quote.balance_cents == 700000
    _order, project = svc.accept_quote_for_user(
        db,
        user_id=user_id,
        quote_id=quote.id,
        quote_version=quote.quote_version,
        terms_version=quote.terms_version,
        accepted=True,
    )
    db.commit()
    assert project.balance_due_at is not None
    delta_days = round((project.balance_due_at - now_ms()) / 86_400_000)
    assert delta_days == 3, "尾款期限按配置 3 天计算"
    db.rollback()


def test_slot_hold_minutes_comes_from_policy(db: Any, commerce: CommerceStub) -> None:
    user_id = make_user(db)
    series_id = make_series(db)
    asset_id = make_asset(db, user_id)
    db.commit()
    round_row = make_round(db, capacity=1)
    inquiry = submitted_inquiry(db, user_id=user_id, series_id=series_id, asset_id=asset_id)
    select_application(db, round_row=round_row, user_id=user_id, inquiry=inquiry)
    quote = publish_quote(db, inquiry=inquiry)

    before = now_ms()
    order, _project = svc.accept_quote_for_user(
        db,
        user_id=user_id,
        quote_id=quote.id,
        quote_version=quote.quote_version,
        terms_version=quote.terms_version,
        accepted=True,
    )
    db.commit()
    reservation = refresh(db).scalar(
        sa.select(SlotReservation).where(SlotReservation.order_id == order.order_id)
    )
    expected = add_minutes_ms(30, before)
    assert abs(reservation.expires_at - expected) < 60_000, "预留时长取 slot_hold_minutes=30"
    assert reservation.hold_minutes == 30
    db.rollback()


def test_duplicate_application_insert_returns_existing_row(db: Any) -> None:
    """唯一约束 (round_id,user_id,inquiry_id) 是重复申请的最终防线。"""
    user_id = make_user(db)
    series_id = make_series(db)
    asset_id = make_asset(db, user_id)
    db.commit()
    round_row = make_round(db, capacity=5)
    inquiry = submitted_inquiry(db, user_id=user_id, series_id=series_id, asset_id=asset_id)

    first = svc.apply_to_round(
        db,
        user_id=user_id,
        round_id=round_row.id,
        payload=ScheduleApplicationInput(inquiry_id=inquiry.id),
        idempotency_key="dup-key",
    )
    db.commit()
    second = svc._insert_application(
        db,
        round_row=round_row,
        user_id=user_id,
        inquiry=inquiry,
        note=None,
        idempotency_key="dup-key",
    )
    db.commit()
    assert second.id == first.id
    assert refresh(db).scalar(
        sa.select(sa.func.count())
        .select_from(ScheduleApplication)
        .where(ScheduleApplication.round_id == round_row.id)
    ) == 1
    db.rollback()
