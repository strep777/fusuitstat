"""媒体模块 HTTP 路由：上传票据、内容接收、访问授权与后台素材管理。

约束（10 文档）：预签名上传约束 key、大小、类型与过期时间；客户端 complete 后
后端读取实际文件验证，不信任前端 ``scan_passed``；私密下载 URL 默认 60 秒且 no-store。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile

from app.api.dependencies import ContextDep, CsrfDep, PrincipalDep, require_permission
from app.modules.media import service as svc
from app.modules.media.models import MediaAsset
from app.modules.media.schemas import (
    MediaAssetResponse,
    UploadCompleteResponse,
    UploadContentResponse,
    UploadTicketRequest,
    UploadTicketResponse,
)
from app.platform import audit
from app.platform.errors import AppError
from app.platform.ids import iso

public_router = APIRouter(tags=["media"])
admin_router = APIRouter(tags=["admin-media"])


def _asset_payload(asset: MediaAsset, *, include_private: bool = False) -> MediaAssetResponse:
    payload = MediaAssetResponse(
        id=asset.id,
        purpose=asset.purpose,
        visibility=asset.visibility,
        mime=asset.mime,
        bytes=asset.bytes,
        width=asset.width,
        height=asset.height,
        scan_status=asset.scan_status,
        status=asset.status,
        alt_text=asset.alt_text,
        created_at=iso(asset.created_at),
    )
    if include_private:
        payload.scan_note = asset.scan_note
        payload.original_filename = asset.original_filename
        payload.sha256 = asset.sha256
        payload.owner_id = asset.owner_id
    return payload


@public_router.post("/uploads", response_model=UploadTicketResponse, status_code=201)
def create_upload(
    payload: UploadTicketRequest, ctx: ContextDep, _csrf: CsrfDep, principal: PrincipalDep
) -> UploadTicketResponse:
    """创建临时上传。返回受限地址与限制值，不返回任何长期凭证。"""
    owner_id = principal.id
    if principal.kind == "customer":
        owner_id = principal.require_customer()
    elif principal.kind != "staff":
        raise AppError("AUTH_REQUIRED")

    # 每日上传配额，防止用上传接口做资源滥用。
    from app.platform import rate_limit

    rate_limit.check(
        ctx.session,
        scope="upload_day",
        subject=owner_id or (ctx.client_ip or "unknown"),
        limit=200,
        window_seconds=86_400,
    )

    ticket = svc.create_upload_ticket(
        ctx.session,
        owner_id=owner_id,
        purpose=payload.purpose,
        filename=payload.filename,
        mime=payload.mime,
        size=payload.size,
    )
    audit.record(
        ctx.session,
        actor_type="staff" if principal.is_staff else "customer",
        actor_id=owner_id,
        action="media.upload_ticket",
        resource_type="media_asset",
        resource_id=ticket.asset_id,
        after={"purpose": payload.purpose, "visibility": ticket.visibility, "size": payload.size},
        ip=ctx.client_ip,
    )
    return UploadTicketResponse(**ticket.__dict__)


@public_router.post("/uploads/{asset_id}/content", response_model=UploadContentResponse)
async def upload_content(
    asset_id: str,
    ctx: ContextDep,
    _csrf: CsrfDep,
    principal: PrincipalDep,
    file: Annotated[UploadFile, File()],
    declared_mime: Annotated[str | None, Form()] = None,
) -> UploadContentResponse:
    """接收文件内容。校验魔数、尺寸与像素总量，解码失败即隔离。"""
    asset = ctx.session.get(MediaAsset, asset_id)
    if asset is None:
        raise AppError("RESOURCE_NOT_FOUND")
    if not principal.is_staff and asset.owner_id != principal.id:
        raise AppError("RESOURCE_NOT_FOUND")
    if declared_mime:
        asset.mime = declared_mime.split(";")[0].strip().lower()

    data = await file.read()
    asset = svc.store_upload(ctx.session, asset=asset, data=data)
    audit.record(
        ctx.session,
        actor_type="staff" if principal.is_staff else "customer",
        actor_id=principal.id,
        action="media.upload_content",
        resource_type="media_asset",
        resource_id=asset.id,
        after={"bytes": asset.bytes, "scan_status": asset.scan_status},
        ip=ctx.client_ip,
    )
    return UploadContentResponse(
        asset_id=asset.id,
        scan_status=asset.scan_status,
        bytes=asset.bytes,
        width=asset.width,
        height=asset.height,
        ready=asset.scan_status == "ready",
    )


@public_router.post("/uploads/{asset_id}/complete", response_model=UploadCompleteResponse)
def complete_upload(asset_id: str, ctx: ContextDep, _csrf: CsrfDep, principal: PrincipalDep) -> UploadCompleteResponse:
    """核验实际对象并排队扫描/返回结果。不信任前端 scan_passed。"""
    asset = ctx.session.get(MediaAsset, asset_id)
    if asset is None:
        raise AppError("RESOURCE_NOT_FOUND")
    asset = svc.complete_upload(
        ctx.session, asset=asset, uploader_id=principal.id or "", is_staff=principal.is_staff
    )
    return UploadCompleteResponse(
        asset_id=asset.id,
        scan_status=asset.scan_status,
        status=asset.status,
        ready=asset.scan_status == "ready",
        scan_note=asset.scan_note,
    )


@public_router.get("/uploads/{asset_id}", response_model=MediaAssetResponse)
def get_upload(asset_id: str, ctx: ContextDep, principal: PrincipalDep) -> MediaAssetResponse:
    asset = ctx.session.get(MediaAsset, asset_id)
    if asset is None:
        raise AppError("RESOURCE_NOT_FOUND")
    svc.require_access(
        ctx.session, asset=asset, principal_id=principal.id, is_staff=principal.is_staff
    )
    return _asset_payload(asset)


@admin_router.get("/media/{asset_id}/access", dependencies=[Depends(require_permission("production.read"))])
@public_router.get("/media/{asset_id}/access")
def media_access(asset_id: str, ctx: ContextDep, response: Response, principal: PrincipalDep) -> dict[str, Any]:
    """私密附件短期下载地址。no-store，不进入缓存与日志。"""
    asset = ctx.session.get(MediaAsset, asset_id)
    if asset is None:
        raise AppError("RESOURCE_NOT_FOUND")
    payload = svc.signed_download(
        ctx.session, asset=asset, requester_id=principal.id, is_staff=principal.is_staff
    )
    response.headers["Cache-Control"] = "no-store"
    return payload


@public_router.get("/media/objects/{object_key:path}")
def download_object(
    object_key: str,
    expires: int,
    nonce: str,
    sig: str,
    ctx: ContextDep,
) -> Response:
    """本地存储的受签名下载端点（开发环境）。生产使用对象存储签名 URL。

    短期 URL 仍是临时持有凭证，不替代下载前鉴权：签名校验失败一律 404。
    """
    from app.adapters import storage as storage_adapter

    gateway = storage_adapter.get_gateway()
    if not isinstance(gateway, storage_adapter.LocalStorageGateway):
        raise AppError("RESOURCE_NOT_FOUND")
    if not gateway.verify_signature(key=object_key, expires=expires, nonce=nonce, signature=sig):
        raise AppError("RESOURCE_NOT_FOUND")
    try:
        data = gateway.get(key=object_key, private=True)
    except Exception as exc:  # noqa: BLE001 - 不泄露对象是否存在
        raise AppError("RESOURCE_NOT_FOUND") from exc
    content_type = "image/webp" if object_key.endswith(".webp") else "application/octet-stream"
    _ = ctx
    return Response(
        content=data,
        media_type=content_type,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


# ------------------------------------------------------------------ 后台
@admin_router.get(
    "/media", response_model=list, dependencies=[Depends(require_permission("media.manage"))]
)
def admin_list_media(
    ctx: ContextDep, purpose: str | None = None, scan_status: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    return [
        _asset_payload(asset, include_private=True).model_dump()
        for asset in svc.admin_list(ctx.session, purpose=purpose, scan_status=scan_status, limit=limit)
    ]


@admin_router.patch(
    "/media/{asset_id}", response_model=dict, dependencies=[Depends(require_permission("media.manage"))]
)
def admin_update_media(asset_id: str, payload: dict[str, Any], ctx: ContextDep, _csrf: CsrfDep, principal: PrincipalDep) -> dict[str, Any]:
    """登记授权信息、替代文本与是否可公开。授权信息是发布作品的前置条件。"""
    asset = ctx.session.get(MediaAsset, asset_id)
    if asset is None:
        raise AppError("RESOURCE_NOT_FOUND")
    before = {
        "alt_text": asset.alt_text,
        "license_note": asset.license_note,
        "is_public_ok": bool(asset.is_public_ok),
    }
    if "alt_text" in payload and payload["alt_text"] is not None:
        asset.alt_text = str(payload["alt_text"])[:300]
    if "license_note" in payload and payload["license_note"] is not None:
        asset.license_note = str(payload["license_note"])[:300]
    if "is_public_ok" in payload and payload["is_public_ok"] is not None:
        want_public = bool(payload["is_public_ok"])
        if want_public and asset.purpose not in svc.PUBLIC_PURPOSES:
            raise AppError(
                "STATE_CONFLICT",
                "该用途的素材必须保持私密；公开素材请使用 content/product/character/avatar 用途。",
            )
        asset.is_public_ok = 1 if want_public else 0
        asset.visibility = "public" if want_public else "private"
    asset.updated_at = asset.updated_at  # 触发 onupdate 语义
    audit.record(
        ctx.session,
        actor_type="staff",
        actor_id=principal.id,
        action="media.update",
        resource_type="media_asset",
        resource_id=asset.id,
        before=before,
        after={
            "alt_text": asset.alt_text,
            "license_note": asset.license_note,
            "is_public_ok": bool(asset.is_public_ok),
        },
    )
    return {"id": asset.id, "visibility": asset.visibility, "is_public_ok": bool(asset.is_public_ok)}


@admin_router.post(
    "/media/{asset_id}/link",
    response_model=dict,
    dependencies=[Depends(require_permission("media.manage"))],
)
def admin_link_media(
    asset_id: str, payload: dict[str, Any], ctx: ContextDep, _csrf: CsrfDep, principal: PrincipalDep
) -> dict[str, Any]:
    """建立素材与业务对象的引用关系（作品图、商品图、角色图）。"""
    resource_type = str(payload.get("resource_type", ""))
    resource_id = str(payload.get("resource_id", ""))
    role = str(payload.get("role", "attachment"))
    if not resource_type or not resource_id:
        raise AppError("VALIDATION_ERROR", fields={"resource_type": "必填", "resource_id": "必填"})
    link = svc.link_asset(
        ctx.session,
        asset_id=asset_id,
        resource_type=resource_type,
        resource_id=resource_id,
        role=role,
    )
    audit.record(
        ctx.session,
        actor_type="staff",
        actor_id=principal.id,
        action="media.link",
        resource_type="media_asset",
        resource_id=asset_id,
        after={"resource_type": resource_type, "resource_id": resource_id, "role": role},
    )
    return {"id": link.id, "asset_id": link.asset_id}


@admin_router.post(
    "/media/cleanup", response_model=dict, dependencies=[Depends(require_permission("media.manage"))]
)
def admin_cleanup_media(ctx: ContextDep, _csrf: CsrfDep, principal: PrincipalDep) -> dict[str, int]:
    """清理无有效引用的文件。只清除 24 小时前仍无引用的对象。"""
    count = svc.cleanup_orphans(ctx.session, limit=200)
    audit.record(
        ctx.session,
        actor_type="staff",
        actor_id=principal.id,
        action="media.cleanup",
        resource_type="media_asset",
        after={"cleaned": count},
    )
    return {"cleaned": count}


@admin_router.get(
    "/media/storage-status",
    response_model=dict,
    dependencies=[Depends(require_permission("media.manage"))],
)
def admin_storage_status(ctx: ContextDep) -> dict[str, Any]:
    """存储状态：只显示非敏感信息，不显示密钥。"""
    from app.adapters import storage as storage_adapter
    from app.platform.config import get_settings

    settings = get_settings()
    gateway = storage_adapter.get_gateway()
    pending = len(svc.admin_list(ctx.session, scan_status="pending", limit=200))
    quarantined = len(svc.admin_list(ctx.session, scan_status="quarantined", limit=200))
    _ = Request  # 保持导入语义清晰
    return {
        "backend": gateway.name,
        "configured_backend": settings.storage_backend,
        "public_prefix": settings.storage_public_prefix,
        "private_prefix": settings.storage_private_prefix,
        "signed_url_ttl_seconds": settings.signed_url_ttl_seconds,
        "pending_scan": pending,
        "quarantined": quarantined,
    }


__all__ = ["public_router", "admin_router"]
