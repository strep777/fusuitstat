"""商务模块 HTTP 路由。

路径与权限见 06 文档；挂载在 ``/api/v1`` 下且不带前缀。所有按 ID 的查询都同时带
``user_id`` 过滤，非本人返回 404，不泄露资源是否存在。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, Query, Request

from app.api.dependencies import (
    ContextDep,
    CsrfDep,
    CustomerIdDep,
    enforce_write_guard,
)
from app.modules.commerce import repository as repo
from app.modules.commerce import service as svc
from app.modules.commerce.admin_router import admin_router
from app.modules.commerce.schemas import (
    AfterSaleCreateInput,
    AfterSaleDto,
    AfterSaleMessageInput,
    AfterSalePage,
    CartDto,
    CartItemInput,
    CheckoutPreviewDto,
    CheckoutPreviewInput,
    InvoiceDto,
    InvoiceRequestInput,
    OrderActionDto,
    OrderCancelInput,
    OrderCreatedDto,
    OrderCreateInput,
    ReturnShipmentInput,
)
from app.platform import audit
from app.platform.errors import AppError
from app.platform.ids import iso

public_router = APIRouter(tags=["commerce"])


# ------------------------------------------------------------------ 购物车
@public_router.get("/cart", response_model=CartDto)
def get_cart(ctx: ContextDep, user_id: CustomerIdDep) -> CartDto:
    """服务端购物车。只存 SKU 与数量，不存价格与隐私。"""
    return CartDto(**svc.cart_payload(ctx.session, user_id=user_id))


@public_router.put("/cart/items/{skuId}", response_model=CartDto)
def put_cart_item(
    skuId: str,
    payload: CartItemInput,
    ctx: ContextDep,
    request: Request,
    _csrf: CsrfDep,
    user_id: CustomerIdDep,
) -> CartDto:
    """PUT 为绝对数量；``quantity=0`` 表示移除。"""
    enforce_write_guard(request)
    result = svc.set_cart_item(ctx.session, user_id=user_id, sku_id=skuId, quantity=payload.quantity)
    return CartDto(**result)


@public_router.delete("/cart/items/{skuId}", response_model=CartDto)
def delete_cart_item(
    skuId: str,
    ctx: ContextDep,
    request: Request,
    _csrf: CsrfDep,
    user_id: CustomerIdDep,
) -> CartDto:
    enforce_write_guard(request)
    result = svc.remove_cart_item(ctx.session, user_id=user_id, sku_id=skuId)
    return CartDto(**result)


# ------------------------------------------------------------------ 结算与下单
@public_router.post("/checkout/preview", response_model=CheckoutPreviewDto)
def checkout_preview(
    payload: CheckoutPreviewInput,
    ctx: ContextDep,
    request: Request,
    _csrf: CsrfDep,
    user_id: CustomerIdDep,
) -> CheckoutPreviewDto:
    """预览权威价格与配送，返回一次性 ``checkout_token``。"""
    enforce_write_guard(request)
    result = svc.preview_checkout(
        ctx.session,
        user_id=user_id,
        items=[item.model_dump() for item in payload.items],
        address_id=payload.address_id,
        coupon_code=payload.coupon_code,
        terms_version=payload.terms_version,
        redeem_points=payload.redeem_points,
    )
    applied_coupon = result.get("coupon_code")
    result.setdefault(
        "coupon_note",
        "优惠已按后台配置计入本单金额。" if applied_coupon else None,
    )
    return CheckoutPreviewDto(**result)


@public_router.post("/orders", response_model=OrderCreatedDto, status_code=201)
def create_order(
    payload: OrderCreateInput,
    ctx: ContextDep,
    request: Request,
    _csrf: CsrfDep,
    user_id: CustomerIdDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> OrderCreatedDto:
    """创建订单：金额不作为可信输入接收，服务端重算并与预览比对。"""
    enforce_write_guard(request)
    if not idempotency_key:
        raise AppError("VALIDATION_ERROR", "缺少幂等键。", fields={"Idempotency-Key": "必填"})
    created = svc.create_order(
        ctx.session,
        user_id=user_id,
        checkout_token=payload.checkout_token,
        address_id=payload.address_id,
        terms_version=payload.terms_version,
        accepted=payload.accepted,
        idempotency_key=idempotency_key,
        redeem_points=payload.redeem_points,
    )
    return OrderCreatedDto(
        order_id=created.order_id,
        order_no=created.order_no,
        status=created.status,
        installment_id=created.installment_id,
        total_cents=created.totals.total_cents,
        currency=created.totals.currency,
        expires_at=iso(created.expires_at),
        points_discount_cents=created.totals.points_discount_cents,
        points_redeemed=created.totals.points_redeemed,
    )


@public_router.post("/orders/{id}/cancel", response_model=OrderActionDto)
def cancel_order(
    id: str,
    payload: OrderCancelInput,
    ctx: ContextDep,
    request: Request,
    _csrf: CsrfDep,
    user_id: CustomerIdDep,
) -> OrderActionDto:
    """关闭订单：先核对渠道，确认没有在途成功付款后才释放预留。"""
    enforce_write_guard(request)
    reason = (payload.reason or "客户取消").strip()[:200]
    result = svc.cancel_order(ctx.session, user_id=user_id, order_id=id, reason=reason)
    return OrderActionDto(
        order_id=result["order_id"],
        status=result["status"],
        message="订单已取消，库存预留已释放。",
    )


@public_router.post("/orders/{id}/confirm-receipt", response_model=OrderActionDto)
def confirm_receipt(
    id: str,
    ctx: ContextDep,
    request: Request,
    _csrf: CsrfDep,
    user_id: CustomerIdDep,
) -> OrderActionDto:
    enforce_write_guard(request)
    result = svc.confirm_receipt(ctx.session, user_id=user_id, order_id=id)
    return OrderActionDto(
        order_id=result["order_id"], status=result["status"], message="已确认收货。"
    )


# ------------------------------------------------------------------ 售后
@public_router.post("/orders/{id}/after-sales", response_model=AfterSaleDto, status_code=201)
def create_after_sale(
    id: str,
    payload: AfterSaleCreateInput,
    ctx: ContextDep,
    request: Request,
    _csrf: CsrfDep,
    user_id: CustomerIdDep,
) -> AfterSaleDto:
    enforce_write_guard(request)
    row = svc.create_after_sale(
        ctx.session,
        user_id=user_id,
        order_id=id,
        type_=payload.type,
        reason=payload.reason,
        order_item_id=payload.order_item_id,
        requested_amount_cents=payload.requested_amount_cents,
    )
    return AfterSaleDto(**svc.after_sale_payload(ctx.session, row))


@public_router.get("/after-sales", response_model=AfterSalePage)
def list_after_sales(
    ctx: ContextDep,
    user_id: CustomerIdDep,
    status: Annotated[str | None, Query(max_length=20)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AfterSalePage:
    rows, total = repo.list_after_sales(
        ctx.session, user_id=user_id, status=status, limit=limit, offset=offset
    )
    items = [AfterSaleDto(**svc.after_sale_payload(ctx.session, row)) for row in rows]
    return AfterSalePage(items=items, next_cursor=None, has_more=offset + len(items) < total)


@public_router.get("/after-sales/{id}", response_model=AfterSaleDto)
def get_after_sale(id: str, ctx: ContextDep, user_id: CustomerIdDep) -> AfterSaleDto:
    row = repo.get_after_sale(ctx.session, after_sale_id=id, user_id=user_id)
    if row is None:
        raise AppError("RESOURCE_NOT_FOUND")
    return AfterSaleDto(**svc.after_sale_payload(ctx.session, row))


@public_router.post("/after-sales/{id}/messages", response_model=AfterSaleDto)
def add_after_sale_message(
    id: str,
    payload: AfterSaleMessageInput,
    ctx: ContextDep,
    request: Request,
    _csrf: CsrfDep,
    user_id: CustomerIdDep,
) -> AfterSaleDto:
    enforce_write_guard(request)
    row = svc.add_after_sale_message(ctx.session, user_id=user_id, after_sale_id=id, body=payload.body)
    return AfterSaleDto(**svc.after_sale_payload(ctx.session, row))


@public_router.post("/after-sales/{id}/return-shipment", response_model=AfterSaleDto)
def submit_return_shipment(
    id: str,
    payload: ReturnShipmentInput,
    ctx: ContextDep,
    request: Request,
    _csrf: CsrfDep,
    user_id: CustomerIdDep,
) -> AfterSaleDto:
    """填写退货物流。只记录退货在途，不触发回库。"""
    enforce_write_guard(request)
    svc.submit_return_shipment(
        ctx.session,
        user_id=user_id,
        after_sale_id=id,
        carrier=payload.carrier,
        tracking_no=payload.tracking_no,
    )
    row = repo.get_after_sale(ctx.session, after_sale_id=id, user_id=user_id)
    if row is None:
        raise AppError("RESOURCE_NOT_FOUND")
    return AfterSaleDto(**svc.after_sale_payload(ctx.session, row))


# ------------------------------------------------------------------ 发票
@public_router.post("/orders/{id}/invoices", response_model=InvoiceDto, status_code=201)
def request_invoice(
    id: str,
    payload: InvoiceRequestInput,
    ctx: ContextDep,
    request: Request,
    _csrf: CsrfDep,
    user_id: CustomerIdDep,
) -> InvoiceDto:
    """开票申请：抬头与税号加密保存并最小化。"""
    enforce_write_guard(request)
    row = svc.request_invoice(
        ctx.session,
        user_id=user_id,
        order_id=id,
        type_=payload.type,
        title=payload.title,
        tax_no=payload.tax_no,
        email=payload.email,
        note=payload.note,
    )
    audit.record(
        ctx.session,
        actor_type="customer",
        actor_id=user_id,
        action="invoice.request_submitted",
        resource_type="order",
        resource_id=id,
        after={"invoice_id": row.id, "type": row.type},
        ip=ctx.client_ip,
    )
    title = svc.invoice_title_data(row)
    return InvoiceDto(
        id=row.id,
        type=row.type,
        status=row.status,
        amount_cents=row.amount_cents,
        currency=row.currency,
        invoice_reference=row.invoice_reference,
        requested_at=iso(row.requested_at),
        issued_at=iso(row.issued_at),
        title=title.get("title"),
    )


@public_router.get("/orders/{id}/invoices", response_model=list[InvoiceDto])
def list_invoices(id: str, ctx: ContextDep, user_id: CustomerIdDep) -> list[InvoiceDto]:
    order = repo.get_order(ctx.session, order_id=id, user_id=user_id)
    if order is None:
        raise AppError("RESOURCE_NOT_FOUND")
    return [
        InvoiceDto(
            id=row.id,
            type=row.type,
            status=row.status,
            amount_cents=row.amount_cents,
            currency=row.currency,
            invoice_reference=row.invoice_reference,
            requested_at=iso(row.requested_at),
            issued_at=iso(row.issued_at),
            title=svc.invoice_title_data(row).get("title"),
        )
        for row in repo.list_invoices(ctx.session, order_id=order.id)
    ]


__all__ = ["public_router", "admin_router"]
