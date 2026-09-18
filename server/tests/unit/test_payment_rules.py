"""支付模块领域规则单元测试。

覆盖：渠道适配器的安全边界（验签/证书序列/时间戳/解密、未配置凭据 fail closed、
沙箱绝不伪造成功）、金额分配与隔离口径、可退额度的条件 UPDATE 语义（TC-P09）、
实收汇总只计 APPLIED 分配。HTTP 全流程见 ``tests/integration/test_payment_flow.py``。
"""

from __future__ import annotations

import hashlib
import sys

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.adapters.base import GatewayNotConfigured
from app.adapters.payments import SandboxPaymentGateway, get_gateway
from app.adapters.payments.base import business_event_key
from app.adapters.payments.sandbox import REPLAY_WINDOW_MS
from app.modules.contracts import VerifiedPayment
from app.modules.payments import repository as repo
from app.modules.payments import repository_bridge as bridge
from app.modules.payments import service as svc
from app.modules.payments.models import PaymentAllocation
from app.platform import audit
from app.platform.errors import AppError

USER_ID = "usr_payment-unit-test"


# ------------------------------------------------------------------ 工具
def _make_intent(db: Session, commerce, *, amounts: list[int], provider: str = "sandbox"):
    """用商务替身建单并发起一次支付尝试（沙箱渠道）。"""
    _order, installments = commerce.create_order(user_id=USER_ID, amounts=amounts)
    intent = svc.create_intent(
        db,
        installment_id=installments[0].id,
        provider=provider,
        scene="native",
        actor_id=USER_ID,
    )
    return installments[0], intent


def _verified(intent, *, amount_cents: int, currency: str = "CNY", event_id: str | None = None,
              transaction_id: str = "TX-UNIT-1", account_ref: str | None = None) -> VerifiedPayment:
    return VerifiedPayment(
        provider=intent.provider,
        provider_transaction_id=transaction_id,
        merchant_order_no=intent.merchant_order_no,
        amount_cents=amount_cents,
        currency=currency,
        paid_at=svc.now_ms(),
        raw_digest=hashlib.sha256(f"{intent.merchant_order_no}:{transaction_id}".encode()).hexdigest(),
        event_id=event_id,
        account_ref=account_ref,
    )


# ------------------------------------------------------------------ 沙箱通道
def test_sandbox_create_returns_placeholder_action_and_no_success(sandbox_channel) -> None:
    """沙箱下单只给占位二维码；未收款前 query 绝不返回成功。"""
    gateway = sandbox_channel("sandbox")
    result = gateway.create("STSANDBOX001", 12800, "CNY", "native", "测试商品", None)
    assert result["status"] == "PENDING"
    assert result["action"]["type"] == "qr"
    assert "STSANDBOX001" in result["action"]["url"]
    assert result.get("sandbox") is True

    before = gateway.query("STSANDBOX001")
    assert before["status"] != "SUCCEEDED"

    gateway.mark_paid("STSANDBOX001", amount_cents=12800, transaction_id="SBXTXN-1")
    after = gateway.query("STSANDBOX001")
    assert after["status"] == "SUCCEEDED"
    assert after["amount_cents"] == 12800


