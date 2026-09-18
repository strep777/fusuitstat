"""订单查询模块的读模型查询。

订单表由商务模块拥有（04 文档：一个表只有一个拥有者）；本模块只做面向客户端的
只读查询，因此通过 ``app.modules.commerce.repository`` 读取，不复制归属判断逻辑。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.modules.commerce import ports as commerce_ports

#: 列表筛选允许的状态值（与 08 文档订单状态一致）。
ORDER_STATUSES = (
    "PENDING_PAYMENT",
    "ACTIVE",
    "FULFILLING",
    "SHIPPED",
    "COMPLETED",
    "CANCELLED",
)

#: 待付款提醒涉及的订单状态。
AWAITING_PAYMENT_STATUSES = ("PENDING_PAYMENT",)


def list_orders(
    session: Session,
    *,
    user_id: str,
    status: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[Any], int]:
    return commerce_ports.list_orders(
        session, user_id=user_id, status=status, limit=limit, offset=offset
    )


def get_order(session: Session, *, user_id: str, order_id: str) -> Any | None:
    """按 ID 读订单，同时过滤 owner；非本人等同不存在。"""
    return commerce_ports.get_order(session, order_id=order_id, user_id=user_id)


def count_awaiting_payment(session: Session, *, user_id: str) -> int:
    """待付款提醒数量：只统计本人待付款订单。"""
    return commerce_ports.count_orders_awaiting_payment(session, user_id=user_id)


def count_active_orders(session: Session, *, user_id: str) -> int:
    return commerce_ports.count_active_orders_for_user(session, user_id=user_id)


__all__ = [
    "AWAITING_PAYMENT_STATUSES",
    "ORDER_STATUSES",
    "count_active_orders",
    "count_awaiting_payment",
    "get_order",
    "list_orders",
]
