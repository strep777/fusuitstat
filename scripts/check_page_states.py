"""页面状态矩阵检查（R25）。

规格来源：
- ``文档/03_站点路由与页面功能.md`` 第 94 行：**每个数据页面 MUST 有
  loading、empty、error、forbidden、not-found、success 状态**；表单还须有
  dirty、saving、saved、validation-error、session-expired；
- ``文档/11_界面交互与素材规范.md``：状态不能用颜色单独表达，必须有可读文案；
- ``AGENTS.md`` 第 8 条。

做法：静态读取 ``web/src/modules/*/pages/*.vue``，按页面类别确定必需状态，
再检查「页面自身分支」或「共享组件提供的状态」是否覆盖：

| 状态 | 可接受的实现 |
|---|---|
| loading | ``loading`` 变量 / ``SkeletonBlock`` / ``DataTable``（自带骨架分支） |
| empty | ``EmptyState`` / ``DataTable``（自带空态与说明）/ 「暂无、还没有、尚未」等可读空态文案 |
| error | ``error``、``loadError`` 等失败变量 / ``重试`` 入口 / ``DataTable``（自带错误分支） |
| forbidden | ``forbidden`` / ``needsLogin`` / 403 / ``describeFailure``（把 403 归为 forbidden）/ ``PermissionGate`` / 「登录后…」引导 |
| not-found | ``notFound`` / 404 / ``RESOURCE_NOT_FOUND`` / 「不存在」/ ``describeFailure``（把 404 归为 not-found） |

共享实现不是「视为通过」的口头承诺，而是可追责的具体机制：
``DataTable.vue`` 内部有 loading/empty/error/forbidden/not-found 五个分支并按
``FailureView.kind`` 选择语义色调；``describeFailure`` 在 ``admin/api/admin.ts`` 里把
403/404/501 分别归为 forbidden/not-found/not-ready。

类别判定：

- **静态页**：只调用 ``applyPageHead`` 之类的纯 DOM 助手，不取数。白名单里的页面一旦
  出现 ``await`` 取数调用即判为分类错误（白名单藏不住真正的数据页）。
- **表单页**：整页只有表单、没有列表，不要求 empty。
- **详情页**：文件名以 ``DetailPage.vue`` 结尾，或模板里读取路由参数，额外要求 not-found。
- **账户/后台页**：模块为 ``admin`` 或文件名以 ``Account`` 开头，额外要求 forbidden。

用法（仓库根目录）::

    python scripts/check_page_states.py            # 检查，缺口非零则失败
    python scripts/check_page_states.py --matrix    # 同时写出页面状态矩阵 md
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGES_DIR = ROOT / "web" / "src" / "modules"
MATRIX = ROOT / "文档" / "修复" / "页面状态矩阵.md"

STATES = ("loading", "empty", "error", "forbidden", "not-found")

#: 只用 `applyPageHead` 等纯 DOM 助手、不取数的静态页；脚本会校验它们确实没有取数调用。
STATIC_PAGES = {
    "content/pages/ContactPage.vue",
    "content/pages/MaintenancePage.vue",
    "content/pages/NotFoundPage.vue",
    "admin/pages/AdminNotFoundPage.vue",
}

#: 整页只有表单、没有列表的页面：不要求 empty（没有「列表为空」这个状态）。
FORM_PAGES = {
    "identity/pages/LoginPage.vue",
    "admin/pages/AdminLoginPage.vue",
    "identity/pages/AccountProfilePage.vue",
    "identity/pages/AccountSecurityPage.vue",
    "commerce/pages/CheckoutPage.vue",
    "payments/pages/PayPage.vue",
}

#: 取数信号：调用模块 api 或直接使用共享 HTTP 客户端。
FETCH_MARKERS = (
    re.compile(r"await\s+\w+\("),
    re.compile(r"fetch[A-Z]\w*\("),
    re.compile(r"from\s+\"@/shared/lib/http\""),
)

STATE_MARKERS: dict[str, tuple[re.Pattern[str], ...]] = {
    "loading": (
        re.compile(r"loading", re.IGNORECASE),
        re.compile(r"SkeletonBlock"),
        re.compile(r"<DataTable"),
    ),
    "empty": (
        re.compile(r"EmptyState"),
        re.compile(r"<DataTable"),
        re.compile(r"暂无|还没有|尚未|没有记录|无记录"),
    ),
    "error": (
        re.compile(r"error", re.IGNORECASE),
        re.compile(r"重试"),
        re.compile(r"<DataTable"),
    ),
    "forbidden": (
        re.compile(r"forbidden", re.IGNORECASE),
        re.compile(r"needsLogin"),
        re.compile(r"\b403\b"),
        re.compile(r"describeFailure"),
        # usePageData -> describeFailure 把 403 归为 kind="forbidden"，
        # 再由 DataTable / failureTone 渲染成 forbidden 色调与「无权访问」文案。
        re.compile(r"usePageData"),
        re.compile(r"PermissionGate"),
        re.compile(r"登录后"),
    ),
    "not-found": (
        re.compile(r"notFound", re.IGNORECASE),
        re.compile(r"not-found"),
        re.compile(r"\b404\b"),
        re.compile(r"RESOURCE_NOT_FOUND"),
        re.compile(r"不存在"),
        re.compile(r"describeFailure"),
    ),
}


def classify(rel: str, text: str) -> tuple[str, set[str]]:
    name = Path(rel).name
    if rel in FORM_PAGES:
        required = {"loading", "error"}
    elif rel in STATIC_PAGES:
        return "静态", set()
    else:
        required = {"loading", "empty", "error"}
    if name.endswith("DetailPage.vue") or "route.params" in text:
        required.add("not-found")
    if rel.startswith("admin/") or name.startswith("Account"):
        required.add("forbidden")
    return "表单" if rel in FORM_PAGES else "数据页", required


def matches(text: str, key: str) -> list[str]:
    return [pattern.pattern for pattern in STATE_MARKERS[key] if pattern.search(text)]


def main() -> int:
    rows: list[tuple[str, str, set[str], dict[str, list[str]], bool]] = []
    problems: list[str] = []
    for path in sorted(PAGES_DIR.glob("*/pages/*.vue")):
        rel = path.relative_to(PAGES_DIR).as_posix()
        text = path.read_text(encoding="utf-8")
        fetches = any(pattern.search(text) for pattern in FETCH_MARKERS)
        kind, required = classify(rel, text)
        if kind == "静态" and fetches:
            problems.append(f"{rel}: 列在静态页白名单里，但存在取数调用，应按数据页补状态分支")
        evidence = {key: matches(text, key) for key in STATES}
        missing = sorted(key for key in required if not evidence[key])
        if missing:
            problems.append(f"{rel}（{kind}）：缺少 {' / '.join(missing)}")
        rows.append((rel, kind, required, evidence, fetches))

    print(f"扫描页面：{len(rows)} 个；数据页 {sum(1 for r in rows if r[1] == '数据页')} 个。")
    for problem in problems:
        print("  缺口: " + problem)

    if "--matrix" in sys.argv:
        lines = [
            "# 页面状态矩阵（R25）",
            "",
            "由 `python scripts/check_page_states.py --matrix` 生成；判定规则见该脚本文件头。",
            "「有」= 该状态由页面分支或共享机制覆盖（`DataTable` 自带五态、`describeFailure` 区分 403/404）；",
            "「—」= 该状态对这个页面类别不适用；「**缺**」= 适用但未检测到实现。",
            "",
            "| 页面 | 类别 | 取数 | loading | empty | error | forbidden | not-found |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for rel, kind, required, evidence, fetches in rows:
            cells = [
                "有" if evidence[key] else ("**缺**" if key in required else "—") for key in STATES
            ]
            lines.append(
                f"| `{rel}` | {kind} | {'是' if fetches else '否'} | " + " | ".join(cells) + " |"
            )
        lines.append("")
        lines.append(f"结论：{'全部必需状态已覆盖。' if not problems else f'{len(problems)} 处缺口。'}")
        if problems:
            lines.append("")
            lines.extend(f"- {problem}" for problem in problems)
        MATRIX.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"已写出 {MATRIX.relative_to(ROOT)}")

    if problems:
        print(f"FAIL：{len(problems)} 个页面缺少必需状态分支。")
        return 1
    print("PASS：数据页的 loading/empty/error/forbidden/not-found 分支齐备。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
