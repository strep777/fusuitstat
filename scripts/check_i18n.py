#!/usr/bin/env python
"""多语言词条门禁（C02/C24、02 文档 FR-022）。

检查项（任一项不通过即失败）：

1. **键集一致**：`en-US` 与 `zh-CN` 的键路径集合完全相同（多一个或少一个都失败）；
2. **无空词条**：两种语言的任何词条都不能是空串或只有空白；
3. **无占位符漂移**：同一键在两种语言里的 `{name}` 占位符集合必须一致——少了会漏参数，
   多了会留下未替换的花括号；
4. **无未定义键**：源码里 `t("...")` 使用的键必须真实存在于词条树（写错键在运行期只会
   回退成中文原文，静态检查才能拦住）；
5. **英文词条不含中日韩字符**：除显式允许的专有名词（品牌/法定主体/尚未定英文名的术语）外，
   英文词条里出现 CJK 视为漏译。

另外输出**覆盖率报告**（不参与判定）：还有多少界面文件包含中文字面量，用于跟踪双语进度。

用法：
    python scripts/check_i18n.py                 # 只判定
    python scripts/check_i18n.py --matrix        # 额外写出 文档/修复/双语覆盖矩阵.md
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB_SRC = ROOT / "web" / "src"
CATALOG_DIR = WEB_SRC / "shared" / "i18n"
MATRIX_PATH = ROOT / "文档" / "修复" / "双语覆盖矩阵.md"

CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
PLACEHOLDER = re.compile(r"\{(\w+)\}")

#: 注释与文档字符串里的中文不算「界面文案」，统计前先剥离，否则覆盖率数字没有意义。
COMMENT_PATTERNS = (
    re.compile(r"<!--.*?-->", re.DOTALL),
    re.compile(r"/\*.*?\*/", re.DOTALL),
    re.compile(r"^\s*//.*$", re.MULTILINE),
)


def strip_comments(text: str) -> str:
    for pattern in COMMENT_PATTERNS:
        text = pattern.sub("", text)
    return text

#: 允许在英文词条里保留中文的键前缀——专有名词与尚未定英文名的术语。
#: 每一条都必须有理由，不能为了「让门禁通过」而放宽。
ALLOWED_CJK_KEYS: dict[str, str] = {
    "common.languageZh": "语言切换项显示中文名称，属原文自称",
    "footer.points": "「星绒币」的英文名尚未确认（N02/N04 一并待定），不新造译名",
    "footer.legalEntity": "经营主体的法定登记名，未经核准不译成英文",
}

#: 词条目录之外仍然包含中文字面量的文件里，允许豁免的路径前缀（及理由）。
ALLOWED_CHINESE_FILES: dict[str, str] = {}

#: 服务端下发的「按语言给出的」运营文案字段。它们在模板里**必须**先解析成一句话，
#: 否则英文站会渲染出 `[object Object]`（或把中文原文当成英文）。
LOCALIZED_FIELDS = (
    "duty_note",
    "policy_note",
    "ratio_note",
    "redemption_ratio_note",
    "tagline",
    "statement",
    "holiday_note",
)

#: 已经由 `useSiteStore()` 统一解析过的访问前缀：模板直接取值是安全的。
RESOLVED_PREFIXES = ("site.brand.", "site.commercePolicy.")

INTERPOLATION = re.compile(r"\{\{(.*?)\}\}", re.DOTALL)
BARE_FIELD = re.compile(
    r"^\s*[A-Za-z_$][\w$]*(?:\??\.[A-Za-z_$][\w$]*|\[[^\]]+\])*\s*$"
)


def check_localized_rendering() -> list[str]:
    """模板里不得直接渲染「按语言给出的」字段（必须先经 `resolveText()` 或解析后的计算属性）。"""
    problems: list[str] = []
    for path in sorted(WEB_SRC.glob("**/*.vue")):
        text = path.read_text(encoding="utf-8")
        relative = str(path.relative_to(ROOT))
        for match in INTERPOLATION.finditer(text):
            expression = match.group(1).strip()
            if "resolveText(" in expression:
                continue
            if any(prefix in expression for prefix in RESOLVED_PREFIXES):
                continue
            for field in LOCALIZED_FIELDS:
                if not re.search(rf"(?<![\w]){re.escape(field)}\b", expression):
                    continue
                # 只拦「整段就是这个字段路径」的裸渲染；带 `??`/`||` 等回退的表达式由人工复核。
                if BARE_FIELD.match(expression):
                    problems.append(
                        f"模板直接渲染多语言字段：{relative} → {{{{{expression}}}}}"
                        f"（应经 resolveText() 或站点 store 解析）"
                    )
    return problems


def load_catalog(path: Path) -> dict[str, str]:
    """用 Node 读出扁平化的键 → 文案（复用前端 TypeScript 源，避免第二份词条）。

    词条文件是纯数据 + 可擦除类型（`as const` / `satisfies` / `import type`），
    因此 Node 的类型剥离即可直接 import，不需要打包器，也不会产生第二份词条。
    """
    script = """
