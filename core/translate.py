"""可选的标题/摘要翻译。

qq-maid-bot 的 RSS 推送会把非中文标题与摘要翻成中文再发。这里复用 AstrBot 当前
默认模型做同样的事，但默认关闭（会产生模型调用费用），并且有三条硬约束：

1. 翻译失败、模型不可用、超时，一律回落原文；
2. 只翻「可见文本」，翻译前后的链接集合必须完全一致，否则丢弃译文；
3. 译文仍然要重新过一遍 QQ Markdown 子集渲染，不能直接拼进消息。
"""

from __future__ import annotations

import dataclasses
import re

from .models import StoredItem
from .qqmd import sanitize_inline, to_qq_markdown

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_LINK_RE = re.compile(r"\]\(<([^>]+)>\)|(https?://[^\s)<>]+)")

_SYSTEM_PROMPT = (
    "你是一个只做翻译的引擎。把用户给出的文本翻译成简体中文。"
    "严格保留原文的 Markdown 结构、链接地址和换行；不要添加解释、不要改写链接、"
    "不要输出任何前言后语。"
)


def looks_like_chinese(text: str) -> bool:
    """粗略判断是否已经是中文：中文字符占比达到 20% 即视为中文。"""
    stripped = (text or "").strip()
    if not stripped:
        return True
    cjk = len(_CJK_RE.findall(stripped))
    return cjk > 0 and cjk / max(1, len(stripped)) >= 0.2


def extract_links(text: str) -> list[str]:
    links = []
    for match in _LINK_RE.finditer(text or ""):
        links.append(match.group(1) or match.group(2) or "")
    return sorted(link for link in links if link)


class FeedTranslator:
    """按需翻译，任何异常都安静降级。"""

    def __init__(self, context, *, enabled: bool, max_chars: int, logger) -> None:
        self.context = context
        self.enabled = enabled
        self.max_chars = max_chars
        self.logger = logger

    async def translate_item(self, umo: str, item: StoredItem) -> StoredItem:
        if not self.enabled:
            return item
        needs_title = not looks_like_chinese(item.title)
        needs_summary = bool(item.summary) and not looks_like_chinese(item.summary or "")
        if not needs_title and not needs_summary:
            return item

        provider = None
        try:
            provider = await self.context.get_using_provider_async(umo=umo)
        except Exception as exc:  # 没有可用模型是很常见的状态
            self.logger.debug(f"[rss] 获取模型失败，跳过翻译：{exc}")
            return item
        if provider is None:
            return item

        title = item.title
        summary = item.summary
        if needs_title:
            translated = await self._translate(provider, item.title, umo)
            if translated:
                title = sanitize_inline(translated, 120) or item.title
        if needs_summary and item.summary:
            translated = await self._translate(provider, item.summary, umo)
            if translated:
                if extract_links(item.summary) == extract_links(translated):
                    summary = to_qq_markdown(translated, 0) or item.summary
                else:
                    self.logger.warning("[rss] 翻译改动了链接，已改用原文摘要")
        return dataclasses.replace(item, title=title, summary=summary)

    async def _translate(self, provider, text: str, umo: str) -> str | None:
        source = (text or "").strip()
        if not source:
            return None
        if len(source) > self.max_chars:
            self.logger.debug("[rss] 文本超过翻译长度上限，跳过翻译")
            return None
        try:
            response = await provider.text_chat(
                prompt=source,
                system_prompt=_SYSTEM_PROMPT,
            )
        except Exception as exc:
            self.logger.debug(f"[rss] 翻译失败，改用原文：{exc}")
            return None
        result = (getattr(response, "completion_text", "") or "").strip()
        return result or None
