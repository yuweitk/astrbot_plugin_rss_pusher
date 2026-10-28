"""推送模板与列表排版测试。"""

from __future__ import annotations

from astrbot_plugin_rss_pusher.core.formatter import (
    format_add_batch,
    format_add_single,
    format_help,
    format_push_markdown,
    format_push_text,
    format_recent,
    format_subscription_list,
)
from astrbot_plugin_rss_pusher.core.models import StoredItem, Subscription
from astrbot_plugin_rss_pusher.core.timeutil import format_for_display


def _item(**overrides) -> StoredItem:
    base = {
        "item_key": "id:1",
        "revision_hash": "h",
        "title": "文章标题",
        "link": "https://example.com/post/1",
        "summary": "摘要正文",
        "published_at": "2026-10-20T02:00:00+00:00",
        "updated_at": None,
        "first_seen_at": "2026-10-20T02:01:00+00:00",
    }
    base.update(overrides)
    return StoredItem(**base)


def _sub(**overrides) -> Subscription:
    base = {
        "id": "sub1",
        "scope_key": "qq_official:GroupMessage:g1",
        "platform": "qq_official",
        "target_type": "group",
        "target_id": "g1",
        "url": "https://example.com/feed.xml",
        "title": "少数派",
        "created_at": "2026-10-20T02:00:00+00:00",
        "last_checked_at": "2026-10-20T03:00:00+00:00",
    }
    base.update(overrides)
    return Subscription(**base)


def test_push_markdown_layout():
    text = format_push_markdown("少数派", _item())
    lines = text.split("\n")
    assert lines[0] == "## RSS 更新：少数派"
    assert lines[2] == "### [文章标题](<https://example.com/post/1>)"
    assert "摘要正文" in text
    assert "发布时间：2026-10-20 10:00" in text
    assert "原文：[查看条目](<https://example.com/post/1>)" in text


def test_push_markdown_without_link():
    text = format_push_markdown("少数派", _item(link=None))
    assert "### 文章标题" in text
    assert "原文：" not in text


def test_push_text_layout_has_no_markdown():
    text = format_push_text("少数派", _item())
    assert text.startswith("【RSS 更新】少数派")
    assert "###" not in text
    assert "https://example.com/post/1" in text


def test_push_title_is_sanitized():
    text = format_push_markdown("少数派", _item(title="**加粗** [x](y)"))
    assert "＊＊加粗＊＊" in text


def test_push_respects_max_chars():
    item = _item(summary="长" * 500)
    text = format_push_markdown("少数派", item, max_chars=120)
    assert len(text) <= 120


def test_subscription_list_markdown_and_text():
    md = format_subscription_list([_sub()], markdown=True)
    assert md.startswith("# 📰 RSS 订阅")
    assert "共 1 个订阅" in md
    assert "打开订阅源" in md
    assert "最近检查：2026-10-20 11:00" in md

    plain = format_subscription_list([_sub()], markdown=False)
    assert not plain.startswith("#")
    assert "少数派 · ✅ 已启用" in plain
    assert "https://example.com/feed.xml" in plain


def test_subscription_list_shows_error():
    sub = _sub(last_error="HTTP 500")
    assert "⚠️ 最近错误：HTTP 500" in format_subscription_list([sub], markdown=True)


def test_empty_list_variants():
    assert "还没有订阅" in format_subscription_list([], markdown=True)
    assert "还没有订阅" in format_subscription_list([], markdown=False)


def test_recent_layout():
    text = format_recent(
        [_sub()],
        [("少数派", _item())],
        markdown=True,
    )
    assert text.startswith("# 最近 RSS 更新")
    assert "[文章标题](<https://example.com/post/1>)" in text


def test_recent_empty_states():
    assert "还没有 RSS 订阅" in format_recent([], [], markdown=True)
    text = format_recent([_sub()], [], markdown=True)
    assert "还没有抓到更新" in text


def test_recent_hints_failed_subscriptions():
    text = format_recent([_sub(last_error="timeout")], [("少数派", _item())], markdown=True)
    assert "1 个订阅源最近检查失败" in text


def test_help_and_add_messages():
    assert "/rss add" in format_help()
    assert "已添加 RSS 订阅" in format_add_single(_sub(), 3)
    batch = format_add_batch([(_sub(), 3)], [("https://bad", "HTTP 404")])
    assert "已添加 1 个订阅" in batch
    assert "失败 1 个" in batch


def test_time_display_handles_offset():
    assert format_for_display("2026-10-20T02:00:00+00:00", 8) == "2026-10-20 10:00"
    assert format_for_display("2026-10-20T02:00:00+00:00", 0) == "2026-10-20 02:00"
    assert format_for_display("2026-10-20T02:00:00Z", 8) == "2026-10-20 10:00"
    assert format_for_display("不是时间", 8) == "不是时间"
    assert format_for_display(None, 8) == "未知时间"
