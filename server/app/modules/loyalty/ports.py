"""积分模块对外端口（跨模块契约，签名不可改）。

其他模块只通过本文件与 ``app.modules.contracts`` 声明的 DTO 协作，不直接导入
积分模块的 ORM 表或 repository（04 文档）。

对外能力：

- ``recompute_award``：按来源重算目标积分并入账（同一事务）；
- ``account_view``：账户视图；
- ``post_due_awards``：worker 到点入账；
- ``apply_adjustment``：后台调整审批入账。

入站依赖：

- ``order_snapshot``：读取商务模块的权威订单奖励快照。商务模块由另一位工程师并行
  实现，导入失败时抛 ``PROVIDER_UNAVAILABLE``，让积分模块可以独立测试与隔离部署。

``order_reward_snapshot`` 的键约定（与 ``commerce.ports`` 一致）：
``order_id、user_id、status、eligible_net_cents、paid_goods_cents、
refunded_goods_cents、paid_shipping_cents、refunded_shipping_cents、
completed_at（ISO 字符串）、has_open_after_sales``。
积分模块只把它当作**当前**快照使用，永远不信任旧事件携带的金额或点数（09 文档）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.modules.contracts import AwardRequest
from app.modules.loyalty import service
from app.platform.errors import AppError


def recompute_award(session: Session, request: AwardRequest) -> dict[str, Any]:
    """重算并（在可入账时）入账来源奖励。调用方负责提交事务。"""
    return service.recompute_award(session, request)


def account_view(session: Session, *, user_id: str) -> dict[str, Any]:
    """``{posted_balance,reserved_points,available_points,pending_points,rules_version}``。"""
    return service.account_view(session, user_id=user_id)


def post_due_awards(session: Session, *, now: int | None = None, limit: int = 50) -> int:
    """把到期且无未决售后的奖励入账；返回入账条数。"""
    return service.post_due_awards(session, now=now, limit=limit)


def apply_adjustment(session: Session, *, adjustment_id: str, approver_id: str | None) -> dict[str, Any]:
    """批准调整单并追加流水入账（权限码 ``points.approve``）。"""
    return service.approve_adjustment(session, adjustment_id=adjustment_id, approver_id=approver_id)


def reverse_award(
    session: Session, *, source_type: str, source_id: str, reason: str, actor_id: str | None
) -> dict[str, Any]:
    """复核冲正来源奖励（确认作弊/误发）：追加反向流水，原流水保留。"""
    return service.reverse_award(
        session, source_type=source_type, source_id=source_id, reason=reason, actor_id=actor_id
    )


def order_snapshot(session: Session, order_id: str) -> dict[str, Any]:
    """入站端口：商务模块的订单奖励快照（当前已核验金额与成功退款）。"""
    try:
        from app.modules.commerce.ports import order_reward_snapshot
    except ImportError as exc:  # 商务模块尚未就绪：隔离测试与灰度部署都能继续
        raise AppError("PROVIDER_UNAVAILABLE", "商务模块尚未就绪") from exc
    return order_reward_snapshot(session, order_id)


__all__ = [
    "account_view",
    "apply_adjustment",
    "order_snapshot",
    "post_due_awards",
    "recompute_award",
    "reverse_award",
]
