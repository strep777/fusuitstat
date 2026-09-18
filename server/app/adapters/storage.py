"""存储适配器：本地（开发）与兼容 S3 的对象存储（生产）。

公开作品与私密附件分桶或严格分前缀；私密下载 URL 默认 60 秒，签名 URL 不进入日志。
生产环境使用本地存储会被配置校验拒绝。
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from app.adapters.base import GatewayError, GatewayNotConfigured
from app.platform.config import Settings, get_settings
from app.platform.ids import new_id, now_ms, random_token
from app.platform.security import decrypt_pii, encrypt_pii


@dataclass(frozen=True)
class StoredObject:
    key: str
    bytes: int
    sha256: str


@dataclass(frozen=True)
class SignedUrl:
    url: str
    expires_in: int


class StorageGateway(Protocol):
    name: str

    def put(self, *, key: str, data: bytes, content_type: str, private: bool) -> StoredObject: ...

    def get(self, *, key: str, private: bool) -> bytes: ...

    def exists(self, *, key: str, private: bool) -> bool: ...

    def delete(self, *, key: str, private: bool) -> None: ...

    def signed_url(self, *, key: str, private: bool, ttl_seconds: int, filename: str | None = None) -> SignedUrl: ...

    def public_url(self, *, key: str) -> str: ...


class LocalStorageGateway:
    """本地文件存储。object key 由服务器生成，不信任原始文件名。"""

    name = "local"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._root = settings.storage_root

    def _path(self, key: str, private: bool) -> Path:
        prefix = self._settings.storage_private_prefix if private else self._settings.storage_public_prefix
        safe_key = key.replace("..", "_").lstrip("/\\")
        return self._root / prefix / safe_key

    def put(self, *, key: str, data: bytes, content_type: str, private: bool) -> StoredObject:
        _ = content_type
        target = self._path(key, private)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return StoredObject(key=key, bytes=len(data), sha256=hashlib.sha256(data).hexdigest())

    def get(self, *, key: str, private: bool) -> bytes:
        target = self._path(key, private)
        if not target.is_file():
            raise GatewayError("对象不存在", retryable=False)
        return target.read_bytes()

    def exists(self, *, key: str, private: bool) -> bool:
        return self._path(key, private).is_file()

    def delete(self, *, key: str, private: bool) -> None:
        target = self._path(key, private)
        if target.is_file():
            target.unlink()

    def signed_url(self, *, key: str, private: bool, ttl_seconds: int, filename: str | None = None) -> SignedUrl:
        """本地开发用受签名保护的下载地址，形状与对象存储一致。"""
        expires = now_ms() + ttl_seconds * 1000
        nonce = random_token(8)
        payload = f"{key}|{expires}|{nonce}"
        secret = self._settings.secret("session_secret")
        signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]
        query = f"expires={expires}&nonce={nonce}&sig={signature}"
        if filename:
            query += f"&filename={filename}"
        return SignedUrl(
            url=f"{self._settings.api_prefix}/media/objects/{key}?{query}",
            expires_in=ttl_seconds,
        )

    def public_url(self, *, key: str) -> str:
        return f"/media/{self._settings.storage_public_prefix}/{key}"

    def verify_signature(self, *, key: str, expires: int, nonce: str, signature: str) -> bool:
        if expires <= now_ms():
            return False
        secret = self._settings.secret("session_secret")
        payload = f"{key}|{expires}|{nonce}"
        expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]
        return hmac.compare_digest(expected, signature)


class S3StorageGateway:
    """兼容 S3 的对象存储骨架（阿里云 OSS / 腾讯云 COS 通过 S3 兼容端点接入）。

    未配置端点或桶时 fail closed，不静默降级到本地磁盘。
    """

    name = "s3"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        if not settings.storage_s3_endpoint:
            raise GatewayNotConfigured("s3")

    def _client(self) -> Any:  # pragma: no cover - 需要真实凭据
        try:
            import boto3
        except ImportError as exc:
            raise GatewayNotConfigured("s3:boto3") from exc
        return boto3.client("s3", endpoint_url=self._settings.storage_s3_endpoint)

    def put(self, *, key: str, data: bytes, content_type: str, private: bool) -> StoredObject:
        bucket = self._bucket(private)
        self._client().put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type)
        return StoredObject(key=key, bytes=len(data), sha256=hashlib.sha256(data).hexdigest())

    def get(self, *, key: str, private: bool) -> bytes:  # pragma: no cover
        response = self._client().get_object(Bucket=self._bucket(private), Key=key)
        return bytes(response["Body"].read())

    def exists(self, *, key: str, private: bool) -> bool:  # pragma: no cover
        try:
            self._client().head_object(Bucket=self._bucket(private), Key=key)
            return True
        except Exception:  # noqa: BLE001
            return False

    def delete(self, *, key: str, private: bool) -> None:  # pragma: no cover
        self._client().delete_object(Bucket=self._bucket(private), Key=key)

    def signed_url(self, *, key: str, private: bool, ttl_seconds: int, filename: str | None = None) -> SignedUrl:  # pragma: no cover
        params = {"Bucket": self._bucket(private), "Key": key}
        if filename:
            params["ResponseContentDisposition"] = f'attachment; filename="{filename}"'
        url = self._client().generate_presigned_url("get_object", Params=params, ExpiresIn=ttl_seconds)
        return SignedUrl(url=url, expires_in=ttl_seconds)

    def public_url(self, *, key: str) -> str:
        return f"{self._settings.storage_s3_endpoint.rstrip('/')}/{self._settings.storage_s3_bucket_public}/{key}"

    def _bucket(self, private: bool) -> str:
        return (
            self._settings.storage_s3_bucket_private if private else self._settings.storage_s3_bucket_public
        )


_gateway: StorageGateway | None = None


def get_gateway() -> StorageGateway:
    global _gateway
    if _gateway is None:
        settings = get_settings()
        if settings.storage_backend == "s3":
            _gateway = S3StorageGateway(settings)
        else:
            _gateway = LocalStorageGateway(settings)
    return _gateway


def reset_gateway() -> None:
    global _gateway
    _gateway = None


def new_object_key(purpose: str, *, user_id: str | None) -> str:
    """服务器生成随机 object key，不信任原始文件名。"""
    owner = (user_id or "anon").replace("/", "_")[:24]
    return f"{purpose}/{owner}/{new_id('asset')}"


def encrypt_target(value: str) -> str:
    return encrypt_pii(value)


def decrypt_target(value: str) -> str:
    return decrypt_pii(value)
