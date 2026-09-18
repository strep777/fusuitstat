"""合同电子签生命周期回归（R18 / C07、C19）。

依据：
- `功能确认清单-已确认.md` C07：合同**全部电子签**，网站内通过**邮件发送合同链接**完成签署；
- C19：未成年付费委托需要监护人同意与**额外签署**；
- `文档/07` 与 `09`：条款版本与同意时间留证；布尔勾选不等于可靠电子签名；
- `文档/08` 的口径同样适用于签署：**成功只认已验签回调或服务端查单**。

本轮交付的边界（诚实声明）：

1. **可在本地验证**：签署任务的登记与状态机、签署链接与邮件通知、回调验签（签名/时间戳/
   重放/伪造）、监护人作为第二个签署人且**任一未签都不算已签**、合同快照摘要留证、
   制作开工门禁、渠道未接入时的如实呈现；
2. **BLOCKED（R30）**：真实电子签平台（法大大/DocuSign 等）的下单、状态回传与文件取回
   需要平台账号与官方文档；本文件不伪造这些路径，适配器对未接入的真实厂商 fail closed。
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.adapters.base import GatewayNotConfigured
from app.adapters.esign import (
    DisabledEsignGateway,
    get_esign_gateway,
    reset_esign_gateway,
    set_esign_gateway_override,
)
from app.adapters.esign.base import SIGNER_CUSTOMER, SIGNER_GUARDIAN
from app.adapters.esign.sandbox import SandboxEsignGateway
from app.modules.commissions import esign_service
from app.modules.commissions.models import ContractEnvelope
from app.platform.config import Settings
from app.platform.errors import AppError
from app.platform.ids import now_ms
from tests.integration.test_commission_flow import (
    BASE,
    accept_payload,
    login,
    make_asset,
    make_series,
    publish_quote,
)

ESIGN_SECRET = "esign-test-secret-0123456789"


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "_env_file": None,
        "app_env": "test",
        "public_origin": "http://127.0.0.1:5173",
        "esign_provider": "sandbox",
        "esign_webhook_secret": ESIGN_SECRET,
        "esign_link_ttl_hours": 48,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.fixture()
def esign_sandbox(monkeypatch: pytest.MonkeyPatch) -> SandboxEsignGateway:
    """把电子签渠道换成沙箱实现（签名与状态机同形，但完全本地）。"""
    gateway = SandboxEsignGateway(_settings())
    set_esign_gateway_override(gateway)
    try:
        yield gateway
    finally:
        set_esign_gateway_override(None)
        reset_esign_gateway()


def _accepted_quote(db: Any, client: Any, *, guardian: bool = False) -> tuple[Any, ContractEnvelope]:
    """走真实链路：询价 → 提交 → 发布报价 → 接受（接受时登记并发送签署任务）。"""
    actor = login(db)
    series_id = make_series(db)
    asset_id = make_asset(db, actor.user_id)
    overrides: dict[str, Any] = {}
    if guardian:
        overrides = {
            "guardian_required": True,
            "guardian_name": "监护人甲",
            "guardian_email": "guardian@example.test",
            "guardian_relation": "母亲",
        }
    created = client.post(
        f"{BASE}/inquiries",
        json={
            "character_name": "星轨测试兽",
            "species": "狼",
            "series_id": series_id,
            "suit_type": "full",
            "design_asset_ids": [asset_id],
            "requirements": "需要可拆卸尾巴与发光眼。",
            "rights_declaration": True,
            **overrides,
        },
        headers=actor.headers(),
    )
    assert created.status_code == 201, created.text
    inquiry_id = created.json()["id"]
    assert (
        client.post(f"{BASE}/inquiries/{inquiry_id}/submit", headers=actor.headers()).status_code
        == 200
    )
    if guardian:
        # C19：未成年委托在接受前必须有监护人同意记录（先同意，再接受、再签署）。
        consented = client.post(
            f"{BASE}/me/consents",
            json={"purpose": "miner_guardian", "document_version": "guardian-1", "accepted": True},
            headers=actor.headers(),
        )
        if consented.status_code == 404:
            from app.modules.identity.service import record_consent

            record_consent(db, user_id=actor.user_id, purpose="miner_guardian", version="guardian-1")
            db.commit()
        else:
            assert consented.status_code == 201, consented.text
    quote = publish_quote(db, inquiry_id=inquiry_id, total_cents=500000)
    accepted = client.post(
        f"{BASE}/quotes/{quote.id}/accept",
        json=accept_payload(quote),
        headers=actor.headers(**{"Idempotency-Key": f"esign-{inquiry_id}"}),
    )
    assert accepted.status_code == 201, accepted.text
    envelope = esign_service.latest_envelope(db, quote_id=quote.id)
    assert envelope is not None, "接受报价后必须登记合同签署任务"
    return actor, envelope


# ------------------------------------------------------------------ 适配器层
def test_sandbox_gateway_signs_and_verifies(esign_sandbox: SandboxEsignGateway) -> None:
    from app.adapters.esign.base import SIGNER_CUSTOMER as CUSTOMER
    from app.adapters.esign.base import SignerInput

    created = esign_sandbox.create_envelope(
        document_no="env-1",
        subject="委托合同",
        snapshot_digest="d" * 64,
        signers=[SignerInput(role=CUSTOMER, name="客户甲", email="a@example.test")],
        return_url=None,
    )
    assert created.status == "SENT" and created.sign_url
    assert esign_sandbox.verify_link("env-1", CUSTOMER, esign_sandbox.link_signature("env-1", CUSTOMER))
    # 篡改链接签名 → 校验失败
    assert not esign_sandbox.verify_link("env-1", CUSTOMER, "deadbeef")

    headers, body = esign_sandbox.sign_notification(document_no="env-1", signed_roles=[CUSTOMER])
    verdict = esign_sandbox.verify_notification(headers, body)
    assert verdict.accepted and verdict.state is not None
    assert verdict.state.status == "SIGNED" and verdict.state.signed_digest


def test_sandbox_gateway_rejects_bad_signatures_and_unknown_documents(
    esign_sandbox: SandboxEsignGateway,
) -> None:
    from app.adapters.esign.base import SIGNER_CUSTOMER as CUSTOMER
    from app.adapters.esign.base import SignerInput

    esign_sandbox.create_envelope(
        document_no="env-2",
        subject="委托合同",
        snapshot_digest="e" * 64,
        signers=[SignerInput(role=CUSTOMER, name="客户乙", email="b@example.test")],
        return_url=None,
    )
    headers, body = esign_sandbox.sign_notification(document_no="env-2", signed_roles=[CUSTOMER])
    # 篡改报文 → 签名不符
    assert esign_sandbox.verify_notification(headers, body + b" ").reason == "signature_mismatch"
    # 缺头 → 拒绝
    assert esign_sandbox.verify_notification({}, body).reason == "signature_missing"
    # 过期时间戳（重放）→ 拒绝
    stale = int(now_ms() // 1000) - 3600
    signature = hmac.new(ESIGN_SECRET.encode(), f"{stale}.".encode() + body, hashlib.sha256).hexdigest()
    verdict = esign_sandbox.verify_notification(
        {"X-Esign-Timestamp": str(stale), "X-Esign-Signature": signature}, body
    )
    assert verdict.reason == "timestamp_out_of_window"
    # 未发起过的签署任务（签名正确也不行）→ 拒绝
    other_headers, other_body = esign_sandbox.sign_notification(
        document_no="env-unknown", signed_roles=[CUSTOMER]
    )
    assert esign_sandbox.verify_notification(other_headers, other_body).reason == "unknown_document"


def test_signed_file_only_available_after_completion(esign_sandbox: SandboxEsignGateway) -> None:
    from app.adapters.base import GatewayError
    from app.adapters.esign.base import SIGNER_CUSTOMER as CUSTOMER
    from app.adapters.esign.base import SignerInput

    created = esign_sandbox.create_envelope(
        document_no="env-3",
        subject="委托合同",
        snapshot_digest="f" * 64,
        signers=[SignerInput(role=CUSTOMER, name="客户丙", email="c@example.test")],
        return_url=None,
    )
    with pytest.raises(GatewayError):
        esign_sandbox.download_signed(provider_document_id=created.provider_document_id)
    esign_sandbox.mark_signed_locally(document_no="env-3", role=CUSTOMER)
    content = esign_sandbox.download_signed(provider_document_id=created.provider_document_id)
    assert json.loads(content.decode())["document_no"] == "env-3"


def test_disabled_provider_fails_closed_and_reports_reason() -> None:
    reset_esign_gateway()
    gateway = DisabledEsignGateway()
    assert isinstance(gateway, DisabledEsignGateway)
    with pytest.raises(GatewayNotConfigured):
        gateway.create_envelope(
            document_no="x", subject="s", snapshot_digest="d", signers=[], return_url=None
        )
    verdict = gateway.verify_notification({}, b"{}")
    assert verdict.accepted is False and verdict.reason == "esign_disabled"
    _ = get_esign_gateway()  # 默认配置下工厂返回未接入实现，不会抛错
    reset_esign_gateway()


def test_production_requires_a_real_esign_provider() -> None:
    """C07：合同全部电子签——生产环境不允许"未接入"或沙箱渠道上线。"""
    base: dict[str, Any] = {
        "_env_file": None,
        "app_env": "production",
        "public_origin": "https://starium.cn",
        "session_secret": "x" * 40,
        "csrf_secret": "y" * 40,
        "otp_secret": "z" * 40,
        "phone_lookup_key": "p" * 40,
        "pii_encryption_key": "q" * 40,
        "cookie_secure": True,
        "sms_adapter": "aliyun",
        "aliyun_sms_access_key_id": "ak",
        "aliyun_sms_access_key_secret": "sk",
        "sms_template_code": "SMS_1",
        "storage_backend": "s3",
        "smtp_host": "mail.test",
        "payment_providers": "wechat",
        "wechat_merchant_id": "1900000001",
        "wechat_api_v3_key": "k" * 32,
    }
    with pytest.raises(ValueError) as error:
        Settings(**base)  # type: ignore[arg-type]
    assert "ESIGN_PROVIDER" in str(error.value)

    # 沙箱渠道同样不允许
    base["esign_provider"] = "sandbox"
    with pytest.raises(ValueError) as error:
        Settings(**base)  # type: ignore[arg-type]
    assert "沙箱" in str(error.value) or "ESIGN_PROVIDER" in str(error.value)

    # 真实厂商但缺凭据 → 仍然拒绝
    base["esign_provider"] = "fadada"
    with pytest.raises(ValueError) as error:
        Settings(**base)  # type: ignore[arg-type]
    assert "ESIGN_APP_ID" in str(error.value)

    # 凭据齐全 → 放行
    base.update(
        {
            "esign_app_id": "app-1",
            "esign_app_secret": "secret-1",
            "esign_webhook_secret": "wh-1",
            "esign_api_base": "https://openapi.example-esign.com",
        }
    )
    settings = Settings(**base)  # type: ignore[arg-type]
    assert settings.esign_provider == "fadada"


# ------------------------------------------------------------------ 服务层：登记与状态机
def test_envelope_is_created_and_reports_snapshot_digest(
    db: Session, client: Any, esign_sandbox: SandboxEsignGateway
) -> None:
    _actor, envelope = _accepted_quote(db, client)
    assert envelope.status in ("SENT", "PARTIALLY_SIGNED")
    assert envelope.snapshot_digest
    assert envelope.provider == "sandbox" and envelope.provider_document_id
    payload = esign_service.envelope_payload(db, envelope)
    assert payload["awaiting_roles"] == [SIGNER_CUSTOMER]
    assert payload["provider_configured"] is True
    # 合同状态随制作单一起返回
    status = esign_service.contract_status_for_project(db, project_id=envelope.project_id or "")
    assert status["status"] == envelope.status and status["signed"] is False


def test_guardian_signature_is_required_for_minor_commissions(
    db: Session, client: Any, esign_sandbox: SandboxEsignGateway
) -> None:
    """C19：监护人必须作为第二个签署人；只有客户签完不算已签。"""
    _actor, envelope = _accepted_quote(db, client, guardian=True)
    assert envelope.guardian_required == 1
    roles = {item["role"] for item in (envelope.signers or [])}
    assert roles == {SIGNER_CUSTOMER, SIGNER_GUARDIAN}

    # 客户先签：状态为部分签署，未完成
    headers, body = esign_sandbox.sign_notification(
        document_no=envelope.id, signed_roles=[SIGNER_CUSTOMER]
    )
    result = esign_service.apply_esign_notification(
        db, headers=headers, raw_body=body, provider="sandbox"
    )
    db.commit()
    assert result["accepted"] and result["status"] == "PARTIALLY_SIGNED"
    db.refresh(envelope)
    assert envelope.status == "PARTIALLY_SIGNED"

    # 监护人签完：才变成已签署，并写入留证
    headers, body = esign_sandbox.sign_notification(
        document_no=envelope.id, signed_roles=[SIGNER_CUSTOMER, SIGNER_GUARDIAN]
    )
    result = esign_service.apply_esign_notification(
        db, headers=headers, raw_body=body, provider="sandbox"
    )
    db.commit()
    assert result["status"] == "SIGNED"
    db.refresh(envelope)
    assert envelope.completed_at is not None and envelope.document_sha256
    from app.modules.identity.models import Consent

    consent = db.scalars(
        sa.select(Consent).where(Consent.purpose == "contract_signature")
    ).first()
    assert consent is not None
    evidence = consent.evidence or {}
    assert evidence["guardian_required"] is True
    assert evidence["snapshot_digest"] == envelope.snapshot_digest


def test_tampered_callback_does_not_change_status(
    db: Session, client: Any, esign_sandbox: SandboxEsignGateway
) -> None:
    _actor, envelope = _accepted_quote(db, client)
    assert envelope.status == "SENT"
    headers, body = esign_sandbox.sign_notification(
        document_no=envelope.id, signed_roles=[SIGNER_CUSTOMER]
    )
    forged = body.replace(b'"SIGNED"', b'"PENDING"')
    result = esign_service.apply_esign_notification(
        db, headers=headers, raw_body=forged, provider="sandbox"
    )
    db.commit()
    assert result["accepted"] is False and result["reason"] == "signature_mismatch"
    db.refresh(envelope)
    assert envelope.status == "SENT", "未验签通过的回调不得改变签署状态"


def test_duplicate_callback_is_idempotent(
    db: Session, client: Any, esign_sandbox: SandboxEsignGateway
) -> None:
    _actor, envelope = _accepted_quote(db, client)
    headers, body = esign_sandbox.sign_notification(
        document_no=envelope.id, signed_roles=[SIGNER_CUSTOMER]
    )
    first = esign_service.apply_esign_notification(
        db, headers=headers, raw_body=body, provider="sandbox"
    )
    db.commit()
    second = esign_service.apply_esign_notification(
        db, headers=headers, raw_body=body, provider="sandbox"
    )
    db.commit()
    assert first["status"] == "SIGNED"
    # 同一事件重复投递：效果一次（状态不变，标记重复）
    assert second["status"] == "DUPLICATE" and second["accepted"] is True


def test_production_gate_blocks_unsigned_contract(
    db: Session, client: Any, esign_sandbox: SandboxEsignGateway
) -> None:
    """C07：合同未签署完成不能进入制作；签完之后放行。"""
    from app.modules.commissions import service as commission_service

    _actor, envelope = _accepted_quote(db, client)
    assert envelope.project_id
    project_id = envelope.project_id

    with pytest.raises(AppError) as error:
        commission_service.advance_project_stage(
            db, project_id=project_id, to_status="MEASUREMENT_PENDING", actor_id=None
        )
    assert error.value.code == "STATE_CONFLICT"
    assert "合同尚未签署完成" in str(error.value)

    headers, body = esign_sandbox.sign_notification(
        document_no=envelope.id, signed_roles=[SIGNER_CUSTOMER]
    )
    esign_service.apply_esign_notification(db, headers=headers, raw_body=body, provider="sandbox")
    db.commit()
    project = commission_service.advance_project_stage(
        db, project_id=project_id, to_status="MEASUREMENT_PENDING", actor_id=None
    )
    db.commit()
    assert project.status == "MEASUREMENT_PENDING"


def test_declined_contract_blocks_production_with_reason(
    db: Session, client: Any, esign_sandbox: SandboxEsignGateway
) -> None:
    from app.modules.commissions import service as commission_service

    _actor, envelope = _accepted_quote(db, client)
    assert envelope.project_id
    headers, body = esign_sandbox.sign_notification(
        document_no=envelope.id, signed_roles=[], declined_roles=[SIGNER_CUSTOMER]
    )
    result = esign_service.apply_esign_notification(
        db, headers=headers, raw_body=body, provider="sandbox"
    )
    db.commit()
    assert result["status"] == "DECLINED"
    with pytest.raises(AppError) as error:
        commission_service.advance_project_stage(
            db, project_id=envelope.project_id, to_status="MEASUREMENT_PENDING", actor_id=None
        )
    assert "重新签署" in str(error.value)


def test_disabled_provider_registers_task_without_fake_link(
    db: Session, client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """未接入电子签渠道：任务留痕、原因为「渠道未接入」，且**不产生任何签署链接**。"""
    set_esign_gateway_override(DisabledEsignGateway())
    try:
        _actor, envelope = _accepted_quote(db, client)
        assert envelope.status == "PENDING_SEND"
        assert envelope.provider_document_id is None and envelope.sign_url is None
        assert envelope.failure_reason and "未接入" in envelope.failure_reason
        payload = esign_service.envelope_payload(db, envelope)
        assert payload["provider_configured"] is False
        assert payload["esign_provider"] == "disabled"
    finally:
        set_esign_gateway_override(None)


def test_void_rejects_signed_contract(
    db: Session, client: Any, esign_sandbox: SandboxEsignGateway
) -> None:
    """未签署的合同可以作废（重复作废幂等）；**已签署的合同不可作废**（留证不可篡改）。"""
    _actor, envelope = _accepted_quote(db, client)
    esign_service.void_envelope(db, envelope=envelope, reason="客户取消委托")
    db.commit()
    assert envelope.status == "VOID"
    # 重复作废幂等：不改变已作废状态，也不掩盖原因
    esign_service.void_envelope(db, envelope=envelope, reason="客户取消委托（重复请求）")
    db.commit()
    assert envelope.status == "VOID"

    # 另一份合同签完之后不允许作废
    _actor2, signed_envelope = _accepted_quote(db, client)
    headers, body = esign_sandbox.sign_notification(
        document_no=signed_envelope.id, signed_roles=[SIGNER_CUSTOMER]
    )
    esign_service.apply_esign_notification(db, headers=headers, raw_body=body, provider="sandbox")
    db.commit()
    db.refresh(signed_envelope)
    assert signed_envelope.status == "SIGNED"
    with pytest.raises(AppError) as error:
        esign_service.void_envelope(db, envelope=signed_envelope, reason="想撤销")
    assert "已签署的合同不能作废" in str(error.value)


def test_esign_event_payload_is_persisted_for_evidence(
    db: Session, client: Any, esign_sandbox: SandboxEsignGateway
) -> None:
    from app.platform.config import get_settings

    _actor, envelope = _accepted_quote(db, client)
    headers, body = esign_sandbox.sign_notification(
        document_no=envelope.id, signed_roles=[SIGNER_CUSTOMER]
    )
    digest, relative = esign_service.persist_esign_event(body)
    assert digest == hashlib.sha256(body).hexdigest()
    stored = get_settings().storage_root / relative
    assert stored.is_file() and stored.read_bytes() == body
    _ = headers
