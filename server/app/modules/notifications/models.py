"""通知模块：模板渲染、站内通知记录、投递记录与发送状态。

约束（C27，2026-09-18 修订）：站内通知与邮件并存；邮件为必选通道，交易短信按需；
营销邮件默认不订阅。消息发送失败不回滚已成功支付（FR-017）。
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.ids import new_id, now_ms
from app.platform.orm import Base, PkMixin

CHANNELS = ("email", "sms")
NOTIFICATION_STATUSES = ("UNREAD", "READ", "ARCHIVED")
DELIVERY_STATUSES = ("PENDING", "SENT", "FAILED", "SUPPRESSED")


class Notification(PkMixin, Base):
    """站内通知记录。用户通知中心读取本表（/account/notifications）。"""

    __tablename__ = "notifications"
    id_kind = "notification"

    user_id: Mapped[str | None] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[str | None] = mapped_column(sa.String(64))
    event_type: Mapped[str] = mapped_column(sa.String(80), nullable=False)
    template_version: Mapped[str] = mapped_column(sa.String(20), nullable=False, default="v1")
    category: Mapped[str] = mapped_column(sa.String(30), nullable=False, default="transaction")
    title: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    body: Mapped[str] = mapped_column(sa.Text, nullable=False)
    link_path: Mapped[str | None] = mapped_column(sa.String(200))
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False, default="UNREAD")
    read_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    created_at: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, default=now_ms)

    __table_args__ = (
        sa.CheckConstraint("status IN ('UNREAD','READ','ARCHIVED')", name="ck_notifications_status"),
        sa.Index("ix_notifications_user", "user_id", "status", "created_at"),
    )


class Delivery(PkMixin, Base):
    """投递尝试。``UNIQUE(user_id, event_id, channel)`` 由应用层与索引共同保证。"""

    __tablename__ = "notification_deliveries"
    id_kind = "delivery"

    notification_id: Mapped[str | None] = mapped_column(
        sa.ForeignKey("notifications.id", ondelete="CASCADE")
    )
    user_id: Mapped[str | None] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"))
    event_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(sa.String(80), nullable=False)
    channel: Mapped[str] = mapped_column(sa.String(10), nullable=False)
    # 收件地址：邮箱/手机号密文，仅投递使用，不进入查询接口
    target_ciphertext: Mapped[str] = mapped_column(sa.Text, nullable=False)
    template_version: Mapped[str] = mapped_column(sa.String(20), nullable=False, default="v1")
    subject: Mapped[str] = mapped_column(sa.String(160), nullable=False, default="")
    body: Mapped[str] = mapped_column(sa.Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False, default="PENDING")
    attempt: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=1)
    provider_id: Mapped[str | None] = mapped_column(sa.String(80))
    provider_name: Mapped[str] = mapped_column(sa.String(30), nullable=False, default="mock")
    last_error: Mapped[str | None] = mapped_column(sa.String(300))
    sent_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    created_at: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, default=now_ms)
    updated_at: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, default=now_ms, onupdate=now_ms)

    __table_args__ = (
        sa.Index("uq_delivery_user_event_channel", "user_id", "event_id", "channel", unique=True),
        sa.CheckConstraint("channel IN ('email','sms')", name="ck_delivery_channel"),
        sa.CheckConstraint("status IN ('PENDING','SENT','FAILED','SUPPRESSED')", name="ck_delivery_status"),
        sa.Index("ix_delivery_status", "status", "created_at"),
    )


class MailSuppression(Base):
    """营销邮件退订名单（C27：营销默认不订阅，须提供退订）。"""

    __tablename__ = "mail_suppressions"

    user_id: Mapped[str] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    scope: Mapped[str] = mapped_column(sa.String(20), primary_key=True, default="marketing")
    reason: Mapped[str | None] = mapped_column(sa.String(200))
    created_at: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, default=now_ms)


__all__ = [
    "CHANNELS",
    "DELIVERY_STATUSES",
    "Delivery",
    "MailSuppression",
    "NOTIFICATION_STATUSES",
    "Notification",
    "new_id",
]