def test_sandbox_verification_rejects_tamper_replay_and_missing_fields(sandbox_channel) -> None:
    """错签名、过期重放、缺字段都不通过验签；非成功通知不产生收款。"""
    gateway = sandbox_channel("sandbox")
    headers, raw = gateway.simulate_notification(merchant_order_no="STSANDBOX002", amount_cents=5000)
    payment = gateway.verify_notification(headers, raw)
    assert payment is not None
    assert payment.amount_cents == 5000
    assert payment.raw_digest == hashlib.sha256(raw).hexdigest()

    bad_headers, bad_raw = gateway.simulate_notification(
        merchant_order_no="STSANDBOX003", amount_cents=5000, tamper_signature=True
    )
    verdict = gateway.verify_notification_detailed(bad_headers, bad_raw)
    assert verdict.accepted is False
    assert verdict.reason == "signature_mismatch"

    stale_headers, stale_raw = gateway.simulate_notification(
        merchant_order_no="STSANDBOX004",
        amount_cents=5000,
        timestamp_ms=svc.now_ms() - REPLAY_WINDOW_MS * 2,
    )
    stale = gateway.verify_notification_detailed(stale_headers, stale_raw)
    assert stale.accepted is False
    assert stale.reason == "timestamp_out_of_window"

    missing = gateway.verify_notification_detailed({"content-type": "application/json"}, raw)
    assert missing.accepted is False
    assert missing.reason == "timestamp_missing"

    closed_headers, closed_raw = gateway.simulate_notification(
        merchant_order_no="STSANDBOX005", amount_cents=5000, status="CLOSED", mark_paid=False
    )
    closed = gateway.verify_notification_detailed(closed_headers, closed_raw)
    assert closed.accepted is True
    assert closed.payment is None
    assert closed.reason == "not_a_success_payment"


def test_sandbox_channel_is_disabled_outside_development_and_test(payment_settings) -> None:
    """沙箱通道只用于开发/测试：其他环境 fail closed。"""
    staging = payment_settings.model_copy(update={"app_env": "staging"})
    gateway = SandboxPaymentGateway(staging, provider="sandbox")
    with pytest.raises(GatewayNotConfigured):
        gateway.create("STSANDBOX006", 1000, "CNY", "native", "测试", None)


# ------------------------------------------------------------------ 工厂与真实通道
def test_factory_rejects_disabled_provider_and_real_channels_fail_closed(payment_settings) -> None:
    """未启用的渠道给稳定错误码；已启用但未配置凭据的真实渠道绝不降级到沙箱。"""
    with pytest.raises(AppError) as disabled:
        get_gateway("paypal")
    assert disabled.value.code == "PAYMENT_PROVIDER_DISABLED"

    wechat = get_gateway("wechat")
    assert not isinstance(wechat, SandboxPaymentGateway)
    with pytest.raises(GatewayNotConfigured):
        wechat.create("STWX001", 1000, "CNY", "native", "测试", None)

    alipay = get_gateway("alipay")
    assert not isinstance(alipay, SandboxPaymentGateway)
    with pytest.raises(GatewayNotConfigured):
        alipay.create("STALI001", 1000, "CNY", "native", "测试", None)


def test_wechat_verification_certificate_serial_timestamp_and_decrypt(wechat_channel) -> None:
    """真实微信 APIv3 通知：合法通过；未知证书序列、过期重放、解密失败、错签名全部拒绝。"""
    gateway = wechat_channel.gateway
    headers, raw = wechat_channel.notification(merchant_order_no="STWX002", amount_cents=25800)
    payment = gateway.verify_notification(headers, raw)
    assert payment is not None
    assert payment.provider == "wechat"
    assert payment.merchant_order_no == "STWX002"
    assert payment.amount_cents == 25800
    assert payment.account_ref == "mchid:1900000001|appid:wx-test-appid"
    assert payment.raw_digest == hashlib.sha256(raw).hexdigest()

    unknown_headers, unknown_raw = wechat_channel.notification(
        merchant_order_no="STWX003", amount_cents=100, serial="NOT-STORED-SERIAL"
    )
    unknown = gateway.verify_notification_detailed(unknown_headers, unknown_raw)
    assert unknown.accepted is False
    assert unknown.reason == "unknown_cert_serial"

    expired_headers, expired_raw = wechat_channel.notification(
        merchant_order_no="STWX004", amount_cents=100, timestamp=int(svc.now_ms() / 1000) - 3600
    )
    expired = gateway.verify_notification_detailed(expired_headers, expired_raw)
    assert expired.accepted is False
    assert expired.reason == "timestamp_out_of_window"

    wrong_key_headers, wrong_key_raw = wechat_channel.notification(
        merchant_order_no="STWX005", amount_cents=100, encrypt_key=b"x" * 32
    )
    wrong_key = gateway.verify_notification_detailed(wrong_key_headers, wrong_key_raw)
    assert wrong_key.accepted is False
    assert wrong_key.reason == "decrypt_failed"

    forged_headers, forged_raw = wechat_channel.notification(
        merchant_order_no="STWX006", amount_cents=100, sign_key=wechat_channel.other_private_key
    )
    forged = gateway.verify_notification_detailed(forged_headers, forged_raw)
    assert forged.accepted is False
    assert forged.reason == "signature_mismatch"

    unsigned_headers, unsigned_raw = wechat_channel.notification(
        merchant_order_no="STWX007", amount_cents=100, omit_signature=True
    )
    unsigned = gateway.verify_notification_detailed(unsigned_headers, unsigned_raw)
    assert unsigned.accepted is False
    assert unsigned.reason == "headers_incomplete"


