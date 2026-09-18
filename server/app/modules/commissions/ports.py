"""委托模块对外能力，以及本模块依赖其他模块的唯一入口。

其他模块只能使用本文件暴露的函数与 DTO；本模块对其他模块的调用也集中在
这里，便于替换与单测隔离（04 文档：模块之间只通过 ``ports.py`` 与
``contracts.py`` 的 DTO 协作）。

对外能力：
- ``accept_quote``：``app.modules.contracts.CommissionsPort`` 实现；
- ``project_for_order`` / ``commit_slot_on_prepayment`` / ``release_slot_on_order_closed``：
  供支付、订单与工作流在名额预留生命周期中调用。

对外部依赖（商务、身份、内容、通知）一律延迟导入：模块尚未就绪时按
``PROVIDER_UNAVAILABLE`` 或保守默认值处理，绝不静默跳过校验。
"""

from __future__ import annotations

import inspect
from typing import Any

from sqlalchemy.orm import Session

from app.modules.contracts import CommissionOrderRequest, CreatedOrder
from app.platform.errors import AppError
from app.platform.ids import now_ms


# ------------------------------------------------------------------ 内容与规则
def commission_policy(session: Session) -> dict[str, Any]:
    """委托规则默认值（预付款比例、报价有效期、名额预留时长、尾款期限、免费修改次数、条款版本）。

    唯一来源是内容模块的站点设置与配置基线，本模块不写死任何默认值。
    """
    from app.modules.content.service import commission_policy as content_commission_policy

    return content_commission_policy(session)


def series_is_published(session: Session, *, series_id: str) -> bool:
    """``series_id`` 必须指向已发布且在有效期内的 ``series`` 内容条目。"""
    from app.modules.content import repository as content_repo

    entry = content_repo.get_entry(session, series_id)
    if entry is None or entry.kind != "series" or entry.status != "published":
        return False
    moment = now_ms()
    if entry.publish_at is not None and entry.publish_at > moment:
        return False
    return not (entry.unpublish_at is not None and entry.unpublish_at <= moment)


def design_assets_owned_ready(session: Session, *, asset_ids: list[str], owner_id: str) -> None:
    """询价设定图校验：附件 ready、本人所有且 purpose=design（10 文档）。"""
    from app.modules.media.service import assert_owned_ready_design

    assert_owned_ready_design(session, asset_ids=asset_ids, owner_id=owner_id)


# ------------------------------------------------------------------ 身份与同意
def record_terms_consent(
    session: Session,
    *,
    user_id: str,
    version: str,
    quote_id: str,
    content_hash: str,
    evidence: dict[str, Any] | None = None,
    purpose: str = "commission_rights",
    resource_type: str = "quote",
) -> str:
    """合同留证：记录条款版本、正文摘要哈希、关联对象与同意时间。返回留证 ID。"""
    from app.modules.identity.service import record_consent

    entry = record_consent(
        session,
        user_id=user_id,
        purpose=purpose,
        version=version,
        resource_type=resource_type,
        resource_id=quote_id,
        content_hash=content_hash,
        evidence=evidence,
    )
    return entry.id


def require_rush_enabled(session: Session) -> None:
    """C11 限时申请默认关闭：未启用时入口隐藏且后端拒绝。"""
    from app.modules.content.service import require_feature

    require_feature(session, "rush_rounds")


def has_guardian_consent(session: Session, *, user_id: str) -> bool:
    """C19：未成年付费委托需要有效的监护人同意。"""
    from app.modules.identity.service import has_consent

    return has_consent(session, user_id=user_id, purpose="miner_guardian")


