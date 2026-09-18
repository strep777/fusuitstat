"""ORM 基类与通用列。

约定（05 文档）：
- 公共主键用 UUID 字符串（带类型前缀，URL 安全）；
- 业务时间以 UTC 毫秒 INTEGER 持久化；
- 业务表通用 ``id、created_at、updated_at、version``；
- 账本、回调与审计只追加，不使用 ``updated_at``/``version``。
"""

from __future__ import annotations

from typing import ClassVar

import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

from app.platform.ids import new_id, now_ms


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""

    type_annotation_map = {
        dict: sa.JSON,
        list: sa.JSON,
    }


class TimestampMixin:
    created_at: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, default=now_ms)
    updated_at: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, default=now_ms, onupdate=now_ms)


class VersionMixin:
    """乐观锁版本号。编辑冲突返回 409 VERSION_CONFLICT。"""

    version: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=1)


class PkMixin:
    """公开随机主键。

    ``id_kind`` 决定前缀，便于排障时辨认对象类型。列定义用 ``declared_attr``：
    映射发生在子类上，闭包因此能读到子类的 ``id_kind``；若直接在混入类里写
    ``lambda: new_id(self.id_kind)``，``self`` 会解析到混入类作用域而报 ``NameError``。
    """

    id_kind: ClassVar[str] = "object"

    @declared_attr
    def id(cls) -> Mapped[str]:  # noqa: N805 - declared_attr 约定使用 cls
        kind = getattr(cls, "id_kind", "object")
        return mapped_column(sa.String(48), primary_key=True, default=lambda: new_id(kind))


def money_column(nullable: bool = False, default: int | None = None) -> Mapped[int]:
    """整数分金额列，非负由 CHECK 约束保证（见各表 __table_args__）。"""
    return mapped_column(sa.Integer, nullable=nullable, default=default)


def enum_column(values: tuple[str, ...], *, default: str | None = None) -> Mapped[str]:
    """枚举列：TEXT + CHECK，值域在数据库层受约束。"""
    check = "({col} IN ({vals}))".format(
        col="{col}", vals=", ".join(f"'{value}'" for value in values)
    )
    return mapped_column(
        sa.String(40),
        nullable=default is None,
        default=default,
        info={"enum_values": values, "enum_check": check},
    )
