"""商务与订单模块运营后台路由。

约束（08/03 文档）：
- 后台**禁止修改已付金额**；发货与关闭订单必须走状态机；
- 订单列表与详情只能读取，任何资金纠正走退款流程（``/admin/refunds``）；
- 地址以订单快照为准，运营改地址要产生履约变更记录，不覆盖原始证据；
- 退款成功不自动补库存，回仓检验合格或经运营审核才创建补库存动作。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import Field

from app.api.dependencies import ContextDep, CsrfDep, PrincipalDep, require_permission
from app.modules.commerce import repository as commerce_repo
from app.modules.commerce import service as commerce_svc
from app.modules.shared.schemas import StrictDto
from app.platform import audit
from app.platform.errors import AppError
from app.platform.ids import iso

admin_router = APIRouter(tags=["admin-commerce"])

#: 后台允许的订单状态筛选值（与 08 文档一致）。
ORDER_STATUSES = ("PENDING_PAYMENT", "ACTIVE", "FULFILLING", "SHIPPED", "COMPLETED", "CANCELLED")


class ShipmentCreateInput(StrictDto):
    """运营发货输入。``kind`` 区分出库与退货入库。"""

    order_id: Annotated[str, Field(min_length=4, max_length=48)]
    carrier: Annotated[str, Field(min_length=1, max_length=60)]
    tracking_no: Annotated[str, Field(min_length=1, max_length=80)]
    kind: Annotated[str, Field(max_length=20)] = "outbound"
    note: Annotated[str | None, Field(max_length=300)] = None


class AfterSaleDecisionInput(StrictDto):
    """售后处理输入。售后状态不等于退款渠道结果（08 文档）。"""

    decision: Annotated[str, Field(min_length=2, max_length=20)]
    note: Annotated[str, Field(min_length=2, max_length=500)]


class AdminOrderCloseInput(StrictDto):
    """后台关闭订单。原因是必填项，会进入审计。"""

    reason: Annotated[str, Field(min_length=2, max_length=200)]


@admin_router.get(
    "/orders", response_model=dict, dependencies=[Depends(require_permission("order.read"))]
)
def list_orders(
    ctx: ContextDep,
    status: str | None = None,
    user_id: Annotated[str | None, Query(max_length=48)] = None,
    kind: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    """订单列表。后台可跨用户查询；金额与状态只读。"""
    rows, total = commerce_repo.list_orders_admin(
        ctx.session, user_id=user_id, status=status, kind=kind, limit=limit, offset=(page - 1) * limit
    )
    return {
        "items": [commerce_svc.order_summary_payload(ctx.session, row) for row in rows],
        "total": total,
        "page": page,
        "limit": limit,
        "has_more": page * limit < total,
        "next_cursor": None,
    }


@admin_router.get(
    "/orders/{order_id}", response_model=dict, dependencies=[Depends(require_permission("order.read"))]
)
def order_detail(order_id: str, ctx: ContextDep) -> dict[str, Any]:
    order = commerce_repo.get_order(ctx.session, order_id=order_id)
    if order is None:
        raise AppError("RESOURCE_NOT_FOUND")
    payload = commerce_svc.order_detail_payload(ctx.session, order)
    # 后台附加：履约地址变更记录与操作提示（不覆盖原始快照）。
    payload["address_changes"] = [
        {
            "id": row.id,
            "actor_type": row.actor_type,
            "changed_by": row.actor_id,
            "reason": row.reason,
            "created_at": iso(row.created_at),
        }
        for row in commerce_repo.list_address_changes(ctx.session, order_id=order.id)
    ]
    payload["notice"] = "后台不能修改已付金额；资金纠正请走退款流程。"
    return payload


@admin_router.post(
    "/shipments",
    response_model=dict,
    status_code=201,
    dependencies=[Depends(require_permission("order.fulfill"))],
)
def create_shipment(
    payload: ShipmentCreateInput,
    ctx: ContextDep,
    _csrf: CsrfDep,
    principal: PrincipalDep,
) -> dict[str, Any]:
    """发货。必须校验已付余额与客户确认；物流号不作为身份凭证。"""
    shipment = commerce_svc.create_shipment(
        ctx.session,
        order_id=payload.order_id,
        kind=payload.kind,
        carrier=payload.carrier,
        tracking_no=payload.tracking_no,
        actor_id=principal.id,
        note=payload.note,
    )
    audit.record(
        ctx.session,
        actor_type="staff",
        actor_id=principal.id,
        action="shipment.create",
        resource_type="order",
        resource_id=payload.order_id,
        after={"carrier": payload.carrier, "kind": payload.kind},
    )
    order = commerce_repo.get_order(ctx.session, order_id=payload.order_id)
    return {
        "id": shipment.id,
        "carrier": shipment.carrier,
        "tracking_no": shipment.tracking_no,
        "shipped_at": shipment.shipped_at,
        "order_status": order.status if order else None,
        "notice": "物流单号仅用于配送跟踪，不作为身份凭证。",
    }


@admin_router.post(
    "/orders/{order_id}/close",
    response_model=dict,
    dependencies=[Depends(require_permission("order.fulfill"))],
)
def close_order(
    order_id: str,
    payload: AdminOrderCloseInput,
    ctx: ContextDep,
    _csrf: CsrfDep,
    principal: PrincipalDep,
) -> dict[str, Any]:
    """关闭订单：先关闭或查询渠道，确认没有在途成功付款后才释放预留。"""
    order = commerce_repo.get_order(ctx.session, order_id=order_id)
    if order is None:
        raise AppError("RESOURCE_NOT_FOUND")
    result = commerce_svc.cancel_order(
        ctx.session,
        user_id=order.user_id,
        order_id=order_id,
        reason=payload.reason,
    )
    audit.record(
        ctx.session,
        actor_type="staff",
        actor_id=principal.id,
        action="order.close",
        resource_type="order",
        resource_id=order_id,
        after=result,
        reason=payload.reason,
    )
    return {"id": order_id, "reason": payload.reason, **result}


@admin_router.get(
    "/after-sales", response_model=dict, dependencies=[Depends(require_permission("order.read"))]
)
def list_after_sales(
    ctx: ContextDep,
    status: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    rows, total = commerce_repo.list_after_sales_admin(
        ctx.session, status=status, limit=limit, offset=(page - 1) * limit
    )
    return {
        "items": [commerce_svc.after_sale_payload(ctx.session, row) for row in rows],
        "total": total,
        "page": page,
        "limit": limit,
        "has_more": page * limit < total,
        "next_cursor": None,
    }


@admin_router.post(
    "/after-sales/{after_sale_id}/decision",
    response_model=dict,
    dependencies=[Depends(require_permission("order.fulfill"))],
)
def decide_after_sale(
    after_sale_id: str,
    payload: AfterSaleDecisionInput,
    ctx: ContextDep,
    _csrf: CsrfDep,
    principal: PrincipalDep,
) -> dict[str, Any]:
    """处理售后：审核结论与退款渠道结果分开记录，失败或未知不能说钱已退。"""
    row = commerce_svc.decide_after_sale(
        ctx.session,
        after_sale_id=after_sale_id,
        decision=payload.decision,
        actor_id=principal.id,
        note=payload.note,
    )
    return commerce_svc.after_sale_payload(ctx.session, row)


__all__ = ["admin_router", "ORDER_STATUSES"]
