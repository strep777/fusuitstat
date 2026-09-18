"""身份模块的 HTTP 输入输出 DTO 与稳定枚举。

枚举由后端定义，前端从 OpenAPI 生成的类型读取，禁止手抄多份（06 文档）。
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

PhoneField = Annotated[str, Field(min_length=5, max_length=32)]

LocaleCode = Literal["zh-CN", "en-US"]
CurrencyCode = Literal["CNY", "USD"]


class StrictModel(BaseModel):
    """额外字段直接拒绝，避免前端把 user_id、金额等塞进请求体。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ------------------------------------------------------------------ 认证
class SmsChallengeRequest(StrictModel):
    phone: PhoneField
    purpose: Literal["login", "phone_change_old", "phone_change_new", "sensitive_action"] = "login"
    captcha_token: str | None = Field(default=None, max_length=200)
    locale: LocaleCode = "zh-CN"


class SmsChallengeResponse(BaseModel):
    challenge_id: str
    expires_in: int
    retry_after: int
    # 仅开发/测试环境返回，便于本地联调；生产恒为 None（不存在万能码）。
    dev_hint: str | None = None


class SmsVerifyRequest(StrictModel):
    challenge_id: str = Field(min_length=4, max_length=48)
    phone: PhoneField
    code: str = Field(min_length=4, max_length=8)
    terms_version: str | None = Field(default=None, max_length=40)
    privacy_version: str | None = Field(default=None, max_length=40)
    accepted: bool = False
    return_to: str | None = Field(default=None, max_length=300)

    @field_validator("code")
    @classmethod
    def _digits_only(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned.isdigit():
            raise ValueError("验证码只能是数字")
        return cleaned


class UserSummary(BaseModel):
    id: str
    nickname: str
    phone_masked: str
    locale: str
    currency: str
    region: str
    status: str
    created_at: str | None = None
    # 乐观锁版本号：前端编辑资料时回传，避免静默覆盖。
    version: int | None = None


class SessionResponse(BaseModel):
    user: UserSummary
    session_expires_at: str
    idle_expires_at: str
    csrf_token: str
    is_new_user: bool = False


class CsrfResponse(BaseModel):
    csrf_token: str
    expires_in: int


class PointsBadge(BaseModel):
    """账户总览用的星绒币摘要。权威数值来自积分账户，前端不自行估算。"""

    posted_balance: int = 0
    available_points: int = 0
    pending_points: int = 0
    rules_version: str = ""


class MeResponse(BaseModel):
    user: UserSummary
    permissions: list[str] = Field(default_factory=list)
    sensitive_verified: bool = False
    unread_notifications: int = 0
    pending_actions: dict[str, int] = Field(default_factory=dict)
    points: PointsBadge = Field(default_factory=PointsBadge)


class MeUpdateRequest(StrictModel):
    nickname: str | None = Field(default=None, min_length=1, max_length=40)
    locale: LocaleCode | None = None
    currency: CurrencyCode | None = None
    avatar_asset_id: str | None = Field(default=None, max_length=48)
    version: int = Field(ge=1)


class UserSessionItem(BaseModel):
    id: str
    scope: str
    user_agent: str | None
    created_at: str
    last_seen_at: str
    expires_at: str
    current: bool = False


class PhoneChangeChallengeRequest(StrictModel):
    role: Literal["old", "new", "sensitive"]
    phone: PhoneField | None = None


class SensitiveVerifyRequest(StrictModel):
    """敏感动作的短信二次验证输入。验证码必须由服务端核验。"""

    challenge_id: str = Field(min_length=4, max_length=48)
    code: str = Field(min_length=4, max_length=8)


class PhoneChangeConfirmRequest(StrictModel):
    old_challenge_id: str | None = Field(default=None, max_length=48)
    old_code: str | None = Field(default=None, max_length=8)
    new_challenge_id: str = Field(max_length=48)
    new_code: str = Field(min_length=4, max_length=8)
    manual_appeal: bool = False
    evidence_note: str | None = Field(default=None, max_length=500)


class ClosureRequest(StrictModel):
    reason: str | None = Field(default=None, max_length=300)
    challenge_id: str = Field(max_length=48)
    code: str = Field(min_length=4, max_length=8)
    acknowledged_blockers: bool = False


class ClosureResponse(BaseModel):
    status: str
    blockers: list[str]
    scheduled_for: str | None = None
    message: str


# ------------------------------------------------------------------ 地址
class AddressInput(StrictModel):
    label: str = Field(default="默认地址", max_length=30)
    recipient: str = Field(min_length=1, max_length=60)
    phone: PhoneField
    region_code: str = Field(default="CN", max_length=10)
    province: str = Field(default="", max_length=40)
    city: str = Field(default="", max_length=40)
    detail: str = Field(min_length=4, max_length=200)
    postal_code: str | None = Field(default=None, max_length=20)
    is_default: bool = False


class AddressPatch(StrictModel):
    label: str | None = Field(default=None, max_length=30)
    recipient: str | None = Field(default=None, min_length=1, max_length=60)
    phone: PhoneField | None = None
    province: str | None = Field(default=None, max_length=40)
    city: str | None = Field(default=None, max_length=40)
    detail: str | None = Field(default=None, min_length=4, max_length=200)
    postal_code: str | None = Field(default=None, max_length=20)
    is_default: bool | None = None
    version: int = Field(ge=1)


class AddressResponse(BaseModel):
    id: str
    label: str
    recipient: str
    phone_masked: str
    region_code: str
    province: str
    city: str
    detail: str
    postal_code: str | None
    is_default: bool
    version: int


# ------------------------------------------------------------------ 员工
class StaffLoginRequest(StrictModel):
    login_name: str = Field(min_length=3, max_length=40)
    password: str = Field(min_length=8, max_length=200)


class StaffMfaRequest(StrictModel):
    challenge_token: str = Field(max_length=200)
    code: str = Field(min_length=6, max_length=12)


class StaffMfaEnrollResponse(BaseModel):
    secret: str
    otpauth_uri: str
    recovery_codes: list[str]


class StaffSessionResponse(BaseModel):
    staff_id: str
    display_name: str
    login_name: str
    permissions: list[str]
    roles: list[str]
    session_expires_at: str
    csrf_token: str
    mfa_enabled: bool


class StaffLoginPending(BaseModel):
    status: Literal["MFA_REQUIRED", "MFA_ENROLL_REQUIRED"]
    challenge_token: str
    enroll: StaffMfaEnrollResponse | None = None


class StaffItem(BaseModel):
    id: str
    login_name: str
    display_name: str
    email: str
    status: str
    mfa_enabled: bool
    roles: list[str]
    last_login_at: str | None
    is_owner: bool


class StaffCreateRequest(StrictModel):
    login_name: str = Field(min_length=3, max_length=40)
    display_name: str = Field(min_length=1, max_length=40)
    email: str = Field(default="", max_length=120)
    password: str = Field(min_length=12, max_length=200)
    roles: list[str] = Field(min_length=1, max_length=6)


class StaffUpdateRequest(StrictModel):
    display_name: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=120)
    status: Literal["active", "suspended", "retired"] | None = None
    roles: list[str] | None = Field(default=None, max_length=6)
    reason: str | None = Field(default=None, max_length=300)
    version: int = Field(ge=1)


class StaffPasswordResetRequest(StrictModel):
    password: str = Field(min_length=12, max_length=200)
    reason: str = Field(min_length=2, max_length=300)


class ConsentInput(StrictModel):
    purpose: Literal[
        "terms",
        "privacy",
        "commission_rights",
        "marketing_email",
        "showcase_identity",
        "showcase_work",
        "miner_guardian",
        "measurement_data",
    ]
    document_version: str = Field(max_length=40)
    accepted: bool
    resource_type: str | None = Field(default=None, max_length=40)
    resource_id: str | None = Field(default=None, max_length=48)


class EmailVerificationRequest(StrictModel):
    email: EmailStr = Field(max_length=254)


class EmailVerificationConfirm(StrictModel):
    code: str = Field(pattern=r"^\d{6}$")