def test_wechat_account_mismatch_is_flagged_for_service_layer(wechat_channel) -> None:
    """商户号不符：适配器保留真实收款并标出异常原因，由服务层隔离告警（不丢弃）。"""
    headers, raw = wechat_channel.notification(
        merchant_order_no="STWX008", amount_cents=100, mchid="9999999999"
    )
    verdict = wechat_channel.gateway.verify_notification_detailed(headers, raw)
    assert verdict.accepted is True
    assert verdict.reason == "verified_account_mismatch"
    assert verdict.payment is not None
    assert verdict.payment.account_ref == "mchid:9999999999|appid:wx-test-appid"


def test_alipay_verification_amount_and_replay_rules(alipay_channel) -> None:
    """真实支付宝异步通知：RSA2 验签 + 十进制金额精确转分 + 篡改与过期拒绝。"""
    gateway = alipay_channel.gateway
    raw = alipay_channel.notification(merchant_order_no="STALI002", amount_cents=12800)
    payment = gateway.verify_notification({}, raw)
    assert payment is not None
    assert payment.amount_cents == 12800
    assert payment.account_ref == "app_id:2021000000000000"

    tampered = alipay_channel.notification(
        merchant_order_no="STALI003", amount_cents=12800, tamper_amount_cents=1
    )
    verdict = gateway.verify_notification_detailed({}, tampered)
    assert verdict.accepted is False
    assert verdict.reason == "signature_mismatch"

    stale = alipay_channel.notification(
        merchant_order_no="STALI004", amount_cents=100, notify_time="2020-01-01 00:00:00"
    )
    expired = gateway.verify_notification_detailed({}, stale)
    assert expired.accepted is False
    assert expired.reason == "timestamp_out_of_window"

    closed = alipay_channel.notification(
        merchant_order_no="STALI005", amount_cents=100, trade_status="TRADE_CLOSED"
    )
    ignored = gateway.verify_notification_detailed({}, closed)
    assert ignored.accepted is True
    assert ignored.payment is None


# ------------------------------------------------------------------ 分配与隔离
def test_amount_mismatch_is_quarantined_without_crediting(
    payment_settings, payment_db: Session, commerce, sandbox_channel
) -> None:
    """金额不符：记隔离分配与安全事件，不给订单入账、不推进商务状态。"""
    sandbox_channel("sandbox")
    _installment, intent = _make_intent(payment_db, commerce, amounts=[10000])
    payment_db.commit()

    result = svc.apply_verified_payment(
        payment_db, _verified(intent, amount_cents=9900, transaction_id="TX-MISMATCH")
    )
    payment_db.commit()

    assert result["status"] == "QUARANTINED"
    assert result["applied_cents"] == 0
    assert commerce.paid_calls == []
    allocations = repo.list_allocations_for_payment(payment_db, intent.attempt_id)
    assert [row.classification for row in allocations] == ["QUARANTINED"]
    assert allocations[0].amount_cents == 9900
    attempt = repo.get_attempt(payment_db, intent.attempt_id)
    assert attempt is not None and attempt.status != "SUCCEEDED"
    events = repo.list_events_for_attempt(payment_db, intent.attempt_id)
    assert events and events[-1].processing_result == "QUARANTINED"
    assert events[-1].verified == 1
    assert audit.query(payment_db, action="payment.quarantined")


