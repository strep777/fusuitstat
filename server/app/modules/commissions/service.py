"""委托模块用例：询价状态机、报价版本、名额占用、制作单、量体、阶段与变更单。

不变量（05/07 文档）：
1. 询价状态机 ``DRAFT → SUBMITTED → NEEDS_INFO → SUBMITTED → QUOTED → ACCEPTED``，
   另有 ``QUOTED → EXPIRED``、``QUOTED → SUBMITTED``（要求重新评估）、
   ``DRAFT/SUBMITTED/NEEDS_INFO/QUOTED → WITHDRAWN``、``SUBMITTED → REJECTED``；
   已接受询价不能撤回，终态不删除、不可越权修改，重新申请建立新记录。
2. 发布新报价把旧版置为 ``SUPERSEDED``；接受报价必须在同一事务校验当前版本、有效期、
   用户归属、名额资格与条款版本，否则返回 ``QUOTE_CHANGED`` / ``QUOTE_EXPIRED``；
   同一报价只生成一个订单。
3. 名额占用用条件 UPDATE（``reserved_count + committed_count < capacity``）实现，
   不使用 ``SELECT FOR UPDATE``；已付预付款才转 ``committed_count``。
4. 制作单按 07 文档基线顺序推进，禁止跳过尾款校验发货。
5. 所有可配置默认值来自 ``content.service.commission_policy``，本模块不写死。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.catalog import ports as catalog_ports
from app.modules.commissions import ports as commission_ports
from app.modules.commissions import repository as repo
from app.modules.commissions.models import (
    MAX_APPLICATIONS_PER_ROUND,
    MAX_MEASUREMENT_FIELDS,
    MEASUREMENT_SOFT_MAX_CM,
    MEASUREMENT_SOFT_MIN_CM,
    PROJECT_FLOW,
    TERMINAL_INQUIRY_STATUSES,
    ChangeRequest,
    CommissionProject,
    Inquiry,
    InquiryRevision,
    Measurement,
    ProductionStage,
    Quote,
    QuoteItem,
    ScheduleApplication,
    ScheduleRound,
    SlotReservation,
)
from app.modules.commissions.schemas import (
    ChangeRequestOut,
    CommissionProjectOut,
    InquiryCreate,
    InquiryDetail,
    InquiryPatch,
    InquiryRevisionItem,
    InquirySummary,
    InquirySupplementInput,
    MeasurementInput,
    MeasurementOut,
    ProductionStageOut,
    QuoteAcceptResponse,
    QuoteLineItemOut,
    QuoteOut,
    QuotePublishInput,
    ReassessmentInput,
    ScheduleApplicationInput,
    ScheduleApplicationOut,
    ScheduleRoundInput,
    ScheduleRoundPublic,
    StageChangeInput,
    StageConfirmInput,
)
from app.modules.contracts import CreatedOrder, OrderTotals
from app.platform import audit
from app.platform.db import affected_rows
from app.platform.errors import AppError
from app.platform.ids import add_days_ms, add_minutes_ms, iso, new_id, now_ms, to_ms
from app.platform.reliability import IdempotencyKey, digest, enqueue
from app.platform.security import decrypt_pii, encrypt_pii

IDEMPOTENCY_TTL_DAYS = 7
STAGE_TITLES = {
    "QUEUED": "已接单",
    "MEASUREMENT_PENDING": "等待量体",
    "DESIGN_CONFIRMING": "设定确认",
    "MATERIALS_READY": "材料就绪",
    "PRODUCING": "制作中",
    "CUSTOMER_REVIEW": "客户验收预览",
    "READY_TO_SHIP": "待发货",
    "SHIPPED": "已发货",
    "COMPLETED": "已完成",
}


# ------------------------------------------------------------------ 通用工具
def paginate(items: list[Any], *, limit: int, offset: int) -> tuple[list[Any], str | None, bool]:
    """列表统一形状：多取一条判断 ``has_more``，游标为下一页偏移。"""
    has_more = len(items) > limit
    page = items[:limit]
    next_cursor = str(offset + limit) if has_more else None
    return page, next_cursor, has_more


def parse_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        return max(0, int(cursor))
    except ValueError as exc:
        raise AppError("VALIDATION_ERROR", "分页游标无效，请重新打开列表。") from exc


def _snapshot_digest(**parts: Any) -> str:
    """条款与报价快照摘要：正文由内容模块维护，这里对同意时的商业条款做留证摘要。"""
    return digest(parts)


def _existing_idempotent(
    session: Session, *, actor_id: str, operation: str, key: str | None, request_hash: str
) -> IdempotencyKey | None:
    if not key:
        return None
    row = session.scalars(
        sa.select(IdempotencyKey).where(
            IdempotencyKey.actor_id == actor_id,
            IdempotencyKey.operation == operation,
            IdempotencyKey.key == key,
        )
    ).one_or_none()
    if row is None:
        return None
    if row.request_hash != request_hash:
        # 同键不同请求内容：拒绝，避免把 A 的请求结果返回给 B。
        raise AppError("IDEMPOTENCY_CONFLICT")
    return row


def _remember_idempotent(
    session: Session,
    *,
    actor_id: str,
    operation: str,
    key: str | None,
    request_hash: str,
    resource_id: str,
    response: dict[str, Any] | None = None,
) -> None:
    if not key:
        return
    record = IdempotencyKey(
        id=new_id("job"),
        actor_id=actor_id,
        operation=operation,
        key=key,
        request_hash=request_hash,
        resource_id=resource_id,
        status="COMPLETED",
        response_snapshot=response,
        expires_at=add_days_ms(IDEMPOTENCY_TTL_DAYS),
    )
    try:
        # 并发同键：另一个连接已完成同样请求，这里只保留先到者的记录。
        with session.begin_nested():
            session.add(record)
            session.flush()
    except IntegrityError:  # pragma: no cover - 依赖真实并发
        pass


def order_from_snapshot(snapshot: dict[str, Any]) -> CreatedOrder:
    """由接受报价时写入的订单与款项快照还原 ``CreatedOrder``（重复请求返回同一订单）。"""
    totals = OrderTotals(**snapshot["totals"])
    return CreatedOrder(
        order_id=snapshot["order_id"],
        order_no=snapshot.get("order_no", ""),
        status=snapshot.get("status", "PENDING_PAYMENT"),
        installment_id=snapshot.get("installment_id"),
        totals=totals,
        expires_at=snapshot.get("expires_at"),
    )


# ------------------------------------------------------------------ 询价表单校验
def _check_series(session: Session, *, series_id: str) -> None:
    if not commission_ports.series_is_published(session, series_id=series_id):
        raise AppError(
            "VALIDATION_ERROR",
            "请选择已开放的委托系列。",
            fields={"series_id": "系列不存在或未发布"},
        )


def _check_budget(min_cents: int | None, max_cents: int | None) -> None:
    if min_cents is not None and max_cents is not None and min_cents > max_cents:
        raise AppError(
            "VALIDATION_ERROR",
            "预算下限不能高于上限。",
            fields={"budget_min_cents": "不能高于预算上限"},
        )


def _check_rights(rights_declaration: bool) -> None:
    if not rights_declaration:
        raise AppError(
            "VALIDATION_ERROR",
            "请确认你拥有所提交角色设定的使用权。",
            fields={"rights_declaration": "必选"},
        )


def _check_design_assets(session: Session, *, user_id: str, asset_ids: list[str]) -> None:
    """设定图 1–3 张、本人所有、purpose=design 且扫描完成。"""
    if not 1 <= len(asset_ids) <= 3:
        raise AppError(
            "VALIDATION_ERROR",
            "请上传 1–3 张清晰设定图。",
            fields={"design_asset_ids": "数量必须为 1–3 张"},
        )
    commission_ports.design_assets_owned_ready(session, asset_ids=list(asset_ids), owner_id=user_id)


def _require_submittable(inquiry: Inquiry) -> None:
    missing: list[str] = []
    if not inquiry.character_name:
        missing.append("character_name")
    if not inquiry.species:
        missing.append("species")
    if not inquiry.series_id:
        missing.append("series_id")
    if missing:
        raise AppError(
            "VALIDATION_ERROR",
            "请先补全角色名称、物种与委托系列。",
            fields={name: "必填" for name in missing},
        )


def _revision_payload(inquiry: Inquiry) -> dict[str, Any]:
    """版本快照：只保存业务事实，不含联系方式等隐私字段之外的内容。"""
    return {
        "character_name": inquiry.character_name,
        "species": inquiry.species,
        "series_id": inquiry.series_id,
        "suit_type": inquiry.suit_type,
        "design_asset_ids": list(inquiry.design_asset_ids or []),
        "requirements": inquiry.requirements,
        "budget_min_cents": inquiry.budget_min_cents,
        "budget_max_cents": inquiry.budget_max_cents,
        "target_date": inquiry.target_date,
        "delivery_region": inquiry.delivery_region,
        "contact_preference": inquiry.contact_preference,
        "rights_declaration": bool(inquiry.rights_declaration),
        "guardian_required": bool(inquiry.guardian_required),
    }


def _append_revision(
    session: Session,
    *,
    inquiry: Inquiry,
    author_id: str | None,
    author_type: str,
    change_note: str | None,
    submitted: bool,
) -> InquiryRevision:
    """追加询价版本：不覆盖工作人员已见版本。"""
    revision = repo.max_revision(session, inquiry_id=inquiry.id) + 1
    moment = now_ms()
    row = InquiryRevision(
        id=new_id("inquiry"),
        inquiry_id=inquiry.id,
        revision=revision,
        payload_snapshot=_revision_payload(inquiry),
        author_id=author_id,
        author_type=author_type,
        change_note=change_note,
        submitted_at=moment if submitted else None,
    )
    session.add(row)
    inquiry.current_revision = revision
    session.flush()
    return row


# ------------------------------------------------------------------ 询价用例
def _check_service_application(
    session: Session, *, user_id: str, application_id: str | None
) -> str | None:
    """C28「服务先申请再报价」：询价只能引用本人**已审批通过且未过期**的服务申请。

    非本人或不存在一律 404（不透露申请是否存在）；已用于其他询价、非服务类、
    未通过审批或已过购买窗口分别给出可读原因。
    """
    if not application_id:
        return None
    snapshot = catalog_ports.application_snapshot(
        session, user_id=user_id, application_id=application_id
    )
    if snapshot is None:
        raise AppError("RESOURCE_NOT_FOUND")
    if snapshot["converted_order_id"]:
        raise AppError("STATE_CONFLICT", "该申请已用于下单，请重新提交申请。")
    if snapshot["kind"] != "service":
        raise AppError(
            "VALIDATION_ERROR",
            "该申请不是服务类申请，不能用于发起询价。",
            fields={"application_id": "申请类型不匹配"},
        )
    if snapshot["status"] != "APPROVED":
        raise AppError("STATE_CONFLICT", "该服务申请尚未通过审批，暂时不能发起询价。")
    expires_at = snapshot["purchase_expires_at"]
    if expires_at and int(expires_at) <= now_ms():
        raise AppError("STATE_CONFLICT", "该申请的购买窗口已过期，请重新提交申请。")
    existing = session.scalars(
        sa.select(Inquiry.id).where(Inquiry.application_id == application_id)
    ).first()
    if existing is not None:
        raise AppError("STATE_CONFLICT", "该申请已用于另一份询价，请到「我的委托」继续处理。")
    return application_id


def create_inquiry(
    session: Session,
    *,
    user_id: str,
    payload: InquiryCreate,
    idempotency_key: str | None = None,
) -> Inquiry:
    """创建询价草稿（幂等键可选）。量体信息不在首次询价收集。"""
    request_hash = digest({"op": "inquiry.create", "user_id": user_id, "payload": payload.model_dump()})
    existing = _existing_idempotent(
        session, actor_id=user_id, operation="inquiry.create", key=idempotency_key, request_hash=request_hash
    )
    if existing is not None and existing.resource_id:
        inquiry = repo.get_inquiry(session, inquiry_id=existing.resource_id, user_id=user_id)
        if inquiry is not None:
            return inquiry

    _check_series(session, series_id=payload.series_id)
    _check_budget(payload.budget_min_cents, payload.budget_max_cents)
    _check_rights(payload.rights_declaration)
    if payload.design_asset_ids:
        _check_design_assets(session, user_id=user_id, asset_ids=list(payload.design_asset_ids))
    application_id = _check_service_application(
        session, user_id=user_id, application_id=payload.application_id
    )

    inquiry = Inquiry(
        id=new_id("inquiry"),
        user_id=user_id,
        status="DRAFT",
        series_id=payload.series_id,
        character_name=payload.character_name,
        species=payload.species,
        suit_type=payload.suit_type,
        requirements=payload.requirements,
        design_asset_ids=list(payload.design_asset_ids),
        budget_min_cents=payload.budget_min_cents,
        budget_max_cents=payload.budget_max_cents,
        target_date=payload.target_date,
        delivery_region=payload.delivery_region,
        contact_preference=payload.contact_preference,
        rights_declaration=1 if payload.rights_declaration else 0,
        guardian_required=1 if payload.guardian_required else 0,
        application_id=application_id,
    )
    session.add(inquiry)
    session.flush()
    _append_revision(
        session,
        inquiry=inquiry,
        author_id=user_id,
        author_type="customer",
        change_note="创建草稿",
        submitted=False,
    )
    audit.record(
        session,
        actor_type="customer",
        actor_id=user_id,
        action="inquiry.create",
        resource_type="inquiry",
        resource_id=inquiry.id,
        after={"status": inquiry.status, "revision": inquiry.current_revision},
    )
    _remember_idempotent(
        session,
        actor_id=user_id,
        operation="inquiry.create",
        key=idempotency_key,
        request_hash=request_hash,
        resource_id=inquiry.id,
    )
    return inquiry


def get_inquiry_detail(session: Session, *, user_id: str, inquiry_id: str) -> Inquiry:
    inquiry = repo.get_inquiry(session, inquiry_id=inquiry_id, user_id=user_id)
    if inquiry is None:
        # 不泄露对象是否存在（06 文档）。
        raise AppError("RESOURCE_NOT_FOUND")
    return inquiry


def update_inquiry(
    session: Session, *, user_id: str, inquiry_id: str, patch: InquiryPatch
) -> Inquiry:
    """允许版本的编辑：草稿原地修改，已提交询价追加新版本，终态拒绝。"""
    inquiry = get_inquiry_detail(session, user_id=user_id, inquiry_id=inquiry_id)
    if inquiry.status in TERMINAL_INQUIRY_STATUSES:
        raise AppError("STATE_CONFLICT", "该询价已结束，不能再次修改。")
    if inquiry.status == "EXPIRED":
        raise AppError("STATE_CONFLICT", "报价已过期，请先要求重新评估后再修改。")
    if patch.version != inquiry.version:
        raise AppError("VERSION_CONFLICT")

    if patch.series_id is not None and patch.series_id != inquiry.series_id:
        _check_series(session, series_id=patch.series_id)
    budget_min = patch.budget_min_cents if patch.budget_min_cents is not None else inquiry.budget_min_cents
    budget_max = patch.budget_max_cents if patch.budget_max_cents is not None else inquiry.budget_max_cents
    _check_budget(budget_min, budget_max)
    if patch.rights_declaration is not None:
        _check_rights(patch.rights_declaration)
    if patch.design_asset_ids is not None:
        _check_design_assets(session, user_id=user_id, asset_ids=list(patch.design_asset_ids))

    before = {"status": inquiry.status, "revision": inquiry.current_revision}
    if patch.character_name is not None:
        inquiry.character_name = patch.character_name
    if patch.species is not None:
        inquiry.species = patch.species
    if patch.series_id is not None:
        inquiry.series_id = patch.series_id
    if patch.suit_type is not None:
        inquiry.suit_type = patch.suit_type
    if patch.design_asset_ids is not None:
        inquiry.design_asset_ids = list(patch.design_asset_ids)
    if patch.requirements is not None:
        inquiry.requirements = patch.requirements
    if patch.budget_min_cents is not None:
        inquiry.budget_min_cents = patch.budget_min_cents
    if patch.budget_max_cents is not None:
        inquiry.budget_max_cents = patch.budget_max_cents
    if patch.target_date is not None:
        inquiry.target_date = patch.target_date
    if patch.delivery_region is not None:
        inquiry.delivery_region = patch.delivery_region
    if patch.contact_preference is not None:
        inquiry.contact_preference = patch.contact_preference
    if patch.rights_declaration is not None:
        inquiry.rights_declaration = 1 if patch.rights_declaration else 0
    if patch.guardian_required is not None:
        inquiry.guardian_required = 1 if patch.guardian_required else 0
    inquiry.version += 1
    inquiry.updated_at = now_ms()

    # 草稿只保留最新内容；已提交（工作人员可能已读）的一律追加版本。
    if inquiry.status != "DRAFT":
        _append_revision(
            session,
            inquiry=inquiry,
            author_id=user_id,
            author_type="customer",
            change_note="客户修改需求",
            submitted=inquiry.status == "SUBMITTED",
        )
    session.flush()
    audit.record(
        session,
        actor_type="customer",
        actor_id=user_id,
        action="inquiry.update",
        resource_type="inquiry",
        resource_id=inquiry.id,
        before=before,
        after={"status": inquiry.status, "revision": inquiry.current_revision},
    )
    return inquiry


def submit_inquiry(session: Session, *, user_id: str, inquiry_id: str) -> Inquiry:
    """提交询价：校验设定图与权利声明后进入 ``SUBMITTED``。

    ``SUBMITTED`` 重复提交返回同一对象（幂等），不产生新版本。
    """
    inquiry = get_inquiry_detail(session, user_id=user_id, inquiry_id=inquiry_id)
    if inquiry.status == "SUBMITTED":
        return inquiry
    if inquiry.status == "DRAFT" or inquiry.status == "NEEDS_INFO":
        pass
    else:
        raise AppError("STATE_CONFLICT", "当前状态不允许提交，请刷新后重试。")

    _require_submittable(inquiry)
    _check_rights(bool(inquiry.rights_declaration))
    _check_design_assets(session, user_id=user_id, asset_ids=list(inquiry.design_asset_ids or []))

    moment = now_ms()
    before = {"status": inquiry.status, "revision": inquiry.current_revision}
    inquiry.status = "SUBMITTED"
    inquiry.submitted_at = inquiry.submitted_at or moment
    inquiry.needs_info_note = None
    inquiry.version += 1
    inquiry.updated_at = moment
    _append_revision(
        session,
        inquiry=inquiry,
        author_id=user_id,
        author_type="customer",
        change_note="客户提交",
        submitted=True,
    )
    audit.record(
        session,
        actor_type="customer",
        actor_id=user_id,
        action="inquiry.submit",
        resource_type="inquiry",
        resource_id=inquiry.id,
        before=before,
        after={"status": inquiry.status, "revision": inquiry.current_revision},
    )
    return inquiry


def withdraw_inquiry(
    session: Session, *, user_id: str, inquiry_id: str, reason: str | None = None
) -> Inquiry:
    """撤回：``DRAFT/SUBMITTED/NEEDS_INFO/QUOTED → WITHDRAWN``。已接受询价不能撤回。"""
    inquiry = get_inquiry_detail(session, user_id=user_id, inquiry_id=inquiry_id)
    if inquiry.status == "WITHDRAWN":
        return inquiry
    if inquiry.status in TERMINAL_INQUIRY_STATUSES or inquiry.status == "EXPIRED":
        raise AppError("STATE_CONFLICT", "当前状态不允许撤回；已接受的询价请通过订单取消处理。")
    moment = now_ms()
    before = {"status": inquiry.status}
    inquiry.status = "WITHDRAWN"
    inquiry.withdrawn_at = moment
    inquiry.version += 1
    inquiry.updated_at = moment
    session.flush()
    audit.record(
        session,
        actor_type="customer",
        actor_id=user_id,
        action="inquiry.withdraw",
        resource_type="inquiry",
        resource_id=inquiry.id,
        before=before,
        after={"status": inquiry.status},
        reason=reason,
    )
    return inquiry


def add_supplement(
    session: Session,
    *,
    user_id: str | None,
    inquiry_id: str,
    payload: InquirySupplementInput,
    actor_id: str | None = None,
) -> Inquiry:
    """补件：产生新的 ``inquiry_revisions`` 记录，不覆盖工作人员已见版本。

    报价后补件会改变评估基础，因此 ``QUOTED`` 回到 ``SUBMITTED``（要求重新评估）；
    ``EXPIRED`` 需显式 ``request_reevaluation`` 才能重新进入评估。

    ``user_id`` 为空表示由员工代客户补件（后台入口）；此时通过员工侧查询读取询价，
    附件归属按询价所有者校验，不信任调用方传入的用户标识。
    """
    if user_id is None:
        inquiry = repo.get_inquiry_for_staff(session, inquiry_id=inquiry_id)
        owner_id = inquiry.user_id if inquiry is not None else ""
    else:
        inquiry = get_inquiry_detail(session, user_id=user_id, inquiry_id=inquiry_id)
        owner_id = user_id
    if inquiry is None:
        raise AppError("RESOURCE_NOT_FOUND")
    if inquiry.status in TERMINAL_INQUIRY_STATUSES:
        raise AppError("STATE_CONFLICT", "该询价已结束，请重新提交新的询价。")
    if payload.version != inquiry.version:
        raise AppError("VERSION_CONFLICT")
    if inquiry.status == "EXPIRED" and not payload.request_reevaluation:
        raise AppError("STATE_CONFLICT", "报价已过期，请要求重新评估或重新提交询价。")

    if payload.design_asset_ids is not None:
        _check_design_assets(session, user_id=owner_id, asset_ids=list(payload.design_asset_ids))
        inquiry.design_asset_ids = list(payload.design_asset_ids)
    if payload.requirements is not None:
        inquiry.requirements = payload.requirements

    moment = now_ms()
    before = {"status": inquiry.status, "revision": inquiry.current_revision}
    if inquiry.status in ("SUBMITTED", "NEEDS_INFO", "QUOTED", "EXPIRED"):
        inquiry.status = "SUBMITTED"
        inquiry.submitted_at = inquiry.submitted_at or moment
        inquiry.needs_info_note = None
    inquiry.version += 1
    inquiry.updated_at = moment
    _append_revision(
        session,
        inquiry=inquiry,
        author_id=user_id,
        author_type="customer",
        change_note=payload.note or "客户补件",
        submitted=True,
    )
    audit.record(
        session,
        actor_type="customer",
        actor_id=user_id,
        action="inquiry.supplement",
        resource_type="inquiry",
        resource_id=inquiry.id,
        before=before,
        after={"status": inquiry.status, "revision": inquiry.current_revision},
        reason=payload.note,
    )
    return inquiry


def mark_needs_info(
    session: Session, *, inquiry_id: str, actor_id: str | None, reason: str
) -> Inquiry:
    """工作室要求补件：``SUBMITTED → NEEDS_INFO``（07 文档状态机）。

    资料不足期间不会发布可支付报价；必须说明需要补什么，不能只给「失败」。
    条件 UPDATE + 版本校验保证两个后台标签页不会互相覆盖；补件要求本身也留一条
    staff 版本记录，客户补齐后回到 ``SUBMITTED``。
    """
    inquiry = repo.get_inquiry_for_staff(session, inquiry_id)
    if inquiry is None:
        raise AppError("RESOURCE_NOT_FOUND")
    if inquiry.status == "NEEDS_INFO":
        return inquiry
    if inquiry.status != "SUBMITTED":
        raise AppError("STATE_CONFLICT", "只有待处理询价可以要求补件。")
    clean_reason = (reason or "").strip()
    if len(clean_reason) < 2:
        raise AppError("VALIDATION_ERROR", "请说明需要补充的资料。", fields={"reason": "必填"})

    moment = now_ms()
    before = {"status": inquiry.status}
    result = session.execute(
        sa.update(Inquiry)
        .where(
            Inquiry.id == inquiry.id,
            Inquiry.version == inquiry.version,
            Inquiry.status == "SUBMITTED",
        )
        .values(
            status="NEEDS_INFO",
            needs_info_note=clean_reason[:500],
            staff_seen_revision=inquiry.current_revision,
            version=Inquiry.version + 1,
            updated_at=moment,
        )
    )
    if not affected_rows(result):
        raise AppError("STATE_CONFLICT", "询价状态已变化，请刷新后重试。")
    session.expire(inquiry)
    inquiry = repo.get_inquiry_for_staff(session, inquiry_id)
    assert inquiry is not None
    _append_revision(
        session,
        inquiry=inquiry,
        author_id=actor_id,
        author_type="staff",
        change_note=f"要求补件：{clean_reason}"[:300],
        submitted=False,
    )
    commission_ports.notify_customer(
        session,
        user_id=inquiry.user_id,
        event_type="quote.needs_info",
        title="委托询价需要补充资料",
        body="",
        payload={"inquiry_id": inquiry.id, "reason": clean_reason},
    )
    audit.record(
        session,
        actor_type="staff",
        actor_id=actor_id,
        action="inquiry.needs_info",
        resource_type="inquiry",
        resource_id=inquiry.id,
        before=before,
        after={"status": inquiry.status, "revision": inquiry.current_revision},
        reason=clean_reason,
    )
    session.flush()
    return inquiry


def reject_inquiry(
    session: Session, *, inquiry_id: str, actor_id: str | None, reason: str
) -> Inquiry:
    """不承接：``SUBMITTED → REJECTED``，必须说明可修改的原因。"""
    inquiry = repo.get_inquiry_for_staff(session, inquiry_id)
    if inquiry is None:
        raise AppError("RESOURCE_NOT_FOUND")
    if inquiry.status != "SUBMITTED":
        raise AppError("STATE_CONFLICT", "只有待处理询价可以拒单。")
    if not reason.strip():
        raise AppError("VALIDATION_ERROR", "请说明不承接的原因。", fields={"reason": "必填"})
    moment = now_ms()
    before = {"status": inquiry.status}
    inquiry.status = "REJECTED"
    inquiry.reject_reason = reason[:400]
    inquiry.rejected_at = moment
    inquiry.staff_seen_revision = inquiry.current_revision
    inquiry.version += 1
    inquiry.updated_at = moment
    audit.record(
        session,
        actor_type="staff",
        actor_id=actor_id,
        action="inquiry.reject",
        resource_type="inquiry",
        resource_id=inquiry.id,
        before=before,
        after={"status": inquiry.status},
        reason=reason,
    )
    session.flush()
    return inquiry


# ------------------------------------------------------------------ 报价用例
def assign_inquiry(
    session: Session,
    *,
    inquiry_id: str,
    assignee_id: str | None,
    actor_id: str | None,
    note: str | None = None,
) -> Inquiry:
    """把询价分派给某位员工（03 文档：/admin/quotes 支持分派）。

    - ``assignee_id`` 为空表示退回未分派，不报错；
    - 分派只改责任人，不改变询价状态、不代替客户决定；
    - 已拒单或已接受的询价不再改派，避免责任人与结果矛盾。
    """
    inquiry = repo.get_inquiry_for_staff(session, inquiry_id)
    if inquiry is None:
        raise AppError("RESOURCE_NOT_FOUND")
    if inquiry.status in ("REJECTED", "ACCEPTED"):
        raise AppError("STATE_CONFLICT", "已结束的询价不再改派。")
    if assignee_id is not None and not assignee_id.strip():
        assignee_id = None
    if assignee_id is not None and assignee_id == inquiry.assignee_id:
        return inquiry
    before = {"assignee_id": inquiry.assignee_id}
    inquiry.assignee_id = assignee_id
    inquiry.version += 1
    inquiry.updated_at = now_ms()
    audit.record(
        session,
        actor_type="staff",
        actor_id=actor_id,
        action="inquiry.assign",
        resource_type="inquiry",
        resource_id=inquiry.id,
        before=before,
        after={"assignee_id": assignee_id},
        reason=note,
    )
    session.flush()
    return inquiry


def publish_quote(
    session: Session, *, inquiry_id: str, actor_id: str, payload: QuotePublishInput
) -> Quote:
    """发布报价版本：金额按明细重算，旧版本置 ``SUPERSEDED``，询价进入 ``QUOTED``。"""
    inquiry = repo.get_inquiry_for_staff(session, inquiry_id)
    if inquiry is None:
        raise AppError("RESOURCE_NOT_FOUND")
    if inquiry.status not in ("SUBMITTED", "QUOTED"):
        raise AppError("STATE_CONFLICT", "当前询价状态不允许发布报价。")

    policy = commission_ports.commission_policy(session)
    line_items = payload.line_items
    total_cents = sum(item.quantity * item.unit_price_cents for item in line_items)
    if total_cents <= 0:
        raise AppError("VALIDATION_ERROR", "报价总额必须大于 0。", fields={"line_items": "金额无效"})
    percent = int(policy["prepayment_percent"])
    prepayment_cents = (
        payload.prepayment_cents
        if payload.prepayment_cents is not None
        else total_cents * percent // 100
    )
    if prepayment_cents > total_cents:
        raise AppError(
            "VALIDATION_ERROR",
            "预付款不能高于报价总额。",
            fields={"prepayment_cents": "不能高于总额"},
        )
    moment = now_ms()
    if payload.expires_at:
        expires_at = _parse_moment(payload.expires_at) or 0
    elif payload.valid_days is not None:
        expires_at = add_days_ms(int(payload.valid_days), moment)
    else:
        expires_at = add_days_ms(int(policy["quote_valid_days"]), moment)
    if expires_at <= moment:
        raise AppError("VALIDATION_ERROR", "报价有效期必须晚于当前时间。", fields={"expires_at": "无效"})

    session.execute(
        sa.update(Quote)
        .where(Quote.inquiry_id == inquiry.id, Quote.status == "PUBLISHED")
        .values(status="SUPERSEDED", superseded_at=moment, version=Quote.version + 1)
    )
    quote = Quote(
        id=new_id("quote"),
        inquiry_id=inquiry.id,
        quote_version=repo.max_quote_version(session, inquiry_id=inquiry.id) + 1,
        status="PUBLISHED",
        currency=payload.currency,
        total_cents=total_cents,
        prepayment_cents=prepayment_cents,
        balance_cents=total_cents - prepayment_cents,
        expires_at=expires_at,
        estimated_start=_parse_moment(payload.estimated_start),
        estimated_delivery=_parse_moment(payload.estimated_delivery),
        included_revisions=(
            payload.included_revisions
            if payload.included_revisions is not None
            else int(policy["free_revision_count"])
        ),
        exclusions=list(payload.exclusions),
        terms_version=payload.terms_version or str(policy["terms_version"]),
        issuer_id=actor_id,
        note=payload.note,
        published_at=moment,
    )
    session.add(quote)
    session.flush()
    for position, item in enumerate(line_items):
        session.add(
            QuoteItem(
                id=new_id("quote"),
                quote_id=quote.id,
                position=position,
                label=item.label,
                quantity=item.quantity,
                unit_price_cents=item.unit_price_cents,
                amount_cents=item.quantity * item.unit_price_cents,
                note=item.note,
            )
        )
    inquiry.status = "QUOTED"
    inquiry.staff_seen_revision = inquiry.current_revision
    inquiry.version += 1
    inquiry.updated_at = moment
    session.flush()

    commission_ports.notify_customer(
        session,
        user_id=inquiry.user_id,
        event_type="quote.published",
        title=f"报价已发布（版本 {quote.quote_version}）",
        body="",
        payload={
            "quote_id": quote.id,
            "quote_version": quote.quote_version,
            "total_cents": quote.total_cents,
            "prepayment_cents": quote.prepayment_cents,
            "currency": quote.currency,
            "expires_at": iso(quote.expires_at),
        },
    )
    enqueue(
        session,
        event_type="quote.published",
        aggregate_id=quote.id,
        payload={
            "inquiry_id": inquiry.id,
            "quote_version": quote.quote_version,
            "total_cents": quote.total_cents,
            "expires_at": quote.expires_at,
        },
        aggregate_version=quote.quote_version,
    )
    audit.record(
        session,
        actor_type="staff",
        actor_id=actor_id,
        action="quote.publish",
        resource_type="quote",
        resource_id=quote.id,
        after={
            "inquiry_id": inquiry.id,
            "quote_version": quote.quote_version,
            "total_cents": quote.total_cents,
            "prepayment_cents": quote.prepayment_cents,
            "terms_version": quote.terms_version,
        },
    )
    session.flush()
    return quote


def list_quotes(session: Session, *, user_id: str, inquiry_id: str) -> list[Quote]:
    inquiry = get_inquiry_detail(session, user_id=user_id, inquiry_id=inquiry_id)
    return repo.list_quotes_for_inquiry(session, inquiry_id=inquiry.id)


def expire_quotes(session: Session, *, moment: int | None = None, limit: int = 50) -> int:
    """到期处理（worker 调用）：报价置 ``EXPIRED``，询价 ``QUOTED → EXPIRED``。"""
    now = moment if moment is not None else now_ms()
    rows = repo.expired_published_quotes(session, moment=now, limit=limit)
    for quote in rows:
        quote.status = "EXPIRED"
        quote.version += 1
        quote.updated_at = now
        inquiry = repo.get_inquiry_for_staff(session, quote.inquiry_id)
        if inquiry is not None and inquiry.status == "QUOTED":
            inquiry.status = "EXPIRED"
            inquiry.version += 1
            inquiry.updated_at = now
    if rows:
        session.flush()
    return len(rows)


# ------------------------------------------------------------------ 接受报价
def claim_quote(session: Session, *, quote: Quote, moment: int) -> bool:
    """原子抢占报价的接受权。

    条件 UPDATE 只允许 ``PUBLISHED`` 版本被抢占一次：并发两个标签页同时接受时，
    只有一个连接拿到 ``rowcount = 1``，另一个拿到 0 并返回同一订单。
    """
    result = session.execute(
        sa.update(Quote)
        .where(
            Quote.id == quote.id,
            Quote.quote_version == quote.quote_version,
            Quote.status == "PUBLISHED",
        )
        .values(status="ACCEPTED", accepted_at=moment, version=Quote.version + 1)
    )
    return bool(affected_rows(result))


def _reserve_capacity(session: Session, *, round_row: ScheduleRound) -> None:
    """名额原子占用：``reserved_count + committed_count < capacity``。"""
    result = session.execute(
        sa.update(ScheduleRound)
        .where(
            ScheduleRound.id == round_row.id,
            ScheduleRound.status == "OPEN",
            ScheduleRound.reserved_count + ScheduleRound.committed_count < ScheduleRound.capacity,
        )
        .values(reserved_count=ScheduleRound.reserved_count + 1, version=ScheduleRound.version + 1)
    )
    if not affected_rows(result):
        raise AppError("NO_CAPACITY")
    session.expire(round_row)


def _eligible_application(
    session: Session, *, inquiry: Inquiry
) -> tuple[ScheduleRound | None, ScheduleApplication | None]:
    """名额资格：普通申请需先通过人工筛选（``SELECTED``）。"""
    applications = repo.selected_applications_for_inquiry(session, inquiry_id=inquiry.id)
    if not applications:
        return None, None
    application = applications[0]
    round_row = repo.get_round(session, application.round_id)
    if round_row is None:
        return None, application
    if round_row.mode == "rush":
        commission_ports.require_rush_enabled(session)
    if application.status != "SELECTED":
        raise AppError("STATE_CONFLICT", "该档期申请尚未入选，请等待工作室确认。")
    return round_row, application


def accept_quote_for_user(
    session: Session,
    *,
    user_id: str,
    quote_id: str,
    quote_version: int,
    terms_version: str,
    accepted: bool = True,
    inquiry_id: str | None = None,
    idempotency_key: str | None = None,
) -> tuple[CreatedOrder, CommissionProject]:
    """接受报价并建立唯一委托订单。

    同一事务内完成：校验（版本、有效期、归属、名额资格、条款版本、C19 监护人同意）
    → 抢占报价接受权 → 原子占用名额 → 留证条款同意 → 调用商务模块建单并落制作单。
    """
    quote = repo.get_quote_for_user(session, quote_id=quote_id, user_id=user_id)
    if quote is None:
        raise AppError("RESOURCE_NOT_FOUND")
    if inquiry_id is not None and quote.inquiry_id != inquiry_id:
        raise AppError("RESOURCE_NOT_FOUND")
    inquiry = repo.get_inquiry(session, inquiry_id=quote.inquiry_id, user_id=user_id)
    if inquiry is None:
        raise AppError("RESOURCE_NOT_FOUND")

    request_hash = digest(
        {
            "op": "quote.accept",
            "user_id": user_id,
            "quote_id": quote.id,
            "quote_version": quote_version,
            "terms_version": terms_version,
        }
    )
    existing = _existing_idempotent(
        session,
        actor_id=user_id,
        operation="quote.accept",
        key=idempotency_key,
        request_hash=request_hash,
    )
    if existing is not None and existing.resource_id:
        order = order_from_snapshot(existing.response_snapshot or {"order_id": existing.resource_id, "totals": {}})
        project = repo.get_project_for_order(session, order_id=order.order_id)
        if project is None:
            raise AppError("STATE_CONFLICT", "订单已存在但制作单缺失，请联系客服核对。")
        return order, project

    # 已被（并发或重复请求）接受：返回同一订单，不再占用第二个名额。
    if quote.status == "ACCEPTED" and quote.contract_snapshot:
        order = order_from_snapshot(quote.contract_snapshot)
        project = repo.get_project_for_order(session, order_id=order.order_id)
        if project is None:
            raise AppError("STATE_CONFLICT", "订单已存在但制作单缺失，请联系客服核对。")
        return order, project

    if not accepted:
        raise AppError("VALIDATION_ERROR", "请先阅读并同意委托条款。", fields={"accepted": "必选"})
    if quote_version != quote.quote_version or quote.status in ("SUPERSEDED", "WITHDRAWN"):
        raise AppError("QUOTE_CHANGED")
    moment = now_ms()
    if quote.status == "EXPIRED" or quote.expires_at <= moment:
        raise AppError("QUOTE_EXPIRED")

    policy = commission_ports.commission_policy(session)
    if not terms_version or terms_version != quote.terms_version:
        raise AppError("QUOTE_CHANGED", "条款版本已更新，请重新查看最新报价与条款。")
    if str(policy["terms_version"]) != quote.terms_version:
        raise AppError("QUOTE_CHANGED", "委托条款已更新，请让工作室按最新条款重新发布报价。")
    if inquiry.guardian_required and not commission_ports.has_guardian_consent(session, user_id=user_id):
        # C19：未成年付费委托必须有有效的监护人同意与额外签署。
        raise AppError("STATE_CONFLICT", "未成年委托需要监护人同意与额外签署监护人条款。")

    if not claim_quote(session, quote=quote, moment=moment):
        # 另一个请求先完成接受：返回它建立的订单（rowcount = 0）。
        session.expire(quote)
        current = repo.get_quote(session, quote.id)
        if current is not None and current.status == "ACCEPTED" and current.contract_snapshot:
            order = order_from_snapshot(current.contract_snapshot)
            project = repo.get_project_for_order(session, order_id=order.order_id)
            if project is not None:
                return order, project
        raise AppError("QUOTE_CHANGED")

    reservation: SlotReservation | None = None
    round_row, application = _eligible_application(session, inquiry=inquiry)
    hold_minutes = int(policy["slot_hold_minutes"])
    if round_row is not None:
        if round_row.status != "OPEN":
            raise AppError("SCHEDULE_CLOSED")
        # 先原子占用名额（条件 UPDATE），失败抛 NO_CAPACITY；整笔事务失败则一并回滚。
        _reserve_capacity(session, round_row=round_row)
        reservation = SlotReservation(
            id=new_id("slot"),
            round_id=round_row.id,
            order_id=None,
            user_id=user_id,
            inquiry_id=inquiry.id,
            quote_id=quote.id,
            application_id=application.id if application else None,
            state="RESERVED",
            hold_minutes=hold_minutes,
            expires_at=add_minutes_ms(hold_minutes, moment),
        )

    order = commission_ports.create_commission_order(
        session,
        user_id=user_id,
        quote_id=quote.id,
        quote_version=quote.quote_version,
        total_cents=quote.total_cents,
        prepayment_cents=quote.prepayment_cents,
        currency=quote.currency,
        terms_version=quote.terms_version,
        inquiry_id=inquiry.id,
        slot_reservation_id=reservation.id if reservation else None,
        idempotency_key=idempotency_key or f"{quote.id}:{quote.quote_version}",
    )
    if reservation is not None:
        # 订单号由商务模块生成，同一事务内回填到预留记录。
        reservation.order_id = order.order_id
        session.add(reservation)
        session.flush()

    if inquiry.application_id:
        # C28：服务申请 → 询价 → 报价 → 接受并建单，申请在此刻收口为已转订单。
        catalog_ports.mark_application_converted(
            session, application_id=inquiry.application_id, order_id=order.order_id
        )

    project = CommissionProject(
        id=new_id("project"),
        order_id=order.order_id,
        inquiry_id=inquiry.id,
        quote_id=quote.id,
        user_id=user_id,
        status="QUEUED",
        currency=quote.currency,
        total_cents=quote.total_cents,
        prepayment_cents=quote.prepayment_cents,
        terms_version=quote.terms_version,
        estimated_start=quote.estimated_start,
        estimated_delivery=quote.estimated_delivery,
        balance_due_at=add_days_ms(int(policy["balance_due_days"]), moment),
    )
    session.add(project)
    inquiry.status = "ACCEPTED"
    inquiry.accepted_quote_id = quote.id
    inquiry.accepted_at = moment
    inquiry.version += 1
    inquiry.updated_at = moment
    quote.order_id = order.order_id
    session.flush()

    snapshot = {
        "order_id": order.order_id,
        "order_no": order.order_no,
        "status": order.status,
        "installment_id": order.installment_id,
        "totals": {
            "goods_cents": order.totals.goods_cents,
            "discount_cents": order.totals.discount_cents,
            "shipping_cents": order.totals.shipping_cents,
            "total_cents": order.totals.total_cents,
            "currency": order.totals.currency,
        },
        "expires_at": order.expires_at,
        "quote_version": quote.quote_version,
        "terms_version": quote.terms_version,
        "accepted_at": moment,
    }
    consent_id = commission_ports.record_terms_consent(
        session,
        user_id=user_id,
        version=quote.terms_version,
        quote_id=quote.id,
        content_hash=_snapshot_digest(
            quote_id=quote.id,
            quote_version=quote.quote_version,
            total_cents=quote.total_cents,
            prepayment_cents=quote.prepayment_cents,
            currency=quote.currency,
            terms_version=quote.terms_version,
            inquiry_id=inquiry.id,
        ),
        evidence={
            **snapshot,
            "project_id": project.id,
            "slot_reservation_id": reservation.id if reservation else None,
        },
    )
    snapshot["consent_id"] = consent_id
    snapshot["project_id"] = project.id
    snapshot["slot_reservation_id"] = reservation.id if reservation else None
    quote.contract_snapshot = snapshot
    session.flush()

    _remember_idempotent(
        session,
        actor_id=user_id,
        operation="quote.accept",
        key=idempotency_key,
        request_hash=request_hash,
        resource_id=order.order_id,
        response=snapshot,
    )
    enqueue(
        session,
        event_type="quote.accepted",
        aggregate_id=quote.id,
        payload={
            "inquiry_id": inquiry.id,
            "order_id": order.order_id,
            "project_id": project.id,
            "quote_version": quote.quote_version,
            "total_cents": quote.total_cents,
            "prepayment_cents": quote.prepayment_cents,
            "slot_reservation_id": reservation.id if reservation else None,
        },
        aggregate_version=quote.quote_version,
    )
    audit.record(
        session,
        actor_type="customer",
        actor_id=user_id,
        action="quote.accept",
        resource_type="quote",
        resource_id=quote.id,
        after={
            "order_id": order.order_id,
            "project_id": project.id,
            "quote_version": quote.quote_version,
            "terms_version": quote.terms_version,
            "consent_id": consent_id,
        },
    )
    session.flush()
    return order, project


# ------------------------------------------------------------------ 名额生命周期
def commit_slot_on_prepayment(session: Session, *, order_id: str) -> bool:
    """预付款到账：预留转正式占用（``reserved_count - 1``、``committed_count + 1``）。"""
    reservation = repo.get_reservation_for_order(session, order_id=order_id)
    if reservation is None or reservation.state in ("COMMITTED", "RELEASED", "EXPIRED"):
        return False
    moment = now_ms()
    result = session.execute(
        sa.update(ScheduleRound)
        .where(
            ScheduleRound.id == reservation.round_id,
            ScheduleRound.reserved_count >= 1,
        )
        .values(
            reserved_count=ScheduleRound.reserved_count - 1,
            committed_count=ScheduleRound.committed_count + 1,
            version=ScheduleRound.version + 1,
        )
    )
    if not affected_rows(result):
        raise AppError("STATE_CONFLICT", "名额占用与档期计数不一致，请人工核对。")
    reservation.state = "COMMITTED"
    reservation.committed_at = moment
    audit.record(
        session,
        actor_type="system",
        action="slot.commit",
        resource_type="slot_reservation",
        resource_id=reservation.id,
        after={"order_id": order_id, "round_id": reservation.round_id, "state": "COMMITTED"},
    )
    session.flush()
    return True


def release_slot_for_order(session: Session, *, order_id: str, reason: str) -> bool:
    """订单关闭核对完成、确认无在途付款后释放名额。已正式占用的名额需人工处理。"""
    reservation = repo.get_reservation_for_order(session, order_id=order_id)
    if reservation is None or reservation.state in ("RELEASED", "EXPIRED"):
        return False
    if reservation.state == "COMMITTED":
        raise AppError("STATE_CONFLICT", "该名额已转为正式占用，需人工核对后再释放。")
    result = session.execute(
        sa.update(ScheduleRound)
        .where(ScheduleRound.id == reservation.round_id, ScheduleRound.reserved_count >= 1)
        .values(reserved_count=ScheduleRound.reserved_count - 1, version=ScheduleRound.version + 1)
    )
    if not affected_rows(result):
        raise AppError("STATE_CONFLICT", "名额释放与档期计数不一致，请人工核对。")
    reservation.state = "RELEASED"
    reservation.released_at = now_ms()
    reservation.release_reason = reason[:200]
    session.flush()
    return True


def expire_slot_reservations(session: Session, *, moment: int | None = None, limit: int = 50) -> int:
    """名额预留超时：进入关闭核对（``CLOSING``）并登记通知事件。

    关闭核对确认渠道无在途付款后才调用 :func:`release_slot_for_order` 释放名额。
    """
    now = moment if moment is not None else now_ms()
    rows = repo.due_reservations(session, moment=now, limit=limit)
    for reservation in rows:
        reservation.state = "CLOSING"
        enqueue(
            session,
            event_type="schedule.slot_expiring",
            aggregate_id=reservation.id,
            payload={
                "order_id": reservation.order_id,
                "round_id": reservation.round_id,
                "user_id": reservation.user_id,
                "expires_at": reservation.expires_at,
            },
        )
    if rows:
        session.flush()
    return len(rows)


def rounds_page(
    session: Session, *, limit: int = 20, offset: int = 0, moment: int | None = None
) -> tuple[list[ScheduleRound], str | None, bool]:
    rows = repo.list_rounds(session, limit=limit + 1, offset=offset)
    return paginate(rows, limit=limit, offset=offset)


def create_schedule_round(
    session: Session, *, actor_id: str | None, payload: ScheduleRoundInput
) -> ScheduleRound:
    """员工创建档期：保存时区、UTC 开始/截止与容量（07 文档）。"""
    start_at = _parse_moment(payload.start_at)
    end_at = _parse_moment(payload.end_at)
    if start_at is None or end_at is None:
        raise AppError("VALIDATION_ERROR", "请填写档期开始与截止时间。", fields={"start_at": "必填"})
    if start_at >= end_at:
        raise AppError("VALIDATION_ERROR", "档期开始时间必须早于截止时间。", fields={"end_at": "无效"})
    opens_at = _parse_moment(payload.opens_at)
    closes_at = _parse_moment(payload.closes_at)
    if opens_at is not None and closes_at is not None and opens_at >= closes_at:
        raise AppError("VALIDATION_ERROR", "申请开放时间必须早于截止时间。", fields={"closes_at": "无效"})
    if payload.mode == "rush":
        commission_ports.require_rush_enabled(session)

    row = ScheduleRound(
        id=new_id("round"),
        title=payload.title,
        mode=payload.mode,
        status=payload.status,
        stage_label=payload.stage_label,
        timezone=payload.timezone,
        start_at=start_at,
        end_at=end_at,
        opens_at=opens_at,
        closes_at=closes_at,
        capacity=payload.capacity,
        note=payload.note,
    )
    session.add(row)
    session.flush()
    audit.record(
        session,
        actor_type="staff",
        actor_id=actor_id,
        action="schedule_round.create",
        resource_type="schedule_round",
        resource_id=row.id,
        after={
            "title": row.title,
            "mode": row.mode,
            "capacity": row.capacity,
            "start_at": iso(row.start_at),
            "end_at": iso(row.end_at),
        },
    )
    return row


def apply_to_round(
    session: Session,
    *,
    user_id: str,
    round_id: str,
    payload: ScheduleApplicationInput,
    idempotency_key: str | None = None,
) -> ScheduleApplication:
    """提交档期申请（本人 + 幂等）。同轮同询价重复提交返回原对象。"""
    round_row = repo.get_round(session, round_id)
    if round_row is None:
        raise AppError("RESOURCE_NOT_FOUND")
    moment = now_ms()
    if round_row.status != "OPEN":
        raise AppError("SCHEDULE_CLOSED")
    if round_row.opens_at is not None and moment < round_row.opens_at:
        raise AppError("SCHEDULE_CLOSED", "该档期尚未开放申请。")
    if round_row.closes_at is not None and moment >= round_row.closes_at:
        raise AppError("SCHEDULE_CLOSED", "该档期申请已截止。")
    if round_row.mode == "rush":
        commission_ports.require_rush_enabled(session)

    inquiry = repo.get_inquiry(session, inquiry_id=payload.inquiry_id, user_id=user_id)
    if inquiry is None:
        raise AppError("RESOURCE_NOT_FOUND")
    if inquiry.status in TERMINAL_INQUIRY_STATUSES:
        raise AppError("STATE_CONFLICT", "该询价已结束，不能申请档期。")

    request_hash = digest(
        {"op": "schedule.apply", "user_id": user_id, "round_id": round_id, "inquiry_id": inquiry.id}
    )
    existing = _existing_idempotent(
        session,
        actor_id=user_id,
        operation="schedule.apply",
        key=idempotency_key,
        request_hash=request_hash,
    )
    if existing is not None and existing.resource_id:
        row = repo.get_application(session, application_id=existing.resource_id, user_id=user_id)
        if row is not None:
            return row

    duplicate = repo.find_application(
        session, round_id=round_row.id, user_id=user_id, inquiry_id=inquiry.id
    )
    if duplicate is not None:
        return duplicate
    if repo.count_applications(session, round_id=round_row.id, user_id=user_id) >= MAX_APPLICATIONS_PER_ROUND:
        raise AppError(
            "STATE_CONFLICT",
            f"每个档期最多提交 {MAX_APPLICATIONS_PER_ROUND} 条申请。",
        )

    application = _insert_application(
        session,
        round_row=round_row,
        user_id=user_id,
        inquiry=inquiry,
        note=payload.note,
        idempotency_key=idempotency_key,
    )
    _remember_idempotent(
        session,
        actor_id=user_id,
        operation="schedule.apply",
        key=idempotency_key,
        request_hash=request_hash,
        resource_id=application.id,
    )
    audit.record(
        session,
        actor_type="customer",
        actor_id=user_id,
        action="schedule.apply",
        resource_type="schedule_application",
        resource_id=application.id,
        after={
            "round_id": round_row.id,
            "inquiry_id": inquiry.id,
            "rank_sequence": application.rank_sequence,
        },
    )
    return application


def _insert_application(
    session: Session,
    *,
    round_row: ScheduleRound,
    user_id: str,
    inquiry: Inquiry,
    note: str | None,
    idempotency_key: str | None,
) -> ScheduleApplication:
    """插入申请；限时模式以数据库分配的单调序号排位，冲突时重取。"""
    rush = round_row.mode == "rush"
    application: ScheduleApplication | None = None
    for attempt in range(3):
        rank = repo.max_rank_sequence(session, round_id=round_row.id) + 1 if rush else None
        application = ScheduleApplication(
            id=new_id("application"),
            round_id=round_row.id,
            user_id=user_id,
            inquiry_id=inquiry.id,
            rank_sequence=rank,
            status="SUBMITTED",
            note=note,
            idempotency_key=idempotency_key,
        )
        try:
            with session.begin_nested():
                session.add(application)
                session.flush()
        except IntegrityError:
            duplicate = repo.find_application(
                session, round_id=round_row.id, user_id=user_id, inquiry_id=inquiry.id
            )
            if duplicate is not None:
                return duplicate
            if attempt == 2:
                raise AppError("STATE_CONFLICT", "档期申请序号分配冲突，请重试。") from None
            continue
        return application
    raise AppError("STATE_CONFLICT", "档期申请提交失败，请重试。")  # pragma: no cover


def decide_application(
    session: Session,
    *,
    application_id: str,
    actor_id: str | None,
    decision: str,
    reason: str | None = None,
    rank_sequence: int | None = None,
) -> ScheduleApplication:
    """员工筛选档期申请：``SUBMITTED → SHORTLISTED/SELECTED/REJECTED``。

    ``rank_sequence`` 为人工排序序号（C11：普通申请按审批顺序排队）。
    留空时排在当前档期末尾；已处理的申请不允许重复决定。
    """
    if decision not in ("SHORTLISTED", "SELECTED", "REJECTED"):
        raise AppError("VALIDATION_ERROR", fields={"decision": "非法决定"})
    application = session.get(ScheduleApplication, application_id)
    if application is None:
        raise AppError("RESOURCE_NOT_FOUND")
    if application.status in ("SELECTED", "REJECTED", "WITHDRAWN"):
        raise AppError("STATE_CONFLICT", "该申请已处理，不能重复决定。")
    before = {"status": application.status, "rank_sequence": application.rank_sequence}
    application.status = decision
    application.decided_by = actor_id
    application.decided_at = now_ms()
    application.decision_reason = (reason or "")[:300] or None
    if rank_sequence is not None:
        application.rank_sequence = rank_sequence
    elif not application.rank_sequence:
        application.rank_sequence = (
            repo.max_rank_sequence(session, round_id=application.round_id) + 1
        )
    application.version += 1
    application.updated_at = now_ms()
    audit.record(
        session,
        actor_type="staff",
        actor_id=actor_id,
        action="schedule.decide",
        resource_type="schedule_application",
        resource_id=application.id,
        before=before,
        after={"status": decision, "rank_sequence": application.rank_sequence},
        reason=reason,
    )
    session.flush()
    return application


# ------------------------------------------------------------------ 制作单
def commit_project_order(
    session: Session,
    *,
    order_id: str,
    user_id: str,
    inquiry_id: str,
    quote_id: str,
    total_cents: int,
    prepayment_cents: int,
    currency: str,
    terms_version: str,
    estimated_start: int | None = None,
    estimated_delivery: int | None = None,
) -> CommissionProject:
    """由商务模块工作流登记的委托订单建立制作单（同一订单只建一张，唯一约束保证）。"""
    existing = repo.get_project_for_order(session, order_id=order_id)
    if existing is not None:
        return existing
    policy = commission_ports.commission_policy(session)
    project = CommissionProject(
        id=new_id("project"),
        order_id=order_id,
        inquiry_id=inquiry_id,
        quote_id=quote_id,
        user_id=user_id,
        status="QUEUED",
        currency=currency,
        total_cents=total_cents,
        prepayment_cents=prepayment_cents,
        terms_version=terms_version,
        estimated_start=estimated_start,
        estimated_delivery=estimated_delivery,
        balance_due_at=add_days_ms(int(policy["balance_due_days"])),
    )
    session.add(project)
    session.flush()
    return project


def project_for_actor(
    session: Session, *, project_id: str, user_id: str | None, maker_id: str | None
) -> CommissionProject:
    """本人或分配装师可读；其他主体返回 ``RESOURCE_NOT_FOUND``（不泄露是否存在）。"""
    project = None
    if user_id:
        project = repo.get_project_for_user(session, project_id=project_id, user_id=user_id)
    if project is None and maker_id:
        project = repo.get_project_for_maker(session, project_id=project_id, maker_id=maker_id)
    if project is None:
        raise AppError("RESOURCE_NOT_FOUND")
    return project


def project_summary_for_order(session: Session, *, order_id: str) -> dict[str, Any] | None:
    """订单页用的制作单摘要：只含进度，不含量体与私密素材。"""
    project = repo.get_project_for_order(session, order_id=order_id)
    if project is None:
        return None
    stage = repo.current_stage(session, project_id=project.id)
    return {
        "project_id": project.id,
        "status": project.status,
        "current_stage_code": project.current_stage_code,
        "estimated_delivery": iso(project.estimated_delivery),
        "awaiting_customer": bool(
            stage is not None and stage.needs_customer_confirmation and stage.confirmed_revision is None
        ),
    }


# ------------------------------------------------------------------ 量体
def create_measurement(
    session: Session, *, user_id: str, project_id: str, payload: MeasurementInput
) -> Measurement:
    """提交量体资料（本人、私密、按版本追加）。数值越界只提示，不拒绝。"""
    project = repo.get_project_for_user(session, project_id=project_id, user_id=user_id)
    if project is None:
        raise AppError("RESOURCE_NOT_FOUND")
    if project.status not in ("QUEUED", "MEASUREMENT_PENDING", "DESIGN_CONFIRMING"):
        raise AppError("STATE_CONFLICT", "该制作单已进入生产阶段，量体数据需联系客服修改。")
    if len(payload.payload) > MAX_MEASUREMENT_FIELDS:
        raise AppError("VALIDATION_ERROR", "量体字段过多。", fields={"payload": "字段过多"})

    measurements: dict[str, float] = {}
    flagged: list[str] = []
    for name, value in payload.payload.items():
        key = name.strip()[:40]
        if not key:
            raise AppError("VALIDATION_ERROR", "量体字段名不能为空。", fields={"payload": "字段名无效"})
        if value < 0:
            raise AppError(
                "VALIDATION_ERROR", "量体数值不能为负数。", fields={f"payload.{key}": "必须为非负数"}
            )
        measurements[key] = float(value)
        if payload.unit_system == "metric" and not (
            MEASUREMENT_SOFT_MIN_CM <= float(value) <= MEASUREMENT_SOFT_MAX_CM
        ):
            # 特殊体型只能提示并交工作人员确认，禁止硬编码排除用户（07 文档）。
            flagged.append(key)

    policy = commission_ports.commission_policy(session)
    measurement = Measurement(
        id=new_id("measurement"),
        project_id=project.id,
        version=repo.max_measurement_version(session, project_id=project.id) + 1,
        encrypted_payload=encrypt_pii(_dump_measurements(measurements)),
        unit_system=payload.unit_system,
        note=payload.note,
        flagged_fields=flagged,
        author_id=user_id,
    )
    session.add(measurement)
    session.flush()
    consent_id = commission_ports.record_terms_consent(
        session,
        user_id=user_id,
        version=str(policy["terms_version"]),
        quote_id=measurement.id,
        content_hash=_snapshot_digest(
            project_id=project.id, version=measurement.version, unit=payload.unit_system
        ),
        evidence={"project_id": project.id, "measurement_version": measurement.version},
        purpose="measurement_data",
        resource_type="measurement",
    )
    measurement.consent_id = consent_id
    audit.record(
        session,
        actor_type="customer",
        actor_id=user_id,
        action="measurement.create",
        resource_type="measurement",
        resource_id=measurement.id,
        after={"project_id": project.id, "version": measurement.version, "flagged": flagged},
    )
    session.flush()
    return measurement


def _dump_measurements(values: dict[str, float]) -> str:
    return json.dumps(values, ensure_ascii=False, sort_keys=True)


def load_measurements(measurement: Measurement) -> dict[str, float]:
    data = json.loads(decrypt_pii(measurement.encrypted_payload))
    return {str(key): float(value) for key, value in data.items()}


# ------------------------------------------------------------------ 制作阶段
def upsert_production_stage(
    session: Session,
    *,
    project_id: str,
    actor_id: str | None,
    stage_code: str,
    description: str | None = None,
    title: str | None = None,
    owner_id: str | None = None,
    estimated_at: int | None = None,
    media_asset_ids: list[str] | None = None,
    needs_customer_confirmation: bool | None = None,
    status: str | None = None,
) -> ProductionStage:
    """员工更新阶段内容。已确认阶段再次修改内容会递增 ``revision``，旧确认随之失效。"""
    if stage_code not in PROJECT_FLOW:
        raise AppError("VALIDATION_ERROR", fields={"stage_code": "未登记的阶段"})
    project = repo.get_project(session, project_id)
    if project is None:
        raise AppError("RESOURCE_NOT_FOUND")
    stage = repo.latest_stage_for_code(session, project_id=project_id, stage_code=stage_code)
    moment = now_ms()
    if stage is None:
        stage = ProductionStage(
            id=new_id("stage"),
            project_id=project_id,
            stage_code=stage_code,
            revision=1,
            status=status or ("AWAITING_CUSTOMER" if needs_customer_confirmation else "PENDING"),
            title=title or STAGE_TITLES.get(stage_code, stage_code),
            description=description or "",
            owner_id=owner_id or actor_id,
            estimated_at=estimated_at,
            needs_customer_confirmation=1 if needs_customer_confirmation else 0,
            media_asset_ids=list(media_asset_ids or []),
        )
        session.add(stage)
        session.flush()
        if stage.needs_customer_confirmation:
            notify_stage_ready(session, stage=stage, project=project)
        audit.record(
            session,
            actor_type="staff",
            actor_id=actor_id,
            action="production_stage.upsert",
            resource_type="production_stage",
            resource_id=stage.id,
            after={"stage_code": stage.stage_code, "revision": stage.revision, "status": stage.status},
        )
        return stage

    content_changed = (
        (description is not None and description != stage.description)
        or (media_asset_ids is not None and list(media_asset_ids) != list(stage.media_asset_ids or []))
        or (title is not None and title != stage.title)
    )
    if content_changed and stage.status in ("CONFIRMED", "AWAITING_CUSTOMER"):
        # 旧图不能代替新阶段确认：内容变化即产生新 revision，客户需按新版本重新确认。
        stage.revision += 1
        stage.confirmed_revision = None
        stage.approved_by = None
        stage.approved_at = None
    if description is not None:
        stage.description = description
    if title is not None:
        stage.title = title
    if owner_id is not None:
        stage.owner_id = owner_id
    if estimated_at is not None:
        stage.estimated_at = estimated_at
    if media_asset_ids is not None:
        stage.media_asset_ids = list(media_asset_ids)
    if needs_customer_confirmation is not None:
        stage.needs_customer_confirmation = 1 if needs_customer_confirmation else 0
    if status is not None:
        stage.status = status
    elif stage.needs_customer_confirmation and stage.confirmed_revision is None:
        stage.status = "AWAITING_CUSTOMER"
    if stage.started_at is None and stage.status in ("IN_PROGRESS", "AWAITING_CUSTOMER"):
        stage.started_at = moment
    stage.version += 1
    stage.updated_at = moment
    session.flush()

    if content_changed and stage.needs_customer_confirmation and stage.confirmed_revision is None:
        notify_stage_ready(session, stage=stage, project=project)
    audit.record(
        session,
        actor_type="staff",
        actor_id=actor_id,
        action="production_stage.upsert",
        resource_type="production_stage",
        resource_id=stage.id,
        after={"stage_code": stage.stage_code, "revision": stage.revision, "status": stage.status},
    )
    return stage


def notify_stage_ready(session: Session, *, stage: ProductionStage, project: CommissionProject) -> None:
    """阶段需要客户确认时通知客户并登记 outbox 事件。"""
    commission_ports.notify_customer(
        session,
        user_id=project.user_id,
        event_type="production.stage_ready",
        title=f"制作阶段待确认：{stage.title or stage.stage_code}",
        body="",
        payload={
            "project_id": project.id,
            "stage_name": stage.title or stage.stage_code,
            "revision": stage.revision,
            "description": stage.description,
        },
        category="production",
    )
    enqueue(
        session,
        event_type="production.stage_ready",
        aggregate_id=stage.id,
        payload={"project_id": project.id, "stage_code": stage.stage_code, "revision": stage.revision},
    )


def confirm_stage(
    session: Session, *, user_id: str, stage_id: str, payload: StageConfirmInput
) -> ProductionStage:
    """客户确认阶段，绑定其看到的 revision；旧版确认返回 409。

    确认人恒为当前登录客户，工作人员无法代替客户伪造确认。
    """
    stage = repo.get_stage_for_user(session, stage_id=stage_id, user_id=user_id)
    if stage is None:
        raise AppError("RESOURCE_NOT_FOUND")
    if payload.revision != stage.revision:
        raise AppError("VERSION_CONFLICT")
    if payload.version is not None and payload.version != stage.version:
        raise AppError("VERSION_CONFLICT")
    if not stage.needs_customer_confirmation:
        raise AppError("STATE_CONFLICT", "该阶段无需客户确认。")
    if stage.confirmed_revision == stage.revision and stage.status == "CONFIRMED":
        return stage
    if not payload.accepted:
        raise AppError("VALIDATION_ERROR", "请确认后再提交。", fields={"accepted": "必选"})

    moment = now_ms()
    result = session.execute(
        sa.update(ProductionStage)
        .where(
            ProductionStage.id == stage.id,
            ProductionStage.revision == payload.revision,
            ProductionStage.needs_customer_confirmation == 1,
        )
        .values(
            status="CONFIRMED",
            confirmed_revision=payload.revision,
            approved_by=user_id,
            approved_at=moment,
            finished_at=moment,
            version=ProductionStage.version + 1,
            updated_at=moment,
        )
    )
    if not affected_rows(result):
        # 装师在上传新阶段图后客户确认旧版：必须 409，不能把旧确认写到新版本上。
        raise AppError("VERSION_CONFLICT")
    session.expire(stage)
    refreshed = repo.get_stage_for_user(session, stage_id=stage.id, user_id=user_id)
    assert refreshed is not None
    audit.record(
        session,
        actor_type="customer",
        actor_id=user_id,
        action="production_stage.confirm",
        resource_type="production_stage",
        resource_id=stage.id,
        after={"revision": payload.revision, "status": "CONFIRMED"},
        reason=payload.note,
    )
    session.flush()
    return refreshed


def delay_stage(
    session: Session,
    *,
    stage_id: str,
    actor_id: str | None,
    estimated_at: int,
    reason: str,
) -> ProductionStage:
    """延期：更新预计时间并说明原因，同时登记通知（07 文档）。"""
    if not reason.strip():
        raise AppError("VALIDATION_ERROR", "请说明延期原因。", fields={"reason": "必填"})
    stage = repo.get_stage(session, stage_id)
    if stage is None:
        raise AppError("RESOURCE_NOT_FOUND")
    project = repo.get_project(session, stage.project_id)
    if project is None:
        raise AppError("RESOURCE_NOT_FOUND")
    moment = now_ms()
    before = {"estimated_at": iso(stage.estimated_at), "status": stage.status}
    stage.estimated_at = estimated_at
    stage.delay_reason = reason[:300]
    stage.status = "DELAYED"
    stage.version += 1
    stage.updated_at = moment
    project.estimated_delivery = estimated_at
    project.version += 1
    project.updated_at = moment
    commission_ports.notify_customer(
        session,
        user_id=project.user_id,
        event_type="production.stage_delayed",
        title="制作进度调整通知",
        body=f"预计时间已更新为 {iso(estimated_at)}。原因：{reason}",
        link_path=f"/account/commissions/{project.id}",
        payload={
            "project_id": project.id,
            "stage_code": stage.stage_code,
            "estimated_at": iso(estimated_at),
            "reason": reason,
        },
        category="production",
    )
    audit.record(
        session,
        actor_type="staff",
        actor_id=actor_id,
        action="production_stage.delay",
        resource_type="production_stage",
        resource_id=stage.id,
        before=before,
        after={"estimated_at": iso(estimated_at), "status": stage.status},
        reason=reason,
    )
    session.flush()
    return stage


def advance_project_stage(
    session: Session, *, project_id: str, to_status: str, actor_id: str | None
) -> CommissionProject:
    """推进制作单状态：只能按 07 文档基线顺序前进，且禁止跳过尾款校验发货。"""
    project = repo.get_project(session, project_id)
    if project is None:
        raise AppError("RESOURCE_NOT_FOUND")
    if to_status not in PROJECT_FLOW:
        raise AppError("VALIDATION_ERROR", fields={"status": "未登记的制作状态"})
    if project.status not in PROJECT_FLOW:
        raise AppError("STATE_CONFLICT", "该制作单已结束或已取消。")
    current_index = PROJECT_FLOW.index(project.status)
    target_index = PROJECT_FLOW.index(to_status)
    if target_index != current_index + 1:
        raise AppError("STATE_CONFLICT", "制作单状态必须按顺序推进，不能跳步。")
    if to_status == "SHIPPED" and not commission_ports.order_balance_settled(
        session, order_id=project.order_id, total_cents=project.total_cents
    ):
        # 尾款未结清不能发货（07 文档）。
        raise AppError("STATE_CONFLICT", "尾款尚未结清，不能发货。")

    moment = now_ms()
    before = {"status": project.status}
    project.status = to_status
    project.current_stage_code = to_status
    project.version += 1
    project.updated_at = moment
    if to_status == "MEASUREMENT_PENDING" and project.started_at is None:
        project.started_at = moment
    if to_status == "SHIPPED":
        project.shipped_at = moment
    if to_status == "COMPLETED":
        project.completed_at = moment

    previous_code = PROJECT_FLOW[current_index]
    previous = repo.latest_stage_for_code(session, project_id=project.id, stage_code=previous_code)
    if previous is not None and previous.status not in ("DONE",):
        previous.status = "DONE"
        previous.finished_at = previous.finished_at or moment
        previous.version += 1
        previous.updated_at = moment

    stage = repo.latest_stage_for_code(session, project_id=project.id, stage_code=to_status)
    if stage is None:
        stage = ProductionStage(
            id=new_id("stage"),
            project_id=project.id,
            stage_code=to_status,
            revision=1,
            status="IN_PROGRESS",
            title=STAGE_TITLES.get(to_status, to_status),
            owner_id=project.maker_id or actor_id,
            started_at=moment,
        )
        session.add(stage)
    else:
        stage.status = "IN_PROGRESS"
        stage.started_at = stage.started_at or moment
        stage.version += 1
        stage.updated_at = moment
    session.flush()
    audit.record(
        session,
        actor_type="staff",
        actor_id=actor_id,
        action="commission_project.advance",
        resource_type="commission_project",
        resource_id=project.id,
        before=before,
        after={"status": project.status},
    )
    session.flush()
    return project


def assign_maker(
    session: Session, *, project_id: str, maker_id: str | None, actor_id: str | None
) -> CommissionProject:
    project = repo.get_project(session, project_id)
    if project is None:
        raise AppError("RESOURCE_NOT_FOUND")
    before = {"maker_id": project.maker_id}
    project.maker_id = maker_id
    project.version += 1
    project.updated_at = now_ms()
    audit.record(
        session,
        actor_type="staff",
        actor_id=actor_id,
        action="commission_project.assign",
        resource_type="commission_project",
        resource_id=project.id,
        before=before,
        after={"maker_id": maker_id},
    )
    session.flush()
    return project


# ------------------------------------------------------------------ 变更单
def request_stage_change(
    session: Session, *, user_id: str, stage_id: str, payload: StageChangeInput
) -> ChangeRequest:
    """客户在阶段上提出修改（本人 + 版本），生成 ``change_request`` 草稿。"""
    stage = repo.get_stage_for_user(session, stage_id=stage_id, user_id=user_id)
    if stage is None:
        raise AppError("RESOURCE_NOT_FOUND")
    if payload.revision != stage.revision:
        raise AppError("VERSION_CONFLICT")
    if payload.version is not None and payload.version != stage.version:
        raise AppError("VERSION_CONFLICT")
    if payload.change_request_id is not None:
        return accept_change_request(
            session,
            user_id=user_id,
            change_request_id=payload.change_request_id,
            project_id=stage.project_id,
        )
    if not payload.description.strip():
        raise AppError("VALIDATION_ERROR", "请说明需要修改的内容。", fields={"description": "必填"})

    change = ChangeRequest(
        id=new_id("change"),
        project_id=stage.project_id,
        version=repo.max_change_version(session, project_id=stage.project_id) + 1,
        scope=payload.scope or "other",
        description=payload.description,
        status="SUBMITTED",
        requested_by=user_id,
        requested_by_type="customer",
        terms_version="",
        media_asset_ids=list(payload.media_asset_ids),
    )
    session.add(change)
    stage.status = "CHANGE_REQUESTED"
    stage.version += 1
    stage.updated_at = now_ms()
    session.flush()
    audit.record(
        session,
        actor_type="customer",
        actor_id=user_id,
        action="change_request.request",
        resource_type="change_request",
        resource_id=change.id,
        after={"project_id": stage.project_id, "version": change.version, "scope": change.scope},
    )
    return change


def request_reassessment(
    session: Session, *, user_id: str, inquiry_id: str, payload: ReassessmentInput
) -> Inquiry:
    """客户要求重新评估：``QUOTED → SUBMITTED``（07 文档状态机）。

    规则：
    - 只有本人、且状态为 ``QUOTED`` 的询价可以退回；
    - ``version`` 必须匹配，避免两个标签页覆盖彼此的决定；
    - 历史报价版本不删除，只由后续发布新版本时置为 SUPERSEDED；
    - 已接受（ACCEPTED）的询价不能走本入口：只能取消未付款订单或进入售后。
    """
    inquiry = get_inquiry_detail(session, user_id=user_id, inquiry_id=inquiry_id)
    expected_version = getattr(payload, "version", None)
    if expected_version is not None and expected_version != inquiry.version:
        raise AppError("VERSION_CONFLICT")
    if inquiry.status == "SUBMITTED":
        return inquiry
    if inquiry.status == "ACCEPTED":
        raise AppError(
            "STATE_CONFLICT",
            "该询价已接受并生成订单；如需调整请取消未付款订单或发起售后。",
        )
    if inquiry.status != "QUOTED":
        raise AppError("STATE_CONFLICT", "当前状态不能要求重新评估。")

    moment = now_ms()
    result = session.execute(
        sa.update(Inquiry)
        .where(Inquiry.id == inquiry.id, Inquiry.version == inquiry.version, Inquiry.status == "QUOTED")
        .values(status="SUBMITTED", version=Inquiry.version + 1, updated_at=moment)
    )
    if not affected_rows(result):
        raise AppError("STATE_CONFLICT", "询价状态已变化，请刷新后重试。")
    session.expire(inquiry)
    inquiry = get_inquiry_detail(session, user_id=user_id, inquiry_id=inquiry_id)

    # 旧报价置为 SUPERSEDED，保留版本与金额供复核（07 文档：不能静默加价）。
    session.execute(
        sa.update(Quote)
        .where(Quote.inquiry_id == inquiry.id, Quote.status.in_(("PUBLISHED", "ACCEPTED")))
        .values(status="SUPERSEDED", updated_at=moment)
    )

    _append_revision(
        session,
        inquiry=inquiry,
        author_id=user_id,
        author_type="customer",
        change_note=str(getattr(payload, "reason", "") or "客户要求重新评估")[:300],
        submitted=True,
    )
    audit.record(
        session,
        actor_type="customer",
        actor_id=user_id,
        action="inquiry.request_reassessment",
        resource_type="inquiry",
        resource_id=inquiry.id,
        after={"status": inquiry.status, "version": inquiry.version},
        reason=str(getattr(payload, "reason", "") or "")[:300],
    )
    return inquiry


def get_change_for_user(
    session: Session, *, change_request_id: str, user_id: str
) -> ChangeRequest | None:
    """按客户身份读取变更单（校验项目归属）。"""
    change = repo.get_change_any_project(session, change_request_id=change_request_id)
    if change is None:
        return None
    project = repo.get_project_for_user(session, project_id=change.project_id, user_id=user_id)
    if project is None:
        return None
    return change


def accept_change_request_by_id(
    session: Session, *, user_id: str, change_request_id: str, expected_version: int | None = None
) -> ChangeRequest:
    """按变更单 ID 接受（客户在订单页直接决策）。"""
    change = get_change_for_user(session, change_request_id=change_request_id, user_id=user_id)
    if change is None:
        raise AppError("RESOURCE_NOT_FOUND")
    if expected_version is not None and expected_version != change.version:
        raise AppError("VERSION_CONFLICT")
    return accept_change_request(
        session, user_id=user_id, change_request_id=change.id, project_id=change.project_id
    )


def reject_change_request(
    session: Session, *, user_id: str, change_request_id: str, expected_version: int | None = None
) -> ChangeRequest:
    """客户拒绝变更单：状态转 REJECTED，不改动原金额、合同快照与已成功的付款。"""
    change = get_change_for_user(session, change_request_id=change_request_id, user_id=user_id)
    if change is None:
        raise AppError("RESOURCE_NOT_FOUND")
    if change.status == "REJECTED":
        return change
    if change.status != "SUBMITTED":
        raise AppError("STATE_CONFLICT", "该变更单已结束，不能再次决定。")
    if expected_version is not None and expected_version != change.version:
        raise AppError("VERSION_CONFLICT")

    moment = now_ms()
    result = session.execute(
        sa.update(ChangeRequest)
        .where(
            ChangeRequest.id == change.id,
            ChangeRequest.version == change.version,
            ChangeRequest.status == "SUBMITTED",
        )
        .values(
            status="REJECTED",
            decided_by=user_id,
            decided_at=moment,
            version=ChangeRequest.version + 1,
            updated_at=moment,
        )
    )
    if not affected_rows(result):
        raise AppError("STATE_CONFLICT", "该变更单状态已变化，请刷新后重试。")
    session.expire(change)
    change = get_change_for_user(session, change_request_id=change_request_id, user_id=user_id)
    assert change is not None
    audit.record(
        session,
        actor_type="customer",
        actor_id=user_id,
        action="change_request.reject",
        resource_type="change_request",
        resource_id=change.id,
        after={"status": change.status},
    )
    return change


# 说明：``mark_needs_info`` 只在本文件上方定义一次（已合并补件通知、版本记录与
# 条件更新语义）。合并前此处存在第二份同名定义，会遮蔽上方实现，故删除。


def update_schedule_round(
    session: Session,
    *,
    round_id: str,
    actor_id: str | None,
    version: int,
    capacity: int | None = None,
    opens_at: str | None = None,
    closes_at: str | None = None,
    status: str | None = None,
    reason: str | None = None,
) -> ScheduleRound:
    """后台修改档期容量与时间窗口。

    容量不得低于已占用数量（``reserved_count + committed_count``），
    数据库 CHECK 也会再次拒绝越界写入（05 文档）。
    """
    row = repo.get_round(session, round_id=round_id)
    if row is None:
        raise AppError("RESOURCE_NOT_FOUND")
    if version != row.version:
        raise AppError("VERSION_CONFLICT")
    occupied = int(row.reserved_count) + int(row.committed_count)
    before = {
        "capacity": row.capacity,
        "opens_at": row.opens_at,
        "closes_at": row.closes_at,
        "status": row.status,
    }
    if capacity is not None:
        if capacity < occupied:
            raise AppError(
                "STATE_CONFLICT",
                f"容量不能低于已占用数量（{occupied}）。",
                fields={"capacity": "低于已占用数量"},
            )
        row.capacity = capacity
    if opens_at is not None:
        parsed = _parse_moment(opens_at)
        if parsed is None:
            raise AppError("VALIDATION_ERROR", fields={"opens_at": "时间格式不正确"})
        row.opens_at = parsed
    if closes_at is not None:
        parsed = _parse_moment(closes_at)
        if parsed is None:
            raise AppError("VALIDATION_ERROR", fields={"closes_at": "时间格式不正确"})
        row.closes_at = parsed
    if status is not None:
        row.status = status
    if row.opens_at is not None and row.closes_at is not None and row.opens_at >= row.closes_at:
        raise AppError("VALIDATION_ERROR", "开放时间必须早于截止时间。")
    row.version += 1
    row.updated_at = now_ms()
    audit.record(
        session,
        actor_type="staff",
        actor_id=actor_id,
        action="schedule_round.update",
        resource_type="schedule_round",
        resource_id=row.id,
        before=before,
        after={
            "capacity": row.capacity,
            "opens_at": row.opens_at,
            "closes_at": row.closes_at,
            "status": row.status,
        },
        reason=reason,
    )
    session.flush()
    return row


def approve_customer_change(
    session: Session, *, change_request_id: str, actor_id: str | None, reason: str | None = None
) -> ChangeRequest:
    """员工通过客户提出的变更单：转 ``ACCEPTED``（客户已提出即视为已同意范围）。"""
    change = repo.get_change_any_project(session, change_request_id=change_request_id)
    if change is None:
        raise AppError("RESOURCE_NOT_FOUND")
    if change.status == "ACCEPTED":
        return change
    if change.status != "SUBMITTED":
        raise AppError("STATE_CONFLICT", "该变更单已结束。")
    moment = now_ms()
    result = session.execute(
        sa.update(ChangeRequest)
        .where(
            ChangeRequest.id == change.id,
            ChangeRequest.version == change.version,
            ChangeRequest.status == "SUBMITTED",
        )
        .values(
            status="ACCEPTED",
            decided_by=actor_id,
            decided_at=moment,
            version=ChangeRequest.version + 1,
            updated_at=moment,
        )
    )
    if not affected_rows(result):
        raise AppError("STATE_CONFLICT", "该变更单状态已变化，请刷新后重试。")
    session.expire(change)
    refreshed = repo.get_change_any_project(session, change_request_id=change_request_id)
    assert refreshed is not None
    audit.record(
        session,
        actor_type="staff",
        actor_id=actor_id,
        action="change_request.approve",
        resource_type="change_request",
        resource_id=refreshed.id,
        after={"status": refreshed.status},
        reason=reason,
    )
    return refreshed


def reject_customer_change(
    session: Session, *, change_request_id: str, actor_id: str | None, reason: str
) -> ChangeRequest:
    """员工拒绝客户提出的变更单：状态转 ``REJECTED``，不改动已发生的账务。"""
    change = repo.get_change_any_project(session, change_request_id=change_request_id)
    if change is None:
        raise AppError("RESOURCE_NOT_FOUND")
    if change.status == "REJECTED":
        return change
    if change.status not in ("SUBMITTED", "ACCEPTED"):
        raise AppError("STATE_CONFLICT", "该变更单已结束。")
    moment = now_ms()
    result = session.execute(
        sa.update(ChangeRequest)
        .where(ChangeRequest.id == change.id, ChangeRequest.version == change.version)
        .values(
            status="REJECTED",
            decided_by=actor_id,
            decided_at=moment,
            reason=(reason or "")[:300] or None,
            version=ChangeRequest.version + 1,
            updated_at=moment,
        )
    )
    if not affected_rows(result):
        raise AppError("STATE_CONFLICT", "该变更单状态已变化，请刷新后重试。")
    session.expire(change)
    refreshed = repo.get_change_any_project(session, change_request_id=change_request_id)
    assert refreshed is not None
    audit.record(
        session,
        actor_type="staff",
        actor_id=actor_id,
        action="change_request.reject_by_staff",
        resource_type="change_request",
        resource_id=refreshed.id,
        after={"status": refreshed.status},
        reason=reason,
    )
    return refreshed


def propose_change(
    session: Session,
    *,
    project_id: str,
    actor_id: str | None,
    scope: str,
    description: str,
    delta_cents: int,
    terms_version: str,
    schedule_delta_days: int = 0,
    new_total_cents: int | None = None,
    reason: str | None = None,
    media_asset_ids: list[str] | None = None,
) -> ChangeRequest:
    """员工提出变更单：明确新金额、延期与条款版本（等客户接受后才生成补款应付项）。"""
    project = repo.get_project(session, project_id)
    if project is None:
        raise AppError("RESOURCE_NOT_FOUND")
    if delta_cents < 0:
        # 减项通过退款或减少未付款项表达，不在这里填负数（07 文档）。
        raise AppError("VALIDATION_ERROR", fields={"delta_cents": "必须为非负数"})
    change = ChangeRequest(
        id=new_id("change"),
        project_id=project.id,
        version=repo.max_change_version(session, project_id=project.id) + 1,
        scope=scope,
        description=description,
        reason=reason,
        delta_cents=delta_cents,
        new_total_cents=new_total_cents
        if new_total_cents is not None
        else project.total_cents + delta_cents,
        currency=project.currency,
        schedule_delta_days=schedule_delta_days,
        terms_version=terms_version,
        status="SUBMITTED",
        requested_by=actor_id,
        requested_by_type="staff",
        media_asset_ids=list(media_asset_ids or []),
    )
    session.add(change)
    session.flush()
    audit.record(
        session,
        actor_type="staff",
        actor_id=actor_id,
        action="change_request.propose",
        resource_type="change_request",
        resource_id=change.id,
        after={
            "project_id": project.id,
            "version": change.version,
            "delta_cents": delta_cents,
            "terms_version": terms_version,
        },
        reason=reason,
    )
    return change


def accept_change_request(
    session: Session, *, user_id: str, change_request_id: str, project_id: str | None = None
) -> ChangeRequest:
    """客户接受变更单：条件更新保证已接受版本不可覆盖，随后生成单独补款应付项。"""
    project = None
    if project_id is not None:
        project = repo.get_project_for_user(session, project_id=project_id, user_id=user_id)
    if project is None:
        raise AppError("RESOURCE_NOT_FOUND")
    change = repo.get_change(session, change_request_id=change_request_id, project_id=project.id)
    if change is None:
        raise AppError("RESOURCE_NOT_FOUND")
    if change.status == "ACCEPTED":
        return change
    if change.status != "SUBMITTED":
        raise AppError("STATE_CONFLICT", "该变更单已结束，不能再次接受。")
    if not change.terms_version:
        raise AppError("STATE_CONFLICT", "该变更单尚未确定条款版本，请联系工作室。")

    moment = now_ms()
    result = session.execute(
        sa.update(ChangeRequest)
        .where(
            ChangeRequest.id == change.id,
            ChangeRequest.version == change.version,
            ChangeRequest.status == "SUBMITTED",
        )
        .values(
            status="ACCEPTED",
            accepted_at=moment,
            decided_by=user_id,
            decided_at=moment,
            version=ChangeRequest.version + 1,
            updated_at=moment,
        )
    )
    if not affected_rows(result):
        raise AppError("STATE_CONFLICT", "该变更单状态已变化，请刷新后重试。")
    session.expire(change)
    change = repo.get_change(session, change_request_id=change_request_id, project_id=project.id)
    assert change is not None

    commission_ports.record_terms_consent(
        session,
        user_id=user_id,
        version=change.terms_version,
        quote_id=change.id,
        content_hash=_snapshot_digest(
            change_request_id=change.id,
            version=change.version,
            delta_cents=change.delta_cents,
            terms_version=change.terms_version,
        ),
        evidence={"project_id": project.id, "order_id": project.order_id, "delta_cents": change.delta_cents},
    )

    outcome: str | None = None
    if change.delta_cents > 0 and project.order_id:
        outcome = commission_ports.create_change_installment(
            session,
            order_id=project.order_id,
            project_id=project.id,
            change_request_id=change.id,
            amount_cents=change.delta_cents,
            currency=change.currency,
            terms_version=change.terms_version,
            label=f"变更补款：{change.description[:40] or change.scope}",
            idempotency_key=f"change:{change.id}",
        )
    if outcome is None:
        # 商务端口未就绪或本次变更不涉及金额：只落变更单并登记事件，绝不静默加价。
        enqueue(
            session,
            event_type="order.change_accepted",
            aggregate_id=change.id,
            payload={
                "project_id": project.id,
                "order_id": project.order_id,
                "change_request_id": change.id,
                "amount_cents": change.delta_cents,
                "currency": change.currency,
                "terms_version": change.terms_version,
            },
        )
    else:
        change.installment_id = outcome
    if change.schedule_delta_days:
        project.estimated_delivery = add_days_ms(
            change.schedule_delta_days, project.estimated_delivery or moment
        )
        project.version += 1
        project.updated_at = moment
    session.flush()
    audit.record(
        session,
        actor_type="customer",
        actor_id=user_id,
        action="change_request.accept",
        resource_type="change_request",
        resource_id=change.id,
        after={
            "project_id": project.id,
            "delta_cents": change.delta_cents,
            "installment_id": change.installment_id,
        },
    )
    return change


# ------------------------------------------------------------------ DTO 组装
def inquiry_summary(inquiry: Inquiry) -> InquirySummary:
    return InquirySummary(
        id=inquiry.id,
        status=inquiry.status,
        character_name=inquiry.character_name,
        species=inquiry.species,
        series_id=inquiry.series_id,
        suit_type=inquiry.suit_type,
        current_revision=inquiry.current_revision,
        version=inquiry.version,
        guardian_required=bool(inquiry.guardian_required),
        submitted_at=iso(inquiry.submitted_at),
        created_at=iso(inquiry.created_at) or "",
        updated_at=iso(inquiry.updated_at) or "",
    )


def inquiry_detail_dto(session: Session, inquiry: Inquiry) -> InquiryDetail:
    revisions = [
        InquiryRevisionItem(
            revision=row.revision,
            author_type=row.author_type,
            change_note=row.change_note,
            submitted_at=iso(row.submitted_at),
            created_at=iso(row.created_at) or "",
        )
        for row in repo.list_revisions(session, inquiry_id=inquiry.id, limit=10)
    ]
    current_quote = repo.current_published_quote(session, inquiry_id=inquiry.id)
    project = repo.active_project_for_inquiry(session, inquiry_id=inquiry.id)
    return InquiryDetail(
        **inquiry_summary(inquiry).model_dump(),
        requirements=inquiry.requirements,
        design_asset_ids=list(inquiry.design_asset_ids or []),
        budget_min_cents=inquiry.budget_min_cents,
        budget_max_cents=inquiry.budget_max_cents,
        target_date=inquiry.target_date,
        delivery_region=inquiry.delivery_region,
        contact_preference=inquiry.contact_preference,
        rights_declaration=bool(inquiry.rights_declaration),
        needs_info_note=inquiry.needs_info_note,
        reject_reason=inquiry.reject_reason,
        accepted_quote_id=inquiry.accepted_quote_id,
        accepted_at=iso(inquiry.accepted_at),
        withdrawn_at=iso(inquiry.withdrawn_at),
        application_id=inquiry.application_id,
        revisions=revisions,
        current_quote_version=current_quote.quote_version if current_quote else None,
        project_id=project.id if project else None,
    )


def quote_dto(session: Session, quote: Quote) -> QuoteOut:
    moment = now_ms()
    items = [
        QuoteLineItemOut(
            label=row.label,
            quantity=row.quantity,
            unit_price_cents=row.unit_price_cents,
            amount_cents=row.amount_cents,
            note=row.note,
        )
        for row in repo.list_quote_items(session, quote_id=quote.id)
    ]
    return QuoteOut(
        id=quote.id,
        inquiry_id=quote.inquiry_id,
        quote_version=quote.quote_version,
        status=quote.status,
        currency=quote.currency,
        line_items=items,
        total_cents=quote.total_cents,
        prepayment_cents=quote.prepayment_cents,
        balance_cents=quote.balance_cents,
        expires_at=iso(quote.expires_at) or "",
        estimated_start=iso(quote.estimated_start),
        estimated_delivery=iso(quote.estimated_delivery),
        included_revisions=quote.included_revisions,
        exclusions=list(quote.exclusions or []),
        terms_version=quote.terms_version,
        issuer_id=quote.issuer_id,
        note=quote.note,
        published_at=iso(quote.published_at),
        accepted_at=iso(quote.accepted_at),
        order_id=quote.order_id,
        is_current=quote.status == "PUBLISHED" and quote.expires_at > moment,
        is_expired=quote.status == "EXPIRED" or quote.expires_at <= moment,
    )


def measurement_dto(row: Measurement, *, include_payload: bool) -> MeasurementOut:
    return MeasurementOut(
        id=row.id,
        project_id=row.project_id,
        version=row.version,
        unit_system=row.unit_system,
        note=row.note,
        flagged_fields=list(row.flagged_fields or []),
        measurements=load_measurements(row) if include_payload else None,
        created_at=iso(row.created_at) or "",
    )


def stage_dto(row: ProductionStage) -> ProductionStageOut:
    return ProductionStageOut(
        id=row.id,
        project_id=row.project_id,
        stage_code=row.stage_code,
        revision=row.revision,
        status=row.status,
        title=row.title,
        description=row.description,
        owner_id=row.owner_id,
        started_at=iso(row.started_at),
        estimated_at=iso(row.estimated_at),
        finished_at=iso(row.finished_at),
        needs_customer_confirmation=bool(row.needs_customer_confirmation),
        confirmed_revision=row.confirmed_revision,
        approved_by=row.approved_by,
        approved_at=iso(row.approved_at),
        media_asset_ids=list(row.media_asset_ids or []),
        delay_reason=row.delay_reason,
        version=row.version,
        created_at=iso(row.created_at) or "",
        updated_at=iso(row.updated_at) or "",
    )


def change_dto(row: ChangeRequest) -> ChangeRequestOut:
    return ChangeRequestOut(
        id=row.id,
        project_id=row.project_id,
        version=row.version,
        scope=row.scope,
        description=row.description,
        reason=row.reason,
        delta_cents=row.delta_cents,
        new_total_cents=row.new_total_cents,
        currency=row.currency,
        schedule_delta_days=row.schedule_delta_days,
        terms_version=row.terms_version,
        status=row.status,
        installment_id=row.installment_id,
        decided_at=iso(row.decided_at),
        accepted_at=iso(row.accepted_at),
        created_at=iso(row.created_at) or "",
    )


def project_dto(session: Session, project: CommissionProject, *, include_private: bool) -> CommissionProjectOut:
    stages = [stage_dto(row) for row in repo.list_stages(session, project_id=project.id)]
    measurements = [
        measurement_dto(row, include_payload=include_private)
        for row in repo.list_measurements(session, project_id=project.id)
    ]
    changes = [change_dto(row) for row in repo.list_changes(session, project_id=project.id)]
    return CommissionProjectOut(
        id=project.id,
        order_id=project.order_id,
        inquiry_id=project.inquiry_id,
        quote_id=project.quote_id,
        status=project.status,
        current_stage_code=project.current_stage_code,
        currency=project.currency,
        total_cents=project.total_cents,
        prepayment_cents=project.prepayment_cents,
        terms_version=project.terms_version,
        maker_id=project.maker_id,
        estimated_start=iso(project.estimated_start),
        estimated_delivery=iso(project.estimated_delivery),
        balance_due_at=iso(project.balance_due_at),
        started_at=iso(project.started_at),
        shipped_at=iso(project.shipped_at),
        completed_at=iso(project.completed_at),
        version=project.version,
        created_at=iso(project.created_at) or "",
        updated_at=iso(project.updated_at) or "",
        stages=stages,
        measurements=measurements,
        change_requests=changes,
    )


def round_public_dto(row: ScheduleRound, *, moment: int | None = None) -> ScheduleRoundPublic:
    """公开排期：只给匿名余量摘要，绝不含申请人身份、联系方式或订单号。"""
    now = moment if moment is not None else now_ms()
    remaining = max(0, row.capacity - row.reserved_count - row.committed_count)
    if remaining <= 0:
        bucket = "full"
    elif remaining <= 2:
        bucket = "limited"
    else:
        bucket = "plenty"
    accepting = (
        row.status == "OPEN"
        and (row.opens_at is None or now >= row.opens_at)
        and (row.closes_at is None or now < row.closes_at)
    )
    return ScheduleRoundPublic(
        id=row.id,
        title=row.title,
        mode=row.mode,
        status=row.status,
        stage_label=row.stage_label,
        timezone=row.timezone,
        start_at=iso(row.start_at),
        end_at=iso(row.end_at),
        opens_at=iso(row.opens_at),
        closes_at=iso(row.closes_at),
        accepting=accepting,
        remaining_bucket=bucket if accepting else "closed",
    )


def public_schedule_summary(session: Session, *, moment: int | None = None) -> dict[str, Any] | None:
    """首页与导航用的档期摘要。

    返回结构对应前端 ``ScheduleSummary``：
    ``{id,title,mode,status,start_at,end_at,capacity,remaining,timezone}``。
    ``status`` 为 ``open`` / ``upcoming`` / ``closed``；``remaining`` 只给数量，
    不暴露任何申请人身份（07 文档：公开排期只展示匿名余量与阶段）。
    """
    now = moment if moment is not None else now_ms()
    rows, _next_cursor, _has_more = rounds_page(session, limit=50, offset=0)
    if not rows:
        return None

    def to_summary(row: ScheduleRound, status: str) -> dict[str, Any]:
        remaining = max(0, row.capacity - row.reserved_count - row.committed_count)
        return {
            "id": row.id,
            "title": row.title,
            "mode": row.mode,
            "status": status,
            "start_at": iso(row.start_at) or "",
            "end_at": iso(row.end_at) or "",
            "capacity": int(row.capacity),
            "remaining": remaining,
            "timezone": row.timezone,
        }

    opened = [
        row
        for row in rows
        if row.status == "OPEN"
        and (row.opens_at is None or now >= row.opens_at)
        and (row.closes_at is None or now < row.closes_at)
    ]
    if opened:
        # 多轮同时开放时取截止时间最近的一轮，避免首页展示已过期信息。
        opened.sort(key=lambda row: row.closes_at or row.end_at)
        return to_summary(opened[0], "open")

    upcoming = [
        row
        for row in rows
        if (row.opens_at is not None and row.opens_at > now) or (row.start_at > now and row.status != "CLOSED")
    ]
    if upcoming:
        upcoming.sort(key=lambda row: row.opens_at or row.start_at)
        return to_summary(upcoming[0], "upcoming")

    rows.sort(key=lambda row: row.end_at, reverse=True)
    return to_summary(rows[0], "closed")


def application_dto(row: ScheduleApplication) -> ScheduleApplicationOut:
    return ScheduleApplicationOut(
        id=row.id,
        round_id=row.round_id,
        inquiry_id=row.inquiry_id,
        status=row.status,
        rank_sequence=row.rank_sequence,
        note=row.note,
        decision_reason=row.decision_reason,
        decided_at=iso(row.decided_at),
        created_at=iso(row.created_at) or "",
    )


def accept_response_dto(order: CreatedOrder, project: CommissionProject, quote: Quote) -> QuoteAcceptResponse:
    """接受报价响应：返回订单、应付项与制作单 ID（重复请求返回同一订单）。"""
    return QuoteAcceptResponse(
        order_id=order.order_id,
        order_no=order.order_no,
        status=order.status,
        installment_id=order.installment_id,
        amount_cents=order.totals.total_cents,
        prepayment_cents=quote.prepayment_cents,
        currency=order.totals.currency,
        expires_at=iso(order.expires_at),
        quote_id=quote.id,
        quote_version=quote.quote_version,
        project_id=project.id,
        slot_reservation_id=(quote.contract_snapshot or {}).get("slot_reservation_id"),
    )


def _parse_moment(value: str | None) -> int | None:
    """解析前端传来的 ISO 8601 时间（``Z`` 结尾）为 UTC 毫秒。"""
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AppError("VALIDATION_ERROR", "时间格式不正确。", fields={"time": "需要 ISO 8601"}) from exc
    return to_ms(parsed)
