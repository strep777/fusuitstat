"""运维支撑：一致备份/恢复、受控预渲染与恢复后的重新对账提示。

本包只提供脚本与命令可调用的纯函数，不注册 HTTP 路由（14 文档）。
"""

from __future__ import annotations

from app.ops.backup import checksum, create_backup, restore_to, verify_backup
from app.ops.prebuild import prebuild_routes, run_prebuild

__all__ = [
    "checksum",
    "create_backup",
    "prebuild_routes",
    "restore_to",
    "run_prebuild",
    "verify_backup",
]
