"""积分模块数据访问：账户、规则、奖励、账本、预约、调整与申诉。

只做查询与原子写，不含业务判断；跨行规则由 ``service.py`` 在写事务中检查（05 文档）。
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.modules.loyalty.models import (
    PointAccount,
    PointAdjustment,
    PointAppeal,
    PointAward,
    PointLedger,
    PointReservation,
    PointRule,
)
from app.platform.db import affected_rows
from app.platform.errors import AppError
from app.platform.ids import new_id, now_ms
from app.platform.reliability import ConsumedEvent, OutboxEvent


# ------------------------------------------------------------------ 账户
def get_account(session: Session, *, user_id: str) -> PointAccount | None:
    return session.scalars(
        sa.select(PointAccount).where(PointAccount.user_id == user_id)
    ).one_or_none()


def get_account_by_id(session: Session, *, account_id: str) -> PointAccount | None:
    return session.get(PointAccount, account_id)


def ensure_account(session: Session, *, user_id: str) -> PointAccount:
    """幂等取得账户。用 ``ON CONFLICT DO NOTHING`` 避免并发建户冲突。"""
    moment = now_ms()
    session.execute(
        sqlite_insert(PointAccount)
        .values(
            id=new_id("account"),
            user_id=user_id,
            posted_balance=0,
            reserved_points=0,
            version=1,
            created_at=moment,
            updated_at=moment,
        )
        .on_conflict_do_nothing(index_elements=["user_id"])
    )
    account = get_account(session, user_id=user_id)
    if account is None:  # pragma: no cover - 只可能是数据库不可用
        raise AppError("INTERNAL_ERROR", "积分账户暂时不可用，请稍后重试。")
    return account


def add_balance(session: Session, *, account_id: str, delta: int) -> int:
    """原子累加账户缓存（可为负）。返回受影响行数。"""
    result = session.execute(
        sa.update(PointAccount)
        .where(PointAccount.id == account_id)
        .values(
            posted_balance=PointAccount.posted_balance + delta,
            version=PointAccount.version + 1,
            updated_at=now_ms(),
        )
    )
    return affected_rows(result)


def add_reserved(session: Session, *, user_id: str, points: int) -> int:
    """条件冻结：仅当可用额度足够时成功（防双标签页同时花同一积分）。"""
    result = session.execute(
        sa.update(PointAccount)
        .where(
            PointAccount.user_id == user_id,
            PointAccount.posted_balance - PointAccount.reserved_points >= points,
        )
        .values(reserved_points=PointAccount.reserved_points + points, version=PointAccount.version + 1)
    )
    return affected_rows(result)


def release_reserved(session: Session, *, account_id: str, points: int) -> int:
    result = session.execute(
        sa.update(PointAccount)
        .where(PointAccount.id == account_id)
        .values(
            reserved_points=sa.case(
                (PointAccount.reserved_points >= points, PointAccount.reserved_points - points),
                else_=0,
            ),
            version=PointAccount.version + 1,
        )
    )
    return affected_rows(result)


def ledger_total(session: Session, *, user_id: str) -> int:
    """账本合计。账户缓存必须等于该值（用于一致性与对账校验）。"""
    return int(
        session.scalar(
            sa.select(sa.func.coalesce(sa.func.sum(PointLedger.delta), 0)).where(
                PointLedger.user_id == user_id
            )
        )
        or 0
    )


# ------------------------------------------------------------------ 账本
def append_ledger(session: Session, **values: Any) -> int:
    """追加流水。返回 1 表示真的写入，0 表示唯一键已存在（重复请求）。"""
    result = session.execute(
        sqlite_insert(PointLedger)
        .values(**values)
        .on_conflict_do_nothing(
            index_elements=["source_type", "source_id", "source_revision", "entry_type"]
        )
    )
    return affected_rows(result)


def list_ledger(session: Session, *, user_id: str, limit: int = 20, offset: int = 0) -> list[PointLedger]:
    stmt = (
        sa.select(PointLedger)
        .where(PointLedger.user_id == user_id)
        .order_by(PointLedger.created_at.desc(), PointLedger.id.desc())
        .limit(min(limit, 100))
        .offset(max(0, offset))
    )
    return list(session.scalars(stmt))


def count_ledger(session: Session, *, user_id: str) -> int:
    return int(
        session.scalar(
            sa.select(sa.func.count()).select_from(PointLedger).where(PointLedger.user_id == user_id)
        )
        or 0
    )


def get_ledger(session: Session, *, ledger_id: str, user_id: str | None = None) -> PointLedger | None:
    stmt = sa.select(PointLedger).where(PointLedger.id == ledger_id)
    if user_id is not None:
        stmt = stmt.where(PointLedger.user_id == user_id)
    return session.scalars(stmt).one_or_none()


def last_ledger_for_source(session: Session, *, source_type: str, source_id: str) -> PointLedger | None:
    return session.scalars(
        sa.select(PointLedger)
        .where(PointLedger.source_type == source_type, PointLedger.source_id == source_id)
        .order_by(PointLedger.created_at.desc(), PointLedger.id.desc())
    ).first()


# ------------------------------------------------------------------ 奖励
def get_award(
    session: Session, *, source_type: str, source_id: str, refresh: bool = False
) -> PointAward | None:
    row = session.scalars(
        sa.select(PointAward).where(
            PointAward.source_type == source_type, PointAward.source_id == source_id
        )
    ).one_or_none()
    if row is not None and refresh:
        session.refresh(row)
    return row


def get_award_by_id(session: Session, *, award_id: str, user_id: str | None = None) -> PointAward | None:
    stmt = sa.select(PointAward).where(PointAward.id == award_id)
    if user_id is not None:
        stmt = stmt.where(PointAward.user_id == user_id)
    return session.scalars(stmt).one_or_none()


def insert_award(session: Session, **values: Any) -> int:
    """幂等建立奖励来源：``(source_type, source_id)`` 唯一。"""
    result = session.execute(
        sqlite_insert(PointAward)
        .values(**values)
        .on_conflict_do_nothing(index_elements=["source_type", "source_id"])
    )
    return affected_rows(result)


def cas_update_award(session: Session, *, award_id: str, expected_revision: int, values: dict[str, Any]) -> int:
    """条件更新（乐观锁断言）：只有 revision 未被他人推进时才生效。"""
    result = session.execute(
        sa.update(PointAward)
        .where(PointAward.id == award_id, PointAward.revision == expected_revision)
        .values(**values)
    )
    return affected_rows(result)


def pending_points(session: Session, *, user_id: str) -> int:
    """待入账积分：尚未入账的奖励目标合计，不能消费。"""
    total = session.scalar(
        sa.select(
            sa.func.coalesce(
                sa.func.sum(PointAward.target_points - PointAward.posted_points), 0
            )
        ).where(PointAward.user_id == user_id, PointAward.status == "PENDING")
    )
    return max(0, int(total or 0))


def due_awards(session: Session, *, now: int, limit: int = 50) -> list[PointAward]:
    return list(
        session.scalars(
            sa.select(PointAward)
            .where(PointAward.status == "PENDING", PointAward.available_at <= now)
            .order_by(PointAward.available_at)
            .limit(min(limit, 200))
        )
    )


def list_awards(session: Session, *, user_id: str, limit: int = 20, offset: int = 0) -> list[PointAward]:
    stmt = (
        sa.select(PointAward)
        .where(PointAward.user_id == user_id)
        .order_by(PointAward.created_at.desc())
        .limit(min(limit, 100))
        .offset(max(0, offset))
    )
    return list(session.scalars(stmt))


# ------------------------------------------------------------------ 规则版本
def get_rule(session: Session, *, rule_version: str) -> PointRule | None:
    return session.scalars(
        sa.select(PointRule).where(PointRule.rule_version == rule_version)
    ).one_or_none()


def latest_published_rule(session: Session, *, kind: str) -> PointRule | None:
    return session.scalars(
        sa.select(PointRule)
        .where(PointRule.kind == kind, PointRule.status == "PUBLISHED")
        .order_by(PointRule.effective_at.desc(), PointRule.created_at.desc())
    ).first()


def count_rules(session: Session, *, kind: str) -> int:
    return int(
        session.scalar(
            sa.select(sa.func.count()).select_from(PointRule).where(PointRule.kind == kind)
        )
        or 0
    )


# ------------------------------------------------------------------ 调整
def get_adjustment(session: Session, *, adjustment_id: str) -> PointAdjustment | None:
    return session.get(PointAdjustment, adjustment_id)


def list_adjustments(
    session: Session, *, user_id: str | None = None, status: str | None = None, limit: int = 50
) -> list[PointAdjustment]:
    stmt = sa.select(PointAdjustment)
    if user_id is not None:
        stmt = stmt.where(PointAdjustment.user_id == user_id)
    if status is not None:
        stmt = stmt.where(PointAdjustment.status == status)
    stmt = stmt.order_by(PointAdjustment.created_at.desc()).limit(min(limit, 200))
    return list(session.scalars(stmt))


def cas_update_adjustment(
    session: Session, *, adjustment_id: str, expected_status: str, values: dict[str, Any]
) -> int:
    result = session.execute(
        sa.update(PointAdjustment)
        .where(PointAdjustment.id == adjustment_id, PointAdjustment.status == expected_status)
        .values(**values)
    )
    return affected_rows(result)


# ------------------------------------------------------------------ 申诉
def list_appeals(
    session: Session,
    *,
    user_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[PointAppeal]:
    stmt = sa.select(PointAppeal)
    if user_id is not None:
        stmt = stmt.where(PointAppeal.user_id == user_id)
    if status is not None:
        stmt = stmt.where(PointAppeal.status == status)
    stmt = stmt.order_by(PointAppeal.created_at.desc()).limit(min(limit, 200)).offset(offset)
    return list(session.scalars(stmt))


def count_appeals(
    session: Session, *, user_id: str | None = None, status: str | None = None
) -> int:
    stmt = sa.select(sa.func.count()).select_from(PointAppeal)
    if user_id is not None:
        stmt = stmt.where(PointAppeal.user_id == user_id)
    if status is not None:
        stmt = stmt.where(PointAppeal.status == status)
    return int(session.scalar(stmt) or 0)


def open_appeal(session: Session, *, user_id: str, ledger_id: str) -> PointAppeal | None:
    """同一流水的未结申诉：重复提交直接返回原申诉，避免刷单。"""
    return session.scalars(
        sa.select(PointAppeal)
        .where(
            PointAppeal.user_id == user_id,
            PointAppeal.ledger_id == ledger_id,
            PointAppeal.status.in_(("SUBMITTED", "UNDER_REVIEW")),
        )
        .order_by(PointAppeal.created_at.desc())
    ).first()


def appeal_states(session: Session, *, user_id: str, ledger_ids: list[str]) -> dict[str, str]:
    """批量取流水的最新申诉状态，供明细页展示申诉入口。"""
    if not ledger_ids:
        return {}
    rows = session.execute(
        sa.select(PointAppeal.ledger_id, PointAppeal.status)
        .where(PointAppeal.user_id == user_id, PointAppeal.ledger_id.in_(ledger_ids))
        .order_by(PointAppeal.created_at)
    ).all()
    states: dict[str, str] = {}
    for ledger_id, status in rows:
        states[str(ledger_id)] = str(status)
    return states


# ------------------------------------------------------------------ 冻结（C15）
def get_reservation(session: Session, *, redemption_id: str) -> PointReservation | None:
    return session.scalars(
        sa.select(PointReservation).where(PointReservation.redemption_id == redemption_id)
    ).one_or_none()


def insert_reservation(session: Session, **values: Any) -> int:
    result = session.execute(
        sqlite_insert(PointReservation)
        .values(**values)
        .on_conflict_do_nothing(index_elements=["redemption_id"])
    )
    return affected_rows(result)


def cas_update_reservation(
    session: Session, *, reservation_id: str, expected_state: str, values: dict[str, Any]
) -> int:
    result = session.execute(
        sa.update(PointReservation)
        .where(PointReservation.id == reservation_id, PointReservation.state == expected_state)
        .values(**values)
    )
    return affected_rows(result)


def expired_reservations(session: Session, *, now: int, limit: int = 50) -> list[PointReservation]:
    return list(
        session.scalars(
            sa.select(PointReservation)
            .where(PointReservation.state == "HELD", PointReservation.expires_at.is_not(None))
            .where(PointReservation.expires_at <= now)
            .order_by(PointReservation.expires_at)
            .limit(min(limit, 200))
        )
    )


# ------------------------------------------------------------------ 事件消费
def unconsumed_events(
    session: Session, *, event_type: str, consumer_name: str, now: int, limit: int
) -> list[OutboxEvent]:
    """本消费者尚未处理的到期事件（``points.award_due``）。

    积分模块必须自己把“收款/交付/退款到达”翻译成奖励重算：事件只携带
    订单与用户标识，携带的点数一律不可信（09 文档）。
    """
    consumed = sa.select(ConsumedEvent.event_id).where(ConsumedEvent.consumer_name == consumer_name)
    return list(
        session.scalars(
            sa.select(OutboxEvent)
            .where(
                OutboxEvent.event_type == event_type,
                OutboxEvent.available_at <= now,
                OutboxEvent.event_id.not_in(consumed),
            )
            .order_by(OutboxEvent.available_at)
            .limit(min(limit, 200))
        )
    )


def mark_event_consumed(session: Session, *, consumer_name: str, event_id: str) -> None:
    session.execute(
        sqlite_insert(ConsumedEvent)
        .values(consumer_name=consumer_name, event_id=event_id, processed_at=now_ms())
        .on_conflict_do_nothing(index_elements=["consumer_name", "event_id"])
    )


def scheduled_award_events(session: Session, *, order_id: str, available_at: int) -> int:
    """同一订单同一入账时刻是否已有排队事件（避免重复排期）。"""
    return int(
        session.scalar(
            sa.select(sa.func.count())
            .select_from(OutboxEvent)
            .where(
                OutboxEvent.event_type == "points.award_due",
                OutboxEvent.aggregate_id == order_id,
                OutboxEvent.status.in_(("PENDING", "CLAIMED")),
                OutboxEvent.available_at == available_at,
            )
        )
        or 0
    )


__all__ = [
    "add_balance",
    "add_reserved",
    "append_ledger",
    "appeal_states",
    "cas_update_adjustment",
    "cas_update_award",
    "cas_update_reservation",
    "count_ledger",
    "count_rules",
    "due_awards",
    "ensure_account",
    "expired_reservations",
    "get_account",
    "get_account_by_id",
    "get_adjustment",
    "get_award",
    "get_award_by_id",
    "get_ledger",
    "get_reservation",
    "get_rule",
    "insert_award",
    "insert_reservation",
    "last_ledger_for_source",
    "latest_published_rule",
    "ledger_total",
    "list_adjustments",
    "list_appeals",
    "list_awards",
    "list_ledger",
    "mark_event_consumed",
    "open_appeal",
    "pending_points",
    "release_reserved",
    "scheduled_award_events",
    "unconsumed_events",
]
