"""委托模块 HTTP 路由。

路径与 06 文档接口目录一致；端点在 ``/api/v1`` 下挂载，本文件内不带前缀。
写操作使用 ``CsrfDep`` 校验会话令牌，公开写操作另加 ``enforce_write_guard``；
所有按 ID 的读取都由仓储层同时带 ``user_id`` 过滤，非本人访问一律
``RESOURCE_NOT_FOUND``（不泄露对象是否存在）。

运营后台路由在同目录的 ``admin_router.py``，由 ``app/api/admin_routes.py`` 汇总挂载。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import JSONResponse

from app.api.dependencies import (
    ContextDep,
    CsrfDep,
    CustomerIdDep,
    PrincipalDep,
    enforce_write_guard,
)
from app.modules.commissions import esign_service
from app.modules.commissions import repository as repo
from app.modules.commissions import service as svc
from app.modules.commissions.admin_router import admin_router
from app.modules.commissions.schemas import (
    ChangeDecisionInput,
    ChangeRequestOut,
    CommissionProjectOut,
    EstimateRequestCreated,
    EstimateRequestInput,
    InquiryCreate,
    InquiryDetail,
    InquiryPatch,
    InquirySubmitResponse,
    InquirySummary,
    InquirySupplementInput,
    MeasurementInput,
    MeasurementOut,
    ProductionStageOut,
    QuoteAcceptInput,
    QuoteAcceptResponse,
    QuoteOut,
    ReassessmentInput,
    ScheduleApplicationInput,
    ScheduleApplicationOut,
    ScheduleRoundPublic,
    StageChangeInput,
    StageConfirmInput,
)
from app.modules.shared.schemas import Page
from app.platform import audit
from app.platform.errors import AppError
from app.platform.ids import iso

# 所有写操作统一校验 Origin/受信 Referer（10 文档：写请求不能仅靠 SameSite）；
# 需要登录的写操作另有 ``CsrfDep`` 校验会话 CSRF token。
public_router = APIRouter(tags=["commissions"], dependencies=[Depends(enforce_write_guard)])

IdempotencyHeader = Annotated[str | None, Header(alias="Idempotency-Key", max_length=120)]


async def _raw_body(request: Request) -> bytes:
    """回调原始字节：验签与留证都必须基于**未经解析**的原文。"""
    return await request.body()


RawBodyDep = Annotated[bytes, Depends(_raw_body)]


# ------------------------------------------------------------------ 询价
@public_router.get("/inquiries", response_model=Page[InquirySummary])
def list_inquiries(
    ctx: ContextDep,
    user_id: CustomerIdDep,
    status: Annotated[str | None, Query(max_length=20)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
) -> Page[InquirySummary]:
    """本人询价列表。草稿、处理中与历史记录都在这里。"""
    offset = svc.parse_cursor(cursor)
    rows = repo.list_inquiries(ctx.session, user_id=user_id, status=status, limit=limit + 1, offset=offset)
    items, next_cursor, has_more = svc.paginate(rows, limit=limit, offset=offset)
    return Page[InquirySummary](
        items=[svc.inquiry_summary(row) for row in items],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@public_router.post("/inquiries", response_model=InquiryDetail, status_code=201)
def create_inquiry(
    payload: InquiryCreate,
    ctx: ContextDep,
    _csrf: CsrfDep,
    user_id: CustomerIdDep,
    idempotency_key: IdempotencyHeader = None,
) -> InquiryDetail:
    """创建询价草稿。``Idempotency-Key`` 可选：重复提交返回同一条询价。"""
    inquiry = svc.create_inquiry(
        ctx.session, user_id=user_id, payload=payload, idempotency_key=idempotency_key
    )
    return svc.inquiry_detail_dto(ctx.session, inquiry)


@public_router.get("/inquiries/{inquiry_id}", response_model=InquiryDetail)
def get_inquiry(inquiry_id: str, ctx: ContextDep, user_id: CustomerIdDep) -> InquiryDetail:
    inquiry = svc.get_inquiry_detail(ctx.session, user_id=user_id, inquiry_id=inquiry_id)
    return svc.inquiry_detail_dto(ctx.session, inquiry)


@public_router.patch("/inquiries/{inquiry_id}", response_model=InquiryDetail)
def update_inquiry(
    inquiry_id: str, payload: InquiryPatch, ctx: ContextDep, _csrf: CsrfDep, user_id: CustomerIdDep
) -> InquiryDetail:
    """按版本编辑：版本不符返回 409；已提交询价追加版本，不覆盖工作人员已见内容。"""
    inquiry = svc.update_inquiry(ctx.session, user_id=user_id, inquiry_id=inquiry_id, patch=payload)
    return svc.inquiry_detail_dto(ctx.session, inquiry)


@public_router.post("/inquiries/{inquiry_id}/submit", response_model=InquirySubmitResponse)
def submit_inquiry(
    inquiry_id: str, ctx: ContextDep, _csrf: CsrfDep, user_id: CustomerIdDep
) -> InquirySubmitResponse:
    """提交询价：校验设定图与权利声明；重复提交返回同一对象。

    响应形状按 06 文档固定为 ``{inquiry_id,status,version,submitted_at}``：
    前端提交成功后要立刻按 ``inquiry_id`` 回读详情（过去这里返回详情对象，
    导致前端拿到的 ``inquiry_id`` 为 undefined，回读打到 ``/inquiries/undefined`` 并 404）。
    """
    inquiry = svc.submit_inquiry(ctx.session, user_id=user_id, inquiry_id=inquiry_id)
    return InquirySubmitResponse(
        inquiry_id=inquiry.id,
        status=inquiry.status,
        version=inquiry.version,
        submitted_at=iso(inquiry.submitted_at),
    )


@public_router.post("/inquiries/{inquiry_id}/withdraw", response_model=InquiryDetail)
def withdraw_inquiry(
    inquiry_id: str, ctx: ContextDep, _csrf: CsrfDep, user_id: CustomerIdDep
) -> InquiryDetail:
    inquiry = svc.withdraw_inquiry(ctx.session, user_id=user_id, inquiry_id=inquiry_id)
    return svc.inquiry_detail_dto(ctx.session, inquiry)


@public_router.post("/inquiries/{inquiry_id}/supplements", response_model=InquiryDetail)
def add_supplement(
    inquiry_id: str,
    payload: InquirySupplementInput,
    ctx: ContextDep,
    _csrf: CsrfDep,
    user_id: CustomerIdDep,
) -> InquiryDetail:
    """补件：产生新版本；报价后补件回到待评估。"""
    inquiry = svc.add_supplement(
        ctx.session, user_id=user_id, inquiry_id=inquiry_id, payload=payload
    )
    return svc.inquiry_detail_dto(ctx.session, inquiry)


@public_router.get("/inquiries/{inquiry_id}/quotes", response_model=Page[QuoteOut])
def list_inquiry_quotes(
    inquiry_id: str,
    ctx: ContextDep,
    user_id: CustomerIdDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
) -> Page[QuoteOut]:
    """本人询价的报价版本列表（含历史版本与被替代版本）。"""
    offset = svc.parse_cursor(cursor)
    quotes = svc.list_quotes(ctx.session, user_id=user_id, inquiry_id=inquiry_id)
    items, next_cursor, has_more = svc.paginate(quotes, limit=limit, offset=offset)
    return Page[QuoteOut](
        items=[svc.quote_dto(ctx.session, row) for row in items],
        next_cursor=next_cursor,
        has_more=has_more,
    )


# ------------------------------------------------------------------ 报价接受
@public_router.post("/quotes/{quote_id}/accept", response_model=QuoteAcceptResponse, status_code=201)
def accept_quote(
    quote_id: str,
    payload: QuoteAcceptInput,
    ctx: ContextDep,
    _csrf: CsrfDep,
    user_id: CustomerIdDep,
    idempotency_key: IdempotencyHeader = None,
) -> QuoteAcceptResponse:
    """接受报价并建立唯一委托订单。重复请求返回同一订单，不会重复占名额。"""
    quote = repo.get_quote_for_user(ctx.session, quote_id=quote_id, user_id=user_id)
    if quote is None:
        raise AppError("RESOURCE_NOT_FOUND")
    order, project = svc.accept_quote_for_user(
        ctx.session,
        user_id=user_id,
        quote_id=quote.id,
        quote_version=payload.quote_version,
        terms_version=payload.terms_version,
        accepted=payload.accepted,
        idempotency_key=idempotency_key,
    )
    return svc.accept_response_dto(order, project, quote)


# ------------------------------------------------------------------ 电子签（C07/C19）
@public_router.get("/inquiries/{inquiry_id}/contract", response_model=dict)
def inquiry_contract(
    inquiry_id: str,
    ctx: ContextDep,
    user_id: CustomerIdDep,
) -> dict[str, Any]:
    """本人询价对应合同的签署状态（含各签署人与待签角色）。

    只返回**话术与状态**，不返回渠道凭据；未接入电子签渠道时如实说明 `provider_configured=false`。
    """
    inquiry = repo.get_inquiry(ctx.session, inquiry_id=inquiry_id, user_id=user_id)
    if inquiry is None:
        raise AppError("RESOURCE_NOT_FOUND")
    envelope = esign_service.latest_envelope_for_inquiry(ctx.session, inquiry_id=inquiry.id)
    if envelope is None:
        return {
            "available": False,
            "reason": "NO_ENVELOPE",
            "note": "该询价尚无合同签署任务（报价接受后自动发起）。",
        }
    payload = esign_service.envelope_payload(ctx.session, envelope)
    payload["available"] = True
    return payload


@public_router.post("/contract-envelopes/{envelope_id}/refresh", response_model=dict)
def refresh_contract(
    envelope_id: str,
    ctx: ContextDep,
    _csrf: CsrfDep,
    user_id: CustomerIdDep,
) -> dict[str, Any]:
    """主动向电子签平台查单并刷新状态（成功只认平台，不认页面回调）。"""
    envelope = esign_service.get_envelope_for_user(
        ctx.session, envelope_id=envelope_id, user_id=user_id
    )
    if envelope is None:
        raise AppError("RESOURCE_NOT_FOUND")
    esign_service.refresh_envelope(ctx.session, envelope=envelope)
    return esign_service.envelope_payload(ctx.session, envelope)


@public_router.post("/esign-notifications/{provider}", include_in_schema=True)
def esign_notification(
    provider: str,
    request: Request,
    ctx: ContextDep,
    raw_body: RawBodyDep,
) -> Response:
    """电子签平台回调：验签通过才落状态；原始字节留证，未验签一律 403。

    与支付回调同一口径：**签署完成只认平台**（已验签回调或服务端查单），
    页面上的"我已签署"按钮不产生任何状态变化。
    """
    digest, _path = esign_service.persist_esign_event(raw_body)
    result = esign_service.apply_esign_notification(
        ctx.session,
        headers=dict(request.headers),
        raw_body=raw_body,
        provider=provider,
    )
    ctx.session.commit()
    accepted = bool(result.get("accepted"))
    status_code = 200 if accepted else (404 if result.get("status") == "PROVIDER_DISABLED" else 403)
    return JSONResponse(
        {"received": accepted, "status": result.get("status"), "reason": result.get("reason")},
        status_code=status_code,
        headers={"X-Esign-Status": str(result.get("status") or ""), "X-Esign-Digest": digest},
    )


# ------------------------------------------------------------------ 档期（公开）
@public_router.get("/schedule-rounds", response_model=Page[ScheduleRoundPublic])
def list_schedule_rounds(
    ctx: ContextDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
) -> Page[ScheduleRoundPublic]:
    """公开排期：只给匿名余量摘要、时区与开始/截止时间，不含任何申请人身份信息。"""
    offset = svc.parse_cursor(cursor)
    rows, next_cursor, has_more = svc.rounds_page(ctx.session, limit=limit, offset=offset)
    return Page[ScheduleRoundPublic](
        items=[svc.round_public_dto(row) for row in rows],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@public_router.post("/schedule-rounds/{round_id}/applications", response_model=ScheduleApplicationOut, status_code=201)
def apply_to_round(
    round_id: str,
    payload: ScheduleApplicationInput,
    ctx: ContextDep,
    _csrf: CsrfDep,
    user_id: CustomerIdDep,
    idempotency_key: IdempotencyHeader = None,
) -> ScheduleApplicationOut:
    """提交档期申请（本人 + 幂等）。普通申请先人工筛选，入选后接受报价时才占名额。"""
    application = svc.apply_to_round(
        ctx.session,
        user_id=user_id,
        round_id=round_id,
        payload=payload,
        idempotency_key=idempotency_key,
    )
    return svc.application_dto(application)


# ------------------------------------------------------------------ 制作单
@public_router.get("/commission-projects/{project_id}", response_model=CommissionProjectOut)
def get_commission_project(
    project_id: str, ctx: ContextDep, principal: PrincipalDep, response: Response
) -> CommissionProjectOut:
    """制作信息：本人或分配装师可读；其他主体返回 404。"""
    user_id = principal.id if principal.kind == "customer" else None
    maker_id = principal.id if principal.kind == "staff" else None
    project = svc.project_for_actor(
        ctx.session, project_id=project_id, user_id=user_id, maker_id=maker_id
    )
    response.headers["Cache-Control"] = "no-store"
    return svc.project_dto(ctx.session, project, include_private=True)


@public_router.post(
    "/commission-projects/{project_id}/measurements", response_model=MeasurementOut, status_code=201
)
def create_measurement(
    project_id: str,
    payload: MeasurementInput,
    ctx: ContextDep,
    _csrf: CsrfDep,
    user_id: CustomerIdDep,
) -> MeasurementOut:
    """提交私密量体版本（本人）。数值越界只提示，不拒绝特殊体型。"""
    measurement = svc.create_measurement(
        ctx.session, user_id=user_id, project_id=project_id, payload=payload
    )
    return svc.measurement_dto(measurement, include_payload=True)


# ------------------------------------------------------------------ 制作阶段
@public_router.post("/production-stages/{stage_id}/confirm", response_model=ProductionStageOut)
def confirm_stage(
    stage_id: str, payload: StageConfirmInput, ctx: ContextDep, _csrf: CsrfDep, user_id: CustomerIdDep
) -> ProductionStageOut:
    """客户确认阶段：绑定 revision，旧版确认返回 409。"""
    stage = svc.confirm_stage(ctx.session, user_id=user_id, stage_id=stage_id, payload=payload)
    audit.record(
        ctx.session,
        actor_type="customer",
        actor_id=user_id,
        action="production_stage.confirm.request",
        resource_type="production_stage",
        resource_id=stage.id,
        after={"revision": payload.revision},
        ip=ctx.client_ip,
    )
    return svc.stage_dto(stage)


@public_router.post("/production-stages/{stage_id}/changes", response_model=ChangeRequestOut)
def request_stage_change(
    stage_id: str, payload: StageChangeInput, ctx: ContextDep, _csrf: CsrfDep, user_id: CustomerIdDep
) -> ChangeRequestOut:
    """请求修改阶段，或接受工作人员提出的变更单（接受后生成单独补款应付项）。"""
    change = svc.request_stage_change(ctx.session, user_id=user_id, stage_id=stage_id, payload=payload)
    return svc.change_dto(change)


@public_router.post("/inquiries/{inquiry_id}/reassess", response_model=InquiryDetail)
def request_reassessment(
    inquiry_id: str,
    payload: ReassessmentInput,
    ctx: ContextDep,
    _csrf: CsrfDep,
    user_id: CustomerIdDep,
) -> InquiryDetail:
    """要求重新评估：``QUOTED → SUBMITTED``（07 文档状态机）。

    不删除历史报价版本；旧版本被置为 SUPERSEDED 由服务端的重新评估流程处理，
    已接受的报价不能走本入口（只能取消未付款订单或进入售后）。
    """
    inquiry = svc.request_reassessment(
        ctx.session, user_id=user_id, inquiry_id=inquiry_id, payload=payload
    )
    audit.record(
        ctx.session,
        actor_type="customer",
        actor_id=user_id,
        action="inquiry.request_reassessment",
        resource_type="inquiry",
        resource_id=inquiry.id,
        after={"status": inquiry.status, "version": inquiry.version},
        reason=payload.reason,
        ip=ctx.client_ip,
    )
    return svc.inquiry_detail_dto(ctx.session, inquiry)


@public_router.post("/change-requests/{change_id}/decision", response_model=ChangeRequestOut)
def decide_change_request(
    change_id: str,
    payload: ChangeDecisionInput,
    ctx: ContextDep,
    _csrf: CsrfDep,
    user_id: CustomerIdDep,
) -> ChangeRequestOut:
    """客户对工作人员提出的变更单做决定。

    - 接受：条件更新保证已接受版本不可覆盖，随后生成单独补款应付项；
    - 拒绝：变更单转 ``REJECTED``，原金额、合同快照与已成功付款都不变（07 文档）。
      减项通过退款或减少未付款项表达，不在这里改已发生的账务。
    """
    if payload.accepted:
        change = svc.accept_change_request_by_id(
            ctx.session, user_id=user_id, change_request_id=change_id, expected_version=payload.version
        )
    else:
        change = svc.reject_change_request(
            ctx.session, user_id=user_id, change_request_id=change_id, expected_version=payload.version
        )
    return svc.change_dto(change)


@public_router.get("/estimates/options")
def estimate_options(ctx: ContextDep) -> dict[str, Any]:
    """公开估价配置：无需登录，返回系列、风格、加项与参考区间说明。"""
    return svc.estimate_options(ctx.session)


@public_router.post("/estimates", response_model=EstimateRequestCreated, status_code=201)
def create_estimate(
    payload: EstimateRequestInput,
    ctx: ContextDep,
) -> EstimateRequestCreated:
    """提交公开参考估价请求（FR-026）。

    无需登录，不锁库存、不生成订单或应付项；联系方式加密保存，只用于人工回访。
    """
    return svc.create_estimate_request(ctx.session, payload, source_ip=ctx.client_ip)


__all__ = ["public_router", "admin_router"]
