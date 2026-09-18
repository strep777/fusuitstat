"""跨模块能力声明：其他模块只通过这里创建通知与投递。"""

from __future__ import annotations

from typing import Protocol

from sqlalchemy.orm import Session

from app.modules.contracts import NotificationRequest

__all__ = [
    "NotificationPort",
    "NotificationRequest",
    "notify",
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
