"""调度器测试：只推新条目、失败重试、抓取失败记录。"""

from __future__ import annotations

import asyncio

from astrbot_plugin_rss_pusher.core.feed import FeedError
from astrbot_plugin_rss_pusher.core.models import FeedItem, ParsedFeed
from astrbot_plugin_rss_pusher.core.scheduler import RssScheduler, SchedulerConfig


class FakeSender:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.sent: list[tuple] = []

    async def send(self, subscription, item):
        if self.fail:
            return False, "send failed"
        self.sent.append((subscription, item))
        return True, None


class FakeTranslator:
    async def translate_item(self, umo, item):
        return item


def _item(key: str, title: str = "标题", published: str = "2026-10-20T02:00:00+00:00") -> FeedItem:
    return FeedItem(
        item_key=key,
        revision_hash=f"hash-{key}",
        title=title,
        link=f"https://example.com/{key}",
        summary="摘要",
        published_at=published,
        updated_at=published,
        source_order=0,
    )


class Feeds:
    """按调用顺序返回不同的 Feed，模拟源站更新。"""

    def __init__(self, *feeds) -> None:
        self.feeds = list(feeds)
        self.calls = 0
        self.error: Exception | None = None

    async def __call__(self, url, **kwargs):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.feeds.pop(0) if len(self.feeds) > 1 else self.feeds[0]


def _scheduler(store, sender, fetcher) -> RssScheduler:
    config = SchedulerConfig(interval_seconds=300, max_push_per_feed=3, max_failures=2)
    return RssScheduler(store, sender, FakeTranslator(), config, logger=_Logger(), fetcher=fetcher)


class _Logger:
    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def exception(self, *args, **kwargs):
        pass

    def debug(self, *args, **kwargs):
        pass


def _seed(store, items):
    return asyncio.run(
        store.create_subscription(
            scope_key="qq_official:GroupMessage:g1",
            platform="qq_official",
            target_type="group",
            target_id="g1",
            url="https://example.com/feed.xml",
            title="测试订阅",
            items=items,
            seen_retention=500,
        )
    )


def test_first_round_does_not_push_history(store):
    _seed(store, [_item("a")])
    sender = FakeSender()
    scheduler = _scheduler(store, sender, Feeds(ParsedFeed("测试订阅", [_item("a")])))
    subs, pushed = asyncio.run(scheduler.run_once())
    assert (subs, pushed) == (1, 0)
    assert sender.sent == []


def test_new_item_is_pushed_once(store):
    _seed(store, [_item("a")])
    sender = FakeSender()
    feeds = Feeds(
        ParsedFeed("测试订阅", [_item("a"), _item("b")]),
        ParsedFeed("测试订阅", [_item("a"), _item("b")]),
    )
    scheduler = _scheduler(store, sender, feeds)

    assert asyncio.run(scheduler.run_once())[1] == 1
    assert [item.item_key for _, item in sender.sent] == ["b"]
    # 第二轮没有新内容，不应重复推送
    assert asyncio.run(scheduler.run_once())[1] == 0
    assert len(sender.sent) == 1


def test_max_per_feed_limits_burst(store):
    _seed(store, [])
    sender = FakeSender()
    scheduler = _scheduler(
        store,
        sender,
        Feeds(ParsedFeed("测试订阅", [_item("a"), _item("b"), _item("c"), _item("d")])),
    )
    assert asyncio.run(scheduler.run_once())[1] == 3
    assert asyncio.run(scheduler.run_once())[1] == 1


def test_failed_push_retries_until_limit(store):
    _seed(store, [])
    sender = FakeSender(fail=True)
    scheduler = _scheduler(store, sender, Feeds(ParsedFeed("测试订阅", [_item("a")])))

    assert asyncio.run(scheduler.run_once())[1] == 0
    sub = asyncio.run(store.list_by_scope("qq_official:GroupMessage:g1"))[0]
    pending = asyncio.run(store.pending_items(sub.id, 10, 2))
    assert pending[0].push_attempts == 1

    assert asyncio.run(scheduler.run_once())[1] == 0  # 第二次仍失败
    assert asyncio.run(store.pending_items(sub.id, 10, 2)) == []  # 达到上限后放弃


def test_fetch_error_is_recorded(store):
    _seed(store, [])
    sender = FakeSender()
    feeds = Feeds(ParsedFeed("测试订阅", []))
    feeds.error = FeedError("status", "订阅源返回 HTTP 500")
    scheduler = _scheduler(store, sender, feeds)

    assert asyncio.run(scheduler.run_once())[1] == 0
    sub = asyncio.run(store.list_by_scope("qq_official:GroupMessage:g1"))[0]
    assert sub.failed and "HTTP 500" in (sub.last_error or "")
    assert sub.consecutive_failures == 1
