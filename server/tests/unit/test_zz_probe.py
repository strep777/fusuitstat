"""临时最小测试：隔离 pytest 夹具链中的挂起点（不属于交付物）。"""

from __future__ import annotations

import faulthandler
import sqlite3


def test_probe_settings(settings) -> None:
    faulthandler.dump_traceback_later(20, exit=True)
    print(f"settings data_dir={settings.data_dir}", flush=True)
    connection = sqlite3.connect(str(settings.data_dir / "probe.db"))
    connection.execute("CREATE TABLE probe (x INTEGER)")
    connection.close()
    assert settings.app_env == "test"
