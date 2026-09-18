"""跨模块共享的 HTTP DTO 基类与分页形状。

列表统一使用 ``items、next_cursor、has_more``；静态内容可用 page 页码，
但同一资源不混用两种分页（06 文档）。
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

LimitField = Annotated[int, Field(ge=1, le=100)]
CursorField = Annotated[str | None, Field(default=None, max_length=200)]

#: 面向用户的运营文案：可以是所有语言共用的一句话，也可以是 `{"zh-CN": …, "en-US": …}`。
#: 服务端**不按请求语言裁剪**，由客户端按当前语言解析（06 文档「多语言文本」一节）：
#: 这样切换语言不需要重新取配置，也不会出现两份设置互相漂移。
LocalizedTextDto = str | dict[str, str]


class Dto(BaseModel):
    """输出 DTO。允许从 ORM 属性构造，但不允许多余字段。"""

    model_config = ConfigDict(from_attributes=True)


class StrictDto(Dto):
    """输入 DTO：额外字段直接拒绝。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Page[T](Dto):
    items: list[T]
    next_cursor: str | None = None
    has_more: bool = False


class PageMeta(StrictDto):
    page: int = Field(default=1, ge=1)
    limit: LimitField = 20

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.limit


class VersionedInput(StrictDto):
    """带乐观锁版本号的编辑输入。"""

    version: int = Field(ge=1)


class OkResponse(Dto):
    ok: bool = True
    message: str | None = None


class IdResponse(Dto):
    id: str


class StatusResponse(Dto):
    status: str
    message: str | None = None


class Money(Dto):
    """金额一律整数分 + 显式币种。"""

    amount_cents: int = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)


def require_non_empty(value: list[str] | None, field: str) -> list[str]:
    """校验列表非空，用于「至少选一个」的输入。"""
    if not value:
        raise ValueError(f"{field} 不能为空")
    return value


__all__ = [
    "CursorField",
    "Dto",
    "IdResponse",
    "LimitField",
    "LocalizedTextDto",
    "Money",
    "OkResponse",
    "Page",
    "PageMeta",
    "StatusResponse",
    "StrictDto",
    "VersionedInput",
    "model_validator",
    "require_non_empty",
]
