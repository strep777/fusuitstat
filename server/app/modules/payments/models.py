"""支付模块：支付尝试、收款分配、回调事件、退款、退款分摊与对账。

拥有的数据：``payment_attempts``、``payment_allocations``、``payment_events``、
``refunds``、``refund_items``、``reconciliation_runs``、``reconciliation_differences``。

不创建也不修改 ``orders``、``order_items``、``installments``、``after_sales``、
``shipments``、``invoices``（商务模块拥有）。``order_id`` / ``installment_id``
是跨模块引用：按 04 文档模块之间不互相导入 ORM 表，因此**不建数据库外键**，
由服务层在写事务内通过 ``repository_bridge.py`` 校验归属与金额。

外键策略（05 文档「财务/订单数据禁止级联删除」）：本模块内部的财务外键一律
``ondelete="RESTRICT"``，任何删除都会被数据库拒绝，账本、回调与分配只追加。
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.ids import new_id, now_ms
from app.platform.orm import Base, PkMixin, TimestampMixin, VersionMixin

# 状态值域与 08 文档「分离状态」一致。
PAYMENT_ATTEMPT_STATUSES = (
    "CREATED",
    "PENDING",
    "UNKNOWN",
    "SUCCEEDED",
    "CLOSED",
    "FAILED",
)
#: 仍可能产生真实收款、需要查单或关闭的活动状态。
ACTIVE_ATTEMPT_STATUSES = ("CREATED", "PENDING", "UNKNOWN")
ALLOCATION_CLASSIFICATIONS = ("APPLIED", "OVERPAYMENT", "QUARANTINED")
REFUND_STATUSES = (
    "REQUESTED",
    "APPROVED",
    "PROCESSING",
    "UNKNOWN",
    "SUCCEEDED",
    "FAILED",
    "REJECTED",
)
#: 占用可退额度、但渠道结果尚未落定的退款状态。
REFUND_RESERVING_STATUSES = ("APPROVED", "PROCESSING", "UNKNOWN")
REFUND_ITEM_KINDS = ("goods", "shipping", "overpayment", "discount")
PAYMENT_EVENT_RESULTS = (
    "PENDING",
    "APPLIED",
    "OVERPAYMENT",
    "QUARANTINED",
    "UNKNOWN_ORDER",
    "IGNORED",
    "REJECTED",
)
RECONCILIATION_RUN_STATUSES = ("RUNNING", "COMPLETED", "FAILED")
DIFFERENCE_TYPES = (
    "CHANNEL_ONLY",
    "LOCAL_ONLY",
    "AMOUNT_MISMATCH",
    "DUPLICATE_PAYMENT",
    "REFUND_MISMATCH",
    "FEE_MISMATCH",
)
DIFFERENCE_STATUSES = ("OPEN", "INVESTIGATING", "RESOLVED")
DIFFERENCE_RESOLUTIONS = (
    "CONFIRMED_CHANNEL",
    "CONFIRMED_LOCAL",
    "MANUAL_ADJUSTED",
    "FALSE_POSITIVE",
    "ESCALATED",
)


class PaymentAttempt(PkMixin, TimestampMixin, VersionMixin, Base):
    """一次支付尝试。``merchant_order_no`` 唯一且在下单前落库。

    渠道交易号在非空时按 provider 唯一（SQLite 唯一约束允许多个 NULL），
    用于「按渠道交易号去重」。成功记录永不改回失败（08 文档）。
    """

    __tablename__ = "payment_attempts"
    id_kind = "attempt"

    order_id: Mapped[str] = mapped_column(sa.String(48), nullable=False, index=True)
    installment_id: Mapped[str] = mapped_column(sa.String(48), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(sa.String(48), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    scene: Mapped[str] = mapped_column(sa.String(20), nullable=False, default="native")
    merchant_order_no: Mapped[str] = mapped_column(sa.String(64), nullable=False, unique=True)
    provider_transaction_id: Mapped[str | None] = mapped_column(sa.String(64))
    expected_cents: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    currency: Mapped[str] = mapped_column(sa.String(3), nullable=False, default="CNY")
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False, default="CREATED")
    action: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON)
    action_expires_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    last_queried_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    query_attempts: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    verified_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    closed_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    closed_reason: Mapped[str | None] = mapped_column(sa.String(200))
    failure_reason: Mapped[str | None] = mapped_column(sa.String(200))

    __table_args__ = (
        sa.UniqueConstraint("provider", "provider_transaction_id", name="uq_attempt_provider_txn"),
        sa.CheckConstraint(
            "status IN ('CREATED','PENDING','UNKNOWN','SUCCEEDED','CLOSED','FAILED')",
            name="ck_attempt_status",
        ),
        sa.CheckConstraint("expected_cents >= 0", name="ck_attempt_expected_nonneg"),
        sa.Index("ix_attempt_installment_status", "installment_id", "status"),
        sa.Index("ix_attempt_order", "order_id", "created_at"),
    )


class PaymentAllocation(PkMixin, TimestampMixin, VersionMixin, Base):
    """已核验收款的分配。``classification`` 决定是否计入订单实收。

    ``refunded_cents`` 与 ``reserved_refund_cents`` 是可退额度的两个计数器，
    退款申请用条件 ``UPDATE`` 原子预留（``rowcount`` 判定），避免两个客服各退一次：
    ``amount_cents - refunded_cents - reserved_refund_cents >= 申请金额``。

    「同一笔成功款对同一应付项只分配一次」由 ``payment_events`` 的
    ``UNIQUE(provider,event_id)`` 去重屏障在同一事务内保证（08 文档），
    因此这里不额外加 ``UNIQUE(payment_id,installment_id,classification)``：
    同一尝试可能收到多笔异常通知，每笔都要各自留下一条 ``QUARANTINED`` 记录。
    """

    __tablename__ = "payment_allocations"
    id_kind = "allocation"

    payment_id: Mapped[str] = mapped_column(
        sa.ForeignKey("payment_attempts.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    order_id: Mapped[str] = mapped_column(sa.String(48), nullable=False, index=True)
    installment_id: Mapped[str] = mapped_column(sa.String(48), nullable=False, index=True)
    amount_cents: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    currency: Mapped[str] = mapped_column(sa.String(3), nullable=False, default="CNY")
    classification: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    refunded_cents: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    reserved_refund_cents: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    note: Mapped[str | None] = mapped_column(sa.String(200))

    __table_args__ = (
        sa.CheckConstraint(
            "classification IN ('APPLIED','OVERPAYMENT','QUARANTINED')", name="ck_allocation_class"
        ),
        sa.CheckConstraint("amount_cents >= 0", name="ck_allocation_amount_nonneg"),
        sa.CheckConstraint("refunded_cents >= 0", name="ck_allocation_refunded_nonneg"),
        sa.CheckConstraint("reserved_refund_cents >= 0", name="ck_allocation_reserved_nonneg"),
        sa.CheckConstraint(
            "refunded_cents + reserved_refund_cents <= amount_cents", name="ck_allocation_refund_limit"
        ),
        sa.Index("ix_allocation_order_class", "order_id", "classification"),
    )


class PaymentEvent(PkMixin, Base):
    """渠道回调/查单事件留证，只追加。

    只保存 ``payload_digest`` 与必要元数据；验签所需的原始字节写在
    ``storage/payment_events/<digest>.payload``，库里只记路径（08 文档：
    不存明文隐私全文，但要能复核验签）。``UNIQUE(provider, event_id)``
    是「同一通知只产生一次收款效果」的数据库级保证；渠道无事件号时用
    规范化业务键（provider+渠道交易号+商户单号+金额）。
    """

    __tablename__ = "payment_events"
    id_kind = "event"

    provider: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    event_id: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    event_type: Mapped[str] = mapped_column(sa.String(40), nullable=False, default="payment.succeeded")
    payment_attempt_id: Mapped[str | None] = mapped_column(sa.String(48), index=True)
    merchant_order_no: Mapped[str | None] = mapped_column(sa.String(64), index=True)
    provider_transaction_id: Mapped[str | None] = mapped_column(sa.String(64))
    amount_cents: Mapped[int | None] = mapped_column(sa.Integer)
    currency: Mapped[str | None] = mapped_column(sa.String(3))
    payload_digest: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    payload_path: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    verified: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=1)
    processing_result: Mapped[str] = mapped_column(sa.String(20), nullable=False, default="PENDING")
    rejection_reason: Mapped[str | None] = mapped_column(sa.String(200))
    received_at: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, default=now_ms)

    __table_args__ = (
        sa.UniqueConstraint("provider", "event_id", name="uq_payment_event_provider_event"),
        sa.CheckConstraint("verified IN (0,1)", name="ck_payment_event_verified"),
        sa.CheckConstraint("amount_cents IS NULL OR amount_cents >= 0", name="ck_payment_event_amount"),
        sa.Index("ix_payment_event_received", "received_at"),
    )


class Refund(PkMixin, TimestampMixin, VersionMixin, Base):
    """退款单。``refund_no`` 唯一，重试使用同一退款号。

    可退额 = 原收款金额 - 已成功退款 - PROCESSING/UNKNOWN/APPROVED 中的预留退款额；
    预留落在 ``payment_allocations`` 上（同事务条件更新）。失败或未知不得对外
    宣称「钱已退」。
    """

    __tablename__ = "refunds"
    id_kind = "refund"

    refund_no: Mapped[str] = mapped_column(sa.String(64), nullable=False, unique=True)
    order_id: Mapped[str] = mapped_column(sa.String(48), nullable=False, index=True)
    payment_id: Mapped[str] = mapped_column(
        sa.ForeignKey("payment_attempts.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    allocation_id: Mapped[str] = mapped_column(
        sa.ForeignKey("payment_allocations.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    installment_id: Mapped[str] = mapped_column(sa.String(48), nullable=False)
    user_id: Mapped[str] = mapped_column(sa.String(48), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    amount_cents: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    currency: Mapped[str] = mapped_column(sa.String(3), nullable=False, default="CNY")
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False, default="REQUESTED")
    kind: Mapped[str] = mapped_column(sa.String(20), nullable=False, default="normal")
    reason: Mapped[str] = mapped_column(sa.String(400), nullable=False, default="")
    provider_refund_id: Mapped[str | None] = mapped_column(sa.String(64))
    requested_by: Mapped[str | None] = mapped_column(sa.String(48))
    approved_by: Mapped[str | None] = mapped_column(sa.String(48))
    approved_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    reserved_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    succeeded_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    resolved_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    failure_reason: Mapped[str | None] = mapped_column(sa.String(200))
    idempotency_key: Mapped[str | None] = mapped_column(sa.String(120))

    __table_args__ = (
        sa.UniqueConstraint("provider", "provider_refund_id", name="uq_refund_provider_txn"),
        sa.CheckConstraint(
            "status IN ('REQUESTED','APPROVED','PROCESSING','UNKNOWN','SUCCEEDED','FAILED','REJECTED')",
            name="ck_refund_status",
        ),
        sa.CheckConstraint("amount_cents >= 0", name="ck_refund_amount_nonneg"),
        sa.Index("ix_refund_order_status", "order_id", "status"),
    )


class RefundItem(PkMixin, Base):
    """退款分摊到商品行、运费、多付款或已用优惠；各项之和必须等于退款额。"""

    __tablename__ = "refund_items"

    refund_id: Mapped[str] = mapped_column(
        sa.ForeignKey("refunds.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    order_item_id: Mapped[str | None] = mapped_column(sa.String(48))
    kind: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    amount_cents: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    description: Mapped[str | None] = mapped_column(sa.String(200))
    created_at: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, default=now_ms)

    __table_args__ = (
        sa.UniqueConstraint("refund_id", "order_item_id", "kind", name="uq_refund_item_line"),
        sa.CheckConstraint("kind IN ('goods','shipping','overpayment','discount')", name="ck_refund_item_kind"),
        sa.CheckConstraint("amount_cents >= 0", name="ck_refund_item_amount_nonneg"),
    )


class ReconciliationRun(PkMixin, Base):
    """一次对账执行。``UNIQUE(provider, bill_date, revision)`` 保留重新对账历史。"""

    __tablename__ = "reconciliation_runs"
    id_kind = "reconciliation"

    provider: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    bill_date: Mapped[str] = mapped_column(sa.String(10), nullable=False)
    revision: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False, default="RUNNING")
    bill_file_hash: Mapped[str | None] = mapped_column(sa.String(64))
    bill_payload_path: Mapped[str | None] = mapped_column(sa.String(200))
    channel_total_cents: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    local_total_cents: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    channel_refund_cents: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    local_refund_cents: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    difference_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    actor_id: Mapped[str | None] = mapped_column(sa.String(48))
    failure_reason: Mapped[str | None] = mapped_column(sa.String(200))
    started_at: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, default=now_ms)
    finished_at: Mapped[int | None] = mapped_column(sa.BigInteger)

    __table_args__ = (
        sa.UniqueConstraint("provider", "bill_date", "revision", name="uq_reconciliation_revision"),
        sa.CheckConstraint("status IN ('RUNNING','COMPLETED','FAILED')", name="ck_reconciliation_status"),
        sa.CheckConstraint("revision >= 1", name="ck_reconciliation_revision_positive"),
        sa.Index("ix_reconciliation_bill", "provider", "bill_date"),
    )


class ReconciliationDifference(PkMixin, Base):
    """对账差异。逐条人工处置并写理由，**不允许批量「标记一致」**。"""

    __tablename__ = "reconciliation_differences"
    id_kind = "reconciliation"

    run_id: Mapped[str] = mapped_column(
        sa.ForeignKey("reconciliation_runs.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    bill_date: Mapped[str] = mapped_column(sa.String(10), nullable=False)
    difference_type: Mapped[str] = mapped_column(sa.String(24), nullable=False)
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False, default="OPEN")
    merchant_order_no: Mapped[str | None] = mapped_column(sa.String(64))
    transaction_id: Mapped[str | None] = mapped_column(sa.String(64))
    refund_no: Mapped[str | None] = mapped_column(sa.String(64))
    channel_amount_cents: Mapped[int | None] = mapped_column(sa.Integer)
    local_amount_cents: Mapped[int | None] = mapped_column(sa.Integer)
    fee_cents: Mapped[int | None] = mapped_column(sa.Integer)
    evidence: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON)
    resolution: Mapped[str | None] = mapped_column(sa.String(24))
    resolution_reason: Mapped[str | None] = mapped_column(sa.String(400))
    resolved_by: Mapped[str | None] = mapped_column(sa.String(48))
    resolved_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    created_at: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, default=now_ms)

    __table_args__ = (
        sa.CheckConstraint(
            "difference_type IN ('CHANNEL_ONLY','LOCAL_ONLY','AMOUNT_MISMATCH',"
            "'DUPLICATE_PAYMENT','REFUND_MISMATCH','FEE_MISMATCH')",
            name="ck_difference_type",
        ),
        sa.CheckConstraint("status IN ('OPEN','INVESTIGATING','RESOLVED')", name="ck_difference_status"),
        sa.CheckConstraint(
            "resolution IS NULL OR resolution IN ('CONFIRMED_CHANNEL','CONFIRMED_LOCAL',"
            "'MANUAL_ADJUSTED','FALSE_POSITIVE','ESCALATED')",
            name="ck_difference_resolution",
        ),
        sa.Index("ix_difference_run_status", "run_id", "status"),
    )


__all__ = [
    "ACTIVE_ATTEMPT_STATUSES",
    "ALLOCATION_CLASSIFICATIONS",
    "DIFFERENCE_RESOLUTIONS",
    "DIFFERENCE_STATUSES",
    "DIFFERENCE_TYPES",
    "PAYMENT_ATTEMPT_STATUSES",
    "PAYMENT_EVENT_RESULTS",
    "REFUND_ITEM_KINDS",
    "REFUND_RESERVING_STATUSES",
    "REFUND_STATUSES",
    "RECONCILIATION_RUN_STATUSES",
    "PaymentAllocation",
    "PaymentAttempt",
    "PaymentEvent",
    "ReconciliationDifference",
    "ReconciliationRun",
    "Refund",
    "RefundItem",
    "new_id",
]
