"""微信支付（APIv3）适配器骨架。

**签名算法与证书模式必须以商户当前官方文档为准**：微信 APIv3 使用
``WECHATPAY2-SHA256-RSA2048`` 商户私钥签名、平台证书/公钥验签，回调报文
``resource`` 用 APIv3 密钥做 AES-256-GCM 解密；平台证书会轮换，必须按
``Wechatpay-Serial`` 选择对应证书并支持下载新证书。本文件的参数名、应答字段与
账单列名需在商户开通时逐项复核（官方参考：wechatpay-apiv3 SDK 的通知验签与解密）。

本适配器实现的安全要求：

- **保留验签所需原始字节**：验签基于原始 ``bytes``，摘要写回 ``raw_digest``；
- **证书序列与轮换**：按 ``Wechatpay-Serial`` 从证书库取公钥，未知序列一律拒绝；
- **时间戳/重放**：``Wechatpay-Timestamp`` 超出重放窗口拒绝；
- **解密后核对**：解密失败拒绝；解密成功后核对商户号/应用/订单号/币种/金额是否
  齐备，与本地配置不一致时标出 ``verified_account_mismatch`` 交由服务层隔离告警；
- **按渠道交易号与事件号去重**：事件号来自通知体 ``id``，无事件号时用规范化业务键，
  数据库 ``UNIQUE(provider,event_id)`` 与 ``UNIQUE(provider,provider_transaction_id)``
  兜底。

IP 白名单只作辅助，不能代替验签。未配置 APIv3 密钥或平台证书时抛
``GatewayNotConfigured``，绝不静默降级到沙箱。

注意：商户私钥当前不在 ``Settings`` 字段中（属待补配置项），因此交易类方法
（create/query/close/refund/download_bill）从环境变量
``WECHAT_PAY_PRIVATE_KEY``（PEM 文本）或 ``WECHAT_PAY_PRIVATE_KEY_PATH`` 读取；
缺失即 fail closed。回调验签不需要商户私钥。
"""

from __future__ import annotations

import base64
import json
import os
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
    header_value,
    payload_digest,
)
from app.platform.config import Settings
from app.platform.ids import now_ms

API_HOST = "https://api.mch.weixin.qq.com"
#: 回调时间戳重放窗口（毫秒）。
REPLAY_WINDOW_MS = 300_000
#: 微信账单下载地址必须落在官方域名（防止被重定向到任意主机）。
ALLOWED_BILL_HOSTS = ("api.mch.weixin.qq.com", "api.mch.weixin.qq.com.cdn", "payapp.weixin.qq.com")

TRADE_STATE_SUCCESS = "SUCCESS"


