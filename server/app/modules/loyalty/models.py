"""积分模块拥有的数据：账户缓存、规则版本、奖励、账本、预约与调整。

约束（05 文档“积分、反馈与可靠任务”）：

- ``point_ledger`` 是权威记录，只追加，不 UPDATE/DELETE；账户余额只是可重建缓存，
  ``posted_balance = Σ point_ledger.delta``，退款扣回可使账面为负；
- ``available_points = max(0, posted_balance - reserved_points)``；
- ``point_awards`` 以 ``(source_type, source_id)`` 唯一：预付款/尾款/加项的支付
  不分别触发多次完整奖励，收款重复通知也只产生一个奖励来源；
- ``point_rules`` 版本发布后不可改，规则调整不回溯重算老订单（新规则新版本）；
- ``point_reservations`` 仅 C15 兑换开启后启用，默认关闭（09 文档：未确认时关闭入口）。

不得实现充值、提现、转账、投资收益，也不得用积分替代现金退款（09 文档）。
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.ids import new_id, now_ms
from app.platform.orm import Base, PkMixin, TimestampMixin, VersionMixin

AWARD_STATUSES = ("PENDING", "POSTED", "CANCELLED")
AWARD_SOURCE_TYPES = ("order", "feedback", "adjustment")

# 账本条目类型：奖励入账、退款扣回、后台调整、复核冲正、兑换与到期（C15 未开启不用）。
LEDGER_ENTRY_TYPES = (
    "AWARD",
    "AWARD_CLAWBACK",
    "ADJUSTMENT",
    "REVERSAL",
    "REDEMPTION",
    "REDEMPTION_RELEASE",
    "EXPIRY",
)

RESERVATION_STATES = ("HELD", "CONSUMED", "RELEASED", "EXPIRED")
ADJUSTMENT_STATUSES = ("PENDING", "APPROVED", "REJECTED", "APPLIED")
RULE_STATUSES = ("DRAFT", "PUBLISHED", "RETIRED")
RULE_KINDS = ("purchase", "feedback")

APPEAL_STATUSES = ("SUBMITTED", "UNDER_REVIEW", "ACCEPTED", "REJECTED")


def _in_check(column: str, values: tuple[str, ...], name: str) -> sa.CheckConstraint:
    """枚举列的行内约束：TEXT + CHECK（05 文档通用约定）。"""
    joined = ", ".join(f"'{value}'" for value in values)
    return sa.CheckConstraint(f"{column} IN ({joined})", name=name)


class PointAccount(PkMixin, TimestampMixin, VersionMixin, Base):
    """积分账户缓存。``user_id`` 唯一；余额可由账本重算（不能作为唯一真相）。"""

    __tablename__ = "point_accounts"
    id_kind = "account"

    user_id: Mapped[str] = mapped_column(
        sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    # 允许为负：退款扣回后账面为负，不要求用户付现，也不影响依法退款。
    posted_balance: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    reserved_points: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)

    __table_args__ = (
        sa.CheckConstraint("reserved_points >= 0", name="ck_point_accounts_reserved_nonneg"),
    )


class PointRule(PkMixin, Base):
    """积分规则版本。发布后不可修改，只能发布新版本。"""

    __tablename__ = "point_rules"

    rule_version: Mapped[str] = mapped_column(sa.String(40), nullable=False, unique=True)
    kind: Mapped[str] = mapped_column(sa.String(20), nullable=False, default="purchase")
    parameters: Mapped[dict[str, Any]] = mapped_column(sa.JSON, nullable=False, default=dict)
    effective_at: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, default=now_ms)
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False, default="PUBLISHED")
    published_by: Mapped[str | None] = mapped_column(sa.String(48))
    note: Mapped[str | None] = mapped_column(sa.String(200))
    created_at: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, default=now_ms)

    __table_args__ = (
        _in_check("kind", RULE_KINDS, "ck_point_rules_kind"),
        _in_check("status", RULE_STATUSES, "ck_point_rules_status"),
        sa.Index("ix_point_rules_kind", "kind", "status", "effective_at"),
    )


class PointAward(PkMixin, TimestampMixin, Base):
    """奖励来源。同一订单/反馈只有一个奖励，重算只改目标与已入账净额。"""

    __tablename__ = "point_awards"
    id_kind = "award"

    user_id: Mapped[str] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    source_type: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    source_id: Mapped[str] = mapped_column(sa.String(48), nullable=False)
    rule_version: Mapped[str] = mapped_column(sa.String(40), nullable=False, default="")
    target_points: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    posted_points: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    available_at: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, default=now_ms)
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False, default="PENDING")
    label: Mapped[str] = mapped_column(sa.String(120), nullable=False, default="")
    detail: Mapped[dict[str, Any]] = mapped_column(sa.JSON, nullable=False, default=dict)
    # 重算代数：每次目标变化 +1，账本用它做 ``source_revision``（幂等去重键的一部分）。
    revision: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    posted_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    blocked_reason: Mapped[str | None] = mapped_column(sa.String(200))

    __table_args__ = (
        sa.UniqueConstraint("source_type", "source_id", name="uq_point_award_source"),
        _in_check("status", AWARD_STATUSES, "ck_point_awards_status"),
        sa.Index("ix_point_awards_user", "user_id", "status"),
        sa.Index("ix_point_awards_due", "status", "available_at"),
    )


class PointLedger(PkMixin, Base):
    """积分流水：只追加，不 UPDATE/DELETE。纠错一律追加调整或冲正并关联原流水。"""

    __tablename__ = "point_ledger"
    id_kind = "ledger"

    account_id: Mapped[str] = mapped_column(
        sa.ForeignKey("point_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # 冗余保存本人，便于“本人流水”恒带 user_id 过滤且无需连表。
    user_id: Mapped[str] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    delta: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    entry_type: Mapped[str] = mapped_column(sa.String(24), nullable=False)
    source_type: Mapped[str] = mapped_column(sa.String(20), nullable=False, default="")
    source_id: Mapped[str] = mapped_column(sa.String(48), nullable=False, default="")
    source_revision: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=1)
    rule_version: Mapped[str] = mapped_column(sa.String(40), nullable=False, default="")
    reversal_of: Mapped[str | None] = mapped_column(sa.String(48))
    # 业务中文描述与来源摘要：不写他人反馈正文，避免泄露。
    description: Mapped[str] = mapped_column(sa.String(200), nullable=False, default="")
    label: Mapped[str] = mapped_column(sa.String(120), nullable=False, default="")
    created_at: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, default=now_ms)

    __table_args__ = (
        sa.UniqueConstraint(
            "source_type",
            "source_id",
            "source_revision",
            "entry_type",
            name="uq_point_ledger_source_revision",
        ),
        _in_check("entry_type", LEDGER_ENTRY_TYPES, "ck_point_ledger_entry_type"),
        sa.Index("ix_point_ledger_user", "user_id", "created_at"),
    )


class PointReservation(PkMixin, TimestampMixin, Base):
    """积分冻结（C15 兑换）。默认关闭；开启后按 ``redemption_id`` 唯一防重复冻结。"""

    __tablename__ = "point_reservations"
    id_kind = "reservation"

    account_id: Mapped[str] = mapped_column(
        sa.ForeignKey("point_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    redemption_id: Mapped[str] = mapped_column(sa.String(48), nullable=False, unique=True)
    points: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    state: Mapped[str] = mapped_column(sa.String(16), nullable=False, default="HELD")
    expires_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    settled_at: Mapped[int | None] = mapped_column(sa.BigInteger)

    __table_args__ = (
        sa.CheckConstraint("points > 0", name="ck_point_reservations_points_positive"),
        _in_check("state", RESERVATION_STATES, "ck_point_reservations_state"),
    )


class PointAdjustment(PkMixin, TimestampMixin, VersionMixin, Base):
    """后台积分调整申请与审批（C21）。入账只能追加流水，不能直接改余额。"""

    __tablename__ = "point_adjustments"
    id_kind = "adjustment"

    user_id: Mapped[str] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    delta_points: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    reason: Mapped[str] = mapped_column(sa.String(300), nullable=False)
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False, default="PENDING")
    # 申请与批准分离（C21）：同一员工不能既申请又批准。
    requested_by: Mapped[str | None] = mapped_column(sa.String(48))
    approved_by: Mapped[str | None] = mapped_column(sa.String(48))
    approved_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    reject_reason: Mapped[str | None] = mapped_column(sa.String(300))
    # 补发时关联原奖励来源（订单/反馈），便于对账。
    source_type: Mapped[str | None] = mapped_column(sa.String(20))
    source_id: Mapped[str | None] = mapped_column(sa.String(48))
    ledger_id: Mapped[str | None] = mapped_column(sa.String(48))
    evidence: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON)

    __table_args__ = (
        sa.CheckConstraint("delta_points <> 0", name="ck_point_adjustments_delta_nonzero"),
        _in_check("status", ADJUSTMENT_STATUSES, "ck_point_adjustments_status"),
        sa.Index("ix_point_adjustments_user", "user_id", "status"),
        sa.Index("ix_point_adjustments_status", "status", "created_at"),
    )


class PointAppeal(PkMixin, TimestampMixin, Base):
    """积分申诉。用户对某条流水提出理由，运营复核后给出结论（09 文档：允许申诉）。"""

    __tablename__ = "point_appeals"
    id_kind = "appeal"

    user_id: Mapped[str] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    ledger_id: Mapped[str] = mapped_column(
        sa.ForeignKey("point_ledger.id", ondelete="CASCADE"), nullable=False
    )
    reason: Mapped[str] = mapped_column(sa.String(1000), nullable=False)
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False, default="SUBMITTED")
    resolution: Mapped[str | None] = mapped_column(sa.String(300))
    handled_by: Mapped[str | None] = mapped_column(sa.String(48))
    handled_at: Mapped[int | None] = mapped_column(sa.BigInteger)

    __table_args__ = (
        _in_check("status", APPEAL_STATUSES, "ck_point_appeals_status"),
        sa.Index("ix_point_appeals_user", "user_id", "status"),
        sa.Index("ix_point_appeals_ledger", "ledger_id"),
    )


__all__ = [
    "ADJUSTMENT_STATUSES",
    "APPEAL_STATUSES",
    "AWARD_SOURCE_TYPES",
    "AWARD_STATUSES",
    "LEDGER_ENTRY_TYPES",
    "PointAccount",
    "PointAdjustment",
    "PointAppeal",
    "PointAward",
    "PointLedger",
    "PointReservation",
    "PointRule",
    "RESERVATION_STATES",
    "RULE_KINDS",
    "RULE_STATUSES",
    "new_id",
]
