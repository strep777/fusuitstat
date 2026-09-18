"""后台员工认证：口令 + TOTP MFA，独立会话，权限码授权。

约束（10 文档）：管理员独立账号、Argon2id 口令哈希、TOTP/WebAuthn MFA；
无公开注册、默认密码或生产共享账号；恢复码哈希保存；MFA 重置须身份核验与审计。
"""

from __future__ import annotations

from typing import Any

import pyotp
import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.modules.identity import repository as repo
from app.modules.identity.models import MfaChallenge, StaffAccount, StaffRole
from app.modules.identity.schemas import StaffMfaEnrollResponse, StaffSessionResponse
from app.platform import audit
from app.platform.config import get_settings
from app.platform.errors import AppError
from app.platform.ids import add_minutes_ms, iso, new_id, now_ms, random_token
from app.platform.rate_limit import check as rate_check
from app.platform.security import (
    decrypt_pii,
    encrypt_pii,
    hash_password,
    hash_token,
    verify_password,
)

MFA_CHALLENGE_TTL_MINUTES = 10
MAX_FAILED_LOGINS = 8
LOCK_MINUTES = 15
RECOVERY_CODE_COUNT = 8


def _issue_challenge(
    session: Session, *, staff: StaffAccount, pending_secret: str | None = None, pending_codes: list[str] | None = None
) -> str:
    """创建一次性 MFA 挑战。待绑定密钥只保存在服务端，密文入库。"""
    token = random_token(24)
    row = MfaChallenge(
        id=new_id("job"),
        staff_id=staff.id,
        token_hash=hash_token(token),
        pending_secret_ciphertext=encrypt_pii(pending_secret) if pending_secret else None,
        pending_recovery_hashes=[hash_token(code) for code in pending_codes] if pending_codes else None,
        expires_at=add_minutes_ms(MFA_CHALLENGE_TTL_MINUTES),
        created_at=now_ms(),
    )
    session.add(row)
    session.flush()
    return token


def _load_challenge(session: Session, token: str, *, staff_id: str) -> MfaChallenge:
    row = session.scalars(
        sa.select(MfaChallenge).where(MfaChallenge.token_hash == hash_token(token))
    ).one_or_none()
    if row is None or row.staff_id != staff_id:
        raise AppError("AUTH_FAILED", "验证流程无效，请重新登录。")
    if row.consumed_at is not None:
        raise AppError("AUTH_FAILED", "该验证流程已使用，请重新登录。")
    if row.expires_at <= now_ms():
        raise AppError("AUTH_FAILED", "验证流程已过期，请重新登录。")
    return row


def begin_login(
    session: Session, *, login_name: str, password: str, client_ip: str | None
) -> dict[str, Any]:
    """第一步：口令校验。返回 MFA 挑战或需要首次绑定 MFA。"""
    moment = now_ms()
    rate_check(
        session,
        scope="staff_login_ip",
        subject=client_ip or "unknown",
        limit=30,
        window_seconds=3600,
        code="RATE_LIMITED",
        now=moment,
    )
    staff = repo.get_staff_by_login(session, login_name.lower().strip())
    if staff is None:
        # 统一错误，不暴露账号是否存在。
        raise AppError("AUTH_FAILED", "账号或密码不正确。")
    if staff.status != "active":
        raise AppError("FORBIDDEN", "该员工账号已停用。")
    if staff.locked_until and staff.locked_until > moment:
        raise AppError("RATE_LIMITED", "登录失败次数过多，账号暂时锁定。")

    if not verify_password(password, staff.password_hash):
        staff.failed_login_count += 1
        if staff.failed_login_count >= MAX_FAILED_LOGINS:
            staff.locked_until = add_minutes_ms(LOCK_MINUTES, moment)
        audit.record(
            session,
            actor_type="staff",
            actor_id=staff.id,
            action="staff.login_failed",
            resource_type="staff_account",
            resource_id=staff.id,
            reason="口令校验失败",
            ip=client_ip,
        )
        raise AppError("AUTH_FAILED", "账号或密码不正确。")

    enroll: StaffMfaEnrollResponse | None = None
    if staff.mfa_enabled and staff.mfa_secret_ciphertext:
        challenge_token = _issue_challenge(session, staff=staff)
    else:
        secret = pyotp.random_base32()
        recovery_codes = [
            random_token(6).replace("-", "").replace("_", "")[:10].upper()
            for _ in range(RECOVERY_CODE_COUNT)
        ]
        challenge_token = _issue_challenge(
            session, staff=staff, pending_secret=secret, pending_codes=recovery_codes
        )
        settings = get_settings()
        enroll = StaffMfaEnrollResponse(
            secret=secret,
            otpauth_uri=pyotp.totp.TOTP(secret).provisioning_uri(
                name=staff.login_name, issuer_name=settings.mfa_issuer
            ),
            recovery_codes=recovery_codes,
        )

    session.flush()
    return {
        "status": "MFA_ENROLL_REQUIRED" if enroll else "MFA_REQUIRED",
        "challenge_token": challenge_token,
        "staff_id": staff.id,
        "enroll": enroll,
    }