class WechatPayGateway:
    """微信支付通道。``name`` 固定为 ``wechat``。"""

    name = "wechat"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    # ------------------------------------------------------------ 配置
    @property
    def certificate_dir(self) -> Path:
        """平台证书/公钥库（轮换时写入新序列，旧序列保留以便过渡期验签）。"""
        return self._settings.storage_root / "payment_certs"

    def _api_v3_key(self) -> bytes:
        key = (self._settings.wechat_api_v3_key or "").strip()
        if len(key.encode("utf-8")) != 32:
            raise GatewayNotConfigured("wechat:api-v3-key")
        return key.encode("utf-8")

    def _merchant_private_key_pem(self) -> str:
        inline = os.environ.get("WECHAT_PAY_PRIVATE_KEY", "").strip()
        if inline:
            return inline.replace("\\n", "\n")
        path = os.environ.get("WECHAT_PAY_PRIVATE_KEY_PATH", "").strip()
        if path and Path(path).is_file():
            return Path(path).read_text(encoding="utf-8")
        return ""

    def _ensure_trading_configured(self) -> str:
        """交易类调用（create/query/close/refund/bill）需要的完整凭据。"""
        if not self._settings.wechat_merchant_id:
            raise GatewayNotConfigured("wechat:merchant-id")
        pem = self._merchant_private_key_pem()
        if not pem:
            raise GatewayNotConfigured("wechat:merchant-private-key")
        self._api_v3_key()
        return pem

    def _ensure_verification_configured(self) -> None:
        """回调验签只需要 APIv3 密钥与平台证书。"""
        self._api_v3_key()

    # ------------------------------------------------------------ 平台证书
    def store_platform_certificate(self, serial: str, pem: str) -> Path:
        """写入/更新平台证书（``refresh_platform_certificates`` 与运维导入共用）。"""
        target = self.certificate_dir / f"wechat_{serial}.pem"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(pem, encoding="utf-8")
        return target

    def has_platform_certificates(self) -> bool:
        return self.certificate_dir.is_dir() and any(self.certificate_dir.glob("wechat_*.pem"))

    def _platform_public_key(self, serial: str | None) -> Any:
        """按 ``Wechatpay-Serial`` 取平台公钥；证书可能以 X.509 或公钥 PEM 保存。"""
        if not serial:
            return None
        target = self.certificate_dir / f"wechat_{serial}.pem"
        if not target.is_file():
            return None
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization

        pem = target.read_bytes()
        try:
            return x509.load_pem_x509_certificate(pem).public_key()
        except ValueError:
            return serialization.load_pem_public_key(pem)

    def refresh_platform_certificates(self) -> list[str]:
        """下载并缓存平台证书（含轮换）。需要商户私钥签名请求。"""
        pem = self._ensure_trading_configured()
        payload = self._request("GET", "/v3/certificates", pem=pem)
        stored: list[str] = []
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        key = self._api_v3_key()
        for item in payload.get("data", []):
            serial = str(item.get("serial_no", ""))
            encrypted = item.get("encrypt_certificate") or {}
            if not serial or not encrypted.get("ciphertext"):
                continue
            nonce = str(encrypted.get("nonce", "")).encode("utf-8")
            associated = str(encrypted.get("associated_data", "")).encode("utf-8")
            aead = AESGCM(key)
            plaintext = aead.decrypt(nonce, base64.b64decode(encrypted["ciphertext"]), associated)
            self.store_platform_certificate(serial, plaintext.decode("utf-8"))
            stored.append(serial)
        return stored

    # ------------------------------------------------------------ 请求签名
    def _authorization(self, method: str, url_path: str, body: str, pem: str) -> str:
        timestamp = str(int(now_ms() / 1000))
        nonce = base64.b16encode(os.urandom(16)).decode("ascii").lower()
        message = f"{method}\n{url_path}\n{timestamp}\n{nonce}\n{body}\n".encode()
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding, rsa

        private_key = serialization.load_pem_private_key(pem.encode("utf-8"), password=None)
        if not isinstance(private_key, rsa.RSAPrivateKey):
            # 微信支付 APIv3 只接受 RSA2048 商户私钥（WECHATPAY2-SHA256-RSA2048）。
            raise GatewayNotConfigured("wechat:merchant-private-key:not-rsa")
        signature = base64.b64encode(
            private_key.sign(message, padding.PKCS1v15(), hashes.SHA256())
        ).decode("ascii")
        mchid = self._settings.wechat_merchant_id
        return (
            "WECHATPAY2-SHA256-RSA2048 "
            f'mchid="{mchid}",nonce_str="{nonce}",signature="{signature}",timestamp="{timestamp}",'
            f'serial_no="{self._settings.wechat_platform_cert_serial}"'
        )

    def _request(
        self, method: str, url_path: str, *, body: dict[str, Any] | None = None, pem: str, timeout: float = 8.0
    ) -> dict[str, Any]:
        raw_body = json.dumps(body, ensure_ascii=False, separators=(",", ":")) if body is not None else ""
        headers = {
            "Authorization": self._authorization(method, url_path, raw_body, pem),
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "starium-server/1.0",
        }
        url = f"{API_HOST}{url_path}"
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.request(method, url, content=raw_body.encode("utf-8"), headers=headers)
        except httpx.TimeoutException as exc:
            # 超时不代表渠道没建单：服务层必须记 UNKNOWN 并用同一商户单号查单。
            raise GatewayError("wechat 请求超时", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise GatewayError(f"wechat 请求失败：{type(exc).__name__}", retryable=True) from exc
        if response.status_code >= 500:
            raise GatewayError("wechat 服务端错误", retryable=True)
        if response.status_code >= 400:
            # 渠道明确拒绝：不重试，避免重复下单。
            raise GatewayError(f"wechat 拒绝请求（{response.status_code}）", retryable=False)
        if not response.content:
            return {}
        try:
            return dict(response.json())
        except ValueError as exc:
            raise GatewayError("wechat 响应无法解析", retryable=True) from exc

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
        pem = self._ensure_trading_configured()
        if currency != "CNY":
            raise GatewayError("wechat 仅支持 CNY", retryable=False)
        payload: dict[str, Any] = {
            "appid": self._settings.wechat_app_id,
            "mchid": self._settings.wechat_merchant_id,
            "description": subject[:120],
            "out_trade_no": merchant_order_no,
            "notify_url": f"{self._settings.public_origin.rstrip('/')}{self._settings.api_prefix}"
            "/payment-notifications/wechat",
            "amount": {"total": int(amount_cents), "currency": "CNY"},
        }
        if scene == "native":
            url_path = "/v3/pay/transactions/native"
        elif scene in ("h5", "web"):
            url_path = "/v3/pay/transactions/h5"
            payload["scene_info"] = {
                "payer_client_ip": "0.0.0.0",
                "h5_info": {"type": "Wap"},
            }
        elif scene == "jsapi":
            # JSAPI 需要真实 openid 与授权流程（08 文档：不能把手机验证码登录等同微信身份）。
            raise GatewayError("微信 JSAPI 需要 openid 授权流程，尚未开通", retryable=False)
        else:
            raise GatewayError(f"不支持的微信支付场景：{scene}", retryable=False)
        result = self._request("POST", url_path, body=payload, pem=pem)
        if result.get("code_url"):
            action = {"type": ACTION_QR, "url": str(result["code_url"])}
        elif result.get("h5_url"):
            action = {"type": ACTION_REDIRECT, "url": str(result["h5_url"])}
        else:
            action = {"type": "none", "url": ""}
        _ = return_url
        return {
            "status": PAYMENT_RESULT_PENDING,
            "action": action,
            "provider_transaction_id": None,
            "expires_at": None,
            "raw_digest": payload_digest(f"{merchant_order_no}:{amount_cents}".encode()),
        }

    def query(self, merchant_order_no: str) -> dict[str, Any]:
        pem = self._ensure_trading_configured()
        mchid = self._settings.wechat_merchant_id
        try:
            payload = self._request(
                "GET", f"/v3/pay/transactions/out-trade-no/{merchant_order_no}?mchid={mchid}", pem=pem
            )
        except GatewayError as exc:
            if not exc.retryable:
                return {"status": PAYMENT_RESULT_NOT_FOUND, "provider_transaction_id": None}
            raise
        return self._payment_state_from_payload(merchant_order_no, payload)

    def _payment_state_from_payload(
        self, merchant_order_no: str, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        state = str(payload.get("trade_state", ""))
        amount = payload.get("amount") or {}
        if state == TRADE_STATE_SUCCESS:
            transaction_id = str(payload.get("transaction_id") or "")
            return {
                "status": PAYMENT_RESULT_SUCCEEDED,
                "provider_transaction_id": transaction_id,
                "amount_cents": int(amount.get("total") or 0),
                "currency": str(amount.get("currency") or "CNY"),
                "paid_at": _iso_to_ms(payload.get("success_time")),
                "event_id": business_event_key(self.name, transaction_id, merchant_order_no),
                "raw_digest": payload_digest(json.dumps(payload, sort_keys=True).encode("utf-8")),
            }
        if state in ("CLOSED", "REVOKED", "PAYERROR"):
            return {
                "status": PAYMENT_RESULT_CLOSED if state != "PAYERROR" else PAYMENT_RESULT_FAILED,
                "provider_transaction_id": payload.get("transaction_id"),
            }
        return {"status": PAYMENT_RESULT_PENDING, "provider_transaction_id": None}

    def close(self, merchant_order_no: str) -> dict[str, Any]:
        pem = self._ensure_trading_configured()
        payload = {"mchid": self._settings.wechat_merchant_id}
        try:
            self._request(
                "POST", f"/v3/pay/transactions/out-trade-no/{merchant_order_no}/close", body=payload, pem=pem
            )
        except GatewayError as exc:
            if exc.retryable:
                raise
            # 已支付/已关闭等状态由服务层复核，不能当作「已关闭」。
            return {"status": PAYMENT_RESULT_SUCCEEDED}
        return {"status": PAYMENT_RESULT_CLOSED}

    def refund(
        self, merchant_order_no: str, refund_no: str, amount_cents: int, reason: str
    ) -> dict[str, Any]:
        pem = self._ensure_trading_configured()
        payload = {
            "out_trade_no": merchant_order_no,
            "out_refund_no": refund_no,
            "reason": reason[:80],
            "notify_url": f"{self._settings.public_origin.rstrip('/')}{self._settings.api_prefix}"
            "/payment-notifications/wechat",
            "amount": {
                "refund": int(amount_cents),
                "total": int(amount_cents),
                "currency": "CNY",
            },
        }
        try:
            result = self._request("POST", "/v3/refund/domestic/refunds", body=payload, pem=pem)
        except GatewayError as exc:
            # 退款超时/网络失败：结果未知，保留预约额度并继续查单。
            if exc.retryable:
                return {"status": REFUND_RESULT_PROCESSING, "provider_refund_id": None}
            raise
        return _refund_state(result)

    def query_refund(self, refund_no: str) -> dict[str, Any]:
        pem = self._ensure_trading_configured()
        try:
            result = self._request("GET", f"/v3/refund/domestic/refunds/{refund_no}", pem=pem)
        except GatewayError as exc:
            if exc.retryable:
                raise
            return {"status": "NOT_FOUND", "provider_refund_id": None}
        return _refund_state(result)

    def download_bill(self, bill_date: str) -> bytes:
        pem = self._ensure_trading_configured()
        result = self._request(
            "GET", f"/v3/bill/tradebill?bill_date={bill_date}&bill_type=ALL", pem=pem
        )
        download_url = str(result.get("download_url") or "")
        host = urlparse(download_url).hostname or ""
        if not download_url or not any(host.endswith(allowed) for allowed in ALLOWED_BILL_HOSTS):
            raise GatewayError("微信账单下载地址不在白名单内", retryable=False)
        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.get(download_url)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise GatewayError("微信账单下载失败", retryable=True) from exc
        return response.content

    def parse_bill(self, bill_date: str, raw: bytes) -> list[dict[str, Any]]:
        """解析微信交易账单 CSV。

        微信账单首行是列名（``交易时间,公众账号ID,商户号,特约商户号,设备号,微信订单号,
        商户订单号,...``），前缀有说明行。列名与顺序须按当前官方文档核对，无法识别时
        明确失败而不是猜列位。
        """
        _ = bill_date
        text = raw.decode("utf-8-sig", errors="replace")
        lines = text.splitlines()
        header_index = next((i for i, line in enumerate(lines) if "商户订单号" in line), None)
        if header_index is None:
            raise GatewayError("微信账单格式无法识别，需按当前官方文档更新解析器", retryable=False)
        columns = [item.strip().lstrip("`") for item in lines[header_index].split(",")]
        rows: list[dict[str, Any]] = []
        for line in lines[header_index + 1 :]:
            if not line.strip() or line.startswith("总交易单数"):
                continue
            values = [item.strip().lstrip("`") for item in line.split(",")]
            if len(values) < len(columns):
                continue
            record = dict(zip(columns, values, strict=False))
            order_no = record.get("商户订单号", "")
            amount = cents_from_decimal_text(record.get("应结订单金额") or record.get("订单金额") or "0")
            fee = cents_from_decimal_text(record.get("手续费") or "0")
            rows.append(
                {
                    "merchant_order_no": order_no,
                    "transaction_id": record.get("微信订单号", ""),
                    "kind": "refund" if record.get("退款单号") else "payment",
                    "amount_cents": amount or 0,
                    "currency": "CNY",
                    "refund_no": record.get("商户退款单号") or None,
                    "fee_cents": fee or 0,
                    "occurred_at": _iso_to_ms(record.get("交易时间")),
                }
            )
        return rows

    # ------------------------------------------------------------ 回调验签
    def verify_notification_detailed(
        self, headers: Mapping[str, str], raw_body: bytes
    ) -> NotificationVerdict:
        self._ensure_verification_configured()
        timestamp_raw = header_value(headers, "Wechatpay-Timestamp")
        nonce = header_value(headers, "Wechatpay-Nonce")
        signature = header_value(headers, "Wechatpay-Signature")
        serial = header_value(headers, "Wechatpay-Serial")
        if not timestamp_raw or not timestamp_raw.isdigit() or not nonce or not signature:
            return NotificationVerdict(accepted=False, reason="headers_incomplete")
        timestamp = int(timestamp_raw)
        if abs(int(now_ms() / 1000) - timestamp) * 1000 > REPLAY_WINDOW_MS:
            return NotificationVerdict(accepted=False, reason="timestamp_out_of_window")
        public_key = self._platform_public_key(serial)
        if public_key is None:
            if not self.has_platform_certificates():
                raise GatewayNotConfigured("wechat:platform-certificate")
            # 未知证书序列：可能是伪造，也可能是尚未同步的轮换证书。
            return NotificationVerdict(accepted=False, reason="unknown_cert_serial")
        if not _rsa_verify(public_key, f"{timestamp}\n{nonce}\n{raw_body.decode('utf-8')}\n".encode(), signature):
            return NotificationVerdict(accepted=False, reason="signature_mismatch")
        return self._decrypt_notification(raw_body)

    def _decrypt_notification(self, raw_body: bytes) -> NotificationVerdict:
        try:
            body = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return NotificationVerdict(accepted=False, reason="payload_malformed")
        if not isinstance(body, dict):
            return NotificationVerdict(accepted=False, reason="payload_malformed")
        event_id = str(body.get("id") or "") or None
        event_type = str(body.get("event_type") or "payment.succeeded")
        resource = body.get("resource") or {}
        plaintext = self._decrypt_resource(resource)
        if plaintext is None:
            return NotificationVerdict(accepted=False, reason="decrypt_failed", event_id=event_id)
        transaction_id = str(plaintext.get("transaction_id") or "")
        order_no = str(plaintext.get("out_trade_no") or "")
        amount = plaintext.get("amount") or {}
        total = amount.get("total")
        if not transaction_id or not order_no or not isinstance(total, int):
            return NotificationVerdict(accepted=False, reason="payload_incomplete", event_id=event_id)
        trade_state = str(plaintext.get("trade_state") or "")
        account_ref = _wechat_account_ref(plaintext)
        if trade_state != TRADE_STATE_SUCCESS:
            return NotificationVerdict(
                accepted=True, reason="not_a_success_payment", event_id=event_id, event_type=event_type
            )
        reason = "verified"
        expected = self.expected_account_ref()
        if expected and account_ref and account_ref != expected:
            # 商户/应用不匹配：不丢弃真实收款，标记后由服务层隔离告警（TC-P04）。
            reason = "verified_account_mismatch"
        payment = VerifiedPayment(
            provider=self.name,
            provider_transaction_id=transaction_id,
            merchant_order_no=order_no,
            amount_cents=int(total),
            currency=str(amount.get("currency") or "CNY"),
            paid_at=_iso_to_ms(plaintext.get("success_time")) or now_ms(),
            raw_digest=payload_digest(raw_body),
            event_id=event_id or business_event_key(self.name, transaction_id, order_no, str(total)),
            account_ref=account_ref,
        )
        return NotificationVerdict(
            accepted=True, reason=reason, payment=payment, event_id=payment.event_id, event_type=event_type
        )

    def _decrypt_resource(self, resource: Mapping[str, Any]) -> dict[str, Any] | None:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        try:
            nonce = str(resource.get("nonce", "")).encode("utf-8")
            associated = str(resource.get("associated_data", "")).encode("utf-8")
            ciphertext = base64.b64decode(str(resource.get("ciphertext", "")))
            plaintext = AESGCM(self._api_v3_key()).decrypt(nonce, ciphertext, associated)
            parsed = json.loads(plaintext.decode("utf-8"))
        except Exception:  # noqa: BLE001 - 解密失败一律拒绝，不泄露细节
            return None
        return parsed if isinstance(parsed, dict) else None

    def verify_notification(
        self, headers: Mapping[str, str], raw_body: bytes
    ) -> VerifiedPayment | None:
        return self.verify_notification_detailed(headers, raw_body).payment

    def expected_account_ref(self) -> str | None:
        mchid = self._settings.wechat_merchant_id
        appid = self._settings.wechat_app_id
        if not mchid or not appid:
            # 只配置了其中一项时不比对完整标识，避免把合法通知误判为商户不符。
            return None
        return f"mchid:{mchid}|appid:{appid}"

    def ack_success(self) -> dict[str, Any]:
        return {"code": "SUCCESS", "message": "成功"}

    def ack_failure(self, reason: str) -> dict[str, Any]:
        _ = reason
        return {"code": "FAIL", "message": "验签失败"}


def _wechat_account_ref(plaintext: Mapping[str, Any]) -> str | None:
    mchid = plaintext.get("mchid")
    appid = plaintext.get("appid")
    if not mchid and not appid:
        return None
    return f"mchid:{mchid or ''}|appid:{appid or ''}"


def _refund_state(payload: Mapping[str, Any]) -> dict[str, Any]:
    status = str(payload.get("status") or "")
    mapping = {
        "SUCCESS": REFUND_RESULT_SUCCEEDED,
        "PROCESSING": REFUND_RESULT_PROCESSING,
        "ABNORMAL": "UNKNOWN",
        "CLOSED": "FAILED",
    }
    amount = payload.get("amount") or {}
    return {
        "status": mapping.get(status, "UNKNOWN"),
        "provider_refund_id": payload.get("refund_id"),
        "amount_cents": int(amount.get("refund") or 0) or None,
    }


def _rsa_verify(public_key: Any, message: bytes, signature_b64: str) -> bool:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    try:
        public_key.verify(
            base64.b64decode(signature_b64), message, padding.PKCS1v15(), hashes.SHA256()
        )
    except (InvalidSignature, ValueError, TypeError):
        return False
    return True


def _iso_to_ms(value: Any) -> int | None:
    if not value:
        return None
    text = str(value).strip()
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return int(moment.timestamp() * 1000)


__all__ = [
    "ACK_TEXT_KEY",
    "API_HOST",
    "ALLOWED_BILL_HOSTS",
    "REPLAY_WINDOW_MS",
    "WechatPayGateway",
    "decimal_text_from_cents",
]
