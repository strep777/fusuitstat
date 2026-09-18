"""每请求依赖：数据库会话、主体识别、CSRF 与权限校验。

Cookie（10 文档）：生产使用 ``__Host-session``，HttpOnly、Secure、Path=/、SameSite=Lax，
不设置 Domain。写请求同时检查 Origin/受信 Referer，不能仅靠 SameSite。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Annotated
from urllib.parse import urlparse

from fastapi import Depends, Header, Request, Response
from sqlalchemy.orm import Session

from app.modules.identity import repository as repo
from app.modules.identity import service as identity_service
from app.modules.identity.models import Session as SessionModel
from app.modules.identity.ports import VISITOR, Principal
from app.platform.config import get_settings
from app.platform.db import get_session_factory
from app.platform.errors import AppError

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# 只允许本站相对路径；拒绝 //evil.example 与编码绕过（10 文档）。
def safe_return_path(candidate: str | None, *, fallback: str = "/account") -> str:
    if not candidate:
        return fallback
    value = candidate.strip()
    if not value.startswith("/") or value.startswith("//") or value.startswith("/\\"):
        return fallback
    if "\\" in value or "://" in value:
        return fallback
    parsed = urlparse(value)
    if parsed.scheme or parsed.netloc:
        return fallback
    lowered = value.lower()
    if lowered.startswith("/%2f") or lowered.startswith("/%5c"):
        return fallback
    return value


@dataclass
class RequestContext:
    session: Session
    principal: Principal
    session_row: SessionModel | None
    client_ip: str | None
    origin: str | None
    request_id: str


def client_ip_of(request: Request) -> str | None:
    """只信任配置的反向代理传来的转发头，避免伪造 X-Forwarded-For 绕过限流。"""
    settings = get_settings()
    direct = request.client.host if request.client else None
    if direct in settings.trusted_proxies:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return direct


def _session_cookie_for_scope(request: Request, scope: str) -> str | None:
    name = identity_service.ADMIN_SESSION_COOKIE if scope == "admin" else identity_service.SESSION_COOKIE
    token = request.cookies.get(name)
    if token:
        return token
    # 开发环境走 http，__Host- 前缀要求 Secure，浏览器不会存储；回退到非前缀名。
    if not get_settings().is_production:
        return request.cookies.get(name.replace("__Host-", "dsh_"))
    return None


def resolve_principal(session: Session, request: Request) -> tuple[Principal, SessionModel | None]:
    """客户会话与员工会话分离；客户登录不能继承员工权限。"""
    origin = request.headers.get("origin")
    scope = "admin" if origin is None and request.url.path.startswith("/api/v1/admin") else "customer"
    if request.url.path.startswith("/api/v1/admin"):
        scope = "admin"
    token = _session_cookie_for_scope(request, scope)
    row = identity_service.load_session(session, token, scope=scope)
    if row is None:
        return VISITOR, None
    identity_service.touch_session(session, row)
    if scope == "admin" and row.staff_id:
        staff = repo.get_staff(session, row.staff_id)
        if staff is None or staff.status != "active":
            return VISITOR, None
        principal = Principal(
            kind="staff",
            id=staff.id,
            display_name=staff.display_name,
            permissions=repo.permissions_for_staff(session, staff.id),
        )
        return principal, row
    if row.user_id:
        user = repo.get_user(session, row.user_id)
        if user is None or user.status == "closed":
            return VISITOR, None
        principal = Principal(
            kind="customer",
            id=user.id,
            display_name=user.nickname,
            permissions=frozenset(),
            status=user.status,
        )
        return principal, row
    return VISITOR, None


def get_context(request: Request) -> Iterator[RequestContext]:
    session = get_session_factory()()
    try:
        principal, row = resolve_principal(session, request)
        ctx = RequestContext(
            session=session,
            principal=principal,
            session_row=row,
            client_ip=client_ip_of(request),
            origin=request.headers.get("origin"),
            request_id=getattr(request.state, "trace_id", "") or "",
        )
        yield ctx
        # 只读请求也提交，以便滑动过期时间与限流计数落库。
        if session.in_transaction():
            session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


ContextDep = Annotated[RequestContext, Depends(get_context)]


def current_principal(ctx: ContextDep) -> Principal:
    return ctx.principal


PrincipalDep = Annotated[Principal, Depends(current_principal)]


def require_customer(ctx: ContextDep) -> str:
    return ctx.principal.require_customer()


CustomerIdDep = Annotated[str, Depends(require_customer)]


def origin_is_trusted(request: Request) -> bool:
    """写请求的 Origin/Referer 校验。"""
    settings = get_settings()
    allowed = {settings.public_origin.rstrip("/")}
    if not settings.is_production:
        allowed.update({"http://127.0.0.1:5173", "http://localhost:5173", "http://127.0.0.1:4173"})
    origin = request.headers.get("origin")
    if origin:
        return origin.rstrip("/") in allowed
    referer = request.headers.get("referer")
    if referer:
        parsed = urlparse(referer)
        return f"{parsed.scheme}://{parsed.netloc}" in allowed
    # 无 Origin 且无 Referer 的写请求：拒绝（浏览器同源请求会带 Origin）。
    return False


def enforce_write_guard(request: Request) -> None:
    """非 GET 写操作的统一前置校验：方法、来源。CSRF 在需要主体的路由中校验。"""
    if request.method in SAFE_METHODS:
        return
    if request.url.path.startswith(f"{get_settings().api_prefix}/payment-notifications"):
        # 支付通知走渠道验签，不要求用户 cookie 与 Origin。
        return
    if not origin_is_trusted(request):
        raise AppError("CSRF_INVALID")


def csrf_guard(
    ctx: ContextDep,
    request: Request,
    x_csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> None:
    """需要登录的写操作：校验会话 CSRF token。"""
    if request.method in SAFE_METHODS:
        return
    identity_service.verify_csrf(ctx.session, ctx.session_row, x_csrf_token)


CsrfDep = Annotated[None, Depends(csrf_guard)]


def require_permission(permission: str) -> Callable[[ContextDep], Principal]:
    """生成权限依赖。后台每个动作都必须绑定权限码（FR-016）。"""

    def _dependency(ctx: ContextDep) -> Principal:
        ctx.principal.require(permission)
        return ctx.principal

    return _dependency


def set_session_cookie(response: Response, *, token: str, max_age_seconds: int, admin: bool = False) -> None:
    settings = get_settings()
    name = identity_service.ADMIN_SESSION_COOKIE if admin else identity_service.SESSION_COOKIE
    if not settings.is_production:
        # 本地开发通过 http 访问，__Host- 前缀要求 Secure 会被浏览器丢弃。
        name = name.replace("__Host-", "dsh_")
    response.set_cookie(
        key=name,
        value=token,
        max_age=max_age_seconds,
        httponly=True,
        secure=settings.cookie_secure or settings.is_production,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response, *, admin: bool = False) -> None:
    settings = get_settings()
    name = identity_service.ADMIN_SESSION_COOKIE if admin else identity_service.SESSION_COOKIE
    if not settings.is_production:
        name = name.replace("__Host-", "dsh_")
    response.delete_cookie(key=name, path="/", httponly=True, secure=settings.cookie_secure)