def test_account_and_currency_mismatch_are_quarantined(
    payment_settings, payment_db: Session, commerce, sandbox_channel
) -> None:
    """商户标识不符与币种不符同样隔离，不进入订单实收。"""
    sandbox_channel("sandbox")
    installment, intent = _make_intent(payment_db, commerce, amounts=[8000])
    payment_db.commit()

    currency = svc.apply_verified_payment(
        payment_db, _verified(intent, amount_cents=8000, currency="USD", transaction_id="TX-CUR")
    )
    assert currency["status"] == "QUARANTINED"
    assert currency["reason"] == "currency_mismatch"

    account = svc.apply_verified_payment(
        payment_db,
        _verified(
            intent, amount_cents=8000, transaction_id="TX-ACC", account_ref="sandbox:other.example"
        ),
    )
    assert account["status"] == "QUARANTINED"
    assert account["reason"] == "account_mismatch"
    assert repo.applied_sum(payment_db, installment_id=installment.id) == 0
    assert commerce.paid_calls == []


def test_duplicate_provider_transaction_is_quarantined(
    payment_settings, payment_db: Session, commerce, sandbox_channel
) -> None:
    """同一渠道交易号已挂在别的尝试上：第二笔必须隔离，不能重复入账。"""
    sandbox_channel("sandbox")
    _installment, first = _make_intent(payment_db, commerce, amounts=[5000])
    payment_db.commit()
    applied = svc.apply_verified_payment(
        payment_db, _verified(first, amount_cents=5000, transaction_id="TX-DUP")
    )
    assert applied["status"] == "APPLIED"

    # 另一个商户单号（另一个尝试）携带同一渠道交易号
    second_gateway = get_gateway("sandbox")
    second_gateway.create("STSANDBOX-DUP", 100, "CNY", "native", "x", None)
    second = repo.create_attempt(
        payment_db,
        order_id="ord_dup",
        installment_id="ist_dup",
        user_id=USER_ID,
        provider="sandbox",
        scene="native",
        merchant_order_no="STSANDBOX-DUP",
        expected_cents=100,
        currency="CNY",
        action_expires_at=None,
    )
    payment_db.commit()
    duplicate = svc.apply_verified_payment(
        payment_db,
        VerifiedPayment(
            provider="sandbox",
            provider_transaction_id="TX-DUP",
            merchant_order_no=second.merchant_order_no,
            amount_cents=100,
            currency="CNY",
            paid_at=svc.now_ms(),
            raw_digest=hashlib.sha256(b"dup").hexdigest(),
            event_id="dup-event-1",
        ),
    )
    assert duplicate["status"] == "QUARANTINED"
    assert duplicate["reason"] == "duplicate_provider_transaction"


def test_late_payment_on_closed_installment_becomes_overpayment(
    payment_settings, payment_db: Session, commerce, sandbox_channel
) -> None:
    """付款晚于关闭：全额记多付（OVERPAYMENT）且不核销库存/不推进订单。"""
    sandbox_channel("sandbox")
    installment, intent = _make_intent(payment_db, commerce, amounts=[6000])
    payment_db.commit()
    installment.status = "CLOSED"
    commerce.orders[installment.order_id].status = "CANCELLED"
    payment_db.commit()

    result = svc.apply_verified_payment(
        payment_db, _verified(intent, amount_cents=6000, transaction_id="TX-LATE")
    )
    assert result["status"] == "OVERPAYMENT"
    assert result["applied_cents"] == 0
    assert result["overpayment_cents"] == 6000
    assert commerce.paid_calls == []


