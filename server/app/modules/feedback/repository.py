"""反馈模块数据访问：反馈、消息、复审与申诉。

查询一律支持 ``user_id`` 过滤；本人接口必须带该过滤，非本人按不存在处理（06 文档）。
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.modules.feedback.models import Feedback, FeedbackAppeal, FeedbackMessage, FeedbackReview
from app.platform.db import affected_rows
from app.platform.reliability import IdempotencyKey

# 这些终态不再视为“重复提交”，允许用户重新反馈。
DEDUPE_IGNORED_STATUSES = ("CLOSED", "REJECTED", "DUPLICATE")


def get_feedback(session: Session, *, feedback_id: str, user_id: str | None = None) -> Feedback | None:
    stmt = sa.select(Feedback).where(Feedback.id == feedback_id)
    if user_id is not None:
        stmt = stmt.where(Feedback.user_id == user_id)
    return session.scalars(stmt).one_or_none()


def find_duplicate(
    session: Session, *, user_id: str, page_path: str | None, title: str, exclude_id: str | None = None
) -> Feedback | None:
    """同一用户对同一 ``page_path + title`` 的未终结反馈视为重复。"""
    stmt = sa.select(Feedback).where(
        Feedback.user_id == user_id,
        Feedback.title == title,
        Feedback.status.not_in(DEDUPE_IGNORED_STATUSES),
    )
    if page_path is None:
        stmt = stmt.where(Feedback.page_path.is_(None))
    else:
        stmt = stmt.where(Feedback.page_path == page_path)
    if exclude_id is not None:
        stmt = stmt.where(Feedback.id != exclude_id)
    return session.scalars(stmt.order_by(Feedback.created_at)).first()


def overdue_feedback(
    session: Session, *, before_ms: int, workdays: int = 7, limit: int = 50
) -> list[Feedback]:
    """已提交、尚未结束、工作人员尚未响应，且提交时间已早于「最宽可能截止时间」的反馈（C30 逾期提醒）。

    这里只做粗筛（`workdays` 个自然日前），精确判定按客服日历在服务层完成。
    """
    natural_cutoff = before_ms - max(1, workdays) * 24 * 3600 * 1000
    return list(
        session.scalars(
            sa.select(Feedback)
            .where(
                Feedback.status.not_in(("CLOSED", "REJECTED", "DUPLICATE")),
                Feedback.withdrawn_at.is_(None),
                Feedback.first_responded_at.is_(None),
                Feedback.created_at <= natural_cutoff,
            )
            .order_by(Feedback.created_at)
            .limit(max(1, min(limit, 200)))
        )
    )


def list_feedbacks(
    session: Session,
    *,
    user_id: str | None = None,
    status: str | None = None,
    category: str | None = None,
    reward_status: str | None = None,
    query: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[Feedback]:
    stmt = sa.select(Feedback)
    if user_id is not None:
        stmt = stmt.where(Feedback.user_id == user_id)
    if status is not None:
        stmt = stmt.where(Feedback.status == status)
    if category is not None:
        stmt = stmt.where(Feedback.category == category)
    if reward_status is not None:
        stmt = stmt.where(Feedback.reward_status == reward_status)
    if query:
        like = f"%{query}%"
        stmt = stmt.where(sa.or_(Feedback.title.like(like), Feedback.body.like(like)))
    stmt = (
        stmt.order_by(Feedback.created_at.desc(), Feedback.id.desc())
        .limit(min(limit, 100))
        .offset(max(0, offset))
    )
    return list(session.scalars(stmt))


def count_feedbacks(
    session: Session,
    *,
    user_id: str | None = None,
    status: str | None = None,
    category: str | None = None,
    reward_status: str | None = None,
    query: str | None = None,
) -> int:
    stmt = sa.select(sa.func.count()).select_from(Feedback)
    if user_id is not None:
        stmt = stmt.where(Feedback.user_id == user_id)
    if status is not None:
        stmt = stmt.where(Feedback.status == status)
    if category is not None:
        stmt = stmt.where(Feedback.category == category)
    if reward_status is not None:
        stmt = stmt.where(Feedback.reward_status == reward_status)
    if query:
        like = f"%{query}%"
        stmt = stmt.where(sa.or_(Feedback.title.like(like), Feedback.body.like(like)))
    return int(session.scalar(stmt) or 0)


def list_messages(
    session: Session, *, feedback_id: str, visibility: str | None = None
) -> list[FeedbackMessage]:
    stmt = sa.select(FeedbackMessage).where(FeedbackMessage.feedback_id == feedback_id)
    if visibility is not None:
        stmt = stmt.where(FeedbackMessage.visibility == visibility)
    stmt = stmt.order_by(FeedbackMessage.created_at, FeedbackMessage.id)
    return list(session.scalars(stmt))


# ------------------------------------------------------------------ 复审
def latest_review(session: Session, *, feedback_id: str) -> FeedbackReview | None:
    return session.scalars(
        sa.select(FeedbackReview)
        .where(FeedbackReview.feedback_id == feedback_id)
        .order_by(FeedbackReview.revision.desc())
    ).first()


def insert_review(session: Session, **values: Any) -> int:
    """插入复审记录。``(feedback_id, revision)`` 唯一，重复返回 0（不重复奖励）。"""
    result = session.execute(
        sqlite_insert(FeedbackReview)
        .values(**values)
        .on_conflict_do_nothing(index_elements=["feedback_id", "revision"])
    )
    return affected_rows(result)


def cas_claim_review(
    session: Session, *, feedback_id: str, expected_revision: int, values: dict[str, Any]
) -> int:
    """条件推进复审代数（乐观锁断言）：并发审核只有一个能拿到该 revision。"""
    result = session.execute(
        sa.update(Feedback)
        .where(Feedback.id == feedback_id, Feedback.review_revision == expected_revision)
        .values(**values)
    )
    return affected_rows(result)


def rewarded_feedback_ids(session: Session, *, user_id: str, start_ms: int, end_ms: int) -> set[str]:
    """窗口内已获得奖励的反馈编号（按 Asia/Shanghai 自然日计算边界）。"""
    rows = session.scalars(
        sa.select(Feedback.id).where(
            Feedback.user_id == user_id,
            Feedback.reward_granted_at.is_not(None),
            Feedback.reward_granted_at >= start_ms,
            Feedback.reward_granted_at < end_ms,
            Feedback.reward_status.in_(("GRANTED", "APPROVED")),
        )
    )
    return {str(row) for row in rows}


# ------------------------------------------------------------------ 申诉
def get_appeal(session: Session, *, appeal_id: str, user_id: str | None = None) -> FeedbackAppeal | None:
    stmt = sa.select(FeedbackAppeal).where(FeedbackAppeal.id == appeal_id)
    if user_id is not None:
        stmt = stmt.where(FeedbackAppeal.user_id == user_id)
    return session.scalars(stmt).one_or_none()


def open_appeal(session: Session, *, feedback_id: str, user_id: str) -> FeedbackAppeal | None:
    return session.scalars(
        sa.select(FeedbackAppeal)
        .where(
            FeedbackAppeal.feedback_id == feedback_id,
            FeedbackAppeal.user_id == user_id,
            FeedbackAppeal.status.in_(("SUBMITTED", "UNDER_REVIEW")),
        )
        .order_by(FeedbackAppeal.created_at.desc())
    ).first()


def list_appeals(
    session: Session, *, feedback_id: str | None = None, status: str | None = None, limit: int = 50
) -> list[FeedbackAppeal]:
    stmt = sa.select(FeedbackAppeal)
    if feedback_id is not None:
        stmt = stmt.where(FeedbackAppeal.feedback_id == feedback_id)
    if status is not None:
        stmt = stmt.where(FeedbackAppeal.status == status)
    stmt = stmt.order_by(FeedbackAppeal.created_at.desc()).limit(min(limit, 200))
    return list(session.scalars(stmt))


# ------------------------------------------------------------------ 幂等
def get_idempotency(
    session: Session, *, actor_id: str, operation: str, key: str
) -> IdempotencyKey | None:
    return session.scalars(
        sa.select(IdempotencyKey).where(
            IdempotencyKey.actor_id == actor_id,
            IdempotencyKey.operation == operation,
            IdempotencyKey.key == key,
        )
    ).one_or_none()


def insert_idempotency(session: Session, **values: Any) -> int:
    result = session.execute(
        sqlite_insert(IdempotencyKey)
        .values(**values)
        .on_conflict_do_nothing(index_elements=["actor_id", "operation", "key"])
    )
    return affected_rows(result)


__all__ = [
    "DEDUPE_IGNORED_STATUSES",
    "cas_claim_review",
    "count_feedbacks",
    "find_duplicate",
    "get_appeal",
    "get_feedback",
    "get_idempotency",
    "insert_idempotency",
    "insert_review",
    "latest_review",
    "list_appeals",
    "list_feedbacks",
    "list_messages",
    "open_appeal",
    "rewarded_feedback_ids",
]
