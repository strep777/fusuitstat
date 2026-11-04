"""请求守卫：防爬虫、全局限额、并发闸门、请求体上限与公共缓存头。

设计见 ``文档/19_防爬虫与高并发安全设计.md``。这里只做**到达应用进程之后**的
保护；连接数、TLS 与 DDoS 清洗必须由反向代理/云防火墙承担。

约束：
- 热路径不写数据库；进程内固定窗口计数，多 worker 时按 worker 数放大。
- 敏感业务（短信、登录、反馈、上传）继续使用 SQLite 原子限额，不能只靠本模块。
- 日志不记录明文 IP、Cookie、查询串或请求体。
"""

from __future__ import annotations

import asyncio
import hmac
import ipaddress
import logging
import time
from typing import Any

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

from app.platform.config import Settings
from app.platform.errors import DEFAULT_MESSAGES
from app.platform.ids import new_id

logger = logging.getLogger("starium.guard")

#: 已知高侵略性采集器/扫描器。只做粗筛，不能替代边缘 Bot 规则。
_BUILTIN_BLOCKLIST = (
    "sqlmap",
    "nikto",
    "nmap",
    "masscan",
    "acunetix",
    "nessus",
    "semrush",
    "ahrefs",
    "mj12bot",
    "bytespider",
    "gptbot",
    "ccbot",
    "claude-web",
    "amazonbot",
    "petalbot",
    "dataforseo",
)

_PUBLIC_CACHE_PREFIXES = (
    "/api/v1/products",
    "/api/v1/content",
    "/api/v1/site",
    "/api/v1/series",
    "/api/v1/works",
    "/api/v1/characters",
    "/api/v1/news",
)

_SKIP_PATHS = {"/healthz", "/readyz", "/api/docs", "/api/openapi.json"}


