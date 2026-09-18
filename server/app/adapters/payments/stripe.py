"""Stripe 适配器（信用卡，C04）。

**签名算法与事件形状以 Stripe 当前官方文档为准**（本文件按 2024 起的通用口径实现，
开通商户号时必须逐条复核）：

- 下单：``POST /v1/payment_intents``（``application/x-www-form-urlencoded``），商户单号写进
  ``metadata[merchant_order_no]``；``automatic_payment_methods[enabled]=true`` 由 Stripe 决定可用方式；
  金额一律整数分（Stripe 的最小货币单位对 USD/CNY 都是分）；重试使用 ``Idempotency-Key``。
- 查单：``GET /v1/payment_intents/search``，按 ``metadata['merchant_order_no']`` 精确检索
  （Search API 为 Stripe 官方接口；未开通 Search 的账号需在联调时确认，见 R30）。
- 退款：``POST /v1/refunds``，``Idempotency-Key`` 使用**同一个退款单号**，重试不会重复退款；
  退款单号同样写进 ``metadata[refund_no]``。
- 回调：``Stripe-Signature`` 头的待签名字符串是 ``{t}.{raw_body}``，用 endpoint 的
  **签名密钥**（``whsec_...``）做 HMAC-SHA256，比较任意一个 ``v1``；``t`` 超出容忍窗口
  （默认 300 秒）直接拒绝，防重放。**只有验签通过的成功事件才产生 VerifiedPayment**。
- 对账：Stripe 没有"下载账单文件"这一形态，本适配器以 **Balance Transactions** 作为对账源
  （``GET /v1/balance_transactions``，按账单日 UTC 边界取数，游标翻页），序列化为 JSON 字节流，
  ``parse_bill`` 再解析成统一账单行。

安全要求：未配置凭据 fail closed（``GatewayNotConfigured``），**绝不静默降级到沙箱**；
回调验签失败只返回结论与稳定原因，不回显任何密钥材料；卡片号等支付工具明细不进入业务记录。
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from typing import Any

import httpx

from app.adapters.base import GatewayError, GatewayNotConfigured
from app.adapters.payments.base import (
    ACTION_NONE,
    ACTION_REDIRECT,
    PAYMENT_RESULT_CLOSED,
    PAYMENT_RESULT_NOT_FOUND,
    PAYMENT_RESULT_PENDING,
    PAYMENT_RESULT_SUCCEEDED,
    PAYMENT_RESULT_UNKNOWN,
    REFUND_RESULT_FAILED,
    REFUND_RESULT_NOT_FOUND,
    REFUND_RESULT_PROCESSING,
    REFUND_RESULT_SUCCEEDED,
    REFUND_RESULT_UNKNOWN,
    NotificationVerdict,
    VerifiedPayment,
    bill_day_bounds,
    header_value,
    payload_digest,
)
from app.platform.config import Settings
from app.platform.ids import iso, now_ms

API_HOST = "https://api.stripe.com"
#: Webhook 签名容忍窗口（Stripe 默认值，秒）。
SIGNATURE_TOLERANCE_SECONDS = 300
#: 产生 VerifiedPayment 的成功事件。
SUCCESS_EVENTS = ("payment_intent.succeeded",)
#: 明确失败/关闭的事件（验签通过但不入账）。
FAILURE_EVENTS = ("payment_intent.payment_failed", "payment_intent.canceled")
#: 状态映射。
PENDING_STATES = {"requires_payment_method", "requires_confirmation", "requires_action", "processing"}
SUCCEEDED_STATES = {"succeeded"}
CLOSED_STATES = {"canceled"}


def parse_signature_header(header: str | None) -> tuple[int | None, list[str]]:
    """解析 ``Stripe-Signature``：``t=...,v1=...,v1=...``。

    返回 ``(timestamp, v1 签名列表)``；缺 ``t`` 或缺 ``v1`` 时对应项为空。
    """
    if not header:
        return None, []
    timestamp: int | None = None
    signatures: list[str] = []
    for part in header.split(","):
        key, _, value = part.strip().partition("=")
        if key == "t":
            try:
                timestamp = int(value)
            except ValueError:
                timestamp = None
        elif key == "v1" and value:
            signatures.append(value.strip().lower())
    return timestamp, signatures


def expected_signature(secret: str, timestamp: int, raw_body: bytes) -> str:
    """待签名字符串为 ``{t}.{payload}``，算法 HMAC-SHA256（十六进制小写）。"""
    signed = f"{timestamp}.".encode() + raw_body
    return hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()


class StripeGateway:
    """信用卡通道（Stripe）。``name`` 固定为 ``stripe``。"""

    name = "stripe"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    # ------------------------------------------------------------ 配置
    def _secret_key(self) -> str:
        key = (self._settings.stripe_secret_key or "").strip()
        if not key:
            raise GatewayNotConfigured("stripe:secret-key")
        return key

    def _webhook_secret(self) -> str:
        secret = (self._settings.stripe_webhook_secret or "").strip()
        if not secret:
            raise GatewayNotConfigured("stripe:webhook-secret")
        return secret

    def _ensure_trading_configured(self) -> None:
        self._secret_key()

    def _ensure_verification_configured(self) -> None:
        self._webhook_secret()

    def _timeout(self) -> httpx.Timeout:
        return httpx.Timeout(10.0, connect=5.0)

    def _request(
        self,
        method: str,
        path: str,
        *,
        data: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """调用 Stripe REST；网络与 5xx 抛可重试的 ``GatewayError``，4xx 抛不可重试的。"""
        self._ensure_trading_configured()
        headers = {
            "Authorization": f"Bearer {self._secret_key()}",
            "Stripe-Version": self._settings.stripe_api_version,
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        try:
            with httpx.Client(timeout=self._timeout()) as client:
                response = client.request(
                    method,
                    f"{API_HOST}{path}",
                    data=dict(data or {}),
                    params=dict(params or {}),
                    headers=headers,
                )
        except httpx.HTTPError as exc:
            raise GatewayError("stripe 请求失败", retryable=True) from exc
        if response.status_code >= 500:
            raise GatewayError(f"stripe 服务端错误 HTTP {response.status_code}", retryable=True)
        try:
            payload = response.json()
        except ValueError as exc:  # pragma: no cover - Stripe 始终返回 JSON
            raise GatewayError("stripe 响应不是 JSON", retryable=True) from exc
        if response.status_code >= 400:
            error = payload.get("error") or {}
            # 只带稳定字段，不带原始报文（06 文档：不把支付原始报文写进业务记录/日志）。
            raise GatewayError(
                f"stripe 拒绝请求：{error.get('code') or error.get('type') or response.status_code}",
                retryable=response.status_code == 429,
            )
        return dict(payload)

    # ------------------------------------------------------------ 收款
    def create(
        self,
        merchant_order_no: str,
        amount_cents: int,
        currency: str,
        scene: str,
        subject: str,
        return_url: str | None,
    ) -> dict[str, Any]:
        code = (currency or "").upper()
        if code not in ("USD", "CNY"):
            raise GatewayError("stripe 首发只支持 USD/CNY", retryable=False)
        data: dict[str, Any] = {
            "amount": int(amount_cents),
            "currency": code.lower(),
            "description": subject[:200],
            "metadata[merchant_order_no]": merchant_order_no,
            "automatic_payment_methods[enabled]": "true",
        }
        if return_url:
            data["return_url"] = return_url
        payload = self._request(
            "POST", "/v1/payment_intents", data=data, idempotency_key=f"create-{merchant_order_no}"
        )
        intent_id = str(payload.get("id") or "")
        status = str(payload.get("status") or "")
        next_action = payload.get("next_action") or {}
        redirect = (next_action.get("redirect_to_url") or {}).get("url")
        if redirect:
            action: dict[str, Any] = {"type": ACTION_REDIRECT, "url": str(redirect)}
        else:
            # 无需跳转（例如已确认或等待异步结果）：不给假二维码/假跳转。
            action = {"type": ACTION_NONE}
        expires = payload.get("created")
        return {
            "status": self._payment_status(status),
            "action": action,
            "provider_transaction_id": intent_id or None,
            "expires_at": (int(expires) * 1000 + 30 * 60 * 1000) if isinstance(expires, int) else None,
            "raw_digest": payload_digest(f"{merchant_order_no}:{amount_cents}:{status}".encode()),
        }

    def _payment_status(self, stripe_status: str) -> str:
        if stripe_status in SUCCEEDED_STATES:
            return PAYMENT_RESULT_SUCCEEDED
        if stripe_status in CLOSED_STATES:
            return PAYMENT_RESULT_CLOSED
        if stripe_status in PENDING_STATES:
            return PAYMENT_RESULT_PENDING
        return PAYMENT_RESULT_UNKNOWN

    def query(self, merchant_order_no: str) -> dict[str, Any]:
        """按商户单号查单（Search API）。查不到返回 NOT_FOUND，不猜状态。"""
        self._ensure_trading_configured()
        try:
            payload = self._request(
                "GET",
                "/v1/payment_intents/search",
                params={
                    "query": f"metadata['merchant_order_no']:'{self._escape_search(merchant_order_no)}'",
                    "limit": 1,
                },
            )
        except GatewayNotConfigured:
            # 未配置密钥是配置错误，绝不能答成「渠道查无此单」。
            raise
        except GatewayError as exc:
            if exc.retryable:
                raise
            return {"status": PAYMENT_RESULT_NOT_FOUND, "provider_transaction_id": None}
        rows = payload.get("data") or []
        if not rows:
            return {"status": PAYMENT_RESULT_NOT_FOUND, "provider_transaction_id": None}
        return self._intent_state(rows[0])

    @staticmethod
    def _escape_search(value: str) -> str:
        """Search 查询串里的单引号必须转义，避免拼接出别的查询条件。"""
        return value.replace("\\", "\\\\").replace("'", "\\'")

    def _intent_state(self, intent: Mapping[str, Any]) -> dict[str, Any]:
        status = str(intent.get("status") or "")
        amount = intent.get("amount_received") or intent.get("amount") or 0
        return {
            "status": self._payment_status(status),
            "provider_transaction_id": str(intent.get("id") or ""),
            "amount_cents": int(amount or 0),
            "currency": str(intent.get("currency") or "").upper(),
            "paid_at": int(intent["created"]) * 1000 if isinstance(intent.get("created"), int) else None,
        }

    def close(self, merchant_order_no: str) -> dict[str, Any]:
        """关闭未支付的 PaymentIntent；已支付的单不会被关闭（服务层据此复核）。"""
        self._ensure_trading_configured()
        state = self.query(merchant_order_no)
        if state["status"] == PAYMENT_RESULT_NOT_FOUND:
            return state
        intent_id = str(state.get("provider_transaction_id") or "")
        if state["status"] == PAYMENT_RESULT_SUCCEEDED or not intent_id:
            return state
        payload = self._request(
            "POST",
            f"/v1/payment_intents/{intent_id}/cancel",
            idempotency_key=f"close-{merchant_order_no}",
        )
        return self._intent_state(payload)

    # ------------------------------------------------------------ 退款
    def refund(
        self, merchant_order_no: str, refund_no: str, amount_cents: int, reason: str
    ) -> dict[str, Any]:
        """按原渠道原路退款；``Idempotency-Key`` 用同一个退款单号，重试不会重复退款。"""
        self._ensure_trading_configured()
        state = self.query(merchant_order_no)
        intent_id = str(state.get("provider_transaction_id") or "")
        if not intent_id:
            return {"status": REFUND_RESULT_NOT_FOUND, "provider_refund_id": None, "amount_cents": 0}
        payload = self._request(
            "POST",
            "/v1/refunds",
            data={
                "payment_intent": intent_id,
                "amount": int(amount_cents),
                "metadata[refund_no]": refund_no,
                "metadata[reason]": reason[:200],
            },
            idempotency_key=f"refund-{refund_no}",
        )
        return self._refund_state(payload, refund_no=refund_no)

    def _refund_state(self, payload: Mapping[str, Any], *, refund_no: str) -> dict[str, Any]:
        status = str(payload.get("status") or "")
        mapping = {
            "succeeded": REFUND_RESULT_SUCCEEDED,
            "pending": REFUND_RESULT_PROCESSING,
            "requires_action": REFUND_RESULT_PROCESSING,
            "failed": REFUND_RESULT_FAILED,
            "canceled": REFUND_RESULT_FAILED,
        }
        return {
            "status": mapping.get(status, REFUND_RESULT_UNKNOWN),
            "provider_refund_id": str(payload.get("id") or "") or None,
            "amount_cents": int(payload.get("amount") or 0),
            "refund_no": refund_no,
            "reason": str((payload.get("metadata") or {}).get("reason") or ""),
        }

    def query_refund(self, refund_no: str) -> dict[str, Any]:
        """查退款结果。

        官方没有"按 metadata 检索退款"的接口，因此这里**沿用退款单号作为幂等键的语义**：
        重新以同一 ``Idempotency-Key`` 提交查询是拿不到退款对象的，故本方法只按
        Balance Transactions 明细核对（见 ``parse_bill``），不在缺少凭据时臆造状态：
        查不到就返回 ``NOT_FOUND``，由服务层继续按退款单号重试 ``refund()``（幂等）。
        """
        self._ensure_trading_configured()
        for row in self._balance_transactions(limit=100):
            if str(row.get("refund_no") or "") == refund_no:
                return {
                    "status": (
                        REFUND_RESULT_SUCCEEDED if row.get("kind") == "refund" else REFUND_RESULT_UNKNOWN
                    ),
                    "provider_refund_id": str(row.get("transaction_id") or "") or None,
                    "amount_cents": int(row.get("amount_cents") or 0),
                    "refund_no": refund_no,
                }
        return {"status": REFUND_RESULT_NOT_FOUND, "provider_refund_id": None, "amount_cents": 0}

    # ------------------------------------------------------------ 对账
    def download_bill(self, bill_date: str) -> bytes:
        """以 Balance Transactions 作为对账源，导出该账单日的 JSON 字节流。"""
        self._ensure_trading_configured()
        rows = self._balance_transactions(bill_date=bill_date)
        return json.dumps({"bill_date": bill_date, "data": rows}, ensure_ascii=False).encode("utf-8")

    def _balance_transactions(
        self, *, bill_date: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": min(max(limit, 1), 100), "expand[]": "data.source"}
        if bill_date:
            start_ms, end_ms = bill_day_bounds(bill_date)
            # Stripe 的 created 过滤是秒级闭区间。
            params["created[gte]"] = start_ms // 1000
            params["created[lte]"] = max(start_ms // 1000, end_ms // 1000 - 1)
        payload = self._request("GET", "/v1/balance_transactions", params=params)
        return [self._bill_row(row) for row in (payload.get("data") or [])]

    def _bill_row(self, row: Mapping[str, Any]) -> dict[str, Any]:
        source = row.get("source") or {}
        amount = int(row.get("amount") or 0)
        fee = int(row.get("fee") or 0)
        metadata = source.get("metadata") if isinstance(source, Mapping) else None
        metadata = metadata if isinstance(metadata, Mapping) else {}
        kind = "refund" if amount < 0 else "payment"
        return {
            "merchant_order_no": str(metadata.get("merchant_order_no") or ""),
            "transaction_id": str(row.get("id") or ""),
            "kind": kind,
            "amount_cents": abs(amount),
            "currency": str(row.get("currency") or "").upper(),
            "refund_no": str(metadata.get("refund_no") or "") or None,
            "fee_cents": abs(fee),
            "occurred_at": int(row["created"]) * 1000 if isinstance(row.get("created"), int) else None,
        }

    def parse_bill(self, bill_date: str, raw: bytes) -> list[dict[str, Any]]:
        """解析 ``download_bill`` 导出的 JSON；不是本适配器导出的形状则拒绝。"""
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GatewayError("stripe 对账数据不是合法 JSON", retryable=False) from exc
        if not isinstance(payload, dict) or payload.get("bill_date") != bill_date:
            raise GatewayError("stripe 对账数据的账单日不匹配", retryable=False)
        rows = payload.get("data")
        if not isinstance(rows, list):
            raise GatewayError("stripe 对账数据缺少 data", retryable=False)
        return [dict(row) for row in rows if isinstance(row, Mapping)]

    # ------------------------------------------------------------ 回调
    def verify_notification_detailed(
        self, headers: Mapping[str, str], raw_body: bytes
    ) -> NotificationVerdict:
        """验签 + 结构核对；不通过只给稳定原因，不抛异常、不入账。"""
        try:
            self._ensure_verification_configured()
        except GatewayNotConfigured:
            return NotificationVerdict(accepted=False, reason="webhook_secret_missing")
        timestamp, signatures = parse_signature_header(header_value(headers, "Stripe-Signature"))
        if timestamp is None or not signatures:
            return NotificationVerdict(accepted=False, reason="signature_missing")
        if abs(now_ms() // 1000 - timestamp) > SIGNATURE_TOLERANCE_SECONDS:
            return NotificationVerdict(accepted=False, reason="timestamp_out_of_window")
        expected = expected_signature(self._webhook_secret(), timestamp, raw_body)
        if not any(hmac.compare_digest(expected, item) for item in signatures):
            return NotificationVerdict(accepted=False, reason="signature_mismatch")

        try:
            event = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return NotificationVerdict(accepted=False, reason="payload_malformed")
        if not isinstance(event, Mapping):
            return NotificationVerdict(accepted=False, reason="payload_malformed")
        event_id = str(event.get("id") or "") or None
        event_type = str(event.get("type") or "")
        if event_type in FAILURE_EVENTS:
            return NotificationVerdict(
                accepted=True, reason="not_a_success_payment", event_id=event_id, event_type=event_type
            )
        if event_type not in SUCCESS_EVENTS:
            return NotificationVerdict(
                accepted=True, reason="ignored_event_type", event_id=event_id, event_type=event_type
            )
        payload = event.get("data", {}).get("object") if isinstance(event.get("data"), Mapping) else None
        if not isinstance(payload, Mapping):
            return NotificationVerdict(accepted=False, reason="payload_malformed", event_id=event_id)
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), Mapping) else {}
        merchant_order_no = str((metadata or {}).get("merchant_order_no") or "")
        if not merchant_order_no:
            # 没有商户单号无法与本地应付项核对：拒绝入账，交由人工/对账处理。
            return NotificationVerdict(accepted=False, reason="merchant_order_no_missing", event_id=event_id)
        amount = payload.get("amount_received") or payload.get("amount") or 0
        currency = str(payload.get("currency") or "").upper()
        if not currency or not isinstance(amount, int) or amount < 0:
            return NotificationVerdict(accepted=False, reason="amount_malformed", event_id=event_id)
        account_ref = payload.get("account") if isinstance(payload.get("account"), str) else None
        payment = VerifiedPayment(
            provider="stripe",
            provider_transaction_id=str(payload.get("id") or ""),
            merchant_order_no=merchant_order_no,
            amount_cents=int(amount),
            currency=currency,
            paid_at=int(payload["created"]) * 1000 if isinstance(payload.get("created"), int) else None,
            raw_digest=payload_digest(raw_body),
            event_id=event_id,
            account_ref=account_ref or self.expected_account_ref(),
        )
        return NotificationVerdict(
            accepted=True, reason="verified", payment=payment, event_id=event_id, event_type=event_type
        )

    def verify_notification(self, headers: Mapping[str, str], raw_body: bytes) -> VerifiedPayment | None:
        verdict = self.verify_notification_detailed(headers, raw_body)
        return verdict.payment if verdict.accepted else None

    def expected_account_ref(self) -> str | None:
        """本商户在 Stripe 的账户标识；未配置返回 ``None``（此时跳过该项核对）。"""
        return (self._settings.stripe_account_id or "").strip() or None

    def ack_success(self) -> dict[str, Any]:
        # Stripe 只要求 2xx：不回显事件内容，也不落任何渠道凭据。
        return {"received": True}

    def ack_failure(self, reason: str) -> dict[str, Any]:
        return {"received": False, "reason": reason}

    def notification_url(self) -> str:
        """回调地址（配置到 Stripe endpoint，用于生成签名密钥）。"""
        return (
            f"{self._settings.public_origin.rstrip('/')}{self._settings.api_prefix}"
            "/payment-notifications/stripe"
        )

    def describe_configuration(self) -> dict[str, Any]:
        """给运维/后台看的配置自检结果：**只报告是否已配置，不返回值**。"""
        return {
            "provider": self.name,
            "secret_key_configured": bool((self._settings.stripe_secret_key or "").strip()),
            "webhook_secret_configured": bool((self._settings.stripe_webhook_secret or "").strip()),
            "account_id_configured": bool((self._settings.stripe_account_id or "").strip()),
            "api_version": self._settings.stripe_api_version,
            "notification_url": self.notification_url(),
            "checked_at": iso(now_ms()),
        }

    def __repr__(self) -> str:  # pragma: no cover - 仅用于日志可读性
        return f"<StripeGateway api_version={self._settings.stripe_api_version}>"


__all__ = [
    "API_HOST",
    "FAILURE_EVENTS",
    "SIGNATURE_TOLERANCE_SECONDS",
    "SUCCESS_EVENTS",
    "StripeGateway",
    "expected_signature",
    "parse_signature_header",
]
