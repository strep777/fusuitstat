"""媒体服务：上传校验、安全扫描、派生图生成、访问授权与清理。

校验顺序（10 文档）：限制扩展名 → MIME → 文件魔数 → 实际大小 → 安全解码；
通过后重新编码派生图、移除 EXIF，扫描完成后才可用。
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import struct
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from PIL import Image, UnidentifiedImageError
from sqlalchemy.orm import Session

from app.adapters import storage as storage_adapter
from app.modules.media.models import PURPOSES, MediaAsset, MediaDerivative, MediaLink
from app.platform.config import get_settings
from app.platform.errors import AppError
from app.platform.ids import new_id, now_ms

ALLOWED_MIME = {
    "image/jpeg": "JPEG",
    "image/png": "PNG",
    "image/webp": "WEBP",
}
# 首发不接受用户 SVG/HTML/脚本/PDF。
REJECTED_EXTENSIONS = {
    ".svg",
    ".svgz",
    ".html",
    ".htm",
    ".js",
    ".mjs",
    ".pdf",
    ".zip",
    ".rar",
    ".7z",
    ".exe",
    ".bat",
    ".sh",
    ".ps1",
    ".tif",
    ".tiff",
    ".gif",
}

# 作品与商品图派生宽度（11 文档：作品生成 320/640/960/1440 宽派生图）。
CONTENT_VARIANTS: tuple[tuple[str, int], ...] = (
    ("w320", 320),
    ("w640", 640),
    ("w960", 960),
    ("w1440", 1440),
)
THUMB_VARIANT = ("thumb", 240)

# 公开用途可进入公开 CDN；其余保持私密（客户未授权的图片绝不放公开 CDN）。
PUBLIC_PURPOSES = frozenset({"avatar", "content", "product", "character"})


@dataclass(frozen=True)
class UploadTicket:
    asset_id: str
    object_key: str
    visibility: str
    upload_url: str
    expires_in: int
    max_bytes: int


def _magic_matches(data: bytes, expected_format: str) -> bool:
    """文件魔数检查，不信任扩展名与 Content-Type。"""
    if data.startswith(b"\xff\xd8\xff"):
        return expected_format == "JPEG"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return expected_format == "PNG"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return expected_format == "WEBP"
    return False


def _pixel_count_without_full_decode(data: bytes) -> tuple[int, int]:
    """在安全解码前先读出尺寸，用于压缩炸弹的早期拦截。"""
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        width, height = struct.unpack(">II", data[16:24])
        return int(width), int(height)
    if data.startswith(b"\xff\xd8"):
        index = 2
        while index + 9 < len(data):
            if data[index] != 0xFF:
                index += 1
                continue
            marker = data[index + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                height, width = struct.unpack(">HH", data[index + 5 : index + 9])
                return int(width), int(height)
            if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                index += 2
                continue
            if index + 3 >= len(data):
                break
            length = struct.unpack(">H", data[index + 2 : index + 4])[0]
            index += 2 + length
    return 0, 0


def create_upload_ticket(
    session: Session, *, owner_id: str | None, purpose: str, filename: str, mime: str, size: int
) -> UploadTicket:
    """预签名上传：约束 key、大小、类型与过期时间。"""
    settings = get_settings()
    if purpose not in PURPOSES:
        raise AppError("VALIDATION_ERROR", "素材用途不合法。", fields={"purpose": "非法用途"})
    lowered = filename.lower()
    suffix = "." + lowered.rsplit(".", 1)[-1] if "." in lowered else ""
    if suffix in REJECTED_EXTENSIONS:
        raise AppError("UPLOAD_REJECTED", "该文件类型不被接受，请上传 JPEG、PNG 或 WebP 图片。")
    normalized_mime = (mime or "").split(";")[0].strip().lower()
    if normalized_mime not in ALLOWED_MIME:
        raise AppError("UPLOAD_REJECTED", "只接受 JPEG、PNG 或 WebP 图片。")
    if size > settings.upload_max_bytes:
        raise AppError(
            "UPLOAD_LIMIT_EXCEEDED",
            f"单张图片不得超过 {settings.upload_max_bytes // (1024 * 1024)}MiB。",
        )

    visibility = "public" if purpose in PUBLIC_PURPOSES else "private"
    key = storage_adapter.new_object_key(purpose, user_id=owner_id)
    asset = MediaAsset(
        id=new_id("asset"),
        owner_id=owner_id,
        purpose=purpose,
        visibility=visibility,
        object_key=key,
        original_filename=filename[:200],
        mime=normalized_mime,
        bytes=0,
        scan_status="pending",
        is_public_ok=1 if visibility == "public" else 0,
    )
    session.add(asset)
    session.flush()
    return UploadTicket(
        asset_id=asset.id,
        object_key=key,
        visibility=visibility,
        upload_url=f"{settings.api_prefix}/uploads/{asset.id}/content",
        expires_in=900,
        max_bytes=settings.upload_max_bytes,
    )


def store_upload(session: Session, *, asset: MediaAsset, data: bytes) -> MediaAsset:
    """接收上传内容：校验、落库、派生、扫描。"""
    settings = get_settings()
    if not data:
        raise AppError("UPLOAD_REJECTED", "文件内容为空。")
    if len(data) > settings.upload_max_bytes:
        raise AppError("UPLOAD_LIMIT_EXCEEDED")

    expected_format = ALLOWED_MIME.get(asset.mime)
    if expected_format is None or not _magic_matches(data, expected_format):
        asset.scan_status = "rejected"
        asset.scan_note = "文件魔数与声明的类型不一致"
        raise AppError("UPLOAD_REJECTED", "文件内容与图片格式不符，已拒绝。")

    declared_width, declared_height = _pixel_count_without_full_decode(data)
    if declared_width and declared_height:
        if declared_width > settings.upload_max_edge or declared_height > settings.upload_max_edge:
            asset.scan_status = "rejected"
            asset.scan_note = f"边长超过 {settings.upload_max_edge}"
            raise AppError("UPLOAD_REJECTED", f"图片边长不得超过 {settings.upload_max_edge} 像素。")
        if declared_width * declared_height > settings.upload_max_pixels:
            asset.scan_status = "rejected"
            asset.scan_note = "解码像素总量超限"
            raise AppError("UPLOAD_REJECTED", "图片像素总量过大，可能是压缩炸弹，已拒绝。")

    try:
        Image.MAX_IMAGE_PIXELS = settings.upload_max_pixels
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
            if width > settings.upload_max_edge or height > settings.upload_max_edge:
                raise AppError("UPLOAD_REJECTED", "图片边长超出限制。")
            if width * height > settings.upload_max_pixels:
                raise AppError("UPLOAD_REJECTED", "图片像素总量超出限制。")
            # 重新编码派生图并移除 EXIF。
            derived = _derive(image, asset.visibility == "public")
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        asset.scan_status = "quarantined"
        asset.scan_note = f"解码失败：{type(exc).__name__}"
        raise AppError("UPLOAD_REJECTED", "图片无法安全解码，已隔离。") from exc

    gateway = storage_adapter.get_gateway()
    stored = gateway.put(
        key=asset.object_key, data=data, content_type=asset.mime, private=asset.visibility == "private"
    )

    # 上传成功但询价提交失败时附件仍可复用；这里只做对象与派生图登记。
    asset.bytes = stored.bytes
    asset.sha256 = stored.sha256
    asset.width = width
    asset.height = height
    asset.scan_status = "ready"
    asset.scan_note = None
    asset.expires_at = None

    for variant, payload, variant_width, variant_height in derived:
        derivative_key = f"{asset.object_key}.{variant}.webp"
        gateway.put(
            key=derivative_key, data=payload, content_type="image/webp",
            private=asset.visibility == "private",
        )
        session.add(
            MediaDerivative(
                id=new_id("asset"),
                asset_id=asset.id,
                variant=variant,
                object_key=derivative_key,
                bytes=len(payload),
                width=variant_width,
                height=variant_height,
            )
        )
    session.flush()
    return asset


def _derive(image: Image.Image, public: bool) -> list[tuple[str, bytes, int, int]]:
    """生成派生图：公开图多尺寸 + 缩略图；私密图只生成缩略图与预览。"""
    results: list[tuple[str, bytes, int, int]] = []
    variants = CONTENT_VARIANTS if public else (("preview", 960),)
    source = image.convert("RGB") if image.mode in ("P", "CMYK", "LA") else image.convert("RGBA")
    for name, target_width in variants:
        if source.width <= target_width:
            resized = source.copy()
        else:
            ratio = target_width / source.width
            resized = source.resize(
                (target_width, max(1, round(source.height * ratio))), Image.Resampling.LANCZOS
            )
        buffer = io.BytesIO()
        resized.convert("RGB").save(buffer, format="WEBP", quality=86, method=5)
        results.append((name, buffer.getvalue(), resized.width, resized.height))

    thumb = source.copy()
    thumb.thumbnail((THUMB_VARIANT[1], THUMB_VARIANT[1]), Image.Resampling.LANCZOS)
    thumb_buffer = io.BytesIO()
    thumb.convert("RGB").save(thumb_buffer, format="WEBP", quality=80, method=5)
    results.append((THUMB_VARIANT[0], thumb_buffer.getvalue(), thumb.width, thumb.height))
    return results


def complete_upload(session: Session, *, asset: MediaAsset, uploader_id: str, is_staff: bool) -> MediaAsset:
    """客户端 complete 后由后端读取实际对象校验，不信任前端 ``scan_passed``。"""
    if not is_staff and asset.owner_id != uploader_id:
        raise AppError("RESOURCE_NOT_FOUND")
    if asset.scan_status == "ready":
        return asset
    gateway = storage_adapter.get_gateway()
    if not gateway.exists(key=asset.object_key, private=asset.visibility == "private"):
        raise AppError("UPLOAD_NOT_READY", "尚未收到文件内容，请重新上传。")
    data = gateway.get(key=asset.object_key, private=asset.visibility == "private")
    return store_upload(session, asset=asset, data=data)


# ------------------------------------------------------------------ 访问授权
def require_access(session: Session, *, asset: MediaAsset, principal_id: str | None, is_staff: bool) -> None:
    """逐资源归属检查。403 不透露对象是否存在。"""
    if asset.status != "active":
        raise AppError("RESOURCE_NOT_FOUND")
    if asset.visibility == "public":
        return
    if principal_id and asset.owner_id == principal_id:
        return
    if principal_id:
        from app.application.workflows.media_access import can_read_private_asset
        if can_read_private_asset(session, asset_id=asset.id, principal_id=principal_id, is_staff=is_staff):
            return
    raise AppError("RESOURCE_NOT_FOUND")


def signed_download(
    session: Session, *, asset: MediaAsset, requester_id: str | None, is_staff: bool
) -> dict[str, Any]:
    """私密附件短期下载地址，默认 60 秒，no-store。"""
    settings = get_settings()
    require_access(session, asset=asset, principal_id=requester_id, is_staff=is_staff)
    if asset.scan_status != "ready":
        raise AppError("UPLOAD_NOT_READY")
    target = asset.object_key
    derivative = session.scalars(
        sa.select(MediaDerivative).where(
            MediaDerivative.asset_id == asset.id, MediaDerivative.variant == "preview"
        )
    ).one_or_none()
    if derivative is not None:
        target = derivative.object_key
    signed = storage_adapter.get_gateway().signed_url(
        key=target,
        private=asset.visibility == "private",
        ttl_seconds=settings.signed_url_ttl_seconds,
        filename=asset.original_filename,
    )
    return {
        "asset_id": asset.id,
        "url": signed.url,
        "expires_in": signed.expires_in,
        "content_type": "image/webp" if target.endswith(".webp") else asset.mime,
        "cache_control": "no-store",
    }


def public_media_payload(session: Session, asset_id: str) -> dict[str, Any] | None:
    """公开素材的展示数据（用于作品、商品、角色、内容配图）。"""
    asset = session.get(MediaAsset, asset_id)
    if asset is None or asset.status != "active" or asset.visibility != "public":
        return None
    if asset.scan_status != "ready":
        return None
    derivatives = list(
        session.scalars(sa.select(MediaDerivative).where(MediaDerivative.asset_id == asset.id))
    )
    gateway = storage_adapter.get_gateway()
    sources: list[dict[str, Any]] = []
    thumb = None
    for row in derivatives:
        url = gateway.public_url(key=row.object_key)
        if row.variant == "thumb":
            thumb = url
        if row.variant.startswith("w"):
            sources.append({"url": url, "width": row.width})
    sources.sort(key=lambda item: item["width"])
    return {
        "id": asset.id,
        "alt": asset.alt_text or "",
        "width": asset.width,
        "height": asset.height,
        "src": gateway.public_url(key=asset.object_key),
        "thumb": thumb,
        "srcset": sources,
    }


def link_asset(
    session: Session, *, asset_id: str, resource_type: str, resource_id: str, role: str = "attachment"
) -> MediaLink:
    asset = session.get(MediaAsset, asset_id)
    if asset is None:
        raise AppError("RESOURCE_NOT_FOUND", "素材不存在。")
    if asset.scan_status != "ready":
        raise AppError("UPLOAD_NOT_READY", "素材尚未处理完成。")
    existing = session.scalars(
        sa.select(MediaLink).where(
            MediaLink.asset_id == asset_id,
            MediaLink.resource_type == resource_type,
            MediaLink.resource_id == resource_id,
            MediaLink.role == role,
        )
    ).one_or_none()
    if existing is not None:
        return existing
    link = MediaLink(
        id=new_id("asset"),
        asset_id=asset_id,
        resource_type=resource_type,
        resource_id=resource_id,
        role=role,
    )
    session.add(link)
    session.flush()
    return link


def assets_for(
    session: Session, *, resource_type: str, resource_id: str, role: str | None = None
) -> list[MediaAsset]:
    stmt = (
        sa.select(MediaAsset)
        .join(MediaLink, MediaLink.asset_id == MediaAsset.id)
        .where(MediaLink.resource_type == resource_type, MediaLink.resource_id == resource_id)
    )
    if role:
        stmt = stmt.where(MediaLink.role == role)
    stmt = stmt.order_by(MediaLink.created_at)
    return list(session.scalars(stmt))


def assert_owned_ready(
    session: Session, *, asset_ids: list[str], owner_id: str, purpose: str
) -> list[MediaAsset]:
    """通用素材校验：本人所有、用途匹配、已完成扫描且未被隔离。

    ``purpose`` 由调用方给出（``design`` 设定图 / ``feedback`` 反馈附件 /
    ``stage`` 制作节点图…）。任何一项不满足都按 ``UPLOAD_NOT_READY`` 拒绝，
    且不泄露素材是否存在。
    """
    assets: list[MediaAsset] = []
    for asset_id in asset_ids:
        asset = session.get(MediaAsset, asset_id)
        if asset is None or asset.owner_id != owner_id or asset.purpose != purpose:
            raise AppError("UPLOAD_NOT_READY", "附件不可用，请重新上传。")
        if asset.scan_status != "ready" or asset.status != "active":
            raise AppError("UPLOAD_NOT_READY", "附件仍在处理中，请稍后重试。")
        assets.append(asset)
    return assets


def assert_owned_ready_design(session: Session, *, asset_ids: list[str], owner_id: str) -> list[MediaAsset]:
    """询价提交前校验：附件 ready、本人所有且 purpose=design。"""
    settings = get_settings()
    if not asset_ids:
        return []
    if len(asset_ids) > settings.upload_max_files:
        raise AppError(
            "UPLOAD_LIMIT_EXCEEDED", f"最多上传 {settings.upload_max_files} 张设定图。"
        )
    return assert_owned_ready(session, asset_ids=asset_ids, owner_id=owner_id, purpose="design")


def cleanup_orphans(session: Session, *, now: int | None = None, limit: int = 100) -> int:
    """清理无有效引用的文件：无引用附件 24 小时后删除。"""
    moment = now if now is not None else now_ms()
    cutoff = moment - 24 * 3600 * 1000
    rows = list(
        session.scalars(
            sa.select(MediaAsset)
            .outerjoin(MediaLink, MediaLink.asset_id == MediaAsset.id)
            .where(MediaLink.id.is_(None), MediaAsset.created_at < cutoff, MediaAsset.status == "active")
            .limit(limit)
        )
    )
    gateway = storage_adapter.get_gateway()
    for asset in rows:
        # 清理失败不阻塞任务：单个对象删除失败时仍然标记为已删除，
        # 由后续的对象存储生命周期规则兜底。
        with contextlib.suppress(Exception):
            gateway.delete(key=asset.object_key, private=asset.visibility == "private")
        asset.status = "deleted"
    return len(rows)


def admin_list(
    session: Session, *, purpose: str | None = None, scan_status: str | None = None, limit: int = 50
) -> list[MediaAsset]:
    stmt = sa.select(MediaAsset).where(MediaAsset.status == "active")
    if purpose:
        stmt = stmt.where(MediaAsset.purpose == purpose)
    if scan_status:
        stmt = stmt.where(MediaAsset.scan_status == scan_status)
    stmt = stmt.order_by(MediaAsset.created_at.desc()).limit(min(limit, 200))
    return list(session.scalars(stmt))


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