def _error_response(
    request: Request,
    code: str,
    status: int,
    *,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    trace_id = getattr(request.state, "trace_id", None) or new_id("req")
    request.state.trace_id = trace_id
    response = JSONResponse(
        {
            "error": {
                "code": code,
                "message": DEFAULT_MESSAGES.get(code, DEFAULT_MESSAGES["INTERNAL_ERROR"]),
                "fields": {},
                "trace_id": trace_id,
            }
        },
        status_code=status,
        headers=headers,
    )
    response.headers.setdefault("X-Request-Id", trace_id)
    response.headers.setdefault("Cache-Control", "no-store")
    return response


def client_ip(request: Request, settings: Settings) -> str | None:
    """只信任配置的反向代理传来的转发头，避免伪造 X-Forwarded-For 绕过限流。"""
    direct = request.client.host if request.client else None
    chain = request.headers.get("x-forwarded-for", "").split(",")
    for hop in reversed(chain):
        if direct not in settings.trusted_proxies or not hop.strip():
            break
        direct = hop.strip()
    return direct


def _ip_hash(ip: str, settings: Settings) -> str:
    secret = settings.secret("session_secret")
    return hmac.digest(secret.encode(), f"guard::{ip}".encode(), "sha256").hex()[:32]


def _is_admin_allowed(ip: str, entries: list[str]) -> bool:
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for entry in entries:
        try:
            if "/" in entry:
                if address in ipaddress.ip_network(entry, strict=False):
                    return True
            elif address == ipaddress.ip_address(entry):
                return True
        except ValueError:
            logger.warning("guard: invalid ADMIN_IP_ALLOWLIST entry ignored")
    return False


class _WindowCounter:
    """进程内固定窗口计数。

    只用于粗粒度限额。窗口按 Unix 秒对齐，键为 ``scope|subject_hash``。
    """

    def __init__(self) -> None:
        self._buckets: dict[str, tuple[int, int]] = {}
        self._hits = 0

    def hit(self, key: str, limit: int, window_seconds: int) -> tuple[bool, int, int]:
        now = int(time.time())
        expires = now - (now % window_seconds) + window_seconds
        bucket = self._buckets.get(key)
        count = 1 if bucket is None or bucket[0] != expires else bucket[1] + 1
        self._buckets[key] = (expires, count)
        self._hits += 1
        if self._hits % 4096 == 0:
            self._prune(now)
        remaining = max(0, limit - count)
        return count <= limit, remaining, expires

    def _prune(self, now: int) -> None:
        self._buckets = {
            key: value for key, value in self._buckets.items() if value[0] > now
        }


class RequestGuardMiddleware(BaseHTTPMiddleware):
    """单个应用进程的请求守卫。"""

    def __init__(self, app: Any, *, settings: Settings) -> None:  # noqa: ANN401
        super().__init__(app)
        self.settings = settings
        self._counter = _WindowCounter()
        self._semaphore = asyncio.Semaphore(max(1, settings.max_concurrent_requests))
        self._blocklist = tuple(
            item.strip().lower()
            for item in (*_BUILTIN_BLOCKLIST, *settings.crawler_blocklist.split(","))
            if item.strip()
        )

    @property
    def active(self) -> bool:
        if not self.settings.security_guard_enabled:
            return False
        return not self.settings.is_testing or self.settings.security_guard_test_mode

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:  # noqa: ANN401
        if not self.active:
            return await call_next(request)

        path = request.url.path
        if path in _SKIP_PATHS:
            return await call_next(request)

        ip = client_ip(request, self.settings) or "unknown"
        subject = _ip_hash(ip, self.settings)

        # 1) 后台 IP 允许清单。默认空=不启用；一旦配置，未命中直接拒绝。
        if self.settings.admin_networks and path.startswith(
            f"{self.settings.api_prefix}/admin"
        ) and not _is_admin_allowed(ip, self.settings.admin_networks):
            logger.warning("guard: admin ip denied path=%s", path)
            return _error_response(request, "FORBIDDEN", 403)

        if path.startswith(self.settings.api_prefix):
            # 2) UA 粗筛：空 UA 与已知扫描器。生产默认开启，测试默认关闭。
            if self.settings.crawler_block_active:
                user_agent = (request.headers.get("user-agent") or "").strip().lower()
                if not user_agent or any(token in user_agent for token in self._blocklist):
                    logger.warning("guard: crawler denied path=%s ua_len=%s", path, len(user_agent))
                    return _error_response(request, "ROBOT_DENIED", 403)

            # 3) 蜜罐：正常页面不会请求该路径。
            if path == f"{self.settings.api_prefix}/_hp":
                allowed, _, _ = self._counter.hit(f"trap|{subject}", 3, 86_400)
                logger.warning("guard: honeypot hit")
                if not allowed:
                    return _error_response(request, "ROBOT_DENIED", 403)
                return _error_response(request, "RESOURCE_NOT_FOUND", 404)

            # 4) 请求体上限。multipart 由业务层再做精确校验。
            if request.method in {"POST", "PUT", "PATCH"}:
                content_type = (request.headers.get("content-type") or "").lower()
                max_bytes = (
                    self.settings.max_multipart_body_bytes
                    if content_type.startswith("multipart/form-data")
                    else self.settings.max_json_body_bytes
                )
                raw_length = request.headers.get("content-length")
                if raw_length:
                    try:
                        length = int(raw_length)
                    except ValueError:
                        length = -1
                    if length < 0:
                        return _error_response(request, "PAYLOAD_TOO_LARGE", 413)
                    if length > max_bytes:
                        logger.warning("guard: payload rejected path=%s bytes=%s", path, length)
                        return _error_response(request, "PAYLOAD_TOO_LARGE", 413)

            # 5) 分档限额。
            method = request.method.upper()
            scopes: list[tuple[str, int]] = [
                ("global", self.settings.rate_limit_global_per_minute)
            ]
            if path.startswith(f"{self.settings.api_prefix}/admin"):
                scopes.append(("admin", self.settings.rate_limit_admin_per_minute))
            if method in {"GET", "HEAD"}:
                scopes.append(("read", self.settings.rate_limit_read_per_minute))
            elif method not in {"OPTIONS"}:
                scopes.append(("write", self.settings.rate_limit_write_per_minute))
            if request.query_params.get("q"):
                scopes.append(("search", self.settings.rate_limit_search_per_minute))

            limited_headers: dict[str, str] | None = None
            for scope, limit in scopes:
                allowed, remaining, reset = self._counter.hit(
                    f"{scope}|{subject}", limit, 60
                )
                if limited_headers is None or remaining < int(
                    limited_headers.get("X-RateLimit-Remaining", "999999")
                ):
                    limited_headers = {
                        "X-RateLimit-Limit": str(limit),
                        "X-RateLimit-Remaining": str(remaining),
                        "X-RateLimit-Reset": str(reset),
                    }
                if not allowed:
                    retry_after = max(1, reset - int(time.time()))
                    headers = {**(limited_headers or {}), "Retry-After": str(retry_after)}
                    logger.warning("guard: rate limited scope=%s path=%s", scope, path)
                    return _error_response(request, "RATE_LIMITED", 429, headers=headers)

            # 6) 并发闸门：等待很短时间仍无槽位就快速失败，避免请求堆积。
            try:
                await asyncio.wait_for(
                    self._semaphore.acquire(),
                    timeout=max(1, self.settings.concurrency_queue_ms) / 1000,
                )
            except TimeoutError:
                logger.warning("guard: concurrency rejected path=%s", path)
                return _error_response(
                    request,
                    "SERVER_BUSY",
                    503,
                    headers={"Retry-After": "2"},
                )
            try:
                response = await call_next(request)
            finally:
                self._semaphore.release()

            if limited_headers:
                for key, value in limited_headers.items():
                    response.headers.setdefault(key, value)
        else:
            response = await call_next(request)

        # 7) 公共读缓存：只对匿名、无 Authorization 的成功 GET 生效。
        if (
            request.method in {"GET", "HEAD"}
            and response.status_code == 200
            and not request.cookies
            and not request.headers.get("authorization")
            and any(path.startswith(prefix) for prefix in _PUBLIC_CACHE_PREFIXES)
        ):
            response.headers["Cache-Control"] = (
                f"public, max-age={self.settings.public_cache_seconds}, "
                "stale-while-revalidate=60"
            )
        return response
