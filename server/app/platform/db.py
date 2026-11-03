"""SQLite 引擎、会话与 UnitOfWork。

商用边界（04 文档）：
- 每个连接启用 ``foreign_keys=ON``、``busy_timeout=5000``；
- 数据库启用 WAL；金融数据以 ``synchronous=FULL`` 为初始耐久性设置；
- 一个 API worker 起步，事务尽量短，金额/库存争用使用 ``BEGIN IMMEDIATE``；
- 不使用 ``SELECT FOR UPDATE``（SQLite 不支持）。
"""

from __future__ import annotations

import contextlib
import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, Literal

import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.platform.config import Settings, get_settings
from app.platform.errors import AppError
from app.platform.orm import Base

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def _configure_sqlite_connection(
    dbapi_connection: Any, _record: Any, settings: Settings | None = None
) -> None:
    """连接级 PRAGMA。设置随引擎绑定，避免测试用临时库时读到全局配置。"""
    active = settings or get_settings()
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute(f"PRAGMA busy_timeout={active.sqlite_busy_timeout_ms}")
        cursor.execute(f"PRAGMA synchronous={active.sqlite_synchronous}")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()


def create_engine_for(settings: Settings) -> Engine:
    """按配置创建引擎。测试传入独立的临时文件路径即可隔离。"""
    url = settings.resolved_database_url
    connect_args: dict[str, Any] = {}
    if url.startswith("sqlite"):
        connect_args = {"check_same_thread": False, "timeout": settings.sqlite_busy_timeout_ms / 1000}
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        # 数据库文件的父目录也必须存在（测试常直接给临时目录下的路径）。
        Path(_sqlite_file_path(url)).parent.mkdir(parents=True, exist_ok=True)
    engine = sa.create_engine(
        url,
        echo=settings.db_echo,
        future=True,
        pool_pre_ping=True,
        connect_args=connect_args,
        # 文件库允许多读并发；写仍由 SQLite 串行化。内存库使用独立池，不能传 pool_size。
        **(
            {"pool_size": 20, "max_overflow": 10, "pool_timeout": 5}
            if url.startswith("sqlite") and "memory" not in url
            else {}
        ),
    )
    if url.startswith("sqlite"):
        event.listen(
            engine,
            "connect",
            lambda connection, record: _configure_sqlite_connection(connection, record, settings),
        )
    return engine


def _sqlite_file_path(url: str) -> str:
    """从 SQLAlchemy URL 中取出 SQLite 文件路径。"""
    marker = "sqlite+pysqlite:///"
    if url.startswith(marker):
        return url[len(marker) :]
    marker = "sqlite:///"
    if url.startswith(marker):
        return url[len(marker) :]
    return ""


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine_for(get_settings())
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    return _session_factory


def reset_engine() -> None:
    """测试辅助：释放单例引擎与会话工厂。"""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None


@contextlib.contextmanager
def session_scope() -> Iterator[Session]:
    """只读或自管理事务的会话。"""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


class UnitOfWork:
    """一个业务用例一个事务。

    ``immediate=True`` 时以 ``BEGIN IMMEDIATE`` 打开写事务，避免“先读后写”在并发下
    退化为 ``SQLITE_BUSY`` 或丢失更新（SQLite 无行级锁）。
    """

    def __init__(self, *, immediate: bool = False) -> None:
        self._immediate = immediate
        self.session: Session = get_session_factory()()
        self._committed = False

    def __enter__(self) -> UnitOfWork:
        if self._immediate:
            self.session.execute(sa.text("BEGIN IMMEDIATE"))
        return self

    def commit(self) -> None:
        self.session.commit()
        self._committed = True

    def rollback(self) -> None:
        self.session.rollback()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: Any,
    ) -> Literal[False]:
        try:
            if exc_type is None and not self._committed:
                self.session.commit()
                self._committed = True
            elif exc_type is not None:
                self.session.rollback()
        finally:
            self.session.close()
        return False


def affected_rows(result: object) -> int:
    """条件 ``UPDATE`` / ``DELETE`` 的影响行数。

    金额、库存与名额争用一律用「条件更新 + 影响行数」判定（04 文档），
    运行时 ``Session.execute`` 返回 ``CursorResult``（有 ``rowcount``），
    静态类型标注为 ``Result``。集中在此读取，避免逐处强制转换。
    """
    return int(getattr(result, "rowcount", 0) or 0)


def uow(immediate: bool = False) -> UnitOfWork:
    return UnitOfWork(immediate=immediate)


def init_database(engine: Engine | None = None) -> None:
    """测试与本地开发用：直接建表。生产使用 Alembic 迁移，禁用 create_all 代替。"""
    target = engine or get_engine()
    import app.modules  # noqa: F401  触发模型注册

    Base.metadata.create_all(target)


def integrity_report(engine: Engine | None = None) -> dict[str, Any]:
    """完整性检查（备份恢复与迁移校验使用）。"""
    target = engine or get_engine()
    with target.connect() as connection:
        integrity = connection.execute(sa.text("PRAGMA integrity_check")).scalar()
        foreign_keys = connection.execute(sa.text("PRAGMA foreign_key_check")).fetchall()
        journal = connection.execute(sa.text("PRAGMA journal_mode")).scalar()
    return {
        "integrity_check": integrity,
        "foreign_key_violations": len(foreign_keys),
        "journal_mode": journal,
        "ok": integrity == "ok" and not foreign_keys,
    }


def retry_on_busy(func: Callable[[], Any], *, attempts: int = 3, base_delay: float = 0.05) -> Any:
    """``SQLITE_BUSY`` 有限退避。仅用于整笔幂等业务，不用于已调用外部支付之后的重跑。"""
    import time

    last: Exception | None = None
    for index in range(attempts):
        try:
            return func()
        except sa.exc.OperationalError as exc:  # pragma: no cover - 依赖真实锁竞争
            if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                raise
            last = exc
            time.sleep(base_delay * (2**index))
    raise AppError("PROVIDER_UNAVAILABLE", "数据库繁忙，请稍后重试。") from last
