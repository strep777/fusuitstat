"""电子签适配器工厂（C07）。

``ESIGN_PROVIDER`` 取值：

- ``disabled``（默认）：**不发送、不假装已签**。合同模块会如实记录"未接入电子签渠道"，
  商业上线前的生产配置校验会直接拒绝这种状态（C07 要求合同全部电子签）。
- ``sandbox``：本地联调通道（状态机与回调签名同形），生产禁止。
- 真实厂商名（如 ``fadada`` / ``docusign``）：已登记但**尚未接入**——在拿到平台凭据与
  官方文档前，一律 ``EsignNotConfigured`` fail closed，不退化成沙箱、也不返回"假已签"。
"""

from __future__ import annotations

from collections.abc import Mapping

from app.adapters.base import GatewayNotConfigured
from app.adapters.esign.base import (
    EnvelopeCreated,
    EnvelopeState,
    EsignGateway,
    EsignVerdict,
    SignerInput,
)
from app.adapters.esign.sandbox import SandboxEsignGateway
from app.platform.config import get_settings
from app.platform.payment_providers import SANDBOX_PROVIDERS

#: 已登记的渠道名：``disabled`` 表示未接入；未登记的渠道名按配置错误处理。
SUPPORTED_PROVIDERS = ("disabled", "sandbox", "mock", "fadada", "docusign")

DISABLED_REASON = "ESIGN_DISABLED"


class DisabledEsignGateway:
    """未接入电子签渠道时的占位实现：所有能力都不做，且明确报告原因。

    它**不会**返回成功、不会生成签署链接、不会产生"已签"结论——只是让上层能如实展示
    "当前没有电子签渠道"，而不是把未配置当成已签署。
    """

    name = "disabled"

    def __init__(self, reason: str = DISABLED_REASON) -> None:
        self.reason = reason

    def create_envelope(
        self,
        *,
        document_no: str,
        subject: str,
        snapshot_digest: str,
        signers: list[SignerInput],
        return_url: str | None,
    ) -> EnvelopeCreated:
        _ = (document_no, subject, snapshot_digest, signers, return_url)
        raise GatewayNotConfigured("esign:disabled")

    def query_envelope(self, *, provider_document_id: str, document_no: str) -> EnvelopeState:
        _ = (provider_document_id, document_no)
        raise GatewayNotConfigured("esign:disabled")

    def verify_notification(self, headers: Mapping[str, str], raw_body: bytes) -> EsignVerdict:
        # 未接入渠道时收到"签署回调"本身就是异常：拒绝并说明原因。
        _ = (headers, raw_body)
        return EsignVerdict(accepted=False, reason="esign_disabled")

    def download_signed(self, *, provider_document_id: str) -> bytes:
        _ = provider_document_id
        raise GatewayNotConfigured("esign:disabled")

    def void_envelope(self, *, provider_document_id: str, reason: str) -> EnvelopeState:
        _ = (provider_document_id, reason)
        raise GatewayNotConfigured("esign:disabled")


_gateway: EsignGateway | None = None
_override: EsignGateway | None = None


def esign_enabled() -> bool:
    """是否接入了可用的电子签渠道（``disabled`` 不算接入）。"""
    return provider_name() not in ("", "disabled")


def provider_name() -> str:
    return (get_settings().esign_provider or "").strip().lower()


def get_esign_gateway() -> EsignGateway:
    """取当前电子签渠道；未接入时返回 ``DisabledEsignGateway``（能力调用会 fail closed）。"""
    global _gateway
    if _override is not None:
        return _override
    if _gateway is None:
        _gateway = _build_gateway()
    return _gateway


def _build_gateway() -> EsignGateway:
    name = provider_name()
    if name in ("", "disabled"):
        return DisabledEsignGateway()
    settings = get_settings()
    if name in SANDBOX_PROVIDERS:
        if settings.is_production:
            raise GatewayNotConfigured("esign:sandbox-in-production")
        return SandboxEsignGateway(settings)
    if name in SUPPORTED_PROVIDERS:
        # 真实厂商：拿到凭据与官方文档前不猜 API 形状，明确 fail closed。
        raise GatewayNotConfigured(f"esign:{name}-not-implemented")
    raise GatewayNotConfigured(f"esign:unknown-provider:{name}")


def set_esign_gateway_override(gateway: EsignGateway | None) -> None:
    """测试接缝：替换电子签渠道实例；``None`` 表示撤销替换。"""
    global _override
    _override = gateway


def reset_esign_gateway() -> None:
    global _gateway, _override
    _gateway = None
    _override = None


__all__ = [
    "DISABLED_REASON",
    "SUPPORTED_PROVIDERS",
    "DisabledEsignGateway",
    "esign_enabled",
    "get_esign_gateway",
    "provider_name",
    "reset_esign_gateway",
    "set_esign_gateway_override",
]
