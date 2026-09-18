"""持久化限流与预算熔断。

计数在数据库原子维护，不能靠一个 worker 的内存（10 文档）。
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.platform.config import get_settings
from app.platform.db import affected_rows
from app.platform.errors import AppError
from app.platform.ids import new_id, now_ms
from app.platform.reliability import RateLimitBucket
from app.platform.security import hash_token


def _subject_hash(scope: str, subject: str) -> str:
    """主体摘要：不落明文手机号/IP。"""
    secret = get_settings().secret("session_secret")
    return hash_token(f"{scope}::{secret}::{subject}")[:64]


def consume(
    session: Session,
    *,
    scope: str,
    subject: str,
    limit: int,
    window_seconds: int,
    now: int | None = None,
) -> tuple[int, int]:
    """占用一个配额。

    返回 ``(已用次数, 重试秒数)``；超限抛 ``RATE_LIMITED``/调用方指定错误码。
    使用原子 upsert，多个 worker 同时计数不会丢失。
    """
    moment = now if now is not None else now_ms()
    window_start = moment - (moment % (window_seconds * 1000))
    subject_hash = _subject_hash(scope, subject)
    expires_at = window_start + window_seconds * 1000

    statement = (
        sqlite_insert(RateLimitBucket)
        .values(
            scope=scope,
            subject_hash=subject_hash,
            window_start=window_start,
            count=1,
            expires_at=expires_at,
        )
        .on_conflict_do_update(
            index_elements=["scope", "subject_hash", "window_start"],
            set_={"count": RateLimitBucket.__table__.c.count + 1},
        )
        .returning(RateLimitBucket.__table__.c.count)
    )
    count = int(session.execute(statement).scalar_one())
    retry_after = max(1, (expires_at - moment) // 1000)
    return count, retry_after


def check(
    session: Session,
    *,
    scope: str,
    subject: str,
    limit: int,
    window_seconds: int,
    code: str = "RATE_LIMITED",
    now: int | None = None,
) -> None:
    """超限抛错；未超限则占用一次配额。"""
    count, retry_after = consume(
        session, scope=scope, subject=subject, limit=limit, window_seconds=window_seconds, now=now
    )
    if count > limit:
        raise AppError(
            code,
            f"操作过于频繁，请在 {retry_after} 秒后重试。",
            http_status=429,
            headers={"Retry-After": str(retry_after)},
        )


def peek(session: Session, *, scope: str, subject: str, window_seconds: int, now: int | None = None) -> int:
    """只读查询当前窗口计数，不占用配额。"""
    moment = now if now is not None else now_ms()
    window_start = moment - (moment % (window_seconds * 1000))
    subject_hash = _subject_hash(scope, subject)
    value = session.execute(
        sa.select(RateLimitBucket.count).where(
            RateLimitBucket.scope == scope,
            RateLimitBucket.subject_hash == subject_hash,
            RateLimitBucket.window_start == window_start,
        )
    ).scalar_one_or_none()
    return int(value or 0)


def cleanup(session: Session, *, now: int | None = None, limit: int = 500) -> int:
    """清理过期窗口。"""
    moment = now if now is not None else now_ms()
    result = session.execute(
        sa.delete(RateLimitBucket).where(
            RateLimitBucket.expires_at < moment - 86_400_000,
            RateLimitBucket.scope.in_(
                sa.select(RateLimitBucket.scope).where(RateLimitBucket.expires_at < moment).limit(limit)
            ),
        )
    )
    return affected_rows(result)


class DailyBudget:
    """每日预算熔断（短信费用等）。超预算时拒绝并保留异常申诉入口。"""

    def __init__(self, session: Session, *, scope: str, limit: int) -> None:
        self.session = session
        self.scope = f"budget:{scope}"
        self.limit = limit

    def consume_and_check(self, subject: str = "global", *, now: int | None = None) -> int:
        moment = now if now is not None else now_ms()
        count, _ = consume(
            self.session, scope=self.scope, subject=subject, limit=self.limit, window_seconds=86_400, now=moment
        )
        if count > self.limit:
            raise AppError("SMS_BUDGET_EXHAUSTED")
        return count

    def used(self, subject: str = "global", *, now: int | None = None) -> int:
        return peek(self.session, scope=self.scope, subject=subject, window_seconds=86_400, now=now)


def new_scope_key() -> str:
    return new_id("job")
