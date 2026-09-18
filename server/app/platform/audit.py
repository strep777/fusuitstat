"""审计与安全事件。

追加写、不带可编辑时间字段；敏感值不入记录。后台每个动作都必须留痕（FR-016）。
"""

from __future__ import annotations

import hashlib
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.platform.config import get_settings
from app.platform.ids import now_ms
from app.platform.reliability import AuditLog, digest

_ACTOR_TYPES = ("visitor", "customer", "staff", "system", "provider")


def ip_fingerprint(ip: str | None) -> str | None:
    """IP 只保存加盐哈希，不落明文（NFR-09）。"""
    if not ip:
        return None
    key = get_settings().secret("session_secret").encode("utf-8")
    return hashlib.sha256(key + ip.encode("utf-8")).hexdigest()[:32]


def record(
    session: Session,
    *,
    actor_type: str,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    actor_id: str | None = None,
    actor_label: str | None = None,
    before: Any | None = None,
    after: Any | None = None,
    reason: str | None = None,
    trace_id: str | None = None,
    ip: str | None = None,
) -> AuditLog:
    """在调用方事务内写审计记录（与业务变更同事务提交）。"""
    if actor_type not in _ACTOR_TYPES:
        raise ValueError(f"未登记的 actor_type：{actor_type}")
    entry = AuditLog(
        actor_type=actor_type,
        actor_id=actor_id,
        actor_label=actor_label,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        before_digest=digest(before) if before is not None else None,
        after_digest=digest(after) if after is not None else None,
        reason=reason,
        trace_id=trace_id,
        ip_hash=ip_fingerprint(ip),
        occurred_at=now_ms(),
    )
    session.add(entry)
    return entry


def query(
    session: Session,
    *,
    resource_type: str | None = None,
    resource_id: str | None = None,
    actor_id: str | None = None,
    action: str | None = None,
    limit: int = 50,
) -> list[AuditLog]:
    stmt = sa.select(AuditLog)
    if resource_type:
        stmt = stmt.where(AuditLog.resource_type == resource_type)
    if resource_id:
        stmt = stmt.where(AuditLog.resource_id == resource_id)
    if actor_id:
        stmt = stmt.where(AuditLog.actor_id == actor_id)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    stmt = stmt.order_by(AuditLog.occurred_at.desc()).limit(min(limit, 200))
    return list(session.scalars(stmt))
