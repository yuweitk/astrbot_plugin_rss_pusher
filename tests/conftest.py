"""测试夹具：注入 astrbot 替身、提供 store/plugin 等常用对象。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN_ROOT.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from astrbot.api.star import StarTools  # noqa: E402
from astrbot_plugin_rss_pusher.core.store import RssStore  # noqa: E402

BASE_CONFIG = {
    "enabled": False,
    "poll_interval_seconds": 300,
    "fetch": {
        "timeout_seconds": 15,
        "max_body_kb": 2048,
        "max_redirects": 4,
        "user_agent": "test-agent",
        "allow_private_network": True,
    },
    "push": {
        "message_format": "auto",
        "markdown_platforms": ["qq_official", "qq_official_webhook", "qq_official_v2"],
        "max_per_feed": 3,
        "max_failures": 3,
        "summary_max_chars": 500,
        "max_message_chars": 3600,
    },
    "storage": {"seen_retention": 500},
    "commands": {
        "group_admin_only": True,
        "max_per_session": 20,
        "markdown_replies": True,
        "timezone_offset_hours": 8.0,
    },
    "translate": {"enabled": False, "max_chars": 400},
}


@pytest.fixture
def store(tmp_path):
    instance = RssStore(tmp_path / "rss.db")
    yield instance
    instance.close()


@pytest.fixture
def plugin(tmp_path, monkeypatch):
    StarTools.data_root = tmp_path
    from astrbot_plugin_rss_pusher.main import RssPusherPlugin

    context = astrbot_stub.Context()
    instance = RssPusherPlugin(context, astrbot_stub.AstrBotConfig(BASE_CONFIG))
    yield instance
    instance.store.close()


@pytest.fixture
def event_factory():
    def make(message_str: str, **kwargs):
        # 默认按「群管理员」构造，方便测增删流程；权限用例显式传 admin=False
        kwargs.setdefault("admin", True)
        return astrbot_stub.AstrMessageEvent(message_str, **kwargs)

    return make
