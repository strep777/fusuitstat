"""站内通知中心契约回归。

依据：`文档/功能确认清单-已确认.md` **C27（2026-09-18 修订）**——站内通知与邮件并存，
邮件为必选通道，且消息发送失败不回滚已完成的业务（FR-017）；接口形状见
`06_API接口契约.md` 的 `GET /notifications`、`POST /notifications/{id}/read`、
`POST /notifications/read-all`。

锁定点：
1. 列表只返回本人数据，分页、未读筛选与分类筛选可用；
2. 已读标记需要 CSRF + 受信 Origin，非本人或不存在的 id 一律 404（不泄露是否存在）；
3. 全部已读只影响本人；
4. 邮件投递失败不影响站内通知的存在与可读——两个通道并存，不互相掩盖。
"""

from __future__ import annotations

import sqlalchemy as sa

from app.modules.notifications.models import Delivery, Notification
from app.platform.ids import now_ms


def _add_notification(
    session,
    *,
    user_id: str,
    title: str,
    status: str = "UNREAD",
    category: str = "transaction",
    created_at: int | None = None,
) -> Notification:
    row = Notification(
        user_id=user_id,
        event_type="order.paid",
        template_version="v1",
        category=category,
        title=title,
        body=f"{title} 的正文",
        link_path="/account/orders",
        status=status,
        created_at=created_at if created_at is not None else now_ms(),
    )
    session.add(row)
    session.flush()
    return row


def test_notification_list_is_own_only_and_pageable(customer, customer_factory, payment_db):
    base = now_ms()
    _add_notification(payment_db, user_id=customer.user_id, title="第一封", created_at=base - 3000)
    _add_notification(payment_db, user_id=customer.user_id, title="第二封", created_at=base - 2000)
    _add_notification(
        payment_db, user_id=customer.user_id, title="第三封已读", status="READ", created_at=base - 1000
    )
    _add_notification(
        payment_db,
        user_id=customer.user_id,
        title="营销信",
        category="marketing",
        created_at=base - 500,
    )
    other = customer_factory()
    _add_notification(payment_db, user_id=other.user_id, title="别人的通知", created_at=base)
    payment_db.commit()

    response = customer.client.get("/api/v1/notifications")
    assert response.status_code == 200, response.text
    payload = response.json()
    titles = [item["title"] for item in payload["items"]]
    assert "别人的通知" not in titles
    assert payload["unread_count"] == 3  # 本人的 3 条未读（含营销），不含他人那条，也不含已读那条
    assert payload["has_more"] is False and payload["next_cursor"] is None

    unread = customer.client.get("/api/v1/notifications", params={"unread_only": "true"})
    assert unread.status_code == 200, unread.text
    assert all(item["status"] == "UNREAD" for item in unread.json()["items"])
    assert "第三封已读" not in [item["title"] for item in unread.json()["items"]]

    marketing = customer.client.get("/api/v1/notifications", params={"category": "marketing"})
    assert [item["title"] for item in marketing.json()["items"]] == ["营销信"]

    first_page = customer.client.get(
        "/api/v1/notifications", params={"category": "transaction", "limit": 2}
    ).json()
    assert len(first_page["items"]) == 2 and first_page["has_more"] is True
    assert first_page["next_cursor"] == "2"
    second_page = customer.client.get(
        "/api/v1/notifications",
        params={"category": "transaction", "limit": 2, "cursor": first_page["next_cursor"]},
    ).json()
    assert second_page["has_more"] is False and second_page["next_cursor"] is None
    assert {item["id"] for item in first_page["items"]} & {
        item["id"] for item in second_page["items"]
    } == set()


