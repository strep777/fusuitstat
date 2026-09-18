"""前后端契约漂移检查。

用途（04/06 文档「契约落地要求」）：前端调用的每个后台路径都必须存在于运行时
OpenAPI 中，避免再次出现「前端调用不存在的接口」——这类缺陷不会被类型检查发现，
因为 `api.get<T>(path)` 的 path 只是字符串。

用法（在仓库根目录执行）：
    python scripts/check_frontend_contract.py

退出码：0 = 全部匹配；1 = 存在前端调用但 OpenAPI 中不存在的路径（会逐条打印）。
需要先让 contracts/openapi.json 与后端一致：`cd server && uv run python -m app.cli export-openapi`。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = ROOT / "contracts" / "openapi.json"
WEB_SRC = ROOT / "web" / "src"
API_PREFIX = "/api/v1"

CALL = re.compile(r"api\.(get|post|patch|put|delete)<[^>]*>\(\s*[`\"]([^`\"]+)[`\"]", re.S)


def spec_operations() -> set[str]:
    spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    operations: set[str] = set()
    for path, methods in spec.get("paths", {}).items():
        for method in methods:
            if method.lower() in {"get", "post", "patch", "put", "delete"}:
                operations.add(f"{method.upper()} {path}")
    return operations


def normalize(raw: str) -> str:
    """把前端模板串归一成 OpenAPI 风格路径。"""
    out = re.sub(r"\$\{encodeURIComponent\(([^)]*)\)\}", r"{\1}", raw)
    out = re.sub(r"\$\{[^}]*\}", "{param}", out)
    if not out.startswith("/api/"):
        out = API_PREFIX + out
    return out


def shape(value: str) -> tuple[str, ...]:
    """路径形状：参数段统一成 ``{}`` 后比较，允许参数名不同。"""
    return tuple("{}" if part.startswith("{") else part for part in value.strip("/").split("/"))


def frontend_calls() -> list[tuple[str, str, str]]:
    calls: list[tuple[str, str, str]] = []
    for path in sorted(WEB_SRC.rglob("*.ts")):
        text = path.read_text(encoding="utf-8")
        for match in CALL.finditer(text):
            calls.append((str(path.relative_to(ROOT)), match.group(1).upper(), match.group(2)))
    return calls


def main() -> int:
    if not SPEC_PATH.is_file():
        print(f"缺少契约文件：{SPEC_PATH}", file=sys.stderr)
        return 1
    operations = spec_operations()
    shapes = {(item.split(" ", 1)[0], shape(item.split(" ", 1)[1])) for item in operations}

    missing: list[tuple[str, str, str]] = []
    calls = frontend_calls()
    for source, method, raw in calls:
        if not raw.startswith("/"):
            continue
        candidate = normalize(raw)
        if f"{method} {candidate}" in operations:
            continue
        if (method, shape(candidate)) in shapes:
            continue
        missing.append((source, method, raw))

    print(f"OpenAPI 操作数：{len(operations)}；前端调用点：{len(calls)}")
    if not missing:
        print("PASS：前端调用的后台路径全部存在于 OpenAPI 契约中。")
        return 0
    print(f"FAIL：{len(missing)} 条前端调用在 OpenAPI 中不存在：")
    for source, method, raw in missing:
        print(f"  {source}: {method} {raw}")
    print("修复方式：让前端改调已存在的路径，或在后端补上真实端点——不要删除断言绕过。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