def complete_login(
    session: Session,
    *,
    challenge_token: str,
    code: str,
    client_ip: str | None,
    user_agent: str | None,
    staff_id: str,
) -> tuple[StaffAccount, Any, str, str]:
    """第二步：校验 TOTP 或恢复码，签发员工会话。"""
    row = _load_challenge(session, challenge_token, staff_id=staff_id)
    staff = repo.get_staff(session, staff_id)
    if staff is None or staff.status != "active":
        raise AppError("AUTH_FAILED", "账号或密码不正确。")

    # 首次绑定：把待绑定密钥落到员工账号上。
    if row.pending_secret_ciphertext:
        staff.mfa_secret_ciphertext = row.pending_secret_ciphertext
        staff.recovery_code_hashes = list(row.pending_recovery_hashes or [])
        first_enrollment = True
    else:
        first_enrollment = False

    if not staff.mfa_secret_ciphertext:
        raise AppError("STATE_CONFLICT", "MFA 尚未初始化，请重新登录完成绑定。")

    secret = decrypt_pii(staff.mfa_secret_ciphertext)
    cleaned = code.strip().replace(" ", "")
    ok = False
    used_recovery = False
    if cleaned.isdigit() and len(cleaned) == 6:
        ok = pyotp.TOTP(secret).verify(cleaned, valid_window=1)
    if not ok:
        hashes = list(staff.recovery_code_hashes or [])
        candidate = hash_token(cleaned.upper())
        if candidate in hashes:
            hashes.remove(candidate)
            staff.recovery_code_hashes = hashes
            used_recovery = True
            ok = True
    if not ok:
        row.attempts += 1
        staff.failed_login_count += 1
        audit.record(
            session,
            actor_type="staff",
            actor_id=staff.id,
            action="staff.mfa_failed",
            resource_type="staff_account",
            resource_id=staff.id,
            reason="MFA 校验失败",
            ip=client_ip,
        )
        if row.attempts >= 5:
            row.consumed_at = now_ms()
        raise AppError("AUTH_FAILED", "验证码不正确。")

    row.consumed_at = now_ms()
    staff.mfa_enabled = 1
    if first_enrollment:
        staff.mfa_confirmed_at = now_ms()
    staff.failed_login_count = 0
    staff.locked_until = None
    staff.last_login_at = now_ms()

    # 使用恢复码后清空其余恢复码，要求重新绑定以保证可追溯。
    if used_recovery:
        staff.recovery_code_hashes = []

    session_row, token, csrf_secret = _issue_staff_session(
        session, staff=staff, client_ip=client_ip, user_agent=user_agent
    )
    audit.record(
        session,
        actor_type="staff",
        actor_id=staff.id,
        actor_label=staff.login_name,
        action="staff.login",
        resource_type="staff_account",
        resource_id=staff.id,
        reason="使用恢复码登录" if used_recovery else ("首次绑定 MFA" if first_enrollment else None),
        ip=client_ip,
    )
    return staff, session_row, token, csrf_secret


def _issue_staff_session(
    session: Session, *, staff: StaffAccount, client_ip: str | None, user_agent: str | None
) -> tuple[Any, str, str]:
    from app.modules.identity import service as identity_service

    return identity_service.issue_session(
        session,
        staff_id=staff.id,
        scope="admin",
        user_agent=user_agent,
        client_ip=client_ip,
    )


def staff_session_response(staff: StaffAccount, row: Any, csrf_token: str, session: Session) -> StaffSessionResponse:
    roles = [role.code for role in repo.roles_for_staff(session, staff.id)]
    permissions = sorted(repo.permissions_for_staff(session, staff.id))
    return StaffSessionResponse(
        staff_id=staff.id,
        display_name=staff.display_name,
        login_name=staff.login_name,
        permissions=permissions,
        roles=roles,
        session_expires_at=iso(row.expires_at) or "",
        csrf_token=csrf_token,
        mfa_enabled=bool(staff.mfa_enabled),
    )