def test_mark_read_requires_csrf_and_hides_foreign_ids(customer, customer_factory, payment_db):
    mine = _add_notification(payment_db, user_id=customer.user_id, title="我的通知")
    other = customer_factory()
    theirs = _add_notification(payment_db, user_id=other.user_id, title="别人的通知")
    payment_db.commit()

    no_csrf = customer.headers()
    no_csrf.pop("X-CSRF-Token")
    blocked = customer.client.post(f"/api/v1/notifications/{mine.id}/read", headers=no_csrf)
    assert blocked.status_code == 403, blocked.text
    payment_db.refresh(mine)
    assert mine.status == "UNREAD"

    foreign = customer.client.post(
        f"/api/v1/notifications/{theirs.id}/read", headers=customer.headers()
    )
    assert foreign.status_code == 404, foreign.text
    assert foreign.json()["error"]["code"] == "RESOURCE_NOT_FOUND"
    payment_db.refresh(theirs)
    assert theirs.status == "UNREAD"

    missing = customer.client.post(
        "/api/v1/notifications/notification-does-not-exist/read", headers=customer.headers()
    )
    assert missing.status_code == 404

    ok = customer.client.post(f"/api/v1/notifications/{mine.id}/read", headers=customer.headers())
    assert ok.status_code == 200, ok.text
    assert ok.json() == {"ok": True, "unread_count": 0}
    payment_db.refresh(mine)
    assert mine.status == "READ" and mine.read_at is not None

    # 重复标记已读：幂等，不报错也不改变未读数。
    again = customer.client.post(f"/api/v1/notifications/{mine.id}/read", headers=customer.headers())
    assert again.status_code == 200 and again.json()["unread_count"] == 0


def test_read_all_only_touches_own_rows(customer, customer_factory, payment_db):
    for index in range(3):
        _add_notification(payment_db, user_id=customer.user_id, title=f"我的未读 {index}")
    _add_notification(payment_db, user_id=customer.user_id, title="我的已读", status="READ")
    other = customer_factory()
    theirs = _add_notification(payment_db, user_id=other.user_id, title="别人的未读")
    payment_db.commit()

    response = customer.client.post("/api/v1/notifications/read-all", headers=customer.headers())
    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True, "updated": 3, "unread_count": 0}

    payment_db.refresh(theirs)
    assert theirs.status == "UNREAD"
    assert (
        payment_db.scalar(
            sa.select(sa.func.count())
            .select_from(Notification)
            .where(Notification.user_id == customer.user_id, Notification.status == "UNREAD")
        )
        == 0
    )


def test_failed_email_delivery_keeps_in_app_notification(customer, payment_db):
    """FR-017：邮件失败不回滚业务——站内通知仍在，且仍可读。"""
    from app.modules.contracts import NotificationRequest
    from app.modules.notifications.ports import notify

    created = notify(
        payment_db,
        NotificationRequest(
            event_id="notification-contract-1",
            event_type="quote.published",
            user_id=customer.user_id,
            title="报价已发布",
            body="请查看报价并在有效期内确认。",
            payload={"email": "untrusted@example.test"},
        ),
    )
    payment_db.commit()
    assert created == 1

    delivery = payment_db.scalars(
        sa.select(Delivery).where(Delivery.user_id == customer.user_id, Delivery.channel == "email")
    ).one()
    assert delivery.status == "FAILED" and delivery.last_error == "RECIPIENT_UNVERIFIED"

    listed = customer.client.get("/api/v1/notifications").json()
    assert [item["title"] for item in listed["items"]] == ["报价已发布"]
    assert listed["items"][0]["status"] == "UNREAD"
    assert listed["unread_count"] == 1

    # 同一事件重复登记不产生第二封站内通知（event_id 去重）。
    assert (
        notify(
            payment_db,
            NotificationRequest(
                event_id="notification-contract-1",
                event_type="quote.published",
                user_id=customer.user_id,
                title="报价已发布",
                body="请查看报价并在有效期内确认。",
                payload={"email": "untrusted@example.test"},
            ),
        )
        == 0
    )
    payment_db.commit()
    assert len(customer.client.get("/api/v1/notifications").json()["items"]) == 1
