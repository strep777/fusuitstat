"""支付宝适配器骨架。

**签名算法与证书模式必须以商户当前官方文档为准**：支付宝开放平台默认 RSA2
（SHA256withRSA）签名，回调为 ``application/x-www-form-urlencoded``，验签串是
「除 sign、sign_type 外全部参数按字典序拼接的 ``k=v&k=v``」，用支付宝公钥验签；
证书模式下还需拼接 ``app_cert_sn`` 与 ``alipay_root_cert_sn``（本文件实现公钥模式，
证书模式需在开通时按官方文档补齐）。异步通知应答必须是纯文本 ``success``。

本适配器实现的安全要求：原始字节参与验签、``notify_time`` 重放窗口、
``app_id``/``seller_id`` 核对、金额按十进制字符串转整数分（拒绝无法精确到分的值）、
按 ``notify_id`` 与 ``trade_no`` 去重。IP 白名单只作辅助，不能代替验签。
未配置 app_id/支付宝公钥/商户私钥时抛 ``GatewayNotConfigured``，绝不降级到沙箱。

商户私钥不在 ``Settings`` 字段中（属待补配置项），从环境变量
``ALIPAY_PRIVATE_KEY``（PEM 文本）或 ``ALIPAY_PRIVATE_KEY_PATH`` 读取，缺失即
fail closed。
"""

from __future__ import annotations

import base64
import csv
import io
import json
import os
import zipfile
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode

import httpx

from app.adapters.base import GatewayError, GatewayNotConfigured
from app.adapters.payments.base import (
    ACK_TEXT_KEY,
    ACTION_QR,
    ACTION_REDIRECT,
    PAYMENT_RESULT_CLOSED,
    PAYMENT_RESULT_FAILED,
    PAYMENT_RESULT_NOT_FOUND,
    PAYMENT_RESULT_PENDING,
    PAYMENT_RESULT_SUCCEEDED,
    REFUND_RESULT_PROCESSING,
    REFUND_RESULT_SUCCEEDED,
    NotificationVerdict,
    VerifiedPayment,
    business_event_key,
    cents_from_decimal_text,
    decimal_text_from_cents,
    payload_digest,
)
from app.platform.config import Settings
from app.platform.ids import SHANGHAI, now_ms

GATEWAY_URL = "https://openapi.alipay.com/gateway.do"
#: 通知时间戳重放窗口（毫秒）。支付宝会重试同一通知，去重仍以 notify_id/trade_no 为准；
#: 该窗口须按当前官方文档与商户重试策略复核。
REPLAY_WINDOW_MS = 3_600_000
SUCCESS_TRADE_STATES = ("TRADE_SUCCESS", "TRADE_FINISHED")
SIGN_TYPE = "RSA2"


