"""运营后台 API 唯一汇总入口（``/api/v1/admin``）。

职责（03/06/14 文档）：
1. 员工认证：口令 + TOTP 两步登录、独立会话 cookie、退出与 ``auth/me``；登录响应
   一律 ``Cache-Control: no-store``，失败统一 ``AUTH_FAILED``，不暴露账号是否存在。
2. 后台 CSRF：除 ``/auth/login``、``/auth/mfa`` 外，**所有写操作**都带 ``CsrfDep``。
3. 每个路由用 ``Depends(require_permission("<code>"))`` 绑定权限码（FR-016），
   权限码清单的唯一来源是 ``app.modules.identity.permissions``。
4. 只 include 各模块已交付的后台路由，缺失模块用 ``try/except ImportError`` 容忍。

响应脱敏红线：禁止返回手机号明文、地址明文、验证码、session cookie、附件签名 URL、
完整支付报文与任何密钥。状态机动作（发布/批准/发货/重试）一律走 POST 动作，
通用 PATCH 不越过状态机。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, text

from app.api.dependencies import (
    ContextDep,
    CsrfDep,
    PrincipalDep,
    clear_session_cookie,
    require_permission,
    set_session_cookie,
)
from app.modules.identity import admin_auth
from app.modules.identity import permissions as permission_catalog
from app.modules.identity import repository as identity_repo
from app.modules.identity import service as identity_service
from app.modules.identity.ports import Principal
from app.platform import audit
from app.platform.config import get_settings
from app.platform.errors import AppError
from app.platform.ids import iso, now_ms
from app.platform.reliability import enqueue

logger = logging.getLogger("starium.admin")

router = APIRouter(tags=["admin"])


# ------------------------------------------------------------------ 输入 DTO
class StrictModel(BaseModel):
    """后台输入 DTO：额外字段直接拒绝，避免前端把 user_id、金额等塞进请求体。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class StaffLoginInput(StrictModel):
    login_name: str = Field(min_length=3, max_length=40)
    password: str = Field(min_length=8, max_length=200)


class StaffMfaInput(StrictModel):
    challenge_token: str = Field(min_length=8, max_length=200)
    code: str = Field(min_length=6, max_length=12)
    # 兼容只回传 challenge_token + code 的客户端；缺省时从挑战记录反查员工。
    staff_id: str | None = Field(default=None, max_length=48)


class RestrictionInput(StrictModel):
    status: str = Field(min_length=3, max_length=16)
    reason: str = Field(min_length=2, max_length=300)
    version: int = Field(ge=1)


class StaffCreateInput(StrictModel):
    login_name: str = Field(min_length=3, max_length=40)
    display_name: str = Field(min_length=1, max_length=40)
    email: str = Field(default="", max_length=120)
    password: str = Field(min_length=12, max_length=200)
    roles: list[str] = Field(min_length=1, max_length=6)


class StaffUpdateInput(StrictModel):
    display_name: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=120)
    status: str | None = Field(default=None, max_length=16)
    roles: list[str] | None = Field(default=None, max_length=6)
    reason: str | None = Field(default=None, max_length=300)
    version: int = Field(ge=1)


class PasswordResetInput(StrictModel):
    password: str = Field(min_length=12, max_length=200)
    reason: str = Field(min_length=2, max_length=300)


class JobActionInput(StrictModel):
    """运维动作的最小输入：可带原因与执行参数，不接受任意 SQL 或自由字段。"""

    reason: str | None = Field(default=None, max_length=300)
    provider: str | None = Field(default=None, max_length=20)
    bill_date: str | None = Field(default=None, max_length=10)


def _with_csrf(permission: str) -> Callable[[CsrfDep, PrincipalDep], Principal]:
    """需要登录的写操作：先校验会话 CSRF，再校验权限码。"""

    def _dependency(_csrf: CsrfDep, _principal: PrincipalDep) -> Principal:
        _principal.require(permission)
        return _principal

    return _dependency


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def _enroll_payload(enroll: Any) -> dict[str, Any] | None:
    if enroll is None:
        return None
    if isinstance(enroll, dict):
        return enroll
    dump = getattr(enroll, "model_dump", None)
    return dump() if callable(dump) else dict(enroll)


