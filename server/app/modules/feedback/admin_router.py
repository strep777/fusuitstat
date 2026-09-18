"""反馈模块运营后台路由（权限码绑定见每个端点）。

约束（09/03 文档）：
- 审核记录、奖励命令、outbox 与通知登记在同一事务；
- 同一复审版本只入账一次；升级奖励只补差额；
- 用户可见回复与内部备注严格区分（``visibility``）；
- 界面上隐藏入口不能代替授权，服务端逐动作校验权限码。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from app.api.dependencies import ContextDep, CsrfDep, PrincipalDep, require_permission
from app.modules.feedback import repository as repo
from app.modules.feedback import service as svc
from app.modules.feedback.models import FeedbackMessage
from app.modules.feedback.schemas import (
    FeedbackAppealResolve,
    FeedbackMessageCreate,
    FeedbackReviewCreate,
    FeedbackRewardRevoke,
)
from app.platform import audit
from app.platform.errors import AppError
from app.platform.ids import new_id, now_ms

admin_router = APIRouter(tags=["admin-feedback"])


@admin_router.get(
    "/feedback",
    response_model=dict,
    dependencies=[Depends(require_permission("feedback.read"))],
)
def list_feedback(
    ctx: ContextDep,
    status: str | None = None,
    category: str | None = None,
    reward_status: str | None = None,
    q: Annotated[str | None, Query(max_length=120)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    """反馈列表。只返回反馈字段与脱敏提交者标识，不含手机号明文。"""
    _ = q
    rows = repo.list_feedbacks(
        ctx.session,
        status=status,
        category=category,
        reward_status=reward_status,
        query=q,
        limit=limit,
        offset=(page - 1) * limit,
    )
    total = repo.count_feedbacks(
        ctx.session, status=status, category=category, reward_status=reward_status, query=q
    )
    return {
        "items": [svc.serialize_summary(ctx.session, row) for row in rows],
        "total": total,
        "page": page,
        "limit": limit,
        "has_more": page * limit < total,
        "next_cursor": None,
    }


@admin_router.get(
    "/feedback/{feedback_id}",
    response_model=dict,
    dependencies=[Depends(require_permission("feedback.read"))],
)
def feedback_detail(feedback_id: str, ctx: ContextDep) -> dict[str, Any]:
    row = repo.get_feedback(ctx.session, feedback_id=feedback_id)
    if row is None:
        raise AppError("RESOURCE_NOT_FOUND")
    payload = svc.serialize_detail(ctx.session, row)
    # 内部视图额外带上内部备注与申诉记录；用户侧接口不会返回这些内容。
    payload["internal_messages"] = [
        svc.serialize_message(message)
        for message in repo.list_messages(ctx.session, feedback_id=row.id, visibility="internal")
    ]
    payload["messages"] = [
        svc.serialize_message(message) for message in repo.list_messages(ctx.session, feedback_id=row.id)
    ]
    payload["appeals"] = [
        svc.serialize_appeal(appeal) for appeal in repo.list_appeals(ctx.session, feedback_id=row.id)
    ]
    latest = repo.latest_review(ctx.session, feedback_id=row.id)
    payload["latest_review"] = (
        {
            "revision": latest.revision,
            "result": latest.result,
            "reason": latest.reason,
            "reward_level": latest.reward_level,
            "reviewer_id": latest.reviewer_id,
        }
        if latest is not None
        else None
    )
    return payload


@admin_router.post(
    "/feedback/{feedback_id}/reviews",
    response_model=dict,
    dependencies=[Depends(require_permission("feedback.review"))],
)
def review_feedback(
    feedback_id: str,
    payload: FeedbackReviewCreate,
    ctx: ContextDep,
    _csrf: CsrfDep,
    principal: PrincipalDep,
) -> dict[str, Any]:
    """审核反馈并（可选）发放奖励。

    同一实际问题原则上奖励首个有效报告；升级只补差额；审核人不能审核自己提交的
    反馈（服务层校验）。达到每日奖励上限仍可正常受理，只是本次不获奖。
    """
    return svc.review_feedback(
        ctx.session,
        feedback_id=feedback_id,
        reviewer_id=principal.id,
        result=payload.result,
        reason=payload.reason,
        reward_level=payload.reward_level,
        impact=payload.impact,
        criteria=payload.criteria,
        duplicate_of=payload.duplicate_of,
        resolution_note=payload.resolution_note,
    )


@admin_router.post(
    "/feedback/{feedback_id}/reward-revoke",
    response_model=dict,
    dependencies=[Depends(require_permission("feedback.review"))],
)
def revoke_reward(
    feedback_id: str,
    payload: FeedbackRewardRevoke,
    ctx: ContextDep,
    _csrf: CsrfDep,
    principal: PrincipalDep,
) -> dict[str, Any]:
    """撤回奖励：只用于确认作弊等情形，走复核追加冲正，不删除历史流水。"""
    return svc.revoke_reward(
        ctx.session,
        feedback_id=feedback_id,
        reviewer_id=principal.id,
        reason=payload.reason,
    )


@admin_router.post(
    "/feedback/{feedback_id}/messages",
    response_model=dict,
    dependencies=[Depends(require_permission("feedback.review"))],
)
def add_staff_message(
    feedback_id: str,
    payload: FeedbackMessageCreate,
    ctx: ContextDep,
    _csrf: CsrfDep,
    principal: PrincipalDep,
    request: Request,
) -> dict[str, Any]:
    """员工回复或内部备注。

    ``visibility`` 由查询参数控制：默认 ``user`` 表示这条回复用户可见；
    ``?visibility=internal`` 表示内部备注，**绝不下发给用户**。
    两种消息在同一张表内以 ``visibility`` 严格区分（09 文档）。
    """
    row = repo.get_feedback(ctx.session, feedback_id=feedback_id)
    if row is None:
        raise AppError("RESOURCE_NOT_FOUND")
    visibility = request.query_params.get("visibility", "user")
    if visibility not in ("user", "internal"):
        raise AppError("VALIDATION_ERROR", fields={"visibility": "只能是 user 或 internal"})
    message = FeedbackMessage(
        id=new_id("message"),
        feedback_id=row.id,
        author_type="staff",
        author_id=principal.id,
        body=payload.body.strip(),
        visibility=visibility,
    )
    ctx.session.add(message)
    # C30 首响：**用户可见**的回复才算响应；内部备注不算（用户看不到，不能算已响应）。
    if visibility == "user":
        svc.mark_first_response(row, now_ms())
    ctx.session.flush()
    audit.record(
        ctx.session,
        actor_type="staff",
        actor_id=principal.id,
        action="feedback.staff_message",
        resource_type="feedback",
        resource_id=row.id,
        after={"visibility": visibility},
    )
    return svc.serialize_message(message)


@admin_router.post(
    "/feedback/{feedback_id}/appeals/decide",
    response_model=dict,
    dependencies=[Depends(require_permission("feedback.review"))],
)
def decide_appeal(
    feedback_id: str,
    payload: FeedbackAppealResolve,
    ctx: ContextDep,
    _csrf: CsrfDep,
    principal: PrincipalDep,
    appeal_id: Annotated[str | None, Query(max_length=48)] = None,
) -> dict[str, Any]:
    """处理用户申诉。复审不能重复奖励；一条反馈同时只有一个未结申诉。"""
    _ = feedback_id
    target = appeal_id
    if not target:
        raise AppError("VALIDATION_ERROR", "缺少申诉编号。", fields={"appeal_id": "必填"})
    row = svc.resolve_appeal(
        ctx.session,
        appeal_id=target,
        status=payload.status,
        resolution=payload.resolution,
        actor_id=principal.id,
    )
    audit.record(
        ctx.session,
        actor_type="staff",
        actor_id=principal.id,
        action="feedback.appeal_decided",
        resource_type="feedback_appeal",
        resource_id=row.id,
        after={"status": row.status, "resolution": row.resolution},
        reason=payload.resolution,
    )
    return svc.serialize_appeal(row)


__all__ = ["admin_router"]
