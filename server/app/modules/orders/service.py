"""订单查询模块用例：列表（筛选/分页/待付款提醒）与详情读模型。"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.modules.commerce import ports as commerce_ports
from app.modules.orders import repository as repo
from app.platform.errors import AppError


def list_orders(
    session: Session,
    *,
    user_id: str,
    status: str | None = None,
    page: int = 1,
    limit: int = 20,
) -> dict[str, Any]:
    """本人订单列表。``page`` 为 1 起的页码，与静态内容分页约定一致。"""
    if status is not None and status not in repo.ORDER_STATUSES:
        raise AppError(
            "VALIDATION_ERROR", "订单状态筛选值不合法。", fields={"status": "非法状态"}
        )
    offset = max(0, (page - 1) * limit)
    rows, total = repo.list_orders(
        session, user_id=user_id, status=status, limit=limit, offset=offset
    )
    items = [commerce_ports.order_summary_payload(session, row) for row in rows]
    has_more = offset + len(items) < total
    return {
        "items": items,
        "next_cursor": str(offset + limit) if has_more else None,
        "has_more": has_more,
        "pending_actions": {
            "orders_awaiting_payment": repo.count_awaiting_payment(session, user_id=user_id)
        },
    }


def order_detail(session: Session, *, user_id: str, order_id: str) -> dict[str, Any]:
    """订单详情：明细与快照、款项、支付、取消、物流、退款与发票。"""
    order = repo.get_order(session, user_id=user_id, order_id=order_id)
    if order is None:
        raise AppError("RESOURCE_NOT_FOUND")
    return commerce_ports.order_detail_payload(session, order)


def order_status_counts(session: Session, *, user_id: str) -> dict[str, int]:
    """各状态订单数量，用于账户页标签角标。

    计数由商务模块聚合（订单状态只有它能解释），本模块只补齐缺省为 0 的枚举项。
    """
    counts = {status: 0 for status in repo.ORDER_STATUSES}
    for status, count in commerce_ports.order_status_counts(session, user_id=user_id).items():
        if status in counts:
            counts[status] = int(count)
    return counts


__all__ = ["list_orders", "order_detail", "order_status_counts"]
