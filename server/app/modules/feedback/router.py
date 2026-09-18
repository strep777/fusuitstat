"""反馈模块前台 HTTP 路由。

路径见 06 文档：``POST /feedback``、``GET /feedback``、``GET /feedback/{id}``、
``POST /feedback/{id}/messages``、``POST /feedback/{id}/appeals``。
后台审核路由在同目录的 ``admin_router.py``（权限码 ``feedback.review``），
由 ``app/api/admin_routes.py`` 汇总挂载。

提交响应固定 ``{id,status:"SUBMITTED",reward_status:"PENDING_REVIEW"}``：
提交时不能给出确定积分。所有按 ID 的查询都带 ``user_id`` 过滤，非本人返回 404。
"""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import APIRouter, Header, Request

from app.api.dependencies import ContextDep, CsrfDep, CustomerIdDep, enforce_write_guard
from app.modules.feedback import service as svc
from app.modules.feedback.admin_router import admin_router
from app.modules.feedback.schemas import (
    FeedbackAppealCreate,
    FeedbackAppealResponse,
    FeedbackCreate,
    FeedbackCreatedResponse,
    FeedbackDetailResponse,
    FeedbackMessageCreate,
    FeedbackMessageItem,
    FeedbackPage,
    FeedbackStatus,
    FeedbackSummaryItem,
    RewardStatus,
)
from app.modules.shared.schemas import CursorField, LimitField

public_router = APIRouter(tags=["feedback"])


def _offset_from_cursor(cursor: str | None) -> int:
    """游标即下一页偏移量的字符串形式（本模块列表统一用 limit + cursor）。"""
    if not cursor:
        return 0
    try:
        value = int(cursor)
    except ValueError:
        return 0
    return max(0, value)


@public_router.post("/feedback", response_model=FeedbackCreatedResponse, status_code=201)
def create_feedback(
    payload: FeedbackCreate,
    ctx: ContextDep,
    _csrf: CsrfDep,
    user_id: CustomerIdDep,
    request: Request,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> FeedbackCreatedResponse:
    """提交反馈（本人 + 幂等键）。响应不含确定积分，奖励由人工审核决定。"""
    enforce_write_guard(request)
    row = svc.create_feedback(
        ctx.session,
        user_id=user_id,
        category=payload.category,
        title=payload.title,
        body=payload.body,
        page_path=payload.page_path,
        steps=payload.steps,
        asset_ids=list(payload.asset_ids),
        idempotency_key=idempotency_key,
        client_ip=ctx.client_ip,
    )
    ctx.session.commit()
    # 模型列是受 CHECK 约束的字符串；此处收敛到契约枚举，前端按枚举分支。
    return FeedbackCreatedResponse(
        id=row.id,
        status=cast(FeedbackStatus, row.status),
        reward_status=cast(RewardStatus, row.reward_status),
    )


@public_router.get("/feedback", response_model=FeedbackPage)
def list_feedback(
    ctx: ContextDep,
    user_id: CustomerIdDep,
    status: str | None = None,
    category: str | None = None,
    limit: LimitField = 20,
    cursor: CursorField = None,
) -> FeedbackPage:
    """本人反馈列表（分页）。"""
    offset = _offset_from_cursor(cursor)
    items, total = svc.list_feedback_page(
        ctx.session, user_id=user_id, status=status, category=category, limit=limit, offset=offset
    )
    has_more = offset + len(items) < total
    return FeedbackPage(
        items=[FeedbackSummaryItem(**item) for item in items],
        next_cursor=str(offset + limit) if has_more else None,
        has_more=has_more,
    )


@public_router.get("/feedback/{feedback_id}", response_model=FeedbackDetailResponse)
def get_feedback(feedback_id: str, ctx: ContextDep, user_id: CustomerIdDep) -> FeedbackDetailResponse:
    """本人反馈详情：只含用户可见回复，内部备注不返回。"""
    detail = svc.get_feedback_detail(ctx.session, user_id=user_id, feedback_id=feedback_id)
    return FeedbackDetailResponse(**detail)


@public_router.post(
    "/feedback/{feedback_id}/messages", response_model=FeedbackMessageItem, status_code=201
)
def add_feedback_message(
    feedback_id: str,
    payload: FeedbackMessageCreate,
    ctx: ContextDep,
    _csrf: CsrfDep,
    user_id: CustomerIdDep,
    request: Request,
) -> FeedbackMessageItem:
    """补充说明（本人）。用户消息始终为用户可见，不会写成内部备注。"""
    enforce_write_guard(request)
    row = svc.add_message(
        ctx.session, user_id=user_id, feedback_id=feedback_id, body=payload.body, client_ip=ctx.client_ip
    )
    ctx.session.commit()
    return FeedbackMessageItem(**svc.serialize_message(row))


@public_router.post(
    "/feedback/{feedback_id}/appeals", response_model=FeedbackAppealResponse, status_code=201
)
def create_feedback_appeal(
    feedback_id: str,
    payload: FeedbackAppealCreate,
    ctx: ContextDep,
    _csrf: CsrfDep,
    user_id: CustomerIdDep,
    request: Request,
) -> FeedbackAppealResponse:
    """对处理结论或奖励提出申诉（本人，理由必填）。"""
    enforce_write_guard(request)
    row = svc.create_appeal(
        ctx.session,
        user_id=user_id,
        feedback_id=feedback_id,
        reason=payload.reason,
        client_ip=ctx.client_ip,
    )
    ctx.session.commit()
    return FeedbackAppealResponse(**svc.serialize_appeal(row))


__all__ = ["public_router", "admin_router"]
