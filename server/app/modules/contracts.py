"""跨模块端口契约（唯一维护位置）。

模块之间只通过这些 DTO 与命令函数协作；子模块不互相导入 ORM 表、
不调用另一个模块的 repository、不访问别人的私有函数（04 文档）。

本文件只声明形状与命令签名，具体实现分属各模块的 ``ports.py``；
``app/application/workflows`` 负责编排跨模块流程。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from pydantic import Field
from sqlalchemy.orm import Session

from app.modules.shared.schemas import Dto, Page

# ------------------------------------------------------------------ 目录
InventoryAction = Literal["reserve", "release", "consume", "restock"]


@dataclass(frozen=True)
class SkuSnapshot:
    sku_id: str
    sku_code: str
    product_id: str
    product_slug: str
    product_title: str
    product_kind: str
    label: str
    attributes: dict[str, Any]
    unit_price_cents: int
    currency: str
    weight_grams: int
    requires_approval: bool
    shipping_template: str
    terms_version: str


@dataclass(frozen=True)
class ReservationResult:
    reservation_id: str
    sku_id: str
    quantity: int
    expires_at: int | None


class CatalogPort(Protocol):
    """目录模块对外能力：库存与商品快照。"""

    def sku_snapshot(self, session: Session, sku_id: str) -> SkuSnapshot: ...

    def reserve_inventory(
        self,
        session: Session,
        *,
        sku_id: str,
        quantity: int,
        order_id: str,
        order_item_id: str,
        expires_at: int | None,
    ) -> ReservationResult: ...

    def release_inventory(self, session: Session, *, reservation_id: str, reason: str) -> None: ...

    def consume_inventory(self, session: Session, *, reservation_id: str) -> None: ...


# ------------------------------------------------------------------ 支付
PaymentProviderName = Literal["wechat", "alipay", "paypal", "stripe"]


@dataclass(frozen=True)
class PaymentIntent:
    attempt_id: str
    provider: str
    merchant_order_no: str
    expected_cents: int
    currency: str
    status: str
    action: dict[str, Any]
    expires_at: int | None


@dataclass(frozen=True)
class VerifiedPayment:
    """仅由已验签通知或服务端主动查单产生。"""

    provider: str
    provider_transaction_id: str
    merchant_order_no: str
    amount_cents: int
    currency: str
    paid_at: int | None
    raw_digest: str
    event_id: str | None = None
    account_ref: str | None = None


@dataclass(frozen=True)
class RefundOutcome:
    refund_no: str
    provider_refund_id: str | None
    amount_cents: int
    status: Literal["PROCESSING", "UNKNOWN", "SUCCEEDED", "FAILED"]


class PaymentsPort(Protocol):
    """支付模块对外能力：为应付项发起收款、查单、退款与对账。"""

    def create_intent(
        self, session: Session, *, installment_id: str, provider: str, scene: str, actor_id: str
    ) -> PaymentIntent: ...

    def apply_verified_payment(self, session: Session, verified: VerifiedPayment) -> dict[str, Any]: ...

    def request_refund(
        self, session: Session, *, refund_id: str, actor_id: str, idempotency_key: str
    ) -> RefundOutcome: ...


# ------------------------------------------------------------------ 商务
@dataclass(frozen=True)
class OrderTotals:
    goods_cents: int
    discount_cents: int
    shipping_cents: int
    total_cents: int
    currency: str


@dataclass(frozen=True)
class CreatedOrder:
    order_id: str
    order_no: str
    status: str
    installment_id: str | None
    totals: OrderTotals
    expires_at: int | None


class CommercePort(Protocol):
    """商务模块对外能力：创建现货订单、查询实付金额。"""

    def create_stock_order(
        self, session: Session, *, user_id: str, payload: dict[str, Any], idempotency_key: str
    ) -> CreatedOrder: ...

    def order_reward_snapshot(self, session: Session, order_id: str) -> dict[str, Any]: ...


# ------------------------------------------------------------------ 委托
@dataclass(frozen=True)
class CommissionOrderRequest:
    inquiry_id: str
    quote_id: str
    quote_version: int
    user_id: str
    terms_version: str
    idempotency_key: str


class CommissionsPort(Protocol):
    """委托模块对外能力：接受报价并建立委托订单。"""

    def accept_quote(
        self, session: Session, request: CommissionOrderRequest
    ) -> CreatedOrder: ...


# ------------------------------------------------------------------ 积分
@dataclass(frozen=True)
class AwardRequest:
    user_id: str
    source_type: str  # "order" | "feedback" | "adjustment"
    source_id: str
    source_revision: int
    eligible_amount_cents: int
    rule_version: str
    available_at: int
    label: str
    detail: dict[str, Any] = field(default_factory=dict)


class LoyaltyPort(Protocol):
    """积分模块对外能力：按来源重算目标积分并入账。"""

    def recompute_award(self, session: Session, request: AwardRequest) -> dict[str, Any]: ...

    def account_view(self, session: Session, *, user_id: str) -> dict[str, Any]: ...


# ------------------------------------------------------------------ 反馈
class FeedbackPort(Protocol):
    def reward_feedback(
        self, session: Session, *, feedback_id: str, level: str, actor_id: str, reason: str
    ) -> dict[str, Any]: ...


# ------------------------------------------------------------------ 通知
def event_names() -> tuple[str, ...]:
    """outbox 事件类型清单。消费者按事件名分发，未知事件只记录最低必要元数据。"""
    return (
        "notification.dispatch",
        "content.prebuild_requested",
        "order.created",
        "order.paid",
        "order.closing_due",
        "order.shipped",
        "payment.verified",
        "payment.unknown",
        "refund.requested",
        "refund.succeeded",
        "points.award_due",
        "points.posted",
        "quote.published",
        "quote.accepted",
        "schedule.slot_expiring",
        "production.stage_ready",
        "feedback.reviewed",
        "settings.feature_flags_changed",
    )


@dataclass(frozen=True)
class NotificationRequest:
    """一次业务事件产生的通知意图。``event_id`` 用于投递去重。"""

    event_id: str
    event_type: str
    user_id: str | None
    title: str
    body: str
    link_path: str | None = None
    category: str = "transaction"
    channels: tuple[str, ...] = ("email",)
    email_target: str | None = None
    phone_target: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


# ------------------------------------------------------------------ 订单读模型
# `commerce` 负责写入与查询订单，`orders` 模块只对外提供同一份只读视图。
# 这两个模块共用同一组响应 DTO，因此按 04 文档把它整体声明在这里（跨模块 DTO 的唯一
# 声明位置），commerce 与 orders 各自再导出，避免 orders 直接依赖 commerce.schemas。
class OrderItemDto(Dto):
    id: str
    sku_id: str | None = None
    description_snapshot: str
    sku_snapshot: dict[str, Any] = Field(default_factory=dict)
    quantity: int
    unit_price_cents: int
    discount_allocated_cents: int = 0
    cash_goods_cents: int = 0
    refunded_goods_cents: int = 0


class InstallmentDto(Dto):
    id: str
    kind: str
    sequence: int
    label: str = ""
    amount_cents: int
    currency: str
    status: str
    due_at: str | None = None
    paid_at: str | None = None


class PaymentSummaryDto(Dto):
    paid_cents: int = 0
    refunded_applied_cents: int = 0
    allocations: list[Any] = Field(default_factory=list)
    source: str = "payments"


class ShipmentDto(Dto):
    id: str
    kind: str
    carrier: str
    tracking_no: str
    shipped_at: str | None = None
    delivered_at: str | None = None
    verified_source: str


class OrderAddressDto(Dto):
    snapshot_id: str | None = None
    recipient: str | None = None
    phone_masked: str | None = None
    detail: str | None = None
    province: str | None = None
    city: str | None = None
    postal_code: str | None = None
    region_code: str | None = None
    snapshot_created_at: str | None = None
    changed: bool = False
    last_change_reason: str | None = None
    change_count: int = 0
    changed_at: str | None = None


class AddressChangeDto(Dto):
    id: str
    actor_type: str
    reason: str
    created_at: str | None = None
    province: str | None = None
    city: str | None = None


class InvoiceDto(Dto):
    id: str
    type: str
    status: str
    amount_cents: int
    currency: str
    invoice_reference: str | None = None
    requested_at: str | None = None
    issued_at: str | None = None
    title: str | None = None


class AfterSaleMessageDto(Dto):
    id: str
    author_type: str
    body: str
    created_at: str | None = None


class AfterSaleDto(Dto):
    id: str
    order_id: str
    order_no: str | None = None
    type: str
    status: str
    reason: str
    requested_amount_cents: int = 0
    currency: str = "CNY"
    refunded_cents: int = 0
    restocked: bool = False
    return_carrier: str | None = None
    return_tracking_no: str | None = None
    decision_note: str | None = None
    created_at: str | None = None
    decided_at: str | None = None
    resolved_at: str | None = None
    messages: list[AfterSaleMessageDto] = Field(default_factory=list)
    policy: dict[str, Any] = Field(default_factory=dict)


class AfterSalePage(Page[AfterSaleDto]):
    pass


class OrderSummaryDto(Dto):
    id: str
    order_no: str
    kind: str
    status: str
    currency: str
    goods_cents: int
    discount_cents: int
    shipping_cents: int
    total_cents: int
    paid_cents: int = 0
    refunded_cents: int = 0
    item_count: int = 0
    title: str = ""
    created_at: str | None = None
    expires_at: str | None = None
    awaiting_payment: bool = False


class OrderDetailDto(OrderSummaryDto):
    refunded_applied_cents: int = 0
    terms_version: str = ""
    terms_accepted_at: str | None = None
    paid_at: str | None = None
    shipped_at: str | None = None
    completed_at: str | None = None
    cancelled_at: str | None = None
    cancel_reason: str | None = None
    quote_id: str | None = None
    items: list[OrderItemDto] = Field(default_factory=list)
    installments: list[InstallmentDto] = Field(default_factory=list)
    payments: PaymentSummaryDto = Field(default_factory=PaymentSummaryDto)
    shipments: list[ShipmentDto] = Field(default_factory=list)
    address: OrderAddressDto | None = None
    address_changes: list[AddressChangeDto] = Field(default_factory=list)
    invoices: list[InvoiceDto] = Field(default_factory=list)
    after_sales: list[AfterSaleDto] = Field(default_factory=list)
    snapshot: dict[str, Any] = Field(default_factory=dict)
    policy_snapshot: dict[str, Any] = Field(default_factory=dict)


class PendingActionsDto(Dto):
    orders_awaiting_payment: int = 0


class OrderPage(Dto):
    items: list[OrderSummaryDto] = Field(default_factory=list)
    next_cursor: str | None = None
    has_more: bool = False
    pending_actions: PendingActionsDto = Field(default_factory=PendingActionsDto)


