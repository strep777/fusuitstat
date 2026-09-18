"""支付模块对外端口（跨模块唯一入口）。

签名与 ``app/modules/contracts.py`` 的 ``PaymentsPort`` 一致；本文件只做转调，
不引入额外规则。商务/后台/任务模块只允许通过这些函数使用支付能力。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.modules.contracts import PaymentIntent, RefundOutcome, VerifiedPayment
from app.modules.payments import repository as repo
from app.modules.payments import repository_bridge as bridge
from app.modules.payments import service as svc


def create_intent(
    session: Session, *, installment_id: str, provider: str, scene: str, actor_id: str
) -> PaymentIntent:
    """为应付项发起收款：登记唯一商户单号后用固定单号下单。"""
    return svc.create_intent(
        session, installment_id=installment_id, provider=provider, scene=scene, actor_id=actor_id
    )


def apply_verified_payment(session: Session, verified: VerifiedPayment) -> dict[str, Any]:
    """落实一笔已验签收款；与分配、应付状态、库存/名额、outbox 同事务提交。"""
    return svc.apply_verified_payment(session, verified)


def mark_order_closing(session: Session, *, order_id: str, reason: str) -> None:
    """订单进入关闭流程：先关闭或查询渠道，再决定能否取消。"""
    svc.mark_order_closing(session, order_id=order_id, reason=reason)


def order_paid_summary(session: Session, *, order_id: str) -> dict[str, Any]:
    """订单实收汇总（``paid_cents`` 只计 APPLIED 分配）。"""
    return svc.order_paid_summary(session, order_id=order_id)


def close_payment_attempts_for_installment(
    session: Session, *, installment_id: str, reason: str
) -> int:
    """关闭应付项下的活动支付尝试，返回真正关闭的数量。"""
    return svc.close_payment_attempts_for_installment(
        session, installment_id=installment_id, reason=reason
    )


def unresolved_attempt_count(session: Session, *, order_id: str) -> int:
    """订单下仍处于活动状态的支付尝试数量。

    关单核对必须在释放库存前用它判定「渠道是否真的没有在途付款」：
    关闭失败（渠道不可用、渠道回答已支付但尚未查清）时会留下活动尝试，
    此时取消订单会与可能到来的成功收款竞争，必须 fail closed。
    """
    total = 0
    for installment in bridge.load_installments_for_order(session, order_id):
        total += len(repo.list_active_attempts_for_installment(session, str(installment["id"])))
    return total


def request_refund(
    session: Session, *, refund_id: str, actor_id: str, idempotency_key: str
) -> RefundOutcome:
    """执行退款：原子预留可退额度后按原渠道退款。"""
    return svc.request_refund(
        session, refund_id=refund_id, actor_id=actor_id, idempotency_key=idempotency_key
    )


def apply_refund_outcome(
    session: Session,
    *,
    refund_id: str,
    status: str,
    provider_refund_id: str | None,
    amount_cents: int,
) -> dict[str, Any]:
    """写回渠道退款结果（成功才扣减、失败且证实未退才释放预留）。"""
    return svc.apply_refund_outcome(
        session,
        refund_id=refund_id,
        status=status,
        provider_refund_id=provider_refund_id,
        amount_cents=amount_cents,
    )


__all__ = [
    "apply_refund_outcome",
    "apply_verified_payment",
    "close_payment_attempts_for_installment",
    "create_intent",
    "mark_order_closing",
    "order_paid_summary",
    "request_refund",
    "unresolved_attempt_count",
]
