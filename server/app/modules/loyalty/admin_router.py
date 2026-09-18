"""积分（星绒币）模块运营后台路由。

约束（09/03 文档）：
- **不允许直接改余额**：只能查询流水、发起调整申请、由另一名员工批准；
- 批准与申请分离（C21：全程单人审批，但申请与批准保持分离并留痕）；
- 后台查询不得泄露他人反馈内容；调整必须填写原因并写审计；
- 只读查询按需跨越全部用户，因此这里直接使用本模块的 ORM 与仓储，
  不绕到其它模块的表。
"""

from __future__ import annotations

from typing import Annotated, Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query

from app.api.dependencies import ContextDep, CsrfDep, PrincipalDep, require_permission
from app.modules.loyalty import repository as repo
from app.modules.loyalty import service as svc
from app.modules.loyalty.models import PointAccount, PointLedger, PointRule
from app.modules.loyalty.schemas import (
    PointAdjustmentCreate,
    PointAdjustmentReject,
)
from app.platform import audit
from app.platform.errors import AppError

admin_router = APIRouter(tags=["admin-loyalty"])


@admin_router.get(
    "/points/ledger",
    response_model=dict,
    dependencies=[Depends(require_permission("points.read"))],
)
def list_ledger(
    ctx: ContextDep,
    user_id: Annotated[str | None, Query(max_length=48)] = None,
    entry_type: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    """星绒币流水（可跨用户查询，供后台核对）。只读；纠错必须新增调整流水。"""
    conditions = []
    if user_id:
        conditions.append(PointLedger.user_id == user_id)
    if entry_type:
        conditions.append(PointLedger.entry_type == entry_type)
    total = int(
        ctx.session.scalar(sa.select(sa.func.count()).select_from(PointLedger).where(*conditions)) or 0
    )
    rows = list(
        ctx.session.scalars(
            sa.select(PointLedger)
            .where(*conditions)
            .order_by(PointLedger.created_at.desc(), PointLedger.id.desc())
            .limit(limit)
            .offset((page - 1) * limit)
        )
    )
    return {
        "items": [svc.serialize_ledger_entry(row) for row in rows],
        "total": total,
        "page": page,
        "limit": limit,
        "has_more": page * limit < total,
        "next_cursor": None,
    }


@admin_router.get(
    "/points/accounts",
    response_model=dict,
    dependencies=[Depends(require_permission("points.read"))],
)
def list_accounts(
    ctx: ContextDep,
    user_id: Annotated[str | None, Query(max_length=48)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    """积分账户列表。只给余额与冻结额，不返回手机号或其它个人信息。"""
    conditions = [PointAccount.user_id == user_id] if user_id else []
    total = int(
        ctx.session.scalar(sa.select(sa.func.count()).select_from(PointAccount).where(*conditions)) or 0
    )
    rows = list(
        ctx.session.scalars(
            sa.select(PointAccount)
            .where(*conditions)
            .order_by(PointAccount.posted_balance.desc())
            .limit(limit)
            .offset((page - 1) * limit)
        )
    )
    return {
        "items": [
            {
                "user_id": row.user_id,
                "posted_balance": int(row.posted_balance),
                "reserved_points": int(row.reserved_points),
                "available_points": max(0, int(row.posted_balance) - int(row.reserved_points)),
                "version": int(row.version),
            }
            for row in rows
        ],
        "total": total,
        "page": page,
        "limit": limit,
        "has_more": page * limit < total,
        "next_cursor": None,
    }


@admin_router.get(
    "/points/rules",
    response_model=list,
    dependencies=[Depends(require_permission("points.read"))],
)
def list_rules(ctx: ContextDep) -> list[dict[str, Any]]:
    """规则版本列表。已发布版本不可修改，只能发布新版本。"""
    rows = list(
        ctx.session.scalars(sa.select(PointRule).order_by(PointRule.effective_at.desc()))
    )
    return [
        {
            "rule_version": row.rule_version,
            "kind": row.kind,
            "parameters": row.parameters,
            "effective_at": row.effective_at,
            "status": row.status,
        }
        for row in rows
    ]


@admin_router.get(
    "/point-adjustments",
    response_model=dict,
    dependencies=[Depends(require_permission("points.read"))],
)
def list_adjustments(
    ctx: ContextDep,
    status: str | None = None,
    user_id: Annotated[str | None, Query(max_length=48)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    rows = svc.list_adjustments(ctx.session, user_id=user_id, status=status, limit=limit)
    items = [
        {
            "id": row.id,
            "user_id": row.user_id,
            "delta_points": int(row.delta_points),
            "reason": row.reason,
            "status": row.status,
            "requested_by": row.requested_by,
            "approved_by": row.approved_by,
            "ledger_id": row.ledger_id,
            "version": int(row.version),
            "created_at": row.created_at,
        }
        for row in rows
    ]
    return {
        "items": items,
        "total": len(items),
        "page": page,
        "limit": limit,
        "has_more": False,
        "next_cursor": None,
    }


@admin_router.post(
    "/point-adjustments",
    response_model=dict,
    status_code=201,
    dependencies=[Depends(require_permission("points.adjust"))],
)
def create_adjustment(
    payload: PointAdjustmentCreate,
    ctx: ContextDep,
    _csrf: CsrfDep,
    principal: PrincipalDep,
) -> dict[str, Any]:
    """建立调整申请。申请只登记意图；入账必须由另一名员工批准。"""
    row = svc.create_adjustment(
        ctx.session,
        user_id=payload.user_id,
        delta_points=payload.delta_points,
        reason=payload.reason,
        requested_by=principal.id,
        source_type=payload.source_type,
        source_id=payload.source_id,
        evidence=payload.evidence,
    )
    return {
        "id": row.id,
        "user_id": row.user_id,
        "delta_points": int(row.delta_points),
        "reason": row.reason,
        "status": row.status,
        "requested_by": row.requested_by,
        "version": int(row.version),
    }


@admin_router.post(
    "/point-adjustments/{adjustment_id}/approve",
    response_model=dict,
    dependencies=[Depends(require_permission("points.approve"))],
)
def approve_adjustment(
    adjustment_id: str,
    ctx: ContextDep,
    _csrf: CsrfDep,
    principal: PrincipalDep,
) -> dict[str, Any]:
    """批准并入账。必须与申请人不同（职责分离），入账为追加流水。"""
    row = repo.get_adjustment(ctx.session, adjustment_id=adjustment_id)
    if row is None:
        raise AppError("RESOURCE_NOT_FOUND")
    if principal.id and row.requested_by and row.requested_by == principal.id:
        raise AppError(
            "FORBIDDEN",
            "调整申请与批准必须由不同员工完成（C21 职责分离），请由另一位同事批准。",
        )
    result = svc.approve_adjustment(ctx.session, adjustment_id=adjustment_id, approver_id=principal.id)
    audit.record(
        ctx.session,
        actor_type="staff",
        actor_id=principal.id,
        action="points.adjustment_approved",
        resource_type="point_adjustment",
        resource_id=adjustment_id,
        after=result,
    )
    return result


@admin_router.post(
    "/point-adjustments/{adjustment_id}/reject",
    response_model=dict,
    dependencies=[Depends(require_permission("points.approve"))],
)
def reject_adjustment(
    adjustment_id: str,
    payload: PointAdjustmentReject,
    ctx: ContextDep,
    _csrf: CsrfDep,
    principal: PrincipalDep,
) -> dict[str, Any]:
    row = svc.reject_adjustment(
        ctx.session,
        adjustment_id=adjustment_id,
        approver_id=principal.id,
        reason=payload.reason,
    )
    return {
        "id": row.id,
        "user_id": row.user_id,
        "delta_points": int(row.delta_points),
        "status": row.status,
        "reason": row.reason,
    }


@admin_router.get(
    "/point-appeals",
    response_model=dict,
    dependencies=[Depends(require_permission("points.read"))],
)
def list_appeals(
    ctx: ContextDep,
    status: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    """积分申诉列表。不返回他人反馈内容。"""
    rows = repo.list_appeals(ctx.session, status=status, limit=limit)
    return {
        "items": [svc.serialize_appeal(row) for row in rows],
        "total": len(rows),
        "page": 1,
        "limit": limit,
        "has_more": False,
        "next_cursor": None,
    }


@admin_router.post(
    "/point-appeals/{appeal_id}/decide",
    response_model=dict,
    dependencies=[Depends(require_permission("points.approve"))],
)
def decide_appeal(
    appeal_id: str,
    ctx: ContextDep,
    _csrf: CsrfDep,
    principal: PrincipalDep,
    status: Annotated[str, Query(max_length=20)] = "ACCEPTED",
    resolution: Annotated[str, Query(min_length=5, max_length=300)] = "",
) -> dict[str, Any]:
    row = svc.resolve_appeal(
        ctx.session,
        appeal_id=appeal_id,
        status=status,
        resolution=resolution,
        actor_id=principal.id,
    )
    return svc.serialize_appeal(row)


__all__ = ["admin_router"]

