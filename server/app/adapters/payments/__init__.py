"""支付适配器工厂。

``get_gateway(provider)`` 读取 ``Settings.enabled_payment_providers``（``PAYMENT_PROVIDERS``
逗号分隔）；未启用的 provider 抛 ``AppError("PAYMENT_PROVIDER_DISABLED")``，
已启用但凭据缺失的真实通道由各自适配器抛 ``GatewayNotConfigured``，
**绝不静默降级到沙箱**（生产环境配置校验也会拒绝 ``PAYMENT_PROVIDERS`` 含 mock）。

``set_gateway_override`` / ``reset_gateways`` 是测试接缝：集成测试用它把某个
真实渠道替换为沙箱实现（渠道侧账本 + 已签名回调），从而在不接触真实渠道的前提下
验证支付时序；生产代码不使用它们。
"""

from __future__ import annotations

from app.adapters.base import GatewayNotConfigured
from app.adapters.payments.base import PaymentGateway
from app.adapters.payments.sandbox import SandboxPaymentGateway
from app.platform.config import get_settings
from app.platform.errors import AppError
from app.platform.payment_providers import SANDBOX_PROVIDERS

#: 沙箱通道的别名（``mock`` 为历史配置名，生产已被配置校验拒绝）。
#: 已登记的渠道名。
SUPPORTED_PROVIDERS = ("wechat", "alipay", "paypal", "stripe", "sandbox", "mock")

_gateways: dict[str, PaymentGateway] = {}
_overrides: dict[str, PaymentGateway] = {}


def normalize_provider(provider: str) -> str:
    return (provider or "").strip().lower()


def enabled_providers() -> list[str]:
    """当前启用的渠道名（来自 ``PAYMENT_PROVIDERS``）。"""
    return get_settings().enabled_payment_providers


def get_gateway(provider: str) -> PaymentGateway:
    """按渠道名取适配器；未启用或已登记但未接入时按稳定错误码失败。"""
    name = normalize_provider(provider)
    if not name:
        raise AppError("VALIDATION_ERROR", "请指定支付方式。", fields={"provider": "必填"})
    if name not in enabled_providers():
        raise AppError("PAYMENT_PROVIDER_DISABLED", f"支付方式「{name}」当前不可用。")
    if get_settings().is_production and name in SANDBOX_PROVIDERS:
        raise AppError("PAYMENT_PROVIDER_DISABLED", "生产环境禁止沙盒支付。")
    if name in _overrides:
        return _overrides[name]
    if name not in _gateways:
        _gateways[name] = _build_gateway(name)
    return _gateways[name]


def _build_gateway(name: str) -> PaymentGateway:
    settings = get_settings()
    if name in SANDBOX_PROVIDERS:
        if settings.is_production:
            raise AppError("PAYMENT_PROVIDER_DISABLED", "生产环境禁止沙盒支付。")
        return SandboxPaymentGateway(settings, provider=name)
    if name == "wechat":
        from app.adapters.payments.wechat import WechatPayGateway

        return WechatPayGateway(settings)
    if name == "alipay":
        from app.adapters.payments.alipay import AlipayGateway

        return AlipayGateway(settings)
    if name == "paypal":
        from app.adapters.payments.paypal import PaypalGateway

        return PaypalGateway(settings)
    if name == "stripe":
        # 已登记但尚未接入：明确 fail closed，而不是退化成沙箱。
        raise GatewayNotConfigured("stripe")
    raise AppError("PAYMENT_PROVIDER_DISABLED", f"支付方式「{name}」当前不可用。")


def set_gateway_override(provider: str, gateway: PaymentGateway | None) -> None:
    """测试接缝：替换某个渠道的适配器实例；``None`` 表示撤销替换。"""
    name = normalize_provider(provider)
    if gateway is None:
        _overrides.pop(name, None)
        return
    _overrides[name] = gateway


def reset_gateways() -> None:
    """测试辅助：清空实例缓存与替换。"""
    _gateways.clear()
    _overrides.clear()


__all__ = [
    "SANDBOX_PROVIDERS",
    "SUPPORTED_PROVIDERS",
    "PaymentGateway",
    "SandboxPaymentGateway",
    "enabled_providers",
    "get_gateway",
    "normalize_provider",
    "reset_gateways",
    "set_gateway_override",
]
