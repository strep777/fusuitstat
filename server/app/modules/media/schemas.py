"""媒体模块 HTTP DTO。"""

from __future__ import annotations

from pydantic import Field

from app.modules.shared.schemas import Dto, StrictDto


class UploadTicketRequest(StrictDto):
    purpose: str = Field(min_length=2, max_length=30)
    filename: str = Field(min_length=1, max_length=200)
    mime: str = Field(min_length=3, max_length=60)
    size: int = Field(ge=0, le=50 * 1024 * 1024)


class UploadTicketResponse(Dto):
    asset_id: str
    object_key: str
    visibility: str
    upload_url: str
    expires_in: int
    max_bytes: int


class UploadContentResponse(Dto):
    asset_id: str
    scan_status: str
    bytes: int
    width: int
    height: int
    ready: bool


class UploadCompleteResponse(Dto):
    asset_id: str
    scan_status: str
    status: str
    ready: bool
    scan_note: str | None = None


class MediaAssetResponse(Dto):
    id: str
    purpose: str
    visibility: str
    mime: str
    bytes: int
    width: int
    height: int
    scan_status: str
    status: str
    alt_text: str | None = None
    created_at: str | None = None
    # 仅后台返回
    scan_note: str | None = None
    original_filename: str | None = None
    sha256: str | None = None
    owner_id: str | None = None
