"""反馈模块对外端口（跨模块契约）。

其他模块只通过本文件与 ``app.modules.contracts`` 声明的 DTO 协作，不直接导入
反馈模块的 ORM 表或 repository（04 文档）。

对外能力：

- ``reward_feedback``：按 C14 等级为某条反馈发放奖励（``contracts.FeedbackPort``）；
- ``create_appeal``：为其他模块（如积分流水）登记申诉。

权限说明：审核与发放奖励属于后台动作，权限码为 ``feedback.review``；
正式路由在 ``app/api/admin_routes.py`` 挂载，本文件只提供命令入口。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.modules.feedback import service


@dataclass(frozen=True)
class AppealRequest:
    """其他模块登记申诉的输入。``target_type`` 目前支持 ``feedback``。"""

    user_id: str
    target_type: str
    target_id: str
    reason: str


def reward_feedback(
    session: Session, *, feedback_id: str, level: str, actor_id: str | None, reason: str
) -> dict[str, Any]:
    """按等级发放反馈奖励。

    ``reason`` 同时作为审核判据记录；``major`` 等级的影响陈述请直接调用
    ``feedback.service.review_feedback``（后台路由即如此），以便完整记录影响与判据。
    """
    return service.review_feedback(
        session,
        feedback_id=feedback_id,
        reviewer_id=actor_id,
        result="RESOLVED",
        reason=reason,
        reward_level=level,
        criteria=reason,
        impact=reason if level == "major" else None,
    )


def create_appeal(session: Session, request: AppealRequest) -> dict[str, Any]:
    """为反馈登记申诉（其他模块调用时需要 ``target_type='feedback'``）。"""
    if request.target_type != "feedback":
        from app.platform.errors import AppError

        raise AppError("VALIDATION_ERROR", "申诉目标类型不支持。", fields={"target_type": "不支持"})
    row = service.create_appeal(
        session, user_id=request.user_id, feedback_id=request.target_id, reason=request.reason
    )
    return service.serialize_appeal(row)


__all__ = ["AppealRequest", "create_appeal", "reward_feedback"]
