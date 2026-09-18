"""敏感动作的短信二次验证。

与登录验证的区别（10 文档）：
- 挑战用途为 ``sensitive_action``，手机号必须是当前绑定号码；
- 校验成功后**不新建会话、不签发新令牌**，只标记当前会话的 ``sensitive_verified_at``；
- 校验失败按挑战累计次数，换 challenge 不能绕过总失败限制。
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.modules.identity import repository as repo
from app.platform.db import affected_rows
from app.platform.errors import AppError
from app.platform.ids import now_ms
from app.platform.security import (
    constant_time_equals,
    encrypt_pii,
    hmac_code,
    mask_phone,
    normalize_phone,
    phone_lookup,
)

SENSITIVE_PURPOSE = "sensitive_action"


def create_sensitive_challenge(
    session: Session,
    *,
    user_id: str,
    phone_e164: str,
    client_ip: str | None,
    now: int | None = None,
) -> dict[str, object]:
    """为敏感动作创建验证码挑战。复用登录挑战的限流与投递链路。"""
    from app.modules.identity.service import create_sms_challenge

    return create_sms_challenge(
        session,
        phone_raw=phone_e164,
        purpose=SENSITIVE_PURPOSE,
        client_ip=client_ip,
        now=now,
    )


def consume_sensitive_challenge(
    session: Session, *, challenge_id: str, code: str, now: int | None = None
) -> None:
    """原子消费敏感动作验证码。失败一律抛稳定错误码，不透露手机号信息。"""
    moment = now if now is not None else now_ms()
    challenge = repo.get_challenge(session, challenge_id)
    if challenge is None or challenge.purpose != SENSITIVE_PURPOSE:
        raise AppError("SMS_CODE_INVALID")
    if challenge.superseded_at is not None or challenge.consumed_at is not None:
        raise AppError("SMS_CHALLENGE_USED")
    if challenge.expires_at <= moment:
        challenge.attempts += 1
        raise AppError("SMS_CODE_EXPIRED")
    if challenge.attempts >= challenge.max_attempts:
        raise AppError("SMS_RATE_LIMITED", "验证码错误次数过多，请重新获取。")

    expected = hmac_code(code, challenge.phone_lookup, SENSITIVE_PURPOSE)
    if not constant_time_equals(expected, challenge.code_hmac):
        challenge.attempts += 1
        raise AppError("SMS_CODE_INVALID")

    consumed = affected_rows(session.execute(
        sa.update(type(challenge))
        .where(type(challenge).id == challenge.id, type(challenge).consumed_at.is_(None))
        .values(consumed_at=moment)
    ))
    if not consumed:
        raise AppError("SMS_CHALLENGE_USED")
    session.flush()


def phone_from_challenge(session: Session, challenge_id: str) -> str | None:
    """读取挑战对应的手机号（仅服务端使用，不返回给客户端）。"""
    from app.platform.security import decrypt_pii

    challenge = repo.get_challenge(session, challenge_id)
    if challenge is None:
        return None
    return decrypt_pii(challenge.phone_ciphertext)


__all__ = [
    "SENSITIVE_PURPOSE",
    "consume_sensitive_challenge",
    "create_sensitive_challenge",
    "phone_from_challenge",
    # 便于测试与调用方复用同一套规范化逻辑
    "encrypt_pii",
    "mask_phone",
    "normalize_phone",
    "phone_lookup",
]
