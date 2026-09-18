"""订单查询模块 HTTP 路由。

``GET /orders``（状态筛选、分页、待付款提醒）与 ``GET /orders/{id}``（明细/快照、
款项、支付、取消、物流、退款与发票）。挂载在 ``/api/v1`` 下且不带前缀。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.dependencies import ContextDep, CustomerIdDep
from app.modules.orders import service as svc
from app.modules.orders.schemas import OrderDetailDto, OrderPage

public_router = APIRouter(tags=["orders"])


@public_router.get("/orders", response_model=OrderPage)
def list_orders(
    ctx: ContextDep,
    user_id: CustomerIdDep,
    status: Annotated[str | None, Query(max_length=24)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> OrderPage:
    """本人订单列表；``pending_actions`` 给出待付款提醒数量。"""
    result = svc.list_orders(ctx.session, user_id=user_id, status=status, page=page, limit=limit)
    return OrderPage(**result)


@public_router.get("/orders/{id}", response_model=OrderDetailDto)
def get_order(id: str, ctx: ContextDep, user_id: CustomerIdDep) -> OrderDetailDto:
    """订单详情。非本人订单返回 404，不泄露资源是否存在。"""
    return OrderDetailDto(**svc.order_detail(ctx.session, user_id=user_id, order_id=id))


__all__ = ["public_router"]
