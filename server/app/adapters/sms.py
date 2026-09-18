"""短信适配器：mock（沙盒）/ 阿里云 / 腾讯云。

- 生产环境使用 mock 会被配置校验直接拒绝（``Settings._enforce_environment_safety``）。
- 沙盒通道把短信写入 ``storage/sandbox/sms.log``，供本地联调读取验证码；
  文件名与内容都不含真实手机号明文（只写脱敏号与 challenge 摘要）。
- 供应商发送超时不无条件重发：调用方记录未知请求，验证码由本地 challenge 控制。
"""

from __future__ import annotations

import hashlib
from typing import Protocol

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
    """阿里云短信通道骨架。

    真实签名算法、模板与参数以商户开通时的官方文档为准（10 文档）；
    未配置密钥时抛 ``GatewayNotConfigured``，绝不静默降级到沙盒。
    """

    name = "aliyun"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _ensure_configured(self) -> None:
        if not self._settings.sms_template_code or not self._settings.sms_sign_name:
            raise GatewayNotConfigured("aliyun-sms")

    def send_code(self, *, to: str, code: str, purpose: str) -> GatewayResult:
        self._ensure_configured()
        payload = {
            "PhoneNumbers": to.lstrip("+"),
            "SignName": self._settings.sms_sign_name,
            "TemplateCode": self._settings.sms_template_code,
            "TemplateParam": f'{{"code":"{code}"}}',
        }
        try:
            with httpx.Client(timeout=5.0) as client:
                response = client.post("https://dysmsapi.aliyuncs.com/", data=payload)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise GatewayError(f"aliyun sms failed: {type(exc).__name__}") from exc
        return GatewayResult(provider=self.name, message_id=response.headers.get("x-acs-request-id", ""))

    def send_text(self, *, to: str, text: str) -> GatewayResult:
        raise GatewayNotConfigured("aliyun-sms-template")


class TencentSmsGateway:
    """腾讯云短信通道骨架（C03/C05：短信通道随云厂商切换）。"""

    name = "tencent"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def send_code(self, *, to: str, code: str, purpose: str) -> GatewayResult:
        raise GatewayNotConfigured("tencent-sms")

    def send_text(self, *, to: str, text: str) -> GatewayResult:
        raise GatewayNotConfigured("tencent-sms")


_gateway: SmsGateway | None = None


def get_gateway() -> SmsGateway:
    global _gateway
    if _gateway is None:
        settings = get_settings()
        if settings.sms_adapter == "mock" or settings.app_env in ("development", "test"):
            _gateway = SandboxSmsGateway(settings)
        elif settings.sms_adapter == "aliyun":
            _gateway = AliyunSmsGateway(settings)
        elif settings.sms_adapter == "tencent":
            _gateway = TencentSmsGateway(settings)
        else:
            raise GatewayNotConfigured(f"sms:{settings.sms_adapter}")
    return _gateway


def reset_gateway() -> None:
    global _gateway
    _gateway = None
