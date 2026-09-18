"""目录模块 HTTP DTO。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.modules.shared.schemas import Dto, StrictDto

ProductKind = Literal["stock_goods", "exclusive_character", "commission", "service"]
CharacterState = Literal["available", "reserved", "adopted", "kept"]


class SkuResponse(Dto):
    id: str
    sku_code: str
    label: str
    attributes: dict[str, Any] = Field(default_factory=dict)
    price_cents: int
    currency: str
    compare_at_cents: int | None = None
    available: int
    in_stock: bool
    low_stock: bool
    max_per_order: int
    weight_grams: int
    cover: dict[str, Any] | None = None


class ProductApproval(Dto):
    approved: bool
    application_id: str | None = None
    priority_rank: int | None = None
    purchase_expires_at: str | None = None


class ProductResponse(Dto):
    id: str
    slug: str
    kind: str
    title: str
    subtitle: str = ""
    description: str = ""
    highlights: list[str] = Field(default_factory=list)
    category: str | None = None
    series_code: str | None = None
    requires_approval: bool = False
    terms_version: str = "shop-1"
    service_kind: str | None = None
    seo_description: str | None = None
    skus: list[SkuResponse] = Field(default_factory=list)
    images: list[dict[str, Any]] = Field(default_factory=list)
    availability: str = "unavailable"
    approval: ProductApproval | None = None
    # 后台附加
    status: str | None = None
    version: int | None = None
    updated_at: str | None = None


class CharacterResponse(Dto):
    id: str
    slug: str
    name: str
    species: str = ""
    designer_credit: str | None = None
    story: str = ""
    rights_summary: str = ""
    status: str
    exclusive: bool = True
    adoption_note: str | None = None
    price_cents: int | None = None
    currency: str = "CNY"
    purchasable: bool = False
    product_slug: str | None = None
    images: list[dict[str, Any]] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class CatalogPage(Dto):
    items: list[Any]
    total: int
    page: int = 1
    limit: int = 20
    has_more: bool = False
    next_cursor: str | None = None


class ApplicationInput(StrictDto):
    product_id: str = Field(max_length=48)
    sku_id: str | None = Field(default=None, max_length=48)
    kind: Literal["drop", "accessory", "cleaning", "repair", "fitting", "service"] = "drop"
    reason: str = Field(min_length=4, max_length=1000)
    measurement_note: str | None = Field(default=None, max_length=500)


class ApplicationDecisionInput(StrictDto):
    decision: Literal["APPROVED", "REJECTED", "NEEDS_INFO"]
    reason: str = Field(min_length=2, max_length=400)
    priority_rank: int | None = Field(default=None, ge=1)
    purchase_window_days: int = Field(default=7, ge=1, le=90)


class ApplicationResponse(Dto):
    id: str
    kind: str
    status: str
    product_id: str
    product_title: str | None = None
    product_slug: str | None = None
    reason: str = ""
    decision_reason: str | None = None
    priority_rank: int | None = None
    purchase_expires_at: str | None = None
    created_at: str | None = None
    decided_at: str | None = None


class ProductCreateInput(StrictDto):
    slug: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    kind: ProductKind = "stock_goods"
    title: str = Field(min_length=1, max_length=200)
    subtitle: str = Field(default="", max_length=200)
    description: str = Field(default="", max_length=20_000)
    highlights: list[str] = Field(default_factory=list, max_length=20)
    category: str | None = Field(default=None, max_length=40)
    series_code: str | None = Field(default=None, max_length=40)
    requires_approval: bool = False
    shipping_template: str = Field(default="default", max_length=40)
    service_kind: str | None = Field(default=None, max_length=30)
    sort_order: int = 0
    seo_description: str | None = Field(default=None, max_length=300)
    terms_version: str = Field(default="shop-1", max_length=40)


class ProductPatchInput(StrictDto):
    version: int = Field(ge=1)
    title: str | None = Field(default=None, max_length=200)
    subtitle: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=20_000)
    highlights: list[str] | None = Field(default=None, max_length=20)
    category: str | None = Field(default=None, max_length=40)
    series_code: str | None = Field(default=None, max_length=40)
    requires_approval: bool | None = None
    shipping_template: str | None = Field(default=None, max_length=40)
    service_kind: str | None = Field(default=None, max_length=30)
    sort_order: int | None = None
    seo_description: str | None = Field(default=None, max_length=300)
    terms_version: str | None = Field(default=None, max_length=40)
    kind: ProductKind | None = None
    status: Literal["draft", "published", "archived"] | None = None


class SkuInput(StrictDto):
    sku_code: str = Field(min_length=1, max_length=60, pattern=r"^[A-Za-z0-9._-]+$")
    label: str = Field(min_length=1, max_length=120)
    attributes: dict[str, Any] = Field(default_factory=dict)
    price_cents: int = Field(ge=0, le=100_000_000)
    currency: Literal["CNY", "USD"] = "CNY"
    compare_at_cents: int | None = Field(default=None, ge=0)
    stock_on_hand: int = Field(default=0, ge=0, le=1_000_000)
    max_per_order: int = Field(default=5, ge=1, le=100)
    weight_grams: int = Field(default=0, ge=0)
    is_active: bool = True
    cover_asset_id: str | None = Field(default=None, max_length=48)
    sort_order: int = 0


class SkuPatchInput(StrictDto):
    version: int = Field(ge=1)
    label: str | None = Field(default=None, max_length=120)
    attributes: dict[str, Any] | None = None
    price_cents: int | None = Field(default=None, ge=0, le=100_000_000)
    compare_at_cents: int | None = Field(default=None, ge=0)
    max_per_order: int | None = Field(default=None, ge=1, le=100)
    weight_grams: int | None = Field(default=None, ge=0)
    is_active: bool | None = None
    cover_asset_id: str | None = Field(default=None, max_length=48)
    sort_order: int | None = None


class StockAdjustInput(StrictDto):
    delta: int = Field(ge=-1_000_000, le=1_000_000)
    reason: str = Field(min_length=4, max_length=200)


class CharacterCreateInput(StrictDto):
    product_id: str = Field(max_length=48)
    slug: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    name: str = Field(min_length=1, max_length=120)
    species: str = Field(default="", max_length=60)
    designer_credit: str | None = Field(default=None, max_length=120)
    story: str = Field(default="", max_length=8000)
    rights_summary: str = Field(default="", max_length=4000)
    public_status: CharacterState = "available"
    exclusive: bool = True
    adoption_note: str | None = Field(default=None, max_length=400)
    cover_asset_id: str | None = Field(default=None, max_length=48)
    tags: list[str] = Field(default_factory=list, max_length=20)
    display_authorized: bool = False
