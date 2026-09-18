"""商务模块对外端口。

其他模块（委托、支付、积分、运营后台）只通过本文件与 ``app.modules.contracts``
声明的 DTO 协作，不直接导入商务模块的 ORM 表或 repository（04 文档）。

签名是跨模块契约的一部分，不可修改：

- ``create_stock_order`` / ``create_commission_order``：建立订单；
- ``create_change_installment``：为已确认变更建立加项应付项，返回 installment_id；
- ``order_reward_snapshot``：积分模块使用的权威净额快照（运费不计奖）；
- ``on_installment_paid`` / ``on_refund_succeeded``：支付模块事件消费者入口。
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.modules.commerce import service as svc
from app.modules.contracts import CreatedOrder


def create_stock_order(
    session: Session,
    *,
    user_id: str,
    checkout_token: str,
    address_id: str,
    terms_version: str,
    accepted: bool,
    idempotency_key: str,
) -> CreatedOrder:
    """结算令牌换订单：同一事务内校验、预留、建单。"""
    return svc.create_order(
        session,
        user_id=user_id,
        checkout_token=checkout_token,
        address_id=address_id,
        terms_version=terms_version,
        accepted=accepted,
        idempotency_key=idempotency_key,
    )


def create_commission_order(
    session: Session,
    *,
    user_id: str,
    quote_id: str,
    quote_version: int,
    total_cents: int,
    prepayment_cents: int,
    currency: str,
    terms_version: str,
    inquiry_id: str,
    slot_reservation_id: str | None,
    idempotency_key: str,
) -> CreatedOrder:
    """接受报价后建立唯一的委托订单（一笔报价只能一个订单）。"""
    return svc.create_commission_order(
        session,
        user_id=user_id,
        quote_id=quote_id,
        quote_version=quote_version,
        total_cents=total_cents,
        prepayment_cents=prepayment_cents,
        currency=currency,
        terms_version=terms_version,
        inquiry_id=inquiry_id,
        slot_reservation_id=slot_reservation_id,
        idempotency_key=idempotency_key,
    )


def create_change_installment(
    session: Session,
    *,
    order_id: str,
    amount_cents: int,
    label: str,
    change_request_id: str,
) -> str:
    """为已确认的变更请求建立加项应付项，返回 ``installment_id``。"""
    return svc.create_change_installment(
        session,
        order_id=order_id,
        amount_cents=amount_cents,
        label=label,
        change_request_id=change_request_id,
    )


def order_reward_snapshot(session: Session, order_id: str) -> dict[str, Any]:
    """订单的权威发奖快照；``eligible_net_cents`` 不含运费。"""
    return svc.order_reward_snapshot(session, order_id)


def on_installment_paid(session: Session, *, order_id: str, installment_id: str) -> dict[str, Any]:
    """收款成功后的库存核销与状态推进（幂等）。"""
    return svc.on_installment_paid(session, order_id=order_id, installment_id=installment_id)


def on_refund_succeeded(
    session: Session, *, order_id: str, refund_id: str, goods_cents: int, shipping_cents: int
) -> dict[str, Any]:
    """退款成功后的汇总更新与积分重算事件；不自动补库存。"""
    return svc.on_refund_succeeded(
        session,
        order_id=order_id,
        refund_id=refund_id,
        goods_cents=goods_cents,
        shipping_cents=shipping_cents,
    )


def effective_delivery_address(session: Session, *, order_id: str) -> dict[str, Any] | None:
    """当前生效的履约地址（原始快照 + 最后一次变更），供履约/物流模块读取。"""
    return svc.effective_delivery_address(session, order_id=order_id)


def expire_due_orders(session: Session, *, now: int | None = None, limit: int = 20) -> int:
    """定时任务入口：把到期未付款订单推进到关闭核对流程，返回处理数量。"""
    return svc.expire_due_orders(session, now=now, limit=limit)


def count_orders_awaiting_payment(session: Session, *, user_id: str) -> int:
    """本人待付款订单数（账户待办提示）。计数属商务模块自己的数据。"""
    from app.modules.commerce import repository as repo

    return repo.count_orders_awaiting_payment(session, user_id=user_id)


def count_open_after_sales_for_user(session: Session, *, user_id: str) -> int:
    """本人处理中的售后数量（注销阻碍提示，10 文档）。"""
    from app.modules.commerce import repository as repo

    return repo.count_open_after_sales_for_user(session, user_id=user_id)


def count_active_orders_for_user(session: Session, *, user_id: str) -> int:
    """本人未完结订单数（注销阻碍提示，10 文档）。"""
    from app.modules.commerce import repository as repo

    return repo.count_active_orders_for_user(session, user_id=user_id)


# ------------------------------------------------------------------ 订单只读视图
def list_orders(
    session: Session, *, user_id: str, status: str | None = None, limit: int = 20, offset: int = 0
) -> tuple[list[Any], int]:
    """本人订单列表与总数（逐对象归属过滤）。"""
    from app.modules.commerce import repository as repo

    return repo.list_orders(session, user_id=user_id, status=status, limit=limit, offset=offset)


def get_order(session: Session, *, order_id: str, user_id: str | None = None) -> Any:
    """按编号取订单；给了 ``user_id`` 就同时做归属过滤（非本人返回 ``None``）。"""
    from app.modules.commerce import repository as repo

    return repo.get_order(session, order_id=order_id, user_id=user_id)


def get_installment(session: Session, *, installment_id: str) -> Any:
    """按编号取应付项（支付模块通过桥接只读调用）。"""
    from app.modules.commerce import repository as repo

    return repo.get_installment(session, installment_id=installment_id)


def list_installments_for_order(session: Session, *, order_id: str) -> list[Any]:
    from app.modules.commerce import repository as repo

    return repo.list_installments(session, order_id=order_id)


def order_status_counts(session: Session, *, user_id: str) -> dict[str, int]:
    """本人订单按状态的计数（账户概览）。"""
    from app.modules.commerce import repository as repo

    return repo.order_status_counts(session, user_id=user_id)


def order_summary_payload(session: Session, order: Any) -> dict[str, Any]:
    """订单摘要载荷（订单列表行）。"""
    from app.modules.commerce import service as svc

    return svc.order_summary_payload(session, order)


def order_detail_payload(session: Session, order: Any) -> dict[str, Any]:
    """订单详情载荷（含应付项与物流；不下发支付密钥）。"""
    from app.modules.commerce import service as svc

    return svc.order_detail_payload(session, order)


__all__ = [
    "count_active_orders_for_user",
    "count_open_after_sales_for_user",
    "count_orders_awaiting_payment",
    "create_change_installment",
    "create_commission_order",
    "create_stock_order",
    "effective_delivery_address",
    "expire_due_orders",
    "get_installment",
    "get_order",
    "list_installments_for_order",
    "list_orders",
    "on_installment_paid",
    "on_refund_succeeded",
    "order_detail_payload",
    "order_reward_snapshot",
    "order_status_counts",
    "order_summary_payload",
    "purge_customer_cart",
]


def purge_customer_cart(session: Session, *, user_id: str) -> int:
    """注销清理：删除本人购物车与条目（可删除资料）。返回删除的条目数。"""
    from app.modules.commerce import repository as commerce_repo

    cart = commerce_repo.get_cart(session, user_id=user_id)
    if cart is None:
        return 0
    items = commerce_repo.list_cart_items(session, cart_id=cart.id)
    for row in items:
        session.delete(row)
    session.delete(cart)
    session.flush()
    return len(items)


def customer_retained_counts(session: Session, *, user_id: str) -> dict[str, int]:
    """注销清理时依法隔离保留的数据计数（订单、付款与发票不做删除）。"""
    from app.modules.commerce.models import Invoice, Order, OrderAddressChange, Shipment

    def _count(model: Any, **conditions: Any) -> int:
        stmt = sa.select(sa.func.count()).select_from(model)
        for column, value in conditions.items():
            stmt = stmt.where(getattr(model, column) == value)
        return int(session.scalar(stmt) or 0)

    orders = _count(Order, user_id=user_id)
    order_ids = list(session.scalars(sa.select(Order.id).where(Order.user_id == user_id)))
    invoices = 0
    shipments = 0
    address_changes = 0
    if order_ids:
        invoices = int(
            session.scalar(
                sa.select(sa.func.count()).select_from(Invoice).where(Invoice.order_id.in_(order_ids))
            )
            or 0
        )
        shipments = int(
            session.scalar(
                sa.select(sa.func.count()).select_from(Shipment).where(Shipment.order_id.in_(order_ids))
            )
            or 0
        )
        address_changes = int(
            session.scalar(
                sa.select(sa.func.count())
                .select_from(OrderAddressChange)
                .where(OrderAddressChange.order_id.in_(order_ids))
            )
            or 0
        )
    return {
        "orders": orders,
        "invoices": invoices,
        "shipments": shipments,
        "address_changes": address_changes,
    }
