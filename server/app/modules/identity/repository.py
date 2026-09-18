"""身份模块查询。只在本模块内使用；其他模块通过 ``ports.py``。"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.modules.identity.models import (
    Address,
    Consent,
    Permission,
    Role,
    RolePermission,
    SmsChallenge,
    StaffAccount,
    StaffRole,
    User,
)
from app.modules.identity.models import (
    Session as SessionModel,
)
from app.platform.db import affected_rows
from app.platform.ids import now_ms


# ---------------------------------------------------------------- users
def get_user(session: Session, user_id: str) -> User | None:
    return session.get(User, user_id)


def get_user_by_phone_lookup(session: Session, phone_lookup: str) -> User | None:
    return session.scalars(sa.select(User).where(User.phone_lookup == phone_lookup)).one_or_none()


def count_users(session: Session) -> int:
    return int(session.scalar(sa.select(sa.func.count()).select_from(User)) or 0)


def list_users(
    session: Session, *, query: str | None = None, status: str | None = None, limit: int = 20, offset: int = 0
) -> list[User]:
    stmt = sa.select(User)
    if query:
        like = f"%{query}%"
        stmt = stmt.where(sa.or_(User.nickname.like(like), User.phone_masked.like(like), User.id == query))
    if status:
        stmt = stmt.where(User.status == status)
    stmt = stmt.order_by(User.created_at.desc()).limit(min(limit, 100)).offset(offset)
    return list(session.scalars(stmt))


# ---------------------------------------------------------------- sms challenges
def latest_active_challenge(
    session: Session, *, phone_lookup: str, purpose: str, moment: int | None = None
) -> SmsChallenge | None:
    now = moment if moment is not None else now_ms()
    return session.scalars(
        sa.select(SmsChallenge)
        .where(
            SmsChallenge.phone_lookup == phone_lookup,
            SmsChallenge.purpose == purpose,
            SmsChallenge.consumed_at.is_(None),
            SmsChallenge.superseded_at.is_(None),
            SmsChallenge.expires_at > now,
        )
        .order_by(SmsChallenge.created_at.desc())
    ).first()


def get_challenge(session: Session, challenge_id: str, *, for_update: bool = False) -> SmsChallenge | None:
    """SQLite 无 SELECT FOR UPDATE；写路径由 ``BEGIN IMMEDIATE`` 保证串行。"""
    _ = for_update
    return session.get(SmsChallenge, challenge_id)


def count_recent_challenges(
    session: Session, *, phone_lookup: str, purpose: str, since_ms: int
) -> int:
    return int(
        session.scalar(
            sa.select(sa.func.count())
            .select_from(SmsChallenge)
            .where(
                SmsChallenge.phone_lookup == phone_lookup,
                SmsChallenge.purpose == purpose,
                SmsChallenge.created_at >= since_ms,
            )
        )
        or 0
    )


def failed_attempt_total(session: Session, *, phone_lookup: str, purpose: str, since_ms: int) -> int:
    """按手机号累计失败次数：换 challenge 不能绕过总失败限制。"""
    return int(
        session.scalar(
            sa.select(sa.func.coalesce(sa.func.sum(SmsChallenge.attempts), 0)).where(
                SmsChallenge.phone_lookup == phone_lookup,
                SmsChallenge.purpose == purpose,
                SmsChallenge.created_at >= since_ms,
            )
        )
        or 0
    )


def purge_expired_challenges(session: Session, *, moment: int | None = None) -> int:
    """过期后最多保留 24 小时，随后清理（10 文档保留表）。"""
    now = moment if moment is not None else now_ms()
    result = session.execute(sa.delete(SmsChallenge).where(SmsChallenge.expires_at < now - 86_400_000))
    return affected_rows(result)


# ---------------------------------------------------------------- sessions
def get_session_by_token_hash(session: Session, token_hash: str) -> SessionModel | None:
    return session.scalars(sa.select(SessionModel).where(SessionModel.token_hash == token_hash)).one_or_none()


def list_sessions_for_user(
    session: Session, *, user_id: str, include_revoked: bool = False
) -> list[SessionModel]:
    stmt = sa.select(SessionModel).where(SessionModel.user_id == user_id)
    if not include_revoked:
        stmt = stmt.where(SessionModel.revoked_at.is_(None))
    return list(session.scalars(stmt.order_by(SessionModel.last_seen_at.desc())))


def list_sessions_for_staff(session: Session, *, staff_id: str) -> list[SessionModel]:
    return list(
        session.scalars(
            sa.select(SessionModel)
            .where(SessionModel.staff_id == staff_id, SessionModel.revoked_at.is_(None))
            .order_by(SessionModel.last_seen_at.desc())
        )
    )


def revoke_user_sessions(
    session: Session, *, user_id: str, reason: str, except_session_id: str | None = None
) -> int:
    now = now_ms()
    stmt = (
        sa.update(SessionModel)
        .where(SessionModel.user_id == user_id, SessionModel.revoked_at.is_(None))
        .values(revoked_at=now, revoked_reason=reason)
    )
    if except_session_id:
        stmt = stmt.where(SessionModel.id != except_session_id)
    return affected_rows(session.execute(stmt))


def revoke_staff_sessions(
    session: Session, *, staff_id: str, reason: str, except_session_id: str | None = None
) -> int:
    now = now_ms()
    stmt = (
        sa.update(SessionModel)
        .where(SessionModel.staff_id == staff_id, SessionModel.revoked_at.is_(None))
        .values(revoked_at=now, revoked_reason=reason)
    )
    if except_session_id:
        stmt = stmt.where(SessionModel.id != except_session_id)
    return affected_rows(session.execute(stmt))


# ---------------------------------------------------------------- consents
def list_consents(session: Session, *, user_id: str, purpose: str | None = None) -> list[Consent]:
    stmt = sa.select(Consent).where(Consent.user_id == user_id)
    if purpose:
        stmt = stmt.where(Consent.purpose == purpose)
    return list(session.scalars(stmt.order_by(Consent.accepted_at.desc())))


def active_consent(
    session: Session, *, user_id: str, purpose: str, resource_id: str | None = None
) -> Consent | None:
    stmt = sa.select(Consent).where(
        Consent.user_id == user_id, Consent.purpose == purpose, Consent.revoked_at.is_(None)
    )
    if resource_id:
        stmt = stmt.where(Consent.resource_id == resource_id)
    return session.scalars(stmt.order_by(Consent.accepted_at.desc())).first()


# ---------------------------------------------------------------- addresses
def list_addresses(session: Session, *, user_id: str) -> list[Address]:
    return list(
        session.scalars(
            sa.select(Address)
            .where(Address.user_id == user_id)
            .order_by(Address.is_default.desc(), Address.created_at.desc())
        )
    )


def get_address(session: Session, *, address_id: str, user_id: str) -> Address | None:
    return session.scalars(
        sa.select(Address).where(Address.id == address_id, Address.user_id == user_id)
    ).one_or_none()


def count_addresses(session: Session, *, user_id: str) -> int:
    return int(
        session.scalar(sa.select(sa.func.count()).select_from(Address).where(Address.user_id == user_id)) or 0
    )


# ---------------------------------------------------------------- staff & rbac
def get_staff(session: Session, staff_id: str) -> StaffAccount | None:
    return session.get(StaffAccount, staff_id)


def get_staff_by_login(session: Session, login_name: str) -> StaffAccount | None:
    return session.scalars(
        sa.select(StaffAccount).where(StaffAccount.login_name == login_name.lower())
    ).one_or_none()


def list_staff(session: Session) -> list[StaffAccount]:
    return list(session.scalars(sa.select(StaffAccount).order_by(StaffAccount.created_at)))


def permissions_for_staff(session: Session, staff_id: str) -> frozenset[str]:
    rows = session.execute(
        sa.select(Permission.code)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .join(StaffRole, StaffRole.role_id == RolePermission.role_id)
        .where(StaffRole.staff_id == staff_id)
    ).scalars()
    return frozenset(rows)


def roles_for_staff(session: Session, staff_id: str) -> list[Role]:
    return list(
        session.scalars(
            sa.select(Role).join(StaffRole, StaffRole.role_id == Role.id).where(StaffRole.staff_id == staff_id)
        )
    )


def get_role_by_code(session: Session, code: str) -> Role | None:
    return session.scalars(sa.select(Role).where(Role.code == code)).one_or_none()


def get_permission_by_code(session: Session, code: str) -> Permission | None:
    return session.scalars(sa.select(Permission).where(Permission.code == code)).one_or_none()


def list_roles(session: Session) -> list[Role]:
    return list(session.scalars(sa.select(Role).order_by(Role.code)))


def list_permissions(session: Session) -> list[Permission]:
    return list(session.scalars(sa.select(Permission).order_by(Permission.group, Permission.code)))
