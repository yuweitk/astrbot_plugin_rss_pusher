"""真实 HTTP 抓取测试：用本地 http.server 模拟订阅源。"""

from __future__ import annotations

import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from astrbot_plugin_rss_pusher.core.feed import FeedError, fetch_feed

FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Local Feed</title>
<item><title>A</title><guid>a</guid><link>https://example.com/a</link>
<description><![CDATA[<p>hello</p>]]></description></item>
</channel></rss>
"""


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path == "/feed.xml":
            self._send(200, FEED, "application/rss+xml")
        elif self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/feed.xml")
            self.end_headers()
        elif self.path == "/loop":
            self.send_response(302)
            self.send_header("Location", "/loop")
            self.end_headers()
        elif self.path == "/html":
            self._send(200, b"<html><body>not a feed</body></html>", "text/html")
        elif self.path == "/missing":
            self._send(404, b"nope", "text/plain")
        else:
            self._send(404, b"", "text/plain")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # 静音
        pass


@pytest.fixture(scope="module")
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def _fetch(url: str, **kwargs):
    return asyncio.run(fetch_feed(url, allow_private=True, timeout=5, **kwargs))


def test_fetch_ok(server):
    feed = _fetch(f"{server}/feed.xml")
    assert feed.title == "Local Feed"
    assert [item.item_key for item in feed.items] == ["id:a"]


def test_fetch_follows_redirect(server):
    feed = _fetch(f"{server}/redirect")
    assert feed.title == "Local Feed"


def test_fetch_redirect_loop_is_bounded(server):
    with pytest.raises(FeedError):
        _fetch(f"{server}/loop", max_redirects=2)


def test_http_error_raises(server):
    with pytest.raises(FeedError) as info:
        _fetch(f"{server}/missing")
    assert info.value.code == "status"


def test_body_limit(server):
    with pytest.raises(FeedError) as info:
        _fetch(f"{server}/feed.xml", max_bytes=64)
    assert info.value.code == "body_too_large"


def test_non_feed_page_raises(server):
    with pytest.raises(FeedError):
        _fetch(f"{server}/html")