# ------------------------------------------------------------------ 通知
def notify_customer(
    session: Session,
    *,
    user_id: str,
    event_type: str,
    title: str,
    body: str,
    link_path: str | None = None,
    payload: dict[str, Any] | None = None,
    category: str = "transaction",
    channels: tuple[str, ...] = ("email",),
    event_id: str | None = None,
) -> None:
    """登记客户通知并投递意图；真实发送由 worker 消费 outbox 完成（FR-017）。"""
    from app.modules.notifications.ports import NotificationRequest
    from app.modules.notifications.service import enqueue_notification
    from app.platform.reliability import enqueue

    notification_event_id = event_id or f"{event_type}:{user_id}:{now_ms()}"
    enqueue_notification(
        session,
        NotificationRequest(
            event_id=notification_event_id,
            event_type=event_type,
            user_id=user_id,
            title=title,
            body=body,
            link_path=link_path,
            category=category,
            channels=channels,
            payload=payload or {},
        ),
    )
    enqueue(
        session,
        event_type="notification.dispatch",
        aggregate_id=notification_event_id,
        payload={
            # ``source_event`` 标明触发通知的业务事件，调用方的 ``reason`` 不被覆盖。
            "source_event": event_type,
            "reason": event_type,
            **(payload or {}),
        },
    )


