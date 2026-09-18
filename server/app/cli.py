"""命令行入口：建库、迁移、种子数据、派生素材、一致性检查与任务运行。

生产使用 Alembic 迁移；``init-db`` 在开发环境可直接建表，但线上禁用
``create_all`` 代替迁移（05 文档）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.platform.config import get_settings
from app.platform.db import get_engine, get_session_factory, integrity_report, reset_engine
from app.platform.ids import iso, now_ms


def _session() -> Session:
    return get_session_factory()()


def cmd_init_db(args: argparse.Namespace) -> int:
    """建库 + 运行 Alembic 迁移 + 同步权限与默认设置。"""
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    print(f"数据目录：{settings.data_dir}")
    print(f"数据库：{settings.resolved_database_url}")

    if not args.skip_migrate:
        from alembic import command
        from alembic.config import Config

        server_dir = Path(__file__).resolve().parents[1]
        config = Config(str(server_dir / "alembic.ini"))
        config.set_main_option("script_location", str(server_dir / "migrations"))
        command.upgrade(config, "head")
        print("迁移已应用至 head")

    _seed_reference_data()
    report = integrity_report(get_engine())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


def _seed_reference_data() -> None:
    """同步权限、角色与默认站点设置。幂等执行。"""
    from app.modules.content.service import ensure_default_settings
    from app.modules.identity.permissions import sync_catalog

    with get_session_factory()() as session:
        result = sync_catalog(session)
        settings_created = ensure_default_settings(session)
        session.commit()
    print(f"权限/角色同步：{result}")
    print(f"默认站点设置新增：{settings_created}")


def cmd_create_owner(args: argparse.Namespace) -> int:
    """创建工作室所有者员工账号（无公开注册，只能命令行创建）。"""
    from app.modules.identity.admin_auth import create_staff

    password = args.password
    if not password:
        import secrets

        password = f"{secrets.token_urlsafe(9)}Aa1!"
        print("已生成一次性口令（请立即保存并在首次登录后修改）：")
        print(f"  {password}")
    with get_session_factory()() as session:
        staff = create_staff(
            session,
            actor_id=None,
            login_name=args.login,
            display_name=args.name,
            email=args.email,
            password=password,
            roles=["admin"],
            is_owner=True,
        )
        session.commit()
        print(f"已创建所有者账号：{staff.login_name}（{staff.id}）")
        print("首次登录需要绑定 TOTP：登录响应会返回 otpauth URI 与恢复码，只显示一次。")
    return 0


def cmd_derive_assets(args: argparse.Namespace) -> int:
    """派生品牌素材。先校验原图 SHA256，不符即停止。"""
    from app.assets.derive import derive_all

    files = derive_all()
    print(f"已生成 {len(files)} 个派生文件：")
    for item in files:
        print(f"  {item}")
    return 0


def cmd_integrity(args: argparse.Namespace) -> int:
    """一致性检查：SQLite 完整性、外键、金额与积分汇总。"""
    engine = get_engine()
    report = integrity_report(engine)
    with engine.connect() as connection:
        checks: dict[str, object] = {}
        tables = [
            "orders",
            "order_items",
            "installments",
            "payment_allocations",
            "refunds",
            "point_ledger",
            "point_accounts",
            "feedback_rewards" if False else "point_awards",
        ]
        for table in tables:
            try:
                count = connection.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar()
                checks[table] = int(count or 0)
            except sa.exc.OperationalError:
                checks[table] = "表不存在"
    report["row_counts"] = checks
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


def cmd_worker(args: argparse.Namespace) -> int:
    """运行任务 worker（outbox 消费、通知投递、积分入账、订单关闭）。"""
    from app.worker import run_worker

    run_worker(once=args.once, max_iterations=args.iterations)
    return 0


def cmd_seed_demo(args: argparse.Namespace) -> int:
    """写入演示内容（开发环境）。所有条目都标记为演示数据。"""
    from app.dev_seed import seed_demo

    with get_session_factory()() as session:
        summary = seed_demo(session, force=args.force)
        session.commit()
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def cmd_export_openapi(args: argparse.Namespace) -> int:
    """导出 OpenAPI 契约到 ``contracts/openapi.json`` 并生成前端类型提示。"""
    from app.main import create_app

    app = create_app()
    schema = app.openapi()
    target = Path(args.output).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths = schema.get("paths", {})
    print(f"已导出 {len(paths)} 条路径到 {target}")
    return 0


def cmd_check_config(args: argparse.Namespace) -> int:
    """检查当前配置能否在目标环境启动（生产不安全组合会 fail closed）。"""
    try:
        settings = get_settings()
    except Exception as exc:  # noqa: BLE001 - 配置错误需要完整展示
        print(f"配置不合法：{exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "app_env": settings.app_env,
                "public_origin": settings.public_origin,
                "database": settings.resolved_database_url,
                "sms_adapter": settings.sms_adapter,
                "payment_providers": settings.enabled_payment_providers,
                "storage_backend": settings.storage_backend,
                "feature_flags": {
                    "online_payment": settings.feature_online_payment,
                    "points_redemption": settings.feature_points_redemption,
                    "coupons": settings.feature_coupons,
                    "rush_rounds": settings.feature_rush_rounds,
                    "marketing_email": settings.feature_marketing_email,
                },
                "checked_at": iso(now_ms()),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def cmd_backup(args: argparse.Namespace) -> int:
    """生成一致快照（SQLite online backup API），不复制 WAL 中的 .db 文件。"""
    try:
        from app.ops.backup import create_backup
    except ImportError as exc:  # pragma: no cover - 运维模块未安装时给出明确提示
        print(f"运维备份模块不可用：{exc}", file=sys.stderr)
        return 2

    with get_engine().connect() as connection:
        path = create_backup(connection.connection.dbapi_connection, label=args.label)
    print(f"备份已生成：{path}")
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    """在隔离目录校验并恢复备份，输出行数与金额/积分汇总。"""
    try:
        from app.ops.backup import verify_backup
    except ImportError as exc:  # pragma: no cover
        print(f"运维备份模块不可用：{exc}", file=sys.stderr)
        return 2

    report = verify_backup(Path(args.path))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") else 1


def cmd_reset_runtime(args: argparse.Namespace) -> int:
    """开发辅助：释放引擎单例（用于脚本内切换数据库）。"""
    reset_engine()
    print("引擎已重置")
    _ = args
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="app.cli", description="弈星纪元官网后端命令行")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init-db", help="建库并应用迁移")
    p.add_argument("--skip-migrate", action="store_true", help="只建目录与种子数据")
    p.set_defaults(func=cmd_init_db)

    p = sub.add_parser("create-owner", help="创建所有者员工账号")
    p.add_argument("--login", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--email", default="")
    p.add_argument("--password", default="")
    p.set_defaults(func=cmd_create_owner)

    p = sub.add_parser("derive-assets", help="派生品牌素材")
    p.set_defaults(func=cmd_derive_assets)

    p = sub.add_parser("integrity", help="一致性检查")
    p.set_defaults(func=cmd_integrity)

    p = sub.add_parser("worker", help="运行任务 worker")
    p.add_argument("--once", action="store_true", help="只跑一轮")
    p.add_argument("--iterations", type=int, default=0, help="最多运行轮数，0 表示不限")
    p.set_defaults(func=cmd_worker)

    p = sub.add_parser("seed-demo", help="写入演示内容（开发环境）")
    p.add_argument("--force", action="store_true", help="覆盖已有演示条目")
    p.set_defaults(func=cmd_seed_demo)

    p = sub.add_parser("export-openapi", help="导出 OpenAPI 契约")
    p.add_argument("--output", default="../contracts/openapi.json")
    p.set_defaults(func=cmd_export_openapi)

    p = sub.add_parser("check-config", help="校验运行配置")
    p.set_defaults(func=cmd_check_config)

    p = sub.add_parser("backup", help="生成一致备份快照")
    p.add_argument("--label", default="manual")
    p.set_defaults(func=cmd_backup)

    p = sub.add_parser("restore", help="校验备份快照")
    p.add_argument("--path", required=True)
    p.set_defaults(func=cmd_restore)

    p = sub.add_parser("reset-runtime", help="重置引擎单例")
    p.set_defaults(func=cmd_reset_runtime)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
