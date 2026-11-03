"""内容发布 → 预渲染产物 → 撤回的端到端回归（R12）。

审计指出：`worker.py` 的预渲染处理器曾经只把任务标成 DONE，没有真正生成产物；
修好后必须有实测回归锁住「产物真的落盘、可被消费、撤回后不再出现」。

依据：04 文档（公开内容预渲染走独立任务）、12/14 文档（发布流程与产物保留）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import sqlalchemy as sa

from app.platform.ids import new_id, now_ms
from app.platform.reliability import OutboxEvent


def test_prebuild_failed_directory_swap_restores_previous_output(db, tmp_path, monkeypatch):
    from app.ops.prebuild import MANIFEST_NAME, run_prebuild

    output = tmp_path / "prebuild"
    assert run_prebuild(db, output_dir=output)["status"] == "DONE"
    before = (output / MANIFEST_NAME).read_bytes()
    original_rename = Path.rename

    def fail_new_directory(source, target):
        if source.name.startswith(".prebuild.staging-"):
            raise PermissionError("simulated directory swap failure")
        return original_rename(source, target)

    monkeypatch.setattr(Path, "rename", fail_new_directory)
    result = run_prebuild(db, output_dir=output)
    assert result["status"] == "FAILED"
    assert (output / MANIFEST_NAME).read_bytes() == before


def test_prebuild_retries_transient_windows_directory_lock(db, tmp_path, monkeypatch):
    from app.ops import prebuild

    output = tmp_path / "prebuild"
    assert prebuild.run_prebuild(db, output_dir=output)["status"] == "DONE"
    before = (output / prebuild.MANIFEST_NAME).read_bytes()
    original_rename = Path.rename
    attempts = []
    delays = []

    def locked_once(source, target):
        if source.name.startswith(".prebuild.staging-"):
            attempts.append(source)
            if len(attempts) == 1:
                error = PermissionError("simulated Windows sharing violation")
                error.winerror = 32
                raise error
        return original_rename(source, target)

    monkeypatch.setattr(Path, "rename", locked_once)
    monkeypatch.setattr(prebuild.time, "sleep", delays.append)
    assert prebuild.run_prebuild(db, output_dir=output)["status"] == "DONE"
    assert len(attempts) == 2 and delays == [0.05]
    assert (output / prebuild.MANIFEST_NAME).read_bytes() != before


def test_prebuild_permanent_windows_lock_stops_retrying_and_restores_output(db, tmp_path, monkeypatch):
    from app.ops import prebuild

    output = tmp_path / "prebuild"
    assert prebuild.run_prebuild(db, output_dir=output)["status"] == "DONE"
    before = (output / prebuild.MANIFEST_NAME).read_bytes()
    original_rename = Path.rename
    attempts = []
    delays = []

    def locked(source, target):
        if source.name.startswith(".prebuild.staging-"):
            attempts.append(source)
            error = PermissionError("simulated Windows access denied")
            error.winerror = 5
            raise error
        return original_rename(source, target)

    monkeypatch.setattr(Path, "rename", locked)
    monkeypatch.setattr(prebuild.time, "sleep", delays.append)
    assert prebuild.run_prebuild(db, output_dir=output)["status"] == "FAILED"
    assert len(attempts) == 4 and delays == [0.05, 0.1, 0.2]
    assert (output / prebuild.MANIFEST_NAME).read_bytes() == before


def _publish_entry(db, *, slug: str, title: str, kind: str = "news"):
    from app.modules.content.models import ContentEntry

    entry = ContentEntry(
        id=new_id("content"),
        kind=kind,
        slug=slug,
        title=title,
        summary="预渲染回归用例",
        sanitized_body="<p>正文内容</p>",
        body_format="html",
        status="published",
        publish_at=now_ms(),
    )
    db.add(entry)
    db.commit()
    return entry


def test_prebuild_writes_dataset_then_worker_event_marks_job_done(db, tmp_path):
    """预渲染真实产出文件；worker 消费 outbox 事件后任务为 DONE 且产物可消费。"""
    from app.ops.prebuild import MANIFEST_NAME, PREBUILD_FORMAT, run_prebuild
    from app.worker import handle_event

    entry = _publish_entry(db, slug="prebuild-regression", title="预渲染回归公告")
    output_dir = tmp_path / "prebuild"

    manifest = run_prebuild(db, output_dir=output_dir, trigger="test", locales=("zh-CN",))
    assert manifest["status"] == "DONE"
    assert manifest["counts"]["routes"] >= 1

    manifest_file = output_dir / MANIFEST_NAME
    assert manifest_file.is_file(), "预渲染必须真正写出 manifest.json"
    written = json.loads(manifest_file.read_text(encoding="utf-8"))
    assert written["format"] == PREBUILD_FORMAT
    assert written["dataset_digest"] == manifest["dataset_digest"]
    routes = {item["route"] for item in written["routes"]}
    assert any(str(entry.slug) in route for route in routes), routes

    # 数据集文件本身也必须存在且可解析（避免「只有清单没有数据」的假产物）。
    dataset_files = list(output_dir.rglob("*.json")) + list(output_dir.rglob("*.html"))
    assert len(dataset_files) >= 2, [str(item) for item in dataset_files]

    # worker 的 outbox 处理器必须真实调用 run_prebuild，并在成功后才置 DONE。
    event = OutboxEvent(
        id=new_id("event"),
        event_id=f"prebuild-{new_id('e')}",
        event_type="content.prebuild_requested",
        aggregate_id=str(entry.id),
        aggregate_version=1,
        payload={"job_id": None, "trigger": "test"},
        available_at=now_ms(),
    )
    db.add(event)
    db.commit()
    result = handle_event(db, event)
    db.commit()
    assert result == "prebuild-done"

    from app.modules.content.models import PrebuildJob

    jobs = list(db.scalars(sa.select(PrebuildJob).order_by(PrebuildJob.created_at.desc()).limit(2)))
    assert jobs, "预渲染必须留下任务记录"
    assert jobs[0].status == "DONE"


def test_prebuild_failure_is_recorded_and_keeps_previous_output(db, tmp_path, monkeypatch):
    """预渲染失败时任务置 FAILED、保留上一版本产物，且 worker 抛出以触发 outbox 重试。"""
    from app.modules.content.models import PrebuildJob
    from app.ops import prebuild as prebuild_module
    from app.worker import handle_event

    entry = _publish_entry(db, slug="prebuild-failure", title="预渲染失败回归")
    output_dir = tmp_path / "prebuild"
    good = prebuild_module.run_prebuild(db, output_dir=output_dir, trigger="test")
    assert good["status"] == "DONE"
    before = (output_dir / prebuild_module.MANIFEST_NAME).read_text(encoding="utf-8")

    def _boom(*_args, **_kwargs):
        raise RuntimeError("PREBUILD_BROKEN")

    monkeypatch.setattr(prebuild_module, "_collect_dataset", _boom)
    failed = prebuild_module.run_prebuild(db, output_dir=output_dir, trigger="test")
    assert failed["status"] == "FAILED"
    assert "PREBUILD_BROKEN" in str(failed["error"])

    # 上一版本产物必须原样保留：失败不能把线上数据清掉。
    assert (output_dir / prebuild_module.MANIFEST_NAME).read_text(encoding="utf-8") == before
    statuses = [row.status for row in db.scalars(sa.select(PrebuildJob))]
    assert "FAILED" in statuses

    # worker 处理失败必须抛错（由 outbox 退避重试），而不是静默标成 DONE。
    event = OutboxEvent(
        id=new_id("event"),
        event_id=f"prebuild-{new_id('e')}",
        event_type="content.prebuild_requested",
        aggregate_id=str(entry.id),
        aggregate_version=1,
        payload={"job_id": None},
        available_at=now_ms(),
    )
    db.add(event)
    db.commit()
    with pytest.raises(RuntimeError, match="PREBUILD_FAILED"):
        handle_event(db, event)