# ------------------------------------------------------------------ 商务模块端口
def create_commission_order(
    session: Session,
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
    """调用商务模块建立委托订单（订单、行、应付项与库存/名额同事务）。

    该函数由商务模块实现；尚未就绪时抛 ``PROVIDER_UNAVAILABLE``，整笔事务回滚，
    不会出现“占了名额却没有订单”的中间状态。
    """
    try:
        from app.modules.commerce.ports import create_commission_order as commerce_create_order
    except ImportError as exc:
        raise AppError("PROVIDER_UNAVAILABLE", "商务模块尚未就绪") from exc

    return commerce_create_order(
        session,
        user_id=user_id,
        quote_id=quote_id,
        quote_version=quote_version,
        total_cents=total_cents,
        prepayment_cents=prepayment_cents,
        currency=currency,
        terms_version=terms_version,
        inquiry_id=inquiry_id,
        slot_reservation_id=slot_reservation_id,
        idempotency_key=idempotency_key,
    )


def create_change_installment(
    session: Session,
    *,
    order_id: str,
    project_id: str,
    change_request_id: str,
    amount_cents: int,
    currency: str,
    terms_version: str,
    label: str,
    idempotency_key: str,
) -> str | None:
    """客户接受变更单后由商务模块生成单独补款 installment，返回 ``installment_id``。

    端口不存在或缺少必需参数时返回 ``None``：调用方只落 ``change_requests`` 并登记
    ``order.change_accepted`` outbox 事件，由商务模块消费后补建应付项。
    """
    try:
        from app.modules.commerce import ports as commerce_ports
    except ImportError:
        return None
    handler = getattr(commerce_ports, "create_change_installment", None)
    if handler is None:
        return None
    candidates: dict[str, Any] = {
        "order_id": order_id,
        "project_id": project_id,
        "change_request_id": change_request_id,
        "amount_cents": amount_cents,
        "currency": currency,
        "terms_version": terms_version,
        "label": label,
        "idempotency_key": idempotency_key,
    }
    # 端口签名由商务模块维护：只传它声明的参数，缺少必需字段时按“未就绪”处理。
    params = set(inspect.signature(handler).parameters)
    if not {"order_id", "amount_cents", "change_request_id"} <= params:
        return None
    kwargs = {name: value for name, value in candidates.items() if name in params}
    outcome = handler(session, **kwargs)
    if isinstance(outcome, str):
        return outcome
    return getattr(outcome, "installment_id", None)


def order_balance_settled(session: Session, *, order_id: str, total_cents: int) -> bool:
    """尾款是否结清。

    优先使用商务端口的明确结论；其次用支付端口的实收汇总（实收 - 已退分摊 ≥ 订单总额）。
    两个端口都不可用时保守返回 ``False``：宁可不发货，也不能跳过尾款校验。
    """
    try:
        from app.modules.commerce import ports as commerce_ports
    except ImportError:
        commerce_ports = None  # type: ignore[assignment]
    if commerce_ports is not None:
        handler = getattr(commerce_ports, "order_balance_settled", None)
        if handler is not None:
            return bool(handler(session, order_id=order_id))

    try:
        from app.modules.payments import ports as payments_ports
    except ImportError:
        return False
    summary_handler = getattr(payments_ports, "order_paid_summary", None)
    if summary_handler is None:
        return False
    summary = summary_handler(session, order_id=order_id) or {}
    paid = int(summary.get("paid_cents", 0))
    refunded = int(summary.get("refunded_applied_cents", 0))
    return max(0, paid - refunded) >= int(total_cents)


# ------------------------------------------------------------------ CommissionsPort
def accept_quote(session: Session, request: CommissionOrderRequest) -> CreatedOrder:
    """``app.modules.contracts.CommissionsPort`` 实现：接受报价并建立唯一委托订单。"""
    from app.modules.commissions import service as commission_service

    order, _project = commission_service.accept_quote_for_user(
        session,
        user_id=request.user_id,
        quote_id=request.quote_id,
        inquiry_id=request.inquiry_id,
        quote_version=request.quote_version,
        terms_version=request.terms_version,
        idempotency_key=request.idempotency_key,
    )
    return order


# ------------------------------------------------------------------ 名额生命周期（供支付与订单模块调用）
def project_for_order(session: Session, *, order_id: str) -> dict[str, Any] | None:
    """制作单摘要：供订单页展示制作进度，不暴露量体等私密内容。"""
    from app.modules.commissions import service as commission_service

    return commission_service.project_summary_for_order(session, order_id=order_id)


def commit_slot_on_prepayment(session: Session, *, order_id: str) -> bool:
    """预付款到账后把名额预留转为正式占用（``committed_count``）。

    返回是否发生了状态变更；未预留名额的订单返回 ``False``。
    """
    from app.modules.commissions import service as commission_service

    return commission_service.commit_slot_on_prepayment(session, order_id=order_id)


def release_slot_on_order_closed(session: Session, *, order_id: str, reason: str) -> bool:
    """订单关闭核对完成、确认无在途付款后释放名额。"""
    from app.modules.commissions import service as commission_service

    return commission_service.release_slot_for_order(session, order_id=order_id, reason=reason)


def count_quotes_awaiting_decision(
    session: Session, *, user_id: str, moment: int | None = None
) -> int:
    """本人待决定的报价数量（账户待办提示）。计数属委托模块自己的数据。"""
    from app.modules.commissions import repository as repo

    return repo.count_quotes_awaiting_decision(session, user_id=user_id, moment=moment)


def public_schedule_summary(session: Session, *, moment: int | None = None) -> dict[str, Any] | None:
    """公开档期摘要（匿名余量，不含任何客户身份）。"""
    from app.modules.commissions import service as commission_service

    return commission_service.public_schedule_summary(session, moment=moment)


def count_stages_awaiting_confirmation(session: Session, *, user_id: str) -> int:
    """本人待确认的制作节点数量（账户待办提示）。"""
    from app.modules.commissions import repository as repo

    return repo.count_stages_awaiting_confirmation(session, user_id=user_id)


def count_inquiries_needs_info(session: Session, *, user_id: str) -> int:
    """本人待补件的询价数量（07 文档 NEEDS_INFO）。"""
    from app.modules.commissions import repository as repo

    return repo.count_inquiries_needs_info(session, user_id=user_id)


def count_open_inquiries_for_user(session: Session, *, user_id: str) -> int:
    """本人仍在处理中的询价数量（注销阻碍提示，10 文档）。"""
    from app.modules.commissions import repository as repo

    return repo.count_open_inquiries_for_user(session, user_id=user_id)


__all__ = [
    "accept_quote",
    "assert_contract_signed",
    "commission_policy",
    "commit_slot_on_prepayment",
    "contract_status_for_project",
    "count_inquiries_needs_info",
    "count_open_inquiries_for_user",
    "count_quotes_awaiting_decision",
    "count_stages_awaiting_confirmation",
    "create_change_installment",
    "create_commission_order",
    "customer_retained_counts",
    "design_assets_owned_ready",
    "has_guardian_consent",
    "notify_customer",
    "order_balance_settled",
    "project_for_order",
    "public_schedule_summary",
    "purge_draft_inquiries",
    "purge_stale_drafts",
    "record_terms_consent",
    "remind_overdue_inquiries",
    "release_slot_on_order_closed",
    "require_rush_enabled",
    "series_is_published",
]


def can_access_asset(session: Session, *, asset_id: str, principal_id: str, is_staff: bool) -> bool:
    """从制作单及其询价/节点/变更引用判断附件归属。"""
    import sqlalchemy as sa

    from app.modules.commissions.models import (
        ChangeRequest,
        CommissionProject,
        Inquiry,
        ProductionStage,
    )

    predicate = (
        CommissionProject.maker_id == principal_id
        if is_staff
        else CommissionProject.user_id == principal_id
    )
    projects = session.scalars(sa.select(CommissionProject).where(predicate))
    for project in projects:
        inquiry = session.get(Inquiry, project.inquiry_id)
        if inquiry and asset_id in (inquiry.design_asset_ids or []):
            return True
        for row in session.scalars(
            sa.select(ProductionStage).where(ProductionStage.project_id == project.id)
        ):
            if asset_id in (row.media_asset_ids or []):
                return True
        for change in session.scalars(
            sa.select(ChangeRequest).where(ChangeRequest.project_id == project.id)
        ):
            if asset_id in (change.media_asset_ids or []):
                return True
    return False


def purge_draft_inquiries(session: Session, *, user_id: str) -> int:
    """注销清理：删除**未提交**的询价草稿（可删除资料）。已提交的询价按保留期处理。"""
    import sqlalchemy as sa

    from app.modules.commissions.models import Inquiry, InquiryRevision

    drafts = list(
        session.scalars(
            sa.select(Inquiry).where(
                Inquiry.user_id == user_id,
                Inquiry.status.in_(("DRAFT", "WITHDRAWN")),
            )
        )
    )
    for inquiry in drafts:
        for revision in session.scalars(
            sa.select(InquiryRevision).where(InquiryRevision.inquiry_id == inquiry.id)
        ):
            session.delete(revision)
        session.delete(inquiry)
    session.flush()
    return len(drafts)


def remind_overdue_inquiries(session: Session, *, limit: int = 50) -> dict[str, Any]:
    """C30 首响逾期提醒（worker 周期任务调用）：同一询价同一天只提醒一次。"""
    from app.modules.commissions import service as commission_service

    return commission_service.remind_overdue_inquiries(session, limit=limit)


def assert_contract_signed(session: Session, *, project_id: str) -> None:
    """C07/C19 合同门禁：制作开工前必须有已签署的合同（跨模块调用只经端口）。"""
    from app.modules.commissions import esign_service

    esign_service.assert_contract_signed(session, project_id=project_id)


def contract_status_for_project(session: Session, *, project_id: str) -> dict[str, Any]:
    """制作单的合同签署状态（运营与客户看到同一口径）。"""
    from app.modules.commissions import esign_service

    return esign_service.contract_status_for_project(session, project_id=project_id)


def purge_stale_drafts(session: Session, *, before_ms: int, limit: int = 50) -> int:
    """周期清理：删除超过保留窗口仍未提交的询价草稿（10 文档「草稿 30 天」）。"""
    import sqlalchemy as sa

    from app.modules.commissions.models import Inquiry, InquiryRevision

    drafts = list(
        session.scalars(
            sa.select(Inquiry)
            .where(
                Inquiry.status.in_(("DRAFT", "WITHDRAWN")),
                Inquiry.updated_at < before_ms,
            )
            .order_by(Inquiry.updated_at)
            .limit(max(1, min(limit, 200)))
        )
    )
    for inquiry in drafts:
        for revision in session.scalars(
            sa.select(InquiryRevision).where(InquiryRevision.inquiry_id == inquiry.id)
        ):
            session.delete(revision)
        session.delete(inquiry)
    session.flush()
    return len(drafts)


def customer_retained_counts(session: Session, *, user_id: str) -> dict[str, int]:
    """注销清理时依法隔离保留的委托数据计数（询价、报价与制作单不做删除）。"""
    import sqlalchemy as sa

    from app.modules.commissions.models import CommissionProject, Inquiry, Quote

    inquiry_ids = list(session.scalars(sa.select(Inquiry.id).where(Inquiry.user_id == user_id)))
    quotes = 0
    if inquiry_ids:
        quotes = int(
            session.scalar(
                sa.select(sa.func.count()).select_from(Quote).where(Quote.inquiry_id.in_(inquiry_ids))
            )
            or 0
        )
    projects = int(
        session.scalar(
            sa.select(sa.func.count())
            .select_from(CommissionProject)
            .where(CommissionProject.user_id == user_id)
        )
        or 0
    )
    return {"inquiries": len(inquiry_ids), "quotes": quotes, "projects": projects}
