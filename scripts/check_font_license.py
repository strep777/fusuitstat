"""字体与远程资源许可门禁。

规则（文档 18）：站点只分发 MIT 字体；不加载第三方远程字体。
退出码 0 = PASS，1 = 发现违规。
"""

from __future__ import annotations

from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
FONT_DIR = ROOT / "web" / "public" / "fonts" / "hack"
ALLOWED = {"Hack-Regular.ttf", "Hack-Bold.ttf", "LICENSE.md", "SOURCE.txt"}
REMOTE = re.compile(r"fonts\.(googleapis|gstatic)\.com|@import\s+url\(\s*['\"]?https?://", re.I)


def main() -> int:
    problems: list[str] = []
    if not FONT_DIR.is_dir():
        problems.append("缺少 web/public/fonts/hack 目录")
    else:
        for item in FONT_DIR.iterdir():
            if item.name not in ALLOWED:
                problems.append(f"未登记的字体文件：{item.name}")
        license_text = (FONT_DIR / "LICENSE.md").read_text(encoding="utf-8", errors="ignore")
        if "MIT License" not in license_text:
            problems.append("Hack LICENSE.md 缺少 MIT License 文本")

    source_roots = [ROOT / "web" / "src", ROOT / "web" / "index.html"]
    for target in source_roots:
        files = [target] if target.is_file() else list(target.rglob("*"))
        for file in files:
            if not file.is_file() or file.suffix.lower() not in {".css", ".vue", ".ts", ".html"}:
                continue
            text = file.read_text(encoding="utf-8", errors="ignore")
            if REMOTE.search(text):
                problems.append(f"发现远程字体引用：{file.relative_to(ROOT)}")

    if problems:
        print("FONT-LICENSE FAIL")
        for problem in problems:
            print(f" - {problem}")
        return 1
    print("FONT-LICENSE PASS：仅分发 MIT Hack，无远程字体引用")
    return 0


if __name__ == "__main__":
    sys.exit(main())