def create_staff(
    session: Session,
    *,
    actor_id: str | None,
    login_name: str,
    display_name: str,
    email: str,
    password: str,
    roles: list[str],
    is_owner: bool = False,
) -> StaffAccount:
    normalized = login_name.lower().strip()
    if repo.get_staff_by_login(session, normalized) is not None:
        raise AppError("STATE_CONFLICT", "该登录名已存在。")
    _validate_password_strength(password)
    staff = StaffAccount(
        id=new_id("staff"),
        login_name=normalized,
        display_name=display_name,
        email=email,
        password_hash=hash_password(password),
        is_owner=1 if is_owner else 0,
    )
    session.add(staff)
    session.flush()
    _assign_roles(session, staff=staff, role_codes=roles, actor_id=actor_id)
    audit.record(
        session,
        actor_type="staff",
        actor_id=actor_id,
        action="staff.create",
        resource_type="staff_account",
        resource_id=staff.id,
        after={"login_name": normalized, "roles": roles},
    )
    return staff


def _validate_password_strength(password: str) -> None:
    if len(password) < 12:
        raise AppError("VALIDATION_ERROR", "员工口令至少 12 位。", fields={"password": "至少 12 位"})
    classes = sum(
        [
            any(ch.islower() for ch in password),
            any(ch.isupper() for ch in password),
            any(ch.isdigit() for ch in password),
            any(not ch.isalnum() for ch in password),
        ]
    )
    if classes < 3:
        raise AppError(
            "VALIDATION_ERROR",
            "员工口令需包含大小写字母、数字、符号中的至少三类。",
            fields={"password": "复杂度不足"},
        )


def _assign_roles(
    session: Session, *, staff: StaffAccount, role_codes: list[str], actor_id: str | None
) -> None:
    session.execute(sa.delete(StaffRole).where(StaffRole.staff_id == staff.id))
    for code in role_codes:
        role = repo.get_role_by_code(session, code)
        if role is None:
            raise AppError("VALIDATION_ERROR", f"角色不存在：{code}", fields={"roles": code})
        session.add(StaffRole(staff_id=staff.id, role_id=role.id, granted_by=actor_id))
    session.flush()


def update_staff(
    session: Session,
    *,
    actor_id: str | None,
    staff_id: str,
    display_name: str | None,
    email: str | None,
    status: str | None,
    roles: list[str] | None,
    reason: str | None,
    version: int,
) -> StaffAccount:
    staff = repo.get_staff(session, staff_id)
    if staff is None:
        raise AppError("RESOURCE_NOT_FOUND")
    if staff.version != version:
        raise AppError("VERSION_CONFLICT")
    before = {
        "display_name": staff.display_name,
        "email": staff.email,
        "status": staff.status,
        "roles": [role.code for role in repo.roles_for_staff(session, staff.id)],
    }
    if staff.is_owner and status and status != "active":
        raise AppError("FORBIDDEN", "不能停用工作室所有者账号。")
    if display_name is not None:
        staff.display_name = display_name
    if email is not None:
        staff.email = email
    if status is not None:
        staff.status = status
        if status != "active":
            repo.revoke_staff_sessions(session, staff_id=staff.id, reason=f"status:{status}")
    if roles is not None:
        _assign_roles(session, staff=staff, role_codes=roles, actor_id=actor_id)
    staff.version += 1
    session.flush()
    audit.record(
        session,
        actor_type="staff",
        actor_id=actor_id,
        action="staff.update",
        resource_type="staff_account",
        resource_id=staff.id,
        before=before,
        after={
            "display_name": staff.display_name,
            "email": staff.email,
            "status": staff.status,
            "roles": [role.code for role in repo.roles_for_staff(session, staff.id)],
        },
        reason=reason,
    )
    return staff


def reset_password(
    session: Session, *, actor_id: str | None, staff_id: str, password: str, reason: str
) -> None:
    staff = repo.get_staff(session, staff_id)
    if staff is None:
        raise AppError("RESOURCE_NOT_FOUND")
    _validate_password_strength(password)
    staff.password_hash = hash_password(password)
    staff.failed_login_count = 0
    staff.locked_until = None
    # MFA 重置必须身份核验与审计：这里要求重新绑定。
    staff.mfa_enabled = 0
    staff.mfa_secret_ciphertext = None
    staff.recovery_code_hashes = []
    staff.version += 1
    repo.revoke_staff_sessions(session, staff_id=staff.id, reason="password_reset")
    audit.record(
        session,
        actor_type="staff",
        actor_id=actor_id,
        action="staff.password_reset",
        resource_type="staff_account",
        resource_id=staff.id,
        reason=reason,
    )


def require_permission(principal_permissions: frozenset[str], permission: str) -> None:
    if permission not in principal_permissions:
        raise AppError("FORBIDDEN", "当前员工账号没有执行该操作的权限。")
