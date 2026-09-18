"""运营后台 API 路由组装。

后台独立入口 ``/admin``，采用独立员工登录和 MFA；即使客户已登录也不能继承
员工权限（03 文档）。每个动作绑定权限码（FR-016）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.admin_routes import router as admin_core
from app.api.dependencies import enforce_write_guard

# 写请求统一在组装层校验方法与 Origin/Referer（10 文档）：后台每个动作绑定权限码，
# 但来源校验不能依赖各路由自觉调用，否则漏挂一处就是一个 CSRF 缺口。
admin_router = APIRouter(dependencies=[Depends(enforce_write_guard)])
admin_router.include_router(admin_core)

__all__ = ["admin_router"]
