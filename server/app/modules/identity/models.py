"""身份模块：客户手机号登录、会话、员工账号与权限、同意记录、地址。

拥有的数据：users、sms_challenges、sessions、staff_accounts、roles/permissions、
consents、addresses。
公开能力：``ports.py`` 中的主体查询与逐资源归属校验。
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.ids import new_id, now_ms
from app.platform.orm import Base, PkMixin, TimestampMixin, VersionMixin

USER_STATUSES = ("active", "restricted", "closed")
SESSION_SCOPES = ("customer", "admin")
CONSENT_PURPOSES = (
    "terms",
    "privacy",
    "commission_rights",
    "marketing_email",
    "showcase_identity",
    "showcase_work",
    "miner_guardian",
    "measurement_data",
)
CHALLENGE_PURPOSES = ("login", "phone_change_old", "phone_change_new", "sensitive_action")
STAFF_STATUSES = ("active", "suspended", "retired")


class User(PkMixin, TimestampMixin, VersionMixin, Base):
    """客户账号。手机号加密存储，另用独立密钥 HMAC 查找。"""

    __tablename__ = "users"
    id_kind = "user"

    phone_ciphertext: Mapped[str] = mapped_column(sa.Text, nullable=False)
    phone_lookup: Mapped[str] = mapped_column(sa.String(64), nullable=False, unique=True, index=True)
    phone_key_version: Mapped[str] = mapped_column(sa.String(16), nullable=False, default="v1")
    phone_masked: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    nickname: Mapped[str] = mapped_column(sa.String(40), nullable=False, default="星友")
    avatar_asset_id: Mapped[str | None] = mapped_column(sa.String(48))
    locale: Mapped[str] = mapped_column(sa.String(10), nullable=False, default="zh-CN")
    currency: Mapped[str] = mapped_column(sa.String(3), nullable=False, default="CNY")
    region: Mapped[str] = mapped_column(sa.String(10), nullable=False, default="CN")
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False, default="active")
    status_reason: Mapped[str | None] = mapped_column(sa.String(200))
    phone_changed_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    closure_requested_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    closed_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    last_login_at: Mapped[int | None] = mapped_column(sa.BigInteger)

    __table_args__ = (
        sa.CheckConstraint("status IN ('active','restricted','closed')", name="ck_users_status"),
    )


class SmsChallenge(PkMixin, TimestampMixin, Base):
    """验证码挑战。只保存 HMAC，不保存明文；一次性原子消费。"""

    __tablename__ = "sms_challenges"
    id_kind = "challenge"

    purpose: Mapped[str] = mapped_column(sa.String(30), nullable=False)
    phone_lookup: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    phone_ciphertext: Mapped[str] = mapped_column(sa.Text, nullable=False)
    code_hmac: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    expires_at: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    attempts: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=5)
    consumed_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    superseded_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    send_status: Mapped[str] = mapped_column(sa.String(20), nullable=False, default="queued")
    send_error: Mapped[str | None] = mapped_column(sa.String(200))
    send_channel_ref: Mapped[str | None] = mapped_column(sa.String(80))
    request_ip_hash: Mapped[str | None] = mapped_column(sa.String(32))
    user_id: Mapped[str | None] = mapped_column(sa.ForeignKey("users.id", ondelete="SET NULL"))

    __table_args__ = (
        sa.Index("ix_sms_challenges_lookup", "phone_lookup", "purpose", "created_at"),
        sa.Index("ix_sms_challenges_expiry", "expires_at"),
    )


class Session(PkMixin, TimestampMixin, Base):
    """不透明会话。只保存 token 哈希；CSRF 密钥同样只存哈希。"""

    __tablename__ = "sessions"
    id_kind = "session"

    user_id: Mapped[str | None] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"), index=True)
    staff_id: Mapped[str | None] = mapped_column(sa.ForeignKey("staff_accounts.id", ondelete="CASCADE"))
    scope: Mapped[str] = mapped_column(sa.String(16), nullable=False, default="customer")
    token_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False, unique=True)
    csrf_secret_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    user_agent: Mapped[str | None] = mapped_column(sa.String(200))
    ip_hash: Mapped[str | None] = mapped_column(sa.String(32))
    expires_at: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    idle_expires_at: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    last_seen_at: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, default=now_ms)
    sensitive_verified_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    revoked_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    revoked_reason: Mapped[str | None] = mapped_column(sa.String(80))

    __table_args__ = (
        sa.CheckConstraint("scope IN ('customer','admin')", name="ck_sessions_scope"),
        sa.Index("ix_sessions_user_active", "user_id", "revoked_at"),
    )


class Consent(PkMixin, Base):
    """同意与授权留证。版本不覆盖；可撤回授权单独记录。"""

    __tablename__ = "consents"

    user_id: Mapped[str | None] = mapped_column(sa.ForeignKey("users.id", ondelete="SET NULL"), index=True)
    purpose: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    document_version: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    content_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(sa.String(40))
    resource_id: Mapped[str | None] = mapped_column(sa.String(48))
    accepted_at: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, default=now_ms)
    revoked_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    revoke_reason: Mapped[str | None] = mapped_column(sa.String(200))
    evidence: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON)

    __table_args__ = (sa.Index("ix_consents_user_purpose", "user_id", "purpose"),)


class Address(PkMixin, TimestampMixin, VersionMixin, Base):
    """收货地址。字段加密；查询必带 user_id；删除不改订单快照。"""

    __tablename__ = "addresses"
    id_kind = "address"

    user_id: Mapped[str] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    label: Mapped[str] = mapped_column(sa.String(30), nullable=False, default="默认地址")
    recipient_ciphertext: Mapped[str] = mapped_column(sa.Text, nullable=False)
    phone_ciphertext: Mapped[str] = mapped_column(sa.Text, nullable=False)
    address_ciphertext: Mapped[str] = mapped_column(sa.Text, nullable=False)
    region_code: Mapped[str] = mapped_column(sa.String(10), nullable=False, default="CN")
    province: Mapped[str] = mapped_column(sa.String(40), nullable=False, default="")
    city: Mapped[str] = mapped_column(sa.String(40), nullable=False, default="")
    postal_code: Mapped[str | None] = mapped_column(sa.String(20))
    is_default: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)

    __table_args__ = (
        sa.CheckConstraint("is_default IN (0,1)", name="ck_addresses_default"),
        sa.Index("ix_addresses_user", "user_id", "is_default"),
    )


class StaffAccount(PkMixin, TimestampMixin, VersionMixin, Base):
    """员工账号。独立登录、Argon2id 口令、TOTP MFA；无公开注册。"""

    __tablename__ = "staff_accounts"
    id_kind = "staff"

    login_name: Mapped[str] = mapped_column(sa.String(40), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    email: Mapped[str] = mapped_column(sa.String(120), nullable=False, default="")
    password_hash: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    mfa_secret_ciphertext: Mapped[str | None] = mapped_column(sa.Text)
    mfa_enabled: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    mfa_confirmed_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    recovery_code_hashes: Mapped[list[Any] | None] = mapped_column(sa.JSON)
    failed_login_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    locked_until: Mapped[int | None] = mapped_column(sa.BigInteger)
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False, default="active")
    last_login_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    is_owner: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)

    __table_args__ = (
        sa.CheckConstraint("status IN ('active','suspended','retired')", name="ck_staff_status"),
        sa.CheckConstraint("mfa_enabled IN (0,1)", name="ck_staff_mfa"),
    )


class Role(PkMixin, Base):
    __tablename__ = "roles"

    code: Mapped[str] = mapped_column(sa.String(40), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(sa.String(60), nullable=False)
    description: Mapped[str] = mapped_column(sa.String(200), nullable=False, default="")
    is_system: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)


class Permission(PkMixin, Base):
    __tablename__ = "permissions"

    code: Mapped[str] = mapped_column(sa.String(60), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(sa.String(80), nullable=False)
    group: Mapped[str] = mapped_column(sa.String(40), nullable=False, default="general")


class StaffRole(Base):
    __tablename__ = "staff_roles"

    staff_id: Mapped[str] = mapped_column(
        sa.ForeignKey("staff_accounts.id", ondelete="CASCADE"), primary_key=True
    )
    role_id: Mapped[str] = mapped_column(sa.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)
    granted_at: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, default=now_ms)
    granted_by: Mapped[str | None] = mapped_column(sa.String(48))


class RolePermission(Base):
    __tablename__ = "role_permissions"

    role_id: Mapped[str] = mapped_column(sa.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)
    permission_id: Mapped[str] = mapped_column(
        sa.ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True
    )


class MfaChallenge(PkMixin, Base):
    """员工后台 MFA 登录挑战：一次性、短期限，只保存 token 哈希与待绑定密钥密文。"""

    __tablename__ = "staff_mfa_challenges"

    staff_id: Mapped[str] = mapped_column(
        sa.ForeignKey("staff_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False, unique=True)
    pending_secret_ciphertext: Mapped[str | None] = mapped_column(sa.Text)
    pending_recovery_hashes: Mapped[list[Any] | None] = mapped_column(sa.JSON)
    expires_at: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    consumed_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    attempts: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    created_at: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)


class PhoneChangeRequest(PkMixin, TimestampMixin, Base):
    """换绑申请。旧号丢失走人工申诉，不能只凭订单号换绑。"""

    __tablename__ = "phone_change_requests"

    user_id: Mapped[str] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    old_phone_lookup: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    new_phone_ciphertext: Mapped[str] = mapped_column(sa.Text, nullable=False)
    new_phone_lookup: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    status: Mapped[str] = mapped_column(sa.String(20), nullable=False, default="PENDING")
    manual_appeal: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    evidence_note: Mapped[str | None] = mapped_column(sa.String(500))
    completed_at: Mapped[int | None] = mapped_column(sa.BigInteger)


__all__ = [
    "Address",
    "CHALLENGE_PURPOSES",
    "CONSENT_PURPOSES",
    "Consent",
    "Permission",
    "PhoneChangeRequest",
    "Role",
    "RolePermission",
    "SESSION_SCOPES",
    "STAFF_STATUSES",
    "Session",
    "SmsChallenge",
    "StaffAccount",
    "StaffRole",
    "USER_STATUSES",
    "User",
    "new_id",
]


class CustomerEmail(Base):
    """客户交易邮箱与一次性验证挑战，地址加密，验证码只存 HMAC。"""
    __tablename__ = "customer_emails"
    user_id: Mapped[str] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    email_ciphertext: Mapped[str | None] = mapped_column(sa.Text)
    verified_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    pending_ciphertext: Mapped[str | None] = mapped_column(sa.Text)
    challenge_hash: Mapped[str | None] = mapped_column(sa.String(64))
    expires_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    attempts: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    sent_at: Mapped[int | None] = mapped_column(sa.BigInteger)
