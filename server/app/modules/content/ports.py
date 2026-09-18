"""业务模块读取配置的公开端口。

其他模块**不得**直接导入 ``content.service``（04 文档）；设置项与功能开关只经这里读取。
规则本身仍只有一个来源：``content.service`` 与 ``content.policies``。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

__all__ = ["feature_flags", "loyalty_policy", "require_feature", "setting_value"]


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
