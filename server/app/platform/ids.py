"""统一 ID 与时间工具。

持久格式：UTC 毫秒 INTEGER（见 05 文档“通用约定”）。API 统一输出带 ``Z`` 的 ISO 8601。
"""

from __future__ import annotations

import secrets
import time
import uuid
from datetime import UTC as UTC
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")

# 公开随机 ID 前缀，便于日志与排障时辨认对象类型，不含任何个人信息。
ID_PREFIXES = {
    "user": "usr",
    "session": "ses",
    "challenge": "smc",
    "address": "adr",
    "asset": "ast",
    "content": "cnt",
    "product": "prd",
    "sku": "sku",
    "character": "chr",
    "cart": "crt",
    "order": "ord",
    "installment": "ist",
    "attempt": "pay",
    "allocation": "alc",
    "refund": "rfd",
    "aftersale": "afs",
    "shipment": "shp",
    "invoice": "inv",
    "inquiry": "inq",
    "quote": "qte",
    "round": "rnd",
    "application": "apl",
    "slot": "slt",
    "project": "prj",
    "stage": "stg",
    "change": "chg",
    "measurement": "msr",
    "feedback": "fbk",
    "review": "fbr",
    "award": "paw",
    "ledger": "ple",
    "adjustment": "pad",
    "notification": "ntf",
    "delivery": "dlv",
    "event": "evt",
    "reconciliation": "rec",
    "staff": "stf",
    "audit": "aud",
    "coupon": "cpn",
    "job": "job",
}


def new_id(kind: str) -> str:
    """生成带前缀的公开随机 ID（URL 中可直接使用，不含敏感信息）。"""
    prefix = ID_PREFIXES.get(kind, kind[:3])
    return f"{prefix}_{uuid.uuid4().hex}"


def now_ms() -> int:
    """当前 UTC 毫秒。"""
    return int(time.time() * 1000)


def to_ms(value: datetime) -> int:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return int(value.timestamp() * 1000)


def from_ms(value: int | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value / 1000, tz=UTC)


def iso(value: int | datetime | None) -> str | None:
    """统一输出带 ``Z`` 的 ISO 8601。"""
    if value is None:
        return None
    if isinstance(value, int):
        moment = from_ms(value)
    else:
        moment = value if value.tzinfo else value.replace(tzinfo=UTC)
    assert moment is not None
    return moment.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def add_days_ms(days: int, base_ms: int | None = None) -> int:
    base = base_ms if base_ms is not None else now_ms()
    return base + days * 86_400_000


def add_minutes_ms(minutes: int, base_ms: int | None = None) -> int:
    base = base_ms if base_ms is not None else now_ms()
    return base + minutes * 60_000


def shanghai_day_bounds_ms(moment_ms: int) -> tuple[int, int]:
    """返回该时刻所属 Asia/Shanghai 自然日的 UTC 毫秒边界 ``[start, end)``。"""
    local = datetime.fromtimestamp(moment_ms / 1000, tz=SHANGHAI)
    start_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    return int(start_local.timestamp() * 1000), int(end_local.timestamp() * 1000)


def shanghai_month_bounds_ms(moment_ms: int) -> tuple[int, int]:
    """返回该时刻所属 Asia/Shanghai 自然月的 UTC 毫秒边界 ``[start, end)``。"""
    local = datetime.fromtimestamp(moment_ms / 1000, tz=SHANGHAI)
    start_local = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start_local.month == 12:
        end_local = start_local.replace(year=start_local.year + 1, month=1)
    else:
        end_local = start_local.replace(month=start_local.month + 1)
    return int(start_local.timestamp() * 1000), int(end_local.timestamp() * 1000)


def random_token(nbytes: int = 32) -> str:
    """密码学安全随机不透明令牌（URL 安全）。"""
    return secrets.token_urlsafe(nbytes)


def random_numeric_code(digits: int = 6) -> str:
    """密码学安全随机数字验证码，保留前导零。"""
    return "".join(str(secrets.randbelow(10)) for _ in range(digits))
