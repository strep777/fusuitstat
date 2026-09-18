"""跨模块能力声明：其他模块只通过这里创建通知与投递。"""

from __future__ import annotations

from typing import Protocol

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.modules.contracts import NotificationRequest

__all__ = [
    "NotificationPort",
    "NotificationRequest",
    "notify",
    "purge_user_inbox",
    "resume_missing_recipient",
    "unread_count",
]


class NotificationPort(Protocol):
    def notify(self, session: Session, request: NotificationRequest) -> int: ...


def notify(session: Session, request: NotificationRequest) -> int:
    """在调用方事务内登记通知；真实投递由 worker 消费 outbox 事件完成。"""
    from app.modules.notifications import service

    return service.enqueue_notification(session, request)


def resume_missing_recipient(session: Session, user_id: str) -> int:
    from app.modules.notifications.service import resume_missing_recipient as resume
    return resume(session, user_id)


def unread_count(session: Session, *, user_id: str) -> int:
    """本人未读通知数（账户徽标）。计数属通知模块自己的数据。"""
    from app.modules.notifications.service import unread_count as count
    return count(session, user_id=user_id)


def purge_user_inbox(session: Session, *, user_id: str) -> int:
    """注销清理：删除本人的站内通知（可删除资料）。

    投递记录（``notification_deliveries``）保留：它是「是否发过、发到哪」的运维证据，
    且不含明文收件地址（收件地址为密文，注销时按保留策略处理）。
    """
    from app.modules.notifications.models import Notification

    rows = list(session.scalars(sa.select(Notification).where(Notification.user_id == user_id)))
    for row in rows:
        session.delete(row)
    session.flush()
    return len(rows)
