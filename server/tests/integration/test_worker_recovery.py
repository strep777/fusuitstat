"""故障重启与至少一次投递的回归（R27）。

审计把「进程级故障重启演练」列为未完成项。这里用真实 outbox 表与真实 worker 逻辑，
在**不调用外部服务**的前提下验证三条不变量：

1. 处理中崩溃（租约未到期即进程消失）后，新 worker 在租约到期时能重新领取同一事件，
   且租约有效期内不会被第二个 worker 抢占；
2. 重复投递（至少一次语义）不产生第二次业务效果——「消费者名 + event_id」去重屏障；
3. 死信人工重试后仍受同一去重约束，不会因为重试而重复执行。

文件末尾另有 **进程外演练**（`python -m app.cli worker` 子进程 + 真实 kill）：
租约保护、`SIGKILL` 打断处理中批次后的恢复、以及恢复后每个事件恰好一次消费。
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
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


# ------------------------------------------------------------------ 进程外演练
SERVER_ROOT = Path(__file__).resolve().parents[2]


def _child_env(settings) -> dict[str, str]:  # type: ignore[no-untyped-def]
    """子进程 worker 的环境：与测试同一个 DATA_DIR 与数据库，不注入任何测试钩子。"""
    env = dict(os.environ)
    env.update(
        {
            "APP_ENV": "test",
            "PUBLIC_ORIGIN": "http://127.0.0.1:5173",
            "DATA_DIR": str(settings.data_dir),
            "SQLITE_SYNCHRONOUS": "NORMAL",
            "OUTBOX_POLL_SECONDS": "0.05",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUNBUFFERED": "1",
        }
    )
    env.pop("DATABASE_URL", None)
    return env


def _require_file_database(settings) -> None:  # type: ignore[no-untyped-def]
    if "memory" in settings.resolved_database_url:
        pytest.skip("内存数据库无法被子进程 worker 复用，跳过进程外演练")


def _spawn_worker(settings, *args: str) -> subprocess.Popen[str]:  # type: ignore[no-untyped-def]
    return subprocess.Popen(
        [sys.executable, "-m", "app.cli", "worker", *args],
        cwd=str(SERVER_ROOT),
        env=_child_env(settings),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _run_worker_once(settings, timeout: int = 180) -> str:  # type: ignore[no-untyped-def]
    completed = subprocess.run(
        [sys.executable, "-m", "app.cli", "worker", "--once"],
        cwd=str(SERVER_ROOT),
        env=_child_env(settings),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    assert completed.returncode == 0, f"子进程 worker 失败：{completed.stdout}\n{completed.stderr}"
    return completed.stdout


def _statuses(db, event_ids: list[str]) -> dict[str, int]:  # type: ignore[no-untyped-def]
    """按状态统计指定事件；worker 自己排期的周期任务不计入。"""
    db.expire_all()
    rows = db.execute(
        sa.select(OutboxEvent.status, sa.func.count())
        .where(OutboxEvent.event_id.in_(event_ids))
        .group_by(OutboxEvent.status)
    ).all()
    return {str(status): int(count) for status, count in rows}


def _consumed_for(db, event_ids: list[str]) -> int:  # type: ignore[no-untyped-def]
    db.expire_all()
    return int(
        db.scalar(
            sa.select(sa.func.count())
            .select_from(ConsumedEvent)
            .where(ConsumedEvent.event_id.in_(event_ids))
        )
        or 0
    )


def _duplicate_consumptions(db, event_ids: list[str]) -> list[tuple[str, int]]:  # type: ignore[no-untyped-def]
    db.expire_all()
    rows = db.execute(
        sa.select(ConsumedEvent.event_id, sa.func.count())
        .where(ConsumedEvent.event_id.in_(event_ids))
        .group_by(ConsumedEvent.event_id)
        .having(sa.func.count() > 1)
    ).all()
    return [(str(event_id), int(count)) for event_id, count in rows]


def test_out_of_process_worker_respects_lease_then_recovers(db, settings):
    """真实子进程 worker：租约未到期不抢别人的事件，到期后恢复且只执行一次。

    这是「worker 进程在持有租约时被杀」的进程外版本：持有方（模拟已消失的进程）在
    父进程里留下 CLAIMED，恢复由**另一个真实进程**完成。
    """
    _require_file_database(settings)
    from app.worker import claim_events

    event = _event(event_type="content.prebuild_requested")
    db.add(event)
    db.commit()
    ids = [event.event_id]

    # 模拟「worker 领取后进程消失」：事件停在 CLAIMED，租约 120 秒。
    claimed = claim_events(db, worker_id="dead-worker", limit=10, settings=settings)
    db.commit()
    assert [row.id for row in claimed] == [event.id]

    # 真实子进程 worker：租约有效期内不得抢走这条事件。
    _run_worker_once(settings)
    assert _statuses(db, ids) == {"CLAIMED": 1}, "租约有效期内被其他进程抢占"
    assert _consumed_for(db, ids) == 0
    db.expire_all()
    assert db.get(OutboxEvent, event.id).claimed_by == "dead-worker"  # type: ignore[union-attr]

    # 租约到期：子进程 worker 重新领取并完成，且只消费一次。
    db.execute(
        sa.update(OutboxEvent).where(OutboxEvent.id == event.id).values(claimed_until=now_ms() - 1)
    )
    db.commit()
    _run_worker_once(settings)
    assert _statuses(db, ids) == {"DONE": 1}
    assert _consumed_for(db, ids) == 1
    assert _duplicate_consumptions(db, ids) == []


def test_out_of_process_worker_killed_mid_batch_recovers_without_double_effects(db, settings):
    """真实 kill：子进程 worker 领取批次后被 SIGKILL，恢复后每个事件恰好一次消费。

    用真实处理器 `content.prebuild_requested`（会写预渲染产物）把批次拉长，
    以便在「已 CLAIMED、尚未全部完成」的窗口内杀掉进程；观察不到 CLAIMED 窗口即判失败，
    避免这条演练退化成空跑。
    """
    _require_file_database(settings)
    events = [_event(event_type="content.prebuild_requested") for _ in range(12)]
    db.add_all(events)
    db.commit()
    ids = [row.event_id for row in events]

    process = _spawn_worker(settings)
    caught = False
    deadline = time.monotonic() + 90
    try:
        while time.monotonic() < deadline:
            if _statuses(db, ids).get("CLAIMED"):
                caught = True
                break
            if process.poll() is not None:
                break
            time.sleep(0.02)
        assert caught, "未能在子进程 worker 处理过程中观察到 CLAIMED 窗口，演练无法成立"
    finally:
        process.kill()
        output = process.communicate(timeout=60)[0]

    # 被 kill 的进程留下了 CLAIMED 事件（处理中崩溃），消费记录不完整。
    statuses = _statuses(db, ids)
    assert statuses.get("CLAIMED", 0) >= 1, f"kill 后应留下未完成的 CLAIMED 事件；子进程输出：{output}"
    assert _consumed_for(db, ids) < len(events)

    # 恢复：租约到期后由新的子进程 worker 全部完成。
    db.execute(
        sa.update(OutboxEvent)
        .where(OutboxEvent.event_id.in_(ids))
        .values(claimed_until=now_ms() - 1)
    )
    db.commit()
    for _ in range(5):
        _run_worker_once(settings)
        done_statuses = _statuses(db, ids)
        if done_statuses.get("DONE", 0) == len(events) and "CLAIMED" not in done_statuses:
            break
    statuses = _statuses(db, ids)
    assert statuses == {"DONE": len(events)}, f"崩溃后事件丢失或未完成：{statuses}"
    assert _consumed_for(db, ids) == len(events), "恢复后每个事件恰好写一条消费记录（效果一次）"
    assert _duplicate_consumptions(db, ids) == []
