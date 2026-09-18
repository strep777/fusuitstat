"""支付模块 HTTP 路由（客户端侧）。

挂载在 ``/api/v1`` 下（``app/api/public.py`` 统一装配），路径不带前缀：

- ``POST /installments/{id}/payment-attempts``：本人 + 幂等键；只接收 provider/scene，
  **不接收商户号或金额**；
- ``GET /payment-attempts/{id}``：本人；返回本地权威状态，按策略触发受限查单；
- ``POST /payment-notifications/{wechat,alipay,paypal}``：渠道验签，不要求用户 cookie，
  也不做 CSRF/Origin 校验（见 ``enforce_write_guard`` 的支付通知白名单）；
- 支付尝试列表：优先 ``GET /orders/{id}/payments``；若商务模块已经提供同名路径，
  自动改用 ``GET /payment-attempts?order_id=...``，避免重复注册路由导致启动失败。

后台退款/对账路由在同目录的 ``admin_router.py``，由 ``app/api/admin_routes.py`` 统一挂载
（含权限码、职责分离与审计）。
"""

from __future__ import annotations

import contextlib
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

from app.adapters.payments.base import ACK_TEXT_KEY
from app.api.dependencies import ContextDep, CsrfDep, CustomerIdDep, enforce_write_guard
from app.modules.payments import notifications as notification_entry
from app.modules.payments import repository as repo
from app.modules.payments import service as svc
from app.modules.payments.admin_router import admin_router
from app.modules.payments.models import ACTIVE_ATTEMPT_STATUSES
from app.modules.payments.schemas import (
    PaymentAction,
    PaymentAttemptCreateRequest,
    PaymentAttemptListItem,
    PaymentAttemptPage,
    PaymentAttemptResponse,
    PaymentAttemptStatusResponse,
)
from app.modules.shared.schemas import LimitField
from app.platform.config import get_settings
from app.platform.errors import AppError
from app.platform.ids import iso
from app.platform.reliability import digest

public_router = APIRouter(tags=["payments"])

#: 渠道回调路由（sandbox 只在开发/测试环境可达）。
NOTIFICATION_PROVIDERS = ("wechat", "alipay", "paypal")


async def _raw_body(request: Request) -> bytes:
    """回调原始字节：验签与留证都必须基于未经解析的原始内容。"""
    return await request.body()


RawBodyDep = Annotated[bytes, Depends(_raw_body)]


