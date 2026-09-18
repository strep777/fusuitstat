"""客户邮箱验证。验证完成前不用于业务投递，换绑不提前覆盖旧邮箱。"""
import secrets
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.adapters.mail import get_gateway
from app.modules.identity.models import CustomerEmail
from app.platform.db import affected_rows
from app.platform.errors import AppError
from app.platform.ids import now_ms
from app.platform.rate_limit import check
from app.platform.security import constant_time_equals, decrypt_pii, encrypt_pii, hmac_code


def verified_email(session: Session, user_id: str) -> str | None:
    row = session.get(CustomerEmail, user_id)
    if row and row.verified_at and row.email_ciphertext:
        return decrypt_pii(row.email_ciphertext)
    return None


def email_status(session: Session, user_id: str) -> dict[str, Any]:
    address = verified_email(session, user_id)
    masked = None
    if address:
        local, domain = address.rsplit("@", 1)
        masked = local[:1] + "***@" + domain
    return {"verified": address is not None, "email_masked": masked}


def start_verification(session: Session, *, user_id: str, email: str) -> None:
    check(session, scope="email_verify_hour", subject=user_id, limit=5, window_seconds=3600)
    moment = now_ms()
    row = session.get(CustomerEmail, user_id)
    if row and row.sent_at and moment - row.sent_at < 60_000:
        raise AppError("RATE_LIMITED", "请稍后再发送验证邮件。")
    if row is None:
        row = CustomerEmail(user_id=user_id)
        session.add(row)
    code = f"{secrets.randbelow(1_000_000):06d}"
    row.pending_ciphertext = encrypt_pii(email)
    row.challenge_hash = hmac_code(user_id, code, namespace="email-verify")
    row.expires_at = moment + 600_000
    row.attempts = 0
    row.sent_at = moment
    # 验证邮件不进入业务通知正文/日志表；沙盒发送器仍明确标记测试环境。
    get_gateway().send(to=email, subject="弈星纪元：验证交易邮箱", body=f"验证码：{code}。10 分钟内有效，请勿转发。")
    session.flush()


def confirm_verification(session: Session, *, user_id: str, code: str) -> bool:
    row = session.get(CustomerEmail, user_id)
    moment = now_ms()
    if not row or not row.challenge_hash or not row.expires_at or row.expires_at <= moment or row.attempts >= 5:
        return False
    expected = hmac_code(user_id, code, namespace="email-verify")
    if not constant_time_equals(row.challenge_hash, expected):
        session.execute(sa.update(CustomerEmail).where(
            CustomerEmail.user_id == user_id, CustomerEmail.challenge_hash == row.challenge_hash,
            CustomerEmail.attempts < 5,
        ).values(attempts=CustomerEmail.attempts + 1))
        return False
    result = session.execute(sa.update(CustomerEmail).where(
        CustomerEmail.user_id == user_id, CustomerEmail.challenge_hash == expected,
        CustomerEmail.expires_at > moment, CustomerEmail.attempts < 5,
    ).values(email_ciphertext=CustomerEmail.pending_ciphertext, verified_at=moment,
             pending_ciphertext=None, challenge_hash=None, expires_at=None, attempts=0))
    return affected_rows(result) == 1
