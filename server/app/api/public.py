"""公开与客户 API 路由组装。

只做挂载与顺序控制，不写业务逻辑（04 文档）。表结构见 06 文档的接口目录。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.dependencies import enforce_write_guard
from app.modules.catalog.router import public_router as catalog_public
from app.modules.commerce.router import public_router as commerce_public
from app.modules.commissions.router import public_router as commissions_public
from app.modules.content.router import public_router as content_public
from app.modules.feedback.router import public_router as feedback_public
from app.modules.identity.router import anon_router as identity_anon
from app.modules.identity.router import router as identity_router
from app.modules.loyalty.router import public_router as loyalty_public
from app.modules.media.router import public_router as media_public
from app.modules.notifications.router import public_router as notifications_public
from app.modules.orders.router import public_router as orders_public
from app.modules.payments.router import public_router as payments_public

# 写请求统一在组装层校验方法与 Origin/Referer（10 文档）：不能依赖各路由自觉调用，
# 否则漏挂一处就是一个 CSRF 缺口（审计发现的 `/me/sessions/revoke-others` 即此）。
public_router = APIRouter(dependencies=[Depends(enforce_write_guard)])

# 顺序：匿名/会话 → 身份 → 内容 → 目录 → 媒体 → 通知 → 交易与履约
public_router.include_router(identity_anon)
public_router.include_router(identity_router)
public_router.include_router(content_public)
public_router.include_router(catalog_public)
public_router.include_router(media_public)
public_router.include_router(notifications_public)
public_router.include_router(commissions_public)
public_router.include_router(commerce_public)
public_router.include_router(orders_public)
public_router.include_router(payments_public)
public_router.include_router(loyalty_public)
public_router.include_router(feedback_public)

__all__ = ["public_router"]
