"""电子签沙箱通道（仅开发/测试；生产配置校验直接拒绝）。

行为对齐真实平台的关键点，便于在本地把**签署生命周期**跑通：

- 创建签署任务时返回一个签名后的**签署链接**（``link`` 参数带 HMAC，可校验）；
- 回调与真实平台同形：``X-Esign-Timestamp`` + ``X-Esign-Signature``，待签名串
  ``{timestamp}.{raw_body}``，HMAC-SHA256；时间戳超窗拒绝（防重放）；
- 状态机与真实平台一致：``SENT`` → ``PARTIALLY_SIGNED``（部分签署人完成）→ ``SIGNED``
  （全部签署人完成）；任一签署人拒签 → ``DECLINED``；
- **签完才产生文件**：``download_signed`` 在未完成时抛错，绝不返回空文件冒充已签。

它不会、也不能让"点了一下链接"变成已签：状态变化必须由签名正确且结构完整的回调驱动。
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlencode

from app.adapters.base import GatewayError
from app.adapters.esign.base import (
    ENVELOPE_DECLINED,
    ENVELOPE_PARTIALLY_SIGNED,
    ENVELOPE_SENT,
    ENVELOPE_SIGNED,
    SIGNER_DECLINED,
    SIGNER_PENDING,
    SIGNER_SIGNED,
    EnvelopeCreated,
    EnvelopeState,
    EsignVerdict,
    SignerInput,
    payload_digest,
)
from app.platform.config import Settings
from app.platform.ids import now_ms

REPLAY_WINDOW_SECONDS = 300


class SandboxEsignGateway:
    """本地电子签通道：签名与状态机同形，但不接触任何真实平台。"""

    name = "sandbox"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._documents: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------ 密钥与签名
    def _secret(self) -> str:
        from app.adapters.base import GatewayNotConfigured

        secret = (self._settings.esign_webhook_secret or "").strip()
        if not secret:
            raise GatewayNotConfigured("esign:webhook-secret")
        return secret

    def link_signature(self, document_no: str, signer_role: str) -> str:
        """签署链接的签名：伪造/篡改链接在校验时会被发现。"""
        message = f"{document_no}:{signer_role}".encode()
        return hmac.new(self._secret().encode("utf-8"), message, hashlib.sha256).hexdigest()[:32]

    def verify_link(self, document_no: str, signer_role: str, signature: str) -> bool:
        return hmac.compare_digest(self.link_signature(document_no, signer_role), signature or "")

    def sign_url(self, document_no: str, signer: SignerInput) -> str:
        query = urlencode(
            {
                "document_no": document_no,
                "role": signer.role,
                "sig": self.link_signature(document_no, signer.role),
            }
        )
        return f"{self._settings.public_origin.rstrip('/')}/esign/sandbox?{query}"

    def sign_notification(
        self, *, document_no: str, signed_roles: list[str], declined_roles: list[str] | None = None
    ) -> tuple[dict[str, str], bytes]:
        """构造一次合法回调（测试与本地联调用；真实平台由平台发出）。"""
        body = self.build_notification(
            document_no=document_no, signed_roles=signed_roles, declined_roles=declined_roles
        )
        timestamp = int(now_ms() // 1000)
        signature = hmac.new(
            self._secret().encode("utf-8"), f"{timestamp}.".encode() + body, hashlib.sha256
        ).hexdigest()
        return {"X-Esign-Timestamp": str(timestamp), "X-Esign-Signature": signature}, body

    def build_notification(
        self, *, document_no: str, signed_roles: list[str], declined_roles: list[str] | None = None
    ) -> bytes:
        document = self._documents.get(document_no) or {}
        signers = [
            {
                "role": item["role"],
                "name": item["name"],
                "email": item["email"],
                "status": (
                    SIGNER_DECLINED
                    if item["role"] in (declined_roles or [])
                    else SIGNER_SIGNED
                    if item["role"] in signed_roles
                    else SIGNER_PENDING
                ),
                "signed_at": int(now_ms()) if item["role"] in signed_roles else None,
            }
            for item in (document.get("signers") or [])
        ]
        return json.dumps(
            {
                "event_id": f"evt-{document_no}-{len(signed_roles)}-{len(declined_roles or [])}",
                "event_type": "envelope.status_changed",
                "document_no": document_no,
                "provider_document_id": document.get("provider_document_id"),
                "signers": signers,
                "decline_reason": "客户拒签" if declined_roles else None,
            },
            ensure_ascii=False,
        ).encode("utf-8")

    # ------------------------------------------------------------ 能力
    def create_envelope(
        self,
        *,
        document_no: str,
        subject: str,
        snapshot_digest: str,
        signers: list[SignerInput],
        return_url: str | None,
    ) -> EnvelopeCreated:
        if not signers:
            raise GatewayError("电子签签署人不能为空", retryable=False)
        provider_document_id = f"sbx-{hashlib.sha256(document_no.encode()).hexdigest()[:16]}"
        self._documents[document_no] = {
            "provider_document_id": provider_document_id,
            "subject": subject,
            "snapshot_digest": snapshot_digest,
            "signers": [
                {"role": item.role, "name": item.name, "email": item.email, "relation": item.relation}
                for item in signers
            ],
            "return_url": return_url,
        }
        expires_at = now_ms() + max(1, int(self._settings.esign_link_ttl_hours)) * 3600 * 1000
        return EnvelopeCreated(
            provider=self.name,
            provider_document_id=provider_document_id,
            status=ENVELOPE_SENT,
            sign_url=self.sign_url(document_no, signers[0]),
            signers=tuple(
                {
                    "role": item.role,
                    "name": item.name,
                    "email": item.email,
                    "relation": item.relation,
                    "status": SIGNER_PENDING,
                    "sign_url": self.sign_url(document_no, item),
                }
                for item in signers
            ),
            expires_at=expires_at,
            raw_digest=payload_digest(f"{document_no}:{subject}:{snapshot_digest}".encode()),
        )

    def query_envelope(self, *, provider_document_id: str, document_no: str) -> EnvelopeState:
        document = self._documents.get(document_no)
        if document is None or document.get("provider_document_id") != provider_document_id:
            return EnvelopeState(status="NOT_FOUND")
        signers = document.get("signed_roles") or []
        declined = document.get("declined_roles") or []
        return EnvelopeState(
            status=self._status_of(signers, declined, document),
            signers=tuple(document.get("signers") or ()),
            decline_reason="客户拒签" if declined else None,
        )

    def _status_of(self, signed_roles: list[str], declined_roles: list[str], document: dict[str, Any]) -> str:
        if declined_roles:
            return ENVELOPE_DECLINED
        total = len(document.get("signers") or [])
        if total and len(set(signed_roles)) >= total:
            return ENVELOPE_SIGNED
        if signed_roles:
            return ENVELOPE_PARTIALLY_SIGNED
        return ENVELOPE_SENT

    def verify_notification(self, headers: Mapping[str, str], raw_body: bytes) -> EsignVerdict:
        from app.adapters.esign.base import ENVELOPE_SIGNED as SIGNED

        timestamp = _header(headers, "X-Esign-Timestamp")
        signature = _header(headers, "X-Esign-Signature")
        if not timestamp or not signature:
            return EsignVerdict(accepted=False, reason="signature_missing")
        if not timestamp.isdigit() or abs(int(now_ms() // 1000) - int(timestamp)) > REPLAY_WINDOW_SECONDS:
            return EsignVerdict(accepted=False, reason="timestamp_out_of_window")
        expected = hmac.new(
            self._secret().encode("utf-8"), f"{timestamp}.".encode() + raw_body, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, signature.strip().lower()):
            return EsignVerdict(accepted=False, reason="signature_mismatch")
        try:
            event = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return EsignVerdict(accepted=False, reason="payload_malformed")
        if not isinstance(event, Mapping):
            return EsignVerdict(accepted=False, reason="payload_malformed")
        document_no = str(event.get("document_no") or "")
        if not document_no:
            return EsignVerdict(accepted=False, reason="document_no_missing")
        signers = [dict(item) for item in (event.get("signers") or []) if isinstance(item, Mapping)]
        signed_roles = [str(item.get("role")) for item in signers if item.get("status") == SIGNER_SIGNED]
        declined_roles = [
            str(item.get("role")) for item in signers if item.get("status") == SIGNER_DECLINED
        ]
        document = self._documents.get(document_no)
        if document is None:
            # 未发起过的签署任务：拒绝，交由人工核对（不落地任何状态）。
            return EsignVerdict(accepted=False, reason="unknown_document", event_id=str(event.get("event_id") or "") or None)
        document["signed_roles"] = signed_roles
        document["declined_roles"] = declined_roles
        document["signers"] = signers
        status = self._status_of(signed_roles, declined_roles, document)
        state = EnvelopeState(
            status=status,
            signers=tuple(signers),
            signed_digest=(
                hashlib.sha256(
                    json.dumps(
                        {"document_no": document_no, "signers": signers, "snapshot": document["snapshot_digest"]},
                        ensure_ascii=False,
                        sort_keys=True,
                    ).encode("utf-8")
                ).hexdigest()
                if status == SIGNED
                else None
            ),
            completed_at=int(now_ms()) if status == SIGNED else None,
            decline_reason=str(event.get("decline_reason") or "") or None,
        )
        return EsignVerdict(
            accepted=True,
            reason="verified",
            state=state,
            provider_document_id=str(event.get("provider_document_id") or "") or None,
            event_id=str(event.get("event_id") or "") or None,
            payload={"document_no": document_no},
        )

    def download_signed(self, *, provider_document_id: str) -> bytes:
        for document_no, document in self._documents.items():
            if document.get("provider_document_id") != provider_document_id:
                continue
            status = self._status_of(
                document.get("signed_roles") or [], document.get("declined_roles") or [], document
            )
            if status != ENVELOPE_SIGNED:
                # 未签完就没有文件：不允许返回空文件冒充已签。
                raise GatewayError("签署尚未完成，暂无签署文件", retryable=False)
            return json.dumps(
                {
                    "document_no": document_no,
                    "provider_document_id": provider_document_id,
                    "snapshot_digest": document["snapshot_digest"],
                    "signers": document.get("signers") or [],
                    "signed_at": int(now_ms()),
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        raise GatewayError("签署任务不存在", retryable=False)

    def void_envelope(self, *, provider_document_id: str, reason: str) -> EnvelopeState:
        for document in self._documents.values():
            if document.get("provider_document_id") == provider_document_id:
                document["voided_reason"] = reason
                return EnvelopeState(status="VOID", decline_reason=reason)
        return EnvelopeState(status="NOT_FOUND")

    # ------------------------------------------------------------ 本地"签署"动作
    def mark_signed_locally(self, *, document_no: str, role: str) -> EnvelopeState:
        """沙箱专用：模拟签署人在平台上完成签署（真实平台由平台侧完成）。"""
        document = self._documents.get(document_no)
        if document is None:
            return EnvelopeState(status="NOT_FOUND")
        signed = set(document.get("signed_roles") or [])
        signed.add(role)
        document["signed_roles"] = sorted(signed)
        return self.query_envelope(
            provider_document_id=str(document["provider_document_id"]), document_no=document_no
        )


def _header(headers: Mapping[str, str], name: str) -> str | None:
    target = name.lower()
    for key, value in headers.items():
        if key.lower() == target:
            return value
    return None


__all__ = ["REPLAY_WINDOW_SECONDS", "SandboxEsignGateway"]
