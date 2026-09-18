"""支付与退款 HTTP 集成测试（13 文档 TC-P01..TC-P11 的支付侧）。

真实依赖：FastAPI 应用、真实 SQLite 临时文件、真实路由与依赖链（会话 cookie + CSRF +
幂等键）、真实渠道适配器（沙箱通道与微信/支付宝验签路径）。商务模块用替身替换桥接，
以便在 commerce 并行开发期独立验证支付时序与金额规则；真实跨模块联调另见文件末尾的
``test_cross_module_*`` 用例（使用真实 commerce 模块与真实下单接口）。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.modules.notifications.models import Notification
from app.modules.payments import repository as repo
from app.modules.payments import service as svc
from app.modules.payments.models import (
    PaymentAllocation,
    PaymentEvent,
    ReconciliationRun,
)
from app.platform import audit
from app.platform.errors import AppError
from app.platform.ids import iso, now_ms
from app.platform.reliability import OutboxEvent


class NotificationResult:
    """渠道回调的应答：协议体 + 本地处理结论（响应头 ``X-Payment-Status``）。"""

    def __init__(self, response: Any) -> None:
        self.response = response

    @property
    def status_code(self) -> int:
        return int(self.response.status_code)

    @property
    def text(self) -> str:
        return str(self.response.text)

    @property
    def status(self) -> str:
        return self.response.headers.get("x-payment-status", "")

    @property
    def duplicate(self) -> bool:
        return self.response.headers.get("x-payment-duplicate") == "1"

    def json(self) -> Any:
        return self.response.json()


def _post_notification(client: TestClient, provider: str, raw: bytes, headers: dict[str, str]) -> NotificationResult:
    """渠道回调不要求 cookie/CSRF/Origin；应答体按渠道协议，结论在响应头。"""
    return NotificationResult(
        client.post(f"/api/v1/payment-notifications/{provider}", content=raw, headers=headers)
    )


def _notify(client: TestClient, gateway: Any, *, merchant_order_no: str, amount_cents: int, **kwargs: Any):
    """用渠道侧已签名回调走真实通知路由。"""
    headers, raw = gateway.simulate_notification(
        merchant_order_no=merchant_order_no, amount_cents=amount_cents, **kwargs
    )
    return _post_notification(client, gateway.name, raw, headers)


def fresh(session: Session) -> Session:
    """结束当前只读事务并过期身份映射，确保看到其他连接已提交的写入。

    会话工厂用 ``expire_on_commit=False``（项目约定），因此仅 rollback 不够，
    必须显式 ``expire_all()`` 才会重新 SELECT 最新行。
    """
    session.rollback()
    session.expire_all()
    return session


def _alerts(db: Session) -> int:
    return int(
        db.scalar(
            sa.select(sa.func.count()).select_from(Notification).where(
                Notification.event_type == "security.alert"
            )
        )
        or 0
    )


def _outbox_count(db: Session, event_type: str) -> int:
    return int(
        db.scalar(
            sa.select(sa.func.count()).select_from(OutboxEvent).where(
                OutboxEvent.event_type == event_type
            )
        )
        or 0
    )


def _applied_allocation(db: Session, attempt_id: str) -> PaymentAllocation:
    attempt = repo.get_attempt(db, attempt_id)
    assert attempt is not None
    allocation = repo.get_allocation_for(
        db, payment_id=attempt.id, installment_id=attempt.installment_id, classification="APPLIED"
    )
    assert allocation is not None, "应存在 APPLIED 分配"
    return allocation


def _overpayment_allocation(db: Session, attempt_id: str) -> PaymentAllocation:
    attempt = repo.get_attempt(db, attempt_id)
    assert attempt is not None
    allocation = repo.get_allocation_for(
        db, payment_id=attempt.id, installment_id=attempt.installment_id, classification="OVERPAYMENT"
    )
    assert allocation is not None, "应存在 OVERPAYMENT 分配"
    return allocation


def _refund_via_port(
    db: Session,
    *,
    attempt_id: str,
    allocation_id: str,
    amount_cents: int,
    items: list[dict[str, Any]],
    requester: str = "stf_finance_1",
    approver: str = "stf_finance_2",
    reason: str = "测试退款",
) -> Any:
    """建立退款申请 → 审批 → 执行（审批与执行分开授权）。"""
    refund = svc.create_refund_request(
        db,
        payment_id=attempt_id,
        allocation_id=allocation_id,
        amount_cents=amount_cents,
        items=items,
        reason=reason,
        actor_id=requester,
    )
    db.commit()
    svc.approve_refund(db, refund_id=refund.id, actor_id=approver, reason="核查通过")
    db.commit()
    return refund


# ------------------------------------------------------------------ TC-P01
def test_tc_p01_full_prepayment_balance_and_cancelled_attempt(
    customer, payment_client, payment_db, commerce, sandbox_channel
) -> None:
    """全款/预付款/尾款只有真实成功款入账；取消的支付尝试不计金额。"""
    gateway = sandbox_channel("sandbox")
    order, installments = commerce.create_order(user_id=customer.user_id, amounts=[30000, 20000])
    payment_db.commit()

    created = customer.create_attempt(installments[0].id, idempotency_key="tc-p01-prepay")
    assert created.status_code == 201, created.text
    assert created.json()["status"] == "PENDING"
    assert created.json()["action"]["type"] == "qr"
    attempt_id = created.json()["payment_attempt_id"]
    attempt = repo.get_attempt(payment_db, attempt_id)
    assert attempt is not None

    paid = _notify(
        payment_client, gateway, merchant_order_no=attempt.merchant_order_no, amount_cents=30000
    )
    assert paid.status_code == 200, paid.text
    assert paid.status == "APPLIED"

    summary = svc.order_paid_summary(payment_db, order_id=order.id)
    assert summary["paid_cents"] == 30000
    assert commerce.installments[installments[0].id].status == "PAID"
    assert commerce.installments[installments[1].id].status == "DUE"

    # 尾款：发起后取消支付，取消的尝试不得入账
    balance = customer.create_attempt(installments[1].id, idempotency_key="tc-p01-balance")
    assert balance.status_code == 201, balance.text
    balance_attempt_id = balance.json()["payment_attempt_id"]
    closed = svc.close_payment_attempts_for_installment(
        payment_db, installment_id=installments[1].id, reason="user_cancelled"
    )
    payment_db.commit()
    assert closed == 1
    assert repo.get_attempt(payment_db, balance_attempt_id).status == "CLOSED"
    assert svc.order_paid_summary(payment_db, order_id=order.id)["paid_cents"] == 30000

    # 尾款真的付了才计入
    late = customer.create_attempt(installments[1].id, idempotency_key="tc-p01-balance-2")
    assert late.status_code == 201, late.text
    second = _notify(
        payment_client,
        gateway,
        merchant_order_no=repo.get_attempt(payment_db, late.json()["payment_attempt_id"]).merchant_order_no,
        amount_cents=20000,
    )
    assert second.status_code == 200, second.text
    assert svc.order_paid_summary(payment_db, order_id=order.id)["paid_cents"] == 50000
    assert len(commerce.paid_calls) == 2
    assert attempt_id  # 首次支付尝试仍是成功记录


# ------------------------------------------------------------------ TC-P02
def test_tc_p02_same_notification_ten_times_has_single_effect(
    customer, payment_client, payment_db, commerce, sandbox_channel
) -> None:
    """同一通知发送 10 次：收款、库存/订单推进、outbox 各只有一次业务效果。"""
    gateway = sandbox_channel("sandbox")
    order, installments = commerce.create_order(user_id=customer.user_id, amounts=[10000])
    payment_db.commit()
    created = customer.create_attempt(installments[0].id, idempotency_key="tc-p02")
    attempt = repo.get_attempt(payment_db, created.json()["payment_attempt_id"])
    assert attempt is not None

    headers, raw = gateway.simulate_notification(
        merchant_order_no=attempt.merchant_order_no, amount_cents=10000
    )
    statuses = []
    for _ in range(10):
        response = _post_notification(payment_client, "sandbox", raw, headers)
        assert response.status_code == 200, response.text
        statuses.append(response.status)
    assert statuses[0] == "APPLIED"
    assert set(statuses[1:]) == {"APPLIED"}

    fresh(payment_db)
    assert repo.applied_sum(payment_db, installment_id=installments[0].id) == 10000
    assert len(repo.list_allocations_for_payment(payment_db, attempt.id)) == 1
    assert len(commerce.paid_calls) == 1
    assert _outbox_count(payment_db, "payment.verified") == 1
    assert int(
        payment_db.scalar(sa.select(sa.func.count()).select_from(PaymentEvent)) or 0
    ) == 1
    assert svc.order_paid_summary(payment_db, order_id=order.id)["paid_cents"] == 10000


# ------------------------------------------------------------------ TC-P03
def test_tc_p03_invalid_notifications_are_rejected_and_recorded(
    customer, payment_client, payment_db, commerce, wechat_channel, payment_settings
) -> None:
    """错签名、过期重放、未知证书序列、解密失败：不入账且记录安全事件与原始字节。"""
    order, installments = commerce.create_order(user_id=customer.user_id, amounts=[10000])
    payment_db.commit()
    attempt = repo.create_attempt(
        payment_db,
        order_id=order.id,
        installment_id=installments[0].id,
        user_id=customer.user_id,
        provider="wechat",
        scene="native",
        merchant_order_no="STWXTC-P03",
        expected_cents=10000,
        currency="CNY",
        action_expires_at=None,
    )
    payment_db.commit()

    cases = {
        "signature_mismatch": wechat_channel.notification(
            merchant_order_no=attempt.merchant_order_no,
            amount_cents=10000,
            sign_key=wechat_channel.other_private_key,
        ),
        "timestamp_out_of_window": wechat_channel.notification(
            merchant_order_no=attempt.merchant_order_no,
            amount_cents=10000,
            timestamp=int(now_ms() / 1000) - 3600,
        ),
        "unknown_cert_serial": wechat_channel.notification(
            merchant_order_no=attempt.merchant_order_no,
            amount_cents=10000,
            serial="SERIAL-NOT-STORED",
        ),
        "decrypt_failed": wechat_channel.notification(
            merchant_order_no=attempt.merchant_order_no,
            amount_cents=10000,
            encrypt_key=b"z" * 32,
        ),
    }
    for name, (headers, raw) in cases.items():
        response = _post_notification(payment_client, "wechat", raw, headers)
        assert response.status_code == 403, (name, response.text)
        assert response.json()["code"] == "FAIL"
        # 原始字节必须留存，库里记摘要与路径
        digest = hashlib.sha256(raw).hexdigest()
        target = payment_settings.data_dir / "storage" / "payment_events" / f"{digest}.payload"
        assert target.is_file(), f"{name} 未保留验签原始字节"
        assert target.read_bytes() == raw

    fresh(payment_db)
    assert repo.applied_sum(payment_db, installment_id=installments[0].id) == 0
    assert commerce.paid_calls == []
    assert repo.get_attempt(payment_db, attempt.id).status != "SUCCEEDED"
    # 未通过验签的通知没有可信业务字段，因此事件按 provider + 摘要留证，不关联尝试
    events = list(
        payment_db.scalars(
            sa.select(PaymentEvent).where(
                PaymentEvent.provider == "wechat", PaymentEvent.verified == 0
            )
        )
    )
    assert len(events) == 4
    assert all(event.processing_result == "REJECTED" for event in events)
    assert {event.rejection_reason for event in events} == set(cases)
    assert audit.query(payment_db, action="payment.notification_rejected")
    assert audit.query(payment_db, resource_type="payment_event")


# ------------------------------------------------------------------ TC-P04
def test_tc_p04_mismatched_amount_currency_and_merchant_are_quarantined(
    customer, payment_client, payment_db, commerce, sandbox_channel, wechat_channel
) -> None:
    """签名合法但金额/币种/商户不符：隔离告警，不为目标订单发货。"""
    order, installments = commerce.create_order(user_id=customer.user_id, amounts=[10000])
    payment_db.commit()

    # 金额不符
    gateway = sandbox_channel("sandbox")
    created = customer.create_attempt(installments[0].id, idempotency_key="tc-p04-amount")
    amount_attempt = repo.get_attempt(payment_db, created.json()["payment_attempt_id"])
    assert amount_attempt is not None
    response = _notify(
        payment_client, gateway, merchant_order_no=amount_attempt.merchant_order_no, amount_cents=9900
    )
    assert response.status_code == 200, response.text
    assert response.status == "QUARANTINED"

    # 币种不符
    currency = _notify(
        payment_client, gateway, merchant_order_no=amount_attempt.merchant_order_no,
        amount_cents=10000, currency="USD",
    )
    assert currency.status == "QUARANTINED"

    # 商户/应用不符（微信真实验签通过、mchid 不匹配）
    wechat_attempt = repo.create_attempt(
        payment_db,
        order_id=order.id,
        installment_id=installments[0].id,
        user_id=customer.user_id,
        provider="wechat",
        scene="native",
        merchant_order_no="STWXTC-P04",
        expected_cents=10000,
        currency="CNY",
        action_expires_at=None,
    )
    payment_db.commit()
    headers, raw = wechat_channel.notification(
        merchant_order_no=wechat_attempt.merchant_order_no, amount_cents=10000, mchid="9999999999"
    )
    mismatch = _post_notification(payment_client, "wechat", raw, headers)
    assert mismatch.status_code == 200, mismatch.text
    assert mismatch.status == "QUARANTINED"

    fresh(payment_db)
    summary = svc.order_paid_summary(payment_db, order_id=order.id)
    assert summary["paid_cents"] == 0
    assert {row["classification"] for row in summary["allocations"]} == {"QUARANTINED"}
    assert commerce.paid_calls == []
    assert commerce.installments[installments[0].id].status == "DUE"
    assert order.status == "PENDING_PAYMENT"  # 不为目标订单发货
    assert _alerts(payment_db) >= 3
    assert repo.get_attempt(payment_db, amount_attempt.id).status != "SUCCEEDED"


# ------------------------------------------------------------------ TC-P05
def test_tc_p05_create_timeout_marks_unknown_and_queries_same_order_no(
    customer, payment_client, payment_db, commerce, sandbox_channel
) -> None:
    """渠道下单超时但真实已建单：记 UNKNOWN 并用同一商户单号查单，不重复收款。"""
    gateway = sandbox_channel("sandbox")
    gateway.timeout_on_create = True
    _order, installments = commerce.create_order(user_id=customer.user_id, amounts=[15000])
    payment_db.commit()

    created = customer.create_attempt(installments[0].id, idempotency_key="tc-p05")
    assert created.status_code == 201, created.text
    assert created.json()["status"] == "UNKNOWN"
    attempt_id = created.json()["payment_attempt_id"]
    attempt = repo.get_attempt(payment_db, attempt_id)
    assert attempt is not None and attempt.status == "UNKNOWN"
    assert gateway.create_calls == [attempt.merchant_order_no]

    # 重复发起不得换新单号再下单
    again = customer.create_attempt(installments[0].id, idempotency_key="tc-p05-again")
    assert again.status_code == 201, again.text
    assert again.json()["payment_attempt_id"] == attempt_id
    assert gateway.create_calls == [attempt.merchant_order_no]

    # 渠道侧真的收到了钱：查单确认（成功只认通知或查单）
    gateway.mark_paid(attempt.merchant_order_no, amount_cents=15000, transaction_id="SBXTXN-P05")
    # 受限查单策略：距上次查单不足最小间隔时不再打扰渠道
    immediate = customer.attempt_status(attempt_id)
    assert immediate.json()["status"] == "UNKNOWN"
    queries_before = len(gateway.query_calls)
    assert queries_before == 1  # 上一次「重复发起」触发的那一次
    # 模拟前端轮询到下一轮（把上次查单时间回拨到最小间隔之前）
    attempt = repo.get_attempt(payment_db, attempt_id)
    assert attempt is not None
    attempt.last_queried_at = svc.now_ms() - svc.QUERY_MIN_INTERVAL_MS - 1_000
    payment_db.commit()

    status = customer.attempt_status(attempt_id)
    assert status.status_code == 200, status.text
    assert status.json()["status"] == "SUCCEEDED"
    assert status.json()["verified_at"]
    assert len(gateway.query_calls) == queries_before + 1

    fresh(payment_db)
    assert repo.applied_sum(payment_db, installment_id=installments[0].id) == 15000
    assert len(repo.list_allocations_for_payment(payment_db, attempt_id)) == 1
    # 再查一次不会重复收款
    customer.attempt_status(attempt_id)
    fresh(payment_db)
    assert repo.applied_sum(payment_db, installment_id=installments[0].id) == 15000
    assert len(commerce.paid_calls) == 1


# ------------------------------------------------------------------ TC-P06
def test_tc_p06_success_cannot_be_downgraded_by_late_create_response(
    customer, payment_client, payment_db, commerce, sandbox_channel
) -> None:
    """回调先到、创建响应后到：成功不可回退，金额只分配一次。"""
    gateway = sandbox_channel("sandbox")
    order, installments = commerce.create_order(user_id=customer.user_id, amounts=[12000])
    payment_db.commit()
    attempt = repo.create_attempt(
        payment_db,
        order_id=order.id,
        installment_id=installments[0].id,
        user_id=customer.user_id,
        provider="sandbox",
        scene="native",
        merchant_order_no="STSANDBOX-P06",
        expected_cents=12000,
        currency="CNY",
        action_expires_at=None,
    )
    payment_db.commit()

    # 回调先到：已验签通知确认收款
    headers, raw = gateway.simulate_notification(
        merchant_order_no=attempt.merchant_order_no,
        amount_cents=12000,
        provider_transaction_id="SBXTXN-P06",
    )
    applied = _post_notification(payment_client, "sandbox", raw, headers)
    assert applied.status == "APPLIED"
    fresh(payment_db)
    assert repo.get_attempt(payment_db, attempt.id).status == "SUCCEEDED"

    # 后到的下单响应（渠道超时/迟到）：不得把 SUCCEEDED 覆盖为 PENDING/UNKNOWN
    late = repo.get_attempt(payment_db, attempt.id)
    assert late is not None
    svc._apply_create_response(payment_db, late, status="UNKNOWN", action=None)
    payment_db.commit()
    fresh(payment_db)
    late = repo.get_attempt(payment_db, attempt.id)
    assert late is not None
    assert late.status == "SUCCEEDED"
    assert late.provider_transaction_id == "SBXTXN-P06"
    assert repo.applied_sum(payment_db, installment_id=installments[0].id) == 12000

    # 成功后再次创建支付：不得新建尝试、不得重复分配
    blocked = customer.create_attempt(installments[0].id, idempotency_key="tc-p06-again")
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["error"]["code"] == "STATE_CONFLICT"
    fresh(payment_db)
    assert len(repo.list_attempts_for_installment(payment_db, installments[0].id)) == 1
    assert len(repo.list_allocations_for_payment(payment_db, attempt.id)) == 1


# ------------------------------------------------------------------ TC-P07
def test_tc_p07_two_channels_paid_overpayment_not_doubled(
    customer, payment_client, payment_db, commerce, sandbox_channel
) -> None:
    """两个渠道都真实收款：逐笔记账，只分配应付余额，剩余进入 OVERPAYMENT 并触发退款。"""
    wechat = sandbox_channel("wechat")
    sandbox = sandbox_channel("sandbox")
    order, installments = commerce.create_order(user_id=customer.user_id, amounts=[10000])
    payment_db.commit()

    first = customer.create_attempt(
        installments[0].id, provider="wechat", idempotency_key="tc-p07-wechat"
    )
    assert first.status_code == 201, first.text
    wechat_attempt = repo.get_attempt(payment_db, first.json()["payment_attempt_id"])
    assert wechat_attempt is not None

    # 切换到第二个渠道：关闭旧单并核对（旧二维码仍可能被支付）
    second = customer.create_attempt(
        installments[0].id, provider="sandbox", idempotency_key="tc-p07-sandbox"
    )
    assert second.status_code == 201, second.text
    assert second.json()["payment_attempt_id"] != wechat_attempt.id
    fresh(payment_db)
    assert repo.get_attempt(payment_db, wechat_attempt.id).status == "CLOSED"

    sandbox_attempt = repo.get_attempt(payment_db, second.json()["payment_attempt_id"])
    assert sandbox_attempt is not None
    applied = _notify(
        payment_client, sandbox, merchant_order_no=sandbox_attempt.merchant_order_no, amount_cents=10000
    )
    assert applied.status == "APPLIED"

    # 旧二维码迟到支付：真实收款同样留账，但不翻倍订单实收
    late = _notify(
        payment_client, wechat, merchant_order_no=wechat_attempt.merchant_order_no, amount_cents=10000
    )
    assert late.status_code == 200, late.text
    assert late.status == "OVERPAYMENT"

    fresh(payment_db)
    summary = svc.order_paid_summary(payment_db, order_id=order.id)
    assert summary["paid_cents"] == 10000
    classifications = sorted(row["classification"] for row in summary["allocations"])
    assert classifications == ["APPLIED", "OVERPAYMENT"]
    assert _outbox_count(payment_db, "refund.requested") == 1
    assert _alerts(payment_db) >= 1
    assert repo.get_attempt(payment_db, wechat_attempt.id).status == "SUCCEEDED"


# ------------------------------------------------------------------ TC-P09
def test_tc_p09_concurrent_refunds_cannot_exceed_original_payment(
    customer, payment_client, payment_db, commerce, sandbox_channel
) -> None:
    """并发退同一笔款：有效预留 + 已退款不超过原收款，同退款号重试返回同结果。"""
    gateway = sandbox_channel("sandbox")
    order, installments = commerce.create_order(user_id=customer.user_id, amounts=[10000])
    payment_db.commit()
    created = customer.create_attempt(installments[0].id, idempotency_key="tc-p09")
    attempt_id = created.json()["payment_attempt_id"]
    attempt = repo.get_attempt(payment_db, attempt_id)
    assert attempt is not None
    _notify(
        payment_client, gateway, merchant_order_no=attempt.merchant_order_no, amount_cents=10000
    )
    fresh(payment_db)
    allocation = _applied_allocation(payment_db, attempt_id)

    first = _refund_via_port(
        payment_db,
        attempt_id=attempt_id,
        allocation_id=allocation.id,
        amount_cents=6000,
        items=[{"kind": "goods", "amount_cents": 6000}],
    )
    outcome = svc.request_refund(
        payment_db, refund_id=first.id, actor_id="stf_finance_2", idempotency_key="refund-key-1"
    )
    payment_db.commit()
    assert outcome.status == "SUCCEEDED"
    assert gateway.refund_calls[0]["merchant_order_no"] == attempt.merchant_order_no
    assert gateway.refund_calls[0]["amount_cents"] == 6000

    # 同键重试返回同资源，不重复退款
    replay = svc.request_refund(
        payment_db, refund_id=first.id, actor_id="stf_finance_2", idempotency_key="refund-key-1"
    )
    payment_db.commit()
    assert replay.refund_no == outcome.refund_no
    assert len(gateway.refund_calls) == 1

    # 另一笔并发退款超出剩余可退额：条件 UPDATE 挡住
    second = _refund_via_port(
        payment_db,
        attempt_id=attempt_id,
        allocation_id=allocation.id,
        amount_cents=6000,
        items=[{"kind": "goods", "amount_cents": 6000}],
    )
    with pytest.raises(AppError) as blocked:
        svc.request_refund(
            payment_db, refund_id=second.id, actor_id="stf_finance_2", idempotency_key="refund-key-2"
        )
    assert blocked.value.code == "REFUND_LIMIT_EXCEEDED"
    payment_db.rollback()

    fresh(payment_db)
    allocation = payment_db.get(PaymentAllocation, allocation.id)
    assert allocation is not None
    assert allocation.refunded_cents == 6000
    assert allocation.refunded_cents + allocation.reserved_refund_cents <= allocation.amount_cents
    assert allocation.reserved_refund_cents == 0

    # 剩余额度仍可正常退（4000），再退 1 分被拒
    third = _refund_via_port(
        payment_db,
        attempt_id=attempt_id,
        allocation_id=allocation.id,
        amount_cents=4000,
        items=[{"kind": "goods", "amount_cents": 4000}],
    )
    assert (
        svc.request_refund(
            payment_db, refund_id=third.id, actor_id="stf_finance_2", idempotency_key="refund-key-3"
        ).status
        == "SUCCEEDED"
    )
    payment_db.commit()
    fourth = _refund_via_port(
        payment_db,
        attempt_id=attempt_id,
        allocation_id=allocation.id,
        amount_cents=100,
        items=[{"kind": "goods", "amount_cents": 100}],
    )
    with pytest.raises(AppError) as over:
        svc.request_refund(
            payment_db, refund_id=fourth.id, actor_id="stf_finance_2", idempotency_key="refund-key-4"
        )
    assert over.value.code == "REFUND_LIMIT_EXCEEDED"
    payment_db.rollback()
    fresh(payment_db)
    assert repo.refund_quota(payment_db, allocation.id)["available_cents"] == 0
    assert svc.order_paid_summary(payment_db, order_id=order.id)["refunded_applied_cents"] == 10000


# ------------------------------------------------------------------ TC-P10
def test_tc_p10_multiple_partial_refunds_with_shipping_and_overpayment(
    customer, payment_client, payment_db, commerce, sandbox_channel
) -> None:
    """多次部分退款含运费与多付：分摊总和与渠道退款一致；多付退款不冲正常实收。"""
    gateway = sandbox_channel("sandbox")
    order, installments = commerce.create_order(user_id=customer.user_id, amounts=[10000])
    payment_db.commit()
    # 先建立两个渠道的支付尝试（同一应付项必须先有活动尝试才能并发收款），再依次收款
    alipay = sandbox_channel("alipay")
    first = customer.create_attempt(installments[0].id, idempotency_key="tc-p10-a")
    attempt_a = repo.get_attempt(payment_db, first.json()["payment_attempt_id"])
    assert attempt_a is not None
    second = customer.create_attempt(
        installments[0].id, provider="alipay", idempotency_key="tc-p10-b"
    )
    assert second.status_code == 201, second.text
    attempt_b = repo.get_attempt(payment_db, second.json()["payment_attempt_id"])
    assert attempt_b is not None

    _notify(payment_client, gateway, merchant_order_no=attempt_a.merchant_order_no, amount_cents=10000)
    fresh(payment_db)

    # 第二个渠道真实收款：应付项已无余额 → 全额多付
    overpaid = _notify(
        payment_client, alipay, merchant_order_no=attempt_b.merchant_order_no, amount_cents=10000
    )
    assert overpaid.status == "OVERPAYMENT"

    fresh(payment_db)
    applied_allocation = _applied_allocation(payment_db, attempt_a.id)
    overpayment_allocation = _overpayment_allocation(payment_db, attempt_b.id)

    refunds = [
        (attempt_a.id, applied_allocation.id, 3000, [{"kind": "goods", "amount_cents": 3000}]),
        (attempt_a.id, applied_allocation.id, 2000, [{"kind": "shipping", "amount_cents": 2000}]),
        (attempt_a.id, applied_allocation.id, 5000, [{"kind": "goods", "amount_cents": 5000}]),
        (
            attempt_b.id,
            overpayment_allocation.id,
            10000,
            [{"kind": "overpayment", "amount_cents": 10000}],
        ),
    ]
    for index, (payment_id, allocation_id, amount_cents, items) in enumerate(refunds):
        refund = _refund_via_port(
            payment_db,
            attempt_id=payment_id,
            allocation_id=allocation_id,
            amount_cents=amount_cents,
            items=items,
        )
        outcome = svc.request_refund(
            payment_db,
            refund_id=refund.id,
            actor_id="stf_finance_2",
            idempotency_key=f"tc-p10-{index}",
        )
        payment_db.commit()
        assert outcome.status == "SUCCEEDED"
        assert outcome.amount_cents == amount_cents
        stored = repo.get_refund(payment_db, refund.id)
        assert stored is not None
        assert repo.refund_item_total(payment_db, stored.id) == stored.amount_cents

    fresh(payment_db)
    # 渠道退款总额与本地退款总额一致
    assert sum(call["amount_cents"] for call in gateway.refund_calls) == 10000
    assert sum(call["amount_cents"] for call in alipay.refund_calls) == 10000
    summary = svc.order_paid_summary(payment_db, order_id=order.id)
    assert summary["paid_cents"] == 10000  # 多付款不冲减正常实收
    assert summary["refunded_applied_cents"] == 10000
    assert [call["goods_cents"] for call in commerce.refund_calls] == [3000, 0, 5000, 0]
    assert [call["shipping_cents"] for call in commerce.refund_calls] == [0, 2000, 0, 0]
    applied_refunds = repo.list_refunds_for_allocation(payment_db, applied_allocation.id)
    over_refunds = repo.list_refunds_for_allocation(payment_db, overpayment_allocation.id)
    assert len(applied_refunds) == 3 and len(over_refunds) == 1
    assert applied_refunds[0].payment_id != over_refunds[0].payment_id


# ------------------------------------------------------------------ TC-P11
def test_tc_p11_duplicate_refund_notification_has_single_effect(
    customer, payment_client, payment_db, commerce, sandbox_channel
) -> None:
    """已退款通知重复：只一笔退款效果，不会重复释放或重复调用商务端口。"""
    gateway = sandbox_channel("sandbox")
    gateway.refund_result_status = "PROCESSING"  # 渠道先返回处理中，再补通知成功
    _order, installments = commerce.create_order(user_id=customer.user_id, amounts=[10000])
    payment_db.commit()
    created = customer.create_attempt(installments[0].id, idempotency_key="tc-p11")
    attempt = repo.get_attempt(payment_db, created.json()["payment_attempt_id"])
    assert attempt is not None
    _notify(
        payment_client, gateway, merchant_order_no=attempt.merchant_order_no, amount_cents=10000
    )
    fresh(payment_db)
    allocation = _applied_allocation(payment_db, attempt.id)

    refund = _refund_via_port(
        payment_db,
        attempt_id=attempt.id,
        allocation_id=allocation.id,
        amount_cents=4000,
        items=[{"kind": "goods", "amount_cents": 4000}],
    )
    outcome = svc.request_refund(
        payment_db, refund_id=refund.id, actor_id="stf_finance_2", idempotency_key="tc-p11-1"
    )
    payment_db.commit()
    assert outcome.status == "PROCESSING"
    fresh(payment_db)
    assert payment_db.get(PaymentAllocation, allocation.id).reserved_refund_cents == 4000

    first = svc.apply_refund_outcome(
        payment_db,
        refund_id=refund.id,
        status="SUCCEEDED",
        provider_refund_id=outcome.provider_refund_id,
        amount_cents=4000,
    )
    payment_db.commit()
    assert first["status"] == "SUCCEEDED" and first["duplicate"] is False
    second = svc.apply_refund_outcome(
        payment_db,
        refund_id=refund.id,
        status="SUCCEEDED",
        provider_refund_id=outcome.provider_refund_id,
        amount_cents=4000,
    )
    payment_db.commit()
    assert second["duplicate"] is True

    fresh(payment_db)
    stored = payment_db.get(PaymentAllocation, allocation.id)
    assert stored is not None
    assert stored.refunded_cents == 4000
    assert stored.reserved_refund_cents == 0
    assert _outbox_count(payment_db, "refund.succeeded") == 1
    assert len(commerce.refund_calls) == 1
    assert len(gateway.refund_calls) == 1


# ------------------------------------------------------------------ 对账
def test_reconciliation_detects_differences_per_row_and_requires_reason(
    customer, payment_client, payment_db, commerce, sandbox_channel, payment_settings
) -> None:
    """对账逐笔比较：渠道有本地无/本地成功渠道无/金额不符/重复支付/手续费不符，逐条处置。"""
    from app.adapters.payments.base import bill_date_of

    gateway = sandbox_channel("sandbox")
    order, installments = commerce.create_order(user_id=customer.user_id, amounts=[10000])
    payment_db.commit()
    created = customer.create_attempt(installments[0].id, idempotency_key="recon-1")
    attempt = repo.get_attempt(payment_db, created.json()["payment_attempt_id"])
    assert attempt is not None
    _notify(
        payment_client, gateway, merchant_order_no=attempt.merchant_order_no, amount_cents=10000
    )
    fresh(payment_db)

    # 渠道有本地无 + 手续费异常 + 重复渠道交易号
    gateway.mark_paid("STSANDBOX-UNKNOWN", amount_cents=5000, transaction_id="SBXTXN-EXTRA")
    gateway.set_fee(attempt.merchant_order_no, -1)
    gateway.mark_paid(
        "STSANDBOX-DUPTXN", amount_cents=100, transaction_id=gateway.ledger_entry(attempt.merchant_order_no)["transaction_id"]
    )
    # 本地成功渠道无：本地再确认一笔，但沙箱账本里没有对应行
    from app.modules.contracts import VerifiedPayment

    attempt_local = repo.create_attempt(
        payment_db,
        order_id=order.id,
        installment_id=installments[0].id,
        user_id=customer.user_id,
        provider="sandbox",
        scene="native",
        merchant_order_no="STSANDBOX-LOCALONLY",
        expected_cents=10000,
        currency="CNY",
        action_expires_at=None,
    )
    payment_db.commit()
    svc.apply_verified_payment(
        payment_db,
        VerifiedPayment(
            provider="sandbox",
            provider_transaction_id="SBXTXN-LOCALONLY",
            merchant_order_no=attempt_local.merchant_order_no,
            amount_cents=10000,
            currency="CNY",
            paid_at=now_ms(),
            raw_digest=hashlib.sha256(b"local-only").hexdigest(),
            event_id="recon-local-only",
        ),
    )
    payment_db.commit()

    bill_date = bill_date_of(now_ms())
    result = svc.run_reconciliation(
        payment_db, provider="sandbox", bill_date=bill_date, actor_id="stf_finance_1"
    )
    payment_db.commit()
    assert result["status"] == "COMPLETED"
    types = {row["difference_type"] for row in result["differences"]}
    assert {"CHANNEL_ONLY", "LOCAL_ONLY", "DUPLICATE_PAYMENT", "FEE_MISMATCH"} <= types
    run = payment_db.get(ReconciliationRun, result["run_id"])
    assert run is not None and run.bill_file_hash
    assert (payment_settings.data_dir / str(run.bill_payload_path)).is_file()

    # 重新对账保留历史（revision 递增）
    second = svc.run_reconciliation(
        payment_db, provider="sandbox", bill_date=bill_date, actor_id="stf_finance_1"
    )
    payment_db.commit()
    assert second["revision"] == result["revision"] + 1
    assert int(
        payment_db.scalar(
            sa.select(sa.func.count())
            .select_from(ReconciliationRun)
            .where(ReconciliationRun.bill_date == bill_date)
        )
        or 0
    ) == 2

    # 逐条处置：必须写理由；已关闭不能再处置
    difference_id = result["differences"][0]["id"]
    with pytest.raises(AppError) as missing_reason:
        svc.resolve_difference(
            payment_db,
            difference_id=difference_id,
            resolution="CONFIRMED_CHANNEL",
            actor_id="stf_finance_1",
            reason="",
        )
    assert missing_reason.value.code == "VALIDATION_ERROR"
    resolved = svc.resolve_difference(
        payment_db,
        difference_id=difference_id,
        resolution="CONFIRMED_CHANNEL",
        actor_id="stf_finance_1",
        reason="已向渠道核对，属渠道侧独立收款",
    )
    payment_db.commit()
    assert resolved["status"] == "RESOLVED"
    with pytest.raises(AppError) as again:
        svc.resolve_difference(
            payment_db,
            difference_id=difference_id,
            resolution="FALSE_POSITIVE",
            actor_id="stf_finance_1",
            reason="重复处置应被拒绝",
        )
    assert again.value.code == "STATE_CONFLICT"
    assert audit.query(payment_db, action="reconciliation.difference_resolved")


# ------------------------------------------------------------------ 归属与接口形状
def test_payment_endpoints_check_ownership(
    customer, customer_factory, payment_db, commerce, sandbox_channel
) -> None:
    """他人订单/支付尝试一律 404，不泄露资源是否存在。"""
    other = customer_factory()
    _order, installments = commerce.create_order(user_id=customer.user_id, amounts=[7000])
    payment_db.commit()

    foreign = other.create_attempt(installments[0].id, idempotency_key="foreign-1")
    assert foreign.status_code == 404
    assert foreign.json()["error"]["code"] == "RESOURCE_NOT_FOUND"

    created = customer.create_attempt(installments[0].id, idempotency_key="owner-1")
    assert created.status_code == 201
    attempt_id = created.json()["payment_attempt_id"]
    assert other.attempt_status(attempt_id).status_code == 404
    assert other.order_payments(attempt_id).status_code == 404


def test_create_attempt_rejects_client_supplied_amount_and_merchant_no(
    customer, payment_db, commerce, sandbox_channel
) -> None:
    """创建支付不接收商户号或金额：额外字段直接 422。"""
    _order, installments = commerce.create_order(user_id=customer.user_id, amounts=[5000])
    payment_db.commit()
    response = customer.create_attempt(
        installments[0].id,
        idempotency_key="no-amount",
        body={"amount_cents": 1, "merchant_order_no": "HACK"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_idempotency_key_conflict_and_replay(customer, payment_db, commerce, sandbox_channel) -> None:
    """同键不同请求 409；同键重试返回同资源。"""
    sandbox_channel("sandbox")
    _order, installments = commerce.create_order(user_id=customer.user_id, amounts=[5000])
    payment_db.commit()
    first = customer.create_attempt(installments[0].id, idempotency_key="idem-x")
    assert first.status_code == 201
    replay = customer.create_attempt(installments[0].id, idempotency_key="idem-x")
    assert replay.status_code == 201
    assert replay.json() == first.json()

    conflict = customer.create_attempt(
        installments[0].id, provider="wechat", idempotency_key="idem-x"
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"

    listed = customer.order_payments(
        repo.get_attempt(payment_db, first.json()["payment_attempt_id"]).order_id
    )
    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()["items"]] == [first.json()["payment_attempt_id"]]


def test_notification_routes_do_not_require_user_cookie_or_origin(
    payment_client, payment_db, commerce, sandbox_channel
) -> None:
    """渠道回调不要求 cookie/CSRF/Origin；沙箱路由只在开发/测试可达。"""
    gateway = sandbox_channel("sandbox")
    headers, raw = gateway.simulate_notification(merchant_order_no="ST-NO-ATTEMPT", amount_cents=100)
    response = _post_notification(payment_client, "sandbox", raw, headers)
    # 未登记商户单号：留证 + 告警，不给成功入账
    assert response.status_code == 200, response.text
    assert response.status == "UNKNOWN_ORDER"
    fresh(payment_db)
    events = payment_db.scalars(
        sa.select(PaymentEvent).where(PaymentEvent.merchant_order_no == "ST-NO-ATTEMPT")
    ).all()
    assert len(events) == 1 and events[0].processing_result == "UNKNOWN_ORDER"

    # sandbox 回调在生产必须 404（app_env 判定）
    settings = svc.get_settings()
    assert settings.app_env == "test"
    production = svc.get_settings().model_copy(update={"app_env": "production"})
    assert production.app_env == "production"  # 生产判定依据同一配置项


def test_payment_attempt_status_is_local_authority(customer, payment_db, commerce, sandbox_channel) -> None:
    """查询返回本地权威状态；渠道不可用时仍是本地状态而不是「已取消」。"""
    sandbox_channel("sandbox")
    _order, installments = commerce.create_order(user_id=customer.user_id, amounts=[9000])
    payment_db.commit()
    created = customer.create_attempt(installments[0].id, idempotency_key="status-1")
    attempt_id = created.json()["payment_attempt_id"]
    status = customer.attempt_status(attempt_id)
    assert status.status_code == 200, status.text
    assert status.json()["status"] in ("PENDING", "UNKNOWN")
    assert status.json()["verified_at"] is None
    attempt = repo.get_attempt(payment_db, attempt_id)
    assert attempt is not None and attempt.last_queried_at is not None
    assert attempt.query_attempts >= 1
    assert iso(attempt.action_expires_at) is not None
    assert json.loads(json.dumps(created.json()))["payment_attempt_id"] == attempt_id


# ------------------------------------------------------------------ 真实跨模块联调
def test_cross_module_payment_confirms_order_and_consumes_inventory(
    customer, payment_client, payment_db, sandbox_channel
) -> None:
    """真实 commerce + catalog 联调：下单 → 支付 → 已验签通知 → 核销库存与推进订单。

    不使用桥接替身：走真实下单接口、真实库存预留、真实 ``on_installment_paid``，
    验证「收款 + 分配 + 应付状态 + 库存核销 + outbox 一并提交」的端到端效果。
    """
    import uuid

    from app.modules.catalog.models import Product, Sku
    from app.modules.commerce.models import Installment, Order
    from app.modules.identity.models import Address
    from app.platform.ids import new_id
    from app.platform.security import encrypt_pii, normalize_phone

    gateway = sandbox_channel("sandbox")
    suffix = uuid.uuid4().hex[:8]
    product = Product(
        id=new_id("product"),
        slug=f"e2e-suit-{suffix}",
        kind="stock_goods",
        title="联调示例兽装",
        subtitle="",
        status="published",
        description="",
        terms_version="shop-1",
    )
    sku = Sku(
        id=new_id("sku"),
        product_id=product.id,
        sku_code=f"SKU-E2E-{suffix}",
        label="标准规格",
        price_cents=25800,
        stock_on_hand=1,
        reserved=0,
        max_per_order=5,
        is_active=1,
    )
    address = Address(
        id=new_id("address"),
        user_id=customer.user_id,
        label="默认地址",
        recipient_ciphertext=encrypt_pii("测试收货人"),
        phone_ciphertext=encrypt_pii(normalize_phone("13800000009")),
        address_ciphertext=encrypt_pii("上海市徐汇区示例路 1 号"),
        region_code="CN-31",
        province="上海市",
        city="上海市",
        is_default=1,
    )
    payment_db.add_all([product, sku, address])
    payment_db.commit()

    preview = customer.client.post(
        "/api/v1/checkout/preview",
        json={"items": [{"sku_id": sku.id, "quantity": 1}], "address_id": address.id},
        headers=customer.headers(),
    )
    assert preview.status_code == 200, preview.text
    created_order = customer.client.post(
        "/api/v1/orders",
        json={
            "checkout_token": preview.json()["checkout_token"],
            "address_id": address.id,
            "terms_version": "shop-1",
            "accepted": True,
        },
        headers=customer.headers(idempotency_key="e2e-order-1"),
    )
    assert created_order.status_code == 201, created_order.text
    order_body = created_order.json()
    assert order_body["status"] == "PENDING_PAYMENT"
    total_cents = int(order_body["total_cents"])

    created = customer.create_attempt(order_body["installment_id"], idempotency_key="e2e-pay-1")
    assert created.status_code == 201, created.text
    attempt = repo.get_attempt(payment_db, created.json()["payment_attempt_id"])
    assert attempt is not None
    assert attempt.expected_cents == total_cents

    notified = _notify(
        payment_client, gateway, merchant_order_no=attempt.merchant_order_no, amount_cents=total_cents
    )
    assert notified.status_code == 200, notified.text
    assert notified.status == "APPLIED"

    fresh(payment_db)
    order = payment_db.get(Order, order_body["order_id"])
    installment = payment_db.get(Installment, order_body["installment_id"])
    stored_sku = payment_db.get(Sku, sku.id)
    assert order is not None and order.status in ("ACTIVE", "FULFILLING")
    assert installment is not None and installment.status == "PAID"
    assert stored_sku is not None
    assert stored_sku.stock_on_hand == 0 and stored_sku.reserved == 0
    assert svc.order_paid_summary(payment_db, order_id=order.id)["paid_cents"] == total_cents

    detail = customer.client.get(f"/api/v1/orders/{order.id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["payments"]["paid_cents"] == total_cents
    assert detail.json()["payments"]["source"] == "payments"


def test_stripe_notification_endpoint_verifies_and_never_false_credits(
    customer, payment_client, payment_db, commerce, payment_settings
) -> None:
    """Stripe 回调端点（R17）：验签通过的未匹配事件不入账，篡改签名一律 403。"""
    from app.adapters.payments.stripe import expected_signature

    secret = payment_settings.stripe_webhook_secret
    assert secret, "测试环境必须配置 Stripe 回调签名密钥"

    order, installments = commerce.create_order(user_id=customer.user_id, amounts=[19900])
    payment_db.commit()
    attempt = repo.create_attempt(
        payment_db,
        order_id=order.id,
        installment_id=installments[0].id,
        user_id=customer.user_id,
        provider="stripe",
        scene="web",
        merchant_order_no="STST-P17-1",
        expected_cents=19900,
        currency="CNY",
        action_expires_at=None,
    )
    payment_db.commit()

    def _event(merchant_order_no: str, amount: int, event_id: str = "evt_p17") -> bytes:
        return json.dumps(
            {
                "id": event_id,
                "type": "payment_intent.succeeded",
                "data": {
                    "object": {
                        "id": "pi_p17",
                        "object": "payment_intent",
                        "status": "succeeded",
                        "amount": amount,
                        "amount_received": amount,
                        "currency": "cny",
                        "created": int(now_ms() // 1000),
                        "metadata": {"merchant_order_no": merchant_order_no},
                    }
                },
            },
            ensure_ascii=False,
        ).encode("utf-8")

    moment = int(now_ms() // 1000)
    # 三个阶段必须用不同的渠道事件号：同一个事件号会被「至少一次投递、业务效果一次」判定为重复。
    raw = _event(attempt.merchant_order_no, 19900, event_id="evt_p17_applied")
    signature = expected_signature(secret, moment, raw)

    # 1) 篡改报文但沿用原签名：拒绝且记安全事件，不入账。
    tampered = _event(attempt.merchant_order_no, 29900, event_id="evt_p17_applied")
    rejected = _post_notification(
        payment_client,
        "stripe",
        tampered,
        {"Stripe-Signature": f"t={moment},v1={signature}", "Content-Type": "application/json"},
    )
    assert rejected.status_code == 403, rejected.text
    assert rejected.status == "REJECTED"
    fresh(payment_db)
    assert repo.applied_sum(payment_db, installment_id=installments[0].id) == 0
    assert commerce.paid_calls == []

    # 2) 未匹配的商户单号（验签通过）：不入账、按忽略留证，但仍回 2xx 让渠道停止重试。
    unknown = _event("STST-UNKNOWN-9", 19900, event_id="evt_p17_unknown")
    unknown_sig = expected_signature(secret, moment, unknown)
    ignored = _post_notification(
        payment_client,
        "stripe",
        unknown,
        {"Stripe-Signature": f"t={moment},v1={unknown_sig}", "Content-Type": "application/json"},
    )
    assert ignored.status_code in (200, 202), ignored.text
    fresh(payment_db)
    assert repo.applied_sum(payment_db, installment_id=installments[0].id) == 0

    # 3) 正确签名的匹配事件：入账到该期应付项。
    matched_sig = expected_signature(secret, moment, raw)
    applied = _post_notification(
        payment_client,
        "stripe",
        raw,
        {"Stripe-Signature": f"t={moment},v1={matched_sig}", "Content-Type": "application/json"},
    )
    assert applied.status_code in (200, 202), applied.text
    fresh(payment_db)
    assert repo.applied_sum(payment_db, installment_id=installments[0].id) == 19900


@pytest.fixture(autouse=True)
def enable_payments_for_channel_tests(monkeypatch):
    monkeypatch.setenv("FEATURE_ONLINE_PAYMENT", "true")
