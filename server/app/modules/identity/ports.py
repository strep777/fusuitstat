"""跨模块能力声明：身份模块对其他模块只暴露这些函数与 DTO。

其他模块不得导入本模块的 ORM 模型或 repository（04 文档）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.modules.identity import repository as repo
from app.modules.identity.models import User


@dataclass(frozen=True)
class Principal:
    """请求主体。员工与客户会话严格分离，不互相继承权限。"""

    kind: str  # "customer" | "staff" | "visitor"
    id: str | None
    display_name: str
    permissions: frozenset[str]
    status: str = "active"

    @property
    def is_authenticated(self) -> bool:
        return self.kind in ("customer", "staff") and self.id is not None

    @property
    def is_staff(self) -> bool:
        return self.kind == "staff"

    def require_customer(self) -> str:
        from app.platform.errors import AppError

        if self.kind != "customer" or not self.id:
            raise AppError("AUTH_REQUIRED")
        if self.status != "active":
            raise AppError("FORBIDDEN", "账号状态受限，请联系客服。")
        return self.id

    def has(self, permission: str) -> bool:
        return permission in self.permissions

    def require(self, permission: str) -> None:
        from app.platform.errors import AppError

        if not self.is_staff:
            raise AppError("FORBIDDEN", "该操作仅限工作室员工。")
        if permission not in self.permissions:
            raise AppError("FORBIDDEN", "当前员工账号没有执行该操作的权限。")

    def require_staff(self) -> str:
        """员工主体 ID。用于审计与责任字段，避免把 ``None`` 写进操作人。"""
        from app.platform.errors import AppError

        if not self.is_staff or not self.id:
            raise AppError("FORBIDDEN", "该操作仅限工作室员工。")
        return self.id


VISITOR = Principal(kind="visitor", id=None, display_name="访客", permissions=frozenset())


def get_user(session: Session, user_id: str) -> User | None:
    return repo.get_user(session, user_id)


def display_name(user: User) -> str:
    return user.nickname or "星友"


def user_is_active(user: User) -> bool:
    return user.status == "active"


def masked_phone(user: User) -> str:
    return user.phone_masked


def owner_check(resource_owner_id: str | None, principal: Principal) -> bool:
    """逐对象归属检查。403 不泄露对象是否存在。"""
    if principal.kind == "staff" and principal.id:
        return True
    return bool(principal.id) and resource_owner_id == principal.id


def verified_email(session: Session, user_id: str) -> str | None:
    from app.modules.identity.email import verified_email as resolve
    return resolve(session, user_id)


def get_address(session: Session, *, address_id: str, user_id: str) -> Any | None:
    """本人地址（逐对象归属过滤）。地址属身份模块数据，商务模块只经此读取。"""
    return repo.get_address(session, address_id=address_id, user_id=user_id)


def staff_permissions(session: Session, staff_id: str) -> frozenset[str]:
    staff = repo.get_staff(session, staff_id)
    if not staff or staff.status != "active":
        return frozenset()
    return repo.permissions_for_staff(session, staff_id)


def resolve_active_staff(session: Session, reference: str | None) -> str | None:
    """把员工登录名或员工 ID 解析成员工 ID；空值表示退回未分派，找不到返回 ``None``。"""
    candidate = (reference or "").strip()
    if not candidate:
        return None
    staff = repo.get_staff(session, candidate) or repo.get_staff_by_login(session, candidate)
    if staff is None or staff.status != "active":
        return None
    return staff.id
