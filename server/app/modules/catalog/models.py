"""目录模块：商品、SKU、角色、库存流水与掉落申请审批。

拥有的数据：products、skus、characters、inventory_reservations、inventory_movements、
stock_applications。
不读订单表（04 文档）；对外通过 ``ports.py`` 暴露库存操作。
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.ids import new_id, now_ms
from app.platform.orm import Base, PkMixin, TimestampMixin, VersionMixin

PRODUCT_KINDS = ("stock_goods", "exclusive_character", "commission", "service")
PRODUCT_STATUSES = ("draft", "published", "archived")
RESERVATION_STATES = ("ACTIVE", "CONSUMED", "RELEASED", "EXPIRED")
MOVEMENT_ACTIONS = ("RESERVE", "RELEASE", "CONSUME", "RESTOCK", "ADJUST", "RETURN_IN")
APPLICATION_STATUSES = ("SUBMITTED", "NEEDS_INFO", "APPROVED", "REJECTED", "CONVERTED", "EXPIRED")
CHARACTER_STATES = ("available", "reserved", "adopted", "kept")
SERVICE_KINDS = ("cleaning", "repair", "accessory", "fitting")


class Product(PkMixin, TimestampMixin, VersionMixin, Base):
    __tablename__ = "products"
    id_kind = "product"

    slug: Mapped[str] = mapped_column(sa.String(120), nullable=False, unique=True)
    kind: Mapped[str] = mapped_column(sa.String(30), nullable=False, default="stock_goods")
    title: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    subtitle: Mapped[str] = mapped_column(sa.String(200), nullable=False, default="")
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False, default="draft")
    description: Mapped[str] = mapped_column(sa.Text, nullable=False, default="")
    highlights: Mapped[list[Any] | None] = mapped_column(sa.JSON)
    terms_version: Mapped[str] = mapped_column(sa.String(40), nullable=False, default="shop-1")
    category: Mapped[str | None] = mapped_column(sa.String(40))
    series_code: Mapped[str | None] = mapped_column(sa.String(40))
    requires_approval: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    shipping_template: Mapped[str] = mapped_column(sa.String(40), nullable=False, default="default")
    service_kind: Mapped[str | None] = mapped_column(sa.String(30))
    sort_order: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    seo_description: Mapped[str | None] = mapped_column(sa.String(300))

    __table_args__ = (
        sa.CheckConstraint(
            "kind IN ('stock_goods','exclusive_character','commission','service')",
            name="ck_products_kind",
        ),
        sa.CheckConstraint("status IN ('draft','published','archived')", name="ck_products_status"),
        sa.CheckConstraint("requires_approval IN (0,1)", name="ck_products_approval"),
        sa.Index("ix_products_public", "status", "kind", "sort_order"),
    )


class Sku(PkMixin, TimestampMixin, VersionMixin, Base):
    """SKU 与库存。``0 <= reserved <= stock_on_hand`` 由 CHECK 保证。"""

    __tablename__ = "skus"
    id_kind = "sku"

    product_id: Mapped[str] = mapped_column(
        sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sku_code: Mapped[str] = mapped_column(sa.String(60), nullable=False, unique=True)
    label: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    attributes: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON)
    price_cents: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(sa.String(3), nullable=False, default="CNY")
    compare_at_cents: Mapped[int | None] = mapped_column(sa.Integer)
    stock_on_hand: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    reserved: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    max_per_order: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=5)
    weight_grams: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    is_active: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=1)
    cover_asset_id: Mapped[str | None] = mapped_column(sa.String(48))
    sort_order: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)

    __table_args__ = (
        sa.CheckConstraint("price_cents >= 0", name="ck_skus_price_nonneg"),
        sa.CheckConstraint("stock_on_hand >= 0 AND reserved >= 0", name="ck_skus_stock_nonneg"),
        sa.CheckConstraint("reserved <= stock_on_hand", name="ck_skus_reserved_le_stock"),
        sa.CheckConstraint("max_per_order >= 1", name="ck_skus_max_per_order"),
        sa.CheckConstraint("is_active IN (0,1)", name="ck_skus_active"),
    )


class Character(PkMixin, TimestampMixin, VersionMixin, Base):
    """可领养角色。独占角色只能绑定一个可售 SKU（FR-004）。"""

    __tablename__ = "characters"
    id_kind = "character"

    product_id: Mapped[str] = mapped_column(
        sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    slug: Mapped[str] = mapped_column(sa.String(120), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    species: Mapped[str] = mapped_column(sa.String(60), nullable=False, default="")
    designer_credit: Mapped[str | None] = mapped_column(sa.String(120))
    story: Mapped[str] = mapped_column(sa.Text, nullable=False, default="")
    rights_summary: Mapped[str] = mapped_column(sa.Text, nullable=False, default="")
    public_status: Mapped[str] = mapped_column(sa.String(16), nullable=False, default="available")
    exclusive: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=1)
    adoption_note: Mapped[str | None] = mapped_column(sa.String(400))
    cover_asset_id: Mapped[str | None] = mapped_column(sa.String(48))
    tags: Mapped[list[Any] | None] = mapped_column(sa.JSON)
    display_authorized: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=1)

    __table_args__ = (
        sa.CheckConstraint(
            "public_status IN ('available','reserved','adopted','kept')", name="ck_characters_status"
        ),
        sa.CheckConstraint("exclusive IN (0,1)", name="ck_characters_exclusive"),
        sa.CheckConstraint("display_authorized IN (0,1)", name="ck_characters_display_auth"),
        sa.Index("ix_characters_public", "public_status", "species"),
    )


class InventoryReservation(PkMixin, TimestampMixin, Base):
    """库存预留。订单行唯一活动预留；qty>0。"""

    __tablename__ = "inventory_reservations"
    id_kind = "sku"

    order_item_id: Mapped[str] = mapped_column(sa.String(48), nullable=False, index=True)
    order_id: Mapped[str] = mapped_column(sa.String(48), nullable=False, index=True)
    sku_id: Mapped[str] = mapped_column(
        sa.ForeignKey("skus.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    quantity: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    state: Mapped[str] = mapped_column(sa.String(16), nullable=False, default="ACTIVE")
    expires_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    released_reason: Mapped[str | None] = mapped_column(sa.String(120))

    __table_args__ = (
        sa.CheckConstraint("quantity > 0", name="ck_reservation_qty"),
        sa.CheckConstraint("state IN ('ACTIVE','CONSUMED','RELEASED','EXPIRED')", name="ck_reservation_state"),
        sa.Index("uq_active_reservation", "order_item_id", "state", unique=True),
    )


class InventoryMovement(PkMixin, Base):
    """库存流水，只追加。``UNIQUE(source_type,source_id,sku_id,action)`` 防重复。"""

    __tablename__ = "inventory_movements"

    sku_id: Mapped[str] = mapped_column(
        sa.ForeignKey("skus.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    action: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    delta_on_hand: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    delta_reserved: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    source_type: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    source_id: Mapped[str] = mapped_column(sa.String(48), nullable=False)
    reason: Mapped[str | None] = mapped_column(sa.String(200))
    created_at: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, default=now_ms)

    __table_args__ = (
        sa.UniqueConstraint("source_type", "source_id", "sku_id", "action", name="uq_inventory_movement"),
        sa.CheckConstraint(
            "action IN ('RESERVE','RELEASE','CONSUME','RESTOCK','ADJUST','RETURN_IN')",
            name="ck_movement_action",
        ),
    )


class StockApplication(PkMixin, TimestampMixin, VersionMixin, Base):
    """掉落/配件/服务的申请。C28：需申请后人工审批，审批通过才可购买。"""

    __tablename__ = "stock_applications"
    id_kind = "application"

    user_id: Mapped[str] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    product_id: Mapped[str] = mapped_column(sa.ForeignKey("products.id", ondelete="RESTRICT"), nullable=False)
    sku_id: Mapped[str | None] = mapped_column(sa.ForeignKey("skus.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(sa.String(30), nullable=False, default="drop")
    status: Mapped[str] = mapped_column(sa.String(20), nullable=False, default="SUBMITTED")
    reason: Mapped[str] = mapped_column(sa.String(1000), nullable=False, default="")
    measurement_note: Mapped[str | None] = mapped_column(sa.String(500))
    decided_by: Mapped[str | None] = mapped_column(sa.String(48))
    decided_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    decision_reason: Mapped[str | None] = mapped_column(sa.String(400))
    priority_rank: Mapped[int | None] = mapped_column(sa.Integer)
    purchase_expires_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    converted_order_id: Mapped[str | None] = mapped_column(sa.String(48))

    __table_args__ = (
        sa.CheckConstraint(
            "status IN ('SUBMITTED','NEEDS_INFO','APPROVED','REJECTED','CONVERTED','EXPIRED')",
            name="ck_application_status",
        ),
        sa.Index("ix_applications_product_status", "product_id", "status"),
        sa.Index("ix_applications_user", "user_id", "status"),
    )


__all__ = [
    "APPLICATION_STATUSES",
    "CHARACTER_STATES",
    "Character",
    "InventoryMovement",
    "InventoryReservation",
    "MOVEMENT_ACTIONS",
    "PRODUCT_KINDS",
    "PRODUCT_STATUSES",
    "Product",
    "RESERVATION_STATES",
    "SERVICE_KINDS",
    "Sku",
    "StockApplication",
    "new_id",
]
