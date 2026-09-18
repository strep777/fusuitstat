"""通知模块 HTTP 路由（``/notifications``）。

06 文档：``GET /notifications``、``POST /notifications/{id}/read``、
``POST /notifications/read-all``，均为本人数据。
本文件只做查询与已读标记，业务规则仍在 ``notifications/service.py``；
DTO 定义在本文件内（本模块的 schemas 由通知模块自己维护，路由不引入新的存储结构）。
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Request
from pydantic import Field

from app.api.dependencies import ContextDep, CsrfDep, CustomerIdDep, enforce_write_guard
from app.modules.notifications import service as svc
from app.modules.shared.schemas import CursorField, Dto, LimitField, Page
from app.platform.errors import AppError

public_router = APIRouter(tags=["notifications"])
# 兼容装配层的两种命名；对外只暴露 public_router。
router = public_router

NotificationStatus = Literal["UNREAD", "READ", "ARCHIVED"]


class NotificationItem(Dto):
    id: str
    event_type: str
    category: str
    title: str
    body: str
    link_path: str | None = None
    status: str
    created_at: str | None = None
    read_at: str | None = None


class NotificationPage(Page[NotificationItem]):
    """通知分页：``next_cursor`` 为下一页偏移量的字符串形式；附带未读数。"""

    unread_count: int = 0


class NotificationReadResponse(Dto):
    ok: bool = True
    unread_count: int = 0


class NotificationReadAllResponse(Dto):
    ok: bool = True
    updated: int = 0
    unread_count: int = 0


def _offset_from_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        value = int(cursor)
    except ValueError:
        return 0
    return max(0, value)


@public_router.get("/notifications", response_model=NotificationPage)
def list_notifications(
    ctx: ContextDep,
    user_id: CustomerIdDep,
    status: NotificationStatus | None = None,
    category: Annotated[str | None, Field(max_length=30)] = None,
    unread_only: bool = False,
    limit: LimitField = 20,
    cursor: CursorField = None,
) -> NotificationPage:
    """本人通知：按分类与未读筛选，倒序分页。"""
    offset = _offset_from_cursor(cursor)
    effective_status = "UNREAD" if unread_only else status
    rows = svc.list_notifications(
        ctx.session,
        user_id=user_id,
        status=effective_status,
        category=category,
        limit=limit,
        offset=offset,
    )
    total = svc.count_notifications(
        ctx.session, user_id=user_id, status=effective_status, category=category
    )
    items = [NotificationItem(**svc.serialize_notification(row)) for row in rows]
    has_more = offset + len(items) < total
    return NotificationPage(
        items=items,
        next_cursor=str(offset + limit) if has_more else None,
        has_more=has_more,
        unread_count=svc.unread_count(ctx.session, user_id=user_id),
    )


@public_router.post("/notifications/{notification_id}/read", response_model=NotificationReadResponse)
def mark_notification_read(
    notification_id: str, ctx: ContextDep, _csrf: CsrfDep, user_id: CustomerIdDep, request: Request
) -> NotificationReadResponse:
    """标记单条已读。非本人或不存在一律 404，不泄露是否存在。"""
    enforce_write_guard(request)
    found = svc.mark_read(ctx.session, user_id=user_id, notification_id=notification_id)
    if not found:
        raise AppError("RESOURCE_NOT_FOUND")
    ctx.session.commit()
    return NotificationReadResponse(ok=True, unread_count=svc.unread_count(ctx.session, user_id=user_id))


@public_router.post("/notifications/read-all", response_model=NotificationReadAllResponse)
def mark_all_notifications_read(
    ctx: ContextDep, _csrf: CsrfDep, user_id: CustomerIdDep, request: Request
) -> NotificationReadAllResponse:
    """全部标记已读，返回本次更新数量。"""
    enforce_write_guard(request)
    updated = svc.mark_all_read(ctx.session, user_id=user_id)
    ctx.session.commit()
    return NotificationReadAllResponse(ok=True, updated=updated, unread_count=0)


__all__ = ["public_router", "router"]
