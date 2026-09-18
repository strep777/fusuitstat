"""故障重启与至少一次投递的回归（R27）。

审计把「进程级故障重启演练」列为未完成项。这里用真实 outbox 表与真实 worker 逻辑，
在**不调用外部服务**的前提下验证三条不变量：

1. 处理中崩溃（租约未到期即进程消失）后，新 worker 在租约到期时能重新领取同一事件，
   且租约有效期内不会被第二个 worker 抢占；
2. 重复投递（至少一次语义）不产生第二次业务效果——「消费者名 + event_id」去重屏障；
3. 死信人工重试后仍受同一去重约束，不会因为重试而重复执行。
"""

from __future__ import annotations

import sqlalchemy as sa

from app.platform.ids import new_id, now_ms
from app.platform.reliability import ConsumedEvent, OutboxEvent


def _event(**overrides: object) -> OutboxEvent:
    payload = {
        "id": new_id("event"),
        "event_id": f"crash-{new_id('e')}",
        # 用已注册且无外部依赖的处理器：只做「已确认」回执，便于精确统计调用次数。
        "event_type": "settings.feature_flags_changed",
        "aggregate_id": "scheduler",
        "aggregate_version": 1,
        "payload": {"scheduled_at": now_ms()},
        "available_at": now_ms(),
    }
    payload.update(overrides)
    return OutboxEvent(**payload)  # type: ignore[arg-type]


def test_claimed_event_is_reclaimed_after_lease_expiry(db):
    """worker 崩溃后事件停留在 CLAIMED；租约到期后新 worker 必须能重新领取。"""
    from app.platform.config import get_settings
    from app.worker import claim_events

    settings = get_settings()
    event = _event()
    db.add(event)
    db.commit()

    # 第一轮：worker A 领取后「进程消失」——事件保持 CLAIMED，租约未到期。
    first = claim_events(db, worker_id="worker-A", limit=10, settings=settings)
    db.commit()
    assert [row.id for row in first] == [event.id]
    assert event.status == "CLAIMED"
    assert event.claimed_by == "worker-A"

    # 租约未到期：第二个 worker 不能抢同一个事件（避免重复处理）。
    blocked = claim_events(db, worker_id="worker-B", limit=10, settings=settings)
    assert blocked == [], "租约有效期内不得被其他 worker 抢占"

    # 租约到期：新 worker 重新领取，事件仍是同一条（不新建、不丢单）。
    event.claimed_until = now_ms() - 1
    db.commit()
    second = claim_events(db, worker_id="worker-B", limit=10, settings=settings)
    db.commit()
    assert [row.id for row in second] == [event.id]
    assert event.claimed_by == "worker-B"
    assert db.scalar(sa.select(sa.func.count()).select_from(OutboxEvent)) == 1


def test_redelivery_after_crash_has_single_business_effect(db, monkeypatch):
    """崩溃后重投同一事件：处理器只被调用一次，重复投递返回 duplicate 且不落第二次消费记录。"""
    from app.worker import _HANDLERS, handle_event

    calls: list[str] = []

    def _counting_handler(session, event):  # type: ignore[no-untyped-def]
        calls.append(event.event_id)
        return "acknowledged"

    monkeypatch.setitem(_HANDLERS, "settings.feature_flags_changed", _counting_handler)

    event = _event()
    db.add(event)
    db.commit()

    assert handle_event(db, event) == "acknowledged"
    db.commit()

    consumed = db.scalar(
        sa.select(sa.func.count())
        .select_from(ConsumedEvent)
        .where(ConsumedEvent.event_id == event.event_id)
    )
    assert consumed == 1, "消费去重必须落库，否则重启后无法判断是否已处理"

    # 模拟崩溃重启后同一事件被再次投递（至少一次语义）。
    assert handle_event(db, event) == "duplicate"
    db.commit()
    assert calls == [event.event_id], "重复投递不得再次执行业务效果"
    assert (
        db.scalar(
            sa.select(sa.func.count())
            .select_from(ConsumedEvent)
            .where(ConsumedEvent.event_id == event.event_id)
        )
        == 1
    )


def test_failed_event_backs_off_then_becomes_dead_letter(db, monkeypatch):
    """处理失败按指数退避重试，超过上限进入死信，且错误原因留痕。"""
    from app.platform.config import get_settings
    from app.worker import _HANDLERS, _fail_event, handle_event

    settings = get_settings()

    def _boom(session, event):  # type: ignore[no-untyped-def]
        raise RuntimeError("HANDLER_BROKEN")

    monkeypatch.setitem(_HANDLERS, "settings.feature_flags_changed", _boom)
    event = _event()
    db.add(event)
    db.commit()

    import pytest

    with pytest.raises(RuntimeError, match="HANDLER_BROKEN"):
        handle_event(db, event)

    for _ in range(settings.outbox_max_attempts):
        _fail_event(db, event.id, "HANDLER_BROKEN", settings)
        db.expire_all()
        row = db.get(OutboxEvent, event.id)
        assert row is not None
        if row.status == "DEAD":
            break
    row = db.get(OutboxEvent, event.id)
    assert row is not None
    assert row.status == "DEAD", "超过最大尝试次数必须进入死信而不是无限重试"
    assert row.attempts >= settings.outbox_max_attempts
    assert "HANDLER_BROKEN" in (row.last_error or "")


def test_dead_letter_manual_retry_keeps_idempotency(db, monkeypatch):
    """死信人工重试：状态被重置并可再次处理，但仍受消费去重约束。"""
    from app.worker import _HANDLERS, handle_event, retry_dead_letter

    calls: list[str] = []
    monkeypatch.setitem(
        _HANDLERS,
        "settings.feature_flags_changed",
        lambda session, event: calls.append(event.event_id) or "acknowledged",
    )

    event = _event(status="DEAD", attempts=5, last_error="boom")
    db.add(event)
    db.commit()

    assert retry_dead_letter(event.id) is True
    db.expire_all()
    refreshed = db.get(OutboxEvent, event.id)
    assert refreshed is not None
    assert refreshed.status == "PENDING" and refreshed.attempts == 0

    assert handle_event(db, refreshed) == "acknowledged"
    db.commit()
    assert handle_event(db, refreshed) == "duplicate"
    db.commit()
    assert calls == [event.event_id], "人工重试不得造成第二次业务效果"
    assert retry_dead_letter("event-does-not-exist") is False
