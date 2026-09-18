"""商务模块 HTTP DTO。

输入一律用 ``StrictDto``（额外字段直接拒绝），因此客户端提交的 ``unit_price_cents``、
``total_cents``、``points`` 等字段不会被静默接受，而是返回 422（08 文档：金额不再作为
可信输入接收）。输出用 ``Dto``，字段名沿用 06 文档的 snake_case。
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from app.modules.contracts import (
    AddressChangeDto,
    AfterSaleDto,
    AfterSaleMessageDto,
    AfterSalePage,
    InstallmentDto,
    InvoiceDto,
    OrderAddressDto,
    OrderDetailDto,
    OrderItemDto,
    OrderPage,
    OrderSummaryDto,
    PaymentSummaryDto,
    PendingActionsDto,
    ShipmentDto,
)
from app.modules.shared.schemas import Dto, StrictDto


# ------------------------------------------------------------------ 购物车
class CartItemInput(StrictDto):
    """PUT 绝对数量。``quantity=0`` 表示移除该项。"""

    quantity: int = Field(default=1, ge=0, le=999)


class CartItemDto(Dto):
    sku_id: str
    quantity: int
    sku_code: str | None = None
    label: str | None = None
    product_id: str | None = None
    product_slug: str | None = None
    product_title: str | None = None
    product_kind: str | None = None
    unit_price_cents: int = 0
    currency: str | None = None
    max_per_order: int = 0
    available: int = 0
    sellable: bool = True
    requires_approval: bool = False
    cover_asset_id: str | None = None


class CartDto(Dto):
    id: str | None = None
    items: list[CartItemDto] = Field(default_factory=list)
    item_count: int = 0
    goods_cents: int = 0
    currency: str | None = None


# ------------------------------------------------------------------ 结算与订单
class CheckoutItemInput(StrictDto):
    sku_id: str = Field(min_length=1, max_length=48)
    quantity: int = Field(ge=1, le=999)


class CheckoutPreviewInput(StrictDto):
    """预览输入只允许 SKU、数量、地址与优惠选择。"""

    items: list[CheckoutItemInput] = Field(min_length=1, max_length=50)
    address_id: str = Field(min_length=1, max_length=48)
    coupon_code: str | None = Field(default=None, max_length=40)
    terms_version: str | None = Field(default=None, max_length=40)


class CheckoutLineDto(Dto):
    sku_id: str
    product_id: str | None = None
    product_slug: str | None = None
    product_title: str | None = None
    label: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    quantity: int
    unit_price_cents: int
    subtotal_cents: int
    currency: str
    weight_grams: int = 0
    requires_approval: bool = False
    available: int = 0


class CheckoutPreviewDto(Dto):
    checkout_token: str
    expires_at: str | None = None
    lines: list[CheckoutLineDto] = Field(default_factory=list)
    goods_cents: int
    discount_cents: int
    shipping_cents: int
    total_cents: int
    currency: str
    address_id: str
    # 优惠券关闭时为 None；启用后回传实际生效的券码与说明，前端不做本地推算。
    coupon_code: str | None = None
    coupon_note: str | None = None


class OrderCreateInput(StrictDto):
    """创建订单输入。金额字段被禁止：任何金额都不可作为可信输入。"""

    checkout_token: str = Field(min_length=8, max_length=200)
    address_id: str = Field(min_length=1, max_length=48)
    terms_version: str = Field(min_length=1, max_length=40)
    accepted: bool


class OrderCreatedDto(Dto):
    order_id: str
    order_no: str
    status: str
    installment_id: str | None = None
    total_cents: int
    currency: str
    expires_at: str | None = None


class OrderCancelInput(StrictDto):
    reason: str | None = Field(default=None, max_length=200)


class OrderActionDto(Dto):
    order_id: str
    status: str
    message: str | None = None


# ------------------------------------------------------------------ 物流
class ReturnShipmentInput(StrictDto):
    carrier: str = Field(min_length=1, max_length=60)
    tracking_no: str = Field(min_length=1, max_length=80)


# ------------------------------------------------------------------ 售后
class AfterSaleCreateInput(StrictDto):
    type: str = Field(default="refund_only", max_length=30)
    reason: str = Field(min_length=1, max_length=1000)
    order_item_id: str | None = Field(default=None, max_length=48)
    requested_amount_cents: int = Field(default=0, ge=0)


class AfterSaleMessageInput(StrictDto):
    body: str = Field(min_length=1, max_length=2000)


# 售后 / 发票 / 订单读模型（`ShipmentDto`、`AfterSaleDto`、`InvoiceDto`、`OrderDetailDto` …）
# 由 `orders` 模块共用，按 04 文档统一声明在 `app/modules/contracts.py`，此处再导出。


# ------------------------------------------------------------------ 发票
class InvoiceRequestInput(StrictDto):
    type: str = Field(default="personal", max_length=20)
    title: str = Field(min_length=1, max_length=120)
    tax_no: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=200)


__all__ = [
    "AddressChangeDto",
    "AfterSaleCreateInput",
    "AfterSaleDto",
    "AfterSaleMessageDto",
    "AfterSaleMessageInput",
    "AfterSalePage",
    "CartDto",
    "CartItemDto",
    "CartItemInput",
    "CheckoutItemInput",
    "CheckoutLineDto",
    "CheckoutPreviewDto",
    "CheckoutPreviewInput",
    "InstallmentDto",
    "InvoiceDto",
    "InvoiceRequestInput",
    "OrderActionDto",
    "OrderAddressDto",
    "OrderCancelInput",
    "OrderCreateInput",
    "OrderCreatedDto",
    "OrderDetailDto",
    "OrderItemDto",
    "OrderPage",
    "OrderSummaryDto",
    "PaymentSummaryDto",
    "PendingActionsDto",
    "ReturnShipmentInput",
    "ShipmentDto",
]
