"""业务模块集合。

导入本包即注册全部 ORM 模型（迁移与建表依赖这一行为）。
尚未落地的模块用 ``ImportError`` 容忍，便于增量开发期间部分模块缺席。
"""

from __future__ import annotations

import importlib

# 顺序无关，但保持稳定以便阅读。
_MODULES = (
    "identity",
    "content",
    "catalog",
    "media",
    "notifications",
    "commissions",
    "commerce",
    "orders",
    "payments",
    "loyalty",
    "feedback",
)

for _name in _MODULES:
    try:
        importlib.import_module(f"app.modules.{_name}.models")
        # 模型不在 ``models`` 子模块里的包（例如积分表挂在其他模块下）：
        # 再导入一次包本身，确保包级导入路径也能注册模型。
        importlib.import_module(f"app.modules.{_name}")
    except ImportError:  # pragma: no cover - 增量开发期间的容错
        continue

del importlib, _name

__all__: list[str] = []
