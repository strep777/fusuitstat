"""合同电子签的用例层（C07：全部电子签；C19：未成年委托需要监护人签署）。

职责边界：

- **只认平台结论**：`send_contract` 负责把合同快照交给电子签渠道并登记签署任务；
  状态变化只能来自 `apply_esign_notification`（已验签回调）或 `refresh_envelope`
  （服务端查单）。前端"点了签署链接"不改变任何状态。
- **快照不可变**：签署任务携带 `snapshot_digest`（合同快照摘要），签署完成后把文件
  摘要一并留证，可核对"签的是哪一版"。
- **监护人**：`guardian_required` 的委托，签署任务里必须有监护人签署人；**任一签署人
  未完成都不算已签**，制作开工前必须有已签合同（见 `assert_contract_signed`）。
- **未接入渠道如实呈现**：`ESIGN_PROVIDER=disabled` 时任务以 `PENDING_SEND` 落库并记录
  失败原因，不生成假链接、不写"已签署"。
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.adapters.base import GatewayError, GatewayNotConfigured
from app.adapters.esign import (
    DisabledEsignGateway,
    get_esign_gateway,
    provider_name,
)
from app.adapters.esign.base import (
    ENVELOPE_DECLINED,
    ENVELOPE_EXPIRED,
    ENVELOPE_PARTIALLY_SIGNED,
    ENVELOPE_PENDING_SEND,
    ENVELOPE_SENT,
    ENVELOPE_SIGNED,
    ENVELOPE_TERMINAL,
    ENVELOPE_VOID,
    SIGNER_CUSTOMER,
    SIGNER_GUARDIAN,
    SIGNER_PENDING,
    EnvelopeState,
    EsignVerdict,
    SignerInput,
    payload_digest,
    snapshot_digest,
)
from app.modules.commissions.models import ContractEnvelope, Inquiry, Quote
from app.platform import audit
from app.platform.errors import AppError
from app.platform.ids import iso, new_id, now_ms

#: 允许的状态流转（回调乱序/重复投递时按这张表判定）。
ALLOWED_ENVELOPE_TRANSITIONS: dict[str, tuple[str, ...]] = {
    ENVELOPE_PENDING_SEND: (ENVELOPE_SENT, ENVELOPE_VOID, ENVELOPE_EXPIRED, ENVELOPE_DECLINED),
    ENVELOPE_SENT: (
        ENVELOPE_PARTIALLY_SIGNED,
        ENVELOPE_SIGNED,
        ENVELOPE_DECLINED,
        ENVELOPE_EXPIRED,
        ENVELOPE_VOID,
    ),
    ENVELOPE_PARTIALLY_SIGNED: (ENVELOPE_SIGNED, ENVELOPE_DECLINED, ENVELOPE_EXPIRED, ENVELOPE_VOID),
    ENVELOPE_SIGNED: (),
    ENVELOPE_DECLINED: (),
    ENVELOPE_EXPIRED: (),
    ENVELOPE_VOID: (),
}


def _signers_for(
    session: Session, *, inquiry: Inquiry | None, customer_name: str, customer_email: str
) -> tuple[list[SignerInput], bool, str | None]:
    """签署人：客户本人 +（未成年委托时）监护人。

    监护人的姓名/邮箱来自询价里登记的监护人联系信息（C19）。**信息不全时不阻塞接受报价**
    （否则客户会被卡在"既不能接受也不能改询价"的中间态），而是把原因写进签署任务的
    `failure_reason` 并保持 `PENDING_SEND`：签署发不出去、制作也就开不了工，缺口可见。
    """
    signers = [SignerInput(role=SIGNER_CUSTOMER, name=customer_name, email=customer_email)]
    if not (inquiry and inquiry.guardian_required):
        return signers, False, None
    guardian_name = str(inquiry.guardian_name or "").strip()
    guardian_email = str(inquiry.guardian_email or "").strip()
    if not guardian_name or not guardian_email:
        return signers, True, "未成年委托缺少监护人姓名或邮箱，无法发起监护人签署"
    signers.append(
        SignerInput(
            role=SIGNER_GUARDIAN,
            name=guardian_name,
            email=guardian_email,
            relation=str(inquiry.guardian_relation or "").strip(),
        )
    )
    return signers, True, None


def active_envelope(session: Session, *, quote_id: str) -> ContractEnvelope | None:
    """该报价最近一次未终结的签署任务（终结后可重新发起）。"""
    return session.scalars(
        sa.select(ContractEnvelope)
        .where(
            ContractEnvelope.quote_id == quote_id,
            ContractEnvelope.status.not_in(ENVELOPE_TERMINAL),
        )
        .order_by(ContractEnvelope.created_at.desc())
    ).first()


def latest_envelope(session: Session, *, quote_id: str) -> ContractEnvelope | None:
    return session.scalars(
        sa.select(ContractEnvelope)
        .where(ContractEnvelope.quote_id == quote_id)
        .order_by(ContractEnvelope.created_at.desc())
    ).first()


def envelope_for_project(session: Session, *, project_id: str) -> ContractEnvelope | None:
    return session.scalars(
        sa.select(ContractEnvelope)
        .where(ContractEnvelope.project_id == project_id)
        .order_by(ContractEnvelope.created_at.desc())
    ).first()


def latest_envelope_for_inquiry(session: Session, *, inquiry_id: str) -> ContractEnvelope | None:
    """该询价最近一次签署任务（一份询价可能先后有多份报价，取最新一份）。"""
    return session.scalars(
        sa.select(ContractEnvelope)
        .where(ContractEnvelope.inquiry_id == inquiry_id)
        .order_by(ContractEnvelope.created_at.desc())
    ).first()


def get_envelope_for_user(
    session: Session, *, envelope_id: str, user_id: str
) -> ContractEnvelope | None:
    """本人签署任务；非本人一律按不存在处理（不泄露对象是否存在）。"""
    return session.scalars(
        sa.select(ContractEnvelope).where(
            ContractEnvelope.id == envelope_id, ContractEnvelope.user_id == user_id
        )
    ).one_or_none()


def envelope_payload(session: Session, envelope: ContractEnvelope) -> dict[str, Any]:
    """给客户/运营看的签署状态（**不包含**平台凭据与内部备注）。

    `provider_configured` 以**实际在用的渠道实例**为准，而不是配置字符串：
    渠道对象缺失/未接入时如实报 False，避免"配置写了但发不出去"的假象。
    """
    gateway = get_esign_gateway()
    configured = not isinstance(gateway, DisabledEsignGateway)
    signers = [
        {
            "role": item.get("role"),
            "name": item.get("name"),
            "email": item.get("email"),
            "relation": item.get("relation") or "",
            "status": item.get("status") or SIGNER_PENDING,
            "signed_at": iso(int(item["signed_at"])) if item.get("signed_at") else None,
        }
        for item in (envelope.signers or [])
    ]
    awaiting = [item["role"] for item in signers if item["status"] == SIGNER_PENDING]
    return {
        "id": envelope.id,
        "quote_id": envelope.quote_id,
        "order_id": envelope.order_id,
        "project_id": envelope.project_id,
        "provider": envelope.provider,
        "status": envelope.status,
        "subject": envelope.subject,
        "snapshot_digest": envelope.snapshot_digest,
        "signers": signers,
        "guardian_required": bool(envelope.guardian_required),
        "awaiting_roles": awaiting,
        "sign_url": envelope.sign_url,
        "sent_at": iso(envelope.sent_at),
        "expires_at": iso(envelope.expires_at),
        "completed_at": iso(envelope.completed_at),
        "decline_reason": envelope.decline_reason,
        "document_sha256": envelope.document_sha256,
        "attempts": int(envelope.attempts),
        "failure_reason": envelope.failure_reason,
        # 未接入渠道时如实说明，页面据此提示"合同签署尚未接入，请联系客服"。
        "provider_configured": configured,
        "esign_provider": gateway.name if configured else "disabled",
    }


def contract_status_for_project(session: Session, *, project_id: str) -> dict[str, Any]:
    """制作单对应的合同签署状态（运营与客户都能看到同一口径）。"""
    envelope = envelope_for_project(session, project_id=project_id)
    gateway_configured = not isinstance(get_esign_gateway(), DisabledEsignGateway)
    if envelope is None:
        return {
            "status": "NOT_REQUESTED",
            "signed": False,
            "guardian_required": False,
            "provider_configured": gateway_configured,
            "note": "该制作单没有签署任务：电子签渠道未接入时不会自动发起。",
        }
    payload = envelope_payload(session, envelope)
    payload["signed"] = envelope.status == ENVELOPE_SIGNED
    return payload


def create_envelope(
    session: Session,
    *,
    quote: Quote,
    inquiry: Inquiry | None,
    user_id: str,
    customer_name: str,
    customer_email: str,
    order_id: str | None,
    project_id: str | None,
) -> ContractEnvelope:
    """在报价接受时登记签署任务（同一事务）。

    **不在这里调用渠道**：渠道调用放在 `send_contract`，这样"接受报价"不会因为电子签
    渠道故障而整个失败（合同可以稍后由运营重发），同时任务一定留痕。
    """
    if quote.contract_snapshot is None:
        raise AppError("STATE_CONFLICT", "合同快照缺失，无法发起电子签。")
    signers, guardian_required, blocked_reason = _signers_for(
        session, inquiry=inquiry, customer_name=customer_name, customer_email=customer_email
    )
    moment = now_ms()
    envelope = ContractEnvelope(
        id=new_id("envelope"),
        quote_id=quote.id,
        inquiry_id=inquiry.id if inquiry else None,
        order_id=order_id,
        project_id=project_id,
        user_id=user_id,
        provider=provider_name() or "disabled",
        status=ENVELOPE_PENDING_SEND,
        subject=f"委托合同 · 报价版本 {quote.quote_version}",
        snapshot_digest=snapshot_digest(quote.contract_snapshot),
        signers=[
            {
                "role": item.role,
                "name": item.name,
                "email": item.email,
                "relation": item.relation,
                "status": SIGNER_PENDING,
                "signed_at": None,
            }
            for item in signers
        ],
        guardian_required=1 if guardian_required else 0,
        attempts=0,
        failure_reason=blocked_reason,
    )
    session.add(envelope)
    session.flush()
    audit.record(
        session,
        actor_type="customer",
        actor_id=user_id,
        action="contract.envelope_created",
        resource_type="contract_envelope",
        resource_id=envelope.id,
        after={"quote_id": quote.id, "snapshot_digest": envelope.snapshot_digest},
    )
    _ = moment
    return envelope


def send_contract(
    session: Session, *, envelope: ContractEnvelope, return_url: str | None = None, actor_id: str | None = None
) -> ContractEnvelope:
    """把签署任务交给电子签渠道并发邮件通知；失败时如实记录原因。

    - 渠道未接入（`disabled`）：任务停在 `PENDING_SEND`，`failure_reason` 写明原因，
      **不生成假链接**；
    - 渠道未配置凭据/网络失败：同样是 `PENDING_SEND` + 原因，可由运营重发
      （重发复用同一任务的签署人，不会产生第二份合同）。
    """
    if envelope.status in ENVELOPE_TERMINAL:
        return envelope
    if envelope.status != ENVELOPE_PENDING_SEND:
        # 已发送/部分签署：重发不会产生第二份合同，也不会重置已完成的签署进度。
        return envelope
    gateway = get_esign_gateway()
    envelope.attempts += 1
    envelope.provider = gateway.name if not isinstance(gateway, DisabledEsignGateway) else "disabled"
    if isinstance(gateway, DisabledEsignGateway):
        envelope.failure_reason = "电子签渠道未接入：合同未发送（C07 要求全部电子签）"
        envelope.updated_at = now_ms()
        session.flush()
        return envelope
    if envelope.failure_reason and "监护人" in envelope.failure_reason:
        # 签署人信息不全：先让客户补齐监护信息，不要发出无法完成的签署任务。
        envelope.updated_at = now_ms()
        session.flush()
        _notify_esign_failure(session, envelope=envelope)
        return envelope
    signers = [
        SignerInput(
            role=str(item.get("role")),
            name=str(item.get("name")),
            email=str(item.get("email")),
            relation=str(item.get("relation") or ""),
        )
        for item in (envelope.signers or [])
    ]
    try:
        created = gateway.create_envelope(
            document_no=envelope.id,
            subject=envelope.subject,
            snapshot_digest=envelope.snapshot_digest,
            signers=signers,
            return_url=return_url,
        )
    except (GatewayError, GatewayNotConfigured) as exc:
        envelope.failure_reason = f"电子签渠道调用失败：{type(exc).__name__}"
        envelope.updated_at = now_ms()
        session.flush()
        _notify_esign_failure(session, envelope=envelope)
        return envelope

    moment = now_ms()
    envelope.provider = created.provider
    envelope.provider_document_id = created.provider_document_id
    envelope.status = created.status if created.status in ("SENT", "PENDING_SEND") else ENVELOPE_SENT
    envelope.sign_url = created.sign_url
    envelope.expires_at = created.expires_at
    envelope.sent_at = moment
    envelope.failure_reason = None
    envelope.signers = [
        {
            "role": item.get("role"),
            "name": item.get("name"),
            "email": item.get("email"),
            "relation": item.get("relation") or "",
            "status": SIGNER_PENDING,
            "signed_at": None,
        }
        for item in (created.signers or envelope.signers or [])
    ]
    envelope.updated_at = moment
    session.flush()
    audit.record(
        session,
        actor_type="staff" if actor_id else "system",
        actor_id=actor_id,
        action="contract.envelope_sent",
        resource_type="contract_envelope",
        resource_id=envelope.id,
        after={"provider": envelope.provider, "provider_document_id": envelope.provider_document_id},
    )
    _notify_signing_link(session, envelope=envelope)
    return envelope


def refresh_envelope(session: Session, *, envelope: ContractEnvelope) -> ContractEnvelope:
    """服务端查单：与支付同一口径，签署状态只认平台（查单是第二种可信来源）。"""
    if not envelope.provider_document_id:
        return envelope
    gateway = get_esign_gateway()
    if isinstance(gateway, DisabledEsignGateway):
        return envelope
    try:
        state = gateway.query_envelope(
            provider_document_id=envelope.provider_document_id, document_no=envelope.id
        )
    except (GatewayError, GatewayNotConfigured):
        return envelope
    _apply_state(session, envelope=envelope, state=state, source="query", event_id=None)
    return envelope


def persist_esign_event(raw_body: bytes) -> tuple[str, str]:
    """回调原文落盘留证：库里只存摘要，原文按摘要命名保存在私密目录下。"""
    from app.platform.config import get_settings

    digest = payload_digest(raw_body)
    relative = f"esign_events/{digest}.payload"
    target = get_settings().storage_root / relative
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw_body)
    return digest, relative


def apply_esign_notification(
    session: Session,
    *,
    headers: dict[str, str],
    raw_body: bytes,
    provider: str,
    actor_id: str | None = None,
) -> dict[str, Any]:
    """处理一次电子签回调：验签 → 找签署任务 → 落状态（至少一次投递、效果一次）。

    返回 `{"accepted", "status", "reason", "envelope_id"}`；**未验签通过绝不改状态**。
    """
    gateway = get_esign_gateway()
    if isinstance(gateway, DisabledEsignGateway) or gateway.name != (provider or "").strip().lower():
        return {
            "accepted": False,
            "status": "PROVIDER_DISABLED",
            "reason": "esign_provider_not_enabled",
            "envelope_id": None,
        }
    verdict: EsignVerdict = gateway.verify_notification(headers, raw_body)
    if not verdict.accepted:
        return {
            "accepted": False,
            "status": "REJECTED",
            "reason": verdict.reason,
            "envelope_id": None,
        }
    document_no = str((verdict.payload or {}).get("document_no") or "")
    envelope = session.get(ContractEnvelope, document_no)
    if envelope is None:
        return {
            "accepted": False,
            "status": "UNKNOWN_ENVELOPE",
            "reason": "envelope_not_found",
            "envelope_id": None,
        }
    # 重复投递同一事件：状态已经落过，不再重复写（效果一次）。
    digest = payload_digest(raw_body)
    if (
        envelope.last_event_digest == digest
        and verdict.state is not None
        and envelope.status == verdict.state.status
    ):
        return {
            "accepted": True,
            "status": "DUPLICATE",
            "reason": "already_applied",
            "envelope_id": envelope.id,
        }
    envelope.last_event_digest = digest
    assert verdict.state is not None
    _apply_state(session, envelope=envelope, state=verdict.state, source="notification", event_id=verdict.event_id)
    return {
        "accepted": True,
        "status": envelope.status,
        "reason": "applied",
        "envelope_id": envelope.id,
    }


def _apply_state(
    session: Session,
    *,
    envelope: ContractEnvelope,
    state: EnvelopeState,
    source: str,
    event_id: str | None,
) -> None:
    target = state.status
    if target == "NOT_FOUND" or target == envelope.status:
        return
    allowed = ALLOWED_ENVELOPE_TRANSITIONS.get(envelope.status, ())
    if target not in allowed:
        # 乱序或伪造的状态跳变：不改状态，只留痕（由运营核对）。
        audit.record(
            session,
            actor_type="system",
            actor_id=None,
            action="contract.envelope_transition_rejected",
            resource_type="contract_envelope",
            resource_id=envelope.id,
            reason=f"{envelope.status}→{target}（来源 {source}）",
        )
        return
    moment = now_ms()
    envelope.status = target
    envelope.updated_at = moment
    if state.signers:
        envelope.signers = [dict(item) for item in state.signers]
    if target == ENVELOPE_SIGNED:
        envelope.completed_at = state.completed_at or moment
        envelope.document_sha256 = state.signed_digest
        _store_signed_document(session, envelope=envelope)
        _record_signature_consent(session, envelope=envelope)
    if target == ENVELOPE_DECLINED:
        envelope.decline_reason = state.decline_reason or "签署被拒绝"
    session.flush()
    audit.record(
        session,
        actor_type="system",
        actor_id=None,
        action="contract.envelope_status_changed",
        resource_type="contract_envelope",
        resource_id=envelope.id,
        after={"status": target, "source": source, "event_id": event_id},
    )
    _notify_status_change(session, envelope=envelope, source=source)


def _record_signature_consent(session: Session, *, envelope: ContractEnvelope) -> None:
    """签署完成写一条 `consents` 留证：谁、什么时间、通过哪个平台签了哪一版。"""
    from app.modules.identity import ports as identity_ports

    identity_ports.record_consent(
        session,
        user_id=envelope.user_id,
        purpose="contract_signature",
        version=f"quote-{envelope.quote_id}",
        resource_type="contract_envelope",
        resource_id=envelope.id,
        evidence={
            "provider": envelope.provider,
            "provider_document_id": envelope.provider_document_id,
            "snapshot_digest": envelope.snapshot_digest,
            "document_sha256": envelope.document_sha256,
            "signers": envelope.signers or [],
            "guardian_required": bool(envelope.guardian_required),
            "completed_at": iso(envelope.completed_at),
        },
    )


def _store_signed_document(session: Session, *, envelope: ContractEnvelope) -> None:
    """取回签署文件并留证（相对路径 + 摘要）；取不到就如实记录失败，不伪造摘要。"""
    gateway = get_esign_gateway()
    if isinstance(gateway, DisabledEsignGateway) or not envelope.provider_document_id:
        return
    from app.platform.config import get_settings

    try:
        content = gateway.download_signed(provider_document_id=envelope.provider_document_id)
    except (GatewayError, GatewayNotConfigured) as exc:
        envelope.failure_reason = f"签署文件下载失败：{type(exc).__name__}"
        return
    digest = payload_digest(content)
    relative = f"contracts/{envelope.id}.json"
    target = get_settings().storage_root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    envelope.document_path = relative
    envelope.document_sha256 = digest
    session.flush()


def assert_contract_signed(session: Session, *, project_id: str) -> None:
    """制作开工前的合同门禁（C07/C19）。

    - 有签署任务且已签 → 通过；
    - 有签署任务未签（含渠道未接入）→ 409，说明缺什么；
    - **没有签署任务**：电子签渠道未接入时不阻塞既有流程，但门禁结论必须如实
      暴露在 `contract_status_for_project` 里（`NOT_REQUESTED` + 原因），
      生产配置校验会拒绝"未接入电子签"上线，因此这里不需要伪造已签。
    """
    envelope = envelope_for_project(session, project_id=project_id)
    if envelope is None:
        return
    if envelope.status == ENVELOPE_SIGNED:
        return
    if envelope.status in (ENVELOPE_DECLINED, ENVELOPE_EXPIRED, ENVELOPE_VOID):
        raise AppError(
            "STATE_CONFLICT",
            f"合同未能签署（当前状态 {envelope.status}），请联系客户重新签署后再开工。",
        )
    awaiting = [
        item.get("role")
        for item in (envelope.signers or [])
        if (item.get("status") or SIGNER_PENDING) == SIGNER_PENDING
    ]
    raise AppError(
        "STATE_CONFLICT",
        "合同尚未签署完成，不能进入制作。"
        + (f"待签署人：{'、'.join(str(role) for role in awaiting)}。" if awaiting else ""),
    )


def void_envelope(
    session: Session, *, envelope: ContractEnvelope, reason: str, actor_id: str | None = None
) -> ContractEnvelope:
    """作废签署任务（报价撤回/客户取消）。已签合同不可作废（留证不可篡改）。"""
    if envelope.status == ENVELOPE_SIGNED:
        raise AppError("STATE_CONFLICT", "已签署的合同不能作废；如需解除请走售后或人工流程。")
    if envelope.provider_document_id:
        gateway = get_esign_gateway()
        if not isinstance(gateway, DisabledEsignGateway):
            try:
                gateway.void_envelope(provider_document_id=envelope.provider_document_id, reason=reason)
            except (GatewayError, GatewayNotConfigured):
                # 渠道侧取消失败不阻塞本地作废，但如实记录（避免"以为撤销了"）。
                envelope.failure_reason = "渠道侧作废失败，请人工确认"
    envelope.status = ENVELOPE_VOID
    envelope.void_reason = reason[:300]
    envelope.updated_at = now_ms()
    session.flush()
    audit.record(
        session,
        actor_type="staff" if actor_id else "system",
        actor_id=actor_id,
        action="contract.envelope_voided",
        resource_type="contract_envelope",
        resource_id=envelope.id,
        reason=reason[:200],
    )
    return envelope


# ------------------------------------------------------------------ 通知
def _notify_signing_link(session: Session, *, envelope: ContractEnvelope) -> None:
    """把签署链接发给签署人（C07：网站内通过邮件发送合同链接）。"""
    from app.modules.notifications.ports import NotificationRequest
    from app.modules.notifications.ports import notify as enqueue_notification
    from app.platform.config import get_settings

    ttl_hours = max(1, int(get_settings().esign_link_ttl_hours))
    for item in envelope.signers or []:
        email = str(item.get("email") or "").strip()
        if not email:
            continue
        role = str(item.get("role") or "")
        enqueue_notification(
            session,
            NotificationRequest(
                event_id=f"esign-link-{envelope.id}-{role}",
                event_type="contract.signature_requested",
                user_id=envelope.user_id if role == SIGNER_CUSTOMER else None,
                title="委托合同待签署",
                body=(
                    f"合同「{envelope.subject}」已发送给 {item.get('name') or email} 签署，"
                    f"链接 {ttl_hours} 小时内有效。"
                ),
                link_path="/account/quotes",
                category="transaction",
                channels=("email",),
                email_target=email,
                payload={
                    "envelope_id": envelope.id,
                    "quote_id": envelope.quote_id,
                    "role": role,
                    # 签署链接由渠道签发；没有链接时通知里不编造。
                    "sign_url": item.get("sign_url") or envelope.sign_url,
                },
            ),
        )


def _notify_status_change(session: Session, *, envelope: ContractEnvelope, source: str) -> None:
    from app.modules.notifications.ports import NotificationRequest
    from app.modules.notifications.ports import notify as enqueue_notification

    titles = {
        ENVELOPE_SIGNED: "合同已签署完成",
        ENVELOPE_DECLINED: "合同被拒签",
        ENVELOPE_EXPIRED: "合同签署已过期",
    }
    title = titles.get(envelope.status)
    if title is None:
        if envelope.status == ENVELOPE_PARTIALLY_SIGNED:
            title = "合同部分签署完成"
        else:
            return
    enqueue_notification(
        session,
        NotificationRequest(
            event_id=f"esign-status-{envelope.id}-{envelope.status}",
            event_type="contract.status_changed",
            user_id=envelope.user_id,
            title=title,
            body=f"合同「{envelope.subject}」当前状态：{envelope.status}。",
            link_path="/account/quotes",
            category="transaction",
            channels=("email",),
            payload={"envelope_id": envelope.id, "status": envelope.status, "source": source},
        ),
    )


def _notify_esign_failure(session: Session, *, envelope: ContractEnvelope) -> None:
    """渠道不可用时提醒运营（客户看到的是"合同待发送"，不该让运营也蒙在鼓里）。"""
    from app.modules.notifications.ports import NotificationRequest
    from app.modules.notifications.ports import notify as enqueue_notification

    enqueue_notification(
        session,
        NotificationRequest(
            event_id=f"esign-failed-{envelope.id}-{envelope.attempts}",
            event_type="contract.send_failed",
            user_id=None,
            title="合同签署发送失败",
            body=f"合同 {envelope.id}（报价 {envelope.quote_id}）发送失败：{envelope.failure_reason}",
            link_path="/admin/quotes",
            category="support",
            channels=(),
            payload={"envelope_id": envelope.id, "quote_id": envelope.quote_id},
        ),
    )


__all__ = [
    "ALLOWED_ENVELOPE_TRANSITIONS",
    "active_envelope",
    "apply_esign_notification",
    "assert_contract_signed",
    "contract_status_for_project",
    "create_envelope",
    "envelope_for_project",
    "envelope_payload",
    "get_envelope_for_user",
    "latest_envelope",
    "latest_envelope_for_inquiry",
    "persist_esign_event",
    "refresh_envelope",
    "send_contract",
    "void_envelope",
]
