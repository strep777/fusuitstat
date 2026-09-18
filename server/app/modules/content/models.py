"""内容模块：作品、公告、系列、FAQ、团队、法律文本、养护指南与站点设置。

拥有的数据：content_entries、content_revisions、series_specs、site_settings。
公开读取只返回 published；草稿不公开（FR-001–003）。
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.ids import new_id, now_ms
from app.platform.orm import Base, PkMixin, TimestampMixin, VersionMixin

CONTENT_KINDS = (
    "page",        # 关于、养护、团队等通用页面
    "work",        # 作品
    "news",        # 公告
    "series",      # 系列
    "faq",         # 常见问题
    "team",        # 团队成员
    "legal",       # 法律文本（服务协议 / 隐私 / 退换 / 积分规则）
    "story",       # 客户故事（C18/C21 可选扩展）
    "designer",    # 设计师页（C18 可选扩展）
    "event",       # 活动（C20 可选扩展）
)
CONTENT_STATUSES = ("draft", "in_review", "published", "archived")
LOCALES = ("zh-CN", "en-US")


class ContentEntry(PkMixin, TimestampMixin, VersionMixin, Base):
    __tablename__ = "content_entries"
    id_kind = "content"

    kind: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    slug: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    locale: Mapped[str] = mapped_column(sa.String(10), nullable=False, default="zh-CN")
    title: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    summary: Mapped[str] = mapped_column(sa.String(400), nullable=False, default="")
    # 已清洗的 HTML（服务端白名单），不允许直接 v-html 未清洗内容。
    sanitized_body: Mapped[str] = mapped_column(sa.Text, nullable=False, default="")
    body_format: Mapped[str] = mapped_column(sa.String(10), nullable=False, default="html")
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False, default="draft")
    publish_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    unpublish_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    revision: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=1)
    cover_asset_id: Mapped[str | None] = mapped_column(sa.String(48))
    tags: Mapped[list[Any] | None] = mapped_column(sa.JSON)
    seo_title: Mapped[str | None] = mapped_column(sa.String(200))
    seo_description: Mapped[str | None] = mapped_column(sa.String(300))
    sort_order: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    is_featured: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    # 作品专用：物种、类型、工艺、署名与授权状态
    species: Mapped[str | None] = mapped_column(sa.String(60))
    work_type: Mapped[str | None] = mapped_column(sa.String(40))
    craft_notes: Mapped[str | None] = mapped_column(sa.String(500))
    designer_credit: Mapped[str | None] = mapped_column(sa.String(120))
    maker_credit: Mapped[str | None] = mapped_column(sa.String(120))
    photographer_credit: Mapped[str | None] = mapped_column(sa.String(120))
    # 只有拿到公开授权的委托作品才能公开（FR-002、C18）
    display_authorized: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    client_display_name: Mapped[str | None] = mapped_column(sa.String(80))
    created_by: Mapped[str | None] = mapped_column(sa.String(48))

    __table_args__ = (
        sa.UniqueConstraint("kind", "slug", "locale", name="uq_content_kind_slug_locale"),
        sa.CheckConstraint(
            "status IN ('draft','in_review','published','archived')", name="ck_content_status"
        ),
        sa.CheckConstraint("display_authorized IN (0,1)", name="ck_content_display_authorized"),
        sa.CheckConstraint("is_featured IN (0,1)", name="ck_content_featured"),
        sa.Index("ix_content_public", "kind", "status", "publish_at"),
        sa.Index("ix_content_species", "species"),
    )


class ContentRevision(PkMixin, Base):
    """内容版本快照。发布触发受控预渲染任务，失败保留上一版本。"""

    __tablename__ = "content_revisions"

    entry_id: Mapped[str] = mapped_column(
        sa.ForeignKey("content_entries.id", ondelete="CASCADE"), nullable=False, index=True
    )
    revision: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    title: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    body_snapshot: Mapped[str] = mapped_column(sa.Text, nullable=False, default="")
    editor_id: Mapped[str | None] = mapped_column(sa.String(48))
    editor_label: Mapped[str | None] = mapped_column(sa.String(80))
    change_note: Mapped[str | None] = mapped_column(sa.String(300))
    published_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    created_at: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, default=now_ms)

    __table_args__ = (sa.UniqueConstraint("entry_id", "revision", name="uq_content_revision"),)


class SeriesSpec(PkMixin, TimestampMixin, VersionMixin, Base):
    """系列规格：起价与参数来自内容数据，仅展示起价，不承诺未核准交期。"""

    __tablename__ = "series_specs"
    id_kind = "content"

    entry_id: Mapped[str] = mapped_column(
        sa.ForeignKey("content_entries.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    code: Mapped[str] = mapped_column(sa.String(40), nullable=False, unique=True)
    base_price_cents: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(sa.String(3), nullable=False, default="CNY")
    price_includes: Mapped[list[Any] | None] = mapped_column(sa.JSON)
    price_excludes: Mapped[list[Any] | None] = mapped_column(sa.JSON)
    lead_time_note: Mapped[str | None] = mapped_column(sa.String(200))
    spec_schema: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON)
    display_order: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    is_active: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=1)

    __table_args__ = (
        sa.CheckConstraint("base_price_cents >= 0", name="ck_series_price_nonneg"),
        sa.CheckConstraint("is_active IN (0,1)", name="ck_series_active"),
    )


class SiteSetting(PkMixin, TimestampMixin, VersionMixin, Base):
    """后台可配置项唯一入口：经营信息、规则版本、功能开关与业务默认值。

    密钥绝不写入本表（14 文档：后台仅显示非敏感状态）。
    """

    __tablename__ = "site_settings"

    key: Mapped[str] = mapped_column(sa.String(80), nullable=False, unique=True)
    value: Mapped[dict[str, Any]] = mapped_column(sa.JSON, nullable=False, default=dict)
    group: Mapped[str] = mapped_column(sa.String(40), nullable=False, default="general")
    label: Mapped[str] = mapped_column(sa.String(120), nullable=False, default="")
    description: Mapped[str] = mapped_column(sa.String(300), nullable=False, default="")
    is_public: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    updated_by: Mapped[str | None] = mapped_column(sa.String(48))

    __table_args__ = (sa.CheckConstraint("is_public IN (0,1)", name="ck_setting_public"),)


class PrebuildJob(PkMixin, Base):
    """受控预渲染任务记录。任务失败保留上一版本静态页（ADR-04）。"""

    __tablename__ = "prebuild_jobs"

    trigger: Mapped[str] = mapped_column(sa.String(30), nullable=False, default="content.publish")
    resource_type: Mapped[str] = mapped_column(sa.String(40), nullable=False, default="content")
    resource_id: Mapped[str | None] = mapped_column(sa.String(48))
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False, default="PENDING")
    routes: Mapped[list[Any] | None] = mapped_column(sa.JSON)
    started_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    finished_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    error: Mapped[str | None] = mapped_column(sa.String(300))
    created_at: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, default=now_ms)
    created_by: Mapped[str | None] = mapped_column(sa.String(48))

    __table_args__ = (
        sa.CheckConstraint("status IN ('PENDING','RUNNING','DONE','FAILED')", name="ck_prebuild_status"),
    )


__all__ = [
    "CONTENT_KINDS",
    "CONTENT_STATUSES",
    "ContentEntry",
    "ContentRevision",
    "LOCALES",
    "PrebuildJob",
    "SeriesSpec",
    "SiteSetting",
    "new_id",
]
