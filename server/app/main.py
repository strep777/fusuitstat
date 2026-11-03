"""应用装配：FastAPI 主应用、路由挂载、健康检查与安全响应头。

架构（04 文档）：同域 BFF 风格接口，``/api`` 由反向代理转发到本应用；
公开内容预渲染走独立任务，账户交易页保持 SPA。
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.dependencies import client_ip_of
from app.platform.config import Settings, get_settings
from app.platform.db import get_engine, integrity_report
from app.platform.errors import install_error_handlers
from app.platform.guard import RequestGuardMiddleware
from app.platform.ids import iso, new_id, now_ms

logger = logging.getLogger("starium")


def _configure_logging(settings: Settings) -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    _configure_logging(settings)

    app = FastAPI(
        title=f"{settings.site_name} 商业官网 API",
        version="1.0.0",
        description=(
            "弈星纪元兽装工作室官网后端（模块化单体）。"
            "金额一律整数分；支付成功只认已验签通知或服务端查单。"
        ),
        docs_url="/api/docs" if not settings.is_production else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if not settings.is_production else None,
    )
    install_error_handlers(app)

    @app.middleware("http")
    async def _security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        trace_id = request.headers.get("x-request-id") or new_id("job")
        request.state.trace_id = trace_id
        response: Response = await call_next(request)
        response.headers["X-Request-Id"] = trace_id
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(self)"
        )
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-site")
        if request.url.path.startswith(settings.api_prefix):
            response.headers.setdefault("X-Robots-Tag", "noindex, nofollow")
        if settings.is_production:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
            # CSP 按支付跳转和图片域名最小开放，禁止万能 *。
            response.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' data: https:; "
                "style-src 'self' 'unsafe-inline'; script-src 'self'; "
                "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
            )
        # 交易与个人 API 禁止 CDN 缓存。
        if request.url.path.startswith(settings.api_prefix):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    if not settings.is_production:
        # 同域默认不开 CORS；开发环境只放行本地前端。
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
            allow_headers=["Content-Type", "X-CSRF-Token", "Idempotency-Key", "If-Match"],
            expose_headers=["X-Request-Id", "Retry-After"],
        )

    _register_routers(app, settings)
    _register_probes(app, settings)

    # 顺序：后添加的中间件在最外层。Guard 必须看到原始请求与最终响应。
    if settings.is_production:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(RequestGuardMiddleware, settings=settings)
    return app


def _register_routers(app: FastAPI, settings: Settings) -> None:
    from app.api.admin import admin_router
    from app.api.public import public_router

    app.include_router(public_router, prefix=settings.api_prefix)
    app.include_router(admin_router, prefix=f"{settings.api_prefix}/admin")


def _register_probes(app: FastAPI, settings: Settings) -> None:
    @app.get("/healthz", include_in_schema=False)
    def liveness() -> JSONResponse:
        """只检查进程活着；不暴露路径、版本秘密或数据库细节。"""
        return JSONResponse({"status": "ok", "time": iso(now_ms())})

    @app.get("/readyz", include_in_schema=False)
    def readiness() -> JSONResponse:
        """检查数据库可读写与迁移版本。"""
        try:
            with get_engine().connect() as connection:
                connection.execute(text("SELECT 1"))
                revision = connection.execute(
                    text("SELECT version_num FROM alembic_version LIMIT 1")
                ).scalar()
            return JSONResponse({"status": "ready", "schema": revision or "unmanaged"})
        except Exception:  # noqa: BLE001 - 探针不返回内部错误细节
            logger.warning("readiness check failed")
            return JSONResponse({"status": "unavailable"}, status_code=503)

    @app.get("/healthz/details", include_in_schema=False)
    def details(request: Request) -> JSONResponse:
        """内部探针：仅允许回环地址访问。"""
        ip = client_ip_of(request)
        if ip not in {"127.0.0.1", "::1"} and not settings.is_production:
            pass
        elif ip not in {"127.0.0.1", "::1"}:
            return JSONResponse({"status": "forbidden"}, status_code=403)
        return JSONResponse({"status": "ok", "database": integrity_report(get_engine())})


app = create_app()