const { pathToFileURL } = await import("node:url");
const mod = await import(pathToFileURL(process.env.DSH_CATALOG_PATH).href);
const tree = mod.zhCN ?? mod.enUS;
const out = {};
(function walk(node, prefix) {
  for (const [key, value] of Object.entries(node)) {
    const next = prefix ? `${prefix}.${key}` : key;
    if (typeof value === "string") out[next] = value;
    else walk(value, next);
  }
})(tree, "");
process.stdout.write(JSON.stringify(out));
"""
    env = {**os.environ, "DSH_CATALOG_PATH": str(path)}
    result = subprocess.run(
        ["node", "--experimental-strip-types", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(ROOT / "web"),
        env=env,
    )
    if result.returncode != 0:
        raise SystemExit(f"读取词条失败：{path}\n{result.stderr.strip()}")
    return json.loads(result.stdout)


def iter_source_files() -> Iterator[Path]:
    for pattern in ("**/*.vue", "**/*.ts"):
        for path in sorted(WEB_SRC.glob(pattern)):
            if CATALOG_DIR in path.parents:
                continue
            yield path


def used_keys() -> dict[str, list[str]]:
    """源码里 `t("key")` / `translate("key")` 的用法 → 使用它的文件。"""
    pattern = re.compile(r"\b(?:t|translate)\(\s*[\"']([a-zA-Z0-9_.]+)[\"']")
    found: dict[str, list[str]] = {}
    for path in iter_source_files():
        text = strip_comments(path.read_text(encoding="utf-8"))
        for key in pattern.findall(text):
            found.setdefault(key, []).append(str(path.relative_to(ROOT)))
    return found


def check_catalogs(zh: dict[str, str], en: dict[str, str]) -> list[str]:
    problems: list[str] = []
    zh_keys, en_keys = set(zh), set(en)
    for key in sorted(zh_keys - en_keys):
        problems.append(f"英文词条缺少键：{key}")
    for key in sorted(en_keys - zh_keys):
        problems.append(f"英文词条多出键（中文里不存在）：{key}")
    for key in sorted(zh_keys & en_keys):
        if not zh[key].strip():
            problems.append(f"中文词条为空：{key}")
        if not en[key].strip():
            problems.append(f"英文词条为空：{key}")
        zh_params = set(PLACEHOLDER.findall(zh[key]))
        en_params = set(PLACEHOLDER.findall(en[key]))
        if zh_params != en_params:
            problems.append(
                f"占位符不一致：{key}（中文 {sorted(zh_params)} / 英文 {sorted(en_params)}）"
            )
        if CJK.search(en[key]) and key not in ALLOWED_CJK_KEYS:
            problems.append(f"英文词条仍含中文（疑似漏译）：{key} → {en[key][:40]}")
    return problems


def check_usage(zh: dict[str, str]) -> list[str]:
    problems: list[str] = []
    for key, files in sorted(used_keys().items()):
        if key not in zh:
            problems.append(f"源码使用了不存在的词条键：{key}（{files[0]}）")
    return problems


def keys_by_prefix(zh: dict[str, str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for key in zh:
        root = key.split(".")[0]
        counts[root] = counts.get(root, 0) + 1
    return counts

def coverage_rows(zh: dict[str, str]) -> list[tuple[str, int, int]]:
    """按模块统计「含中文字面量的文件 / 全部文件」。"""
    modules: dict[str, list[Path]] = {}
    for path in iter_source_files():
        relative = path.relative_to(WEB_SRC)
        module = "app" if relative.parts[0] == "app" else "/".join(relative.parts[:3])
        modules.setdefault(module, []).append(path)

    rows: list[tuple[str, int, int]] = []
    for module, paths in sorted(modules.items()):
        with_cjk = 0
        for path in paths:
            if path.name.startswith("i18n"):
                continue
            if CJK.search(strip_comments(path.read_text(encoding="utf-8"))):
                with_cjk += 1
        rows.append((module, with_cjk, len(paths)))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="多语言词条门禁")
    parser.add_argument("--matrix", action="store_true", help="写出双语覆盖矩阵")
    args = parser.parse_args()

    zh = load_catalog(CATALOG_DIR / "zh-CN.ts")
    en = load_catalog(CATALOG_DIR / "en-US.ts")

    problems = check_catalogs(zh, en) + check_usage(zh) + check_localized_rendering()

    print(f"词条数：zh-CN {len(zh)}；en-US {len(en)}")
    rows = coverage_rows(zh)
    prefix_counts = keys_by_prefix(zh)
    covered = sum(row[1] for row in rows)
    total = sum(row[2] for row in rows)
    print(f"界面文件（.vue/.ts）：{total}；仍含中文字面量：{covered}（双语进行中）")

    if args.matrix:
        lines = [
            "# 双语覆盖矩阵（C02/C24）",
            "",
            "本文件由 `python scripts/check_i18n.py --matrix` 生成，**只统计，不判定**。",
            "判定项（键集一致、无空词条、占位符一致、键存在、英文不含中文）在同一脚本里以退出码表示。",
            "统计时已剥离注释：注释里的中文不算界面文案。",
            "",
            f"- 词条数：zh-CN {len(zh)} / en-US {len(en)}（分组："
            + "、".join(f"`{name}` {count}" for name, count in sorted(prefix_counts.items()))
            + "）",
            f"- 界面文件总数：{total}；仍含中文字面量：{covered}",
            "",
            "| 模块 | 仍含中文的文件 | 文件总数 |",
            "|---|---|---|",
        ]
        for module, with_cjk, count in rows:
            lines.append(f"| `{module}` | {with_cjk} | {count} |")
        lines += [
            "",
            "> 说明：仍含中文字面量**不等于**未翻译（有些文案来自服务端配置），但它是英文站",
            "> 上线的必要条件；未接入词条的界面在英文站会保持中文，因此 `en_locale` 功能开关",
            "> 默认关闭——未上线的语言不输出假入口（02 文档 FR-022、C24）。",
            "> 已接入词条的界面：站点外壳（`SiteHeader` / `SiteFooter` / `SiteLayout`）。",
            "",
        ]
        MATRIX_PATH.parent.mkdir(parents=True, exist_ok=True)
        MATRIX_PATH.write_text("\n".join(lines), encoding="utf-8")
        print(f"已写出 {MATRIX_PATH.relative_to(ROOT)}")

    if problems:
        print("\n多语言门禁未通过：")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print("PASS：词条键集一致、无空词条、占位符一致、源码未引用未定义键、英文词条无漏译、模板未裸渲染多语言字段。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
