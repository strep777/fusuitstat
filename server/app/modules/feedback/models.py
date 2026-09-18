"""反馈模块拥有的数据：反馈、消息、复审与申诉。

约束（05/09 文档）：

- 分类固定为功能问题、使用体验、产品建议、内容纠错、售后相关、其他；
- 状态机 ``SUBMITTED → TRIAGED → NEEDS_INFO → IN_PROGRESS → RESOLVED → CLOSED``，
  可从 TRIAGED 标记 ``DUPLICATE`` 或 ``REJECTED``，但必须有用户可读理由；
- 奖励状态独立：``PENDING_REVIEW、APPROVED、GRANTED、NOT_ELIGIBLE、REVOKED``；
- ``feedback_messages.visibility`` 严格区分用户可见回复与内部备注；
- ``feedback_reviews`` 以 ``(feedback_id, revision)`` 唯一：复审不能重复奖励；
- 反馈不保存 URL query 中的敏感值（token/code/session/email/phone 等整段丢弃）。
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.ids import new_id, now_ms
from app.platform.orm import Base, PkMixin, TimestampMixin, VersionMixin

FEEDBACK_CATEGORIES = ("bug", "experience", "suggestion", "content_error", "after_sale", "other")

# 状态机（09 文档）。DUPLICATE / REJECTED 为 TRIAGED 之后的终态分支。
FEEDBACK_STATUSES = (
    "SUBMITTED",
    "TRIAGED",
    "NEEDS_INFO",
    "IN_PROGRESS",
    "RESOLVED",
    "CLOSED",
    "DUPLICATE",
    "REJECTED",
)

REWARD_STATUSES = ("PENDING_REVIEW", "APPROVED", "GRANTED", "NOT_ELIGIBLE", "REVOKED")
REWARD_LEVELS = ("minor", "normal", "major")

MESSAGE_AUTHOR_TYPES = ("user", "staff", "system")
MESSAGE_VISIBILITIES = ("user", "internal")

APPEAL_STATUSES = ("SUBMITTED", "UNDER_REVIEW", "ACCEPTED", "REJECTED")

# 允许的状态流转。同态复审（例如补充证据后再次 RESOLVED 并升级奖励）单独允许。
ALLOWED_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "SUBMITTED": ("TRIAGED", "NEEDS_INFO", "IN_PROGRESS", "RESOLVED", "DUPLICATE", "REJECTED"),
    "TRIAGED": ("NEEDS_INFO", "IN_PROGRESS", "RESOLVED", "CLOSED", "DUPLICATE", "REJECTED"),
    "NEEDS_INFO": ("TRIAGED", "IN_PROGRESS", "RESOLVED", "DUPLICATE", "REJECTED"),
    "IN_PROGRESS": ("NEEDS_INFO", "RESOLVED", "DUPLICATE", "REJECTED"),
    "RESOLVED": ("CLOSED", "IN_PROGRESS"),
    "CLOSED": ("IN_PROGRESS",),
    "DUPLICATE": (),
    "REJECTED": ("IN_PROGRESS",),
}

# 必须给出用户可读理由的结论（09 文档：DUPLICATE / REJECTED 需要理由）。
REASON_REQUIRED_RESULTS = ("DUPLICATE", "REJECTED")


def _in_check(column: str, values: tuple[str, ...], name: str) -> sa.CheckConstraint:
    joined = ", ".join(f"'{value}'" for value in values)
    return sa.CheckConstraint(f"{column} IN ({joined})", name=name)


class Feedback(PkMixin, TimestampMixin, VersionMixin, Base):
    """用户反馈。奖励状态与处理状态相互独立。"""

    __tablename__ = "feedbacks"
    id_kind = "feedback"

    user_id: Mapped[str] = mapped_column(
        sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    category: Mapped[str] = mapped_column(sa.String(20), nullable=False, default="other")
    title: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    body: Mapped[str] = mapped_column(sa.Text, nullable=False)
    # 仅本站相对路径，且已去掉 query 中的敏感值。
    page_path: Mapped[str | None] = mapped_column(sa.String(200))
    steps: Mapped[str | None] = mapped_column(sa.Text)
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False, default="SUBMITTED")
    # 重复反馈只登记原反馈编号与匿名摘要，不公开原提交者。
    duplicate_of: Mapped[str | None] = mapped_column(
        sa.ForeignKey("feedbacks.id", ondelete="SET NULL")
    )
    duplicate_summary: Mapped[str | None] = mapped_column(sa.String(300))
    reward_status: Mapped[str] = mapped_column(sa.String(20), nullable=False, default="PENDING_REVIEW")
    reward_level: Mapped[str | None] = mapped_column(sa.String(10))
    reward_points: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    reward_award_id: Mapped[str | None] = mapped_column(sa.String(48))
    reward_granted_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    reward_reason: Mapped[str | None] = mapped_column(sa.String(300))
    resolution_note: Mapped[str | None] = mapped_column(sa.String(500))
    # 复审代数：每次审核 +1，作为 loyalty 奖励来源的 source_revision（防重复发奖）。
    review_revision: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    content_fingerprint: Mapped[str] = mapped_column(sa.String(64), nullable=False, default="")
    last_message_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    closed_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    withdrawn_at: Mapped[int | None] = mapped_column(sa.BigInteger)

    __table_args__ = (
        _in_check("category", FEEDBACK_CATEGORIES, "ck_feedbacks_category"),
        _in_check("status", FEEDBACK_STATUSES, "ck_feedbacks_status"),
        _in_check("reward_status", REWARD_STATUSES, "ck_feedbacks_reward_status"),
        sa.CheckConstraint("reward_points >= 0", name="ck_feedbacks_reward_points_nonneg"),
        sa.Index("ix_feedbacks_user_status", "user_id", "status", "created_at"),
        sa.Index("ix_feedbacks_fingerprint", "user_id", "content_fingerprint"),
        sa.Index("ix_feedbacks_reward_day", "user_id", "reward_granted_at"),
    )


class FeedbackMessage(PkMixin, Base):
    """反馈消息。``visibility='internal'`` 的内部备注永远不会出现在用户接口。"""

    __tablename__ = "feedback_messages"
    id_kind = "message"

    feedback_id: Mapped[str] = mapped_column(
        sa.ForeignKey("feedbacks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    author_type: Mapped[str] = mapped_column(sa.String(12), nullable=False, default="user")
    author_id: Mapped[str | None] = mapped_column(sa.String(48))
    body: Mapped[str] = mapped_column(sa.Text, nullable=False)
    visibility: Mapped[str] = mapped_column(sa.String(10), nullable=False, default="user")
    created_at: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, default=now_ms)

    __table_args__ = (
        _in_check("author_type", MESSAGE_AUTHOR_TYPES, "ck_feedback_messages_author"),
        _in_check("visibility", MESSAGE_VISIBILITIES, "ck_feedback_messages_visibility"),
        sa.Index("ix_feedback_messages_feedback", "feedback_id", "created_at"),
    )


class FeedbackReview(PkMixin, Base):
    """审核记录。``(feedback_id, revision)`` 唯一：复审不能重复奖励。"""

    __tablename__ = "feedback_reviews"
    id_kind = "review"

    feedback_id: Mapped[str] = mapped_column(
        sa.ForeignKey("feedbacks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    revision: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    result: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    reason: Mapped[str] = mapped_column(sa.String(500), nullable=False)
    reward_level: Mapped[str | None] = mapped_column(sa.String(10))
    reward_points: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    reward_status: Mapped[str] = mapped_column(sa.String(20), nullable=False, default="NOT_ELIGIBLE")
    # 审核人员必须记录影响与判据（C14），major 等级强制要求。
    impact: Mapped[str | None] = mapped_column(sa.String(300))
    criteria: Mapped[str | None] = mapped_column(sa.String(300))
    reviewer_id: Mapped[str | None] = mapped_column(sa.String(48))
    created_at: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, default=now_ms)

    __table_args__ = (
        sa.UniqueConstraint("feedback_id", "revision", name="uq_feedback_review_revision"),
        _in_check("result", FEEDBACK_STATUSES, "ck_feedback_reviews_result"),
        _in_check("reward_status", REWARD_STATUSES, "ck_feedback_reviews_reward_status"),
        sa.Index("ix_feedback_reviews_feedback", "feedback_id", "created_at"),
    )


class FeedbackAppeal(PkMixin, TimestampMixin, Base):
    """反馈申诉（用户对处理结论或奖励有异议时提出）。"""

    __tablename__ = "feedback_appeals"
    id_kind = "appeal"

    feedback_id: Mapped[str] = mapped_column(
        sa.ForeignKey("feedbacks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    reason: Mapped[str] = mapped_column(sa.String(1000), nullable=False)
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False, default="SUBMITTED")
    resolution: Mapped[str | None] = mapped_column(sa.String(300))
    handled_by: Mapped[str | None] = mapped_column(sa.String(48))
    handled_at: Mapped[int | None] = mapped_column(sa.BigInteger)

    __table_args__ = (
        _in_check("status", APPEAL_STATUSES, "ck_feedback_appeals_status"),
        sa.Index("ix_feedback_appeals_user", "user_id", "status"),
    )


__all__ = [
    "ALLOWED_TRANSITIONS",
    "APPEAL_STATUSES",
    "FEEDBACK_CATEGORIES",
    "FEEDBACK_STATUSES",
    "Feedback",
    "FeedbackAppeal",
    "FeedbackMessage",
    "FeedbackReview",
    "MESSAGE_AUTHOR_TYPES",
    "MESSAGE_VISIBILITIES",
    "REASON_REQUIRED_RESULTS",
    "REWARD_LEVELS",
    "REWARD_STATUSES",
    "new_id",
]
