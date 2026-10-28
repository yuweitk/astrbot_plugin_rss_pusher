"""HTML → Markdown / 纯文本的轻量转换。

qq-maid-bot 用 `html2text` + `pulldown-cmark` 两步处理 RSS 摘要；这里用标准库
`html.parser` 做一次够用的转换，再交给 `qqmd` 收敛到 QQ 支持的 Markdown 子集，
好处是不引入额外的解析依赖，且输出结构可控（表格会被压平成 `a / b`）。
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

_BLOCK_TAGS = {
    "p",
    "div",
    "section",
    "article",
    "header",
    "footer",
    "figure",
    "figcaption",
    "dl",
    "form",
}
_HEADING_TAGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
_SKIP_TAGS = {"script", "style", "noscript", "template", "svg"}

_SCRIPT_RE = re.compile(r"(?is)<script[^>]*>.*?</script>")
_STYLE_RE = re.compile(r"(?is)<style[^>]*>.*?</style>")
_HREF_RE = re.compile(r"^(https?://)", re.I)


def _language_from_class(value: str) -> str:
    """从 `class="language-python"` 里取出语言名。"""
    for token in (value or "").split():
        if token.startswith("language-"):
            return token[len("language-") :]
    return ""


def strip_script_style(raw: str) -> str:
    """去掉 script/style 内容。

    RSS 摘要里偶尔会带整段内联样式或统计脚本，先剥掉再解析能显著减少噪声。
    """
    return _STYLE_RE.sub(" ", _SCRIPT_RE.sub(" ", raw or ""))


class _Renderer(HTMLParser):
    def __init__(self, *, markdown: bool) -> None:
        super().__init__(convert_charrefs=True)
        self.markdown = markdown
        self.out: list[str] = []
        self._skip_depth = 0
        self._link_stack: list[dict] = []
        self._list_stack: list[dict] = []
        self._pre_depth = 0
        self._pre_lang = ""
        self._fence_open = False
        self._in_table = 0

    # ------------------------------------------------------------------ helpers
    def _emit(self, text: str) -> None:
        if text:
            self.out.append(text)

    def _newline(self, count: int = 1) -> None:
        """插入换行，避免堆出多余空行。"""
        joined = "".join(self.out)
        existing = len(joined) - len(joined.rstrip("\n"))
        if existing < count:
            self.out.append("\n" * (count - existing))

    def _ensure_gap(self) -> None:
        """块级元素之间至少一个空行。"""
        joined = "".join(self.out)
        if not joined.strip():
            return
        if not joined.endswith("\n\n"):
            self._newline(2)

    def _current_list_marker(self) -> str:
        if not self._list_stack:
            return ""
        top = self._list_stack[-1]
        indent = "  " * (len(self._list_stack) - 1)
        if top["ordered"]:
            return f"{indent}{top['index']}. "
        return f"{indent}- "

    def _open_fence(self, lang: str = "") -> None:
        """进入代码块时补上开始围栏（语言标记来自 <code class>）。"""
        if not self.markdown or not self._pre_depth or self._fence_open:
            return
        language = re.sub(r"[^0-9A-Za-z_+-]", "", lang or self._pre_lang)
        self._newline(1)
        self._emit("```" + language + "\n")
        self._fence_open = True

    def _close_fence(self) -> None:
        if not self.markdown or not self._pre_depth:
            return
        self._open_fence()
        if not "".join(self.out).endswith("\n"):
            self._emit("\n")
        self._emit("```")
        self._fence_open = False

    # ------------------------------------------------------------------- parser
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attr = {k.lower(): (v or "") for k, v in attrs}
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return

        if tag == "br":
            self._newline(1)
        elif tag in _HEADING_TAGS:
            self._ensure_gap()
            level = _HEADING_TAGS[tag]
            if self.markdown:
                self._emit("#" * min(level, 3) + " ")
        elif tag in ("ul", "ol"):
            self._ensure_gap()
            self._list_stack.append({"ordered": tag == "ol", "index": 1})
        elif tag == "li":
            self._newline(1)
            marker = self._current_list_marker()
            self._emit(marker)
            if self._list_stack and self._list_stack[-1]["ordered"]:
                self._list_stack[-1]["index"] += 1
        elif tag in _BLOCK_TAGS or tag == "blockquote":
            self._ensure_gap()
        elif tag == "a":
            href = attr.get("href", "").strip()
            self._link_stack.append({"href": href, "text": []})
        elif tag == "img":
            alt = attr.get("alt", "").strip()
            if alt and self.markdown:
                self._emit(alt)
        elif tag in ("strong", "b"):
            if self.markdown:
                self._emit("**")
        elif tag in ("em", "i"):
            if self.markdown:
                self._emit("*")
        elif tag in ("del", "s", "strike"):
            if self.markdown:
                self._emit("~~")
        elif tag == "code":
            if self._pre_depth:
                # 语言标记通常挂在 <code class="language-xxx"> 上
                lang = _language_from_class(attr.get("class", ""))
                self._open_fence(lang)
            elif self.markdown:
                self._emit("`")
        elif tag == "pre":
            self._ensure_gap()
            self._pre_depth += 1
            self._fence_open = False
            self._pre_lang = _language_from_class(attr.get("class", ""))
        elif tag == "table":
            self._in_table += 1
            self._ensure_gap()
        elif tag == "tr":
            self._newline(1)
        elif tag in ("td", "th"):
            joined = "".join(self.out)
            if joined and not joined.endswith("\n") and not joined.endswith(" / "):
                self._emit(" / ")

    def handle_startendtag(self, tag, attrs) -> None:  # <img /> <br /> 等
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return

        if tag in _HEADING_TAGS or tag in _BLOCK_TAGS:
            self._ensure_gap()
        elif tag in ("ul", "ol"):
            if self._list_stack:
                self._list_stack.pop()
            self._newline(1)
            if not self._list_stack:
                self._newline(2)
        elif tag == "li":
            self._newline(1)
        elif tag == "blockquote":
            self._ensure_gap()
        elif tag == "a":
            if self._link_stack:
                frame = self._link_stack.pop()
                label = "".join(frame["text"]).strip()
                href = frame["href"]
                if self.markdown and label and _HREF_RE.match(href):
                    self._emit(f"[{label}]({href})")
                else:
                    self._emit(label or href)
        elif tag in ("strong", "b"):
            if self.markdown:
                self._emit("**")
        elif tag in ("em", "i"):
            if self.markdown:
                self._emit("*")
        elif tag in ("del", "s", "strike"):
            if self.markdown:
                self._emit("~~")
        elif tag == "code":
            if self.markdown and not self._pre_depth:
                self._emit("`")
        elif tag == "pre":
            if self._pre_depth:
                if self.markdown:
                    self._close_fence()
                self._pre_depth -= 1
                self._newline(2)
        elif tag == "table":
            self._in_table = max(0, self._in_table - 1)
            self._newline(2)
        elif tag == "tr":
            self._newline(1)
        elif tag in ("td", "th"):
            pass

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._pre_depth:
            self._open_fence("")
            self.out.append(data)
            return
        if self._link_stack:
            # 链接文字先缓存，等 </a> 时再决定渲染成 Markdown 还是纯文字
            self._link_stack[-1]["text"].append(data)
            return
        # 普通文本按行折叠空白，保留作者可见的语感
        text = re.sub(r"[ \t\r\f\v]+", " ", data.replace("\r\n", "\n"))
        if not text.strip():
            if not "".join(self.out).endswith(("\n", " ")) and text:
                self.out.append(" ")
            return
        self.out.append(text)

    def result(self) -> str:
        text = "".join(self.out)
        text = re.sub(r"[ \t]+\n", "\n", text)
        # 空行折叠要跳过代码块，否则代码里的空行会被吃掉
        blocks = re.split(r"(^```.*?^```[ \t]*$)", text, flags=re.S | re.M)
        for index, block in enumerate(blocks):
            if block.startswith("```"):
                continue
            blocks[index] = re.sub(r"\n{3,}", "\n\n", block)
        return "".join(blocks).strip()


def html_to_markdown(raw: str) -> str:
    """把 RSS 摘要的 HTML 转成 Markdown（链接、列表、代码尽量保留结构）。"""
    if not raw or not raw.strip():
        return ""
    parser = _Renderer(markdown=True)
    try:
        parser.feed(strip_script_style(raw))
        parser.close()
    except Exception:  # HTMLParser 对畸形输入比较宽容，这里只兜底极端情况
        return re.sub(r"<[^>]+>", " ", raw).strip()
    return parser.result()


def html_to_text(raw: str) -> str:
    """把 HTML 转成纯文本，用于纯文本推送与修订指纹计算。"""
    if not raw or not raw.strip():
        return ""
    parser = _Renderer(markdown=False)
    try:
        parser.feed(strip_script_style(raw))
        parser.close()
    except Exception:
        return re.sub(r"<[^>]+>", " ", raw).strip()
    return parser.result()
