"""支付模块。

拥有的数据：``payment_attempts``、``payment_allocations``、``payment_events``、
``refunds``、``refund_items``、``reconciliation_runs``、``reconciliation_differences``。

对外能力：``ports.py``（跨模块端口，签名与 ``contracts.PaymentsPort`` 一致）；
HTTP：``router.public_router``；回调验签入口：``notifications.handle_notification``；
渠道适配器：``app/adapters/payments``。

规则源：``文档/08_商城订单支付与退款.md``（唯一规则源）与 05/06 文档。
"""

from __future__ import annotations

from app.modules.payments import models, notifications, ports, repository, schemas, service
from app.modules.payments.router import public_router

__all__ = [
    "models",
    "notifications",
    "ports",
    "public_router",
    "repository",
    "schemas",
    "service",
]