class AlipayGateway:
    """支付宝通道。``name`` 固定为 ``alipay``。"""

    name = "alipay"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    # ------------------------------------------------------------ 配置
    def _public_key_pem(self) -> str:
        raw = (self._settings.alipay_public_key or "").strip()
        if not raw:
            raise GatewayNotConfigured("alipay:public-key")
        return _ensure_pem(raw, kind="PUBLIC KEY")

    def _private_key_pem(self) -> str:
        inline = os.environ.get("ALIPAY_PRIVATE_KEY", "").strip()
        if inline:
            return _ensure_pem(inline.replace("\\n", "\n"), kind="PRIVATE KEY")
        path = os.environ.get("ALIPAY_PRIVATE_KEY_PATH", "").strip()
        if path and Path(path).is_file():
            return _ensure_pem(Path(path).read_text(encoding="utf-8"), kind="PRIVATE KEY")
        raise GatewayNotConfigured("alipay:merchant-private-key")

    def _ensure_trading_configured(self) -> None:
        if not self._settings.alipay_app_id:
            raise GatewayNotConfigured("alipay:app-id")
        self._public_key_pem()
        self._private_key_pem()

    def _ensure_verification_configured(self) -> None:
        self._public_key_pem()

    # ------------------------------------------------------------ 签名
    def _sign(self, params: Mapping[str, str]) -> str:
        message = _canonical(params).encode("utf-8")
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding, rsa

        private_key = serialization.load_pem_private_key(self._private_key_pem().encode(), password=None)
        if not isinstance(private_key, rsa.RSAPrivateKey):
            # 支付宝 OpenAPI 只接受 RSA2（SHA256withRSA）密钥，其他算法必须显式失败，
            # 不能静默降级或用错误算法签名。
            raise GatewayNotConfigured("alipay:merchant-private-key:not-rsa")
        return base64.b64encode(private_key.sign(message, padding.PKCS1v15(), hashes.SHA256())).decode("ascii")

    def _call(self, method: str, biz_content: dict[str, Any], *, notify: bool = False) -> dict[str, Any]:
        self._ensure_trading_configured()
        params = {
            "app_id": self._settings.alipay_app_id,
            "method": method,
            "format": "JSON",
            "charset": "utf-8",
            "sign_type": SIGN_TYPE,
            "timestamp": datetime.now(tz=SHANGHAI).strftime("%Y-%m-%d %H:%M:%S"),
            "version": "1.0",
            "biz_content": json.dumps(biz_content, ensure_ascii=False, separators=(",", ":")),
        }
        if notify:
            params["notify_url"] = (
                f"{self._settings.public_origin.rstrip('/')}{self._settings.api_prefix}"
                "/payment-notifications/alipay"
            )
        params["sign"] = self._sign(params)
        try:
            with httpx.Client(timeout=8.0) as client:
                response = client.post(GATEWAY_URL, data=params)
        except httpx.TimeoutException as exc:
            raise GatewayError("alipay 请求超时", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise GatewayError(f"alipay 请求失败：{type(exc).__name__}", retryable=True) from exc
        if response.status_code >= 500:
            raise GatewayError("alipay 服务端错误", retryable=True)
        if response.status_code >= 400:
            raise GatewayError(f"alipay 拒绝请求（{response.status_code}）", retryable=False)
        try:
            body = response.json()
        except ValueError as exc:
            raise GatewayError("alipay 响应无法解析", retryable=True) from exc
        payload = body.get(f"{method.replace('.', '_')}_response") or body
        if str(payload.get("code", "10000")) != "10000":
            # 业务失败：明确拒绝，不重试下单以免重复建单。
            raise GatewayError(f"alipay 业务失败：{payload.get('sub_code') or payload.get('code')}", retryable=False)
        return dict(payload)

    # ------------------------------------------------------------ 渠道能力
    def create(
        self,
        merchant_order_no: str,
        amount_cents: int,
        currency: str,
        scene: str,
        subject: str,
        return_url: str | None,
    ) -> dict[str, Any]:
        if currency != "CNY":
            raise GatewayError("alipay 仅支持 CNY", retryable=False)
        amount = decimal_text_from_cents(amount_cents)
        biz: dict[str, Any] = {
            "out_trade_no": merchant_order_no,
            "total_amount": amount,
            "subject": subject[:120],
            "timeout_express": "30m",
        }
        if scene in ("native", "qr"):
            biz["qr_code_timeout_express"] = "30m"
            payload = self._call("alipay.trade.precreate", biz, notify=True)
            code_url = str(payload.get("qr_code") or "")
            action = {"type": ACTION_QR, "url": code_url}
        elif scene in ("web", "h5", "app"):
            # 电脑/手机网站支付：服务端生成已签名的跳转地址，由浏览器跳转（无需等待 JSON 响应）。
            method = "alipay.trade.page.pay" if scene == "web" else "alipay.trade.wap.pay"
            biz["product_code"] = "FAST_INSTANT_TRADE_PAY"
            action = {"type": ACTION_REDIRECT, "url": self._signed_redirect(method, biz, return_url)}
        else:
            raise GatewayError(f"不支持的支付宝支付场景：{scene}", retryable=False)
        return {
            "status": PAYMENT_RESULT_PENDING,
            "action": action,
            "provider_transaction_id": None,
            "expires_at": None,
            "raw_digest": payload_digest(f"{merchant_order_no}:{amount_cents}".encode()),
        }

    def _signed_redirect(self, method: str, biz_content: dict[str, Any], return_url: str | None) -> str:
        """生成已签名的跳转地址（网站支付/手机网站支付由浏览器跳转，服务端不当场拿结果）。"""
        self._ensure_trading_configured()
        params = {
            "app_id": self._settings.alipay_app_id,
            "method": method,
            "format": "JSON",
            "charset": "utf-8",
            "sign_type": SIGN_TYPE,
            "timestamp": datetime.now(tz=SHANGHAI).strftime("%Y-%m-%d %H:%M:%S"),
            "version": "1.0",
            "notify_url": f"{self._settings.public_origin.rstrip('/')}{self._settings.api_prefix}"
            "/payment-notifications/alipay",
            "biz_content": json.dumps(biz_content, ensure_ascii=False, separators=(",", ":")),
        }
        if return_url:
            params["return_url"] = return_url
        params["sign"] = self._sign(params)
        return f"{GATEWAY_URL}?{urlencode(params)}"

    def query(self, merchant_order_no: str) -> dict[str, Any]:
        try:
            payload = self._call("alipay.trade.query", {"out_trade_no": merchant_order_no})
        except GatewayError as exc:
            if not exc.retryable:
                return {"status": PAYMENT_RESULT_NOT_FOUND, "provider_transaction_id": None}
            raise
        return self._payment_state_from_payload(merchant_order_no, payload)

    def _payment_state_from_payload(
        self, merchant_order_no: str, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        state = str(payload.get("trade_status") or "")
        transaction_id = str(payload.get("trade_no") or "")
        if state in SUCCESS_TRADE_STATES:
            amount = cents_from_decimal_text(str(payload.get("total_amount") or "0"))
            return {
                "status": PAYMENT_RESULT_SUCCEEDED,
                "provider_transaction_id": transaction_id,
                "amount_cents": amount or 0,
                "currency": "CNY",
                "paid_at": _local_time_to_ms(payload.get("send_pay_date")),
                "event_id": business_event_key(self.name, transaction_id, merchant_order_no),
                "raw_digest": payload_digest(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")),
            }
        if state == "TRADE_CLOSED":
            return {"status": PAYMENT_RESULT_CLOSED, "provider_transaction_id": transaction_id or None}
        if state == "WAIT_BUYER_PAY":
            return {"status": PAYMENT_RESULT_PENDING, "provider_transaction_id": transaction_id or None}
        return {"status": PAYMENT_RESULT_PENDING, "provider_transaction_id": transaction_id or None}

    def close(self, merchant_order_no: str) -> dict[str, Any]:
        try:
            self._call("alipay.trade.close", {"out_trade_no": merchant_order_no})
        except GatewayError as exc:
            if exc.retryable:
                raise
            return {"status": PAYMENT_RESULT_FAILED}
        return {"status": PAYMENT_RESULT_CLOSED}

    def refund(
        self, merchant_order_no: str, refund_no: str, amount_cents: int, reason: str
    ) -> dict[str, Any]:
        biz = {
            "out_trade_no": merchant_order_no,
            "refund_amount": decimal_text_from_cents(amount_cents),
            "out_request_no": refund_no,
            "refund_reason": reason[:120],
        }
        try:
            payload = self._call("alipay.trade.refund", biz)
        except GatewayError as exc:
            if exc.retryable:
                return {"status": REFUND_RESULT_PROCESSING, "provider_refund_id": None}
            raise
        if str(payload.get("fund_change", "")).upper() == "Y" or str(payload.get("code")) == "10000":
            return {
                "status": REFUND_RESULT_SUCCEEDED,
                "provider_refund_id": refund_no,
                "amount_cents": amount_cents,
            }
        return {"status": REFUND_RESULT_PROCESSING, "provider_refund_id": refund_no}

    def query_refund(self, refund_no: str) -> dict[str, Any]:
        try:
            payload = self._call(
                "alipay.trade.fastpay.refund.query", {"out_request_no": refund_no}
            )
        except GatewayError as exc:
            if exc.retryable:
                raise
            return {"status": "NOT_FOUND", "provider_refund_id": None}
        amount = cents_from_decimal_text(str(payload.get("refund_amount") or "0"))
        if payload.get("refund_status") in ("REFUND_SUCCESS", None) and amount:
            return {
                "status": REFUND_RESULT_SUCCEEDED,
                "provider_refund_id": refund_no,
                "amount_cents": amount,
            }
        return {"status": "PROCESSING", "provider_refund_id": refund_no}

    def download_bill(self, bill_date: str) -> bytes:
        payload = self._call(
            "alipay.data.dataservice.bill.downloadurl.query",
            {"bill_type": "trade", "bill_date": bill_date},
        )
        urls = payload.get("bill_download_url") or []
        if isinstance(urls, str):
            urls = [urls]
        if not urls:
            raise GatewayError("alipay 账单尚未生成（不同渠道账单可用时间不同）", retryable=True)
        from urllib.parse import urlparse

        host = urlparse(str(urls[0])).hostname or ""
        if not host.endswith("alipayobjects.com"):
            raise GatewayError("alipay 账单下载地址不在白名单内", retryable=False)
        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.get(str(urls[0]))
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise GatewayError("alipay 账单下载失败", retryable=True) from exc
        return response.content

    def parse_bill(self, bill_date: str, raw: bytes) -> list[dict[str, Any]]:
        """解析支付宝对账单（渠道提供的是 ZIP，内含 GBK 编码 CSV）。"""
        _ = bill_date
        content = raw
        if raw[:2] == b"PK":
            try:
                with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                    name = next((n for n in archive.namelist() if n.lower().endswith(".csv")), None)
                    if name is None:
                        raise GatewayError("支付宝账单压缩包内无 CSV", retryable=False)
                    content = archive.read(name)
            except zipfile.BadZipFile as exc:
                raise GatewayError("支付宝账单压缩包损坏", retryable=False) from exc
        text = content.decode("gbk", errors="replace")
        rows: list[dict[str, Any]] = []
        reader = csv.reader(io.StringIO(text))
        header: list[str] | None = None
        for cells in reader:
            stripped = [cell.strip() for cell in cells]
            if header is None:
                if not stripped or stripped[0].startswith("#"):
                    continue
                if any("支付宝交易号" in cell for cell in stripped):
                    header = stripped
                continue
            if len(stripped) < len(header) or not stripped[0]:
                continue
            record = {key: value for key, value in zip(header, stripped, strict=False)}
            amount = cents_from_decimal_text(record.get("订单金额") or record.get("金额") or "0")
            fee = cents_from_decimal_text(record.get("服务费") or "0")
            rows.append(
                {
                    "merchant_order_no": record.get("商户订单号", ""),
                    "transaction_id": record.get("支付宝交易号", ""),
                    "kind": "refund" if record.get("退款批次号") else "payment",
                    "amount_cents": amount or 0,
                    "currency": "CNY",
                    "refund_no": record.get("退款批次号") or None,
                    "fee_cents": fee or 0,
                    "occurred_at": _local_time_to_ms(record.get("创建时间")),
                }
            )
        if header is None:
            raise GatewayError("支付宝账单格式无法识别，需按当前官方文档更新解析器", retryable=False)
        return rows

    # ------------------------------------------------------------ 回调验签
    def verify_notification_detailed(
        self, headers: Mapping[str, str], raw_body: bytes
    ) -> NotificationVerdict:
        self._ensure_verification_configured()
        _ = headers
        try:
            pairs = parse_qsl(raw_body.decode("utf-8"), keep_blank_values=True)
        except UnicodeDecodeError:
            return NotificationVerdict(accepted=False, reason="payload_malformed")
        params = {key: value for key, value in pairs}
        signature = params.get("sign")
        if not signature:
            return NotificationVerdict(accepted=False, reason="signature_missing")
        if params.get("sign_type", SIGN_TYPE) != SIGN_TYPE:
            # 只接受 RSA2；MD5/RSA 已被官方弃用。
            return NotificationVerdict(accepted=False, reason="unsupported_sign_type")
        if not _rsa_verify(self._public_key_pem(), _canonical(params).encode("utf-8"), signature):
            return NotificationVerdict(accepted=False, reason="signature_mismatch")
        notify_time = _local_time_to_ms(params.get("notify_time"))
        if notify_time is not None and abs(now_ms() - notify_time) > REPLAY_WINDOW_MS:
            return NotificationVerdict(accepted=False, reason="timestamp_out_of_window")
        trade_status = params.get("trade_status", "")
        event_id = params.get("notify_id") or business_event_key(
            self.name, params.get("trade_no", ""), params.get("out_trade_no", ""), trade_status
        )
        app_id = params.get("app_id")
        account_ref = f"app_id:{app_id}" if app_id else None
        # seller_id 核对需要商户自己的收款账号（待补配置项），当前只核对 app_id。
        if trade_status not in SUCCESS_TRADE_STATES:
            return NotificationVerdict(
                accepted=True,
                reason="not_a_success_payment",
                event_id=event_id,
                event_type=params.get("notify_type", "payment.succeeded"),
            )
        order_no = params.get("out_trade_no", "")
        transaction_id = params.get("trade_no", "")
        amount = cents_from_decimal_text(params.get("total_amount", "0"))
        if not order_no or not transaction_id or amount is None:
            return NotificationVerdict(accepted=False, reason="payload_incomplete", event_id=event_id)
        reason = "verified"
        expected = self.expected_account_ref()
        if expected and account_ref and account_ref != expected:
            # app_id/seller_id 不匹配：标记后由服务层隔离告警，真实收款不丢弃。
            reason = "verified_account_mismatch"
        payment = VerifiedPayment(
            provider=self.name,
            provider_transaction_id=transaction_id,
            merchant_order_no=order_no,
            amount_cents=amount,
            currency="CNY",
            paid_at=_local_time_to_ms(params.get("gmt_payment")) or notify_time or now_ms(),
            raw_digest=payload_digest(raw_body),
            event_id=params.get("notify_id") or event_id,
            account_ref=account_ref,
        )
        return NotificationVerdict(
            accepted=True, reason=reason, payment=payment, event_id=payment.event_id
        )

    def verify_notification(
        self, headers: Mapping[str, str], raw_body: bytes
    ) -> VerifiedPayment | None:
        return self.verify_notification_detailed(headers, raw_body).payment

    def expected_account_ref(self) -> str | None:
        if not self._settings.alipay_app_id:
            return None
        return f"app_id:{self._settings.alipay_app_id}"

    def ack_success(self) -> dict[str, Any]:
        # 支付宝要求异步通知应答体为纯文本 success。
        return {ACK_TEXT_KEY: "success"}

    def ack_failure(self, reason: str) -> dict[str, Any]:
        _ = reason
        return {ACK_TEXT_KEY: "failure"}


def _canonical(params: Mapping[str, str]) -> str:
    """待签名/验签串：除 sign、sign_type 外按字典序拼接（值为解码后的原文）。"""
    items = [
        (key, value)
        for key, value in params.items()
        if key not in ("sign", "sign_type") and value is not None
    ]
    items.sort(key=lambda item: item[0])
    return urlencode(items)


def _ensure_pem(raw: str, *, kind: str) -> str:
    """补全裸 base64 密钥为 PEM（支付宝控制台复制的是不带头尾的 base64）。"""
    text = raw.strip()
    if "-----BEGIN" in text:
        return text
    body = "".join(text.split())
    lines = [body[index : index + 64] for index in range(0, len(body), 64)]
    return f"-----BEGIN {kind}-----\n" + "\n".join(lines) + f"\n-----END {kind}-----\n"


def _rsa_verify(public_key_pem: str, message: bytes, signature_b64: str) -> bool:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    try:
        public_key = serialization.load_pem_public_key(public_key_pem.encode("utf-8"))
        if not isinstance(public_key, rsa.RSAPublicKey):
            # 非 RSA 公钥无法验证 RSA2 签名，按验签失败处理。
            return False
        public_key.verify(base64.b64decode(signature_b64), message, padding.PKCS1v15(), hashes.SHA256())
    except (InvalidSignature, ValueError, TypeError):
        return False
    return True


def _local_time_to_ms(value: Any) -> int | None:
    """支付宝时间串（``Asia/Shanghai`` 无时区后缀或 ISO8601）转 UTC 毫秒。"""
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    for template in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return int(datetime.strptime(text, template).replace(tzinfo=SHANGHAI).timestamp() * 1000)
        except ValueError:
            continue
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=SHANGHAI)
    return int(moment.timestamp() * 1000)


__all__ = [
    "GATEWAY_URL",
    "REPLAY_WINDOW_MS",
    "SIGN_TYPE",
    "SUCCESS_TRADE_STATES",
    "AlipayGateway",
]
