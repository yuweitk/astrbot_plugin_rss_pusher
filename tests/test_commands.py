"""`/rss` 参数解析的行为测试（对齐 rss_flow.rs 的用例）。"""

from __future__ import annotations

import pytest
from astrbot_plugin_rss_pusher.core import commands as cmd
from astrbot_plugin_rss_pusher.core.models import Subscription


def test_extract_argument_strips_prefix_and_alias():
    assert cmd.extract_argument("/rss add https://a.com/feed") == "add https://a.com/feed"
    assert cmd.extract_argument("/订阅 列表") == "列表"
    assert cmd.extract_argument("rss订阅 add https://a") == "add https://a"
    assert cmd.extract_argument("/rss") == ""


def test_extract_argument_ignores_similar_words():
    # "rss推送" 不是本插件的指令，不应被截断成 "推送"
    assert cmd.extract_argument("rss推送 一下") == "rss推送 一下"


def test_classify_defaults_and_url_shortcut():
    assert cmd.classify("") == ("list", "")
    assert cmd.classify("list") == ("list", "")
    assert cmd.classify("ls") == ("list", "")
    assert cmd.classify("最近 3") == ("recent", "3")
    assert cmd.classify("https://a.com/feed") == ("add", "https://a.com/feed")
    assert cmd.classify("nope")[0] == "help"


def test_parse_add_single_line_with_name():
    entries = cmd.parse_add_arguments("https://a.com/feed 少数派")
    assert entries == [cmd.AddEntry(url="https://a.com/feed", name="少数派")]


def test_parse_add_multiline_title_then_url():
    argument = "少数派\nhttps://a.com/feed\nGitHub Releases\nhttps://b.com/atom.xml"
    entries = cmd.parse_add_arguments(argument)
    assert entries is not None
    assert [(e.url, e.name) for e in entries] == [
        ("https://a.com/feed", "少数派"),
        ("https://b.com/atom.xml", "GitHub Releases"),
    ]


def test_parse_add_accepts_numbered_list_copy():
    argument = "1. 少数派\n   https://a.com/feed\n2、GitHub\nhttps://b.com/atom.xml"
    entries = cmd.parse_add_arguments(argument)
    assert entries is not None
    assert [e.name for e in entries] == ["少数派", "GitHub"]


def test_parse_add_rejects_empty():
    assert cmd.parse_add_arguments("   ") is None
    assert cmd.parse_add_arguments("只有标题没有地址") is None


def test_parse_delete_arguments_supports_batch():
    assert cmd.parse_delete_arguments("1 2,3，4") == ["1", "2", "3", "4"]
    assert cmd.parse_delete_arguments("") == []


def test_parse_recent_limit():
    assert cmd.parse_recent_limit("") == cmd.RECENT_DEFAULT_LIMIT
    assert cmd.parse_recent_limit("10") == 10
    with pytest.raises(ValueError):
        cmd.parse_recent_limit("21")
    with pytest.raises(ValueError):
        cmd.parse_recent_limit("abc")
    with pytest.raises(ValueError):
        cmd.parse_recent_limit("3 4")


def _sub(sub_id: str) -> Subscription:
    return Subscription(
        id=sub_id,
        scope_key="qq_official:GroupMessage:g1",
        platform="qq_official",
        target_type="group",
        target_id="g1",
        url=f"https://example.com/{sub_id}.xml",
        title=sub_id,
    )


def test_resolve_subscription_by_index_and_prefix():
    subs = [_sub("aaa1"), _sub("bbb2")]
    assert cmd.resolve_subscription(subs, "1").id == "aaa1"
    assert cmd.resolve_subscription(subs, "2").id == "bbb2"
    assert cmd.resolve_subscription(subs, "bbb").id == "bbb2"
    assert cmd.resolve_subscription(subs, "0") is None
    assert cmd.resolve_subscription(subs, "9") is None
    assert cmd.resolve_subscription(subs, "zzz") is None
