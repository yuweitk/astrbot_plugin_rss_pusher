"""端到端冒烟脚本：真 HTTP 抓取 + 真 SQLite + 真实 Markdown 排版。

不需要启动 AstrBot，也不需要真实 QQ 机器人：脚本用 astrbot 替身把
`/rss add` → 轮询 → 推送的整条链路跑一遍，并把「会发出去的消息原文」打印出来，
方便肉眼确认 QQ Markdown 的排版效果。

用法（在插件目录下）：

    python scripts/smoke_preview.py
"""

import asyncio
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN_ROOT.parent))
sys.path.insert(0, str(PLUGIN_ROOT / "tests"))

import astrbot_stub  # noqa: E402

astrbot_stub.install()
from astrbot.api.star import StarTools  # noqa: E402

StarTools.data_root = PLUGIN_ROOT.parent / ".smoke_data"

from astrbot_plugin_rss_pusher.main import RssPusherPlugin  # noqa: E402

FEED = {"body": b""}


def build_feed(extra: bool) -> bytes:
    items = """
  <item>
    <title>用 Rust 写一个轻量 Agent</title>
    <link>https://example.com/posts/rust-agent</link>
    <guid>post-rust-agent</guid>
    <pubDate>Wed, 21 Oct 2026 09:30:00 +0800</pubDate>
    <description><![CDATA[
      <p>这篇讲 <strong>工程取舍</strong>：为什么用单进程，为什么把
      <a href="https://example.com/spec">协议层</a> 抽出来。</p>
      <ul><li>常驻内存 25MiB</li><li>空闲 3 线程</li></ul>
    ]]></description>
  </item>"""
    if extra:
        items += """
  <item>
    <title>**新**文章：Markdown 在 QQ 里怎么不崩</title>
    <link>https://example.com/posts/qq-markdown</link>
    <guid>post-qq-markdown</guid>
    <pubDate>Thu, 22 Oct 2026 11:00:00 +0800</pubDate>
    <description><![CDATA[
      <blockquote>外部内容必须清洗</blockquote>
      <p>见 <a href="https://example.com/md">语法文档</a>。</p>
      <table><tr><th>语法</th><th>稳定性</th></tr><tr><td>标题</td><td>好</td></tr></table>
    ]]></description>
  </item>"""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>工程笔记</title>
  <link>https://example.com</link>{items}
</channel></rss>""".encode()


FEED["body"] = build_feed(False)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = FEED["body"]
        self.send_response(200)
        self.send_header("Content-Type", "application/rss+xml; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{httpd.server_address[1]}/feed.xml"


CONFIG = {
    "enabled": False,
    "poll_interval_seconds": 300,
    "fetch": {"allow_private_network": True, "timeout_seconds": 10},
    "push": {"message_format": "auto", "markdown_platforms": ["qq_official"]},
    "commands": {"group_admin_only": True, "timezone_offset_hours": 8.0},
}


async def main():
    context = astrbot_stub.Context()
    plugin = RssPusherPlugin(context, astrbot_stub.AstrBotConfig(CONFIG))
    event = astrbot_stub.AstrMessageEvent(
        f"/rss add {url}", umo="qq_official:GroupMessage:group-abc", admin=True
    )

    async for result in plugin.rss_command(event):
        print("=== /rss add 回复 ===")
        print(result.get_plain_text())

    async for result in plugin.rss_command(
        astrbot_stub.AstrMessageEvent(
            "/rss list", umo="qq_official:GroupMessage:group-abc", admin=True
        )
    ):
        print("\n=== /rss list ===")
        print(result.get_plain_text())

    # 源站更新一条
    FEED["body"] = build_feed(True)
    subs, pushed = await plugin.scheduler.run_once()
    print(f"\n=== 轮询：订阅 {subs} 个，推送 {pushed} 条 ===")
    for session, chain in context.sent:
        print(f"--- 发往 {session} | use_markdown_={chain.use_markdown_} ---")
        print(chain.get_plain_text())
        print("--- end ---")

    async for result in plugin.rss_command(
        astrbot_stub.AstrMessageEvent(
            "/rss recent 3", umo="qq_official:GroupMessage:group-abc", admin=True
        )
    ):
        print("\n=== /rss recent 3 ===")
        print(result.get_plain_text())

    subs = await plugin.store.all_enabled()
    print("\n=== 订阅状态 ===")
    for sub in subs:
        print(sub.title, sub.url, sub.last_error, sub.consecutive_failures)

    await plugin.terminate()
    httpd.shutdown()


asyncio.run(main())
