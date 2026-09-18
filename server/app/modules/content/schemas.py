"""内容模块 HTTP DTO。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.modules.shared.schemas import Dto, StrictDto

ContentKindLiteral = Literal[
    "page", "work", "news", "series", "faq", "team", "legal", "story", "designer", "event"
]
LocaleLiteral = Literal["zh-CN", "en-US"]


class MediaImageDto(Dto):
    id: str
    alt: str
    width: int
    height: int
    src: str
    thumb: str | None = None
    srcset: list[dict[str, Any]] = Field(default_factory=list)


class SeoDto(Dto):
    title: str
    description: str
    canonical: str


class ContentEntrySummary(Dto):
    id: str
    kind: str
    slug: str
    locale: str
    title: str
    summary: str
    route: str
    cover_asset_id: str | None = None
    cover: MediaImageDto | None = None
    tags: list[str] = Field(default_factory=list)
    species: str | None = None
    work_type: str | None = None
    published_at: str | None = None
    updated_at: str | None = None
    is_featured: bool = False
    seo: SeoDto
    # 后台列表附加字段
    status: str | None = None
    version: int | None = None
    revision: int | None = None


class ContentEntryDetail(ContentEntrySummary):
    body_html: str = ""
    credits: dict[str, str | None] | None = None
    craft_notes: str | None = None
    gallery: list[MediaImageDto] = Field(default_factory=list)


class ContentPage(Dto):
    items: list[ContentEntrySummary]
    total: int
    page: int = 1
    limit: int = 20
    has_more: bool = False
    next_cursor: str | None = None


class PaginationQuery(StrictDto):
    page: int = Field(default=1, ge=1)
    limit: int = Field(default=20, ge=1, le=100)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.limit


class SlugQuery(StrictDto):
    slug: str = Field(min_length=1, max_length=120)


class PublishInput(StrictDto):
    version: int = Field(ge=1)
    change_note: str | None = Field(default=None, max_length=300)


class ContentCreateInput(StrictDto):
    kind: ContentKindLiteral
    slug: str = Field(min_length=1, max_length=120)
    locale: LocaleLiteral = "zh-CN"
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(default="", max_length=400)
    body_html: str = Field(default="", max_length=200_000)
    cover_asset_id: str | None = Field(default=None, max_length=48)
    tags: list[str] = Field(default_factory=list, max_length=20)
    seo_title: str | None = Field(default=None, max_length=200)
    seo_description: str | None = Field(default=None, max_length=300)
    species: str | None = Field(default=None, max_length=60)
    work_type: str | None = Field(default=None, max_length=40)
    craft_notes: str | None = Field(default=None, max_length=500)
    designer_credit: str | None = Field(default=None, max_length=120)
    maker_credit: str | None = Field(default=None, max_length=120)
    photographer_credit: str | None = Field(default=None, max_length=120)
    display_authorized: bool = False
    client_display_name: str | None = Field(default=None, max_length=80)
    # C18：这份作品的公开展示授权属于哪个客户账号（客户据此在账户里撤回）。
    client_user_id: str | None = Field(default=None, max_length=48)
    sort_order: int = 0
    is_featured: bool = False


class ContentUpdateInput(StrictDto):
    version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    summary: str | None = Field(default=None, max_length=400)
    body_html: str | None = Field(default=None, max_length=200_000)
    change_note: str | None = Field(default=None, max_length=300)
    locale: LocaleLiteral | None = None
    cover_asset_id: str | None = Field(default=None, max_length=48)
    tags: list[str] | None = Field(default=None, max_length=20)
    seo_title: str | None = Field(default=None, max_length=200)
    seo_description: str | None = Field(default=None, max_length=300)
    sort_order: int | None = None
    species: str | None = Field(default=None, max_length=60)
    work_type: str | None = Field(default=None, max_length=40)
    craft_notes: str | None = Field(default=None, max_length=500)
    designer_credit: str | None = Field(default=None, max_length=120)
    maker_credit: str | None = Field(default=None, max_length=120)
    photographer_credit: str | None = Field(default=None, max_length=120)
    client_display_name: str | None = Field(default=None, max_length=80)
    client_user_id: str | None = Field(default=None, max_length=48)
    display_authorized: bool | None = None
    is_featured: bool | None = None


# ------------------------------------------------------------------ 公开展示授权（C18）
class DisplayAuthorizationItem(Dto):
    """一条公开展示授权：资源、当前展示状态与「此刻是否真的公开」。"""

    resource_type: str
    resource_id: str
    title: str
    slug: str | None = None
    route: str | None = None
    client_display_name: str | None = None
    display_authorized: bool = False
    published: bool = False
    visible_now: bool = False
    state: str = "ACTIVE"
    granted_at: str | None = None
    updated_at: str | None = None


class DisplayAuthorizationPage(Dto):
    items: list[DisplayAuthorizationItem] = Field(default_factory=list)
    total: int = 0


class DisplayRetractInput(StrictDto):
    """撤回输入：只允许 work / media，且必须是本人资源。"""

    resource_type: Literal["work", "media"]
    resource_id: str = Field(min_length=1, max_length=48)
    reason: str = Field(min_length=2, max_length=200)


class DisplayRetractResponse(Dto):
    resource_type: str
    resource_id: str
    display_authorized: bool = False
    already_retracted: bool = False
    prebuild_job_id: str | None = None
    effective: str = "immediate"


class SeriesSpecInput(StrictDto):
    entry_id: str = Field(max_length=48)
    code: str = Field(min_length=1, max_length=40)
    base_price_cents: int | None = Field(default=None, ge=0)
    currency: Literal["CNY", "USD"] | None = None
    price_includes: list[str] | None = Field(default=None, max_length=40)
    price_excludes: list[str] | None = Field(default=None, max_length=40)
    lead_time_note: str | None = Field(default=None, max_length=200)
    spec_schema: dict[str, Any] | None = None
    display_order: int | None = None
    is_active: bool | None = None
    version: int | None = Field(default=None, ge=1)


class SettingUpdateInput(StrictDto):
    value: dict[str, Any] | list[Any] | str | int | bool
    version: int = Field(ge=1)
    reason: str | None = Field(default=None, max_length=300)
