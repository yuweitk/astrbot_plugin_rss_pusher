"""可选翻译的行为测试：降级、链接一致性、长度上限。"""

from __future__ import annotations

import asyncio
import logging

from astrbot_plugin_rss_pusher.core.models import StoredItem
from astrbot_plugin_rss_pusher.core.translate import (
    FeedTranslator,
    extract_links,
    looks_like_chinese,
)

LOGGER = logging.getLogger("test")


class FakeResponse:
    def __init__(self, text: str) -> None:
        self.completion_text = text


class FakeProvider:
    def __init__(self, outputs, raise_error: bool = False) -> None:
        self.outputs = list(outputs)
        self.raise_error = raise_error
        self.calls = 0

    async def text_chat(self, prompt, system_prompt=None, **kwargs):
        self.calls += 1
        if self.raise_error:
            raise RuntimeError("model down")
        return FakeResponse(self.outputs.pop(0))


class FakeContext:
    def __init__(self, provider) -> None:
        self.provider = provider

    async def get_using_provider_async(self, umo=None):
        return self.provider


def _item(title: str = "Hello world", summary: str | None = "Some english text") -> StoredItem:
    return StoredItem(
        item_key="id:1",
        revision_hash="h",
        title=title,
        link="https://example.com/a",
        summary=summary,
        published_at="2026-10-20T02:00:00+00:00",
        updated_at=None,
        first_seen_at="2026-10-20T02:00:00+00:00",
    )


def _translate(provider, item, *, max_chars: int = 400) -> StoredItem:
    translator = FeedTranslator(
        FakeContext(provider), enabled=True, max_chars=max_chars, logger=LOGGER
    )
    return asyncio.run(translator.translate_item("qq_official:GroupMessage:g1", item))


def test_chinese_text_is_not_translated():
    provider = FakeProvider([])
    item = _item(title="中文标题", summary="中文摘要")
    result = _translate(provider, item)
    assert provider.calls == 0
    assert result.title == "中文标题"


def test_english_text_is_translated():
    provider = FakeProvider(["你好世界", "一些英文文本"])
    result = _translate(provider, _item())
    assert result.title == "你好世界"
    assert result.summary == "一些英文文本"
    assert provider.calls == 2


def test_translation_keeps_links():
    original = "Read [the post](<https://example.com/a>) now"
    translated = "现在阅读[这篇文章](<https://example.com/a>)"
    provider = FakeProvider(["你好", translated])
    result = _translate(provider, _item(summary=original))
    assert result.summary == "现在阅读[这篇文章](<https://example.com/a>)"


def test_translation_changing_links_is_rejected():
    original = "Read [the post](<https://example.com/a>) now"
    translated = "阅读[这篇文章](<https://evil.example.com/b>)"
    provider = FakeProvider(["你好", translated])
    result = _translate(provider, _item(summary=original))
    assert "evil.example.com" not in (result.summary or "")
    assert "example.com/a" in (result.summary or "")


def test_provider_error_falls_back_to_original():
    provider = FakeProvider([], raise_error=True)
    item = _item()
    result = _translate(provider, item)
    assert result.title == item.title
    assert result.summary == item.summary


def test_no_provider_available():
    result = _translate(None, _item())
    assert result.title == "Hello world"


def test_too_long_text_is_skipped():
    provider = FakeProvider([])
    long_text = "a" * 50
    result = _translate(provider, _item(title=long_text), max_chars=10)
    assert result.title == long_text
    assert provider.calls == 0


def test_translated_title_is_single_line():
    provider = FakeProvider(["第一行\n第二行", "摘要"])
    result = _translate(provider, _item())
    assert "\n" not in result.title


def test_helpers():
    assert looks_like_chinese("这是中文")
    assert not looks_like_chinese("this is english")
    assert extract_links("[a](<https://x.com/1>) and https://x.com/2") == [
        "https://x.com/1",
        "https://x.com/2",
    ]
