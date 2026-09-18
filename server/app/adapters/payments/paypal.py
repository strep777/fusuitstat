"""PayPal 适配器骨架。

**签名算法与证书模式必须以商户当前官方文档为准**：PayPal Webhook 校验使用
``PAYPAL-TRANSMISSION-ID``、``PAYPAL-TRANSMISSION-TIME``、``PAYPAL-TRANSMISSION-SIG``、
``PAYPAL-CERT-URL`` 与 ``PAYPAL-AUTH-ALGO``，待验签消息为
``transmissionId|transmissionTime|webhookId|crc32(rawBody)``，用 ``PAYPAL-CERT-URL``
下载的证书公钥做 RSA-SHA256 验签；也可以调用
``/v1/notifications/verify-webhook-signature`` 由 PayPal 校验（需要 OAuth2 令牌）。
本文件实现本地证书校验，并把证书缓存到 ``storage/payment_certs``。

安全要求：``PAYPAL-CERT-URL`` 必须落在 PayPal 官方域名白名单（防止 SSRF 与伪造证书）、
传输时间超出重放窗口拒绝、``webhook_id`` 必须与配置一致、金额与币种按 decimal 字符串
精确转整数分。未配置 webhook_id 或缺少证书时 fail closed，绝不降级到沙箱。

客户端密钥同样不在 ``Settings`` 字段中（属待补配置项），从环境变量
``PAYPAL_CLIENT_SECRET`` 读取；缺失时交易类方法 fail closed。
"""

from __future__ import annotations

import base64
import json
import os
import zlib
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from app.adapters.base import GatewayError, GatewayNotConfigured
from app.adapters.payments.base import (
    ACK_TEXT_KEY,
    ACTION_QR,
    ACTION_REDIRECT,
    PAYMENT_RESULT_CLOSED,
    PAYMENT_RESULT_PENDING,
    REFUND_RESULT_PROCESSING,
    REFUND_RESULT_SUCCEEDED,
    NotificationVerdict,
    VerifiedPayment,
    business_event_key,
    cents_from_decimal_text,
    decimal_text_from_cents,
    header_value,
    payload_digest,
)
from app.platform.config import Settings
from app.platform.ids import now_ms

API_HOST = "https://api-m.paypal.com"
SANDBOX_API_HOST = "https://api-m.sandbox.paypal.com"
#: Webhook 证书只能来自这些官方主机（含沙箱）。
ALLOWED_CERT_HOSTS = ("api.paypal.com", "api-m.paypal.com", "api.sandbox.paypal.com", "api-m.sandbox.paypal.com")
#: 传输时间重放窗口（毫秒）。
REPLAY_WINDOW_MS = 300_000
CAPTURE_COMPLETED_EVENTS = ("PAYMENT.CAPTURE.COMPLETED", "CHECKOUT.ORDER.APPROVED")


