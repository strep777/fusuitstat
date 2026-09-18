"""电子签适配器公共契约（C07：合同全部电子签，签署状态必须来自平台）。

渠道无关的最小能力面：

- ``create_envelope``：按合同快照创建签署任务，返回平台文档号、各签署人视图与签署链接；
- ``query_envelope``：服务端主动查询（**唯一被认可的第二种状态来源**，与支付同一口径：
  成功/完成只认已验签回调或服务端查单）；
- ``verify_notification``：回调验签，只在签名/时间戳/结构全部通过时返回结论；
- ``download_signed``：取回签署完成的文件（字节 + 摘要），用于留证。

安全与一致性约定：

1. **未配置凭据 fail closed**：抛 ``EsignNotConfigured``，绝不返回"假已签"；
2. **签署结论只认平台**：本层不根据"用户点了链接"推断已签，也不承认前端回调；
3. **合同快照不可变**：签署任务携带快照摘要，签署后摘要与文件摘要一并留证；
4. **回调原始字节**由调用方落盘留证，本层只报告摘要；
5. **监护人签署**是同一签署任务里的第二个签署人：任一签署人未完成都不算已签。
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

#: 签署任务状态（渠道状态映射到这一组）。
ENVELOPE_DRAFT = "DRAFT"
ENVELOPE_PENDING_SEND = "PENDING_SEND"
ENVELOPE_SENT = "SENT"
ENVELOPE_PARTIALLY_SIGNED = "PARTIALLY_SIGNED"
ENVELOPE_SIGNED = "SIGNED"
ENVELOPE_DECLINED = "DECLINED"
ENVELOPE_EXPIRED = "EXPIRED"
ENVELOPE_VOID = "VOID"

ENVELOPE_STATUSES = (
    ENVELOPE_DRAFT,
    ENVELOPE_PENDING_SEND,
    ENVELOPE_SENT,
    ENVELOPE_PARTIALLY_SIGNED,
    ENVELOPE_SIGNED,
    ENVELOPE_DECLINED,
    ENVELOPE_EXPIRED,
    ENVELOPE_VOID,
)

#: 终止态：不再变化，也不再有"待签署"的期待。
ENVELOPE_TERMINAL = (ENVELOPE_SIGNED, ENVELOPE_DECLINED, ENVELOPE_EXPIRED, ENVELOPE_VOID)

#: 签署人角色（C07/C19）。
SIGNER_CUSTOMER = "customer"
SIGNER_GUARDIAN = "guardian"
SIGNER_STAFF = "staff"

SIGNER_PENDING = "PENDING"
SIGNER_SIGNED = "SIGNED"
SIGNER_DECLINED = "DECLINED"


@dataclass(frozen=True)
class SignerInput:
    """一个签署人。``role`` 取 ``customer`` / ``guardian`` / ``staff``。"""

    role: str
    name: str
    email: str
    #: 未成年委托时的监护人关系说明（例如"母亲"）；非监护人留空。
    relation: str = ""


@dataclass(frozen=True)
class EnvelopeCreated:
    """创建签署任务的结果。"""

    provider: str
    provider_document_id: str
    status: str
    sign_url: str | None
    signers: tuple[dict[str, Any], ...] = ()
    expires_at: int | None = None
    raw_digest: str = ""


@dataclass(frozen=True)
class EnvelopeState:
    """服务端查单得到的签署状态。"""

    status: str
    signers: tuple[dict[str, Any], ...] = ()
    signed_digest: str | None = None
    completed_at: int | None = None
    decline_reason: str | None = None


@dataclass(frozen=True)
class EsignVerdict:
    """一次回调的验签结论；``accepted=False`` 时 ``reason`` 是稳定机器可读原因。"""

    accepted: bool
    reason: str
    state: EnvelopeState | None = None
    provider_document_id: str | None = None
    event_id: str | None = None
    event_type: str = "envelope.status_changed"
    payload: dict[str, Any] = field(default_factory=dict)


class EsignGateway(Protocol):
    """电子签渠道能力协议。``name`` 与配置里的渠道名一致。"""

    name: str

    def create_envelope(
        self,
        *,
        document_no: str,
        subject: str,
        snapshot_digest: str,
        signers: list[SignerInput],
        return_url: str | None,
    ) -> EnvelopeCreated:
        """创建签署任务（``document_no`` 是我方业务单号，重试必须复用同一个）。"""
        ...

    def query_envelope(self, *, provider_document_id: str, document_no: str) -> EnvelopeState:
        """服务端查单。"""
        ...

    def verify_notification(self, headers: Mapping[str, str], raw_body: bytes) -> EsignVerdict:
        """回调验签 + 状态解析。"""
        ...

    def download_signed(self, *, provider_document_id: str) -> bytes:
        """取回签署完成的文件字节（用于留证与摘要核对）。"""
        ...

    def void_envelope(self, *, provider_document_id: str, reason: str) -> EnvelopeState:
        """作废签署任务（例如报价被撤回或客户取消）。"""
        ...


def payload_digest(raw: bytes) -> str:
    """回调/文件字节的 SHA-256；库里只存摘要。"""
    return hashlib.sha256(raw).hexdigest()


def snapshot_digest(snapshot: Mapping[str, Any]) -> str:
    """合同快照摘要：字段排序后序列化，保证同内容同摘要（可核对"签的是哪一版"）。"""
    import json

    canonical = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = [
    "ENVELOPE_DECLINED",
    "ENVELOPE_DRAFT",
    "ENVELOPE_EXPIRED",
    "ENVELOPE_PARTIALLY_SIGNED",
    "ENVELOPE_PENDING_SEND",
    "ENVELOPE_SENT",
    "ENVELOPE_SIGNED",
    "ENVELOPE_STATUSES",
    "ENVELOPE_TERMINAL",
    "ENVELOPE_VOID",
    "SIGNER_CUSTOMER",
    "SIGNER_DECLINED",
    "SIGNER_GUARDIAN",
    "SIGNER_PENDING",
    "SIGNER_SIGNED",
    "SIGNER_STAFF",
    "EnvelopeCreated",
    "EnvelopeState",
    "EsignGateway",
    "EsignVerdict",
    "SignerInput",
    "payload_digest",
    "snapshot_digest",
]
