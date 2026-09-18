"""商务模块：购物车、结算令牌、订单、应付项、售后、物流与发票。

拥有的数据：carts、cart_items、checkout_sessions、orders、order_items、
installments、after_sales、after_sale_messages、shipments、invoices、
order_address_changes（履约变更记录）。
支付相关的 ``payment_*``、``refunds``、``reconciliation_*`` 属于支付模块（08 文档）。

不变量（05/08 文档）：
1. 金额一律整数分 + 显式币种，列级 CHECK 保证非负；
   ``order_total = Σ(unit_price × quantity) - discount_total + shipping_total``；
2. ``orders.order_no`` 唯一；``quote_id`` 在非空时唯一（一笔报价只能一个订单）；
3. 订单行优惠分摊之和等于订单优惠，商品现金分摊不得为负；
4. 财务记录使用限制删除的外键，禁止级联删除订单。
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.ids import new_id, now_ms
from app.platform.orm import Base, PkMixin, TimestampMixin, VersionMixin

ORDER_KINDS = ("stock_goods", "exclusive_character", "commission", "service")
ORDER_STATUSES = ("PENDING_PAYMENT", "ACTIVE", "FULFILLING", "SHIPPED", "COMPLETED", "CANCELLED")
INSTALLMENT_KINDS = ("full", "prepayment", "balance", "change")
INSTALLMENT_STATUSES = ("DUE", "PAYING", "PAID", "CLOSING", "CLOSED")
AFTER_SALE_TYPES = ("refund_only", "return_refund", "repair", "exchange")
AFTER_SALE_STATUSES = ("OPEN", "NEEDS_INFO", "APPROVED", "RETURNING", "RESOLVED", "REJECTED")
MESSAGE_AUTHOR_TYPES = ("customer", "staff", "system")
SHIPMENT_KINDS = ("outbound", "return")
SHIPMENT_VERIFIED_SOURCES = ("staff", "provider", "customer")
INVOICE_TYPES = ("personal", "business")
INVOICE_STATUSES = ("PENDING", "ISSUED", "FAILED", "VOID")
ADDRESS_CHANGE_ACTORS = ("staff", "customer", "system")


def _quantity_check(name: str) -> sa.CheckConstraint:
    return sa.CheckConstraint("quantity > 0", name=name)


class Cart(PkMixin, TimestampMixin, VersionMixin, Base):
    """服务端购物车。只存 SKU 与数量，不存价格与隐私（08 文档）。"""

    __tablename__ = "carts"
    id_kind = "cart"

    user_id: Mapped[str] = mapped_column(
        sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True
    )

    __table_args__ = (sa.Index("ix_carts_user", "user_id"),)


class CartItem(PkMixin, TimestampMixin, Base):
    """购物车项。``quantity`` 为绝对数量，上限由 SKU 配置裁决。"""

    __tablename__ = "cart_items"
    id_kind = "sku"

    cart_id: Mapped[str] = mapped_column(
        sa.ForeignKey("carts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sku_id: Mapped[str] = mapped_column(
        sa.ForeignKey("skus.id", ondelete="RESTRICT"), nullable=False
    )
    quantity: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=1)

    __table_args__ = (
        sa.UniqueConstraint("cart_id", "sku_id", name="uq_cart_item_sku"),
        _quantity_check("ck_cart_items_qty"),
    )


class CheckoutSession(PkMixin, TimestampMixin, Base):
    """一次性结算令牌。只保存令牌哈希与行摘要，单价以服务端重算为准。"""

    __tablename__ = "checkout_sessions"
    id_kind = "job"

    user_id: Mapped[str] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False, unique=True)
    lines: Mapped[list[Any] | None] = mapped_column(sa.JSON)
    address_id: Mapped[str] = mapped_column(sa.String(48), nullable=False)
    coupon_code: Mapped[str | None] = mapped_column(sa.String(40))
    goods_cents: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    discount_cents: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    shipping_cents: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    total_cents: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(sa.String(3), nullable=False, default="CNY")
    terms_version: Mapped[str] = mapped_column(sa.String(40), nullable=False, default="shop-1")
    expires_at: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    consumed_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    consumed_order_id: Mapped[str | None] = mapped_column(sa.String(48))

    __table_args__ = (
        sa.CheckConstraint("goods_cents >= 0 AND discount_cents >= 0", name="ck_checkout_amounts"),
        sa.CheckConstraint("shipping_cents >= 0 AND total_cents >= 0", name="ck_checkout_amounts2"),
        sa.Index("ix_checkout_sessions_user", "user_id", "created_at"),
    )


class Order(PkMixin, TimestampMixin, VersionMixin, Base):
    """订单主表。

    ``paid_cents``/``refunded_cents`` 只是缓存汇总，权威值来自支付模块的分配与退款流水
    （05 文档）；管理员不得直接写缓存字段。``quote_id`` 非空时唯一。
    """

    __tablename__ = "orders"
    id_kind = "order"

    order_no: Mapped[str] = mapped_column(sa.String(40), nullable=False, unique=True)
    user_id: Mapped[str] = mapped_column(sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    kind: Mapped[str] = mapped_column(sa.String(30), nullable=False, default="stock_goods")
    status: Mapped[str] = mapped_column(sa.String(20), nullable=False, default="PENDING_PAYMENT")
    currency: Mapped[str] = mapped_column(sa.String(3), nullable=False, default="CNY")
    goods_cents: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    discount_cents: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    shipping_cents: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    total_cents: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    paid_cents: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    refunded_cents: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    refunded_goods_cents: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    refunded_shipping_cents: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    quote_id: Mapped[str | None] = mapped_column(sa.String(48), unique=True)
    inquiry_id: Mapped[str | None] = mapped_column(sa.String(48))
    quote_version: Mapped[int | None] = mapped_column(sa.Integer)
    slot_reservation_id: Mapped[str | None] = mapped_column(sa.String(48), unique=True)
    checkout_session_id: Mapped[str | None] = mapped_column(sa.String(48))
    idempotency_key: Mapped[str | None] = mapped_column(sa.String(120))
    address_snapshot_id: Mapped[str | None] = mapped_column(sa.String(48))
    terms_version: Mapped[str] = mapped_column(sa.String(40), nullable=False, default="shop-1")
    terms_accepted_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    terms_snapshot: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON)
    cart_snapshot: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON)
    snapshot: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON)
    policy_snapshot: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON)
    cancel_reason: Mapped[str | None] = mapped_column(sa.String(200))
    cancelled_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    closing_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    closing_reason: Mapped[str | None] = mapped_column(sa.String(120))
    paid_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    fulfilled_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    shipped_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    completed_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    expires_at: Mapped[int | None] = mapped_column(sa.BigInteger)

    __table_args__ = (
        sa.CheckConstraint(
            "kind IN ('stock_goods','exclusive_character','commission','service')",
            name="ck_orders_kind",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING_PAYMENT','ACTIVE','FULFILLING','SHIPPED','COMPLETED','CANCELLED')",
            name="ck_orders_status",
        ),
        sa.CheckConstraint("goods_cents >= 0 AND discount_cents >= 0", name="ck_orders_amounts"),
        sa.CheckConstraint("shipping_cents >= 0 AND total_cents >= 0", name="ck_orders_amounts2"),
        sa.CheckConstraint("paid_cents >= 0 AND refunded_cents >= 0", name="ck_orders_amounts3"),
        sa.CheckConstraint(
            "refunded_goods_cents >= 0 AND refunded_shipping_cents >= 0",
            name="ck_orders_amounts4",
        ),
        sa.CheckConstraint("discount_cents <= goods_cents", name="ck_orders_discount_le_goods"),
        sa.CheckConstraint(
            "total_cents = goods_cents - discount_cents + shipping_cents",
            name="ck_orders_total_formula",
        ),
        sa.Index("ix_orders_user_status", "user_id", "status", "created_at"),
        sa.Index("ix_orders_user_created", "user_id", "created_at"),
    )


class OrderItem(PkMixin, TimestampMixin, Base):
    """订单行快照。``sku_id`` 可空（委托订单可不依赖可售 SKU）。"""

    __tablename__ = "order_items"
    id_kind = "order"

    order_id: Mapped[str] = mapped_column(
        sa.ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    sku_id: Mapped[str | None] = mapped_column(sa.ForeignKey("skus.id", ondelete="RESTRICT"))
    product_id: Mapped[str | None] = mapped_column(sa.String(48))
    description_snapshot: Mapped[str] = mapped_column(sa.String(300), nullable=False, default="")
    sku_snapshot: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON)
    quantity: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=1)
    unit_price_cents: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    discount_allocated_cents: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    cash_goods_cents: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    refunded_goods_cents: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    weight_grams: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    requires_approval: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    application_id: Mapped[str | None] = mapped_column(sa.String(48))

    __table_args__ = (
        _quantity_check("ck_order_items_qty"),
        sa.CheckConstraint("unit_price_cents >= 0", name="ck_order_items_price"),
        sa.CheckConstraint("discount_allocated_cents >= 0", name="ck_order_items_discount"),
        sa.CheckConstraint("cash_goods_cents >= 0", name="ck_order_items_cash"),
        sa.CheckConstraint("refunded_goods_cents >= 0", name="ck_order_items_refunded"),
    )


class Installment(PkMixin, TimestampMixin, VersionMixin, Base):
    """应付项。现货一个全款项；委托有预付款、尾款与已确认加项。"""

    __tablename__ = "installments"
    id_kind = "installment"

    order_id: Mapped[str] = mapped_column(
        sa.ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(sa.String(20), nullable=False, default="full")
    sequence: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=1)
    label: Mapped[str] = mapped_column(sa.String(80), nullable=False, default="")
    amount_cents: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(sa.String(3), nullable=False, default="CNY")
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False, default="DUE")
    due_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    paid_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    closed_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    reason: Mapped[str | None] = mapped_column(sa.String(200))
    change_request_id: Mapped[str | None] = mapped_column(sa.String(48))
    quote_item_id: Mapped[str | None] = mapped_column(sa.String(48))

    __table_args__ = (
        sa.CheckConstraint("kind IN ('full','prepayment','balance','change')", name="ck_installment_kind"),
        sa.CheckConstraint(
            "status IN ('DUE','PAYING','PAID','CLOSING','CLOSED')", name="ck_installment_status"
        ),
        sa.CheckConstraint("amount_cents > 0", name="ck_installment_amount_positive"),
        sa.UniqueConstraint("change_request_id", name="uq_installment_change_request"),
        sa.Index("ix_installments_order_status", "order_id", "status"),
    )


class AddressSnapshot(PkMixin, Base):
    """订单地址快照。字段加密；运营改地址只追加履约变更记录，不覆盖原始证据。"""

    __tablename__ = "address_snapshots"
    id_kind = "address"

    order_id: Mapped[str] = mapped_column(
        sa.ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    source_address_id: Mapped[str | None] = mapped_column(sa.String(48))
    recipient_ciphertext: Mapped[str] = mapped_column(sa.Text, nullable=False)
    phone_ciphertext: Mapped[str] = mapped_column(sa.Text, nullable=False)
    address_ciphertext: Mapped[str] = mapped_column(sa.Text, nullable=False)
    region_code: Mapped[str] = mapped_column(sa.String(10), nullable=False, default="CN")
    province: Mapped[str] = mapped_column(sa.String(40), nullable=False, default="")
    city: Mapped[str] = mapped_column(sa.String(40), nullable=False, default="")
    postal_code: Mapped[str | None] = mapped_column(sa.String(20))
    created_at: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, default=now_ms)


class OrderAddressChange(PkMixin, Base):
    """履约地址变更记录（追加写）。原始地址快照与原始证据保持不变。"""

    __tablename__ = "order_address_changes"
    id_kind = "job"

    order_id: Mapped[str] = mapped_column(
        sa.ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    snapshot_id: Mapped[str] = mapped_column(sa.String(48), nullable=False)
    actor_type: Mapped[str] = mapped_column(sa.String(20), nullable=False, default="system")
    actor_id: Mapped[str | None] = mapped_column(sa.String(48))
    reason: Mapped[str] = mapped_column(sa.String(200), nullable=False, default="")
    previous_digest: Mapped[str] = mapped_column(sa.String(64), nullable=False, default="")
    new_recipient_ciphertext: Mapped[str] = mapped_column(sa.Text, nullable=False)
    new_phone_ciphertext: Mapped[str] = mapped_column(sa.Text, nullable=False)
    new_address_ciphertext: Mapped[str] = mapped_column(sa.Text, nullable=False)
    province: Mapped[str] = mapped_column(sa.String(40), nullable=False, default="")
    city: Mapped[str] = mapped_column(sa.String(40), nullable=False, default="")
    postal_code: Mapped[str | None] = mapped_column(sa.String(20))
    created_at: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, default=now_ms)

    __table_args__ = (
        sa.CheckConstraint("actor_type IN ('staff','customer','system')", name="ck_address_change_actor"),
    )


class AfterSale(PkMixin, TimestampMixin, VersionMixin, Base):
    """售后流程。售后状态与退款渠道结果分离（08 文档）。"""

    __tablename__ = "after_sales"
    id_kind = "aftersale"

    order_id: Mapped[str] = mapped_column(
        sa.ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    type: Mapped[str] = mapped_column(sa.String(30), nullable=False, default="refund_only")
    status: Mapped[str] = mapped_column(sa.String(20), nullable=False, default="OPEN")
    reason: Mapped[str] = mapped_column(sa.String(1000), nullable=False, default="")
    order_item_id: Mapped[str | None] = mapped_column(sa.String(48))
    requested_amount_cents: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(sa.String(3), nullable=False, default="CNY")
    message_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    return_tracking_no: Mapped[str | None] = mapped_column(sa.String(80))
    return_carrier: Mapped[str | None] = mapped_column(sa.String(60))
    return_shipment_id: Mapped[str | None] = mapped_column(sa.String(48))
    refund_id: Mapped[str | None] = mapped_column(sa.String(48))
    refunded_cents: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    restocked: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    decision_note: Mapped[str | None] = mapped_column(sa.String(400))
    decided_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    resolved_at: Mapped[int | None] = mapped_column(sa.BigInteger)

    __table_args__ = (
        sa.CheckConstraint(
            "type IN ('refund_only','return_refund','repair','exchange')", name="ck_after_sales_type"
        ),
        sa.CheckConstraint(
            "status IN ('OPEN','NEEDS_INFO','APPROVED','RETURNING','RESOLVED','REJECTED')",
            name="ck_after_sales_status",
        ),
        sa.CheckConstraint("requested_amount_cents >= 0", name="ck_after_sales_amount"),
        sa.CheckConstraint("refunded_cents >= 0", name="ck_after_sales_refunded"),
        sa.CheckConstraint("restocked IN (0,1)", name="ck_after_sales_restocked"),
        sa.Index("ix_after_sales_order_status", "order_id", "status"),
        sa.Index("ix_after_sales_user_status", "user_id", "status", "created_at"),
    )


class AfterSaleMessage(PkMixin, Base):
    """售后沟通记录。用户可见回复与内部备注严格区分。"""

    __tablename__ = "after_sale_messages"
    id_kind = "aftersale"

    after_sale_id: Mapped[str] = mapped_column(
        sa.ForeignKey("after_sales.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    author_type: Mapped[str] = mapped_column(sa.String(20), nullable=False, default="customer")
    author_id: Mapped[str | None] = mapped_column(sa.String(48))
    body: Mapped[str] = mapped_column(sa.String(2000), nullable=False)
    visibility: Mapped[str] = mapped_column(sa.String(20), nullable=False, default="customer")
    created_at: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, default=now_ms)

    __table_args__ = (
        sa.CheckConstraint(
            "author_type IN ('customer','staff','system')", name="ck_after_sale_message_author"
        ),
        sa.CheckConstraint("visibility IN ('customer','internal')", name="ck_after_sale_message_visibility"),
        sa.Index("ix_after_sale_messages_asc", "after_sale_id", "created_at"),
    )


class Shipment(PkMixin, TimestampMixin, VersionMixin, Base):
    """物流记录。物流号不作为身份凭证，只作履约证据。"""

    __tablename__ = "shipments"
    id_kind = "shipment"

    order_id: Mapped[str] = mapped_column(
        sa.ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(sa.String(20), nullable=False, default="outbound")
    carrier: Mapped[str] = mapped_column(sa.String(60), nullable=False)
    tracking_no: Mapped[str] = mapped_column(sa.String(80), nullable=False)
    shipped_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    delivered_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    verified_source: Mapped[str] = mapped_column(sa.String(20), nullable=False, default="staff")
    note: Mapped[str | None] = mapped_column(sa.String(200))
    created_by: Mapped[str | None] = mapped_column(sa.String(48))

    __table_args__ = (
        sa.CheckConstraint(
            "verified_source IN ('staff','provider','customer')", name="ck_shipment_source"
        ),
        sa.CheckConstraint("kind IN ('outbound','return')", name="ck_shipment_kind"),
    )


class Invoice(PkMixin, TimestampMixin, VersionMixin, Base):
    """发票申请。抬头与税号加密保存并最小化，金额与实际交易匹配。"""

    __tablename__ = "invoices"
    id_kind = "invoice"

    order_id: Mapped[str] = mapped_column(
        sa.ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    type: Mapped[str] = mapped_column(sa.String(20), nullable=False, default="personal")
    status: Mapped[str] = mapped_column(sa.String(20), nullable=False, default="PENDING")
    amount_cents: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(sa.String(3), nullable=False, default="CNY")
    encrypted_title_data: Mapped[str] = mapped_column(sa.Text, nullable=False)
    invoice_reference: Mapped[str | None] = mapped_column(sa.String(80))
    note: Mapped[str | None] = mapped_column(sa.String(200))
    requested_at: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, default=now_ms)
    issued_at: Mapped[int | None] = mapped_column(sa.BigInteger)

    __table_args__ = (
        sa.CheckConstraint("type IN ('personal','business')", name="ck_invoices_type"),
        sa.CheckConstraint("status IN ('PENDING','ISSUED','FAILED','VOID')", name="ck_invoices_status"),
        sa.CheckConstraint("amount_cents >= 0", name="ck_invoices_amount"),
        sa.UniqueConstraint("order_id", "type", name="uq_invoices_order_type"),
    )


__all__ = [
    "ADDRESS_CHANGE_ACTORS",
    "AFTER_SALE_STATUSES",
    "AFTER_SALE_TYPES",
    "AddressSnapshot",
    "AfterSale",
    "AfterSaleMessage",
    "Cart",
    "CartItem",
    "CheckoutSession",
    "INVOICE_STATUSES",
    "INVOICE_TYPES",
    "INSTALLMENT_KINDS",
    "INSTALLMENT_STATUSES",
    "Installment",
    "Invoice",
    "MESSAGE_AUTHOR_TYPES",
    "ORDER_KINDS",
    "ORDER_STATUSES",
    "Order",
    "OrderAddressChange",
    "OrderItem",
    "SHIPMENT_KINDS",
    "SHIPMENT_VERIFIED_SOURCES",
    "Shipment",
    "new_id",
]