# ------------------------------------------------------------------ 创建支付
@public_router.post(
    "/installments/{installment_id}/payment-attempts",
    response_model=PaymentAttemptResponse,
    status_code=201,
)
def create_payment_attempt(
    installment_id: str,
    payload: PaymentAttemptCreateRequest,
    ctx: ContextDep,
    _csrf: CsrfDep,
    request: Request,
    user_id: CustomerIdDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> PaymentAttemptResponse:
    """为应付项创建可支付参数。金额与商户单号都由服务端裁定。"""
    enforce_write_guard(request)
    call = svc.begin_idempotent(
        ctx.session,
        actor_id=user_id,
        operation="payment.create",
        key=idempotency_key or "",
        request_hash=digest(
            {"installment_id": installment_id, "provider": payload.provider, "scene": payload.scene}
        ),
    )
    if call.replay is not None:
        # 同键重试返回同资源，不重复下单。
        return PaymentAttemptResponse(**call.replay)
    intent = svc.create_intent(
        ctx.session,
        installment_id=installment_id,
        provider=payload.provider,
        scene=payload.scene,
        actor_id=user_id,
    )
    body = _attempt_response(intent)
    svc.complete_idempotent(
        ctx.session, call, resource_id=intent.attempt_id, snapshot=body.model_dump()
    )
    ctx.session.commit()
    return body


def _attempt_response(intent: Any) -> PaymentAttemptResponse:
    return PaymentAttemptResponse(
        payment_attempt_id=intent.attempt_id,
        status=intent.status,
        action=PaymentAction(
            type=str(intent.action.get("type") or "none"),
            url=intent.action.get("url") or None,
        ),
        expires_at=iso(intent.expires_at),
    )


# ------------------------------------------------------------------ 查询支付状态
@public_router.get("/payment-attempts/{attempt_id}", response_model=PaymentAttemptStatusResponse)
def get_payment_attempt(attempt_id: str, ctx: ContextDep) -> PaymentAttemptStatusResponse:
    """本地权威状态；活动状态按策略触发受限查单（失败不影响本地状态返回）。"""
    user_id = ctx.principal.require_customer()
    attempt = repo.get_attempt(ctx.session, attempt_id)
    if attempt is None or attempt.user_id != user_id:
        raise AppError("RESOURCE_NOT_FOUND")
    if attempt.status in ACTIVE_ATTEMPT_STATUSES:
        with contextlib.suppress(AppError):
            # 渠道暂时不可用：仍返回本地状态，不能把「确认中」渲染成已取消。
            attempt = svc.query_attempt(ctx.session, attempt)
    return PaymentAttemptStatusResponse(
        id=attempt.id,
        status=attempt.status,
        order_id=attempt.order_id,
        verified_at=iso(attempt.verified_at),
    )


# ------------------------------------------------------------------ 支付尝试列表
def _attempt_page(
    ctx: ContextDep, *, user_id: str, order_id: str, limit: int
) -> PaymentAttemptPage:
    from app.modules.payments import repository_bridge as bridge

    order = bridge.load_order(ctx.session, order_id)
    if order is None or order.get("user_id") != user_id:
        raise AppError("RESOURCE_NOT_FOUND")
    rows = repo.list_attempts_for_order(ctx.session, order_id, limit=limit)
    return PaymentAttemptPage(
        items=[
            PaymentAttemptListItem(
                id=row.id,
                provider=row.provider,
                scene=row.scene,
                status=row.status,
                expected_cents=row.expected_cents,
                currency=row.currency,
                action_type=(row.action or {}).get("type"),
                created_at=iso(row.created_at),
                verified_at=iso(row.verified_at),
            )
            for row in rows
        ],
        next_cursor=None,
        has_more=False,
    )


@public_router.get("/payment-attempts", response_model=PaymentAttemptPage)
def list_payment_attempts(
    ctx: ContextDep,
    order_id: Annotated[str, Query(min_length=3, max_length=48)],
    user_id: CustomerIdDep,
    limit: LimitField = 20,
) -> PaymentAttemptPage:
    """按订单列出已发起的支付尝试（本人）。"""
    return _attempt_page(ctx, user_id=user_id, order_id=order_id, limit=limit)


def list_order_payment_attempts(
    order_id: str, ctx: ContextDep, user_id: CustomerIdDep, limit: LimitField = 20
) -> PaymentAttemptPage:
    """``GET /orders/{id}/payments`` 的处理函数（仅在商务模块未占用该路径时注册）。"""
    return _attempt_page(ctx, user_id=user_id, order_id=order_id, limit=limit)


def _commerce_declares_order_payments_route() -> bool:
    """商务模块是否已提供 ``/orders/{id}/payments``。

    路由重复注册会让应用启动直接失败，因此这里做一次只读探测：探测失败（模块尚未
    就绪）或不存在时由支付模块提供该路径，存在时改用查询参数形式。
    """
    try:
        from app.modules.commerce.router import public_router as commerce_public
    except ImportError:
        return False
    candidates = {"/orders/{order_id}/payments", "/orders/{id}/payments"}
    return any(
        getattr(route, "path", None) in candidates for route in getattr(commerce_public, "routes", ())
    )


if not _commerce_declares_order_payments_route():
    public_router.add_api_route(
        "/orders/{order_id}/payments",
        list_order_payment_attempts,
        methods=["GET"],
        response_model=PaymentAttemptPage,
        name="list_order_payment_attempts",
        tags=["payments"],
    )


# ------------------------------------------------------------------ 渠道回调
def _notification_response(provider: str, request: Request, ctx: ContextDep, raw_body: bytes) -> Response:
    result = notification_entry.handle_notification(
        ctx.session, provider=provider, headers=request.headers, raw_body=raw_body
    )
    # 先提交业务与留证，再按渠道协议应答；提交失败会让渠道重试。
    ctx.session.commit()
    status_code = int(result.get("http_status") or 200)
    ack = dict(result.get("ack") or {})
    # 应答体必须符合渠道协议；本地处理结论放在响应头，便于排障与运维核对。
    headers = {
        "X-Payment-Status": str(result.get("status") or ""),
        "X-Payment-Duplicate": "1" if result.get("duplicate") else "0",
    }
    if ACK_TEXT_KEY in ack:
        return PlainTextResponse(str(ack[ACK_TEXT_KEY]), status_code=status_code, headers=headers)
    return JSONResponse(ack, status_code=status_code, headers=headers)


@public_router.post("/payment-notifications/wechat", include_in_schema=True)
def wechat_notification(request: Request, ctx: ContextDep, raw_body: RawBodyDep) -> Response:
    """微信支付回调：验签 + 解密后落实收款效果。"""
    return _notification_response("wechat", request, ctx, raw_body)


@public_router.post("/payment-notifications/alipay", include_in_schema=True)
def alipay_notification(request: Request, ctx: ContextDep, raw_body: RawBodyDep) -> Response:
    """支付宝异步通知：RSA2 验签后落实收款效果。"""
    return _notification_response("alipay", request, ctx, raw_body)


@public_router.post("/payment-notifications/paypal", include_in_schema=True)
def paypal_notification(request: Request, ctx: ContextDep, raw_body: RawBodyDep) -> Response:
    """PayPal Webhook：证书验签后落实收款效果。"""
    return _notification_response("paypal", request, ctx, raw_body)


@public_router.post("/payment-notifications/stripe", include_in_schema=True)
def stripe_notification(request: Request, ctx: ContextDep, raw_body: RawBodyDep) -> Response:
    """Stripe Webhook：``Stripe-Signature`` 验签后落实收款效果。

    未配置签名密钥时按渠道未配置处理（记安全事件 + 5xx 应答），
    **不会**因为"没有密钥"就放行或入账。
    """
    return _notification_response("stripe", request, ctx, raw_body)


@public_router.post("/payment-notifications/sandbox", include_in_schema=False)
def sandbox_notification(request: Request, ctx: ContextDep, raw_body: RawBodyDep) -> Response:
    """沙箱回调（仅开发/测试环境）：生产环境该路径必须 404。"""
    if get_settings().app_env not in ("development", "test"):
        raise AppError("RESOURCE_NOT_FOUND")
    return _notification_response("sandbox", request, ctx, raw_body)


__all__ = ["public_router", "admin_router"]
