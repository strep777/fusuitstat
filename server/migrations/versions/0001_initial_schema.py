"""initial schema（用 Base.metadata 反射式建表）

Revision ID: 0001
Revises:
Create Date: 2026-09-17

唯一初始迁移：``import app.modules`` 触发全部模型注册后，直接用 ``Base.metadata``
建表，保证迁移结果与 ORM 定义逐列一致（05 文档：字段与约束的唯一规则源）。

- 尚未交付的模块由 ``app.modules`` 内部 ``try/except ImportError`` 容忍，已交付的表
  照常创建，不因为某个模块缺席就让迁移失败；
- 后续增量迁移必须走 Alembic revision，不要在运行时用 ``create_all`` 补列；
- SQLite 的列变更使用 ``op.batch_alter_table``（env.py 已设置 ``render_as_batch``）。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

import app.modules  # noqa: F401  触发全部模型注册
from app.platform.orm import Base

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """建立全部已交付模块的表与约束。"""
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    """回退初始迁移：删除全部表。

    破坏性操作：已有真实交易时禁止用回退代替正向修复（14 文档「回滚」）。
    """
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