class PaypalGateway:
    """PayPal 通道。``name`` 固定为 ``paypal``。"""

    name = "paypal"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    # ------------------------------------------------------------ 配置
    @property
    def certificate_dir(self) -> Path:
        return self._settings.storage_root / "payment_certs"

    def _webhook_id(self) -> str:
        if not self._settings.paypal_webhook_id:
            raise GatewayNotConfigured("paypal:webhook-id")
        return self._settings.paypal_webhook_id

    def _client_secret(self) -> str:
        secret = os.environ.get("PAYPAL_CLIENT_SECRET", "").strip()
        if not secret:
            raise GatewayNotConfigured("paypal:client-secret")
        return secret

    def _ensure_trading_configured(self) -> None:
        if not self._settings.paypal_client_id:
            raise GatewayNotConfigured("paypal:client-id")
        self._client_secret()

    def _ensure_verification_configured(self) -> None:
        self._webhook_id()

    def _api_host(self) -> str:
        return SANDBOX_API_HOST if self._settings.app_env in ("development", "test") else API_HOST

    # ------------------------------------------------------------ 证书
    def _certificate(self, cert_url: str) -> Any:
        """下载并缓存 PayPal Webhook 证书（只允许官方域名，防 SSRF）。"""
        host = urlparse(cert_url).hostname or ""
        if not cert_url.startswith("https://") or not any(
            host == allowed or host.endswith(f".{allowed}") for allowed in ALLOWED_CERT_HOSTS
        ):
            return None
        target = self.certificate_dir / f"paypal_{zlib.crc32(cert_url.encode('utf-8')):08x}.pem"
        if target.is_file():
            pem = target.read_bytes()
        else:
            try:
                with httpx.Client(timeout=8.0) as client:
                    response = client.get(cert_url)
                    response.raise_for_status()
            except httpx.HTTPError as exc:
                raise GatewayError("paypal 证书下载失败", retryable=True) from exc
            pem = response.content
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(pem)
        from cryptography import x509

        try:
            return x509.load_pem_x509_certificate(pem).public_key()
        except ValueError:
            return None

    # ------------------------------------------------------------ 渠道能力
    def _token(self) -> str:
        self._ensure_trading_configured()
        try:
            with httpx.Client(timeout=8.0) as client:
                response = client.post(
                    f"{self._api_host()}/v1/oauth2/token",
                    data={"grant_type": "client_credentials"},
                    auth=(self._settings.paypal_client_id, self._client_secret()),
                )
        except httpx.TimeoutException as exc:
            raise GatewayError("paypal 取令牌超时", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise GatewayError(f"paypal 取令牌失败：{type(exc).__name__}", retryable=True) from exc
        if response.status_code >= 400:
            raise GatewayError("paypal 拒绝取令牌", retryable=False)
        return str(response.json().get("access_token") or "")

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        token = self._token()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        try:
            with httpx.Client(timeout=10.0) as client:
                response = client.request(
                    method, f"{self._api_host()}{path}", json=body, headers=headers
                )
        except httpx.TimeoutException as exc:
            raise GatewayError("paypal 请求超时", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise GatewayError(f"paypal 请求失败：{type(exc).__name__}", retryable=True) from exc
        if response.status_code >= 500:
            raise GatewayError("paypal 服务端错误", retryable=True)
        if response.status_code >= 400:
            raise GatewayError(f"paypal 拒绝请求（{response.status_code}）", retryable=False)
        if not response.content:
            return {}
        return dict(response.json())

    def create(
        self,
        merchant_order_no: str,
        amount_cents: int,
        currency: str,
        scene: str,
        subject: str,
        return_url: str | None,
    ) -> dict[str, Any]:
        """创建 PayPal 订单并返回审批跳转地址。

        ``custom_id`` 携带商户单号，回调时用它关联本地支付尝试。
        """
        self._ensure_trading_configured()
        payload = {
            "intent": "CAPTURE",
            "purchase_units": [
                {
                    "custom_id": merchant_order_no,
                    "invoice_id": merchant_order_no,
                    "description": subject[:120],
                    "amount": {"currency_code": currency, "value": decimal_text_from_cents(amount_cents)},
                }
            ],
            "application_context": {
                "return_url": return_url or self._settings.public_origin,
                "cancel_url": return_url or self._settings.public_origin,
                "user_action": "PAY_NOW",
            },
        }
        result = self._request("POST", "/v2/checkout/orders", payload)
        approve_url = ""
        for link in result.get("links", []):
            if link.get("rel") in ("approve", "payer-action"):
                approve_url = str(link.get("href") or "")
        action_type = ACTION_QR if scene == "native" else ACTION_REDIRECT
        return {
            "status": PAYMENT_RESULT_PENDING,
            "action": {"type": action_type, "url": approve_url},
            "provider_transaction_id": result.get("id"),
            "expires_at": None,
            "raw_digest": payload_digest(f"{merchant_order_no}:{amount_cents}".encode()),
        }

    def query(self, merchant_order_no: str) -> dict[str, Any]:
        """PayPal 没有「按商户单号查单」的公开接口。

        查单依赖 Reporting/Transaction Search（需在商户后台单独开通并对齐字段），
        未开通时 fail closed：不返回任何「成功」推断。PayPal 业务的成功只认
        已验签 Webhook（08 文档：成功只能由已验签通知或服务端主动查单确认）。
        """
        _ = merchant_order_no
        raise GatewayNotConfigured("paypal:transaction-search")

    def _capture_for(self, merchant_order_no: str) -> tuple[str, str] | None:
        """按 ``custom_id``（= 商户单号）定位捕获号与币种。"""
        result = self._request("GET", f"/v2/checkout/orders?query={merchant_order_no}&page_size=5")
        for order in result.get("orders") or []:
            units = order.get("purchase_units") or []
            if not units:
                continue
            if str(units[0].get("custom_id") or "") != merchant_order_no:
                continue
            captures = (units[0].get("payments") or {}).get("captures") or []
            if captures:
                amount = captures[0].get("amount") or {}
                return str(captures[0].get("id") or ""), str(amount.get("currency_code") or "CNY")
        return None

    def close(self, merchant_order_no: str) -> dict[str, Any]:
        """PayPal 未付款订单会自然过期，没有「关闭」语义；超时后不得重复捕获。"""
        _ = merchant_order_no
        return {"status": PAYMENT_RESULT_CLOSED}

    def refund(
        self, merchant_order_no: str, refund_no: str, amount_cents: int, reason: str
    ) -> dict[str, Any]:
        """按原捕获记录原路退款；重试使用同一 ``refund_no``（invoice_id）。"""
        self._ensure_trading_configured()
        capture = self._capture_for(merchant_order_no)
        if capture is None:
            raise GatewayError("paypal 未找到可退款的捕获记录", retryable=False)
        capture_id, currency = capture
        body = {
            "amount": {"currency_code": currency, "value": decimal_text_from_cents(amount_cents)},
            "invoice_id": refund_no,
            "note_to_payer": reason[:255],
        }
        try:
            result = self._request("POST", f"/v2/payments/captures/{capture_id}/refund", body)
        except GatewayError as exc:
            if exc.retryable:
                # 网络/超时：结果未知，保留退款预约额度并继续查单。
                return {"status": REFUND_RESULT_PROCESSING, "provider_refund_id": refund_no}
            raise
        status = str(result.get("status") or "")
        return {
            "status": REFUND_RESULT_SUCCEEDED if status == "COMPLETED" else REFUND_RESULT_PROCESSING,
            "provider_refund_id": str(result.get("id") or refund_no),
            "amount_cents": amount_cents,
        }

    def query_refund(self, refund_no: str) -> dict[str, Any]:
        try:
            result = self._request("GET", f"/v2/payments/refunds/{refund_no}")
        except GatewayError as exc:
            if exc.retryable:
                raise
            return {"status": "NOT_FOUND", "provider_refund_id": None}
        status = str(result.get("status") or "")
        amount = result.get("amount") or {}
        cents = cents_from_decimal_text(str(amount.get("value") or "0"))
        mapped = REFUND_RESULT_SUCCEEDED if status == "COMPLETED" else REFUND_RESULT_PROCESSING
        return {"status": mapped, "provider_refund_id": refund_no, "amount_cents": cents}

    def download_bill(self, bill_date: str) -> bytes:
        """PayPal 的交易明细通过 Reporting/Transaction Search API 获取（需额外开通）。"""
        _ = bill_date
        raise GatewayNotConfigured("paypal:reporting-api")

    def parse_bill(self, bill_date: str, raw: bytes) -> list[dict[str, Any]]:
        _ = bill_date, raw
        raise GatewayNotConfigured("paypal:reporting-api")

    # ------------------------------------------------------------ 回调验签
    def verify_notification_detailed(
        self, headers: Mapping[str, str], raw_body: bytes
    ) -> NotificationVerdict:
        webhook_id = self._webhook_id()
        transmission_id = header_value(headers, "Paypal-Transmission-Id")
        transmission_time = header_value(headers, "Paypal-Transmission-Time")
        signature = header_value(headers, "Paypal-Transmission-Sig")
        cert_url = header_value(headers, "Paypal-Cert-Url")
        auth_algo = header_value(headers, "Paypal-Auth-Algo") or "SHA256withRSA"
        if not transmission_id or not transmission_time or not signature or not cert_url:
            return NotificationVerdict(accepted=False, reason="headers_incomplete")
        moment = _iso_to_ms(transmission_time)
        if moment is None or abs(now_ms() - moment) > REPLAY_WINDOW_MS:
            return NotificationVerdict(accepted=False, reason="timestamp_out_of_window")
        if auth_algo != "SHA256withRSA":
            return NotificationVerdict(accepted=False, reason="unsupported_auth_algo")
        public_key = self._certificate(cert_url)
        if public_key is None:
            return NotificationVerdict(accepted=False, reason="certificate_unavailable")
        message = (
            f"{transmission_id}|{transmission_time}|{webhook_id}|{zlib.crc32(raw_body) & 0xFFFFFFFF}"
        ).encode()
        if not _rsa_verify(public_key, message, signature):
            return NotificationVerdict(accepted=False, reason="signature_mismatch")
        try:
            body = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return NotificationVerdict(accepted=False, reason="payload_malformed")
        event_id = str(body.get("id") or "") or None
        event_type = str(body.get("event_type") or "")
        resource = body.get("resource") or {}
        if event_type not in CAPTURE_COMPLETED_EVENTS:
            return NotificationVerdict(
                accepted=True, reason="not_a_success_payment", event_id=event_id, event_type=event_type
            )
        amount = resource.get("amount") or {}
        cents = cents_from_decimal_text(str(amount.get("value") or "0"))
        order_no = str(resource.get("custom_id") or resource.get("invoice_id") or "")
        if not order_no or cents is None:
            return NotificationVerdict(accepted=False, reason="payload_incomplete", event_id=event_id)
        account_ref = f"client_id:{self._settings.paypal_client_id}" if self._settings.paypal_client_id else None
        reason = "verified"
        expected = self.expected_account_ref()
        if expected and account_ref and account_ref != expected:
            reason = "verified_account_mismatch"
        payment = VerifiedPayment(
            provider=self.name,
            provider_transaction_id=str(resource.get("id") or ""),
            merchant_order_no=order_no,
            amount_cents=cents,
            currency=str(amount.get("currency_code") or "USD"),
            paid_at=_iso_to_ms(resource.get("create_time")) or moment,
            raw_digest=payload_digest(raw_body),
            event_id=event_id or business_event_key(
                self.name, str(resource.get("id") or ""), order_no, str(cents)
            ),
            account_ref=account_ref,
        )
        return NotificationVerdict(
            accepted=True, reason=reason, payment=payment, event_id=payment.event_id, event_type=event_type
        )

    def verify_notification(
        self, headers: Mapping[str, str], raw_body: bytes
    ) -> VerifiedPayment | None:
        return self.verify_notification_detailed(headers, raw_body).payment

    def expected_account_ref(self) -> str | None:
        if not self._settings.paypal_client_id:
            return None
        return f"client_id:{self._settings.paypal_client_id}"

    def ack_success(self) -> dict[str, Any]:
        # PayPal 只检查 HTTP 2xx，应答体可为空。
        return {}

    def ack_failure(self, reason: str) -> dict[str, Any]:
        _ = reason
        return {ACK_TEXT_KEY: ""}


def _rsa_verify(public_key: Any, message: bytes, signature_b64: str) -> bool:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    try:
        public_key.verify(base64.b64decode(signature_b64), message, padding.PKCS1v15(), hashes.SHA256())
    except (InvalidSignature, ValueError, TypeError):
        return False
    return True


def _iso_to_ms(value: Any) -> int | None:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return int(moment.timestamp() * 1000)


__all__ = [
    "ALLOWED_CERT_HOSTS",
    "API_HOST",
    "CAPTURE_COMPLETED_EVENTS",
    "REPLAY_WINDOW_MS",
    "SANDBOX_API_HOST",
    "PaypalGateway",
]