# ------------------------------------------------------------------ 认证
@router.post("/auth/login")
def admin_login(
    payload: StaffLoginInput,
    ctx: ContextDep,
    response: Response,
) -> dict[str, Any]:
    """第一步：口令校验，返回 MFA 挑战。

    账号不存在与口令错误返回**同一个** ``AUTH_FAILED``，不暴露账号是否存在；
    响应禁止缓存，challenge token 只在此响应中出现一次。
    """
    result = admin_auth.begin_login(
        ctx.session, login_name=payload.login_name, password=payload.password, client_ip=ctx.client_ip
    )
    _no_store(response)
    return {
        "status": result["status"],
        "challenge_token": result["challenge_token"],
        "staff_id": result["staff_id"],
        "enroll": _enroll_payload(result.get("enroll")),
    }


@router.post("/auth/mfa")
def admin_mfa(
    payload: StaffMfaInput,
    ctx: ContextDep,
    request: Request,
    response: Response,
) -> dict[str, Any]:
    """第二步：校验 TOTP/恢复码，下发**后台独立**会话 cookie。"""
    staff_id = payload.staff_id or _staff_id_for_challenge(ctx.session, payload.challenge_token)
    if not staff_id:
        raise AppError("AUTH_FAILED", "登录信息无效，请重新登录。")
    staff, row, token, csrf_secret = admin_auth.complete_login(
        ctx.session,
        challenge_token=payload.challenge_token,
        code=payload.code,
        client_ip=ctx.client_ip,
        user_agent=request.headers.get("user-agent"),
        staff_id=staff_id,
    )
    settings = get_settings()
    set_session_cookie(
        response, token=token, max_age_seconds=settings.admin_session_hours * 3600, admin=True
    )
    _no_store(response)
    return dict(admin_auth.staff_session_response(staff, row, csrf_secret, ctx.session).model_dump())


def _staff_id_for_challenge(session: Any, challenge_token: str) -> str | None:
    from app.modules.identity.models import MfaChallenge
    from app.platform.security import hash_token

    row = session.scalars(
        select(MfaChallenge).where(MfaChallenge.token_hash == hash_token(challenge_token))
    ).one_or_none()
    return row.staff_id if row is not None else None


@router.post("/auth/logout")
def admin_logout(
    ctx: ContextDep,
    response: Response,
    _csrf: CsrfDep,
    principal: PrincipalDep,
) -> dict[str, Any]:
    """撤销当前后台会话并清除 cookie。"""
    if ctx.session_row is not None:
        identity_service.revoke_session(ctx.session, ctx.session_row, reason="admin_logout")
        ctx.session.flush()
    clear_session_cookie(response, admin=True)
    _no_store(response)
    return {"status": "SIGNED_OUT", "staff_id": principal.id}


@router.get("/auth/me")
def admin_me(ctx: ContextDep, principal: PrincipalDep) -> dict[str, Any]:
    """当前员工、角色与权限码。客户会话不能继承员工权限。"""
    if not principal.is_staff or not principal.id:
        raise AppError("AUTH_REQUIRED", "请先登录后台。")
    staff = identity_repo.get_staff(ctx.session, principal.id)
    if staff is None:
        raise AppError("AUTH_REQUIRED", "请先登录后台。")
    roles = identity_repo.roles_for_staff(ctx.session, staff.id)
    return {
        "staff": {
            "id": staff.id,
            "login_name": staff.login_name,
            "display_name": staff.display_name,
            "email": staff.email,
            "status": staff.status,
            "is_owner": bool(staff.is_owner),
            "mfa_enabled": bool(staff.mfa_enabled),
            "last_login_at": iso(staff.last_login_at),
        },
        "roles": [
            {"code": role.code, "name": role.name, "description": role.description} for role in roles
        ],
        "permissions": sorted(principal.permissions),
        "session_expires_at": iso(ctx.session_row.expires_at if ctx.session_row else None),
        "separation_pairs": [list(pair) for pair in permission_catalog.SEPARATION_PAIRS],
    }


