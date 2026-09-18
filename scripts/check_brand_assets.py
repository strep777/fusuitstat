"""品牌素材合规检查（11 文档 / AGENTS 约束 9）。

规则：
1. 加载图、积分图、Logo 必须通过 manifest 引用（``web/src/shared/lib/brand-assets.ts``），
   组件里不得出现硬编码的 ``/assets/brand/...`` 路径；
2. 不得用 emoji 或随意生成的图片冒充品牌素材；
3. manifest 里的 ``source`` 原图必须存在且 SHA256 与 ``文档/_调研证据/assets.json`` 一致
   （原图不修改、不移动）；
4. manifest 里声明的运行时派生图必须都已生成。

用法（仓库根目录）：``python scripts/check_brand_assets.py``
退出码 0 = 合规。
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_TS = ROOT / "web" / "src" / "shared" / "lib" / "brand-assets.ts"
WEB_SRC = ROOT / "web" / "src"
PUBLIC_BRAND = ROOT / "web" / "public" / "assets" / "brand"
EVIDENCE = ROOT / "文档" / "_调研证据" / "assets.json"

#: 允许出现品牌运行时路径的文件（集中映射本身与其测试）。
PATH_ALLOWED = {"brand-assets.ts"}

#: 品牌三件套对应的原图；缺失即视为未按 C26 使用指定素材。
REQUIRED_SOURCES = ("素材/加载/", "素材/积分点/", "素材/logo/")

HARDCODED_PATH = re.compile(r"/assets/brand/")
#: 可能被当作图标的 emoji（品牌标识不允许用 emoji 代替）。
EMOJI = re.compile(
    "[\U0001f300-\U0001faff\U00002600-\U000027bf\U0001f000-\U0001f2ff]"
)
EMOJI_ALLOWED_FILES = {"brand-assets.ts"}

#: 排版符号白名单：关闭、菜单、主题切换等纯字形控件不属于「用 emoji 代替品牌素材」，
#: 它们没有品牌含义，也不冒充加载图/积分图/Logo。其余 emoji 一律视为违规。
TYPOGRAPHIC_GLYPHS = set("✕✖×☰☀☾✓✔●○★☆⚠ⓘ→←↑↓•…–—·")


def _manifest_entries() -> list[dict[str, object]]:
    """从 TS 文件里抽出 manifest 字段（不引入 JS 运行时）。"""
    text = MANIFEST_TS.read_text(encoding="utf-8")
    entries: list[dict[str, object]] = []
    for match in re.finditer(r"\bkey: \"(\w+)\"", text):
        block_start = text.rfind("{", 0, match.start())
        block_end = text.find("}", match.end())
        block = text[block_start : block_end if block_end > 0 else len(text)]
        entry: dict[str, object] = {"key": match.group(1)}
        for field in ("source", "path", "sha256"):
            found = re.search(rf'{field}: "([^"]+)"', block)
            if found:
                entry[field] = found.group(1)
        entries.append(entry)
    return entries


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    issues: list[str] = []

    if not MANIFEST_TS.is_file():
        print(f"FAIL：缺少品牌 manifest {MANIFEST_TS}", file=sys.stderr)
        return 1

    entries = _manifest_entries()
    if len(entries) < 3:
        issues.append(f"manifest 条目过少（{len(entries)}），至少需要加载图、积分图与 Logo")

    evidence: dict[str, str] = {}
    if EVIDENCE.is_file():
        payload = json.loads(EVIDENCE.read_text(encoding="utf-8"))
        rows = payload if isinstance(payload, list) else payload.get("assets", [])
        for row in rows:
            if isinstance(row, dict) and "path" in row and "sha256" in row:
                evidence[str(row["path"]).replace("\\", "/")] = str(row["sha256"])

    sources_seen: list[str] = []
    for entry in entries:
        key = entry.get("key")
        source = str(entry.get("source", ""))
        runtime = str(entry.get("path", ""))
        digest = str(entry.get("sha256", ""))
        sources_seen.append(source)

        original = ROOT / source
        if not original.is_file():
            issues.append(f"{key}: 原图不存在 {source}")
        elif digest and _sha256(original) != digest:
            issues.append(f"{key}: 原图 SHA256 与 manifest 不一致（原图被改动或路径漂移）")
        elif evidence:
            expected = evidence.get(source)
            if expected and expected != digest:
                issues.append(f"{key}: manifest SHA256 与调研证据 assets.json 不一致")

        derivative = PUBLIC_BRAND / Path(runtime).name
        if not derivative.is_file():
            issues.append(f"{key}: 派生图未生成 {runtime}（先跑 pnpm assets:derive）")

    joined = "\n".join(sources_seen)
    for prefix in REQUIRED_SOURCES:
        if prefix not in joined:
            issues.append(f"manifest 未引用指定素材目录 {prefix}（C26 三件套）")

    for path in sorted(WEB_SRC.rglob("*")):
        if path.suffix not in {".ts", ".vue"} or not path.is_file():
            continue
        if path.name in PATH_ALLOWED:
            continue
        text = path.read_text(encoding="utf-8")
        if HARDCODED_PATH.search(text):
            issues.append(
                f"{path.relative_to(ROOT)}: 硬编码品牌素材路径，必须改用 brand-assets.ts manifest"
            )
        if path.name not in EMOJI_ALLOWED_FILES:
            offenders = {char for char in EMOJI.findall(text)} - TYPOGRAPHIC_GLYPHS
            if offenders:
                rendered = " ".join(sorted(offenders))
                issues.append(
                    f"{path.relative_to(ROOT)}: 出现 emoji（{rendered}），"
                    "不得用 emoji 代替品牌素材或图标"
                )

    print(f"品牌 manifest 条目：{len(entries)}；派生图目录：{PUBLIC_BRAND.relative_to(ROOT)}")
    if issues:
        print(f"FAIL：{len(issues)} 个问题")
        for issue in issues:
            print("  - " + issue)
        return 1
    print("PASS：品牌素材全部经 manifest 引用，原图摘要一致，派生图齐备，无 emoji 替代。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
