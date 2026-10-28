"""插件主流程测试：指令回复、权限门禁、Markdown 推送链路。"""

from __future__ import annotations

import asyncio

from astrbot_plugin_rss_pusher.core.models import FeedItem, ParsedFeed


def _item(key: str, title: str = "标题", published: str = "2026-10-20T02:00:00+00:00") -> FeedItem:
    return FeedItem(
        item_key=key,
        revision_hash=f"hash-{key}",
        title=title,
        link=f"https://example.com/{key}",
        summary="摘要内容",
        published_at=published,
        updated_at=published,
        source_order=0,
    )


def _run(plugin, event) -> tuple[str, bool | None]:
    async def _collect():
        async for result in plugin.rss_command(event):
            return result.get_plain_text(), result.use_markdown_

    return asyncio.run(_collect())


def test_empty_list_guides_user(plugin, event_factory):
    text, markdown = _run(plugin, event_factory("/rss"))
    assert "还没有订阅" in text
    assert markdown is True


def test_add_and_list(plugin, event_factory, monkeypatch):
    async def fake_fetch(url):
        return ParsedFeed("少数派", [_item("a"), _item("b")])

    monkeypatch.setattr(plugin, "_fetch", fake_fetch)
    event = event_factory("/rss add https://example.com/feed.xml 少数派")

    text, _ = _run(plugin, event)
    assert "已添加 RSS 订阅" in text
    assert "2 条历史条目" in text

    text, _ = _run(plugin, event_factory("/rss list"))
    assert "共 1 个订阅" in text
    assert "少数派" in text

    text, _ = _run(plugin, event_factory("/rss recent 5"))
    assert "最近 RSS 更新" in text
    assert "标题" in text


def test_add_duplicate_and_bad_feed(plugin, event_factory, monkeypatch):
    async def fake_fetch(url):
        return ParsedFeed("少数派", [])

    monkeypatch.setattr(plugin, "_fetch", fake_fetch)
    event = event_factory("/rss add https://example.com/feed.xml")
    _run(plugin, event)
    text, _ = _run(plugin, event)
    assert "已经订阅过了" in text

    from astrbot_plugin_rss_pusher.core.feed import FeedError

    async def failing_fetch(url):
        raise FeedError("status", "订阅源返回 HTTP 404")

    monkeypatch.setattr(plugin, "_fetch", failing_fetch)
    text, _ = _run(plugin, event_factory("/rss add https://example.com/missing.xml"))
    assert "HTTP 404" in text


def test_group_admin_gate(plugin, event_factory, monkeypatch):
    async def fake_fetch(url):
        return ParsedFeed("少数派", [])

    monkeypatch.setattr(plugin, "_fetch", fake_fetch)

    denied = _run(
        plugin, event_factory("/rss add https://example.com/a.xml", group_id="g1", admin=False)
    )[0]
    assert "只有管理员" in denied

    allowed = _run(
        plugin, event_factory("/rss add https://example.com/a.xml", group_id="g1", admin=True)
    )[0]
    assert "已添加 RSS 订阅" in allowed

    # 私聊不受限
    private = _run(
        plugin,
        event_factory(
            "/rss add https://example.com/b.xml",
            umo="qq_official:FriendMessage:u1",
            group_id="",
            admin=False,
        ),
    )[0]
    assert "已添加 RSS 订阅" in private


def test_custom_name_survives_polling(plugin, event_factory, monkeypatch):
    async def fake_fetch(url):
        return ParsedFeed("源站标题", [_item("a")])

    monkeypatch.setattr(plugin, "_fetch", fake_fetch)
    _run(plugin, event_factory("/rss add https://example.com/feed.xml 我的名字"))

    async def scheduler_fetch(url, **kwargs):
        return ParsedFeed("源站标题", [_item("a")])

    plugin.scheduler._fetcher = scheduler_fetch
    asyncio.run(plugin.scheduler.run_once())

    text, _ = _run(plugin, event_factory("/rss list"))
    assert "我的名字" in text
    assert "源站标题" not in text


def test_delete_and_toggle(plugin, event_factory, monkeypatch):
    async def fake_fetch(url):
        return ParsedFeed("少数派", [])

    monkeypatch.setattr(plugin, "_fetch", fake_fetch)
    _run(plugin, event_factory("/rss add https://example.com/a.xml"))
    _run(plugin, event_factory("/rss add https://example.com/b.xml"))

    text, _ = _run(plugin, event_factory("/rss off 1"))
    assert "已暂停 1 个订阅" in text
    text, _ = _run(plugin, event_factory("/rss on 1"))
    assert "已恢复 1 个订阅" in text

    text, _ = _run(plugin, event_factory("/rss delete 1"))
    assert "已删除 RSS 订阅" in text
    text, _ = _run(plugin, event_factory("/rss list"))
    assert "共 1 个订阅" in text


def test_test_command(plugin, event_factory, monkeypatch):
    async def fake_fetch(url):
        return ParsedFeed("少数派", [_item("a")])

    monkeypatch.setattr(plugin, "_fetch", fake_fetch)
    text, _ = _run(plugin, event_factory("/rss test https://example.com/feed.xml"))
    assert "RSS 测试成功" in text and "1" in text


def test_unknown_action_shows_help(plugin, event_factory):
    text, _ = _run(plugin, event_factory("/rss 不知道"))
    assert "RSS 订阅管理" in text


def test_plain_text_platform_uses_text_layout(plugin, event_factory):
    text, markdown = _run(plugin, event_factory("/rss", platform="aiocqhttp"))
    assert markdown is False
    assert "# 📰" not in text


def test_push_uses_markdown_chain(plugin, event_factory, monkeypatch):
    feeds = [
        ParsedFeed("少数派", [_item("a")]),
        ParsedFeed("少数派", [_item("a"), _item("b")]),
    ]

    async def fake_fetch(url):
        return feeds[0]

    monkeypatch.setattr(plugin, "_fetch", fake_fetch)
    _run(plugin, event_factory("/rss add https://example.com/feed.xml"))

    async def scheduler_fetch(url, **kwargs):
        return feeds[1]

    plugin.scheduler._fetcher = scheduler_fetch
    asyncio.run(plugin.scheduler.run_once())

    assert len(plugin.context.sent) == 1
    session, chain = plugin.context.sent[0]
    assert session == "qq_official:GroupMessage:g1"
    assert chain.use_markdown_ is True
    assert chain.get_plain_text().startswith("## RSS 更新：少数派")


def test_push_falls_back_to_plain_when_markdown_fails(plugin, event_factory, monkeypatch):
    async def fake_fetch(url):
        return ParsedFeed("少数派", [_item("a")])

    monkeypatch.setattr(plugin, "_fetch", fake_fetch)
    _run(plugin, event_factory("/rss add https://example.com/feed.xml"))

    calls = {"n": 0}

    class FlakyContext:
        def __init__(self, inner):
            self.inner = inner

        async def send_message(self, session, chain):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("markdown rejected")
            return await self.inner.send_message(session, chain)

    plugin.sender.context = FlakyContext(plugin.context)

    async def scheduler_fetch(url, **kwargs):
        return ParsedFeed("少数派", [_item("a"), _item("b")])

    plugin.scheduler._fetcher = scheduler_fetch
    asyncio.run(plugin.scheduler.run_once())

    assert calls["n"] == 2
    assert len(plugin.context.sent) == 1
    _session, chain = plugin.context.sent[0]
    assert chain.use_markdown_ is None
    assert chain.get_plain_text().startswith("【RSS 更新】")
