"""委托模块运营后台路由。

覆盖分派询价、补件、发布报价版本、拒绝、档期与名额、制作节点与变更单。

约束（07/03 文档）：
- 报价必须有版本、有效期、明细、包含项与例外；发布新版把旧版设为 SUPERSEDED；
- 后台不得代替客户确认阶段、不得伪造客户同意；
- 已成功的付款与旧合同快照不得被后台修改；
- 后台响应不下发私密附件直链（只给素材编号，前端按需走 ``/media/{id}/access``）；
- 参考站合同条款不得复制。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import Field

from app.api.dependencies import ContextDep, CsrfDep, PrincipalDep, RequestContext, require_permission
from app.modules.commissions import repository as repo
from app.modules.commissions import service as svc
from app.modules.commissions.models import STAGE_STATUSES, CommissionProject
from app.modules.commissions.schemas import (
    ContractVoidInput,
    InquirySupplementInput,
    QuotePublishInput,
    ScheduleRoundInput,
)
from app.modules.identity import ports as identity_ports
from app.modules.shared.schemas import Page, StrictDto
from app.platform import audit
from app.platform.errors import AppError
from app.platform.ids import iso

admin_router = APIRouter(tags=["admin-commissions"])


# ------------------------------------------------------------------ 后台输入
class InquiryAssignInput(StrictDto):
    """分派询价给某位员工。``assignee`` 可填员工登录名或员工 ID；留空表示退回未分派。"""

    assignee: Annotated[str | None, Field(max_length=48)] = None
    assignee_id: Annotated[str | None, Field(max_length=48)] = None
    note: Annotated[str | None, Field(max_length=300)] = None

    def reference(self) -> str | None:
        return self.assignee_id or self.assignee


class InquiryReasonInput(StrictDto):
    """需要说明原因的后台动作（补件、拒绝）。"""

    reason: Annotated[str, Field(min_length=2, max_length=500)]


class ScheduleRoundPatchInput(StrictDto):
    """修改档期容量与时间窗口。容量不得低于已占用数量。"""

    version: int = Field(ge=1)
    capacity: Annotated[int | None, Field(ge=0, le=10_000)] = None
    opens_at: str | None = None
    closes_at: str | None = None
    status: Annotated[str | None, Field(max_length=20)] = None
    reason: Annotated[str | None, Field(max_length=300)] = None


class ApplicationDecisionInput(StrictDto):
    """普通申请审批。通过后生成排序与预留资格（C11 普通申请模式）。"""

    decision: Annotated[str, Field(min_length=5, max_length=20)]
    reason: Annotated[str, Field(min_length=2, max_length=400)]
    rank_sequence: Annotated[int | None, Field(ge=1)] = None


class ProjectPatchInput(StrictDto):
    version: int = Field(ge=1)
    maker_id: Annotated[str | None, Field(max_length=48)] = None
    status: Annotated[str | None, Field(max_length=30)] = None
    estimated_start: str | None = None
    estimated_delivery: str | None = None
    reason: Annotated[str | None, Field(max_length=300)] = None


class StageUpsertInput(StrictDto):
    """新增/更新制作节点。图片只登记素材编号，不下发直链。"""

    stage_code: Annotated[str, Field(min_length=1, max_length=40)]
    title: Annotated[str | None, Field(max_length=120)] = None
    description: Annotated[str | None, Field(max_length=2000)] = None
    status: Annotated[str | None, Field(max_length=20)] = None
    requires_confirmation: Annotated[bool, Field()] = False
    media_asset_ids: Annotated[list[str], Field(max_length=10)] = []
    estimated_at: str | None = None
    reason: Annotated[str | None, Field(max_length=300)] = None


class ChangeProposeInput(StrictDto):
    """员工提出变更单：明确新金额、延期与条款版本，等客户接受后才生成补款。"""

    scope: Annotated[str, Field(min_length=1, max_length=30)] = "other"
    description: Annotated[str, Field(min_length=2, max_length=1000)]
    delta_cents: Annotated[int, Field(ge=0, le=100_000_000)]
    terms_version: Annotated[str, Field(min_length=1, max_length=40)]
    schedule_delta_days: Annotated[int, Field(ge=0, le=365)] = 0
    new_total_cents: Annotated[int | None, Field(ge=0)] = None
    reason: Annotated[str | None, Field(max_length=300)] = None
    media_asset_ids: Annotated[list[str], Field(max_length=10)] = []


class ChangeDecisionInput(StrictDto):
    """员工对客户提出的变更单做决定（通过 / 拒绝）。"""

    decision: Annotated[str, Field(min_length=7, max_length=20)]
    reason: Annotated[str, Field(min_length=2, max_length=400)]


class StagePatchInput(StrictDto):
    """按节点编号更新制作节点。后台只能推进状态与说明，不能代替客户确认。"""

    version: int = Field(ge=1)
    status: Annotated[str | None, Field(max_length=20)] = None
    title: Annotated[str | None, Field(max_length=120)] = None
    note: Annotated[str | None, Field(max_length=2000)] = None
    requires_confirmation: Annotated[bool | None, Field()] = None
    media_asset_ids: Annotated[list[str] | None, Field(max_length=10)] = None
    reason: Annotated[str | None, Field(max_length=300)] = None


class AlbumLinkInput(StrictDto):
    """私密相册登记：只接受已扫描完成的素材编号，不下发签名直链（10/11 文档）。"""

    label: Annotated[str, Field(min_length=1, max_length=60)]
    asset_ids: Annotated[list[str], Field(min_length=1, max_length=30)]
    reason: Annotated[str | None, Field(max_length=300)] = None


# ------------------------------------------------------------------ 询价
@admin_router.get(
    "/inquiries", response_model=dict, dependencies=[Depends(require_permission("quote.read"))]
)
def list_inquiries(
    ctx: ContextDep,
    status: str | None = None,
    assignee_id: Annotated[str | None, Query(max_length=48)] = None,
    q: Annotated[str | None, Query(max_length=120)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    """询价列表。联系方式默认脱敏；不返回量体明细。"""
    rows, total = repo.list_inquiries_admin(
        ctx.session,
        status=status,
        assignee_id=assignee_id,
        query=q,
        limit=limit,
        offset=(page - 1) * limit,
    )
    return {
        "items": [svc.inquiry_summary(row).model_dump() for row in rows],
        "total": total,
        "page": page,
        "limit": limit,
        "has_more": page * limit < total,
        "next_cursor": None,
    }


@admin_router.get(
    "/inquiries/{inquiry_id}",
    response_model=dict,
    dependencies=[Depends(require_permission("quote.read"))],
)
def inquiry_detail(inquiry_id: str, ctx: ContextDep) -> dict[str, Any]:
    inquiry = repo.get_inquiry_for_staff(ctx.session, inquiry_id=inquiry_id)
    if inquiry is None:
        raise AppError("RESOURCE_NOT_FOUND")
    payload = svc.inquiry_detail_dto(ctx.session, inquiry).model_dump()
    # 只给素材编号，不下发签名直链；员工按需调用 /media/{id}/access。
    from app.modules.media.ports import assets_for

    payload["design_asset_ids"] = [
        asset.id
        for asset in assets_for(
            ctx.session, resource_type="inquiry", resource_id=inquiry.id, role="design"
        )
    ]
    payload["revisions"] = [
        {
            "revision": row.revision,
            "author_type": row.author_type,
            "change_note": row.change_note,
            "submitted_at": iso(row.submitted_at),
        }
        for row in repo.list_revisions(ctx.session, inquiry_id=inquiry.id)
    ]
    payload["notice"] = "设定图与量体资料属高敏感度内部数据，按最小必要范围查看。"
    return payload


@admin_router.post(
    "/inquiries/{inquiry_id}/assign",
    response_model=dict,
    dependencies=[Depends(require_permission("quote.review"))],
)
def assign_inquiry(
    inquiry_id: str,
    payload: InquiryAssignInput,
    ctx: ContextDep,
    _csrf: CsrfDep,
    principal: PrincipalDep,
) -> dict[str, Any]:
    """分派询价。接受员工登录名或员工 ID；找不到有效员工时返回字段错误，不静默留空。"""
    reference = payload.reference()
    assignee_id: str | None = None
    if reference:
        assignee_id = identity_ports.resolve_active_staff(ctx.session, reference)
        if assignee_id is None:
            raise AppError(
                "VALIDATION_ERROR",
                "找不到可用的员工账号。",
                fields={"assignee": "登录名或员工 ID 无效"},
            )
    inquiry = svc.assign_inquiry(
        ctx.session,
        inquiry_id=inquiry_id,
        assignee_id=assignee_id,
        actor_id=principal.require_staff(),
        note=payload.note,
    )
    return svc.inquiry_summary(inquiry).model_dump()


@admin_router.post(
    "/inquiries/{inquiry_id}/needs-info",
    response_model=dict,
    dependencies=[Depends(require_permission("quote.review"))],
)
def request_more_info(
    inquiry_id: str,
    payload: InquiryReasonInput,
    ctx: ContextDep,
    _csrf: CsrfDep,
    principal: PrincipalDep,
) -> dict[str, Any]:
    """要求补件：资料不足期间不会发布可支付报价（07 文档）。"""
    inquiry = svc.mark_needs_info(
        ctx.session, inquiry_id=inquiry_id, actor_id=principal.id, reason=payload.reason
    )
    return svc.inquiry_summary(inquiry).model_dump()


@admin_router.post(
    "/inquiries/{inquiry_id}/supplements",
    response_model=dict,
    dependencies=[Depends(require_permission("quote.review"))],
)
def add_staff_supplement(
    inquiry_id: str,
    payload: InquirySupplementInput,
    ctx: ContextDep,
    _csrf: CsrfDep,
    principal: PrincipalDep,
) -> dict[str, Any]:
    """员工补件：产生新版本，不覆盖客户已提交的内容。"""
    inquiry = svc.add_supplement(
        ctx.session,
        user_id=None,
        inquiry_id=inquiry_id,
        payload=payload,
        actor_id=principal.id,
    )
    return svc.inquiry_summary(inquiry).model_dump()


@admin_router.post(
    "/inquiries/{inquiry_id}/reject",
    response_model=dict,
    dependencies=[Depends(require_permission("quote.review"))],
)
def reject_inquiry(
    inquiry_id: str,
    payload: InquiryReasonInput,
    ctx: ContextDep,
    _csrf: CsrfDep,
    principal: PrincipalDep,
) -> dict[str, Any]:
    """不承接并说明可修改的原因，不能只给「失败」。"""
    inquiry = svc.reject_inquiry(
        ctx.session, inquiry_id=inquiry_id, actor_id=principal.id, reason=payload.reason
    )
    return svc.inquiry_summary(inquiry).model_dump()


def _require_staff_inquiry(ctx: ContextDep, inquiry_id: str) -> Any:
    inquiry = repo.get_inquiry_for_staff(ctx.session, inquiry_id=inquiry_id)
    if inquiry is None:
        raise AppError("RESOURCE_NOT_FOUND")
    return inquiry


# ------------------------------------------------------------------ 报价
@admin_router.get(
    "/quotes", response_model=dict, dependencies=[Depends(require_permission("quote.read"))]
)
def list_quotes(
    ctx: ContextDep,
    status: str | None = None,
    assignee_id: Annotated[str | None, Query(max_length=48)] = None,
    q: Annotated[str | None, Query(max_length=120)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    """询价与报价工作台列表（03 文档 ``/admin/quotes``）。

    行以**询价**为单位（``id`` 即询价编号），附带当前报价版本、金额与有效期，
    便于同一页完成分派、补件、版本报价与拒绝；后台不能就地改价，
    金额始终来自已保存的报价版本。
    """
    rows, total = repo.list_inquiries_admin(
        ctx.session,
        status=status,
        assignee_id=assignee_id,
        query=q,
        limit=limit,
        offset=(page - 1) * limit,
    )
    items = []
    for inquiry in rows:
        quote = repo.current_published_quote(ctx.session, inquiry_id=inquiry.id)
        if quote is None:
            quote = repo.latest_quote(ctx.session, inquiry_id=inquiry.id)
        items.append(
            {
                **_quote_workbench_row(inquiry, quote),
                # C30：首响承诺与逾期状态，运营在工作台直接看到「哪些已逾期」。
                **svc.inquiry_sla(ctx.session, inquiry),
                "submitted_at": iso(inquiry.submitted_at),
            }
        )
    return {
        "items": items,
        "total": total,
        "page": page,
        "limit": limit,
        "has_more": page * limit < total,
        "next_cursor": None,
    }


def _quote_workbench_row(inquiry: Any, quote: Any) -> dict[str, Any]:
    return {
        "id": inquiry.id,
        "inquiry_id": inquiry.id,
        "status": inquiry.status,
        "character_name": inquiry.character_name,
        "species": inquiry.species,
        "assignee": inquiry.assignee_id,
        "version": inquiry.version,
        "quote_id": getattr(quote, "id", None),
        "quote_version": getattr(quote, "quote_version", None),
        "amount_cents": getattr(quote, "total_cents", None),
        "currency": getattr(quote, "currency", None),
        "valid_until": iso(getattr(quote, "expires_at", None)),
        "rejection_reason": inquiry.reject_reason,
        "needs_info_note": inquiry.needs_info_note,
        "updated_at": iso(inquiry.updated_at),
    }


@admin_router.get(
    "/quotes/{quote_id}", response_model=dict, dependencies=[Depends(require_permission("quote.read"))]
)
def quote_detail(quote_id: str, ctx: ContextDep) -> dict[str, Any]:
    quote = repo.get_quote(ctx.session, quote_id=quote_id)
    if quote is None:
        raise AppError("RESOURCE_NOT_FOUND")
    return svc.quote_dto(ctx.session, quote).model_dump()


@admin_router.post(
    "/inquiries/{inquiry_id}/quotes",
    response_model=dict,
    status_code=201,
    dependencies=[Depends(require_permission("quote.review"))],
)
def publish_quote(
    inquiry_id: str,
    payload: QuotePublishInput,
    ctx: ContextDep,
    _csrf: CsrfDep,
    principal: PrincipalDep,
) -> dict[str, Any]:
    """发布报价版本。

    - 金额由服务端按明细重算，客户端不能直接提交总额；
    - 发布新版把旧版设为 SUPERSEDED，历史版本与金额保留供复核；
    - 有效期、明细、包含项与例外必须完整，否则客户无法判断。
    """
    quote = svc.publish_quote(
        ctx.session, inquiry_id=inquiry_id, actor_id=principal.require_staff(), payload=payload
    )
    return svc.quote_dto(ctx.session, quote).model_dump()


# ------------------------------------------------------------------ 档期与名额
@admin_router.get(
    "/schedule-rounds",
    response_model=dict,
    dependencies=[Depends(require_permission("schedule.manage"))],
)
def list_rounds(
    ctx: ContextDep,
    status: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    """档期列表：容量、已用、时间窗口与时区。"""
    rows, total = repo.list_rounds_admin(
        ctx.session, status=status, limit=limit, offset=(page - 1) * limit
    )
    return {
        "items": [
            {
                **svc.round_public_dto(row).model_dump(),
                "capacity": int(row.capacity),
                "reserved_count": int(row.reserved_count),
                "committed_count": int(row.committed_count),
                "used": int(row.reserved_count) + int(row.committed_count),
            }
            for row in rows
        ],
        "total": total,
        "page": page,
        "limit": limit,
        "has_more": page * limit < total,
        "next_cursor": None,
    }


@admin_router.post(
    "/schedule-rounds",
    response_model=dict,
    status_code=201,
    dependencies=[Depends(require_permission("schedule.manage"))],
)
def create_round(
    payload: ScheduleRoundInput,
    ctx: ContextDep,
    _csrf: CsrfDep,
    principal: PrincipalDep,
) -> dict[str, Any]:
    row = svc.create_schedule_round(ctx.session, actor_id=principal.id, payload=payload)
    return svc.round_public_dto(row).model_dump()


@admin_router.patch(
    "/schedule-rounds/{round_id}",
    response_model=dict,
    dependencies=[Depends(require_permission("schedule.manage"))],
)
def update_round(
    round_id: str,
    payload: ScheduleRoundPatchInput,
    ctx: ContextDep,
    _csrf: CsrfDep,
    principal: PrincipalDep,
) -> dict[str, Any]:
    """修改档期。``reserved_count + committed_count <= capacity`` 由库约束保证。"""
    row = svc.update_schedule_round(
        ctx.session,
        round_id=round_id,
        actor_id=principal.id,
        version=payload.version,
        capacity=payload.capacity,
        opens_at=payload.opens_at,
        closes_at=payload.closes_at,
        status=payload.status,
        reason=payload.reason,
    )
    return svc.round_public_dto(row).model_dump()


@admin_router.get(
    "/schedule-applications",
    response_model=dict,
    dependencies=[Depends(require_permission("schedule.manage"))],
)
def list_schedule_applications(
    ctx: ContextDep,
    round_id: Annotated[str | None, Query(max_length=48)] = None,
    status: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    """普通申请与候补列表（公开排期只展示匿名编号与阶段）。"""
    rows, total = repo.list_applications_admin(
        ctx.session, round_id=round_id, status=status, limit=limit, offset=(page - 1) * limit
    )
    return {
        "items": [svc.application_dto(row).model_dump() for row in rows],
        "total": total,
        "page": page,
        "limit": limit,
        "has_more": page * limit < total,
        "next_cursor": None,
    }


@admin_router.get(
    "/schedule-rounds/{round_id}/applications",
    response_model=dict,
    dependencies=[Depends(require_permission("schedule.manage"))],
)
def list_round_applications(round_id: str, ctx: ContextDep) -> dict[str, Any]:
    rows = repo.list_applications_for_round(ctx.session, round_id=round_id)
    return {
        "items": [svc.application_dto(row).model_dump() for row in rows],
        "total": len(rows),
        "page": 1,
        "limit": len(rows) or 20,
        "has_more": False,
        "next_cursor": None,
    }


@admin_router.post(
    "/schedule-applications/{application_id}/decision",
    response_model=dict,
    dependencies=[Depends(require_permission("schedule.manage"))],
)
def decide_schedule_application(
    application_id: str,
    payload: ApplicationDecisionInput,
    ctx: ContextDep,
    _csrf: CsrfDep,
    principal: PrincipalDep,
) -> dict[str, Any]:
    """审批普通申请。通过后生成排序序号与预留资格（每人每轮最多 3 条，C11）。"""
    application = svc.decide_application(
        ctx.session,
        application_id=application_id,
        actor_id=principal.id,
        decision=payload.decision,
        reason=payload.reason,
        rank_sequence=payload.rank_sequence,
    )
    return svc.application_dto(application).model_dump()


def _authorized_project(ctx: RequestContext, project_id: str) -> CommissionProject:
    project = repo.get_project(ctx.session, project_id)
    if project is None or (
        not ctx.principal.has("schedule.manage") and project.maker_id != ctx.principal.id
    ):
        raise AppError("RESOURCE_NOT_FOUND")
    return project


# ------------------------------------------------------------------ 制作单
@admin_router.get(
    "/commission-projects",
    response_model=dict,
    dependencies=[Depends(require_permission("production.read"))],
)
def list_projects(
    ctx: ContextDep,
    status: str | None = None,
    maker_id: Annotated[str | None, Query(max_length=48)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    if not ctx.principal.has("schedule.manage"):
        maker_id = ctx.principal.id
    rows, total = repo.list_projects_admin(
        ctx.session, status=status, maker_id=maker_id, limit=limit, offset=(page - 1) * limit
    )
    return {
        "items": [svc.project_dto(ctx.session, row, include_private=False).model_dump() for row in rows],
        "total": total,
        "page": page,
        "limit": limit,
        "has_more": page * limit < total,
        "next_cursor": None,
    }


@admin_router.get(
    "/commission-projects/{project_id}",
    response_model=dict,
    dependencies=[Depends(require_permission("production.read"))],
)
def project_detail(project_id: str, ctx: ContextDep) -> dict[str, Any]:
    project = _authorized_project(ctx, project_id)
    payload = svc.project_dto(ctx.session, project, include_private=True).model_dump()
    payload["stages"] = [
        svc.stage_dto(row).model_dump() for row in repo.list_stages(ctx.session, project_id=project.id)
    ]
    payload["change_requests"] = [
        svc.change_dto(row).model_dump() for row in repo.list_changes(ctx.session, project_id=project.id)
    ]
    return payload


@admin_router.patch(
    "/commission-projects/{project_id}",
    response_model=dict,
    dependencies=[Depends(require_permission("production.write"))],
)
def update_project(
    project_id: str,
    payload: ProjectPatchInput,
    ctx: ContextDep,
    _csrf: CsrfDep,
    principal: PrincipalDep,
) -> dict[str, Any]:
    """分派装师或推进制作单状态。装师无权访问支付密钥与退款接口。"""
    project = _authorized_project(ctx, project_id)
    if payload.version != project.version:
        raise AppError("VERSION_CONFLICT")
    if payload.maker_id is not None:
        principal.require("schedule.manage")
        svc.assign_maker(
            ctx.session, project_id=project_id, maker_id=payload.maker_id, actor_id=principal.id
        )
    if payload.status is not None:
        svc.advance_project_stage(
            ctx.session, project_id=project_id, to_status=payload.status, actor_id=principal.id
        )
    if payload.estimated_start or payload.estimated_delivery:
        from app.platform.ids import now_ms

        if payload.estimated_start:
            project.estimated_start = _parse_moment(payload.estimated_start)
        if payload.estimated_delivery:
            project.estimated_delivery = _parse_moment(payload.estimated_delivery)
        project.version += 1
        project.updated_at = now_ms()
    audit.record(
        ctx.session,
        actor_type="staff",
        actor_id=principal.id,
        action="commission_project.update",
        resource_type="commission_project",
        resource_id=project_id,
        after=payload.model_dump(exclude_none=True),
        reason=payload.reason,
    )
    refreshed = repo.get_project(ctx.session, project_id)
    assert refreshed is not None
    return svc.project_dto(ctx.session, refreshed, include_private=True).model_dump()


@admin_router.post(
    "/commission-projects/{project_id}/stages",
    response_model=dict,
    dependencies=[Depends(require_permission("production.write"))],
)
def upsert_stage(
    project_id: str,
    payload: StageUpsertInput,
    ctx: ContextDep,
    _csrf: CsrfDep,
    principal: PrincipalDep,
) -> dict[str, Any]:
    """新增或更新制作节点。需要客户确认的节点会登记通知（不代替客户确认）。"""
    _authorized_project(ctx, project_id)
    stage = svc.upsert_production_stage(
        ctx.session,
        project_id=project_id,
        actor_id=principal.id,
        stage_code=payload.stage_code,
        title=payload.title,
        description=payload.description,
        status=payload.status,
        needs_customer_confirmation=payload.requires_confirmation,
        media_asset_ids=payload.media_asset_ids,
        estimated_at=_parse_moment(payload.estimated_at) if payload.estimated_at else None,
    )
    return svc.stage_dto(stage).model_dump()


@admin_router.patch(
    "/production-stages/{stage_id}",
    response_model=dict,
    dependencies=[Depends(require_permission("production.write"))],
)
def update_stage(
    stage_id: str,
    payload: StagePatchInput,
    ctx: ContextDep,
    _csrf: CsrfDep,
    principal: PrincipalDep,
) -> dict[str, Any]:
    """按节点编号更新状态与说明（03 文档：制作节点在制作单页维护）。

    与 ``POST /commission-managers`` 系列一样走对象授权：未分配给本人的制作单返回 404。
    内容变化会让旧确认失效并递增 ``revision``，等待客户按新版本重新确认。
    """
    stage = repo.get_stage(ctx.session, stage_id)
    if stage is None:
        raise AppError("RESOURCE_NOT_FOUND")
    project = _authorized_project(ctx, stage.project_id)
    if payload.version != stage.version:
        raise AppError("VERSION_CONFLICT")
    if payload.status is not None and payload.status not in STAGE_STATUSES:
        raise AppError(
            "VALIDATION_ERROR", "非法的节点状态。", fields={"status": "未登记的节点状态"}
        )
    updated = svc.upsert_production_stage(
        ctx.session,
        project_id=project.id,
        actor_id=principal.require_staff(),
        stage_code=stage.stage_code,
        title=payload.title,
        description=payload.note,
        status=payload.status,
        needs_customer_confirmation=payload.requires_confirmation,
        media_asset_ids=payload.media_asset_ids,
    )
    return svc.stage_dto(updated).model_dump()


@admin_router.post(
    "/commission-projects/{project_id}/album",
    response_model=dict,
    status_code=201,
    dependencies=[Depends(require_permission("production.write"))],
)
def link_private_album(
    project_id: str,
    payload: AlbumLinkInput,
    ctx: ContextDep,
    _csrf: CsrfDep,
    principal: PrincipalDep,
) -> dict[str, Any]:
    """登记私密相册素材。只接受已扫描完成的编号；响应不下发签名直链。"""
    from app.modules.media import ports as media_ports

    project = _authorized_project(ctx, project_id)
    linked = [
        media_ports.link_asset(
            ctx.session,
            asset_id=asset_id,
            resource_type="commission_project",
            resource_id=project.id,
            role="album",
        )
        for asset_id in payload.asset_ids
    ]
    audit.record(
        ctx.session,
        actor_type="staff",
        actor_id=principal.require_staff(),
        action="commission_project.album_link",
        resource_type="commission_project",
        resource_id=project.id,
        after={"label": payload.label, "asset_ids": list(payload.asset_ids)},
        reason=payload.reason,
    )
    return {
        "project_id": project.id,
        "label": payload.label,
        "asset_ids": [link.asset_id for link in linked],
        "notice": "相册只对客户本人与分配的装师开放，下载走 /media/{id}/access 的短期签名地址。",
    }


@admin_router.get(
    "/commission-projects/{project_id}/change-requests",
    response_model=dict,
    dependencies=[Depends(require_permission("production.read"))],
)
def list_change_requests(project_id: str, ctx: ContextDep) -> dict[str, Any]:
    _authorized_project(ctx, project_id)
    rows = repo.list_changes(ctx.session, project_id=project_id)
    return {
        "items": [svc.change_dto(row).model_dump() for row in rows],
        "total": len(rows),
        "page": 1,
        "limit": len(rows) or 20,
        "has_more": False,
        "next_cursor": None,
    }


@admin_router.post(
    "/commission-projects/{project_id}/changes",
    response_model=dict,
    status_code=201,
    dependencies=[Depends(require_permission("production.write"))],
)
def propose_change(
    project_id: str,
    payload: ChangeProposeInput,
    ctx: ContextDep,
    _csrf: CsrfDep,
    principal: PrincipalDep,
) -> dict[str, Any]:
    """提出变更单：新金额、延期与条款版本必须明确；客户接受后才生成补款应付项。"""
    _authorized_project(ctx, project_id)
    change = svc.propose_change(
        ctx.session,
        project_id=project_id,
        actor_id=principal.id,
        scope=payload.scope,
        description=payload.description,
        delta_cents=payload.delta_cents,
        terms_version=payload.terms_version,
        schedule_delta_days=payload.schedule_delta_days,
        new_total_cents=payload.new_total_cents,
        reason=payload.reason,
        media_asset_ids=payload.media_asset_ids,
    )
    return svc.change_dto(change).model_dump()


@admin_router.post(
    "/change-requests/{change_id}/decide",
    response_model=dict,
    dependencies=[Depends(require_permission("production.write"))],
)
def decide_change(
    change_id: str,
    payload: ChangeDecisionInput,
    ctx: ContextDep,
    _csrf: CsrfDep,
    principal: PrincipalDep,
) -> dict[str, Any]:
    """员工处理客户提出的变更单（通过或拒绝）。已接受的版本不可覆盖。"""
    change = repo.get_change_any_project(ctx.session, change_request_id=change_id)
    if change is None:
        raise AppError("RESOURCE_NOT_FOUND")
    _authorized_project(ctx, change.project_id)
    if payload.decision == "APPROVED":
        change = svc.approve_customer_change(
            ctx.session, change_request_id=change_id, actor_id=principal.id, reason=payload.reason
        )
    elif payload.decision == "REJECTED":
        change = svc.reject_customer_change(
            ctx.session, change_request_id=change_id, actor_id=principal.id, reason=payload.reason
        )
    else:
        raise AppError("VALIDATION_ERROR", fields={"decision": "只能是 APPROVED 或 REJECTED"})
    return svc.change_dto(change).model_dump()


@admin_router.post(
    "/commission-projects/{project_id}/contract/send",
    response_model=dict,
    dependencies=[Depends(require_permission("production.write"))],
)
def resend_contract(
    project_id: str,
    ctx: ContextDep,
    _csrf: CsrfDep,
    principal: PrincipalDep,
) -> dict[str, Any]:
    """重发合同签署（C07）：渠道故障或凭据补齐后由运营手动重发。

    重发**复用同一签署任务**，不会产生第二份合同；渠道未接入时如实返回未发送原因。
    """
    from app.modules.commissions import esign_service

    project = _authorized_project(ctx, project_id)
    envelope = esign_service.envelope_for_project(ctx.session, project_id=project.id)
    if envelope is None:
        raise AppError("RESOURCE_NOT_FOUND", "该制作单没有合同签署任务。")
    if envelope.status == "SIGNED":
        return esign_service.envelope_payload(ctx.session, envelope)
    esign_service.send_contract(ctx.session, envelope=envelope, actor_id=principal.id)
    return esign_service.envelope_payload(ctx.session, envelope)


@admin_router.post(
    "/commission-projects/{project_id}/contract/void",
    response_model=dict,
    dependencies=[Depends(require_permission("production.write"))],
)
def void_contract(
    project_id: str,
    payload: ContractVoidInput,
    ctx: ContextDep,
    _csrf: CsrfDep,
    principal: PrincipalDep,
) -> dict[str, Any]:
    """作废未签署的合同任务（报价撤回、客户取消）；已签署的合同不可作废。"""
    from app.modules.commissions import esign_service

    project = _authorized_project(ctx, project_id)
    envelope = esign_service.envelope_for_project(ctx.session, project_id=project.id)
    if envelope is None:
        raise AppError("RESOURCE_NOT_FOUND", "该制作单没有合同签署任务。")
    esign_service.void_envelope(
        ctx.session, envelope=envelope, reason=payload.reason, actor_id=principal.id
    )
    return esign_service.envelope_payload(ctx.session, envelope)


def _parse_moment(value: str | None) -> int | None:
    """把 ISO 字符串转为 UTC 毫秒。非法输入按缺失处理，由服务层使用默认值。"""
    if not value:
        return None
    from datetime import datetime

    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    from app.platform.ids import to_ms

    return to_ms(parsed)


__all__ = ["admin_router", "Page"]
