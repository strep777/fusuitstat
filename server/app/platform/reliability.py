"""可靠任务：outbox 事件、幂等键、消费去重、限流桶、审计日志。

写业务记录与写 outbox 必须在同一事务；消费用 ``event_id`` + 消费者名去重，
实现“至少一次投递、业务效果一次”（04 文档）。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.ids import new_id, now_ms
from app.platform.orm import Base, PkMixin

OUTBOX_STATUSES = ("PENDING", "CLAIMED", "DONE", "DEAD")


class OutboxEvent(PkMixin, Base):
    """事务性 outbox。``event_id`` 全局唯一，租约到期可被重新领取。"""

    __tablename__ = "outbox_events"
    id_kind = "event"

    event_id: Mapped[str] = mapped_column(sa.String(64), unique=True, nullable=False)
    event_type: Mapped[str] = mapped_column(sa.String(80), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(sa.String(48), nullable=False)
    aggregate_version: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=1)
    payload: Mapped[dict[str, Any]] = mapped_column(sa.JSON, nullable=False, default=dict)
    available_at: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, default=now_ms)
    claimed_until: Mapped[int | None] = mapped_column(sa.BigInteger)
    claimed_by: Mapped[str | None] = mapped_column(sa.String(80))
    attempts: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(sa.String(400))
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False, default="PENDING")
    created_at: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, default=now_ms)
    processed_at: Mapped[int | None] = mapped_column(sa.BigInteger)

    __table_args__ = (
        sa.CheckConstraint("status IN ('PENDING','CLAIMED','DONE','DEAD')", name="ck_outbox_status"),
        sa.Index("ix_outbox_scan", "status", "available_at"),
        sa.Index("ix_outbox_aggregate", "aggregate_id"),
    )


class ConsumedEvent(Base):
    """消费去重记录，与消费效果同事务提交。"""

    __tablename__ = "consumed_events"

    consumer_name: Mapped[str] = mapped_column(sa.String(80), primary_key=True)
    event_id: Mapped[str] = mapped_column(sa.String(64), primary_key=True)
    processed_at: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, default=now_ms)


class IdempotencyKey(PkMixin, Base):
    """幂等登记。同主体+动作+键唯一，保存请求规范化哈希；同键不同请求 409。"""

    __tablename__ = "idempotency_keys"

    actor_id: Mapped[str] = mapped_column(sa.String(48), nullable=False)
    operation: Mapped[str] = mapped_column(sa.String(80), nullable=False)
    key: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    request_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(sa.String(48))
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False, default="IN_PROGRESS")
    response_snapshot: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON)
    created_at: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, default=now_ms)
    expires_at: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)

    __table_args__ = (
        sa.UniqueConstraint("actor_id", "operation", "key", name="uq_idempotency_actor_op_key"),
        sa.CheckConstraint("status IN ('IN_PROGRESS','COMPLETED')", name="ck_idempotency_status"),
    )


class RateLimitBucket(Base):
    """持久化限流桶。不能靠单进程内存（10 文档）。"""

    __tablename__ = "rate_limit_buckets"

    scope: Mapped[str] = mapped_column(sa.String(40), primary_key=True)
    subject_hash: Mapped[str] = mapped_column(sa.String(64), primary_key=True)
    window_start: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    expires_at: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)


class AuditLog(Base):
    """操作审计。追加写，敏感值不入记录；定期导出防篡改存储。"""

    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(sa.String(48), primary_key=True, default=lambda: new_id("audit"))
    actor_type: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(sa.String(48))
    actor_label: Mapped[str | None] = mapped_column(sa.String(120))
    action: Mapped[str] = mapped_column(sa.String(80), nullable=False)
    resource_type: Mapped[str] = mapped_column(sa.String(60), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(sa.String(48))
    before_digest: Mapped[str | None] = mapped_column(sa.String(64))
    after_digest: Mapped[str | None] = mapped_column(sa.String(64))
    reason: Mapped[str | None] = mapped_column(sa.String(400))
    trace_id: Mapped[str | None] = mapped_column(sa.String(48))
    ip_hash: Mapped[str | None] = mapped_column(sa.String(64))
    occurred_at: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, default=now_ms)

    __table_args__ = (
        sa.Index("ix_audit_resource", "resource_type", "resource_id"),
        sa.Index("ix_audit_occurred", "occurred_at"),
        sa.Index("ix_audit_actor", "actor_id", "occurred_at"),
    )


def digest(payload: Any) -> str:
    """稳定的内容摘要，用于审计前后快照与幂等请求哈希。"""
    if isinstance(payload, bytes):
        raw = payload
    else:
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def enqueue(
    session: sa.orm.Session,
    *,
    event_type: str,
    aggregate_id: str,
    payload: dict[str, Any] | None = None,
    aggregate_version: int = 1,
    available_at: int | None = None,
    event_id: str | None = None,
) -> OutboxEvent:
    """在调用方事务内写入 outbox 事件。"""
    event = OutboxEvent(
        event_id=event_id or new_id("event"),
        event_type=event_type,
        aggregate_id=aggregate_id,
        aggregate_version=aggregate_version,
        payload=payload or {},
        available_at=available_at if available_at is not None else now_ms(),
    )
    session.add(event)
    return event
