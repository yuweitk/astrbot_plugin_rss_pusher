"""QQ 官方 Markdown 稳定子集渲染。

对应 qq-maid-bot 的 `qq-maid-common/src/markdown/qq.rs`。QQ 官方机器人的 Markdown
（`msg_type=2`）只稳定支持一部分语法，外部内容直接透传很容易出现「整条消息渲染
错乱」或链接被吞掉，因此这里把外部 Markdown 收敛到：

- 保留：标题（最大三级）、有序/无序列表、仅 http(s) 的内联链接、行内代码、围栏代码块；
- 降级：引用、加粗/斜体/删除线、表格（压平成 `a / b`）、HTML 标签、图片（只留 alt）；
- 纯文本行首的 `#`、`>`、`-` 等标记会被替换成全角字符，避免外部文本伪造结构。

插件自己拼装的推送模板不走这里，只有订阅源带来的标题/摘要需要清洗。
"""

from __future__ import annotations

import re
import unicodedata

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_UL_RE = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_OL_RE = re.compile(r"^(\s*)(\d{1,3})[.)]\s+(.*)$")
_HR_RE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?[\s:|-]+\|?\s*$")
_IMG_RE = re.compile(r"!\[([^\]]*)\]\(((?:[^()\s]|\([^()]*\))*)\)")
_LINK_RE = re.compile(r"\[([^\]]*)\]\(((?:[^()\s]|\([^()]*\))*)\)")
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC_RE = re.compile(r"\*([^*\n]+)\*")
_UNDERSCORE_RE = re.compile(r"(?<![\w_])_([^_\n]+)_(?![\w_])")
_STRIKE_RE = re.compile(r"~~([^~\n]+)~~")
# 注意不要把 Markdown 的 `(<https://...>)` 当成 HTML 标签吃掉
_TAG_RE = re.compile(r"</?(?!https?://)[A-Za-z][^<>]*>")
_HTTP_RE = re.compile(r"^https?://", re.I)

# 行首这些字符会被 QQ 解析成结构，外部文本出现在行首时换成全角
_FULLWIDTH_MAP = {"#": "＃", ">": "＞", "-": "－", "+": "＋", "=": "＝", "|": "｜", "`": "｀"}


def sanitize_inline(raw: str, limit: int = 0) -> str:
    """把动态文本（标题、订阅名）压成单行安全文本。

    与 qq-maid-bot 的 `markdown_inline_text` 一致：把会破坏结构的行内符号换成
    全角字符，而不是加反斜杠——反斜杠在 QQ 里会原样显示。
    """
    text = (raw or "").replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(ch for ch in text if ch == "\n" or unicodedata.category(ch) != "Cc")
    text = re.sub(r"\s+", " ", text).strip()
    translated = []
    for ch in text:
        if ch in "`*_[]()<>|":
            translated.append(_to_fullwidth(ch))
        else:
            translated.append(ch)
    result = "".join(translated)
    if limit > 0:
        result = truncate_chars(result, limit)
    return result


def _to_fullwidth(ch: str) -> str:
    table = {
        "`": "｀",
        "*": "＊",
        "_": "＿",
        "[": "［",
        "]": "］",
        "(": "（",
        ")": "）",
        "<": "＜",
        ">": "＞",
        "|": "｜",
    }
    return table.get(ch, ch)


def truncate_chars(text: str, limit: int) -> str:
    """按 Unicode 字符数截断，超长补省略号。"""
    if limit <= 0:
        return text
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def sanitize_link(url: str | None) -> str | None:
    """只放行 http(s) 且不含尖括号/换行的链接目标。

    `[...](<url>)` 是 Markdown 里对 URL 最安全的写法，这里要先把包裹的尖括号去掉，
    否则重复渲染（例如翻译后再清洗一遍）会把链接降级成纯文本。
    """
    if not url:
        return None
    value = url.strip()
    if value.startswith("<") and value.endswith(">"):
        value = value[1:-1].strip()
    if not _HTTP_RE.match(value):
        return None
    if any(ch in value for ch in "\r\n<>"):
        return None
    return value


def markdown_link(label: str, url: str | None) -> str:
    """`[label](<url>)`；不可用链接退化成纯标签。"""
    safe_url = sanitize_link(url)
    if not safe_url:
        return label
    return f"[{label}](<{safe_url}>)" if label else safe_url


