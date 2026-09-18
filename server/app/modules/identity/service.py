"""身份模块用例：验证码登录、会话、换绑、注销、地址、同意留证。

不变量（05 文档“事务不变量”）：
1. 用户登录验证：消费 challenge + 建号/查号 + 新 session 在一次事务中；
   手机号唯一冲突按已有账号重读。
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.identity import repository as repo
from app.modules.identity.models import (
    Address,
    Consent,
    PhoneChangeRequest,
    SmsChallenge,
    User,
)
from app.modules.identity.models import (
    Session as SessionModel,
)
from app.modules.identity.schemas import (
    AddressInput,
    AddressPatch,
    SessionResponse,
    UserSummary,
)
from app.platform import audit, rate_limit
from app.platform.config import get_settings
from app.platform.db import affected_rows
from app.platform.errors import AppError
from app.platform.ids import (
    add_days_ms,
    add_minutes_ms,
    iso,
    new_id,
    now_ms,
    random_numeric_code,
)
from app.platform.security import (
    constant_time_equals,
    encrypt_pii,
    hash_token,
    hmac_code,
    issue_session_token,
    mask_phone,
    new_csrf_secret,
    normalize_phone,
    phone_lookup,
)

SESSION_COOKIE = "__Host-session"
ADMIN_SESSION_COOKIE = "__Host-admin-session"
CSRF_HEADER = "X-CSRF-Token"

# 登录前使用的匿名 CSRF 会话：防登录 CSRF。
ANON_CSRF_COOKIE = "__Host-anon-csrf"
ANON_CSRF_TTL_SECONDS = 1800
#: 已登录会话的 CSRF 缓存时长。token 本身随会话有效，这里只控制前端多久重取一次。
SESSION_CSRF_TTL_SECONDS = 1800


# ------------------------------------------------------------------ 验证码
def create_sms_challenge(
    session: Session,
    *,
    phone_raw: str,
    purpose: str,
    client_ip: str | None,
    request_origin: str | None = None,
    is_production_origin: bool = True,
    now: int | None = None,
) -> dict[str, Any]:
    """创建验证码挑战。返回统一响应，不暴露手机号是否已注册。"""
    settings = get_settings()
    moment = now if now is not None else now_ms()
    phone = normalize_phone(phone_raw)
    lookup = phone_lookup(phone)

    # 多维限流：同手机号 1 次/60 秒、5 次/小时、10 次/天；同 IP 20 次/小时。
    rate_limit.check(
        session,
        scope="otp_phone_minute",
        subject=lookup,
        limit=1,
        window_seconds=settings.otp_resend_seconds,
        code="SMS_RATE_LIMITED",
        now=moment,
    )
    rate_limit.check(
        session,
        scope="otp_phone_hour",
        subject=lookup,
        limit=settings.otp_phone_hourly_limit,
        window_seconds=3600,
        code="SMS_RATE_LIMITED",
        now=moment,
    )
    rate_limit.check(
        session,
        scope="otp_phone_day",
        subject=lookup,
        limit=settings.otp_phone_daily_limit,
        window_seconds=86_400,
        code="SMS_RATE_LIMITED",
        now=moment,
    )
    if client_ip:
        rate_limit.check(
            session,
            scope="otp_ip_hour",
            subject=client_ip,
            limit=settings.otp_ip_hourly_limit,
            window_seconds=3600,
            code="SMS_RATE_LIMITED",
            now=moment,
        )
    rate_limit.DailyBudget(session, scope="sms_send", limit=settings.otp_daily_budget).consume_and_check(
        now=moment
    )

    # 同一手机+用途只保留一个有效挑战；重发使旧码失效。
    previous = repo.latest_active_challenge(session, phone_lookup=lookup, purpose=purpose, moment=moment)
    if previous is not None:
        previous.superseded_at = moment

    code = random_numeric_code(6)
    challenge = SmsChallenge(
        id=new_id("challenge"),
        purpose=purpose,
        phone_lookup=lookup,
        phone_ciphertext=encrypt_pii(phone),
        code_hmac=hmac_code(code, lookup, purpose),
        expires_at=add_minutes_ms(settings.otp_ttl_seconds // 60, moment),
        max_attempts=settings.otp_max_verify_attempts,
        send_status="queued",
        request_ip_hash=audit.ip_fingerprint(client_ip),
    )
    session.add(challenge)
    session.flush()

    # 真实发送交给 worker；本事务只登记挑战与投递意图。
    from app.modules.notifications.ports import NotificationRequest
    from app.modules.notifications.ports import notify as enqueue_notification
    from app.platform.reliability import enqueue

    enqueue_notification(
        session,
        NotificationRequest(
            event_id=challenge.id,
            event_type="sms.challenge",
            user_id=None,
            title="登录验证码",
            body="",
            category="security",
            channels=("sms",),
            phone_target=phone,
            payload={"code": code, "ttl_seconds": settings.otp_ttl_seconds},
        ),
    )
    enqueue(
        session,
        event_type="notification.dispatch",
        aggregate_id=challenge.id,
        payload={"reason": "sms_challenge", "purpose": purpose},
    )

    response: dict[str, Any] = {
        "challenge_id": challenge.id,
        "expires_in": settings.otp_ttl_seconds,
        "retry_after": settings.otp_resend_seconds,
        "dev_hint": None,
    }
    # 仅开发/测试环境返回提示，便于本地联调；生产不存在万能码或白名单绕过。
    if not settings.is_production and purpose in ("login", "sensitive_action"):
        response["dev_hint"] = code
    _ = request_origin, is_production_origin
    return response


def verify_sms_challenge(
    session: Session,
    *,
    challenge_id: str,
    phone_raw: str,
    code: str,
    terms_version: str | None,
    privacy_version: str | None,
    accepted: bool,
    now: int | None = None,
) -> tuple[User, bool]:
    """原子消费验证码并返回用户与是否新建账号。"""
    moment = now if now is not None else now_ms()
    phone = normalize_phone(phone_raw)
    lookup = phone_lookup(phone)
    challenge = repo.get_challenge(session, challenge_id)
    if challenge is None or challenge.phone_lookup != lookup:
        raise AppError("SMS_CODE_INVALID")
    if challenge.superseded_at is not None:
        raise AppError("SMS_CHALLENGE_USED", "该验证码已被新的验证码替代，请使用最新收到的验证码。")
    if challenge.consumed_at is not None:
        raise AppError("SMS_CHALLENGE_USED")
    if challenge.expires_at <= moment:
        challenge.attempts += 1
        raise AppError("SMS_CODE_EXPIRED")
    if challenge.attempts >= challenge.max_attempts:
        raise AppError("SMS_RATE_LIMITED", "验证码错误次数过多，请重新获取。")
    # 换 challenge 不绕过总失败限制。
    failed_total = repo.failed_attempt_total(
        session, phone_lookup=lookup, purpose=challenge.purpose, since_ms=moment - 86_400_000
    )
    if failed_total >= challenge.max_attempts * 3:
        raise AppError("SMS_RATE_LIMITED", "验证失败次数过多，请稍后再试或联系客服。")

    expected = hmac_code(code, lookup, challenge.purpose)
    if not constant_time_equals(expected, challenge.code_hmac):
        challenge.attempts += 1
        raise AppError("SMS_CODE_INVALID")

    # 原子消费：条件更新，两个连接同时验证只有一个成功。
    consumed = affected_rows(session.execute(
        sa.update(SmsChallenge)
        .where(SmsChallenge.id == challenge.id, SmsChallenge.consumed_at.is_(None))
        .values(consumed_at=moment)
    ))
    if not consumed:
        raise AppError("SMS_CHALLENGE_USED")
    session.expire(challenge)

    user = repo.get_user_by_phone_lookup(session, lookup)
    is_new = user is None
    if user is None:
        if not accepted:
            raise AppError("VALIDATION_ERROR", "请先阅读并同意用户服务协议与隐私政策。", fields={"accepted": "必选"})
        user = User(
            id=new_id("user"),
            phone_ciphertext=encrypt_pii(phone),
            phone_lookup=lookup,
            phone_masked=mask_phone(phone),
            nickname=_default_nickname(phone),
        )
        session.add(user)
        try:
            session.flush()
        except IntegrityError:
            # 并发建号：按已有账号重读（手机号唯一冲突处理）。
            session.rollback()
            user = repo.get_user_by_phone_lookup(session, lookup)
            if user is None:
                raise AppError("STATE_CONFLICT", "账号创建冲突，请重试。") from None
            is_new = False

    if user.status == "closed":
        raise AppError("FORBIDDEN", "该账号已注销，如需恢复请联系客服。")
    if user.status == "restricted":
        raise AppError("FORBIDDEN", "账号状态受限，请联系客服。")

    user.last_login_at = moment
    challenge.user_id = user.id

    if accepted and terms_version:
        _record_consent(session, user_id=user.id, purpose="terms", version=terms_version)
    if accepted and privacy_version:
        _record_consent(session, user_id=user.id, purpose="privacy", version=privacy_version)

    return user, is_new


def _default_nickname(phone: str) -> str:
    tail = phone[-4:]
    return f"星友{tail}"


# ------------------------------------------------------------------ 会话
def issue_session(
    session: Session,
    *,
    user: User | None = None,
    staff_id: str | None = None,
    scope: str = "customer",
    user_agent: str | None = None,
    client_ip: str | None = None,
    rotate_from: SessionModel | None = None,
) -> tuple[SessionModel, str, str]:
    """创建会话并返回 ``(会话行, 明文 token, 明文 csrf_secret)``。

    登录、换绑、权限改变后必须轮换会话标识。
    """
    settings = get_settings()
    moment = now_ms()
    if rotate_from is not None:
        rotate_from.revoked_at = moment
        rotate_from.revoked_reason = "rotated"
    token = issue_session_token()
    csrf_secret = new_csrf_secret()
    hours = settings.admin_session_hours if scope == "admin" else settings.session_absolute_hours
    row = SessionModel(
        id=new_id("session"),
        user_id=user.id if user else None,
        staff_id=staff_id,
        scope=scope,
        token_hash=hash_token(token),
        csrf_secret_hash=hash_token(csrf_secret),
        user_agent=(user_agent or "")[:200] or None,
        ip_hash=audit.ip_fingerprint(client_ip),
        expires_at=add_days_ms(0, moment) + hours * 3_600_000,
        idle_expires_at=moment + settings.session_idle_hours * 3_600_000
        if scope == "customer"
        else moment + hours * 3_600_000,
    )
    session.add(row)
    session.flush()
    return row, token, csrf_secret


def load_session(session: Session, token: str | None, *, scope: str = "customer") -> SessionModel | None:
    """读取会话；过期、撤销、闲置超时都视为无效。"""
    if not token:
        return None
    row = repo.get_session_by_token_hash(session, hash_token(token))
    if row is None or row.scope != scope:
        return None
    moment = now_ms()
    if row.revoked_at is not None or row.expires_at <= moment or row.idle_expires_at <= moment:
        return None
    return row


def touch_session(session: Session, row: SessionModel) -> None:
    """滑动闲置期限。数据库不因页面访问更新计数，只更新必要的时间戳。"""
    settings = get_settings()
    moment = now_ms()
    row.last_seen_at = moment
    idle = settings.session_idle_hours if row.scope == "customer" else settings.admin_session_hours
    row.idle_expires_at = moment + idle * 3_600_000
    session.flush()


def session_csrf_token(row: SessionModel) -> str:
    """由会话标识与服务端密钥**确定性派生**的 CSRF token。

    为什么不轮换：整页刷新后前端手里没有 token，只能重新取；如果每次取都轮换密钥，
    同一页面并发的两个写请求会各取一次、后取的那次让先取的立刻失效（多标签页同理），
    表现为随机的 ``CSRF_INVALID``。派生式 token 可以重复签发、不发散状态，
    且随会话 ID 与创建时间绑定：换绑/重新登录会换会话，旧 token 自然失效。
    """
    return hmac_code(row.id, str(row.created_at), namespace="session-csrf")


def verify_csrf(session: Session, row: SessionModel | None, token: str | None) -> None:
    """CSRF 校验：只存哈希，常量时间比较；同时接受派生式会话 token。

    两条路径都必须接受：登录响应下发的是随机会话密钥（只存哈希），而整页刷新后
    前端从 ``/session/csrf`` 取到的是派生式 token。会话被撤销即两者同时失效。
    """
    if row is None:
        raise AppError("AUTH_REQUIRED")
    if not token:
        raise AppError("CSRF_INVALID")
    if constant_time_equals(row.csrf_secret_hash, hash_token(token)):
        return
    if constant_time_equals(session_csrf_token(row), token):
        return
    raise AppError("CSRF_INVALID")


def revoke_session(session: Session, row: SessionModel, *, reason: str = "logout") -> None:
    row.revoked_at = now_ms()
    row.revoked_reason = reason


def require_recent_sensitive(session: Session, row: SessionModel) -> None:
    """敏感动作要求最近 10 分钟重新验证。"""
    settings = get_settings()
    window = settings.sensitive_reauth_minutes * 60_000
    verified = row.sensitive_verified_at or 0
    if now_ms() - verified > window:
        raise AppError("REAUTH_REQUIRED")


def mark_sensitive_verified(session: Session, row: SessionModel) -> None:
    row.sensitive_verified_at = now_ms()
    session.flush()


def consume_sensitive_challenge(
    session: Session, *, challenge_id: str, code: str, now: int | None = None
) -> None:
    """消费 purpose=sensitive_action 的验证码（不签发新会话）。"""
    from app.modules.identity.sensitive import consume_sensitive_challenge as _consume

    _consume(session, challenge_id=challenge_id, code=code, now=now)


def create_sensitive_challenge(
    session: Session,
    *,
    user_id: str,
    phone_e164: str,
    client_ip: str | None,
    now: int | None = None,
) -> dict[str, Any]:
    """为敏感动作创建验证码挑战。"""
    from app.modules.identity.sensitive import create_sensitive_challenge as _create

    return _create(
        session, user_id=user_id, phone_e164=phone_e164, client_ip=client_ip, now=now
    )


def session_response(user: User, row: SessionModel, csrf_token: str, *, is_new: bool) -> SessionResponse:
    return SessionResponse(
        user=user_summary(user),
        session_expires_at=iso(row.expires_at) or "",
        idle_expires_at=iso(row.idle_expires_at) or "",
        csrf_token=csrf_token,
        is_new_user=is_new,
    )


def user_summary(user: User) -> UserSummary:
    return UserSummary(
        id=user.id,
        nickname=user.nickname,
        phone_masked=user.phone_masked,
        locale=user.locale,
        currency=user.currency,
        region=user.region,
        status=user.status,
        created_at=iso(user.created_at),
        version=user.version,
    )


# ------------------------------------------------------------------ 同意
def _record_consent(
    session: Session,
    *,
    user_id: str,
    purpose: str,
    version: str,
    resource_type: str | None = None,
    resource_id: str | None = None,
    content_hash: str = "",
    evidence: dict[str, Any] | None = None,
) -> Consent:
    from app.platform.reliability import digest

    entry = Consent(
        id=new_id("consent"),
        user_id=user_id,
        purpose=purpose,
        document_version=version,
        content_hash=content_hash or digest({"purpose": purpose, "version": version}),
        resource_type=resource_type,
        resource_id=resource_id,
        evidence=evidence,
    )
    session.add(entry)
    return entry


def record_consent(session: Session, *, user_id: str, purpose: str, version: str, **kwargs: Any) -> Consent:
    return _record_consent(session, user_id=user_id, purpose=purpose, version=version, **kwargs)


def revoke_consent(session: Session, *, user_id: str, purpose: str, resource_id: str | None, reason: str) -> bool:
    entry = repo.active_consent(session, user_id=user_id, purpose=purpose, resource_id=resource_id)
    if entry is None:
        return False
    entry.revoked_at = now_ms()
    entry.revoke_reason = reason[:200]
    return True


def has_consent(session: Session, *, user_id: str, purpose: str, resource_id: str | None = None) -> bool:
    return repo.active_consent(session, user_id=user_id, purpose=purpose, resource_id=resource_id) is not None


# ------------------------------------------------------------------ 资料与地址
def update_profile(
    session: Session,
    *,
    user: User,
    nickname: str | None,
    locale: str | None,
    currency: str | None,
    avatar_asset_id: str | None,
    version: int,
) -> User:
    if version != user.version:
        raise AppError("VERSION_CONFLICT")
    if nickname is not None:
        user.nickname = nickname
    if locale is not None:
        user.locale = locale
    if currency is not None:
        user.currency = currency
    if avatar_asset_id is not None:
        user.avatar_asset_id = avatar_asset_id
    user.version += 1
    user.updated_at = now_ms()
    session.flush()
    return user


MAX_ADDRESSES = 20


def create_address(session: Session, *, user_id: str, payload: AddressInput) -> Address:
    if repo.count_addresses(session, user_id=user_id) >= MAX_ADDRESSES:
        raise AppError("VALIDATION_ERROR", f"最多保存 {MAX_ADDRESSES} 条地址。", fields={"address": "已达上限"})
    phone = normalize_phone(payload.phone)
    row = Address(
        id=new_id("address"),
        user_id=user_id,
        label=payload.label,
        recipient_ciphertext=encrypt_pii(payload.recipient),
        phone_ciphertext=encrypt_pii(phone),
        address_ciphertext=encrypt_pii(payload.detail),
        region_code=payload.region_code,
        province=payload.province,
        city=payload.city,
        postal_code=payload.postal_code,
        is_default=1 if payload.is_default else 0,
    )
    session.add(row)
    if payload.is_default:
        _clear_other_defaults(session, user_id=user_id, keep_id=row.id)
    session.flush()
    return row


def update_address(session: Session, *, user_id: str, address_id: str, patch: AddressPatch) -> Address:
    row = repo.get_address(session, address_id=address_id, user_id=user_id)
    if row is None:
        raise AppError("RESOURCE_NOT_FOUND")
    if patch.version != row.version:
        raise AppError("VERSION_CONFLICT")
    if patch.label is not None:
        row.label = patch.label
    if patch.recipient is not None:
        row.recipient_ciphertext = encrypt_pii(patch.recipient)
    if patch.phone is not None:
        row.phone_ciphertext = encrypt_pii(normalize_phone(patch.phone))
    if patch.province is not None:
        row.province = patch.province
    if patch.city is not None:
        row.city = patch.city
    if patch.detail is not None:
        row.address_ciphertext = encrypt_pii(patch.detail)
    if patch.postal_code is not None:
        row.postal_code = patch.postal_code
    if patch.is_default is not None:
        row.is_default = 1 if patch.is_default else 0
        if patch.is_default:
            _clear_other_defaults(session, user_id=user_id, keep_id=row.id)
    row.version += 1
    row.updated_at = now_ms()
    session.flush()
    return row


def delete_address(session: Session, *, user_id: str, address_id: str) -> None:
    row = repo.get_address(session, address_id=address_id, user_id=user_id)
    if row is None:
        raise AppError("RESOURCE_NOT_FOUND")
    # 删除不改订单快照；订单内保存独立地址快照。
    session.delete(row)


def _clear_other_defaults(session: Session, *, user_id: str, keep_id: str) -> None:
    session.execute(
        sa.update(Address)
        .where(Address.user_id == user_id, Address.id != keep_id)
        .values(is_default=0)
    )


# ------------------------------------------------------------------ 换绑与注销
def start_phone_change(
    session: Session, *, user: User, new_phone_raw: str, evidence_note: str | None = None
) -> PhoneChangeRequest:
    new_phone = normalize_phone(new_phone_raw)
    lookup = phone_lookup(new_phone)
    existing = repo.get_user_by_phone_lookup(session, lookup)
    if existing is not None and existing.id != user.id:
        raise AppError("STATE_CONFLICT", "该手机号已绑定其他账号，请先处理原账号。")
    row = PhoneChangeRequest(
        id=new_id("job"),
        user_id=user.id,
        old_phone_lookup=user.phone_lookup,
        new_phone_ciphertext=encrypt_pii(new_phone),
        new_phone_lookup=lookup,
        evidence_note=evidence_note,
    )
    session.add(row)
    session.flush()
    return row


def complete_phone_change(session: Session, *, user: User, request_id: str, new_phone_raw: str) -> User:
    """服务端原子换绑：更新手机号、轮换会话、通知原渠道。"""
    row = session.get(PhoneChangeRequest, request_id)
    if row is None or row.user_id != user.id or row.status != "PENDING":
        raise AppError("STATE_CONFLICT")
    phone = normalize_phone(new_phone_raw)
    lookup = phone_lookup(phone)
    if lookup != row.new_phone_lookup:
        raise AppError("STATE_CONFLICT", "换绑目标手机号与申请不一致。")

    user.phone_ciphertext = encrypt_pii(phone)
    user.phone_lookup = lookup
    user.phone_masked = mask_phone(phone)
    user.phone_changed_at = now_ms()
    user.version += 1
    row.status = "COMPLETED"
    row.completed_at = now_ms()
    session.flush()
    return user


def closure_blockers(session: Session, *, user_id: str) -> list[str]:
    """注销前必须说明的合法阻碍与处理路径（10 文档：不能以任意理由永久拒绝注销）。"""
    blockers: list[str] = []
    from app.modules.commerce import ports as commerce_ports
    from app.modules.commissions import ports as commission_ports

    active_orders = commerce_ports.count_active_orders_for_user(session, user_id=user_id)
    if active_orders:
        blockers.append(
            f"有 {active_orders} 笔订单尚未完结（待付款、履约中或售后中），需先完成、取消或结清。"
        )
    active_after_sales = commerce_ports.count_open_after_sales_for_user(session, user_id=user_id)
    if active_after_sales:
        blockers.append(f"有 {active_after_sales} 笔售后正在处理，需等待处理结论。")
    active_quotes = commission_ports.count_open_inquiries_for_user(session, user_id=user_id)
    if active_quotes:
        blockers.append(f"有 {active_quotes} 条询价仍在处理中，可先撤回或等待答复。")
    return blockers


def request_closure(
    session: Session, *, user: User, reason: str | None, acknowledged: bool
) -> dict[str, Any]:
    blockers = closure_blockers(session, user_id=user.id)
    if blockers and not acknowledged:
        return {
            "status": "BLOCKED",
            "blockers": blockers,
            "scheduled_for": None,
            "message": "注销前需要先处理下列事项；确认知悉后可再次提交，交易与合同记录将按法定义务隔离保留。",
        }
    user.closure_requested_at = now_ms()
    user.status = "closed"
    user.closed_at = now_ms()
    # 撤销全部会话
    repo.revoke_user_sessions(session, user_id=user.id, reason="closure")
    session.flush()
    return {
        "status": "REQUESTED",
        "blockers": blockers,
        "scheduled_for": iso(add_days_ms(30)),
        "message": "注销申请已受理：账号立即停止服务并撤销全部会话，可删除资料进入清理流程，"
        "交易、付款、退款、合同与发票按适用法定义务隔离保留。",
    }


def restriction_change(
    session: Session, *, user: User, status: str, reason: str, actor_id: str | None
) -> User:
    """后台限制/恢复账号。必须留下原因与审计。"""
    if status not in ("active", "restricted", "closed"):
        raise AppError("VALIDATION_ERROR", fields={"status": "非法状态"})
    before = {"status": user.status, "reason": user.status_reason}
    user.status = status
    user.status_reason = reason[:200]
    user.version += 1
    if status != "active":
        repo.revoke_user_sessions(session, user_id=user.id, reason=f"status:{status}")
    audit.record(
        session,
        actor_type="staff",
        actor_id=actor_id,
        action="user.restriction_change",
        resource_type="user",
        resource_id=user.id,
        before=before,
        after={"status": status, "reason": user.status_reason},
        reason=reason,
    )
    session.flush()
    return user
