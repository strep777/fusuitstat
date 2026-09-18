"""一致备份与隔离恢复（14 文档「备份与恢复」的唯一实现）。

规则：
1. SQLite 处于 WAL 活跃状态时**禁止**只复制 ``.db`` 文件作为备份：WAL 中已提交
   但尚未检查点的事务不在主库文件里，复制出来的文件不是一致快照。本模块统一使用
   SQLite online backup API（``sqlite3.Connection.backup``）生成一致快照。
2. 备份内容 = 数据库快照 + schema 版本（``alembic_version``）+ 对象存储清单
   （``storage/public``、``storage/private`` 的文件与 sha256）+ 必要配置引用
   （**不含密钥明文**，只记录键名与是否已配置）。
3. 快照先做 ``PRAGMA integrity_check`` / ``PRAGMA foreign_key_check`` 自检，再写入
   逐文件 sha256 与清单封口摘要，备份目录名带时间戳与标签。
4. 备份目录内的每个文件都用 AES-256-GCM 加密（密钥由 ``SESSION_SECRET`` 派生，
   不落盘、不写日志）；把整个目录交给独立存储即可满足「离机加密备份」。

恢复后必做（不能只恢复数据库就重新接单）：
- 必须对**恢复时间之后的全部渠道交易重新对账**：关闭新下单与改变账本的人工操作，
  拉取备份时间之后的全部渠道收款/退款与账单，按 08 文档幂等补账，核查库存、名额与
  积分，在隔离环境验证登录/订单/私密图片/worker 后才逐步恢复开单；
- 恢复对象引用与密钥版本，应用注销/删除墓碑记录，防止已删除的私密资料复活；
- 记录丢失窗口与实际 RPO/RTO（14 文档恢复步骤 1–5）。
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import shutil
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.engine import Engine

from app.platform.config import Settings, get_settings
from app.platform.errors import AppError
from app.platform.ids import iso, new_id, now_ms

logger = logging.getLogger("starium.ops.backup")

BACKUP_FORMAT = "starium.backup/1"
MANIFEST_NAME = "manifest.json"
SNAPSHOT_NAME = "database.sqlite3.enc"
SEALED_NAME = ".sealed"
RUNBOOK_NAME = "RECOVERY.md"
DEFAULT_KEEP = 14

#: 恢复后必须重新对账的明确提醒，写进每次备份，避免运维只恢复数据库就接单。
RECONCILE_WARNING = (
    "恢复后必须对恢复时间之后的全部渠道交易重新对账（收款、退款、日账单），"
    "幂等补账并核查库存/名额/积分，验证通过后才可重新开放下单；"
    "仅恢复数据库不等于可以重新接单。"
)

#: 备份清单里允许出现的配置引用：只记录是否已配置与非敏感项，密钥一律不落盘。
CONFIG_REFERENCE_KEYS: tuple[str, ...] = (
    "app_env",
    "public_origin",
    "api_prefix",
    "site_name",
    "default_currency",
    "timezone_display",
    "database_url",
    "sqlite_busy_timeout_ms",
    "sqlite_synchronous",
    "pii_encryption_key_version",
    "admin_session_hours",
    "session_absolute_hours",
    "sms_adapter",
    "payment_providers",
    "storage_backend",
    "storage_public_prefix",
    "storage_private_prefix",
    "signed_url_ttl_seconds",
    "outbox_poll_seconds",
    "outbox_lease_seconds",
    "outbox_max_attempts",
)

#: 只在清单里记录「是否已配置」的敏感键，绝不记录取值。
SECRET_REFERENCE_KEYS: tuple[str, ...] = (
    "session_secret",
    "csrf_secret",
    "otp_secret",
    "phone_lookup_key",
    "pii_encryption_key",
    "smtp_password",
    "wechat_api_v3_key",
    "alipay_public_key",
    "paypal_client_id",
    "smtp_username",
)

#: 备份核对用的行数表；表不存在时记为 ``None``（模块尚未交付）。
ROW_COUNT_TABLES: tuple[str, ...] = (
    "users",
    "sessions",
    "addresses",
    "staff_accounts",
    "content_entries",
    "content_revisions",
    "site_settings",
    "products",
    "skus",
    "characters",
    "inquiries",
    "quotes",
    "orders",
    "order_items",
    "installments",
    "payment_attempts",
    "payment_allocations",
    "payment_events",
    "refunds",
    "refund_items",
    "reconciliation_differences",
    "point_accounts",
    "point_ledger",
    "point_awards",
    "feedback",
    "feedback_reviews",
    "outbox_events",
    "audit_logs",
)


# ------------------------------------------------------------------ 工具
def checksum(path: str | Path, *, algorithm: str = "sha256") -> str:
    """流式计算文件摘要；缺文件按业务错误返回，不抛裸 ``OSError``。"""
    target = Path(path)
    if not target.is_file():
        raise AppError("RESOURCE_NOT_FOUND", "备份文件不存在或已被移动。")
    digest = hashlib.new(algorithm)
    with target.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _backup_root(settings: Settings, target_dir: Path | None) -> Path:
    root = target_dir or (settings.data_dir / "backups")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _backup_key(settings: Settings) -> bytes:
    """备份加密密钥。由主密钥派生，不落盘；恢复密钥须与备份文件分离保管。"""
    material = f"backup:{settings.secret('session_secret')}".encode()
    return hashlib.sha256(material).digest()


def _seal_path(path: Path, *, key: bytes) -> None:
    data = path.read_bytes()
    nonce = os.urandom(12)
    payload = nonce + AESGCM(key).encrypt(nonce, data, path.name.encode("utf-8"))
    target = path.with_name(f"{path.name}.enc")
    target.write_bytes(base64.b64encode(payload))
    path.unlink()


def _unseal_bytes(path: Path, *, key: bytes) -> bytes:
    raw = base64.b64decode(path.read_bytes())
    nonce, payload = raw[:12], raw[12:]
    name = path.name[: -len(".enc")] if path.name.endswith(".enc") else path.name
    return AESGCM(key).decrypt(nonce, payload, name.encode("utf-8"))


def _row_counts(connection: sqlite3.Connection) -> dict[str, int | None]:
    counts: dict[str, int | None] = {}
    existing = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    for table in ROW_COUNT_TABLES:
        if table not in existing:
            counts[table] = None
            continue
        counts[table] = int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    return counts


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _schema_version(connection: sqlite3.Connection) -> str:
    if not _table_exists(connection, "alembic_version"):
        return "unmanaged"
    row = connection.execute("SELECT version_num FROM alembic_version LIMIT 1").fetchone()
    return str(row[0]) if row else "empty"


def _money_totals(connection: sqlite3.Connection) -> dict[str, int]:
    """金额汇总。至少覆盖 ``orders.total_cents`` 与 APPLIED 收款分配之和。"""
    totals = {
        "orders_total_cents": 0,
        "orders_paid_cents": 0,
        "payment_applied_cents": 0,
        "payment_overpayment_cents": 0,
        "payment_quarantined_cents": 0,
        "refunds_succeeded_cents": 0,
    }
    if _table_exists(connection, "orders"):
        row = connection.execute(
            "SELECT COALESCE(SUM(total_cents), 0), COALESCE(SUM(paid_cents), 0) FROM orders"
        ).fetchone()
        totals["orders_total_cents"] = int(row[0] or 0)
        totals["orders_paid_cents"] = int(row[1] or 0)
    if _table_exists(connection, "payment_allocations"):
        rows = connection.execute(
            "SELECT classification, COALESCE(SUM(amount_cents), 0) "
            "FROM payment_allocations GROUP BY classification"
        ).fetchall()
        by_kind = {str(kind): int(amount or 0) for kind, amount in rows}
        totals["payment_applied_cents"] = by_kind.get("APPLIED", 0)
        totals["payment_overpayment_cents"] = by_kind.get("OVERPAYMENT", 0)
        totals["payment_quarantined_cents"] = by_kind.get("QUARANTINED", 0)
    if _table_exists(connection, "refunds"):
        row = connection.execute(
            "SELECT COALESCE(SUM(amount_cents), 0) FROM refunds WHERE status = 'SUCCEEDED'"
        ).fetchone()
        totals["refunds_succeeded_cents"] = int(row[0] or 0)
    return totals


def _point_totals(connection: sqlite3.Connection) -> dict[str, int]:
    """积分汇总：账户缓存余额、账本净额与奖励状态。

    ``point_accounts`` 只是缓存，``point_ledger`` 才是权威流水（05 文档），因此两者
    都汇总，恢复后可以互相比对。积分表属于尚未交付的模块时返回空字典。
    """
    totals: dict[str, int] = {}
    if _table_exists(connection, "point_accounts"):
        row = connection.execute(
            "SELECT COALESCE(SUM(posted_balance), 0), COALESCE(SUM(reserved_points), 0) "
            "FROM point_accounts"
        ).fetchone()
        totals["account_posted_balance"] = int(row[0] or 0)
        totals["account_reserved_points"] = int(row[1] or 0)
    if _table_exists(connection, "point_ledger"):
        row = connection.execute("SELECT COALESCE(SUM(delta), 0) FROM point_ledger").fetchone()
        totals["ledger_delta"] = int(row[0] or 0)
    if _table_exists(connection, "point_awards"):
        row = connection.execute(
            "SELECT COALESCE(SUM(posted_points), 0), COALESCE(SUM(target_points), 0) "
            "FROM point_awards"
        ).fetchone()
        totals["awards_posted_points"] = int(row[0] or 0)
        totals["awards_target_points"] = int(row[1] or 0)
    return totals


def _integrity_of(connection: sqlite3.Connection) -> tuple[str, int]:
    integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
    violations = len(connection.execute("PRAGMA foreign_key_check").fetchall())
    return integrity, violations


def _storage_manifest(storage_root: Path, *, limit: int = 5000) -> dict[str, Any]:
    """对象存储清单：列出公开与私密前缀下的文件与 sha256。

    清单只记录 object key、字节数与摘要，**不记录签名 URL**（签名 URL 是临时凭证）。
    """
    buckets: dict[str, list[dict[str, Any]]] = {"public": [], "private": []}
    truncated = False
    for visibility in ("public", "private"):
        base = storage_root / visibility
        if not base.is_dir():
            continue
        files = sorted(item for item in base.rglob("*") if item.is_file())
        if len(files) > limit:
            files = files[:limit]
            truncated = True
        for item in files:
            buckets[visibility].append(
                {
                    "key": item.relative_to(base).as_posix(),
                    "bytes": item.stat().st_size,
                    "sha256": checksum(item),
                }
            )
    return {
        "root": "storage",
        "visibility_prefixes": list(buckets.keys()),
        "files": buckets,
        "counts": {name: len(items) for name, items in buckets.items()},
        "truncated": truncated,
    }


def _config_reference(settings: Settings) -> dict[str, Any]:
    """必要配置引用：只含非敏感项与「是否已配置」布尔值，不含密钥明文。"""
    plain: dict[str, Any] = {}
    for name in CONFIG_REFERENCE_KEYS:
        if not hasattr(settings, name):
            continue
        value = getattr(settings, name)
        plain[name] = str(value) if isinstance(value, Path) else value
    return {
        "values": plain,
        "secret_configured": {
            name: bool(getattr(settings, name, "")) for name in SECRET_REFERENCE_KEYS
        },
        "note": "仅记录键名与是否已配置；密钥本身不进入备份清单与日志。",
    }


def _write_runbook(path: Path, *, created_at: str, label: str, database_backend: str) -> None:
    path.write_text(
        "\n".join(
            [
                "# 备份恢复说明（14 文档）",
                "",
                f"- 备份时间：{created_at}",
                f"- 标签：{label}",
                f"- 数据库后端：{database_backend}",
                "- 快照方式：SQLite online backup API（WAL 活跃时禁止只复制 .db 文件）",
                "- 文件加密：AES-256-GCM，密钥由 SESSION_SECRET 派生，需与备份文件分离保管",
                "",
                "## 恢复步骤",
                "",
                "1. 关闭新下单与会改变账本的人工操作；保留回调的持久接收或让渠道按协议重试。",
                "2. 在新目录/新主机执行 `uv run python -m app.cli restore --path <备份目录>` 校验，",
                "   或调用 `app.ops.backup.restore_to(path, target_dir)` 恢复到隔离目录。",
                "3. 核对 integrity_check、foreign_key_check、行数、金额与积分汇总。",
                "4. 恢复对象引用与密钥版本，应用注销/删除墓碑记录，防已删除私密资料复活。",
                "5. 查询备份时间之后的全部渠道收款/退款与账单，幂等补账；核查库存、名额与积分。",
                "6. 在隔离环境验证登录/订单/私密图片/worker 后，才切入口并逐步开单。",
                "",
                f"> 重新对账要求：{RECONCILE_WARNING}",
                "",
            ]
        ),
        encoding="utf-8",
    )


# ------------------------------------------------------------------ 备份
def create_backup(
    connection: Any,
    *,
    label: str = "manual",
    target_dir: Path | None = None,
    settings: Settings | None = None,
) -> Path:
    """生成一致备份快照，返回封口后的备份目录。

    ``connection`` 既可以是 ``sqlite3.Connection``，也可以是 SQLAlchemy 引擎/连接；
    备份先写到临时目录，全部检查通过并写入封口摘要后才改名为正式目录，因此**中途失败
    不会留下半个备份，也不会覆盖上一份可恢复备份**（14 文档）。
    """
    active = settings or get_settings()
    root = _backup_root(active, target_dir)
    moment = now_ms()
    stamp = iso(moment) or ""
    safe_label = "".join(ch for ch in label if ch.isalnum() or ch in "-_") or "manual"
    final = root / f"{stamp.replace(':', '').replace('-', '')}-{safe_label}"
    staging = root / f".staging-{new_id('job')}"
    staging.mkdir(parents=True, exist_ok=True)
    key = _backup_key(active)

    try:
        # 1. SQLite online backup API：WAL 中的已提交事务也会进入快照。
        raw_snapshot = staging / "database.sqlite3"
        source = _dbapi_connection(connection, active)
        destination = sqlite3.connect(str(raw_snapshot))
        try:
            source.backup(destination)
            destination.commit()
        finally:
            destination.close()
        # 目标连接会留下空的 -wal/-shm 旁文件；它们不属于快照内容，先清掉。
        for sidecar in ("database.sqlite3-wal", "database.sqlite3-shm"):
            (staging / sidecar).unlink(missing_ok=True)
        snapshot_digest = checksum(raw_snapshot)

        # 2. 快照自检：不通过就整体放弃，绝不留下一份「看起来成功」的备份。
        inspected = _inspect(raw_snapshot, key=None)
        if inspected["integrity_check"] != "ok" or inspected["foreign_key_violations"]:
            raise AppError("INTERNAL_ERROR", "备份快照未通过完整性检查，已放弃本次备份。")

        storage = _storage_manifest(active.storage_root)
        config_reference = _config_reference(active)
        _write_runbook(
            staging / RUNBOOK_NAME,
            created_at=stamp,
            label=safe_label,
            database_backend="sqlite",
        )

        # 3. 逐文件摘要（不含清单自身与封口文件），最后一次性写清单。
        files: dict[str, dict[str, Any]] = {}
        for item in sorted(path for path in staging.iterdir() if path.is_file()):
            files[item.name] = {"bytes": item.stat().st_size, "sha256": checksum(item)}

        manifest: dict[str, Any] = {
            "format": BACKUP_FORMAT,
            "backup_id": final.name,
            "label": safe_label,
            "created_at": stamp,
            "created_at_ms": moment,
            "database_backend": "sqlite",
            "database_method": "sqlite3.Connection.backup",
            "warnings": [RECONCILE_WARNING],
            "snapshot": {
                "name": SNAPSHOT_NAME,
                "plain_name": "database.sqlite3",
                "plain_sha256": snapshot_digest,
                "encryption": "AES-256-GCM",
                "key_derivation": "sha256('backup:' + SESSION_SECRET)",
            },
            "integrity": {
                "integrity_check": inspected["integrity_check"],
                "foreign_key_violations": inspected["foreign_key_violations"],
            },
            "schema_version": inspected["schema_version"],
            "row_counts": inspected["row_counts"],
            "money_totals": inspected["money_totals"],
            "point_totals": inspected["point_totals"],
            "storage": storage,
            "config": config_reference,
            "files": files,
        }
        (staging / MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        sealed_hash = checksum(staging / MANIFEST_NAME)
        (staging / SEALED_NAME).write_text(
            json.dumps({"sha256": sealed_hash, "sealed_at": iso(now_ms())}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        # 4. 加密之后才落正式目录：整个目录可直接交给独立存储。
        for item in sorted(path for path in staging.iterdir() if path.is_file()):
            if item.name in (MANIFEST_NAME, SEALED_NAME):
                continue
            _seal_path(item, key=key)

        if final.exists():  # 同一秒内重复触发时保留全部备份，不覆盖
            final = root / f"{final.name}-{new_id('job')[-6:]}"
        staging.rename(final)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    _prune(root, keep=DEFAULT_KEEP)
    logger.info("备份完成：%s（schema=%s）", final.name, manifest["schema_version"])
    return final


def _dbapi_connection(connection: Any, settings: Settings) -> sqlite3.Connection:
    """从各种入参形态取出 ``sqlite3.Connection``；非 SQLite 一律拒绝。"""
    if isinstance(connection, sqlite3.Connection):
        return connection
    if isinstance(connection, Engine):
        if connection.dialect.name != "sqlite":
            raise AppError("INTERNAL_ERROR", "当前只支持 SQLite 一致快照备份。")
        raw = connection.raw_connection()
        driver = raw.driver_connection
        if not isinstance(driver, sqlite3.Connection):
            raise AppError("INTERNAL_ERROR", "当前只支持 SQLite 一致快照备份。")
        return driver
    if isinstance(connection, sa.engine.Connection):
        dbapi = connection.connection.dbapi_connection
        if not isinstance(dbapi, sqlite3.Connection):
            raise AppError("INTERNAL_ERROR", "当前只支持 SQLite 一致快照备份。")
        return dbapi
    if isinstance(connection, str | Path):
        target = Path(connection)
        if not target.is_file():
            raise AppError("RESOURCE_NOT_FOUND", "数据库文件不存在。")
        return sqlite3.connect(f"file:{target.as_posix()}?mode=ro", uri=True)
    if connection is None:
        target = _database_path(settings)
        return sqlite3.connect(f"file:{target.as_posix()}?mode=ro", uri=True)
    raise AppError("INTERNAL_ERROR", "不支持的备份入参。")


def _database_path(settings: Settings) -> Path:
    url = settings.resolved_database_url
    if not url.startswith("sqlite"):
        raise AppError("INTERNAL_ERROR", "当前只支持 SQLite 一致快照备份。")
    return Path(url.split("///", 1)[-1])


def _prune(root: Path, *, keep: int) -> None:
    """保留最近 ``keep`` 份正式备份；临时目录与最近备份永不在本次清理范围内。"""
    sealed = sorted(
        (
            item
            for item in root.iterdir()
            if item.is_dir() and not item.name.startswith(".") and (item / SEALED_NAME).is_file()
        ),
        key=lambda item: item.name,
    )
    for stale in sealed[:-keep] if keep > 0 else []:
        shutil.rmtree(stale, ignore_errors=True)


# ------------------------------------------------------------------ 校验与恢复
def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AppError("INTERNAL_ERROR", "备份清单无法解析，备份可能已损坏。") from exc
    if not isinstance(parsed, dict):
        raise AppError("INTERNAL_ERROR", "备份清单结构非法，备份可能已损坏。")
    return parsed


def _open_plain_snapshot(backup_dir: Path, key: bytes, scratch: Path) -> Path:
    """把加密快照解密到临时目录供校验/恢复；快照缺失即按损坏处理。"""
    sealed = backup_dir / SNAPSHOT_NAME
    if not sealed.is_file():
        raise AppError("RESOURCE_NOT_FOUND", "备份中缺少数据库快照。")
    plain = scratch / "database.sqlite3"
    try:
        plain.write_bytes(_unseal_bytes(sealed, key=key))
    except Exception as exc:  # noqa: BLE001 - 解密失败信息不落日志细节
        raise AppError("INTERNAL_ERROR", "备份解密失败：密钥不匹配或文件已损坏。") from exc
    return plain


def _inspect(snapshot: Path, *, key: bytes | None) -> dict[str, Any]:
    """对一份明文快照做完整校验并汇总行数/金额/积分。"""
    _ = key
    connection = sqlite3.connect(f"file:{snapshot.as_posix()}?mode=ro", uri=True)
    try:
        integrity, violations = _integrity_of(connection)
        return {
            "integrity_check": integrity,
            "foreign_key_violations": violations,
            "schema_version": _schema_version(connection),
            "row_counts": _row_counts(connection),
            "money_totals": _money_totals(connection),
            "point_totals": _point_totals(connection),
        }
    finally:
        connection.close()


def _report(
    inspected: dict[str, Any],
    *,
    manifest: dict[str, Any] | None,
    extra_problems: Iterable[str] = (),
) -> dict[str, Any]:
    problems = list(extra_problems)
    if inspected["integrity_check"] != "ok":
        problems.append("PRAGMA integrity_check 未通过。")
    if inspected["foreign_key_violations"]:
        problems.append("PRAGMA foreign_key_check 发现外键违规。")
    if manifest is not None:
        if manifest.get("schema_version") != inspected["schema_version"]:
            problems.append("schema 版本与备份清单不一致。")
        for field in ("row_counts", "money_totals", "point_totals"):
            recorded = manifest.get(field) or {}
            for name, value in recorded.items():
                if inspected[field].get(name) != value:
                    problems.append(f"{field}.{name} 与备份清单不一致。")
    return {
        "ok": not problems,
        "integrity_check": inspected["integrity_check"],
        "foreign_key_violations": inspected["foreign_key_violations"],
        "schema_version": inspected["schema_version"],
        "row_counts": inspected["row_counts"],
        "money_totals": inspected["money_totals"],
        "point_totals": inspected["point_totals"],
        "problems": problems,
        "reconcile_required": RECONCILE_WARNING,
    }


def _plain_digest_in_backup(backup_dir: Path, name: str, key: bytes) -> str | None:
    """返回备份目录中某个文件的**明文**摘要。

    落盘形态是 ``<name>.enc``；兼容未加密的明文文件（例如人工放置的快照）。
    两者都不存在时返回 ``None``。
    """
    plain = backup_dir / name
    if plain.is_file():
        return checksum(plain)
    sealed = backup_dir / f"{name}.enc"
    if sealed.is_file():
        try:
            return hashlib.sha256(_unseal_bytes(sealed, key=key)).hexdigest()
        except Exception:  # noqa: BLE001 - 解密失败按摘要不一致处理
            return None
    return None


def verify_backup(path: str | Path, *, settings: Settings | None = None) -> dict[str, Any]:
    """校验备份目录（或裸快照文件）并输出报告，不修改任何正式数据。"""
    active = settings or get_settings()
    key = _backup_key(active)
    target = Path(path)
    if not target.exists():
        raise AppError("RESOURCE_NOT_FOUND", "备份路径不存在。")

    if target.is_file():
        # 裸快照（例如从对象存储取回单个文件）：只做完整性与汇总校验。
        inspected = _inspect(target, key=None)
        return _report(inspected, manifest=None)

    manifest_path = target / MANIFEST_NAME
    if not manifest_path.is_file():
        raise AppError("INTERNAL_ERROR", "备份目录缺少 manifest.json。")
    problems: list[str] = []
    sealed = target / SEALED_NAME
    if not sealed.is_file():
        problems.append("备份缺少封口摘要（.sealed）。")
    else:
        recorded = json.loads(sealed.read_text(encoding="utf-8")).get("sha256")
        if recorded != checksum(manifest_path):
            problems.append("备份清单摘要与封口记录不一致（备份被改写）。")
    manifest = _load_manifest(manifest_path)

    for name, meta in (manifest.get("files") or {}).items():
        digest = _plain_digest_in_backup(target, name, key)
        if digest is None:
            problems.append(f"备份缺少文件：{name}")
            continue
        if digest != meta.get("sha256"):
            problems.append(f"文件摘要不一致：{name}")

    expected_snapshot = (manifest.get("snapshot") or {}).get("plain_sha256")
    if expected_snapshot and _plain_digest_in_backup(target, "database.sqlite3", key) != expected_snapshot:
        problems.append("数据库快照摘要与备份清单不一致。")

    # 解密到备份目录内的临时文件（数据目录内，避免依赖系统临时目录的权限）。
    scratch = target / f".verify-{new_id('job')}"
    scratch.mkdir(parents=True, exist_ok=True)
    try:
        plain = _open_plain_snapshot(target, key, scratch)
        inspected = _inspect(plain, key=None)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return _report(inspected, manifest=manifest, extra_problems=problems)


def restore_to(
    path: str | Path,
    target_dir: str | Path,
    *,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """把备份恢复到**隔离目录**并输出校验报告；不碰当前运行中的数据库。

    返回的 ``database_path`` 指向恢复出来的明文快照，可直接通过
    ``DATABASE_URL=sqlite+pysqlite:///<database_path>`` 在隔离环境启动验证。
    """
    active = settings or get_settings()
    key = _backup_key(active)
    source = Path(path)
    destination = Path(target_dir)
    destination.mkdir(parents=True, exist_ok=True)

    backup_dir = source if source.is_dir() else source.parent
    manifest: dict[str, Any] | None = None
    if (backup_dir / MANIFEST_NAME).is_file():
        manifest = _load_manifest(backup_dir / MANIFEST_NAME)

    scratch = destination / "restored"
    scratch.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        plain = _open_plain_snapshot(source, key, scratch)
    else:
        plain = scratch / "database.sqlite3"
        shutil.copy2(source, plain)

    report = verify_backup(source, settings=active)
    report["restored_database"] = str(plain)
    report["restored_at"] = iso(now_ms())
    report["source"] = str(source)
    report["manifest_format"] = (manifest or {}).get("format")
    report["reconcile_steps"] = [
        "关闭新下单与改变账本的人工操作，保留回调接收或让渠道按协议重试。",
        "查询备份时间之后的全部渠道收款、退款与日账单，按 08 文档幂等补账。",
        "核查库存、名额与积分，处理恢复窗口内的差额。",
        "恢复对象引用与密钥版本，应用注销/删除墓碑记录。",
        "在隔离环境验证登录、订单、私密图片与 worker 后，才切回入口并逐步开单。",
    ]
    logger.info("备份已在隔离目录完成校验：%s（ok=%s）", destination, report["ok"])
    return report


__all__ = [
    "BACKUP_FORMAT",
    "RECONCILE_WARNING",
    "checksum",
    "create_backup",
    "restore_to",
    "verify_backup",
]