def _inline(text: str) -> str:
    """处理一行内的行内语法。"""
    line = _TAG_RE.sub("", text)
    line = _IMG_RE.sub(lambda m: m.group(1).strip(), line)
    line = _LINK_RE.sub(lambda m: _inline_link(m.group(1), m.group(2)), line)
    line = _BOLD_RE.sub(r"\1", line)
    line = _STRIKE_RE.sub(r"\1", line)
    line = _ITALIC_RE.sub(r"\1", line)
    line = _UNDERSCORE_RE.sub(r"\1", line)
    # 花括号包裹的 QQ 专用语法（如 {#emoji}）在外部内容里一律当普通文本
    return line.strip()


def _inline_link(label: str, url: str) -> str:
    label = label.strip()
    safe = sanitize_link(url)
    if not safe:
        return label or url.strip()
    return f"[{label}](<{safe}>)" if label else safe


def to_qq_markdown(raw: str, limit: int = 0) -> str:
    """把任意 Markdown/HTML 文本收敛为 QQ 支持的 Markdown 子集。"""
    if not raw or not raw.strip():
        return ""
    lines = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[str] = []
    in_fence = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            out.append(stripped)
            continue
        if in_fence:
            out.append(line.rstrip())
            continue
        if not stripped:
            out.append("")
            continue
        if _HR_RE.match(stripped):
            out.append("")
            continue
        if _TABLE_SEP_RE.match(stripped) and "-" in stripped:
            continue

        heading = _HEADING_RE.match(stripped)
        if heading:
            level = min(len(heading.group(1)), 3)
            out.append("#" * level + " " + _inline(heading.group(2)))
            continue

        ordered = _OL_RE.match(line)
        if ordered:
            indent = ordered.group(1)[:8]
            out.append(f"{indent}{ordered.group(2)}. {_inline(ordered.group(3))}")
            continue

        bullet = _UL_RE.match(line)
        if bullet:
            indent = bullet.group(1)[:8]
            out.append(f"{indent}- {_inline(bullet.group(2))}")
            continue

        if stripped.startswith(">"):
            # 引用在 QQ 里表现不稳定，只留文字
            out.append(_inline(stripped.lstrip("> ").strip()))
            continue

        if "|" in stripped and stripped.count("|") >= 2:
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            out.append(_inline(" / ".join(cell for cell in cells if cell)))
            continue

        # 普通文本：行首的结构标记换全角，避免把正文渲染成标题/列表
        if stripped[0] in _FULLWIDTH_MAP:
            stripped = _FULLWIDTH_MAP[stripped[0]] + stripped[1:]
        out.append(_inline(stripped))

    text = _collapse_blank_lines("\n".join(out))
    if limit > 0:
        text = truncate_markdown(text, limit)
    return text


def _collapse_blank_lines(text: str) -> str:
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def truncate_markdown(text: str, limit: int) -> str:
    """按行截断，保证不会切断链接或代码块。"""
    if limit <= 0 or len(text) <= limit:
        return text
    kept: list[str] = []
    total = 0
    in_fence = False
    for line in text.split("\n"):
        if line.strip().startswith("```"):
            in_fence = not in_fence
        if total + len(line) + 1 > limit - 1:
            break
        kept.append(line)
        total += len(line) + 1
    body = "\n".join(kept).rstrip()
    if in_fence:
        body += "\n```"
    return (body + "…") if body else "…"


def to_plain_text(raw: str) -> str:
    """Markdown → 纯文本，用于不支持 Markdown 的平台。

    链接保留标签文字并把 URL 放进全角括号（与 qq-maid-bot 的 `to_plain_text` 一致）。
    """
    if not raw or not raw.strip():
        return ""
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    text = _IMG_RE.sub(lambda m: m.group(1).strip(), text)
    text = _LINK_RE.sub(
        lambda m: (
            f"{m.group(1).strip()}（{m.group(2).strip()}）"
            if m.group(1).strip()
            else m.group(2).strip()
        ),
        text,
    )
    lines = []
    in_fence = False
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            lines.append(line.rstrip())
            continue
        if not stripped:
            lines.append("")
            continue
        if _HR_RE.match(stripped):
            lines.append("")
            continue
        heading = _HEADING_RE.match(stripped)
        if heading:
            lines.append(_BOLD_RE.sub(r"\1", heading.group(2)).strip())
            continue
        bullet = _UL_RE.match(line)
        if bullet:
            lines.append(f"{bullet.group(1)}- {bullet.group(2).strip()}")
            continue
        ordered = _OL_RE.match(line)
        if ordered:
            lines.append(f"{ordered.group(1)}{ordered.group(2)}. {ordered.group(3).strip()}")
            continue
        if stripped.startswith(">"):
            lines.append(stripped.lstrip("> ").strip())
            continue
        lines.append(_BOLD_RE.sub(r"\1", stripped))
    return _collapse_blank_lines("\n".join(lines))
