"""支付模块仓储：本模块表的读写查询与原子额度语句。

只操作 ``payment_*`` / ``refunds`` / ``refund_items`` / ``reconciliation_*``；
订单与应付项通过 ``repository_bridge.py`` 只读访问商务模块。

金额相关的跨行规则由服务层在写事务中校验（05 文档）：可退额度用条件
``UPDATE`` + ``rowcount`` 判定，不依赖「先读后写」。
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.modules.payments.models import (
    ACTIVE_ATTEMPT_STATUSES,
    PaymentAllocation,
    PaymentAttempt,
    PaymentEvent,
    ReconciliationDifference,
    ReconciliationRun,
    Refund,
    RefundItem,
)
from app.platform.db import affected_rows
from app.platform.ids import new_id, now_ms


# ------------------------------------------------------------------ 支付尝试
def get_attempt(session: Session, attempt_id: str) -> PaymentAttempt | None:
    return session.get(PaymentAttempt, attempt_id)


def get_attempt_by_merchant_order_no(session: Session, merchant_order_no: str) -> PaymentAttempt | None:
    """按预先落库的商户单号关联（回调先到、创建响应后到也用同一单号）。"""
    return session.scalars(
        sa.select(PaymentAttempt).where(PaymentAttempt.merchant_order_no == merchant_order_no)
    ).one_or_none()


def get_attempt_by_provider_transaction(
    session: Session, *, provider: str, provider_transaction_id: str, exclude_id: str | None = None
) -> PaymentAttempt | None:
    """按渠道交易号去重：同一渠道交易号已挂在别的尝试上就是重复收款。"""
    stmt = sa.select(PaymentAttempt).where(
        PaymentAttempt.provider == provider,
        PaymentAttempt.provider_transaction_id == provider_transaction_id,
    )
    if exclude_id:
        stmt = stmt.where(PaymentAttempt.id != exclude_id)
    return session.scalars(stmt).first()


def list_attempts_for_installment(session: Session, installment_id: str) -> list[PaymentAttempt]:
    return list(
        session.scalars(
            sa.select(PaymentAttempt)
            .where(PaymentAttempt.installment_id == installment_id)
            .order_by(PaymentAttempt.created_at.desc())
        )
    )


def list_active_attempts_for_installment(session: Session, installment_id: str) -> list[PaymentAttempt]:
    """同一应付项的活动支付尝试（有真实在途收款风险的那几个）。"""
    return list(
        session.scalars(
            sa.select(PaymentAttempt)
            .where(
                PaymentAttempt.installment_id == installment_id,
                PaymentAttempt.status.in_(ACTIVE_ATTEMPT_STATUSES),
            )
            .order_by(PaymentAttempt.created_at.asc())
        )
    )


def list_attempts_for_order(session: Session, order_id: str, *, limit: int = 100) -> list[PaymentAttempt]:
    return list(
        session.scalars(
            sa.select(PaymentAttempt)
            .where(PaymentAttempt.order_id == order_id)
            .order_by(PaymentAttempt.created_at.desc())
            .limit(min(limit, 200))
        )
    )


def create_attempt(
    session: Session,
    *,
    order_id: str,
    installment_id: str,
    user_id: str,
    provider: str,
    scene: str,
    merchant_order_no: str,
    expected_cents: int,
    currency: str,
    action_expires_at: int | None,
) -> PaymentAttempt:
    """先落库商户单号再下单：渠道超时也能用同一单号查单。"""
    attempt = PaymentAttempt(
        id=new_id("attempt"),
        order_id=order_id,
        installment_id=installment_id,
        user_id=user_id,
        provider=provider,
        scene=scene,
        merchant_order_no=merchant_order_no,
        expected_cents=expected_cents,
        currency=currency,
        status="CREATED",
        action_expires_at=action_expires_at,
    )
    session.add(attempt)
    session.flush()
    return attempt


# ------------------------------------------------------------------ 收款分配
def get_allocation(session: Session, allocation_id: str) -> PaymentAllocation | None:
    return session.get(PaymentAllocation, allocation_id)


def get_allocation_for(
    session: Session, *, payment_id: str, installment_id: str, classification: str
) -> PaymentAllocation | None:
    return session.scalars(
        sa.select(PaymentAllocation).where(
            PaymentAllocation.payment_id == payment_id,
            PaymentAllocation.installment_id == installment_id,
            PaymentAllocation.classification == classification,
        )
    ).one_or_none()


def list_allocations_for_payment(session: Session, payment_id: str) -> list[PaymentAllocation]:
    return list(
        session.scalars(
            sa.select(PaymentAllocation)
            .where(PaymentAllocation.payment_id == payment_id)
            .order_by(PaymentAllocation.created_at.asc())
        )
    )


def list_allocations_for_order(session: Session, order_id: str) -> list[PaymentAllocation]:
    return list(
        session.scalars(
            sa.select(PaymentAllocation)
            .where(PaymentAllocation.order_id == order_id)
            .order_by(PaymentAllocation.created_at.asc())
        )
    )


def create_allocation(
    session: Session,
    *,
    payment_id: str,
    order_id: str,
    installment_id: str,
    amount_cents: int,
    currency: str,
    classification: str,
    note: str | None = None,
) -> PaymentAllocation:
    allocation = PaymentAllocation(
        id=new_id("allocation"),
        payment_id=payment_id,
        order_id=order_id,
        installment_id=installment_id,
        amount_cents=amount_cents,
        currency=currency,
        classification=classification,
        note=note,
    )
    session.add(allocation)
    session.flush()
    return allocation


def applied_sum(session: Session, *, installment_id: str | None = None, order_id: str | None = None) -> int:
    """``paid_cents`` 只计 APPLIED 分配（08 文档）。"""
    stmt = sa.select(sa.func.coalesce(sa.func.sum(PaymentAllocation.amount_cents), 0)).where(
        PaymentAllocation.classification == "APPLIED"
    )
    if installment_id is not None:
        stmt = stmt.where(PaymentAllocation.installment_id == installment_id)
    if order_id is not None:
        stmt = stmt.where(PaymentAllocation.order_id == order_id)
    return int(session.scalar(stmt) or 0)


def refund_quota(session: Session, allocation_id: str) -> dict[str, int]:
    """可退额度视图：原收款金额 - 已成功退款 - 预留中退款。"""
    row = session.get(PaymentAllocation, allocation_id)
    if row is None:
        return {"amount_cents": 0, "refunded_cents": 0, "reserved_refund_cents": 0, "available_cents": 0}
    return {
        "amount_cents": row.amount_cents,
        "refunded_cents": row.refunded_cents,
        "reserved_refund_cents": row.reserved_refund_cents,
        "available_cents": row.amount_cents - row.refunded_cents - row.reserved_refund_cents,
    }


def reserve_refund_quota(session: Session, *, allocation_id: str, amount_cents: int) -> int:
    """原子预留可退额度，返回受影响行数（0 表示额度不足或状态不允许）。

    条件更新在数据库层保证「有效预留 + 已退款 ≤ 原收款」，两个客服并发退同一笔
    款时只有一个能把 ``rowcount`` 拿到 1。
    """
    if amount_cents <= 0:
        return 0
    result = session.execute(
        sa.update(PaymentAllocation)
        .where(
            PaymentAllocation.id == allocation_id,
            PaymentAllocation.classification.in_(("APPLIED", "OVERPAYMENT")),
            PaymentAllocation.amount_cents
            - PaymentAllocation.refunded_cents
            - PaymentAllocation.reserved_refund_cents
            >= amount_cents,
        )
        .values(
            reserved_refund_cents=PaymentAllocation.reserved_refund_cents + amount_cents,
            version=PaymentAllocation.version + 1,
            updated_at=now_ms(),
        )
    )
    return affected_rows(result)


def settle_refund_quota(session: Session, *, allocation_id: str, amount_cents: int) -> int:
    """退款成功：预留转为已退款，返回受影响行数（0 表示没有对应预留）。"""
    result = session.execute(
        sa.update(PaymentAllocation)
        .where(
            PaymentAllocation.id == allocation_id,
            PaymentAllocation.reserved_refund_cents >= amount_cents,
            PaymentAllocation.refunded_cents + amount_cents <= PaymentAllocation.amount_cents,
        )
        .values(
            reserved_refund_cents=PaymentAllocation.reserved_refund_cents - amount_cents,
            refunded_cents=PaymentAllocation.refunded_cents + amount_cents,
            version=PaymentAllocation.version + 1,
            updated_at=now_ms(),
        )
    )
    return affected_rows(result)


def release_refund_quota(session: Session, *, allocation_id: str, amount_cents: int) -> int:
    """明确失败且渠道证明未退才释放预留。"""
    result = session.execute(
        sa.update(PaymentAllocation)
        .where(
            PaymentAllocation.id == allocation_id,
            PaymentAllocation.reserved_refund_cents >= amount_cents,
        )
        .values(
            reserved_refund_cents=PaymentAllocation.reserved_refund_cents - amount_cents,
            version=PaymentAllocation.version + 1,
            updated_at=now_ms(),
        )
    )
    return affected_rows(result)


def refunded_applied_sum(session: Session, *, order_id: str) -> int:
    """已成功退款且指向 APPLIED 的金额；多付款退款不冲减正常实收。"""
    stmt = (
        sa.select(sa.func.coalesce(sa.func.sum(Refund.amount_cents), 0))
        .select_from(Refund)
        .join(PaymentAllocation, PaymentAllocation.id == Refund.allocation_id)
        .where(
            Refund.order_id == order_id,
            Refund.status == "SUCCEEDED",
            PaymentAllocation.classification == "APPLIED",
        )
    )
    return int(session.scalar(stmt) or 0)


# ------------------------------------------------------------------ 回调事件
def get_event(session: Session, *, provider: str, event_id: str) -> PaymentEvent | None:
    return session.scalars(
        sa.select(PaymentEvent).where(
            PaymentEvent.provider == provider, PaymentEvent.event_id == event_id
        )
    ).one_or_none()


def create_event(
    session: Session,
    *,
    provider: str,
    event_id: str,
    event_type: str,
    payload_digest: str,
    payload_path: str,
    verified: bool,
    attempt: PaymentAttempt | None = None,
    merchant_order_no: str | None = None,
    amount_cents: int | None = None,
    currency: str | None = None,
    provider_transaction_id: str | None = None,
    processing_result: str = "PENDING",
    rejection_reason: str | None = None,
) -> PaymentEvent:
    """写入回调事件（只追加）。

    ``merchant_order_no`` 优先取显式传入值：未登记商户单号的异常收款也要能按单号
    检索与对账（此时没有对应的 ``payment_attempt``）。
    """
    event = PaymentEvent(
        id=new_id("event"),
        provider=provider,
        event_id=event_id,
        event_type=event_type,
        payment_attempt_id=attempt.id if attempt else None,
        merchant_order_no=merchant_order_no or (attempt.merchant_order_no if attempt else None),
        provider_transaction_id=provider_transaction_id,
        amount_cents=amount_cents,
        currency=currency,
        payload_digest=payload_digest,
        payload_path=payload_path,
        verified=1 if verified else 0,
        processing_result=processing_result,
        rejection_reason=rejection_reason,
        received_at=now_ms(),
    )
    session.add(event)
    return event


def list_events_for_attempt(session: Session, attempt_id: str) -> list[PaymentEvent]:
    return list(
        session.scalars(
            sa.select(PaymentEvent)
            .where(PaymentEvent.payment_attempt_id == attempt_id)
            .order_by(PaymentEvent.received_at.asc())
        )
    )


# ------------------------------------------------------------------ 退款
def get_refund(session: Session, refund_id: str) -> Refund | None:
    return session.get(Refund, refund_id)


def get_refund_by_no(session: Session, refund_no: str) -> Refund | None:
    return session.scalars(sa.select(Refund).where(Refund.refund_no == refund_no)).one_or_none()


def list_refunds_for_order(session: Session, order_id: str) -> list[Refund]:
    return list(
        session.scalars(
            sa.select(Refund).where(Refund.order_id == order_id).order_by(Refund.created_at.asc())
        )
    )


def list_succeeded_attempts_in_range(
    session: Session, *, provider: str, start_ms: int, end_ms: int
) -> list[PaymentAttempt]:
    """对账用：某渠道在账单日内确认成功的支付尝试。"""
    return list(
        session.scalars(
            sa.select(PaymentAttempt).where(
                PaymentAttempt.provider == provider,
                PaymentAttempt.status == "SUCCEEDED",
                PaymentAttempt.verified_at.is_not(None),
                PaymentAttempt.verified_at >= start_ms,
                PaymentAttempt.verified_at < end_ms,
            )
        )
    )


def list_succeeded_refunds_in_range(
    session: Session, *, provider: str, start_ms: int, end_ms: int
) -> list[Refund]:
    """对账用：某渠道在账单日内成功的退款。"""
    return list(
        session.scalars(
            sa.select(Refund).where(
                Refund.provider == provider,
                Refund.status == "SUCCEEDED",
                Refund.succeeded_at.is_not(None),
                Refund.succeeded_at >= start_ms,
                Refund.succeeded_at < end_ms,
            )
        )
    )


def list_refunds_for_payment(session: Session, payment_id: str) -> list[Refund]:
    return list(
        session.scalars(
            sa.select(Refund).where(Refund.payment_id == payment_id).order_by(Refund.created_at.asc())
        )
    )


def list_refunds_for_allocation(session: Session, allocation_id: str) -> list[Refund]:
    return list(
        session.scalars(
            sa.select(Refund)
            .where(Refund.allocation_id == allocation_id)
            .order_by(Refund.created_at.asc())
        )
    )


def create_refund(
    session: Session,
    *,
    refund_no: str,
    order_id: str,
    payment_id: str,
    allocation_id: str,
    installment_id: str,
    user_id: str,
    provider: str,
    amount_cents: int,
    currency: str,
    reason: str,
    kind: str = "normal",
    requested_by: str | None = None,
    idempotency_key: str | None = None,
) -> Refund:
    refund = Refund(
        id=new_id("refund"),
        refund_no=refund_no,
        order_id=order_id,
        payment_id=payment_id,
        allocation_id=allocation_id,
        installment_id=installment_id,
        user_id=user_id,
        provider=provider,
        amount_cents=amount_cents,
        currency=currency,
        status="REQUESTED",
        kind=kind,
        reason=reason[:400],
        requested_by=requested_by,
        idempotency_key=idempotency_key,
    )
    session.add(refund)
    session.flush()
    return refund


def create_refund_item(
    session: Session,
    *,
    refund_id: str,
    kind: str,
    amount_cents: int,
    order_item_id: str | None = None,
    description: str | None = None,
) -> RefundItem:
    item = RefundItem(
        id=new_id("refund"),
        refund_id=refund_id,
        order_item_id=order_item_id,
        kind=kind,
        amount_cents=amount_cents,
        description=description,
    )
    session.add(item)
    session.flush()
    return item


def list_refund_items(session: Session, refund_id: str) -> list[RefundItem]:
    return list(
        session.scalars(sa.select(RefundItem).where(RefundItem.refund_id == refund_id)).all()
    )


def refund_item_sums(session: Session, refund_id: str) -> dict[str, int]:
    """按类型汇总退款分摊（goods/shipping/overpayment/discount）。"""
    sums = {kind: 0 for kind in ("goods", "shipping", "overpayment", "discount")}
    for item in list_refund_items(session, refund_id):
        sums[item.kind] = sums.get(item.kind, 0) + item.amount_cents
    return sums


def refund_item_total(session: Session, refund_id: str) -> int:
    return int(
        session.scalar(
            sa.select(sa.func.coalesce(sa.func.sum(RefundItem.amount_cents), 0)).where(
                RefundItem.refund_id == refund_id
            )
        )
        or 0
    )


# ------------------------------------------------------------------ 对账
def latest_reconciliation_revision(session: Session, *, provider: str, bill_date: str) -> int:
    return int(
        session.scalar(
            sa.select(sa.func.coalesce(sa.func.max(ReconciliationRun.revision), 0)).where(
                ReconciliationRun.provider == provider, ReconciliationRun.bill_date == bill_date
            )
        )
        or 0
    )


def create_reconciliation_run(
    session: Session,
    *,
    provider: str,
    bill_date: str,
    revision: int,
    actor_id: str | None,
) -> ReconciliationRun:
    run = ReconciliationRun(
        id=new_id("reconciliation"),
        provider=provider,
        bill_date=bill_date,
        revision=revision,
        status="RUNNING",
        actor_id=actor_id,
    )
    session.add(run)
    session.flush()
    return run


def create_difference(
    session: Session,
    *,
    run: ReconciliationRun,
    difference_type: str,
    merchant_order_no: str | None = None,
    transaction_id: str | None = None,
    refund_no: str | None = None,
    channel_amount_cents: int | None = None,
    local_amount_cents: int | None = None,
    fee_cents: int | None = None,
    evidence: dict[str, Any] | None = None,
) -> ReconciliationDifference:
    difference = ReconciliationDifference(
        id=new_id("reconciliation"),
        run_id=run.id,
        provider=run.provider,
        bill_date=run.bill_date,
        difference_type=difference_type,
        status="OPEN",
        merchant_order_no=merchant_order_no,
        transaction_id=transaction_id,
        refund_no=refund_no,
        channel_amount_cents=channel_amount_cents,
        local_amount_cents=local_amount_cents,
        fee_cents=fee_cents,
        evidence=evidence,
    )
    session.add(difference)
    session.flush()
    return difference


def get_difference(session: Session, difference_id: str) -> ReconciliationDifference | None:
    return session.get(ReconciliationDifference, difference_id)


def list_differences(session: Session, run_id: str) -> list[ReconciliationDifference]:
    return list(
        session.scalars(
            sa.select(ReconciliationDifference)
            .where(ReconciliationDifference.run_id == run_id)
            .order_by(ReconciliationDifference.created_at.asc())
        )
    )


def open_difference_count(session: Session, *, provider: str | None = None) -> int:
    stmt = sa.select(sa.func.count()).select_from(ReconciliationDifference).where(
        ReconciliationDifference.status != "RESOLVED"
    )
    if provider:
        stmt = stmt.where(ReconciliationDifference.provider == provider)
    return int(session.scalar(stmt) or 0)


__all__ = [
    "applied_sum",
    "create_allocation",
    "create_attempt",
    "create_difference",
    "create_event",
    "create_reconciliation_run",
    "create_refund",
    "create_refund_item",
    "get_allocation",
    "get_allocation_for",
    "get_attempt",
    "get_attempt_by_merchant_order_no",
    "get_attempt_by_provider_transaction",
    "get_difference",
    "get_event",
    "get_refund",
    "get_refund_by_no",
    "latest_reconciliation_revision",
    "list_active_attempts_for_installment",
    "list_allocations_for_order",
    "list_allocations_for_payment",
    "list_attempts_for_installment",
    "list_attempts_for_order",
    "list_differences",
    "list_events_for_attempt",
    "list_refund_items",
    "list_refunds_for_allocation",
    "list_refunds_for_order",
    "list_refunds_for_payment",
    "list_succeeded_attempts_in_range",
    "list_succeeded_refunds_in_range",
    "open_difference_count",
    "refund_item_sums",
    "refund_item_total",
    "refund_quota",
    "refunded_applied_sum",
    "release_refund_quota",
    "reserve_refund_quota",
    "settle_refund_quota",
]
