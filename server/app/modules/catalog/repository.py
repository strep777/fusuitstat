"""目录模块查询。"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.modules.catalog.models import (
    Character,
    InventoryMovement,
    InventoryReservation,
    Product,
    Sku,
    StockApplication,
)
from app.platform.ids import now_ms


# ------------------------------------------------------------------ 商品
def get_product(session: Session, product_id: str) -> Product | None:
    return session.get(Product, product_id)


def get_product_by_slug(
    session: Session, slug: str, *, published_only: bool = True
) -> Product | None:
    stmt = sa.select(Product).where(Product.slug == slug)
    if published_only:
        stmt = stmt.where(Product.status == "published")
    return session.scalars(stmt).one_or_none()


def list_products(
    session: Session,
    *,
    kind: str | None = None,
    category: str | None = None,
    series_code: str | None = None,
    query: str | None = None,
    published_only: bool = True,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[Product], int]:
    conditions = []
    if published_only:
        conditions.append(Product.status == "published")
    if kind:
        conditions.append(Product.kind == kind)
    if category:
        conditions.append(Product.category == category)
    if series_code:
        conditions.append(Product.series_code == series_code)
    if query:
        like = f"%{query}%"
        conditions.append(sa.or_(Product.title.like(like), Product.subtitle.like(like)))
    count = int(
        session.scalar(sa.select(sa.func.count()).select_from(Product).where(*conditions)) or 0
    )
    rows = list(
        session.scalars(
            sa.select(Product)
            .where(*conditions)
            .order_by(Product.sort_order, Product.created_at.desc())
            .limit(min(limit, 100))
            .offset(offset)
        )
    )
    return rows, count


def product_categories(session: Session) -> list[tuple[str, int]]:
    rows = session.execute(
        sa.select(Product.category, sa.func.count())
        .where(Product.status == "published", Product.category.is_not(None))
        .group_by(Product.category)
        .order_by(sa.func.count().desc())
    ).all()
    return [(str(row[0]), int(row[1])) for row in rows if row[0]]


def list_skus(session: Session, *, product_id: str, active_only: bool = True) -> list[Sku]:
    stmt = sa.select(Sku).where(Sku.product_id == product_id)
    if active_only:
        stmt = stmt.where(Sku.is_active == 1)
    return list(session.scalars(stmt.order_by(Sku.sort_order, Sku.price_cents)))


def get_sku(session: Session, sku_id: str) -> Sku | None:
    return session.get(Sku, sku_id)


def get_sku_by_code(session: Session, code: str) -> Sku | None:
    return session.scalars(sa.select(Sku).where(Sku.sku_code == code)).one_or_none()


def list_sku_ids_for_products(session: Session, *, product_ids: list[str]) -> list[Sku]:
    if not product_ids:
        return []
    return list(session.scalars(sa.select(Sku).where(Sku.product_id.in_(product_ids))))


def low_stock_skus(session: Session, *, threshold: int = 1, limit: int = 20) -> list[Sku]:
    return list(
        session.scalars(
            sa.select(Sku)
            .where(Sku.is_active == 1, Sku.stock_on_hand - Sku.reserved <= threshold)
            .order_by(Sku.stock_on_hand - Sku.reserved)
            .limit(limit)
        )
    )


# ------------------------------------------------------------------ 角色
def get_character(session: Session, character_id: str) -> Character | None:
    return session.get(Character, character_id)


def get_character_by_slug(session: Session, slug: str) -> Character | None:
    return session.scalars(sa.select(Character).where(Character.slug == slug)).one_or_none()


def get_character_by_product(session: Session, product_id: str) -> Character | None:
    return session.scalars(sa.select(Character).where(Character.product_id == product_id)).one_or_none()


def list_characters(
    session: Session,
    *,
    status: str | None = None,
    species: str | None = None,
    query: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[Character], int]:
    conditions = [Character.display_authorized == 1]
    if status:
        conditions.append(Character.public_status == status)
    if species:
        conditions.append(Character.species == species)
    if query:
        like = f"%{query}%"
        conditions.append(sa.or_(Character.name.like(like), Character.story.like(like)))
    count = int(
        session.scalar(sa.select(sa.func.count()).select_from(Character).where(*conditions)) or 0
    )
    rows = list(
        session.scalars(
            sa.select(Character)
            .where(*conditions)
            .order_by(Character.public_status, Character.created_at.desc())
            .limit(min(limit, 100))
            .offset(offset)
        )
    )
    return rows, count


def character_species_facets(session: Session) -> list[tuple[str, int]]:
    rows = session.execute(
        sa.select(Character.species, sa.func.count())
        .where(Character.display_authorized == 1, Character.species != "")
        .group_by(Character.species)
        .order_by(sa.func.count().desc())
    ).all()
    return [(str(row[0]), int(row[1])) for row in rows]


# ------------------------------------------------------------------ 库存
def get_reservation(session: Session, reservation_id: str) -> InventoryReservation | None:
    return session.get(InventoryReservation, reservation_id)


def active_reservations_for_order(session: Session, *, order_id: str) -> list[InventoryReservation]:
    return list(
        session.scalars(
            sa.select(InventoryReservation).where(
                InventoryReservation.order_id == order_id,
                InventoryReservation.state == "ACTIVE",
            )
        )
    )


def reservations_for_order(session: Session, *, order_id: str) -> list[InventoryReservation]:
    return list(
        session.scalars(
            sa.select(InventoryReservation).where(InventoryReservation.order_id == order_id)
        )
    )


def movements_for_sku(session: Session, *, sku_id: str, limit: int = 50) -> list[InventoryMovement]:
    return list(
        session.scalars(
            sa.select(InventoryMovement)
            .where(InventoryMovement.sku_id == sku_id)
            .order_by(InventoryMovement.created_at.desc())
            .limit(min(limit, 200))
        )
    )


# ------------------------------------------------------------------ 申请
def get_application(session: Session, application_id: str) -> StockApplication | None:
    return session.get(StockApplication, application_id)


def pending_application(session: Session, *, user_id: str, product_id: str) -> StockApplication | None:
    return session.scalars(
        sa.select(StockApplication).where(
            StockApplication.user_id == user_id,
            StockApplication.product_id == product_id,
            StockApplication.status.in_(("SUBMITTED", "NEEDS_INFO", "APPROVED")),
        )
    ).first()


def latest_approved_application(
    session: Session, *, user_id: str, product_id: str
) -> StockApplication | None:
    return session.scalars(
        sa.select(StockApplication)
        .where(
            StockApplication.user_id == user_id,
            StockApplication.product_id == product_id,
            StockApplication.status == "APPROVED",
        )
        .order_by(StockApplication.decided_at.desc())
    ).first()


def list_applications_for_user(
    session: Session,
    *,
    user_id: str,
    status: str | None = None,
    product_id: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[StockApplication]:
    stmt = sa.select(StockApplication).where(StockApplication.user_id == user_id)
    if status:
        stmt = stmt.where(StockApplication.status == status)
    if product_id:
        # 商品详情页只关心本商品的审批状态；不筛选会让「只看前 N 条」漏掉目标商品。
        stmt = stmt.where(StockApplication.product_id == product_id)
    return list(
        session.scalars(
            stmt.order_by(StockApplication.created_at.desc()).limit(min(limit, 100)).offset(offset)
        )
    )


def list_applications_admin(
    session: Session, *, status: str | None = None, product_id: str | None = None, limit: int = 20, offset: int = 0
) -> tuple[list[StockApplication], int]:
    conditions = []
    if status:
        conditions.append(StockApplication.status == status)
    if product_id:
        conditions.append(StockApplication.product_id == product_id)
    count = int(
        session.scalar(
            sa.select(sa.func.count()).select_from(StockApplication).where(*conditions)
        )
        or 0
    )
    rows = list(
        session.scalars(
            sa.select(StockApplication)
            .where(*conditions)
            .order_by(StockApplication.created_at.desc())
            .limit(min(limit, 100))
            .offset(offset)
        )
    )
    return rows, count


# ------------------------------------------------------------------ 素材引用
def product_image_ids(session: Session, *, product_id: str) -> list[str]:
    """商品图素材编号。素材归属媒体模块，这里只经端口取编号（04 文档）。"""
    from app.modules.media.ports import linked_asset_ids

    return linked_asset_ids(session, resource_type="product", resource_id=product_id)


def count_products(session: Session) -> int:
    return int(session.scalar(sa.select(sa.func.count()).select_from(Product)) or 0)


def stale_reservation_count(session: Session) -> int:
    return int(
        session.scalar(
            sa.select(sa.func.count())
            .select_from(InventoryReservation)
            .where(
                InventoryReservation.state == "ACTIVE",
                InventoryReservation.expires_at.is_not(None),
                InventoryReservation.expires_at <= now_ms(),
            )
        )
        or 0
    )
