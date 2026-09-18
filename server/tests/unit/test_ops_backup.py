"""TC-R02：备份一致性（备份期间持续写入）与恢复后行数/金额/积分汇总一致。

覆盖 14 文档「备份与恢复」的硬性要求：
1. WAL 活跃时禁止只复制 ``.db`` 文件——实现必须走 SQLite online backup API；
2. 备份包含数据库快照、schema 版本、对象存储清单与不含密钥的配置引用；
3. 快照通过 ``PRAGMA integrity_check`` 与 ``PRAGMA foreign_key_check``；
4. 恢复（``restore_to``）到隔离目录后，行数与金额/积分汇总与备份清单一致。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import sqlalchemy as sa

from app.modules.catalog.models import Sku
from app.modules.commerce.models import Order
from app.modules.payments.models import PaymentAllocation, PaymentAttempt
from app.ops import backup as backup_ops
from app.platform.config import get_settings
from app.platform.db import create_engine_for, get_engine
from app.platform.ids import new_id
from tests.conftest import create_product, create_sku, create_user

ORDER_TOTAL_CENTS = 10_800
ORDER_COUNT = 6
ALLOCATION_CENTS = 5_400
ALLOCATION_COUNT = 2
SEEDED_SKUS = 40
CONCURRENT_SKUS = 25


def _seed_money(session, user_id: str) -> None:
    """写入可精确断言的订单与收款分配（金额一律整数分，外键真实存在）。"""
    for index in range(ORDER_COUNT):
        order = Order(
            id=new_id("order"),
            order_no=f"T-{index:04d}",
            user_id=user_id,
            goods_cents=10_000,
            discount_cents=0,
            shipping_cents=800,
            total_cents=ORDER_TOTAL_CENTS,
            status="PENDING_PAYMENT",
        )
        session.add(order)
        session.flush()
        if index >= ALLOCATION_COUNT:
            continue
        attempt = PaymentAttempt(
            id=new_id("attempt"),
            order_id=order.id,
            installment_id=new_id("installment"),
            user_id=user_id,
            provider="sandbox",
            merchant_order_no=f"M-{index:04d}",
            expected_cents=ORDER_TOTAL_CENTS,
            currency="CNY",
            status="SUCCEEDED",
        )
        session.add(attempt)
        session.flush()
        session.add(
            PaymentAllocation(
                id=new_id("allocation"),
                payment_id=attempt.id,
                order_id=order.id,
                installment_id=attempt.installment_id,
                amount_cents=ALLOCATION_CENTS,
                classification="APPLIED",
            )
        )
    session.flush()


def test_tc_r02_backup_is_consistent_under_concurrent_writes_and_restores(db) -> None:
    """备份期间持续写入：快照自洽、可校验，并能在隔离目录恢复出同样的汇总。"""
    settings = get_settings()
    user = create_user(db)
    product = create_product(db)
    for index in range(SEEDED_SKUS):
        create_sku(db, product_id=product.id, sku_code=f"SEED-{index:04d}")
    _seed_money(db, user.id)
    db.commit()

    # 备份期间持续写入：独立引擎 + 独立事务，与主连接并发（模拟真实的写流量）。
    writer_engine = create_engine_for(settings)
    writer_factory = sa.orm.sessionmaker(bind=writer_engine, expire_on_commit=False, future=True)
    started = threading.Event()
    stop = threading.Event()
    written = {"count": 0}
    failures: list[str] = []

    def _write_loop() -> None:
        try:
            while not stop.is_set() and written["count"] < CONCURRENT_SKUS:
                index = written["count"]
                with writer_factory() as write_session:
                    write_session.add(
                        Sku(
                            id=new_id("sku"),
                            product_id=product.id,
                            sku_code=f"CONCURRENT-{index:04d}",
                            label="并发写入",
                            price_cents=1000,
                            stock_on_hand=5,
                            reserved=0,
                            max_per_order=3,
                            is_active=1,
                        )
                    )
                    write_session.commit()
                written["count"] += 1
                started.set()
        except Exception as exc:  # noqa: BLE001 - 线程内异常带回主线程断言
            failures.append(f"{type(exc).__name__}: {exc}")
            started.set()

    writer = threading.Thread(target=_write_loop, name="tc-r02-writer", daemon=True)
    writer.start()
    assert started.wait(timeout=15), "并发写入线程未能启动"
    try:
        backup_dir = backup_ops.create_backup(get_engine(), label="tc-r02")
    finally:
        stop.set()
        writer.join(timeout=15)
        writer_engine.dispose()
    assert not failures, failures

    # 1. 备份是目录形态、文件名带时间戳与标签，快照已加密（不是裸 .db 文件）。
    assert backup_dir.is_dir(), "备份必须是目录（快照 + 清单 + 配置引用）"
    assert backup_dir.name.endswith("-tc-r02"), backup_dir.name
    assert (backup_dir / backup_ops.MANIFEST_NAME).is_file()
    assert (backup_dir / backup_ops.SNAPSHOT_NAME).is_file()
    assert not (backup_dir / "database.sqlite3").exists(), "快照不得以明文形式落盘"

    # 2. 校验报告：完整性、外键、schema 版本、行数与金额/积分汇总。
    report = backup_ops.verify_backup(backup_dir)
    assert report["ok"] is True, report["problems"]
    assert report["integrity_check"] == "ok"
    assert report["foreign_key_violations"] == 0
    assert report["row_counts"]["orders"] == ORDER_COUNT
    assert report["row_counts"]["payment_allocations"] == ALLOCATION_COUNT
    assert report["money_totals"]["orders_total_cents"] == ORDER_TOTAL_CENTS * ORDER_COUNT
    assert report["money_totals"]["payment_applied_cents"] == ALLOCATION_CENTS * ALLOCATION_COUNT
    assert report["reconcile_required"].startswith("恢复后必须对恢复时间之后的全部渠道交易重新对账")

    # 3. 备份期间的持续写入不得让快照撕裂（torn snapshot）：
    #    快照的 SKU 行数落在 [本轮写入量] 区间内，且不超过冻结时刻的真实行数。
    frozen = int(db.scalar(sa.select(sa.func.count()).select_from(Sku)) or 0)
    snapshot_skus = report["row_counts"]["skus"]
    assert snapshot_skus is not None
    assert SEEDED_SKUS + 1 <= snapshot_skus <= SEEDED_SKUS + 1 + CONCURRENT_SKUS
    assert snapshot_skus <= frozen, "快照不能包含冻结时刻之后才提交的行"
    assert written["count"] >= 1, "并发写入未真正发生，测试无效"

    # 4. 恢复演练：恢复到隔离目录，汇总必须与备份清单一致。
    #    隔离目录放在数据目录内的独立子目录，保证与运行中的库完全分离。
    restore_dir = settings.data_dir / "restore-drill"
    restored = backup_ops.restore_to(backup_dir, restore_dir)
    assert restored["ok"] is True, restored["problems"]
    assert restored["row_counts"] == report["row_counts"]
    assert restored["money_totals"] == report["money_totals"]
    assert restored["point_totals"] == report["point_totals"]
    assert restored["schema_version"] == report["schema_version"]
    assert len(restored["reconcile_steps"]) >= 4, "恢复报告必须列出重新对账步骤"

    restored_path = Path(restored["restored_database"])
    assert restored_path.is_file(), "恢复出的数据库文件必须存在"
    live_path = Path(settings.resolved_database_url.split("///", 1)[-1])
    assert restored_path.resolve() != live_path.resolve(), "恢复不得写回运行中的数据库"

    # 恢复出来的库可以直接被 SQLite 打开并读到同样的金额汇总。
    connection = sa.create_engine(f"sqlite+pysqlite:///{restored_path.as_posix()}")
    with connection.connect() as restored_connection:
        total = restored_connection.execute(
            sa.text("SELECT COALESCE(SUM(total_cents), 0) FROM orders")
        ).scalar()
    connection.dispose()
    assert int(total or 0) == ORDER_TOTAL_CENTS * ORDER_COUNT


def test_backup_manifest_has_no_plaintext_secrets(db) -> None:
    """备份清单只记录必要配置引用与非敏感项，绝不写入密钥明文。"""
    settings = get_settings()
    secret = settings.session_secret or settings.secret("session_secret")
    create_user(db)
    db.commit()

    backup_dir = backup_ops.create_backup(get_engine(), label="secrets", settings=settings)
    manifest = (backup_dir / backup_ops.MANIFEST_NAME).read_text(encoding="utf-8")
    assert secret not in manifest, "备份清单不得包含主密钥明文"
    assert '"values"' in manifest and '"secret_configured"' in manifest
    # 恢复说明随备份一起落盘（已加密），内容必须写明「恢复后重新对账」。
    runbook = backup_dir / f"{backup_ops.RUNBOOK_NAME}.enc"
    assert runbook.is_file(), "备份必须带恢复说明"
    declared = json.loads(manifest)
    assert backup_ops.RUNBOOK_NAME in declared["files"], declared["files"].keys()
    assert declared["warnings"], "备份必须声明恢复后重新对账"

    # 封口摘要被改写后必须被检出（备份被篡改不能静默通过）。
    (backup_dir / backup_ops.MANIFEST_NAME).write_text(
        manifest.replace('"label": "secrets"', '"label": "tampered"'), encoding="utf-8"
    )
    tampered = backup_ops.verify_backup(backup_dir)
    assert tampered["ok"] is False
    assert any("封口" in problem for problem in tampered["problems"]), tampered["problems"]


def test_backup_uses_session_factory_connection(db) -> None:
    """``create_backup`` 也接受裸 sqlite3 连接（CLI 走的是这条路径）。"""
    create_user(db)
    db.commit()
    with get_engine().connect() as connection:
        backup_dir = backup_ops.create_backup(
            connection.connection.dbapi_connection, label="cli-path"
        )
    report = backup_ops.verify_backup(backup_dir)
    assert report["ok"] is True, report["problems"]
    assert report["row_counts"]["users"] == 1


# ------------------------------------------------------------------ 受控预渲染
def _prebuild_news(session, *, slug: str, title: str, status: str):
    from app.modules.content import service as content_service

    entry = content_service.create_entry(
        session,
        kind="news",
        slug=slug,
        locale="zh-CN",
        title=title,
        summary="",
        body=f"<p>{title}</p>",
        actor_id=None,
    )
    entry.status = status
    session.flush()
    return entry


def test_prebuild_exports_published_only_and_keeps_previous_version(db, monkeypatch) -> None:
    """受控预渲染：只导出已发布内容，失败保留上一版本（ADR-04）。"""
    from app.modules.content import repository as content_repo
    from app.modules.content import service as content_service
    from app.ops import prebuild as prebuild_ops
    from app.platform.config import get_settings

    settings = get_settings()
    _prebuild_news(db, slug="published-news", title="已发布公告", status="published")
    _prebuild_news(db, slug="draft-news", title="草稿公告", status="draft")
    content_service.ensure_default_settings(db)
    db.commit()

    target = settings.data_dir / "prebuild"
    result = prebuild_ops.run_prebuild(db, output_dir=target, actor_id=None, trigger="pytest")
    assert result["status"] == "DONE", result
    assert result["routes"] == ["/news/published-news"]

    manifest_text = (target / prebuild_ops.MANIFEST_NAME).read_text(encoding="utf-8")
    manifest = json.loads(manifest_text)
    assert manifest["format"] == prebuild_ops.PREBUILD_FORMAT
    assert "draft-news" not in manifest_text, "草稿不得进入预渲染产物"
    assert (target / "data/pages/news/published-news.json").is_file()
    assert (target / "data/site/settings.json").is_file()
    # 产物是数据与路由清单，不是前端资源包；不得含密钥。
    assert not list(target.rglob("*.js"))
    assert "secret" not in manifest_text.lower()

    # 构建失败：抛出后旧产物必须原样保留，任务状态记为 FAILED。
    before = (target / prebuild_ops.MANIFEST_NAME).read_text(encoding="utf-8")

    def _boom(*args, **kwargs):
        raise RuntimeError("模拟预渲染失败")

    monkeypatch.setattr(content_repo, "list_published", _boom)
    failed = prebuild_ops.run_prebuild(db, output_dir=target, actor_id=None, trigger="pytest-fail")
    assert failed["status"] == "FAILED"
    assert "RuntimeError" in (failed["error"] or "")
    assert (target / prebuild_ops.MANIFEST_NAME).read_text(encoding="utf-8") == before
    monkeypatch.undo()

    jobs = content_repo.list_prebuild_jobs(db, limit=5)
    assert {row.status for row in jobs} >= {"DONE", "FAILED"}, [row.status for row in jobs]