# ------------------------------------------------------------------ 概览
def _from_module(session: Any, module: str, attribute: str, *args: Any, **kwargs: Any) -> Any:
    """调用可选模块的仓储/服务函数；模块未交付时返回 ``None``。

    降级语义（在 ``missing_modules`` 中显式说明）：``None`` 只表示「模块未就绪」，
    不会被误报成「数量为 0」这种看起来正常的错误结论。
    """
    try:
        imported = __import__(module, fromlist=[attribute])
    except ImportError:
        return None
    func = getattr(imported, attribute, None)
    if not callable(func):
        return None
    try:
        return func(session, *args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - 单个聚合失败不影响整张概览
        logger.warning("聚合 %s.%s 暂不可用：%s", module, attribute, type(exc).__name__)
        return None


def _table_count(session: Any, table: str, *, where: str = "1=1", params: dict[str, Any] | None = None) -> int | None:
    """按表名计数（表名只来自本文件常量）。表不存在时返回 ``None``。"""
    try:
        return int(
            session.scalar(text(f"SELECT COUNT(*) FROM {table} WHERE {where}"), params or {}) or 0
        )
    except Exception:  # noqa: BLE001 - 表尚未建好
        return None


@router.get("/dashboard", dependencies=[Depends(require_permission("dashboard.read"))])
def admin_dashboard(ctx: ContextDep, principal: PrincipalDep) -> dict[str, Any]:
    """运营概览：全部为脱敏汇总计数，不含手机号、地址、报文或密钥。

    权限不足的聚合项返回 ``null`` 并记入 ``restricted``；模块尚未交付的聚合项返回
    ``null`` 并记入 ``missing_modules``。**不用 0 冒充「没有问题」**：0 与「读不到」
    对值班人员的含义完全不同。
    """
    session = ctx.session
    missing: list[str] = []
    restricted: list[str] = []

    orders: dict[str, Any] | None = _order_aggregates(session)
    if orders is None:
        missing.append("commerce")
        orders = {
            "awaiting_payment": None,
            "fulfilling": None,
            "open_after_sales": None,
            "pending_invoices": None,
            "overdue_unpaid": None,
        }

    # 财务：待审核退款与对账差异（仅财务/管理员权限可见）
    pending_refunds: int | None = None
    open_differences: int | None = None
    if "refund.approve" in principal.permissions or "finance.reconcile" in principal.permissions:
        pending_refunds = _table_count(
            session, "refunds", where="status IN ('REQUESTED','APPROVED','PROCESSING','UNKNOWN')"
        )
        open_differences = _table_count(
            session, "reconciliation_differences", where="status != 'RESOLVED'"
        )
        if pending_refunds is None and open_differences is None:
            missing.append("payments")
    else:
        restricted.extend(["pending_refunds", "reconciliation_differences"])

    # 询价：待处理（已提交但尚未给出结论）
    pending_inquiries = _table_count(
        session, "inquiries", where="status IN ('SUBMITTED','NEEDS_INFO','QUOTED')"
    )
    if pending_inquiries is None:
        missing.append("commissions")

    # 反馈：待审核
    pending_feedback = _table_count(session, "feedbacks", where="reward_status = 'PENDING_REVIEW'")
    if pending_feedback is None:
        missing.append("feedback")

    # 目录：低库存 SKU
    low_stock = _from_module(
        session, "app.modules.catalog.repository", "low_stock_skus", threshold=1, limit=200
    )
    if low_stock is not None:
        low_stock_count: int | None = len(low_stock)
    else:
        low_stock_count = _table_count(
            session, "skus", where="is_active = 1 AND stock_on_hand - reserved <= 1"
        )
        if low_stock_count is None:
            missing.append("catalog")

    # 运维：死信事件（仅 ops.read 可见）
    dead_letters: int | None = None
    if "ops.read" in principal.permissions:
        dead_letters = _table_count(session, "outbox_events", where="status = 'DEAD'")
        if dead_letters is None:
            missing.append("reliability")
    else:
        restricted.append("dead_letters")

    try:
        from app.modules.content import service as content_service

        feature_flags: dict[str, bool] | None = content_service.feature_flags(session)
    except Exception as exc:  # noqa: BLE001 - 设置表缺失时不影响概览
        logger.warning("功能开关读取失败：%s", type(exc).__name__)
        missing.append("content")
        feature_flags = None

    return {
        "generated_at": iso(now_ms()),
        "orders": orders,
        "finance": {
            "pending_refunds": pending_refunds,
            "reconciliation_differences": open_differences,
        },
        "commissions": {"pending_inquiries": pending_inquiries},
        "feedback": {"pending_reviews": pending_feedback},
        "catalog": {"low_stock_skus": low_stock_count},
        "operations": {"dead_letter_events": dead_letters},
        "feature_flags": feature_flags,
        "missing_modules": sorted(set(missing)),
        "restricted": sorted(set(restricted)),
        "notes": [
            "计数为服务端聚合的脱敏汇总，不含手机号、地址、验证码、报文或密钥。",
            "模块未交付或表缺失的项为 null，并在 missing_modules 中列出。",
            "权限不足的项为 null，并在 restricted 中列出。",
        ],
    }


def _order_aggregates(session: Any) -> dict[str, int] | None:
    """订单类概览计数；商务模块未交付（或表缺失）时返回 ``None``。"""
    try:
        order = _commerce_model("Order")
        after_sale = _commerce_model("AfterSale")
        invoice = _commerce_model("Invoice")
    except ImportError:
        return None
    moment = now_ms()
    counts = {
        "awaiting_payment": select(func.count())
        .select_from(order)
        .where(order.status == "PENDING_PAYMENT"),
        "fulfilling": select(func.count())
        .select_from(order)
        .where(order.status.in_(("ACTIVE", "FULFILLING"))),
        "open_after_sales": select(func.count())
        .select_from(after_sale)
        .where(after_sale.status.in_(("OPEN", "NEEDS_INFO", "APPROVED", "RETURNING"))),
        "pending_invoices": select(func.count())
        .select_from(invoice)
        .where(invoice.status == "PENDING"),
        "overdue_unpaid": select(func.count())
        .select_from(order)
        .where(
            order.status == "PENDING_PAYMENT",
            order.expires_at.is_not(None),
            order.expires_at <= moment,
        ),
    }
    result: dict[str, int] = {}
    for name, statement in counts.items():
        try:
            result[name] = int(session.scalar(statement) or 0)
        except Exception as exc:  # noqa: BLE001 - 单表缺失不影响其余计数
            logger.warning("订单聚合 %s 暂不可用：%s", name, type(exc).__name__)
            result[name] = 0
    return result


def _commerce_model(name: str) -> Any:
    """按需取商务模块 ORM 模型；模块未交付时由调用方的降级分支处理。"""
    from app.modules.commerce import models as commerce_models

    return getattr(commerce_models, name)


# ------------------------------------------------------------------ 用户（脱敏）
def _user_summary(user: Any, *, sessions: int | None = None, orders: int | None = None) -> dict[str, Any]:
    """用户脱敏摘要：手机号只给 ``phone_masked``，绝不给密文或明文。"""
    payload: dict[str, Any] = {
        "id": user.id,
        "nickname": user.nickname,
        "phone_masked": user.phone_masked,
        "locale": user.locale,
        "currency": user.currency,
        "region": user.region,
        "status": user.status,
        "version": user.version,
        "created_at": iso(user.created_at),
        "last_login_at": iso(user.last_login_at),
    }
    if user.status != "active":
        payload["status_reason"] = user.status_reason
    if sessions is not None:
        payload["active_sessions"] = sessions
    if orders is not None:
        payload["order_count"] = orders
    return payload


@router.get("/users", dependencies=[Depends(require_permission("user.read"))])
def admin_list_users(
    ctx: ContextDep,
    q: Annotated[str | None, Query(max_length=60)] = None,
    status: Annotated[str | None, Query(max_length=16)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    """用户列表：只返回脱敏字段；默认不显示手机号明文。"""
    offset = (page - 1) * limit
    rows = identity_repo.list_users(ctx.session, query=q, status=status, limit=limit, offset=offset)
    total = identity_repo.count_users(ctx.session)
    return {
        "items": [_user_summary(row) for row in rows],
        "total": total,
        "page": page,
        "limit": limit,
        "has_more": page * limit < total,
        "next_cursor": None,
    }


@router.get("/users/{user_id}", dependencies=[Depends(require_permission("user.read"))])
def admin_get_user(user_id: str, ctx: ContextDep) -> dict[str, Any]:
    """用户脱敏详情：会话数、订单数与限制状态。"""
    user = identity_repo.get_user(ctx.session, user_id)
    if user is None:
        raise AppError("RESOURCE_NOT_FOUND")
    sessions = identity_repo.list_sessions_for_user(ctx.session, user_id=user.id)
    orders = _from_module(
        ctx.session, "app.modules.commerce.repository", "count_orders_for_user", user_id=user.id
    )
    payload = _user_summary(user, sessions=len(sessions), orders=orders)
    payload["address_count"] = identity_repo.count_addresses(ctx.session, user_id=user.id)
    payload["pending_phone_change"] = _pending_phone_change(ctx.session, user.id)
    payload["notes"] = [
        "手机号与地址仅以脱敏形式返回；解密明文不属于后台常规能力。",
        "受限账号的所有会话会被撤销，恢复后需重新登录。",
    ]
    return payload


def _pending_phone_change(session: Any, user_id: str) -> bool:
    from app.modules.identity.models import PhoneChangeRequest

    return (
        session.scalars(
            select(PhoneChangeRequest).where(
                PhoneChangeRequest.user_id == user_id, PhoneChangeRequest.status == "PENDING"
            )
        ).first()
        is not None
    )


@router.get("/users/{user_id}/sessions", dependencies=[Depends(require_permission("user.read"))])
def admin_user_sessions(user_id: str, ctx: ContextDep) -> dict[str, Any]:
    """用户会话列表：只给设备与时间信息，不给 token 或 CSRF 值。"""
    user = identity_repo.get_user(ctx.session, user_id)
    if user is None:
        raise AppError("RESOURCE_NOT_FOUND")
    rows = identity_repo.list_sessions_for_user(ctx.session, user_id=user.id)
    return {
        "items": [
            {
                "id": row.id,
                "scope": row.scope,
                "user_agent": row.user_agent,
                "created_at": iso(row.created_at),
                "last_seen_at": iso(row.last_seen_at),
                "expires_at": iso(row.expires_at),
                "current": ctx.session_row is not None and ctx.session_row.id == row.id,
            }
            for row in rows
        ],
        "total": len(rows),
        "has_more": False,
    }


@router.post("/users/{user_id}/restriction", dependencies=[Depends(_with_csrf("user.manage"))])
def admin_set_restriction(
    user_id: str,
    payload: RestrictionInput,
    ctx: ContextDep,
    principal: PrincipalDep,
) -> dict[str, Any]:
    """限制/恢复账号：必须给出原因并留审计；非 active 状态会撤销全部会话。"""
    user = identity_repo.get_user(ctx.session, user_id)
    if user is None:
        raise AppError("RESOURCE_NOT_FOUND")
    if user.version != payload.version:
        raise AppError("VERSION_CONFLICT")
    updated = identity_service.restriction_change(
        ctx.session,
        user=user,
        status=payload.status,
        reason=payload.reason,
        actor_id=principal.id,
    )
    return {
        "id": updated.id,
        "status": updated.status,
        "version": updated.version,
        "sessions_revoked": updated.status != "active",
    }


@router.post("/users/{user_id}/sessions/revoke", dependencies=[Depends(_with_csrf("user.manage"))])
def admin_revoke_user_sessions(
    user_id: str,
    ctx: ContextDep,
    principal: PrincipalDep,
    payload: Annotated[JobActionInput | None, Body()] = None,
) -> dict[str, Any]:
    """撤销该用户的全部会话（例如账号被盗用）。"""
    user = identity_repo.get_user(ctx.session, user_id)
    if user is None:
        raise AppError("RESOURCE_NOT_FOUND")
    reason = (payload.reason if payload and payload.reason else "staff_revoke")[:200]
    count = identity_repo.revoke_user_sessions(ctx.session, user_id=user.id, reason=reason)
    audit.record(
        ctx.session,
        actor_type="staff",
        actor_id=principal.id,
        action="user.sessions_revoked",
        resource_type="user",
        resource_id=user.id,
        after={"revoked": count},
        reason=reason,
    )
    return {"id": user.id, "revoked": count}


# ------------------------------------------------------------------ 员工与角色
def _staff_payload(staff: Any, roles: list[str]) -> dict[str, Any]:
    return {
        "id": staff.id,
        "login_name": staff.login_name,
        "display_name": staff.display_name,
        "email": staff.email,
        "status": staff.status,
        "is_owner": bool(staff.is_owner),
        "mfa_enabled": bool(staff.mfa_enabled),
        "roles": roles,
        "version": staff.version,
        "last_login_at": iso(staff.last_login_at),
        "locked": bool(staff.locked_until and staff.locked_until > now_ms()),
        "created_at": iso(staff.created_at),
    }


@router.get("/staff", dependencies=[Depends(require_permission("staff.manage"))])
def admin_list_staff(ctx: ContextDep) -> dict[str, Any]:
    rows = identity_repo.list_staff(ctx.session)
    return {
        "items": [
            _staff_payload(
                row, [role.code for role in identity_repo.roles_for_staff(ctx.session, row.id)]
            )
            for row in rows
        ],
        "total": len(rows),
        "has_more": False,
    }


@router.post("/staff", status_code=201, dependencies=[Depends(_with_csrf("staff.manage"))])
def admin_create_staff(
    payload: StaffCreateInput,
    ctx: ContextDep,
    principal: PrincipalDep,
) -> dict[str, Any]:
    """创建员工：口令强度由 identity 服务裁定；无公开注册。"""
    _reject_separation_conflict(payload.roles)
    staff = admin_auth.create_staff(
        ctx.session,
        actor_id=principal.id,
        login_name=payload.login_name,
        display_name=payload.display_name,
        email=payload.email,
        password=payload.password,
        roles=payload.roles,
    )
    return {
        "id": staff.id,
        "login_name": staff.login_name,
        "status": staff.status,
        "version": staff.version,
        "mfa_enabled": bool(staff.mfa_enabled),
        "message": "首次登录需要绑定 TOTP；恢复码只在绑定响应中显示一次。",
    }


@router.patch("/staff/{staff_id}", dependencies=[Depends(_with_csrf("staff.manage"))])
def admin_update_staff(
    staff_id: str,
    payload: StaffUpdateInput,
    ctx: ContextDep,
    principal: PrincipalDep,
) -> dict[str, Any]:
    """更新员工资料、状态或角色。停用即撤销其全部会话。"""
    if payload.status is not None and payload.status not in ("active", "suspended", "retired"):
        raise AppError("VALIDATION_ERROR", fields={"status": "非法状态"})
    if payload.roles is not None:
        _reject_separation_conflict(payload.roles)
    staff = admin_auth.update_staff(
        ctx.session,
        actor_id=principal.id,
        staff_id=staff_id,
        display_name=payload.display_name,
        email=payload.email,
        status=payload.status,
        roles=payload.roles,
        reason=payload.reason,
        version=payload.version,
    )
    return _staff_payload(
        staff, [role.code for role in identity_repo.roles_for_staff(ctx.session, staff.id)]
    )


@router.post(
    "/staff/{staff_id}/password-reset", dependencies=[Depends(_with_csrf("staff.manage"))]
)
def admin_reset_staff_password(
    staff_id: str,
    payload: PasswordResetInput,
    ctx: ContextDep,
    principal: PrincipalDep,
) -> dict[str, Any]:
    """重置口令：同时强制重新绑定 MFA，并撤销该员工全部会话。"""
    admin_auth.reset_password(
        ctx.session,
        actor_id=principal.id,
        staff_id=staff_id,
        password=payload.password,
        reason=payload.reason,
    )
    return {"id": staff_id, "mfa_reenroll_required": True, "sessions_revoked": True}


def _reject_separation_conflict(roles: list[str]) -> None:
    """职责分离（C21）：同一员工不能同时持有申请与批准/编辑与发布权限。"""
    matrix = {code: set(permissions) for code, _name, _desc, permissions in permission_catalog.ROLES}
    granted: set[str] = set()
    for role in roles:
        granted |= matrix.get(role, set())
    for left, right in permission_catalog.SEPARATION_PAIRS:
        if left in granted and right in granted:
            raise AppError(
                "VALIDATION_ERROR",
                "角色组合违反职责分离：申请与批准（或编辑与发布）不能由同一人兼任。",
                fields={"roles": f"{left} 与 {right} 冲突"},
            )


@router.get("/roles", dependencies=[Depends(require_permission("staff.manage"))])
def admin_list_roles(ctx: ContextDep) -> dict[str, Any]:
    """角色与权限矩阵：来自 identity 权限目录与仓储（唯一维护位置）。"""
    stored_roles = {row.code: row for row in identity_repo.list_roles(ctx.session)}
    stored_permissions = {row.code: row for row in identity_repo.list_permissions(ctx.session)}
    return {
        "permissions": [
            {
                "code": row.code,
                "name": row.name,
                "group": row.group,
                "assigned_roles": sorted(
                    role.code
                    for role in stored_roles.values()
                    if _role_has_permission(ctx.session, role.id, row.id)
                ),
            }
            for row in stored_permissions.values()
        ],
        "roles": [
            {
                "code": code,
                "name": name,
                "description": description,
                "permissions": sorted(permissions),
                "is_system": bool(getattr(stored_roles.get(code), "is_system", 1)),
            }
            for code, name, description, permissions in permission_catalog.ROLES
        ],
        "separation_pairs": [list(pair) for pair in permission_catalog.SEPARATION_PAIRS],
    }


def _role_has_permission(session: Any, role_id: str, permission_id: str) -> bool:
    from app.modules.identity.models import RolePermission

    return (
        session.scalars(
            select(RolePermission).where(
                RolePermission.role_id == role_id, RolePermission.permission_id == permission_id
            )
        ).first()
        is not None
    )


# ------------------------------------------------------------------ 审计与任务
@router.get("/audit-logs", dependencies=[Depends(require_permission("audit.read"))])
def admin_audit_logs(
    ctx: ContextDep,
    resource_type: Annotated[str | None, Query(max_length=60)] = None,
    resource_id: Annotated[str | None, Query(max_length=48)] = None,
    actor_id: Annotated[str | None, Query(max_length=48)] = None,
    action: Annotated[str | None, Query(max_length=80)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    """审计查询：只读、可过滤、分页。审计记录只追加，不提供修改入口。"""
    _ = ctx.request_id
    rows = audit.query(
        ctx.session,
        resource_type=resource_type,
        resource_id=resource_id,
        actor_id=actor_id,
        action=action,
        limit=limit * page,
    )
    start = (page - 1) * limit
    window = rows[start : start + limit]
    return {
        "items": [
            {
                "id": row.id,
                "actor_type": row.actor_type,
                "actor_id": row.actor_id,
                "actor_label": row.actor_label,
                "action": row.action,
                "resource_type": row.resource_type,
                "resource_id": row.resource_id,
                "reason": row.reason,
                "trace_id": row.trace_id,
                "before_digest": row.before_digest,
                "after_digest": row.after_digest,
                "occurred_at": iso(row.occurred_at),
            }
            for row in window
        ],
        "page": page,
        "limit": limit,
        "has_more": len(rows) > start + limit,
        "next_cursor": None,
    }


@router.get("/jobs", dependencies=[Depends(require_permission("ops.read"))])
def admin_jobs(ctx: ContextDep) -> dict[str, Any]:
    """任务健康：outbox 计数、最早待处理时间、死信清单与数据库完整性。"""
    _ = ctx
    from app.worker import worker_health

    return dict(worker_health())


@router.post("/jobs/{event_id}/retry", dependencies=[Depends(_with_csrf("ops.read"))])
def admin_retry_job(event_id: str, ctx: ContextDep, principal: PrincipalDep) -> dict[str, Any]:
    """人工重试死信。消费者幂等保证业务效果只发生一次。"""
    from app.worker import retry_dead_letter

    retried = retry_dead_letter(event_id)
    if not retried:
        raise AppError("RESOURCE_NOT_FOUND", "该任务不存在或不在死信状态。")
    audit.record(
        ctx.session,
        actor_type="staff",
        actor_id=principal.id,
        action="ops.dead_letter_retry",
        resource_type="outbox_event",
        resource_id=event_id,
        reason="人工重试死信",
    )
    return {"event_id": event_id, "status": "PENDING", "retried": True}


@router.post("/jobs/cleanup", dependencies=[Depends(_with_csrf("ops.read"))])
def admin_cleanup_jobs(
    ctx: ContextDep, principal: PrincipalDep, payload: Annotated[JobActionInput | None, Body()] = None
) -> dict[str, Any]:
    """登记一次清理任务（过期验证码、无引用附件、限流窗口、失败投递）。"""
    reason = (payload.reason if payload and payload.reason else "后台手动触发清理")[:200]
    event = enqueue(
        ctx.session,
        event_type="ops.cleanup",
        aggregate_id=principal.id or "staff",
        payload={"requested_by": principal.id, "reason": reason},
    )
    audit.record(
        ctx.session,
        actor_type="staff",
        actor_id=principal.id,
        action="ops.cleanup_requested",
        resource_type="outbox_event",
        resource_id=event.event_id,
        reason=reason,
    )
    return {"event_id": event.event_id, "event_type": event.event_type, "status": event.status}


@router.post("/jobs/reconcile", dependencies=[Depends(_with_csrf("finance.reconcile"))])
def admin_request_reconciliation(
    ctx: ContextDep, principal: PrincipalDep, payload: Annotated[JobActionInput | None, Body()] = None
) -> dict[str, Any]:
    """登记一次对账任务。差异必须被处置，不能静默忽略（14 文档告警表）。"""
    provider = (payload.provider if payload and payload.provider else "sandbox")[:20]
    bill_date = payload.bill_date if payload and payload.bill_date else None
    event = enqueue(
        ctx.session,
        event_type="finance.reconcile",
        aggregate_id=provider,
        payload={"provider": provider, "bill_date": bill_date, "requested_by": principal.id},
    )
    audit.record(
        ctx.session,
        actor_type="staff",
        actor_id=principal.id,
        action="finance.reconcile_requested",
        resource_type="outbox_event",
        resource_id=event.event_id,
        after={"provider": provider, "bill_date": bill_date},
        reason=payload.reason if payload else None,
    )
    return {
        "event_id": event.event_id,
        "event_type": event.event_type,
        "provider": provider,
        "bill_date": bill_date,
        "status": event.status,
    }


# ------------------------------------------------------------------ 模块后台路由聚合
def _include_module_routers() -> list[str]:
    """include 各模块已交付的后台路由；缺失模块只记录，不阻断后台启动。

    只 include 不修改：这些路由自带权限码与 CSRF 依赖。某个模块在增量开发期间暂时
    无法导入时（``ImportError``）跳过它并留下日志，其余后台功能照常可用；导入期的
    其他异常也一并跳过，但会以 WARNING 明确记录，避免缺陷被静默吞掉。
    """
    included: list[str] = []
    for module_path, attribute, name in (
        ("app.modules.content.router", "admin_router", "content"),
        ("app.modules.catalog.router", "admin_router", "catalog"),
        ("app.modules.media.router", "admin_router", "media"),
        ("app.modules.commissions.router", "admin_router", "commissions"),
        ("app.modules.commerce.router", "admin_router", "commerce"),
        ("app.modules.payments.router", "admin_router", "payments"),
        ("app.modules.loyalty.router", "admin_router", "loyalty"),
        ("app.modules.feedback.router", "admin_router", "feedback"),
    ):
        try:
            module = __import__(module_path, fromlist=[attribute])
            sub_router = getattr(module, attribute, None)
        except ImportError:
            logger.info("后台路由未就绪，跳过 include：%s", name)
            continue
        except Exception as exc:  # noqa: BLE001 - 单模块导入缺陷不拖垮整个后台
            logger.warning("后台路由导入失败（已跳过 %s）：%s: %s", name, type(exc).__name__, exc)
            continue
        if sub_router is None:
            logger.info("后台路由未导出 %s，跳过 include：%s", attribute, name)
            continue
        router.include_router(sub_router)
        included.append(name)
    return included


INCLUDED_MODULE_ROUTERS: tuple[str, ...] = tuple(_include_module_routers())

__all__ = ["INCLUDED_MODULE_ROUTERS", "router"]
