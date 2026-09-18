"""商务模块查询。

所有按 ID 的订单查询都必须能同时带 ``user_id`` 过滤（06 文档：路径 ID 在查询 SQL 中
与 owner 一同过滤），非本人一律返回 404。
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.modules.commerce.models import (
    AddressSnapshot,
    AfterSale,
    AfterSaleMessage,
    Cart,
    CartItem,
    CheckoutSession,
    Installment,
    Invoice,
    Order,
    OrderAddressChange,
    OrderItem,
    Shipment,
)
from app.platform.ids import now_ms

OPEN_ORDER_STATUSES = ("PENDING_PAYMENT", "ACTIVE", "FULFILLING", "SHIPPED")
PAYABLE_INSTALLMENT_STATUSES = ("DUE", "PAYING", "CLOSING")


# ------------------------------------------------------------------ 购物车
def get_cart(session: Session, *, user_id: str) -> Cart | None:
    return session.scalars(sa.select(Cart).where(Cart.user_id == user_id)).one_or_none()


def get_or_create_cart(session: Session, *, user_id: str) -> Cart:
    cart = get_cart(session, user_id=user_id)
    if cart is not None:
        return cart
    cart = Cart(user_id=user_id)
    session.add(cart)
    session.flush()
    return cart


def get_cart_item(session: Session, *, cart_id: str, sku_id: str) -> CartItem | None:
    return session.scalars(
        sa.select(CartItem).where(CartItem.cart_id == cart_id, CartItem.sku_id == sku_id)
    ).one_or_none()


def list_cart_items(session: Session, *, cart_id: str) -> list[CartItem]:
    return list(
        session.scalars(sa.select(CartItem).where(CartItem.cart_id == cart_id).order_by(CartItem.created_at))
    )


# ------------------------------------------------------------------ 结算令牌
def get_checkout_session_by_hash(session: Session, *, token_hash: str) -> CheckoutSession | None:
    return session.scalars(
        sa.select(CheckoutSession).where(CheckoutSession.token_hash == token_hash)
    ).one_or_none()


def get_checkout_session(session: Session, *, checkout_id: str) -> CheckoutSession | None:
    return session.get(CheckoutSession, checkout_id)


# ------------------------------------------------------------------ 订单
def get_order(session: Session, *, order_id: str, user_id: str | None = None) -> Order | None:
    """按 ID 读订单；给定时同时过滤 owner，非本人等同不存在。"""
    order = session.get(Order, order_id)
    if order is None:
        return None
    if user_id is not None and order.user_id != user_id:
        return None
    return order


def get_order_by_no(session: Session, *, order_no: str) -> Order | None:
    return session.scalars(sa.select(Order).where(Order.order_no == order_no)).one_or_none()


def get_order_by_quote(session: Session, *, quote_id: str) -> Order | None:
    return session.scalars(sa.select(Order).where(Order.quote_id == quote_id)).one_or_none()


def order_by_idempotency_key(session: Session, *, user_id: str, key: str) -> Order | None:
    return session.scalars(
        sa.select(Order).where(Order.user_id == user_id, Order.idempotency_key == key)
    ).one_or_none()


def order_idempotency_key_exists(session: Session, *, key: str) -> bool:
    return (
        session.scalar(sa.select(sa.func.count()).select_from(Order).where(Order.idempotency_key == key))
        or 0
    ) > 0


def list_orders(
    session: Session,
    *,
    user_id: str,
    status: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[Order], int]:
    conditions = [Order.user_id == user_id]
    if status:
        conditions.append(Order.status == status)
    total = int(session.scalar(sa.select(sa.func.count()).select_from(Order).where(*conditions)) or 0)
    rows = list(
        session.scalars(
            sa.select(Order)
            .where(*conditions)
            .order_by(Order.created_at.desc())
            .limit(min(limit, 100))
            .offset(offset)
        )
    )
    return rows, total


def list_orders_by_status(
    session: Session, *, status: str, limit: int = 50, offset: int = 0
) -> list[Order]:
    return list(
        session.scalars(
            sa.select(Order)
            .where(Order.status == status)
            .order_by(Order.created_at)
            .limit(min(limit, 200))
            .offset(offset)
        )
    )


def count_orders_awaiting_payment(session: Session, *, user_id: str) -> int:
    return int(
        session.scalar(
            sa.select(sa.func.count())
            .select_from(Order)
            .where(Order.user_id == user_id, Order.status == "PENDING_PAYMENT")
        )
        or 0
    )


def count_active_orders_for_user(session: Session, *, user_id: str) -> int:
    """注销阻碍：未完结订单数量（10 文档）。"""
    return int(
        session.scalar(
            sa.select(sa.func.count())
            .select_from(Order)
            .where(Order.user_id == user_id, Order.status.in_(OPEN_ORDER_STATUSES))
        )
        or 0
    )


def count_orders_for_user(session: Session, *, user_id: str) -> int:
    return int(
        session.scalar(sa.select(sa.func.count()).select_from(Order).where(Order.user_id == user_id)) or 0
    )


def order_status_counts(session: Session, *, user_id: str) -> dict[str, int]:
    """本人订单按状态的计数。订单状态只有商务模块能解释，因此在这里聚合。"""
    rows = session.execute(
        sa.select(Order.status, sa.func.count())
        .select_from(Order)
        .where(Order.user_id == user_id)
        .group_by(Order.status)
    ).all()
    return {str(status): int(count) for status, count in rows}


def list_items(session: Session, *, order_id: str) -> list[OrderItem]:
    return list(
        session.scalars(sa.select(OrderItem).where(OrderItem.order_id == order_id).order_by(OrderItem.created_at))
    )


def get_order_item(session: Session, *, order_item_id: str, order_id: str | None = None) -> OrderItem | None:
    item = session.get(OrderItem, order_item_id)
    if item is None:
        return None
    if order_id is not None and item.order_id != order_id:
        return None
    return item


def list_installments(session: Session, *, order_id: str) -> list[Installment]:
    return list(
        session.scalars(
            sa.select(Installment)
            .where(Installment.order_id == order_id)
            .order_by(Installment.sequence, Installment.created_at)
        )
    )


def list_payable_installments(session: Session, *, order_id: str) -> list[Installment]:
    """可支付（未终结）的应付项：用于关单核对在途付款。"""
    return list(
        session.scalars(
            sa.select(Installment).where(
                Installment.order_id == order_id,
                Installment.status.in_(PAYABLE_INSTALLMENT_STATUSES),
            )
        )
    )


def get_installment(session: Session, *, installment_id: str) -> Installment | None:
    return session.get(Installment, installment_id)


def get_installment_by_change_request(session: Session, *, change_request_id: str) -> Installment | None:
    return session.scalars(
        sa.select(Installment).where(Installment.change_request_id == change_request_id)
    ).one_or_none()


# ------------------------------------------------------------------ 地址快照与变更
def get_address_snapshot(session: Session, *, snapshot_id: str) -> AddressSnapshot | None:
    return session.get(AddressSnapshot, snapshot_id)


def latest_address_snapshot(session: Session, *, order_id: str) -> AddressSnapshot | None:
    return session.scalars(
        sa.select(AddressSnapshot)
        .where(AddressSnapshot.order_id == order_id)
        .order_by(AddressSnapshot.created_at.desc())
    ).first()


def list_address_changes(session: Session, *, order_id: str) -> list[OrderAddressChange]:
    return list(
        session.scalars(
            sa.select(OrderAddressChange)
            .where(OrderAddressChange.order_id == order_id)
            .order_by(OrderAddressChange.created_at)
        )
    )


# ------------------------------------------------------------------ 物流与发票
def list_shipments(session: Session, *, order_id: str, kind: str | None = None) -> list[Shipment]:
    stmt = sa.select(Shipment).where(Shipment.order_id == order_id)
    if kind:
        stmt = stmt.where(Shipment.kind == kind)
    return list(session.scalars(stmt.order_by(Shipment.created_at)))


def get_shipment_by_tracking(
    session: Session, *, order_id: str, carrier: str, tracking_no: str
) -> Shipment | None:
    return session.scalars(
        sa.select(Shipment).where(
            Shipment.order_id == order_id,
            Shipment.carrier == carrier,
            Shipment.tracking_no == tracking_no,
        )
    ).first()


def list_invoices(session: Session, *, order_id: str) -> list[Invoice]:
    return list(
        session.scalars(sa.select(Invoice).where(Invoice.order_id == order_id).order_by(Invoice.created_at))
    )


def get_invoice(session: Session, *, invoice_id: str, user_id: str | None = None) -> Invoice | None:
    invoice = session.get(Invoice, invoice_id)
    if invoice is None:
        return None
    if user_id is not None and invoice.user_id != user_id:
        return None
    return invoice


def list_invoices_admin(
    session: Session, *, status: str | None = None, order_id: str | None = None, limit: int = 50
) -> tuple[list[Invoice], int]:
    """后台发票队列：默认已申请待签发的排在最前（运营按队列处理）。"""
    conditions = []
    if status:
        conditions.append(Invoice.status == status)
    if order_id:
        conditions.append(Invoice.order_id == order_id)
    total = int(
        session.scalar(sa.select(sa.func.count()).select_from(Invoice).where(*conditions)) or 0
    )
    rows = list(
        session.scalars(
            sa.select(Invoice)
            .where(*conditions)
            .order_by(
                sa.case((Invoice.status == "PENDING", 0), else_=1),
                Invoice.requested_at.desc(),
            )
            .limit(max(1, min(limit, 200)))
        )
    )
    return rows, total


# ------------------------------------------------------------------ 售后
def get_after_sale(session: Session, *, after_sale_id: str, user_id: str | None = None) -> AfterSale | None:
    row = session.get(AfterSale, after_sale_id)
    if row is None:
        return None
    if user_id is not None and row.user_id != user_id:
        return None
    return row


def open_after_sale_for_order(session: Session, *, order_id: str) -> AfterSale | None:
    return session.scalars(
        sa.select(AfterSale)
        .where(
            AfterSale.order_id == order_id,
            AfterSale.status.in_(("OPEN", "NEEDS_INFO", "APPROVED", "RETURNING")),
        )
        .order_by(AfterSale.created_at.desc())
    ).first()


def list_after_sales_for_order(session: Session, *, order_id: str) -> list[AfterSale]:
    return list(
        session.scalars(
            sa.select(AfterSale).where(AfterSale.order_id == order_id).order_by(AfterSale.created_at.desc())
        )
    )


def list_orders_admin(
    session: Session,
    *,
    status: str | None = None,
    user_id: str | None = None,
    kind: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[Order], int]:
    """运营后台的订单列表：可跨用户查询，只读。

    后台不按 owner 过滤是业务需要；调用方必须已通过 ``order.read`` 权限校验。
    """
    conditions = []
    if user_id:
        conditions.append(Order.user_id == user_id)
    if status:
        conditions.append(Order.status == status)
    if kind:
        conditions.append(Order.kind == kind)
    total = int(session.scalar(sa.select(sa.func.count()).select_from(Order).where(*conditions)) or 0)
    rows = list(
        session.scalars(
            sa.select(Order)
            .where(*conditions)
            .order_by(Order.created_at.desc())
            .limit(min(limit, 100))
            .offset(max(0, offset))
        )
    )
    return rows, total


def list_after_sales_admin(
    session: Session, *, status: str | None = None, limit: int = 20, offset: int = 0
) -> tuple[list[AfterSale], int]:
    """运营后台的售后列表：可跨用户查询，只读。"""
    conditions = []
    if status:
        conditions.append(AfterSale.status == status)
    total = int(
        session.scalar(sa.select(sa.func.count()).select_from(AfterSale).where(*conditions)) or 0
    )
    rows = list(
        session.scalars(
            sa.select(AfterSale)
            .where(*conditions)
            .order_by(AfterSale.created_at.desc())
            .limit(min(limit, 100))
            .offset(max(0, offset))
        )
    )
    return rows, total


def list_after_sales(
    session: Session, *, user_id: str, status: str | None = None, limit: int = 20, offset: int = 0
) -> tuple[list[AfterSale], int]:
    conditions = [AfterSale.user_id == user_id]
    if status:
        conditions.append(AfterSale.status == status)
    total = int(
        session.scalar(sa.select(sa.func.count()).select_from(AfterSale).where(*conditions)) or 0
    )
    rows = list(
        session.scalars(
            sa.select(AfterSale)
            .where(*conditions)
            .order_by(AfterSale.created_at.desc())
            .limit(min(limit, 100))
            .offset(offset)
        )
    )
    return rows, total


def count_open_after_sales_for_user(session: Session, *, user_id: str) -> int:
    """注销阻碍：处理中的售后数量（10 文档）。"""
    return int(
        session.scalar(
            sa.select(sa.func.count())
            .select_from(AfterSale)
            .where(
                AfterSale.user_id == user_id,
                AfterSale.status.in_(("OPEN", "NEEDS_INFO", "APPROVED", "RETURNING")),
            )
        )
        or 0
    )


def list_after_sale_messages(session: Session, *, after_sale_id: str) -> list[AfterSaleMessage]:
    return list(
        session.scalars(
            sa.select(AfterSaleMessage)
            .where(AfterSaleMessage.after_sale_id == after_sale_id)
            .order_by(AfterSaleMessage.created_at)
        )
    )


def has_open_after_sales(session: Session, *, order_id: str) -> bool:
    return (
        session.scalar(
            sa.select(sa.func.count())
            .select_from(AfterSale)
            .where(
                AfterSale.order_id == order_id,
                AfterSale.status.in_(("OPEN", "NEEDS_INFO", "APPROVED", "RETURNING")),
            )
        )
        or 0
    ) > 0


# ------------------------------------------------------------------ 运营统计
def admin_order_counts(session: Session) -> dict[str, int]:
    awaiting = int(
        session.scalar(
            sa.select(sa.func.count())
            .select_from(Order)
            .where(Order.status == "PENDING_PAYMENT")
        )
        or 0
    )
    fulfilling = int(
        session.scalar(
            sa.select(sa.func.count())
            .select_from(Order)
            .where(Order.status.in_(("ACTIVE", "FULFILLING")))
        )
        or 0
    )
    open_after_sales = int(
        session.scalar(
            sa.select(sa.func.count())
            .select_from(AfterSale)
            .where(AfterSale.status.in_(("OPEN", "NEEDS_INFO", "APPROVED", "RETURNING")))
        )
        or 0
    )
    pending_invoices = int(
        session.scalar(
            sa.select(sa.func.count()).select_from(Invoice).where(Invoice.status == "PENDING")
        )
        or 0
    )
    return {
        "orders_awaiting_payment": awaiting,
        "orders_fulfilling": fulfilling,
        "after_sales_open": open_after_sales,
        "invoices_pending": pending_invoices,
    }


def count_overdue_orders(session: Session, *, moment: int | None = None) -> int:
    """已到期但仍待付款的订单数量，供任务扫描。"""
    now = moment if moment is not None else now_ms()
    return int(
        session.scalar(
            sa.select(sa.func.count())
            .select_from(Order)
            .where(
                Order.status == "PENDING_PAYMENT",
                Order.expires_at.is_not(None),
                Order.expires_at <= now,
            )
        )
        or 0
    )
