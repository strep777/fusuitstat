"""支付回调验签入口（渠道无关）。

职责边界：

1. **先留证再验签**：收到回调立刻把原始字节写到
   ``storage/payment_events/<digest>.payload``，库里只记摘要与路径——
   复核验签、举证与审计都靠这份原始字节（08 文档、05 文档）。
2. **验签与解密**交给渠道适配器（``verify_notification_detailed``）；本层不解释
   渠道报文细节。
3. **不入账的三种情形**都要留痕：验签失败（安全事件）、验签通过但非成功收款
   （忽略）、渠道未配置（暂时不可用）。任何情况下都不存在「先记成功再说」。
4. **成功入账**交给 ``service.apply_verified_payment``：去重事件 + 收款 + 分配 +
   应付状态 + 库存/名额 + outbox 一并提交；通知已处理则返回原成功结果。
5. 本层**不做** CSRF/Origin 校验（支付通知不携带用户 cookie，见
   ``app/api/dependencies.py`` 的 ``enforce_write_guard`` 白名单）；渠道握手凭据
   就是它的签名，IP 限制只作辅助。

HTTP 层拿到返回值后提交事务，并按渠道协议原样应答（微信 JSON、支付宝纯文本、
PayPal 空体）。事务失败时**不给渠道成功应答**，等重试或主动查单。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy.orm import Session

from app.adapters.base import GatewayError, GatewayNotConfigured
from app.adapters.payments import get_gateway
from app.adapters.payments.base import ACK_TEXT_KEY, NotificationVerdict, payload_digest
from app.modules.payments import service as svc
from app.platform.errors import AppError

#: 渠道未配置/暂时不可用时给渠道的应答体（不泄露内部细节）。
_UNAVAILABLE_ACK: dict[str, Any] = {ACK_TEXT_KEY: "failure"}


def _verdict(gateway: Any, headers: Mapping[str, str], raw_body: bytes) -> NotificationVerdict:
    """取带原因的验签结论；适配器没有明细接口时退回 ``verify_notification``。"""
    detailed = getattr(gateway, "verify_notification_detailed", None)
    if callable(detailed):
        verdict = detailed(headers, raw_body)
        if isinstance(verdict, NotificationVerdict):
            return verdict
        raise AppError("INTERNAL_ERROR", "支付渠道验签接口返回了非法结论。")
    payment = gateway.verify_notification(headers, raw_body)
    if payment is None:
        return NotificationVerdict(accepted=False, reason="verification_failed")
    return NotificationVerdict(
        accepted=True, reason="verified", payment=payment, event_id=payment.event_id
    )


def handle_notification(
    session: Session,
    *,
    provider: str,
    headers: Mapping[str, str],
    raw_body: bytes,
) -> dict[str, Any]:
    """处理一次渠道回调，返回给 HTTP 层的应答与状态。

    返回字典：``accepted``、``status``、``duplicate``、``http_status``、``ack``。
    """
    provider = (provider or "").strip().lower()
    digest, payload_path = svc.persist_raw_payload(raw_body)
    try:
        gateway = get_gateway(provider)
    except AppError:
        # 渠道未启用：不验签、不入账，也不冒充成功。
        return {
            "accepted": False,
            "status": "PAYMENT_PROVIDER_DISABLED",
            "duplicate": False,
            "http_status": 404,
            "ack": dict(_UNAVAILABLE_ACK),
        }
    try:
        verdict = _verdict(gateway, headers, raw_body)
    except GatewayNotConfigured:
        svc.record_rejected_notification(
            session,
            provider=provider,
            raw_digest=digest,
            payload_path=payload_path,
            event_key=None,
            reason="provider_not_configured",
        )
        return {
            "accepted": False,
            "status": "PROVIDER_UNAVAILABLE",
            "duplicate": False,
            "http_status": 503,
            "ack": dict(_UNAVAILABLE_ACK),
        }
    except GatewayError:
        svc.record_rejected_notification(
            session,
            provider=provider,
            raw_digest=digest,
            payload_path=payload_path,
            event_key=None,
            reason="verification_backend_error",
        )
        return {
            "accepted": False,
            "status": "PROVIDER_UNAVAILABLE",
            "duplicate": False,
            "http_status": 503,
            "ack": dict(_UNAVAILABLE_ACK),
        }

    if not verdict.accepted:
        # 错签名/过期重放/未知证书序列/解密失败：不入账，记安全事件。
        svc.record_rejected_notification(
            session,
            provider=provider,
            raw_digest=digest,
            payload_path=payload_path,
            event_key=verdict.event_id,
            reason=verdict.reason,
        )
        return {
            "accepted": False,
            "status": "REJECTED",
            "reason": verdict.reason,
            "duplicate": False,
            "http_status": 403,
            "ack": gateway.ack_failure(verdict.reason),
        }

    if verdict.payment is None:
        svc.record_ignored_notification(
            session,
            provider=provider,
            raw_digest=digest,
            payload_path=payload_path,
            event_key=verdict.event_id,
            event_type=verdict.event_type,
        )
        return {
            "accepted": True,
            "status": "IGNORED",
            "reason": verdict.reason,
            "duplicate": False,
            "http_status": 200,
            "ack": gateway.ack_success(),
        }

    result = svc.apply_verified_payment(session, verdict.payment)
    return {
        "accepted": True,
        "status": str(result.get("status")),
        "duplicate": bool(result.get("duplicate")),
        "paid": result.get("status") in ("APPLIED", "OVERPAYMENT"),
        "http_status": 200,
        "ack": gateway.ack_success(),
    }


def raw_payload_digest(raw_body: bytes) -> str:
    """回调原始字节摘要（测试与排障用）。"""
    return payload_digest(raw_body)


__all__ = ["handle_notification", "raw_payload_digest"]
