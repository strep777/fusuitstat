"""媒体模块：上传、校验、扫描、派生图与访问授权。

拥有的数据：media_assets、media_derivatives、media_links。
约束（10 文档）：仅 JPEG/PNG/WebP，最多 3 张、单张 ≤10MiB、最大边长 8192、
解码像素总量 ≤4000 万；限制扩展名、MIME、文件魔数、实际大小，尝试安全解码；
重新编码派生图、移除 EXIF、扫描后才可用。用户 SVG/HTML/脚本/PDF 首发不接受。
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.ids import new_id, now_ms
from app.platform.orm import Base, PkMixin, TimestampMixin

VISIBILITIES = ("private", "public")
SCAN_STATUSES = ("pending", "ready", "quarantined", "rejected")
ASSET_STATUSES = ("active", "deleted")
PURPOSES = (
    "design",          # 询价设定图（私密）
    "measurement",     # 量体资料（私密，高敏感）
    "production",      # 制作过程图（私密，按订单授权）
    "feedback",        # 反馈附件（私密）
    "after_sale",      # 售后凭证（私密）
    "avatar",          # 头像（公开）
    "content",         # 作品与内容配图（公开）
    "product",         # 商品图（公开）
    "character",       # 角色设定图（公开）
)


class MediaAsset(PkMixin, TimestampMixin, Base):
    __tablename__ = "media_assets"
    id_kind = "asset"

    owner_id: Mapped[str | None] = mapped_column(sa.ForeignKey("users.id", ondelete="SET NULL"), index=True)
    purpose: Mapped[str] = mapped_column(sa.String(30), nullable=False)
    visibility: Mapped[str] = mapped_column(sa.String(10), nullable=False, default="private")
    object_key: Mapped[str] = mapped_column(sa.String(200), nullable=False, unique=True)
    original_filename: Mapped[str | None] = mapped_column(sa.String(200))
    mime: Mapped[str] = mapped_column(sa.String(60), nullable=False)
    bytes: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    sha256: Mapped[str] = mapped_column(sa.String(64), nullable=False, default="")
    width: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    height: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    scan_status: Mapped[str] = mapped_column(sa.String(16), nullable=False, default="pending")
    scan_note: Mapped[str | None] = mapped_column(sa.String(200))
    status: Mapped[str] = mapped_column(sa.String(12), nullable=False, default="active")
    exif_stripped: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=1)
    alt_text: Mapped[str | None] = mapped_column(sa.String(300))
    license_note: Mapped[str | None] = mapped_column(sa.String(300))
    is_public_ok: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    expires_at: Mapped[int | None] = mapped_column(sa.BigInteger)

    __table_args__ = (
        sa.CheckConstraint("visibility IN ('private','public')", name="ck_media_visibility"),
        sa.CheckConstraint(
            "scan_status IN ('pending','ready','quarantined','rejected')", name="ck_media_scan_status"
        ),
        sa.CheckConstraint("status IN ('active','deleted')", name="ck_media_status"),
        sa.CheckConstraint("bytes >= 0 AND width >= 0 AND height >= 0", name="ck_media_nonneg"),
        sa.Index("ix_media_owner_purpose", "owner_id", "purpose"),
        sa.Index("ix_media_scan", "scan_status", "created_at"),
    )


class MediaDerivative(PkMixin, Base):
    """派生图。作品生成 320/640/960/1440 宽，品牌素材按 manifest 尺寸。"""

    __tablename__ = "media_derivatives"

    asset_id: Mapped[str] = mapped_column(
        sa.ForeignKey("media_assets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    variant: Mapped[str] = mapped_column(sa.String(30), nullable=False)
    object_key: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    bytes: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    width: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    height: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)

    __table_args__ = (
        sa.UniqueConstraint("asset_id", "variant", name="uq_media_derivative_variant"),
    )


class MediaLink(PkMixin, Base):
    """素材与业务对象的引用关系。服务层校验目标存在与归属。"""

    __tablename__ = "media_links"

    asset_id: Mapped[str] = mapped_column(
        sa.ForeignKey("media_assets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    resource_type: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    resource_id: Mapped[str] = mapped_column(sa.String(48), nullable=False)
    role: Mapped[str] = mapped_column(sa.String(30), nullable=False, default="attachment")
    created_at: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, default=now_ms)

    __table_args__ = (
        sa.Index("ix_media_links_resource", "resource_type", "resource_id"),
        sa.Index("uq_media_link", "asset_id", "resource_type", "resource_id", "role", unique=True),
    )


__all__ = [
    "ASSET_STATUSES",
    "MediaAsset",
    "MediaDerivative",
    "MediaLink",
    "PURPOSES",
    "SCAN_STATUSES",
    "VISIBILITIES",
    "new_id",
]
