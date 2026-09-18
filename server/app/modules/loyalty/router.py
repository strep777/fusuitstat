"""积分模块前台 HTTP 路由。

路径见 06 文档：``GET /points/account``、``GET /points/ledger``、``POST /points/appeals``。
后台的积分调整与规则发布路由在同目录的 ``admin_router.py``（由
``app/api/admin_routes.py`` 汇总挂载，分别需要 ``points.adjust`` /
``points.approve`` / ``points.read`` 权限码）。

本人数据一律由会话推导 ``user_id``（不接收 body 中的用户标识），按 ID 查询必带
``user_id`` 过滤；非本人返回 ``RESOURCE_NOT_FOUND``。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request

from app.api.dependencies import (
    ContextDep,
    CsrfDep,
    CustomerIdDep,
    enforce_write_guard,
)
from app.modules.loyalty import service as svc
from app.modules.loyalty.admin_router import admin_router
from app.modules.loyalty.schemas import (
    PointAccountResponse,
    PointAppealCreate,
    PointAppealPage,
    PointAppealResponse,
    PointLedgerItem,
    PointLedgerPage,
    PointRedemptionQuote,
)
from app.modules.shared.schemas import CursorField, LimitField

public_router = APIRouter(tags=["loyalty"])


def _offset_from_cursor(cursor: str | None) -> int:
    """游标即下一页偏移量的字符串形式（本模块两个列表统一用 limit + cursor）。"""
    if not cursor:
        return 0
    try:
        value = int(cursor)
    except ValueError:
        return 0
    return max(0, value)


@public_router.get("/points/account", response_model=PointAccountResponse)
def get_points_account(ctx: ContextDep, user_id: CustomerIdDep) -> PointAccountResponse:
    """本人积分账户：可用 / 冻结 / 待入账与当前规则版本。"""
    return PointAccountResponse(**svc.account_view(ctx.session, user_id=user_id))


@public_router.get("/points/ledger", response_model=PointLedgerPage)
def list_points_ledger(
    ctx: ContextDep,
    user_id: CustomerIdDep,
    limit: LimitField = 20,
    cursor: CursorField = None,
) -> PointLedgerPage:
    """本人积分流水：delta、中文描述、来源摘要、发生时间、调整关联与申诉入口。"""
    offset = _offset_from_cursor(cursor)
    items, total = svc.ledger_view(ctx.session, user_id=user_id, limit=limit, offset=offset)
    has_more = offset + len(items) < total
    return PointLedgerPage(
        items=[PointLedgerItem(**item) for item in items],
        next_cursor=str(offset + limit) if has_more else None,
        has_more=has_more,
    )


@public_router.get("/points/appeals", response_model=PointAppealPage)
def list_points_appeals(
    ctx: ContextDep,
    user_id: CustomerIdDep,
    limit: LimitField = 20,
    cursor: CursorField = None,
) -> PointAppealPage:
    """本人积分申诉列表（03 文档：``/account/points`` 需要展示申诉与结论）。"""
    offset = _offset_from_cursor(cursor)
    items, total = svc.appeals_view(ctx.session, user_id=user_id, limit=limit, offset=offset)
    has_more = offset + len(items) < total
    return PointAppealPage(
        items=[PointAppealResponse(**item) for item in items],
        next_cursor=str(offset + limit) if has_more else None,
        has_more=has_more,
    )


@public_router.get("/points/redemption-quote", response_model=PointRedemptionQuote)
def quote_points_redemption(
    ctx: ContextDep,
    user_id: CustomerIdDep,
    amount_cents: Annotated[int, Query(ge=1, le=100_000_000)],
    points: Annotated[int, Query(ge=0, le=100_000_000)] = 0,
) -> PointRedemptionQuote:
    """结算积分抵扣报价（C15；未配置规则或开关关闭时返回 403 ``FEATURE_DISABLED``）。

    客户端只提交「这笔金额想用多少积分」，可用上限、抵扣比例与折算结果全部由服务端给出。
    """
    return PointRedemptionQuote(
        **svc.redemption_quote(
            ctx.session, user_id=user_id, amount_cents=amount_cents, points=points
        )
    )


@public_router.post("/points/appeals", response_model=PointAppealResponse, status_code=201)
def create_points_appeal(
    payload: PointAppealCreate,
    ctx: ContextDep,
    _csrf: CsrfDep,
    user_id: CustomerIdDep,
    request: Request,
) -> PointAppealResponse:
    """对本人某条流水提出申诉（理由必填，运营复核后给出结论）。"""
    enforce_write_guard(request)
    row = svc.create_appeal(
        ctx.session, user_id=user_id, ledger_id=payload.ledger_id, reason=payload.reason,
        client_ip=ctx.client_ip,
    )
    ctx.session.commit()
    return PointAppealResponse(**svc.serialize_appeal(row))


__all__ = ["public_router", "admin_router"]
