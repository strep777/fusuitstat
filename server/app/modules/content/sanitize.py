"""富文本清洗：服务端 HTML 白名单。

约束（10 文档）：富文本通过服务端 HTML 白名单清洗，Vue 禁止对用户输入直接
``v-html``。后台正文同样走本清洗器，保证预渲染产物不含脚本。
"""

from __future__ import annotations

import re
from html import escape

ALLOWED_TAGS = {
    "p", "br", "hr", "strong", "b", "em", "i", "u", "s", "del", "ins",
    "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li", "dl", "dt", "dd",
    "blockquote", "code", "pre",
    "a", "img", "figure", "figcaption",
    "table", "thead", "tbody", "tfoot", "tr", "th", "td", "caption",
    "div", "span", "small", "mark", "abbr", "time",
}
VOID_TAGS = {"br", "hr", "img"}
ALLOWED_ATTRS = {
    "a": {"href", "title", "rel", "target"},
    "img": {"src", "alt", "width", "height", "loading", "decoding"},
    "th": {"colspan", "rowspan", "scope"},
    "td": {"colspan", "rowspan"},
    "time": {"datetime"},
    "div": {"class"},
    "span": {"class"},
    "p": {"class"},
}
SAFE_URL_SCHEMES = ("https://", "http://", "/", "mailto:")

_TAG_RE = re.compile(r"<\s*(/?)\s*([a-zA-Z][a-zA-Z0-9]*)((?:[^>\"']|\"[^\"]*\"|'[^']*')*)>")
_ATTR_RE = re.compile(r"([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s\"'>]+)")
_DANGEROUS_BLOCK_RE = re.compile(
    r"<\s*(script|style|iframe|object|embed|form|input|button|textarea|select|link|meta|base|svg|math)\b.*?"
    r"(?:/\s*>|<\s*/\s*\1\s*>)",
    re.IGNORECASE | re.DOTALL,
)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def _safe_url(value: str) -> str | None:
    candidate = value.strip()
    lowered = candidate.lower().replace("\t", "").replace("\n", "")
    if lowered.startswith("javascript:") or lowered.startswith("data:") or lowered.startswith("vbscript:"):
        return None
    if lowered.startswith(SAFE_URL_SCHEMES):
        return candidate
    if lowered.startswith("#"):
        return candidate
    return None


def sanitize_html(raw: str) -> str:
    """移除危险标签与属性，输出可直接渲染的安全 HTML。"""
    if not raw:
        return ""
    text = _COMMENT_RE.sub("", raw)
    text = _DANGEROUS_BLOCK_RE.sub("", text)

    def replace(match: re.Match[str]) -> str:
        closing, tag = match.group(1), match.group(2).lower()
        attributes = match.group(3) or ""
        if tag not in ALLOWED_TAGS:
            return ""
        if closing:
            return f"</{tag}>"
        allowed = ALLOWED_ATTRS.get(tag, set())
        kept: list[str] = []
        for attr_match in _ATTR_RE.finditer(attributes):
            name = attr_match.group(1).lower()
            value = attr_match.group(2).strip("\"'")
            if name.startswith("on") or name not in allowed:
                continue
            if name in ("href", "src"):
                safe = _safe_url(value)
                if safe is None:
                    continue
                value = safe
            kept.append(f'{name}="{escape(value, quote=True)}"')
        suffix = "".join(f" {item}" for item in kept)
        if tag == "a" and "href" in attributes and "rel=" not in attributes:
            suffix += ' rel="noopener noreferrer"'
        if tag in VOID_TAGS:
            return f"<{tag}{suffix} />"
        return f"<{tag}{suffix}>"

    return _TAG_RE.sub(replace, text)


def html_to_text(raw: str) -> str:
    """纯文本摘要，用于搜索与通知。"""
    without_tags = re.sub(r"<[^>]+>", " ", raw or "")
    return re.sub(r"\s+", " ", without_tags).strip()


def excerpt(raw: str, limit: int = 160) -> str:
    text = html_to_text(raw)
    return text if len(text) <= limit else f"{text[: limit - 1]}…"