# ------------------------------------------------------------------ 可退额度（TC-P09 语义）
def test_reserve_refund_quota_is_atomic_conditional_update(
    payment_settings, payment_db: Session, commerce, sandbox_channel
) -> None:
    """可退额度用条件 UPDATE 预留：有效预留 + 已退款永远不会超过原收款。"""
    sandbox_channel("sandbox")
    _installment, intent = _make_intent(payment_db, commerce, amounts=[10000])
    payment_db.commit()
    applied = svc.apply_verified_payment(
        payment_db, _verified(intent, amount_cents=10000, transaction_id="TX-QUOTA")
    )
    allocation_id = applied["allocation_id"]
    assert allocation_id

    assert repo.reserve_refund_quota(payment_db, allocation_id=allocation_id, amount_cents=6000) == 1
    # 第二个客服看到的仍是旧快照，但条件更新会挡住超额预留
    assert repo.reserve_refund_quota(payment_db, allocation_id=allocation_id, amount_cents=5000) == 0
    assert repo.reserve_refund_quota(payment_db, allocation_id=allocation_id, amount_cents=4000) == 1
    assert repo.reserve_refund_quota(payment_db, allocation_id=allocation_id, amount_cents=1) == 0
    payment_db.commit()

    allocation = payment_db.get(PaymentAllocation, allocation_id)
    assert allocation is not None
    assert allocation.reserved_refund_cents == 10000
    assert repo.refund_quota(payment_db, allocation_id)["available_cents"] == 0

    assert repo.settle_refund_quota(payment_db, allocation_id=allocation_id, amount_cents=10000) == 1
    assert repo.reserve_refund_quota(payment_db, allocation_id=allocation_id, amount_cents=1) == 0
    assert repo.release_refund_quota(payment_db, allocation_id=allocation_id, amount_cents=1) == 0
    snapshot = repo.refund_quota(payment_db, allocation_id)
    assert snapshot["refunded_cents"] == 10000
    assert snapshot["reserved_refund_cents"] == 0


# ------------------------------------------------------------------ 汇总口径
def test_order_paid_summary_counts_only_applied(
    payment_settings, payment_db: Session, commerce, sandbox_channel
) -> None:
    """``paid_cents`` 只计 APPLIED；多付款的退款不冲减正常订单实收。"""
    sandbox_channel("sandbox")
    _installment, intent = _make_intent(payment_db, commerce, amounts=[8000])
    payment_db.commit()
    svc.apply_verified_payment(
        payment_db, _verified(intent, amount_cents=8000, transaction_id="TX-SUM")
    )
    attempt = repo.get_attempt(payment_db, intent.attempt_id)
    assert attempt is not None
    repo.create_allocation(
        payment_db,
        payment_id=attempt.id,
        order_id=attempt.order_id,
        installment_id=attempt.installment_id,
        amount_cents=2000,
        currency="CNY",
        classification="OVERPAYMENT",
    )
    repo.create_allocation(
        payment_db,
        payment_id=attempt.id,
        order_id=attempt.order_id,
        installment_id=attempt.installment_id,
        amount_cents=500,
        currency="CNY",
        classification="QUARANTINED",
    )
    applied_allocation = repo.get_allocation_for(
        payment_db, payment_id=attempt.id, installment_id=attempt.installment_id, classification="APPLIED"
    )
    overpayment_allocation = repo.get_allocation_for(
        payment_db,
        payment_id=attempt.id,
        installment_id=attempt.installment_id,
        classification="OVERPAYMENT",
    )
    assert applied_allocation is not None and overpayment_allocation is not None

    # 多付款退款（成功）不冲减实收；正常货款的退款才计入 refunded_applied_cents
    repo.create_refund(
        payment_db,
        refund_no="RF-OVER-1",
        order_id=attempt.order_id,
        payment_id=attempt.id,
        allocation_id=overpayment_allocation.id,
        installment_id=attempt.installment_id,
        user_id=USER_ID,
        provider="sandbox",
        amount_cents=2000,
        currency="CNY",
        reason="多付款退回",
        kind="overpayment",
    ).status = "SUCCEEDED"
    repo.create_refund(
        payment_db,
        refund_no="RF-GOODS-1",
        order_id=attempt.order_id,
        payment_id=attempt.id,
        allocation_id=applied_allocation.id,
        installment_id=attempt.installment_id,
        user_id=USER_ID,
        provider="sandbox",
        amount_cents=3000,
        currency="CNY",
        reason="部分退款",
    ).status = "SUCCEEDED"
    payment_db.commit()

    summary = svc.order_paid_summary(payment_db, order_id=attempt.order_id)
    assert summary["paid_cents"] == 8000
    assert summary["refunded_applied_cents"] == 3000
    assert {row["classification"] for row in summary["allocations"]} == {
        "APPLIED",
        "OVERPAYMENT",
        "QUARANTINED",
    }


