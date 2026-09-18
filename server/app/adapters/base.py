"""适配器公共类型。

``GatewayResult`` 是所有外部调用返回的最小统一形状；供应商原始报文不进入
业务记录，也不返回给用户（06 文档）。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class GatewayResult:
    provider: str
    message_id: str
    accepted: bool = True
    detail: dict[str, str] = field(default_factory=dict)


class GatewayError(RuntimeError):
    """外部服务失败。调用方按业务分支处理，不向上暴露供应商原文。"""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class GatewayNotConfigured(GatewayError):
    """生产环境未配置真实适配器时必须 fail closed，不允许静默降级。"""

    def __init__(self, provider: str) -> None:
        super().__init__(f"{provider} 适配器未配置", retryable=False)
        self.provider = provider
