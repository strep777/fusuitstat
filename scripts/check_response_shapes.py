"""扫描「后端返回裸数组 / 前端按分页对象解析」这类响应形状漂移。

`check_frontend_contract.py` 只比对路径，不比形状；本脚本补上最容易出错的一类：
后端 `response_model=list[...]` 的端点，前端是否仍按 `{items: ...}` 解析。

用法（仓库根目录）：``python scripts/check_response_shapes.py``
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "server" / "app"
WEB = ROOT / "web" / "src"
SPEC = ROOT / "contracts" / "openapi.json"


def list_response_routes() -> set[str]:
    """运行时契约里「成功响应是数组」的路径集合。"""
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    array_routes: set[str] = set()
    for path, methods in spec.get("paths", {}).items():
        for method, operation in methods.items():
            if method.lower() not in {"get", "post", "patch", "put"}:
                continue
            schema = (
                operation.get("responses", {})
                .get("200", operation.get("responses", {}).get("201", {}))
                .get("content", {})
                .get("application/json", {})
                .get("schema", {})
            )
            if schema.get("type") == "array":
                array_routes.add(f"{method.upper()} {path}")
    return array_routes


def decorator_response_models() -> dict[str, str]:
    """源码里 `response_model=list[...]` 的路径 → 返回类型字符串。"""
    found: dict[str, str] = {}
    for path in sorted(APP.rglob("router.py")):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(
            r"@\w+\.(get|post|patch|put|delete)\(\s*\"([^\"]+)\"[^)]*response_model=([^,\)]+)",
            text,
            re.S,
        ):
            method, route, model = match.group(1), match.group(2), match.group(3).strip()
            if model.startswith("list["):
                found[f"{method.upper()} {route}"] = model
    return found


CALL = re.compile(r"api\.(get|post|patch|put)<([^>]*)>\(\s*[`\"]([^`\"]+)[`\"]", re.S)
ITEMS_USE = re.compile(r"\.items\b")


def frontend_page_usage() -> list[tuple[str, str, str, str]]:
    """前端把某个端点按分页对象用（`<Page<...>>` 或 `.items`）的调用点。"""
    hits: list[tuple[str, str, str, str]] = []
    for path in sorted(WEB.rglob("*.ts")) + sorted(WEB.rglob("*.vue")):
        text = path.read_text(encoding="utf-8")
        for match in CALL.finditer(text):
            method, generic, raw = match.group(1).upper(), match.group(2), match.group(3)
            page_typed = "Page<" in generic or "AdminList<" in generic
            hits.append((str(path.relative_to(ROOT)), method, raw, "page" if page_typed else "other"))
    return hits


def main() -> int:
    array_routes = list_response_routes()
    declared = decorator_response_models()
    print(f"契约中成功响应为数组的端点：{len(array_routes)}；源码声明：{len(declared)}")

    problems: list[str] = []
    calls = frontend_page_usage()

    def normalize(raw: str) -> str:
        out = re.sub(r"\$\{encodeURIComponent\(([^)]*)\)\}", r"{\1}", raw)
        out = re.sub(r"\$\{[^}]*\}", "{param}", out)
        if not out.startswith("/api/"):
            out = "/api/v1" + out
        return out

    def shape(value: str) -> tuple[str, ...]:
        return tuple("{}" if part.startswith("{") else part for part in value.strip("/").split("/"))

    array_shapes = {(item.split(" ", 1)[0], shape(item.split(" ", 1)[1])) for item in array_routes}

    for source, method, raw, kind in calls:
        if kind != "page":
            continue
        candidate = normalize(raw)
        if (method, shape(candidate)) in array_shapes:
            problems.append(
                f"{source}: {method} {raw} 按分页对象解析，但服务端返回数组（形状漂移会让 .items 变 undefined）"
            )

    if not problems:
        print("PASS：未发现「服务端返回数组、前端按分页对象解析」的漂移。")
        return 0
    print(f"FAIL：{len(problems)} 处响应形状漂移：")
    for item in problems:
        print("  - " + item)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