# ------------------------------------------------------------------ 其他规则
def test_business_event_key_is_normalized_and_amount_sensitive() -> None:
    """无事件号时用规范化业务键去重：同业务同键、金额不同则不同键。"""
    first = business_event_key("sandbox", "TX-1", "ST-1", "100")
    assert first == business_event_key("sandbox", " TX-1 ", "ST-1", "100")
    assert first != business_event_key("sandbox", "TX-1", "ST-1", "101")
    assert first.startswith("biz-")


def test_bridge_reports_missing_commerce_module(payment_settings, payment_db: Session, monkeypatch) -> None:
    """商务模块缺失时桥接给 ``PROVIDER_UNAVAILABLE``，便于支付模块隔离测试。"""
    import types

    monkeypatch.setitem(sys.modules, "app.modules.commerce", types.ModuleType("app.modules.commerce"))
    monkeypatch.delitem(sys.modules, "app.modules.commerce.repository", raising=False)
    with pytest.raises(AppError) as excinfo:
        bridge.load_order(payment_db, "ord-missing")
    assert excinfo.value.code == "PROVIDER_UNAVAILABLE"
    assert "商务模块" in excinfo.value.message


def test_attempt_unique_constraints(payment_settings, payment_db: Session, commerce, sandbox_channel) -> None:
    """商户单号唯一、渠道交易号在非空时唯一（数据库层保证）。"""
    sandbox_channel("sandbox")
    _installment, intent = _make_intent(payment_db, commerce, amounts=[1000])
    payment_db.commit()
    with pytest.raises(sa.exc.IntegrityError):
        repo.create_attempt(
            payment_db,
            order_id="ord-x",
            installment_id="ist-x",
            user_id=USER_ID,
            provider="sandbox",
            scene="native",
            merchant_order_no=intent.merchant_order_no,
            expected_cents=1000,
            currency="CNY",
            action_expires_at=None,
        )
    payment_db.rollback()


def test_payment_attempt_helper_keeps_expected_amount_server_side(
    payment_settings, payment_db: Session, commerce, sandbox_channel
) -> None:
    """服务端按应付项余额裁定金额，重复发起复用同一商户单号（不换新单号再下单）。"""
    gateway = sandbox_channel("sandbox")
    installment, first = _make_intent(payment_db, commerce, amounts=[5000, 5000])
    payment_db.commit()
    second = svc.create_intent(
        payment_db,
        installment_id=installment.id,
        provider="sandbox",
        scene="native",
        actor_id=USER_ID,
    )
    assert second.merchant_order_no == first.merchant_order_no
    assert second.expected_cents == 5000
    assert gateway.create_calls == [first.merchant_order_no]
    attempts = repo.list_attempts_for_installment(payment_db, installment.id)
    assert len(attempts) == 1


@pytest.fixture(autouse=True)
def enable_payments_for_channel_tests(monkeypatch):
    monkeypatch.setenv("FEATURE_ONLINE_PAYMENT", "true")
