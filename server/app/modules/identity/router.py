"""身份模块 HTTP 路由。

路径常量见 03 文档；端点在 ``/api/v1`` 下挂载，与 06 文档的接口目录一致。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Header, Request, Response
from sqlalchemy.orm import Session as OrmSession

from app.api.dependencies import (
    ContextDep,
    CsrfDep,
    CustomerIdDep,
    PrincipalDep,
    clear_session_cookie,
    enforce_write_guard,
    set_session_cookie,
)
from app.modules.identity import repository as repo
from app.modules.identity import service as svc
from app.modules.identity.models import Session as SessionModel
from app.modules.identity.schemas import (
    AddressInput,
    AddressPatch,
    AddressResponse,
    ClosureRequest,
    ClosureResponse,
    ConsentInput,
    CsrfResponse,
    EmailVerificationConfirm,
    EmailVerificationRequest,
    MeResponse,
    MeUpdateRequest,
    PhoneChangeChallengeRequest,
    PhoneChangeConfirmRequest,
    PointsBadge,
    SensitiveVerifyRequest,
    SessionResponse,
    SmsChallengeRequest,
    SmsChallengeResponse,
    SmsVerifyRequest,
    UserSessionItem,
    UserSummary,
)
from app.modules.notifications import ports as notification_ports
from app.platform import audit
from app.platform.config import get_settings
from app.platform.errors import AppError
from app.platform.ids import iso, now_ms, random_token
from app.platform.rate_limit import check as rate_check
from app.platform.security import constant_time_equals, decrypt_pii, hmac_code, normalize_phone

router = APIRouter(tags=["identity"])
anon_router = APIRouter(tags=["identity"])


# ------------------------------------------------------------------ CSRF
@anon_router.get("/session/csrf", response_model=CsrfResponse)
def issue_csrf(ctx: ContextDep, response: Response) -> CsrfResponse:
    """发放 CSRF token。

    - **已登录**（客户或员工会话）：轮换该会话的 CSRF 密钥并返回绑定该会话的新 token。
      会话只保存哈希，整页刷新后取不回原 token；如果不在这里轮换，刷新后的第一次写操作
      只能带匿名 token，会被 ``verify_csrf`` 判为不匹配而失败。
    - **未登录**：发放短期匿名 token，防登录 CSRF（不暴露手机号是否已注册）。
    """
    response.headers["Cache-Control"] = "no-store"
    if ctx.session_row is not None:
        # 与会话绑定、可重复签发；整页刷新后重新获取即可，不需要（也不应该）轮换密钥，
        # 否则并发写请求或多标签页会互相作废对方的 token。
        return CsrfResponse(
            csrf_token=svc.session_csrf_token(ctx.session_row),
            expires_in=svc.SESSION_CSRF_TTL_SECONDS,
        )
    nonce = random_token(16)
    expires = now_ms() + svc.ANON_CSRF_TTL_SECONDS * 1000
    signature = hmac_code(nonce, str(expires), namespace="anon-csrf")
    return CsrfResponse(
        csrf_token=f"{nonce}.{expires}.{signature}",
        expires_in=svc.ANON_CSRF_TTL_SECONDS,
    )


def _verify_anonymous_csrf(
    token: str | None, *, session_row: SessionModel | None = None, session: OrmSession | None = None
) -> None:
    """登录/建号等**匿名写操作**的 CSRF 校验。

    接受两类 token：
    1. 纯匿名 token（`nonce.expires.hmac`，由 `/session/csrf` 在无会话时签发）；
    2. 已登录会话的派生 token——用户带着旧会话回登录页时，`/session/csrf` 会返回会话
       token；它同样不可被第三方伪造，因此足以防登录 CSRF，没必要强迫前端区分端点。
    """
    if not token:
        raise AppError("CSRF_INVALID")
    if (
        session_row is not None
        and session is not None
        and constant_time_equals(svc.session_csrf_token(session_row), token)
    ):
        return
    parts = token.split(".")
    if len(parts) != 3:
        raise AppError("CSRF_INVALID")
    nonce, expires_raw, signature = parts
    try:
        expires = int(expires_raw)
    except ValueError as exc:
        raise AppError("CSRF_INVALID") from exc
    if expires <= now_ms():
        raise AppError("CSRF_INVALID", "页面停留时间过长，请刷新后重试。")
    expected = hmac_code(nonce, expires_raw, namespace="anon-csrf")
    if not constant_time_equals(expected, signature):
        raise AppError("CSRF_INVALID")


# ------------------------------------------------------------------ 验证码登录
@anon_router.post("/auth/sms/challenges", response_model=SmsChallengeResponse)
def create_sms_challenge(
    payload: SmsChallengeRequest,
    ctx: ContextDep,
    request: Request,
    x_csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> SmsChallengeResponse:
    enforce_write_guard(request)
    _verify_anonymous_csrf(x_csrf_token, session_row=ctx.session_row, session=ctx.session)
    result = svc.create_sms_challenge(
        ctx.session,
        phone_raw=payload.phone,
        purpose=payload.purpose,
        client_ip=ctx.client_ip,
        request_origin=ctx.origin,
    )
    ctx.session.commit()
    return SmsChallengeResponse(**result)


@anon_router.post("/auth/sms/verify", response_model=SessionResponse)
def verify_sms(
    payload: SmsVerifyRequest,
    ctx: ContextDep,
    request: Request,
    response: Response,
) -> SessionResponse:
    enforce_write_guard(request)
    user, is_new = svc.verify_sms_challenge(
        ctx.session,
        challenge_id=payload.challenge_id,
        phone_raw=payload.phone,
        code=payload.code,
        terms_version=payload.terms_version,
        privacy_version=payload.privacy_version,
        accepted=payload.accepted,
    )
    row, token, csrf_secret = svc.issue_session(
        ctx.session,
        user=user,
        client_ip=ctx.client_ip,
        user_agent=request.headers.get("user-agent"),
    )
    ctx.session.commit()
    settings = get_settings()
    set_session_cookie(
        response, token=token, max_age_seconds=settings.session_absolute_hours * 3600
    )
    return svc.session_response(user, row, csrf_secret, is_new=is_new)


@router.post("/auth/logout", status_code=204)
def logout(ctx: ContextDep, response: Response, request: Request) -> Response:
    enforce_write_guard(request)
    if ctx.session_row is not None:
        svc.revoke_session(ctx.session, ctx.session_row, reason="logout")
        audit.record(
            ctx.session,
            actor_type="customer" if ctx.session_row.user_id else "staff",
            actor_id=ctx.session_row.user_id or ctx.session_row.staff_id,
            action="session.logout",
            resource_type="session",
            resource_id=ctx.session_row.id,
            ip=ctx.client_ip,
        )
    ctx.session.commit()
    clear_session_cookie(response)
    response.status_code = 204
    return response


# ------------------------------------------------------------------ 我的资料
@router.get("/me", response_model=MeResponse)
def get_me(ctx: ContextDep, principal: PrincipalDep) -> MeResponse:
    if not principal.is_authenticated:
        raise AppError("AUTH_REQUIRED")
    if principal.kind == "staff":
        return MeResponse(
            user=UserSummary(
                id=principal.id or "",
                nickname=principal.display_name,
                phone_masked="",
                locale="zh-CN",
                currency="CNY",
                region="CN",
                status="active",
            ),
            permissions=sorted(principal.permissions),
        )

    user = repo.get_user(ctx.session, principal.require_customer())
    if user is None:
        raise AppError("AUTH_REQUIRED")
    from app.modules.commerce import ports as commerce_ports
    from app.modules.commissions import ports as commission_ports

    pending = {
        "orders_awaiting_payment": commerce_ports.count_orders_awaiting_payment(ctx.session, user_id=user.id),
        "quotes_awaiting_decision": commission_ports.count_quotes_awaiting_decision(
            ctx.session, user_id=user.id
        ),
        "stages_awaiting_confirmation": commission_ports.count_stages_awaiting_confirmation(
            ctx.session, user_id=user.id
        ),
        "after_sales_open": commerce_ports.count_open_after_sales_for_user(ctx.session, user_id=user.id),
        # 待补件：工作室要求补充资料的询价（07 文档 NEEDS_INFO）。
        "inquiries_needs_info": commission_ports.count_inquiries_needs_info(ctx.session, user_id=user.id),
    }

    points = PointsBadge()
    try:
        from app.modules.loyalty import ports as loyalty_ports

        view = loyalty_ports.account_view(ctx.session, user_id=user.id)
        points = PointsBadge(
            posted_balance=int(view.get("posted_balance", 0)),
            available_points=int(view.get("available_points", 0)),
            pending_points=int(view.get("pending_points", 0)),
            rules_version=str(view.get("rules_version", "")),
        )
    except Exception:  # noqa: BLE001 - 积分模块缺席不应阻塞账户总览
        points = PointsBadge()

    return MeResponse(
        user=svc.user_summary(user),
        permissions=[],
        sensitive_verified=bool(
            ctx.session_row
            and ctx.session_row.sensitive_verified_at
            and now_ms() - ctx.session_row.sensitive_verified_at < settings_window()
        ),
        unread_notifications=notification_ports.unread_count(ctx.session, user_id=user.id),
        pending_actions=pending,
        points=points,
    )


def settings_window() -> int:
    return get_settings().sensitive_reauth_minutes * 60_000


@router.patch("/me", response_model=UserSummary)
def update_me(payload: MeUpdateRequest, ctx: ContextDep, _csrf: CsrfDep) -> UserSummary:
    user_id = ctx.principal.require_customer()
    user = repo.get_user(ctx.session, user_id)
    if user is None:
        raise AppError("AUTH_REQUIRED")
    before = {"nickname": user.nickname, "locale": user.locale, "currency": user.currency}
    user = svc.update_profile(
        ctx.session,
        user=user,
        nickname=payload.nickname,
        locale=payload.locale,
        currency=payload.currency,
        avatar_asset_id=payload.avatar_asset_id,
        version=payload.version,
    )
    audit.record(
        ctx.session,
        actor_type="customer",
        actor_id=user.id,
        action="user.profile_update",
        resource_type="user",
        resource_id=user.id,
        before=before,
        after={"nickname": user.nickname, "locale": user.locale, "currency": user.currency},
        ip=ctx.client_ip,
    )
    return svc.user_summary(user)


@router.get("/me/sessions", response_model=list[UserSessionItem])
def list_my_sessions(ctx: ContextDep, user_id: CustomerIdDep) -> list[UserSessionItem]:
    rows = repo.list_sessions_for_user(ctx.session, user_id=user_id)
    current_id = ctx.session_row.id if ctx.session_row else None
    return [
        UserSessionItem(
            id=row.id,
            scope=row.scope,
            user_agent=row.user_agent,
            created_at=iso(row.created_at) or "",
            last_seen_at=iso(row.last_seen_at) or "",
            expires_at=iso(row.expires_at) or "",
            current=row.id == current_id,
        )
        for row in rows
    ]


@router.delete("/me/sessions/{session_id}", status_code=204)
def revoke_my_session(
    session_id: str, ctx: ContextDep, _csrf: CsrfDep, user_id: CustomerIdDep
) -> Response:
    row = _owned_session(ctx.session, session_id=session_id, user_id=user_id)
    svc.revoke_session(ctx.session, row, reason="user_revoked")
    audit.record(
        ctx.session,
        actor_type="customer",
        actor_id=user_id,
        action="session.revoke",
        resource_type="session",
        resource_id=row.id,
        ip=ctx.client_ip,
    )
    return Response(status_code=204)


@router.post("/me/sessions/revoke-others", status_code=200)
def revoke_other_sessions(ctx: ContextDep, _csrf: CsrfDep, user_id: CustomerIdDep) -> dict[str, int]:
    """全端退出：撤销除当前会话外的全部会话。"""
    count = repo.revoke_user_sessions(
        ctx.session,
        user_id=user_id,
        reason="user_revoked_others",
        except_session_id=ctx.session_row.id if ctx.session_row else None,
    )
    audit.record(
        ctx.session,
        actor_type="customer",
        actor_id=user_id,
        action="session.revoke_others",
        resource_type="user",
        resource_id=user_id,
        after={"revoked": count},
        ip=ctx.client_ip,
    )
    return {"revoked": count}


def _owned_session(session: OrmSession, *, session_id: str, user_id: str) -> SessionModel:
    row = session.get(SessionModel, session_id)
    if row is None or row.user_id != user_id:
        raise AppError("RESOURCE_NOT_FOUND")
    return row


# ------------------------------------------------------------------ 换绑与注销
@router.post("/me/phone-change/challenges", response_model=SmsChallengeResponse)
def phone_change_challenge(
    payload: PhoneChangeChallengeRequest, ctx: ContextDep, _csrf: CsrfDep
) -> SmsChallengeResponse:
    """为换绑或敏感动作获取验证码。

    ``role="sensitive"`` 时向当前绑定号码发送 ``purpose="sensitive_action"`` 的验证码，
    用于解锁换绑、注销等敏感动作；验证码必须由服务端核验后才标记会话。
    """
    user_id = ctx.principal.require_customer()
    user = repo.get_user(ctx.session, user_id)
    if user is None:
        raise AppError("AUTH_REQUIRED")

    if payload.role == "sensitive":
        # 敏感动作要求最近 10 分钟已重新验证；验证码本身用于满足该要求，
        # 因此这里只检查会话有效与账号状态，不要求已有敏感验证时间。
        result = svc.create_sensitive_challenge(
            ctx.session,
            user_id=user_id,
            phone_e164=decrypt_pii(user.phone_ciphertext),
            client_ip=ctx.client_ip,
        )
        ctx.session.commit()
        return SmsChallengeResponse(**result)

    svc.require_recent_sensitive(ctx.session, _row(ctx))
    target = payload.phone or decrypt_pii(user.phone_ciphertext)
    purpose = "phone_change_old" if payload.role == "old" else "phone_change_new"
    result = svc.create_sms_challenge(
        ctx.session, phone_raw=target, purpose=purpose, client_ip=ctx.client_ip
    )
    ctx.session.commit()
    return SmsChallengeResponse(**result)


@router.post("/me/phone-change/confirm", response_model=UserSummary)
def phone_change_confirm(
    payload: PhoneChangeConfirmRequest, ctx: ContextDep, _csrf: CsrfDep, request: Request
) -> UserSummary:
    user_id = ctx.principal.require_customer()
    row = _row(ctx)
    user = repo.get_user(ctx.session, user_id)
    if user is None:
        raise AppError("AUTH_REQUIRED")

    if payload.manual_appeal:
        # 旧号丢失走人工申诉：不能只凭订单号换绑。
        request_row = svc.start_phone_change(
            ctx.session,
            user=user,
            new_phone_raw=_new_phone_from_challenge(ctx, payload.new_challenge_id, payload.new_code),
            evidence_note=payload.evidence_note,
        )
        request_row.manual_appeal = 1
        notification_ports.notify(
            ctx.session,
            _staff_alert_request(
                ctx,
                title="收到换绑人工申诉",
                body=f"用户 {user.id} 提交换绑申诉，需人工核验身份与交易证明。",
            ),
        )
        ctx.session.commit()
        return svc.user_summary(user)

    if not payload.old_challenge_id or not payload.old_code:
        raise AppError("VALIDATION_ERROR", "换绑需要先验证当前手机号。", fields={"old_code": "必填"})
    svc.verify_sms_challenge(
        ctx.session,
        challenge_id=payload.old_challenge_id,
        phone_raw=decrypt_pii(user.phone_ciphertext),
        code=payload.old_code,
        terms_version=None,
        privacy_version=None,
        accepted=True,
    )
    new_phone = _new_phone_from_challenge(ctx, payload.new_challenge_id, payload.new_code)
    request_row = svc.start_phone_change(ctx.session, user=user, new_phone_raw=new_phone)
    user = svc.complete_phone_change(
        ctx.session, user=user, request_id=request_row.id, new_phone_raw=new_phone
    )
    # 换绑后通知原渠道、审计并冻结高风险变更窗口。
    repo.revoke_user_sessions(
        ctx.session,
        user_id=user.id,
        reason="phone_changed",
        except_session_id=row.id,
    )
    audit.record(
        ctx.session,
        actor_type="customer",
        actor_id=user.id,
        action="user.phone_changed",
        resource_type="user",
        resource_id=user.id,
        reason="用户自助换绑",
        ip=ctx.client_ip,
    )
    ctx.session.commit()
    return svc.user_summary(user)


def _new_phone_from_challenge(ctx: ContextDep, challenge_id: str, code: str) -> str:
    challenge = repo.get_challenge(ctx.session, challenge_id)
    if challenge is None:
        raise AppError("SMS_CODE_INVALID")
    phone = decrypt_pii(challenge.phone_ciphertext)
    svc.verify_sms_challenge(
        ctx.session,
        challenge_id=challenge_id,
        phone_raw=phone,
        code=code,
        terms_version=None,
        privacy_version=None,
        accepted=True,
    )
    return phone


def _staff_alert_request(ctx: ContextDep, *, title: str, body: str) -> Any:
    from app.modules.notifications.ports import NotificationRequest

    return NotificationRequest(
        event_id=f"staff-alert-{now_ms()}",
        event_type="security.alert",
        user_id=None,
        title=title,
        body=body,
        category="security",
        channels=(),
        payload={"title": title, "body": body, "occurred_at": iso(now_ms())},
    )


@router.post("/me/closure-requests", response_model=ClosureResponse)
def closure_request(
    payload: ClosureRequest, ctx: ContextDep, _csrf: CsrfDep
) -> ClosureResponse:
    user_id = ctx.principal.require_customer()
    row = _row(ctx)
    svc.require_recent_sensitive(ctx.session, row)
    user = repo.get_user(ctx.session, user_id)
    if user is None:
        raise AppError("AUTH_REQUIRED")
    svc.verify_sms_challenge(
        ctx.session,
        challenge_id=payload.challenge_id,
        phone_raw=decrypt_pii(user.phone_ciphertext),
        code=payload.code,
        terms_version=None,
        privacy_version=None,
        accepted=True,
    )
    result = svc.request_closure(
        ctx.session, user=user, reason=payload.reason, acknowledged=payload.acknowledged_blockers
    )
    audit.record(
        ctx.session,
        actor_type="customer",
        actor_id=user.id,
        action="user.closure_requested",
        resource_type="user",
        resource_id=user.id,
        reason=payload.reason,
        after=result,
        ip=ctx.client_ip,
    )
    ctx.session.commit()
    return ClosureResponse(**result)


@router.post("/me/sensitive-verify", status_code=200)
def sensitive_verify(
    ctx: ContextDep,
    _csrf: CsrfDep,
    payload: SensitiveVerifyRequest | None = None,
) -> dict[str, bool]:
    """重新验证手机号持有后，解锁换绑、注销等敏感动作。

    约束（10 文档）：敏感动作要求最近 10 分钟重新验证。验证码必须由服务端核验：
    挑战用途为 ``sensitive_action``，消费后**不签发新会话**，只标记当前会话。
    """
    user_id = ctx.principal.require_customer()
    row = _row(ctx)
    if payload is None or not payload.challenge_id or not payload.code:
        raise AppError(
            "VALIDATION_ERROR",
            "请先获取验证码并填写后再验证身份。",
            fields={"code": "必填"},
        )
    user = repo.get_user(ctx.session, user_id)
    if user is None:
        raise AppError("AUTH_REQUIRED")

    rate_check(
        ctx.session,
        scope="sensitive_verify",
        subject=user_id,
        limit=10,
        window_seconds=3600,
        now=now_ms(),
    )

    challenge = repo.get_challenge(ctx.session, payload.challenge_id)
    if challenge is None or challenge.purpose != "sensitive_action":
        raise AppError("SMS_CODE_INVALID")
    if challenge.phone_lookup != user.phone_lookup:
        # 只能用当前绑定手机号完成敏感验证，防止用他人号码解锁。
        raise AppError("SMS_CODE_INVALID")

    svc.consume_sensitive_challenge(
        ctx.session, challenge_id=payload.challenge_id, code=payload.code
    )
    svc.mark_sensitive_verified(ctx.session, row)
    audit.record(
        ctx.session,
        actor_type="customer",
        actor_id=user_id,
        action="session.sensitive_verified",
        resource_type="session",
        resource_id=row.id,
        ip=ctx.client_ip,
    )
    return {"sensitive_verified": True}


# ------------------------------------------------------------------ 同意
@router.get("/me/consents")
def list_consents(ctx: ContextDep, user_id: CustomerIdDep) -> list[dict[str, Any]]:
    rows = repo.list_consents(ctx.session, user_id=user_id)
    return [
        {
            "id": row.id,
            "purpose": row.purpose,
            "document_version": row.document_version,
            "accepted_at": iso(row.accepted_at),
            "revoked_at": iso(row.revoked_at),
        }
        for row in rows
    ]


@router.post("/me/consents", status_code=201)
def set_consent(payload: ConsentInput, ctx: ContextDep, _csrf: CsrfDep) -> dict[str, Any]:
    """记录或撤回同意。展示授权可以单独撤回（C18）。"""
    user_id = ctx.principal.require_customer()
    if payload.accepted:
        entry = svc.record_consent(
            ctx.session,
            user_id=user_id,
            purpose=payload.purpose,
            version=payload.document_version,
            resource_type=payload.resource_type,
            resource_id=payload.resource_id,
        )
        result: dict[str, Any] = {
            "id": entry.id,
            "purpose": entry.purpose,
            "accepted_at": iso(entry.accepted_at),
        }
    else:
        revoked = svc.revoke_consent(
            ctx.session,
            user_id=user_id,
            purpose=payload.purpose,
            resource_id=payload.resource_id,
            reason="用户主动撤回",
        )
        result = {"revoked": revoked, "purpose": payload.purpose}
    audit.record(
        ctx.session,
        actor_type="customer",
        actor_id=user_id,
        action="consent.record" if payload.accepted else "consent.revoke",
        resource_type="consent",
        resource_id=payload.resource_id or user_id,
        after=result,
        ip=ctx.client_ip,
    )
    return result


# ------------------------------------------------------------------ 地址
@router.get("/me/addresses", response_model=list[AddressResponse])
def list_addresses(ctx: ContextDep, user_id: CustomerIdDep) -> list[AddressResponse]:
    return [_address_response(row) for row in repo.list_addresses(ctx.session, user_id=user_id)]


@router.post("/me/addresses", response_model=AddressResponse, status_code=201)
def create_address(payload: AddressInput, ctx: ContextDep, _csrf: CsrfDep, user_id: CustomerIdDep) -> AddressResponse:
    row = svc.create_address(ctx.session, user_id=user_id, payload=payload)
    audit.record(
        ctx.session,
        actor_type="customer",
        actor_id=user_id,
        action="address.create",
        resource_type="address",
        resource_id=row.id,
        ip=ctx.client_ip,
    )
    return _address_response(row)


@router.patch("/me/addresses/{address_id}", response_model=AddressResponse)
def update_address(
    address_id: str, payload: AddressPatch, ctx: ContextDep, _csrf: CsrfDep, user_id: CustomerIdDep
) -> AddressResponse:
    row = svc.update_address(ctx.session, user_id=user_id, address_id=address_id, patch=payload)
    audit.record(
        ctx.session,
        actor_type="customer",
        actor_id=user_id,
        action="address.update",
        resource_type="address",
        resource_id=row.id,
        ip=ctx.client_ip,
    )
    return _address_response(row)


@router.delete("/me/addresses/{address_id}", status_code=204)
def delete_address(address_id: str, ctx: ContextDep, _csrf: CsrfDep, user_id: CustomerIdDep) -> Response:
    svc.delete_address(ctx.session, user_id=user_id, address_id=address_id)
    audit.record(
        ctx.session,
        actor_type="customer",
        actor_id=user_id,
        action="address.delete",
        resource_type="address",
        resource_id=address_id,
        ip=ctx.client_ip,
    )
    return Response(status_code=204)


def _address_response(row: Any) -> AddressResponse:
    return AddressResponse(
        id=row.id,
        label=row.label,
        recipient=decrypt_pii(row.recipient_ciphertext),
        phone_masked=_mask(decrypt_pii(row.phone_ciphertext)),
        region_code=row.region_code,
        province=row.province,
        city=row.city,
        detail=decrypt_pii(row.address_ciphertext),
        postal_code=row.postal_code,
        is_default=bool(row.is_default),
        version=row.version,
    )


def _mask(phone: str) -> str:
    from app.platform.security import mask_phone

    return mask_phone(phone)


def _row(ctx: ContextDep) -> SessionModel:
    if ctx.session_row is None:
        raise AppError("AUTH_REQUIRED")
    return ctx.session_row


__all__ = ["router", "anon_router", "normalize_phone"]


@router.get("/me/email")
def get_my_email(ctx: ContextDep, user_id: CustomerIdDep) -> dict[str, Any]:
    from app.modules.identity.email import email_status
    return email_status(ctx.session, user_id)


@router.post("/me/email/verification", status_code=202)
def verify_my_email(payload: EmailVerificationRequest, ctx: ContextDep, _csrf: CsrfDep, user_id: CustomerIdDep) -> dict[str, bool]:
    from app.modules.identity.email import start_verification
    start_verification(ctx.session, user_id=user_id, email=str(payload.email))
    return {"sent": True}


@router.post("/me/email/confirm")
def confirm_my_email(payload: EmailVerificationConfirm, ctx: ContextDep, _csrf: CsrfDep, user_id: CustomerIdDep) -> dict[str, Any]:
    from app.modules.identity.email import confirm_verification, email_status
    if not confirm_verification(ctx.session, user_id=user_id, code=payload.code):
        # 错误尝试也必须持久化，避免异常回滚绕过次数限制。
        ctx.session.commit()
        raise AppError("VALIDATION_ERROR", "验证码无效、过期或尝试次数过多。", fields={"code": "请检查验证码或重新发送"})
    from app.modules.notifications.ports import resume_missing_recipient
    resume_missing_recipient(ctx.session, user_id)
    return email_status(ctx.session, user_id)
