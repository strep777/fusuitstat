"""商务模块：购物车、结算、订单、应付项、售后、物流与发票。

导出模型以便 ``app.platform.db.init_database`` 触发表注册；对外能力见 ``ports.py``。
"""

from __future__ import annotations

from app.modules.commerce import models, repository, schemas, service
from app.modules.commerce.models import (
    AddressSnapshot,
    AfterSale,
    AfterSaleMessage,
    Cart,
    CartItem,
    CheckoutSession,
    Installment,
    Invoice,
    Order,
    OrderAddressChange,
    OrderItem,
    Shipment,
)

__all__ = [
    "AddressSnapshot",
    "AfterSale",
    "AfterSaleMessage",
    "Cart",
    "CartItem",
    "CheckoutSession",
    "Installment",
    "Invoice",
    "Order",
    "OrderAddressChange",
    "OrderItem",
    "Shipment",
    "models",
    "repository",
    "schemas",
    "service",
]
