"""模块边界检查（04 文档：模块只拥有自己的数据）。

规则：
1. 业务模块之间**不得**直接导入彼此的 ``models`` / ``repository`` / ``service``；
   跨模块只能通过对方的 ``ports.py``，跨模块 DTO 只能来自 ``app/modules/contracts.py``；
2. ``app/application/workflows`` 是唯一允许编排多个模块的位置；
3. ``app/api``、``app/platform``、``app/adapters`` 作为组合根可以装配模块，
   但仍应优先使用 ports。

本脚本只做静态检查并列出违例。

已知遗留：仓库里仍有一批历史跨模块导入（审计 AUDIT-20260918 与
``文档/修复/项目修复任务清单.md`` 的 R11 记录了整改范围）。为避免在整改完成前
让 CI 永久红灯，脚本支持基线文件 ``scripts/module_boundary_baseline.txt``：
基线内的违例只提示，**新增**违例才会失败。整改完成后请删除对应基线行。

用法（仓库根目录）：
    python scripts/check_module_boundaries.py          # 与基线比较
    python scripts/check_module_boundaries.py --list    # 只列出全部违例
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "server" / "app"
BASELINE = ROOT / "scripts" / "module_boundary_baseline.txt"

#: 允许跨模块导入的位置（组合根与编排层）。
ORCHESTRATORS = ("application",)
#: 组合根：装配路由与适配器，允许直接 import 模块。
COMPOSITION_ROOTS = ("api",)
#: 模块内部允许互相引用的白名单文件名。
ALLOWED_TARGETS = {"ports", "contracts", "__init__"}
#: 这些模块是通用底座，任何模块都可以直接引用（不含业务数据）。
SHARED_MODULES = {"shared", "contracts"}


def module_name(path: Path) -> str | None:
    """``app/modules/<name>/...`` → ``<name>``；其他返回 ``None``。"""
    parts = path.relative_to(APP).parts
    if len(parts) >= 2 and parts[0] == "modules":
        return parts[1]
    return None


def layer_of(path: Path) -> str:
    parts = path.relative_to(APP).parts
    return parts[0] if parts else ""


def imported_module_names(tree: ast.AST) -> list[tuple[str, int, list[str]]]:
    """返回 ``(被导入的 app 内模块路径, 行号, 导入的名称列表)``。"""
    found: list[tuple[str, int, list[str]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("app."):
            found.append((node.module, node.lineno, [alias.name for alias in node.names]))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("app."):
                    found.append((alias.name, node.lineno, [alias.name]))
    return found


def violations() -> list[str]:
    issues: list[str] = []
    for path in sorted(APP.rglob("*.py")):
        source_module = module_name(path)
        if source_module is None:
            continue  # 非业务模块（platform/adapters/api/application）
        parts = path.relative_to(APP).parts
        # app/modules/<name>/...
        if len(parts) < 3:
            continue
        own_file = path.stem
        if own_file in ALLOWED_TARGETS:
            continue  # __init__ 负责对外导出；ports 就是给外部用的
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for target, lineno, names in imported_module_names(tree):
            segments = target.split(".")
            if len(segments) < 3 or segments[1] != "modules":
                continue
            target_module = segments[2]
            tail = segments[3] if len(segments) > 3 else ""
            if target_module == source_module:
                continue
            if target_module in SHARED_MODULES:
                continue
            if tail in ALLOWED_TARGETS:
                continue
            # `from app.modules.X import ports`：导入的正是端口模块，属合规用法。
            if tail == "" and names and set(names) <= ALLOWED_TARGETS:
                continue
            # `from app.modules.X.router import public_router`：路由组装（挂载同级子路由）
            # 不读写对方数据，是 FastAPI 组合根的正常写法，不属于数据边界违例。
            if tail == "router" and all(name.endswith("router") for name in names):
                continue
            issues.append(
                f"{path.relative_to(ROOT)}:{lineno}: 跨模块导入 app.modules.{target_module}.{tail or '(顶层)'}"
                f"（{', '.join(names)}；应改为 {target_module}.ports）"
            )
    return issues


def _key(issue: str) -> str:
    """基线比较用：只保留「文件 + 被导入模块」，行号变化不算新增。"""
    location, _, detail = issue.partition(": 跨模块导入 ")
    path = location.split(":")[0]
    return f"{path} -> {detail.split('（')[0]}"


def load_baseline() -> set[str]:
    if not BASELINE.is_file():
        return set()
    lines = BASELINE.read_text(encoding="utf-8").splitlines()
    return {line.strip() for line in lines if line.strip() and not line.startswith("#")}


def main() -> int:
    issues = violations()
    if "--list" in sys.argv:
        for issue in issues:
            print("  " + issue)
        print(f"共 {len(issues)} 处")
        return 0
    if "--write-baseline" in sys.argv:
        header = [
            "# 跨模块直接导入的系统性欠账基线（R11）",
            "#",
            "# 每行是「文件 -> 被导入的跨模块目标」。这些是审计 AUDIT-20260918 记录的历史欠账，",
            "# 需要按模块逐个改造为 ports.py 调用；改造完成一条就删除对应行。",
            "# scripts/check_module_boundaries.py 只对「基线之外的新增违例」失败。",
            "",
        ]
        entries = sorted({_key(issue) for issue in issues})
        BASELINE.write_text("\n".join(header + entries) + "\n", encoding="utf-8")
        print(f"已写入基线 {len(entries)} 条：{BASELINE.relative_to(ROOT)}")
        return 0

    baseline = load_baseline()
    known = [item for item in issues if _key(item) in baseline]
    fresh = [item for item in issues if _key(item) not in baseline]

    print(f"检查范围：server/app/modules/**（排除 ports.py 与 __init__.py）")
    print(f"违例 {len(issues)} 处；基线内 {len(known)} 处（整改中），新增 {len(fresh)} 处。")
    for issue in fresh:
        print("  新增: " + issue)
    if fresh:
        print("FAIL：出现基线之外的跨模块直接导入，请改用对方的 ports.py。")
        return 1
    if known:
        print("PASS（含已知遗留）：未出现新的跨模块直接导入；R11 整改完成前请逐条消减基线。")
    else:
        print("PASS：未发现跨模块直接导入 models/repository/service。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
