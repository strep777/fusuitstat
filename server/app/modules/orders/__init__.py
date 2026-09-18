"""订单查询模块：本人订单列表与订单详情。

读模型复用商务模块的 repository 与 service（同一限界上下文内的只读视图），
但 HTTP 层独立成组，与 06 文档 ``GET /orders``、``GET /orders/:id`` 对应。
所有查询都同时带 ``user_id`` 过滤，非本人返回 404。
"""

from __future__ import annotations

from app.modules.orders import repository, schemas, service

__all__ = ["repository", "schemas", "service"]
