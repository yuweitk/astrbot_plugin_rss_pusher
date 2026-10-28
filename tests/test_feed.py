"""Feed 解析、去重指纹与 URL 安全校验测试。"""

from __future__ import annotations

import asyncio

import pytest
from astrbot_plugin_rss_pusher.core.feed import (
    FeedError,
    parse_feed_bytes,
    sanitize_rss_title,
    validate_feed_url,
)

RSS_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>少数派</title>
  <link>https://sspai.com</link>
  <description>高效工作，品质生活</description>
  <item>
    <title>第一篇 &amp; 测试</title>
    <link>https://sspai.com/post/1</link>
    <guid>post-1</guid>
    <pubDate>Tue, 20 Oct 2026 10:00:00 +0800</pubDate>
    <description><![CDATA[<p>摘要一，见 <a href="https://sspai.com/post/1">原文</a></p>]]></description>
  </item>
  <item>
    <title>第二篇</title>
    <link>https://sspai.com/post/2</link>
    <guid>post-2</guid>
    <pubDate>Mon, 19 Oct 2026 09:00:00 +0800</pubDate>
    <description><![CDATA[<p>摘要二</p>]]></description>
  </item>
</channel></rss>
"""

_ATOM_CONTENT = "&lt;ul&gt;&lt;li&gt;修复 A&lt;/li&gt;&lt;li&gt;新增 B&lt;/li&gt;&lt;/ul&gt;"

ATOM_TEMPLATE = (
    """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Release notes</title>
  <id>tag:github.com,2008:repo</id>
  <updated>2026-10-25T08:00:00Z</updated>
  <entry>
    <id>tag:github.com,2008:Repository/1/v1.2.0</id>
    <title>v1.2.0</title>
    <link rel="alternate" type="text/html" href="https://github.com/x/y/releases/tag/v1.2.0"/>
    <updated>2026-10-25T07:30:00Z</updated>
    <content type="html">"""
    + _ATOM_CONTENT
    + """</content>
  </entry>
</feed>
"""
)


def _parse(xml: str, limit: int = 500):
    return parse_feed_bytes(xml.encode("utf-8"), "https://example.com/feed.xml", limit)


def test_rss_basic_fields():
    feed = _parse(RSS_TEMPLATE)
    assert feed.title == "少数派"
    assert len(feed.items) == 2
    first = feed.items[0]
    assert first.title == "第一篇 & 测试"
    assert first.item_key == "id:post-1"
    assert first.link == "https://sspai.com/post/1"
    assert first.published_at is not None and first.published_at.endswith("+00:00")
    assert "摘要一" in (first.summary or "")
    assert "[原文](<https://sspai.com/post/1>)" in (first.summary or "")


def test_atom_content_and_id():
    feed = _parse(ATOM_TEMPLATE)
    assert feed.title == "Release notes"
    assert len(feed.items) == 1
    item = feed.items[0]
    assert item.item_key.startswith("id:tag:github.com")
    assert item.title == "v1.2.0"
    assert "修复 A" in (item.summary or "")
    assert "新增 B" in (item.summary or "")


def test_summary_limit_is_applied():
    feed = _parse(RSS_TEMPLATE, limit=12)
    assert all(len(item.summary or "") <= 13 for item in feed.items)


def test_revision_hash_changes_when_content_changes():
    feed_a = _parse(RSS_TEMPLATE)
    changed = RSS_TEMPLATE.replace("摘要二", "摘要二（已修订）")
    feed_b = _parse(changed)
    same_key = feed_a.items[1].item_key == feed_b.items[1].item_key
    assert same_key
    assert feed_a.items[1].revision_hash != feed_b.items[1].revision_hash
    # 未改动的条目指纹不变
    assert feed_a.items[0].revision_hash == feed_b.items[0].revision_hash


def test_revision_hash_is_stable_across_parses():
    assert (
        _parse(RSS_TEMPLATE).items[0].revision_hash == _parse(RSS_TEMPLATE).items[0].revision_hash
    )


def test_item_without_guid_or_link_uses_fallback_key():
    xml = """<rss version="2.0"><channel><title>T</title>
    <item><title>无链接条目</title><pubDate>Tue, 20 Oct 2026 10:00:00 +0800</pubDate></item>
    </channel></rss>"""
    feed = _parse(xml)
    assert feed.items[0].item_key.startswith("fallback:")
    assert feed.items[0].link is None


def test_placeholder_titles_fall_back():
    assert sanitize_rss_title("null") is None
    assert sanitize_rss_title("   ") is None
    assert sanitize_rss_title("正常标题") == "正常标题"
    assert sanitize_rss_title("带*号*的标题") == "带＊号＊的标题"


def test_non_feed_html_raises():
    with pytest.raises(FeedError):
        _parse("<html><body><h1>404</h1></body></html>")


def test_validate_rejects_local_and_metadata():
    for url in (
        "http://localhost/feed.xml",
        "http://127.0.0.1:8000/feed.xml",
        "http://169.254.169.254/latest/meta-data",
        "file:///etc/passwd",
        "https://user:pass@example.com/feed",
    ):
        with pytest.raises(FeedError):
            asyncio.run(validate_feed_url(url))


def test_validate_allows_private_when_explicitly_enabled():
    assert asyncio.run(validate_feed_url("http://127.0.0.1:8000/feed.xml", allow_private=True))
