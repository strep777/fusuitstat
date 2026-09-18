"""业务模块读取配置的公开端口。

其他模块**不得**直接导入 ``content.service``（04 文档）；设置项与功能开关只经这里读取。
规则本身仍只有一个来源：``content.service`` 与 ``content.policies``。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

__all__ = [
    "add_workdays",
    "feature_flags",
    "loyalty_policy",
    "require_feature",
    "response_deadline",
    "response_sla_workdays",
    "setting_value",
    "support_calendar",
]


def require_feature(session: Session, name: str) -> None:
    """功能开关未启用时抛 ``FEATURE_DISABLED``（入口隐藏且后端拒绝）。"""
    from app.modules.content.service import require_feature as require

    require(session, name)


def feature_flags(session: Session) -> dict[str, bool]:
    from app.modules.content.service import feature_flags as read

    return read(session)


def setting_value(session: Session, key: str, default: Any = None) -> Any:
    from app.modules.content.service import setting_value as read

    return read(session, key, default)


def loyalty_policy(session: Session) -> dict[str, Any]:
    """星绒币规则（每 5 元 1 星绒币等）的唯一读取入口。"""
    from app.modules.content.service import loyalty_policy as read

    return read(session)


def support_calendar(session: Session) -> dict[str, Any]:
    """客服工作日与节假日（C30）：逾期提醒与首响截止时间都按它计算。"""
    from app.modules.content.service import support_calendar as read

    return read(session)


def response_sla_workdays(session: Session) -> dict[str, int]:
    """首响承诺的工作日数（询价 3 / 反馈 7，来自后台配置）。"""
    from app.modules.content.service import response_sla_workdays as read

    return read(session)


def add_workdays(moment_ms: int, workdays: int, *, calendar: dict[str, Any]) -> int:
    """在给定时刻上加 N 个客服工作日（跳过节假日与周末，计入调休上班日）。"""
    from app.modules.content.service import add_workdays as compute

    return compute(moment_ms, workdays, calendar=calendar)


def response_deadline(session: Session, *, start_ms: int, workdays: int) -> int:
    """首响截止时间 = 起始时间 + N 个客服工作日。"""
    from app.modules.content.service import response_deadline as compute

    return compute(session, start_ms=start_ms, workdays=workdays)
