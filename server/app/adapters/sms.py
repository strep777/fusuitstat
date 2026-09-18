"""短信适配器：mock（沙盒）/ 阿里云 / 腾讯云。

- 生产环境使用 mock 会被配置校验直接拒绝（``Settings._enforce_environment_safety``）。
- 沙盒通道把短信写入 ``storage/sandbox/sms.log``，供本地联调读取验证码；
  文件名与内容都不含真实手机号明文（只写脱敏号与 challenge 摘要）。
- 供应商发送超时不无条件重发：调用方记录未知请求，验证码由本地 challenge 控制。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from datetime import UTC, datetime
from typing import Protocol
from urllib.parse import quote

import httpx

from app.adapters.base import GatewayError, GatewayNotConfigured, GatewayResult
from app.platform.config import Settings, get_settings
from app.platform.ids import now_ms
from app.platform.security import mask_phone


class SmsGateway(Protocol):
    name: str

    def send_code(self, *, to: str, code: str, purpose: str) -> GatewayResult: ...

    def send_text(self, *, to: str, text: str) -> GatewayResult: ...


class SandboxSmsGateway:
    """沙盒通道：不连接任何真实服务商，结果明确标记为 sandbox。"""

    name = "sandbox"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._path = settings.storage_root / "sandbox" / "sms.log"

    def _write(self, *, to: str, text: str, purpose: str) -> GatewayResult:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(f"{to}:{now_ms()}".encode()).hexdigest()[:12]
        line = (
            f"{now_ms()} purpose={purpose} to={mask_phone(to)} ref={digest} "
            f"body={text}\n"
        )
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(line)
        return GatewayResult(provider=self.name, message_id=f"sandbox-{digest}", detail={"sandbox": "true"})

    def send_code(self, *, to: str, code: str, purpose: str) -> GatewayResult:
        minutes = max(1, self._settings.otp_ttl_seconds // 60)
        text = f"【{self._settings.sms_sign_name}】验证码 {code}，{minutes} 分钟内有效，请勿泄露。"
        return self._write(to=to, text=text, purpose=purpose)

    def send_text(self, *, to: str, text: str) -> GatewayResult:
        return self._write(to=to, text=text, purpose="notice")


class AliyunSmsGateway:
    """阿里云短信通道（RPC 风格 v1 签名，``SendSms``）。

    参考阿里云「短信服务 API——签名机制」：把公共参数与业务参数按参数名排序，
    用 ``percent_encode`` 拼接成规范化查询串，再以 ``AccessKeySecret + "&"`` 为密钥做
    HMAC-SHA1 并 Base64，得到 ``Signature``。

    未配置密钥时抛 ``GatewayNotConfigured``，绝不静默降级到沙盒（10 文档）。
    真实通道的业务验收仍属 BLOCKED（R30），这里保证的是**可联调的签名与错误处理**。
    """

    name = "aliyun"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _ensure_configured(self) -> None:
        if not self._settings.sms_template_code or not self._settings.sms_sign_name:
            raise GatewayNotConfigured("aliyun-sms")
        if not self._settings.aliyun_sms_access_key_id or not self._settings.aliyun_sms_access_key_secret:
            raise GatewayNotConfigured("aliyun-sms-credentials")

    @staticmethod
    def _percent_encode(value: str) -> str:
        """RFC 3986 编码：``~`` 不转义，空格为 ``%20``，``+`` 必须转义。"""
        return quote(str(value), safe="~")

    def _signed_params(self, params: dict[str, str]) -> dict[str, str]:
        moment = datetime.now(UTC)
        common = {
            "Format": "JSON",
            "Version": "2017-05-25",
            "AccessKeyId": self._settings.aliyun_sms_access_key_id,
            "SignatureMethod": "HMAC-SHA1",
            "SignatureVersion": "1.0",
            "SignatureNonce": uuid.uuid4().hex,
            "Timestamp": moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "Action": "SendSms",
            "RegionId": "cn-hangzhou",
        }
        merged = {**common, **params}
        canonical = "&".join(
            f"{self._percent_encode(key)}={self._percent_encode(merged[key])}"
            for key in sorted(merged)
        )
        string_to_sign = "POST&%2F&" + self._percent_encode(canonical)
        digest = hmac.new(
            (self._settings.aliyun_sms_access_key_secret + "&").encode(),
            string_to_sign.encode(),
            hashlib.sha1,
        ).digest()
        return {**merged, "Signature": base64.b64encode(digest).decode()}

    def _send(self, *, to: str, template_code: str, template_param: dict[str, str]) -> GatewayResult:
        payload = self._signed_params(
            {
                "PhoneNumbers": to.lstrip("+"),
                "SignName": self._settings.sms_sign_name,
                "TemplateCode": template_code,
                "TemplateParam": json.dumps(template_param, ensure_ascii=False, separators=(",", ":")),
            }
        )
        try:
            with httpx.Client(timeout=5.0) as client:
                response = client.post(self._settings.aliyun_sms_endpoint, data=payload)
                response.raise_for_status()
                body = response.json()
        except httpx.HTTPError as exc:
            # 超时不无条件重发：调用方记录未知请求，验证码由本地 challenge 控制。
            raise GatewayError(f"aliyun sms failed: {type(exc).__name__}") from exc
        except ValueError as exc:  # 非 JSON 响应（例如网关错误页）
            raise GatewayError("aliyun sms returned non-json body") from exc
        code = str(body.get("Code") or "")
        if code != "OK":
            # 供应商错误码原样带出，便于运营按渠道文档定位（不含手机号）。
            raise GatewayError(f"aliyun sms rejected: {code or 'UNKNOWN'}")
        return GatewayResult(
            provider=self.name,
            message_id=str(body.get("RequestId") or ""),
            detail={"biz_id": str(body.get("BizId") or "")},
        )

    def send_code(self, *, to: str, code: str, purpose: str) -> GatewayResult:
        self._ensure_configured()
        return self._send(to=to, template_code=self._settings.sms_template_code, template_param={"code": code})

    def send_text(self, *, to: str, text: str) -> GatewayResult:
        self._ensure_configured()
        if not self._settings.sms_notice_template_code:
            raise GatewayNotConfigured("aliyun-sms-notice-template")
        return self._send(
            to=to,
            template_code=self._settings.sms_notice_template_code,
            template_param={"content": text},
        )


class TencentSmsGateway:
    """腾讯云短信通道（TC3-HMAC-SHA256 签名，``SendSms`` / 2021-01-11）。

    签名步骤与腾讯云「签名方法 v3」一致：规范化请求（``POST`` + 规范化 URI + 查询串 +
    规范化头 + 签名头 + 哈希后的请求体）→ 待签名字符串 → 逐级派生签名密钥 →
    ``Authorization`` 头。未配置密钥时抛 ``GatewayNotConfigured``。
    """

    name = "tencent"

    SERVICE = "sms"
    HOST = "sms.tencentcloudapi.com"
    VERSION = "2021-01-11"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _ensure_configured(self) -> None:
        if not self._settings.tencent_sms_secret_id or not self._settings.tencent_sms_secret_key:
            raise GatewayNotConfigured("tencent-sms-credentials")
        if not self._settings.tencent_sms_sdk_app_id or not self._settings.tencent_sms_template_id:
            raise GatewayNotConfigured("tencent-sms")

    def _authorization(self, *, payload: str, timestamp: int) -> str:
        date = datetime.fromtimestamp(timestamp, tz=UTC).strftime("%Y-%m-%d")
        canonical_headers = f"content-type:application/json; charset=utf-8\nhost:{self.HOST}\n"
        signed_headers = "content-type;host"
        hashed_payload = hashlib.sha256(payload.encode()).hexdigest()
        canonical_request = "\n".join(
            ["POST", "/", "", canonical_headers, signed_headers, hashed_payload]
        )
        credential_scope = f"{date}/{self.SERVICE}/tc3_request"
        string_to_sign = "\n".join(
            [
                "TC3-HMAC-SHA256",
                str(timestamp),
                credential_scope,
                hashlib.sha256(canonical_request.encode()).hexdigest(),
            ]
        )

        def _sign(key: bytes, message: str) -> bytes:
            return hmac.new(key, message.encode(), hashlib.sha256).digest()

        secret_date = _sign(f"TC3{self._settings.tencent_sms_secret_key}".encode(), date)
        secret_service = _sign(secret_date, self.SERVICE)
        secret_signing = _sign(secret_service, "tc3_request")
        signature = hmac.new(secret_signing, string_to_sign.encode(), hashlib.sha256).hexdigest()
        return (
            f"TC3-HMAC-SHA256 Credential={self._settings.tencent_sms_secret_id}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )

    def _send(self, *, to: str, template_id: str, params: list[str]) -> GatewayResult:
        timestamp = int(time.time())
        body = {
            "PhoneNumberSet": [to if to.startswith("+") else f"+{to}"],
            "SmsSdkAppId": self._settings.tencent_sms_sdk_app_id,
            "SignName": self._settings.sms_sign_name,
            "TemplateId": template_id,
            "TemplateParamSet": params,
        }
        payload = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
        headers = {
            "Authorization": self._authorization(payload=payload, timestamp=timestamp),
            "Content-Type": "application/json; charset=utf-8",
            "Host": self.HOST,
            "X-TC-Action": "SendSms",
            "X-TC-Timestamp": str(timestamp),
            "X-TC-Version": self.VERSION,
            "X-TC-Region": self._settings.tencent_sms_region,
        }
        try:
            with httpx.Client(timeout=5.0) as client:
                response = client.post(
                    self._settings.tencent_sms_endpoint, content=payload, headers=headers
                )
                response.raise_for_status()
                body_json = response.json()
        except httpx.HTTPError as exc:
            raise GatewayError(f"tencent sms failed: {type(exc).__name__}") from exc
        except ValueError as exc:
            raise GatewayError("tencent sms returned non-json body") from exc
        result = body_json.get("Response") or {}
        error = result.get("Error")
        if error:
            raise GatewayError(
                f"tencent sms rejected: {error.get('Code') or 'UNKNOWN'}"
            )
        statuses = result.get("SendStatusSet") or []
        first = statuses[0] if statuses else {}
        if first and str(first.get("Code")) not in ("Ok", "OK"):
            raise GatewayError(f"tencent sms rejected: {first.get('Code') or 'UNKNOWN'}")
        return GatewayResult(
            provider=self.name,
            message_id=str(first.get("SerialNo") or result.get("RequestId") or ""),
            detail={"request_id": str(result.get("RequestId") or "")},
        )

    def send_code(self, *, to: str, code: str, purpose: str) -> GatewayResult:
        self._ensure_configured()
        # 腾讯云验证码模板要求两个参数：验证码与有效期（分钟）。
        minutes = str(max(1, self._settings.otp_ttl_seconds // 60))
        return self._send(to=to, template_id=self._settings.tencent_sms_template_id, params=[code, minutes])

    def send_text(self, *, to: str, text: str) -> GatewayResult:
        self._ensure_configured()
        if not self._settings.tencent_sms_notice_template_id:
            raise GatewayNotConfigured("tencent-sms-notice-template")
        return self._send(
            to=to, template_id=self._settings.tencent_sms_notice_template_id, params=[text]
        )


_gateway: SmsGateway | None = None


def get_gateway() -> SmsGateway:
    global _gateway
    if _gateway is None:
        settings = get_settings()
        if settings.app_env in ("development", "test") or settings.sms_adapter == "mock":
            # 开发/测试统一走沙盒通道；生产环境使用 mock 会被配置校验直接拒绝。
            _gateway = SandboxSmsGateway(settings)
        elif settings.sms_adapter == "aliyun":
            _gateway = AliyunSmsGateway(settings)
        elif settings.sms_adapter == "tencent":
            _gateway = TencentSmsGateway(settings)
        else:
            raise GatewayNotConfigured(f"sms:{settings.sms_adapter}")
    return _gateway


def build_gateway(adapter: str, settings: Settings) -> SmsGateway:
    """按名称构造**真实**通道（不落到沙盒）。

    生产装配走 ``get_gateway``；本函数给联调与测试用：可以在开发环境直接验证
    阿里云/腾讯云的签名与错误处理，而不必把全局适配器切成真实通道。
    """
    if adapter == "aliyun":
        return AliyunSmsGateway(settings)
    if adapter == "tencent":
        return TencentSmsGateway(settings)
    if adapter in ("mock", "sandbox"):
        return SandboxSmsGateway(settings)
    raise GatewayNotConfigured(f"sms:{adapter}")


def reset_gateway() -> None:
    global _gateway
    _gateway = None
