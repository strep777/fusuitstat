"""Alembic 运行环境。

约定（05/14 文档）：
- 数据库地址不在 ``alembic.ini`` 写死，统一从应用配置读取，避免把凭据写进版本库；
- SQLite 使用 **batch 模式**（``render_as_batch=True``）：SQLite 不支持大部分
  ``ALTER TABLE``，列变更必须走「建新表 → 拷数据 → 换名」的批处理；
- 迁移只建表/改表，不做数据回填；数据回填写成独立脚本并可在隔离目录演练；
- 生产禁用 ``create_all`` 代替迁移，唯一初始迁移用 ``Base.metadata`` 全量建表。
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# 保证 `alembic` 从任意工作目录执行都能 import app.*
SERVER_DIR = Path(__file__).resolve().parents[1]
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from app.platform.config import get_settings  # noqa: E402
from app.platform.orm import Base  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# 触发全部模型注册；尚未交付的模块由 app.modules 内部容忍。
import app.modules  # noqa: E402,F401

target_metadata = Base.metadata


def _database_url() -> str:
    """环境变量优先，其次应用配置；两者都不含硬编码凭据。"""
    import os

    return os.environ.get("DATABASE_URL") or get_settings().resolved_database_url


def run_migrations_offline() -> None:
    """离线模式：只生成 SQL，不连接数据库。"""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：连接数据库执行迁移。"""
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        future=True,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            # SQLite 兼容：列变更走批处理（建新表 → 拷数据 → 换名）。
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
