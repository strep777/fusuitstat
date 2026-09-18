"""商品目录模块对外端口。

其他模块**不得**导入本模块的 ORM 模型或 repository（04 文档）；
需要目录数据或库存动作时只经这里。库存争用一律由本模块用条件 UPDATE 裁定，
调用方只按返回结果分支。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

__all__ = [
    "active_reservations_for_order",
    "application_snapshot",
    "approved_application_for",
    "consume",
    "get_character_by_product",
    "get_product",
    "get_sku",
    "list_products",
    "list_skus",
    "mark_application_converted",
    "mark_character_adopted",
    "product_payload",
    "release",
    "release_character",
    "reserve",
    "reserve_exclusive_character",
    "restock",
]


# ------------------------------------------------------------------ 只读
def list_products(
    session: Session,
    *,
    kind: str | None = None,
    category: str | None = None,
    query: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[Any], int]:
    """已发布商品列表与总数（只给公开字段，不含库存明细）。"""
    from app.modules.catalog import service as catalog_service

    return catalog_service.list_products(
        session, kind=kind, category=category, query=query, limit=limit, offset=offset
    )


def product_payload(
    session: Session, product: Any, *, include_skus: bool = True
) -> dict[str, Any]:
    """商品公开载荷。SKU 价格与可售状态由服务端裁定。"""
    from app.modules.catalog import service as catalog_service

    return catalog_service.product_payload(session, product, include_skus=include_skus)


def list_skus(session: Session, *, product_id: str, active_only: bool = True) -> list[Any]:
    """某商品的 SKU 行（只读，供组装公开载荷）。"""
    from app.modules.catalog import repository as catalog_repo

    return catalog_repo.list_skus(session, product_id=product_id, active_only=active_only)


def get_sku(session: Session, sku_id: str) -> Any:
    from app.modules.catalog import repository as catalog_repo

    return catalog_repo.get_sku(session, sku_id)


def get_product(session: Session, product_id: str) -> Any:
    from app.modules.catalog import repository as catalog_repo

    return catalog_repo.get_product(session, product_id)


def get_character_by_product(session: Session, product_id: str) -> Any:
    """独占角色（领养类商品）。角色归属目录模块，调用方只拿编号与状态。"""
    from app.modules.catalog import repository as catalog_repo

    return catalog_repo.get_character_by_product(session, product_id)


def active_reservations_for_order(session: Session, *, order_id: str) -> list[Any]:
    from app.modules.catalog import repository as catalog_repo

    return catalog_repo.active_reservations_for_order(session, order_id=order_id)


def approved_application_for(session: Session, *, user_id: str, product_id: str) -> Any:
    """该用户对该商品已审批通过且未过期的掉落申请（购买优先级，C28）。"""
    from app.modules.catalog import service as catalog_service

    return catalog_service.approved_application_for(session, user_id=user_id, product_id=product_id)


def application_snapshot(session: Session, *, user_id: str, application_id: str) -> dict[str, Any] | None:
    """本人某条申请的事实快照（C28「服务先申请再报价」的入站端口）。

    只返回判定所需的字段，不把申请模块的 ORM 对象交给其他模块。
    非本人返回 ``None``（调用方按 404 处理，不泄露是否存在）。
    """
    from app.modules.catalog import repository as catalog_repo

    row = catalog_repo.get_application(session, application_id)
    if row is None or row.user_id != user_id:
        return None
    return {
        "id": row.id,
        "user_id": row.user_id,
        "kind": row.kind,
        "status": row.status,
        "product_id": row.product_id,
        "priority_rank": row.priority_rank,
        "purchase_expires_at": row.purchase_expires_at,
        "converted_order_id": row.converted_order_id,
    }


# ------------------------------------------------------------------ 库存动作
def reserve(
    session: Session,
    *,
    sku_id: str,
    quantity: int,
    order_id: str,
    order_item_id: str,
    source_type: str = "order",
    expires_at: int | None = None,
    reason: str | None = None,
) -> Any:
    """预留库存（条件 UPDATE，防超卖）；不足时抛 ``STOCK_NOT_ENOUGH``。"""
    from app.modules.catalog import service as catalog_service

    return catalog_service.reserve(
        session,
        sku_id=sku_id,
        quantity=quantity,
        order_id=order_id,
        order_item_id=order_item_id,
        source_type=source_type,
        expires_at=expires_at,
        reason=reason,
    )


def reserve_exclusive_character(session: Session, *, character_id: str, order_id: str) -> None:
    """预留独占角色（同一角色只能被一个订单占用）。"""
    from app.modules.catalog import service as catalog_service

    catalog_service.reserve_exclusive_character(
        session, character_id=character_id, order_id=order_id
    )


def release(session: Session, *, reservation_id: str, reason: str) -> None:
    """释放预留（取消/关单）。与支付成功用条件更新竞争。"""
    from app.modules.catalog import service as catalog_service

    catalog_service.release(session, reservation_id=reservation_id, reason=reason)


def consume(session: Session, *, reservation_id: str, reason: str | None = None) -> None:
    """核销预留（收款确认）。"""
    from app.modules.catalog import service as catalog_service

    catalog_service.consume(session, reservation_id=reservation_id, reason=reason)


def restock(
    session: Session, *, sku_id: str, quantity: int, reason: str, source_id: str
) -> None:
    """补库存。售后退款不自动调用本函数（08 文档第 5 条）。"""
    from app.modules.catalog import service as catalog_service

    catalog_service.restock(
        session, sku_id=sku_id, quantity=quantity, reason=reason, source_id=source_id
    )


def release_character(session: Session, *, character_id: str, reason: str) -> None:
    from app.modules.catalog import service as catalog_service

    catalog_service.release_character(session, character_id=character_id, reason=reason)


def mark_character_adopted(session: Session, *, character_id: str, order_id: str) -> None:
    from app.modules.catalog import service as catalog_service

    catalog_service.mark_character_adopted(session, character_id=character_id, order_id=order_id)


def mark_application_converted(session: Session, *, application_id: str, order_id: str) -> None:
    """把掉落的申请标记为已转订单（审批购买优先级，C28）。"""
    from app.modules.catalog import service as catalog_service

    catalog_service.mark_application_converted(
        session, application_id=application_id, order_id=order_id
    )
