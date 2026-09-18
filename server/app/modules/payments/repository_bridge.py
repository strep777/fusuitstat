"""与商务模块（commerce）的只读桥接与端口调用。

支付模块不导入商务模块的 ORM 表，也不查询它的表：所有订单/应付项读取都经
``app.modules.commerce.repository`` 的 ``get_installment`` / ``get_order`` /
``list_installments_for_order``，所有状态推进都经 ``app.modules.commerce.ports``
的 ``on_installment_paid`` / ``on_refund_succeeded``。

商务模块由另一位工程师并行实现；这里统一用 ``try/except ImportError`` 包装：
模块尚未就绪时抛 ``AppError("PROVIDER_UNAVAILABLE", "商务模块尚未就绪")``，
便于支付模块用桥接替身做隔离测试，也避免半成品模块把支付流程带崩。

返回值统一规范化为普通字典（兼容对方返回 ORM 行或 dataclass），字段名差异用
多候选读取吸收，降低并行开发的接口漂移风险。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.platform.errors import AppError

_NOT_READY = "商务模块尚未就绪"

# 商务模块应付项状态（08 文档「分离状态」）：DUE、PAYING、PAID、CLOSING、CLOSED。
_INSTALLMENT_STATE_FIELDS = ("state", "status")
# 订单履约状态：PENDING_PAYMENT、ACTIVE、FULFILLING、SHIPPED、COMPLETED、CANCELLED。
_ORDER_STATUS_FIELDS = ("status", "state")


def _commerce_ports() -> Any:
    try:
        from app.modules.commerce import ports as commerce_ports
    except ImportError as exc:  # pragma: no cover - 取决于并行开发进度
        raise AppError("PROVIDER_UNAVAILABLE", _NOT_READY) from exc
    return commerce_ports


def _call(function: str, *args: Any, **kwargs: Any) -> Any:
    """调用商务端口函数。

    ``ports`` 自身也是按需导入 ``commerce.repository``／``commerce.service`` 的，
    因此「商务模块缺失」可能在**调用时**才表现为 ``ImportError``。桥接统一把它翻译成
    ``PROVIDER_UNAVAILABLE``，不让支付模块抛出裸导入错误。
    """
    ports = _commerce_ports()
    target = getattr(ports, function, None)
    if target is None:
        raise AppError("PROVIDER_UNAVAILABLE", _NOT_READY)
    try:
        return target(*args, **kwargs)
    except ImportError as exc:
        raise AppError("PROVIDER_UNAVAILABLE", _NOT_READY) from exc


def _commerce_repository() -> Any:
    """兼容旧调用点：桥接的读取一律走端口。"""
    return _commerce_ports()


def _field(source: Any, names: tuple[str, ...], default: Any = None) -> Any:
    """按候选字段名读取属性或字典键（兼容对方的命名差异）。"""
    for name in names:
        if isinstance(source, dict):
            if name in source and source[name] is not None:
                return source[name]
        else:
            value = getattr(source, name, None)
            if value is not None:
                return value
    return default


# ------------------------------------------------------------------ 只读读取
def get_installment(session: Session, installment_id: str) -> Any:
    """读取单个应付项（商务端口，关键字调用）。"""
    return _call("get_installment", session, installment_id=installment_id)


def get_order(session: Session, order_id: str) -> Any:
    """读取单个订单（商务端口，关键字调用）。"""
    return _call("get_order", session, order_id=order_id)


def list_installments_for_order(session: Session, order_id: str) -> list[Any]:
    """读取订单的全部应付项。"""
    ports = _commerce_ports()
    target = getattr(ports, "list_installments_for_order", None)
    if not callable(target):  # pragma: no cover - 端口未交付时
        raise AppError("PROVIDER_UNAVAILABLE", _NOT_READY)
    try:
        return list(target(session, order_id=order_id))
    except ImportError as exc:
        raise AppError("PROVIDER_UNAVAILABLE", _NOT_READY) from exc


# ------------------------------------------------------------------ 规范化视图
def load_installment(session: Session, installment_id: str) -> dict[str, Any] | None:
    """应付项归一化视图；不存在返回 ``None``（由服务层决定错误码）。"""
    row = get_installment(session, installment_id)
    if row is None:
        return None
    return {
        "id": _field(row, ("id",)),
        "order_id": _field(row, ("order_id",)),
        "amount_cents": int(_field(row, ("amount_cents", "amount"), 0) or 0),
        "currency": _field(row, ("currency",), "CNY"),
        "state": str(_field(row, _INSTALLMENT_STATE_FIELDS, "DUE")),
        "kind": _field(row, ("kind",), "full"),
    }


def load_order(session: Session, order_id: str) -> dict[str, Any] | None:
    """订单归一化视图；不存在返回 ``None``。"""
    row = get_order(session, order_id)
    if row is None:
        return None
    return {
        "id": _field(row, ("id",)),
        "user_id": _field(row, ("user_id",)),
        "status": str(_field(row, _ORDER_STATUS_FIELDS, "PENDING_PAYMENT")),
        "currency": _field(row, ("currency",), "CNY"),
    }


def load_installments_for_order(session: Session, order_id: str) -> list[dict[str, Any]]:
    return [
        {
            "id": _field(row, ("id",)),
            "order_id": _field(row, ("order_id",), order_id),
            "amount_cents": int(_field(row, ("amount_cents", "amount"), 0) or 0),
            "currency": _field(row, ("currency",), "CNY"),
            "state": str(_field(row, _INSTALLMENT_STATE_FIELDS, "DUE")),
            "kind": _field(row, ("kind",), "full"),
        }
        for row in list_installments_for_order(session, order_id)
    ]


# ------------------------------------------------------------------ 端口调用
def on_installment_paid(session: Session, *, order_id: str, installment_id: str) -> dict[str, Any]:
    """已核验收款分配后推进商务侧状态：应付项、订单、库存/名额核销与 outbox。

    与收款、分配、事件去重在同一事务内提交（08 文档第 3 条）。
    """
    result = _call(
        "on_installment_paid", session, order_id=order_id, installment_id=installment_id
    )
    return dict(result or {})


def on_refund_succeeded(
    session: Session, *, order_id: str, refund_id: str, goods_cents: int, shipping_cents: int
) -> dict[str, Any]:
    """退款成功后的商务侧调整（净额重算、积分目标重算）。"""
    result = _call(
        "on_refund_succeeded",
        session,
        order_id=order_id,
        refund_id=refund_id,
        goods_cents=goods_cents,
        shipping_cents=shipping_cents,
    )
    return dict(result or {})


__all__ = [
    "get_installment",
    "get_order",
    "list_installments_for_order",
    "load_installment",
    "load_installments_for_order",
    "load_order",
    "on_installment_paid",
    "on_refund_succeeded",
]
