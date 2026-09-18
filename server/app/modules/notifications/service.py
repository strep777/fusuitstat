"""通知服务：登记通知与投递、执行投递、失败重试与退订抑制。

交易通知失败不回滚已成功支付（FR-017）：调用方只在同一事务内登记记录，
真实发送由 worker 消费 outbox 事件完成。
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.modules.notifications.models import Delivery, MailSuppression, Notification
from app.modules.notifications.ports import NotificationRequest
from app.modules.notifications.templates import render
from app.platform.config import get_settings
from app.platform.db import affected_rows
from app.platform.ids import iso, now_ms
from app.platform.security import decrypt_pii, encrypt_pii

MAX_ATTEMPTS = 5


def _suppressed(session: Session, *, user_id: str | None, category: str) -> bool:
    if not user_id or category != "marketing":
        return False
    if not get_settings().feature_marketing_email:
        return True
    return (
        session.get(MailSuppression, {"user_id": user_id, "scope": "marketing"}) is not None
    )


def enqueue_notification(session: Session, request: NotificationRequest) -> int:
    """在调用方事务内登记站内通知与投递记录。

    返回值是**新建的站内通知数量**（0 = 该 ``event_id`` 已经登记过，调用方据此判断首次投递）。
    投递记录不参与返回值：同一通知会按渠道各建一条 ``Delivery``，投递统计请看 ``delivery_stats``。
    """
    template = render(request.event_type, request.payload)
    suppressed = _suppressed(session, user_id=request.user_id, category=template.category)

    existing_notification = session.scalar(sa.select(Notification).where(
        Notification.user_id == request.user_id, Notification.event_id == request.event_id,
    ))
    if existing_notification is not None:
        return 0
    notification = Notification(
        user_id=request.user_id,
        event_id=request.event_id,
        event_type=request.event_type,
        template_version=template.version,
        category=request.category or template.category,
        title=request.title or template.subject,
        body=request.body or template.body,
        link_path=request.link_path if request.link_path is not None else template.link_path,
    )
    session.add(notification)
    session.flush()

    for channel in request.channels or template.channels:
        target = request.email_target if channel == "email" else request.phone_target
        if channel == "email" and not target and request.user_id:
            from app.modules.identity.ports import verified_email
            target = verified_email(session, request.user_id)
        if not target and channel != "email":
            continue
        existing = session.scalars(
            sa.select(Delivery).where(
                Delivery.user_id == request.user_id,
                Delivery.event_id == request.event_id,
                Delivery.channel == channel,
            )
        ).one_or_none()
        if existing is not None:
            continue
        delivery = Delivery(
            notification_id=notification.id,
            user_id=request.user_id,
            event_id=request.event_id,
            event_type=request.event_type,
            channel=channel,
            target_ciphertext=encrypt_pii(target or ""),
            template_version=template.version,
            subject=request.title or template.subject,
            body=(template.sms_body if channel == "sms" and template.sms_body else (request.body or template.body))
                 + (("\n" + get_settings().public_origin.rstrip("/") + notification.link_path)
                    if channel == "email" and notification.link_path and notification.link_path.startswith("/")
                    and not notification.link_path.startswith("//") else ""),
            status="SUPPRESSED" if suppressed and channel == "email" else ("PENDING" if target else "FAILED"),
            last_error=None if target else "RECIPIENT_UNVERIFIED",
            attempt=0,
            provider_name="suppressed" if suppressed else "pending",
        )
        session.add(delivery)
    return 1


def due_deliveries(session: Session, *, limit: int = 20) -> list[Delivery]:
    return list(
        session.scalars(
            sa.select(Delivery)
            .where(Delivery.status == "PENDING")
            .order_by(Delivery.created_at)
            .limit(min(limit, 100))
        )
    )


def send_delivery(session: Session, delivery: Delivery) -> None:
    """执行一次投递尝试。失败记录原因并保留重试空间。"""
    from app.adapters import mail as mail_adapter
    from app.adapters import sms as sms_adapter

    target = decrypt_pii(delivery.target_ciphertext)
    delivery.attempt += 1
    try:
        if delivery.channel == "email":
            result = mail_adapter.get_gateway().send(
                to=target, subject=delivery.subject, body=delivery.body
            )
        else:
            result = sms_adapter.get_gateway().send_text(to=target, text=delivery.body)
    except Exception as exc:  # noqa: BLE001 - 供应商异常统一转投递失败
        delivery.status = "FAILED" if delivery.attempt >= MAX_ATTEMPTS else "PENDING"
        delivery.last_error = type(exc).__name__
        delivery.updated_at = now_ms()
        return

    delivery.status = "SENT"
    delivery.provider_id = result.message_id
    delivery.provider_name = result.provider
    delivery.sent_at = now_ms()
    delivery.last_error = None
    delivery.updated_at = now_ms()


def retry_failed(session: Session, *, limit: int = 20) -> int:
    """把未超过最大尝试次数的失败投递重新排队。"""
    rows = list(
        session.scalars(
            sa.select(Delivery)
            .where(Delivery.status == "FAILED", Delivery.attempt < MAX_ATTEMPTS,
                   Delivery.last_error != "RECIPIENT_UNVERIFIED")
            .order_by(Delivery.updated_at)
            .limit(min(limit, 100))
        )
    )
    for row in rows:
        row.status = "PENDING"
    return len(rows)


# ------------------------------------------------------------------ 查询
def list_notifications(
    session: Session, *, user_id: str, status: str | None = None, category: str | None = None,
    limit: int = 20, offset: int = 0,
) -> list[Notification]:
    stmt = sa.select(Notification).where(Notification.user_id == user_id)
    if status:
        stmt = stmt.where(Notification.status == status)
    if category:
        stmt = stmt.where(Notification.category == category)
    stmt = stmt.order_by(Notification.created_at.desc()).limit(min(limit, 100)).offset(offset)
    return list(session.scalars(stmt))


def count_notifications(
    session: Session, *, user_id: str, status: str | None = None, category: str | None = None
) -> int:
    """计数必须与 ``list_notifications`` 用同一组筛选条件，否则 ``has_more`` 会算错。"""
    stmt = sa.select(sa.func.count()).select_from(Notification).where(Notification.user_id == user_id)
    if status:
        stmt = stmt.where(Notification.status == status)
    if category:
        stmt = stmt.where(Notification.category == category)
    return int(session.scalar(stmt) or 0)


def unread_count(session: Session, *, user_id: str) -> int:
    return count_notifications(session, user_id=user_id, status="UNREAD")


def mark_read(session: Session, *, user_id: str, notification_id: str) -> bool:
    row = session.scalars(
        sa.select(Notification).where(
            Notification.id == notification_id, Notification.user_id == user_id
        )
    ).one_or_none()
    if row is None:
        return False
    if row.status == "UNREAD":
        row.status = "READ"
        row.read_at = now_ms()
    return True


def mark_all_read(session: Session, *, user_id: str) -> int:
    result = session.execute(
        sa.update(Notification)
        .where(Notification.user_id == user_id, Notification.status == "UNREAD")
        .values(status="READ", read_at=now_ms())
    )
    return affected_rows(result)


def suppression_add(session: Session, *, user_id: str, scope: str = "marketing", reason: str | None = None) -> None:
    if session.get(MailSuppression, {"user_id": user_id, "scope": scope}) is None:
        session.add(MailSuppression(user_id=user_id, scope=scope, reason=reason))


def suppression_remove(session: Session, *, user_id: str, scope: str = "marketing") -> None:
    row = session.get(MailSuppression, {"user_id": user_id, "scope": scope})
    if row is not None:
        session.delete(row)


def delivery_stats(session: Session) -> dict[str, int]:
    rows = session.execute(
        sa.select(Delivery.status, sa.func.count()).group_by(Delivery.status)
    ).all()
    return {str(status): int(count) for status, count in rows}


def serialize_notification(row: Notification) -> dict[str, Any]:
    return {
        "id": row.id,
        "event_type": row.event_type,
        "category": row.category,
        "title": row.title,
        "body": row.body,
        "link_path": row.link_path,
        "status": row.status,
        "created_at": iso(row.created_at),
        "read_at": iso(row.read_at),
    }


def resume_missing_recipient(session: Session, user_id: str) -> int:
    from app.modules.identity.ports import verified_email
    target = verified_email(session, user_id)
    if not target:
        return 0
    result = session.execute(sa.update(Delivery).where(
        Delivery.user_id == user_id, Delivery.channel == "email", Delivery.status == "FAILED",
        Delivery.last_error == "RECIPIENT_UNVERIFIED",
    ).values(target_ciphertext=encrypt_pii(target), status="PENDING", last_error=None))
    return affected_rows(result)
