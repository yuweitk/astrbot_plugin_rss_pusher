"""RSS / Atom 拉取、解析与安全清理。

对应 qq-maid-bot 的 `qq-maid-core/src/runtime/tools/rss/feed.rs`。Feed 是不可信的
外部输入，所以这里做三件事：

1. 请求前校验 URL：只允许 http(s)，拦截 localhost、内网、link-local、云 metadata，
   并且**每一跳重定向都重新校验**（重定向到内网是常见的 SSRF 绕过手法）；
2. 请求中限制超时、重定向次数与响应体大小，避免被慢速源或超大响应拖住；
3. 解析后只输出纯文本字段：标题单行化、摘要渲染成 QQ Markdown 子集、链接只留 http(s)。
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import ipaddress
import socket
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit

import aiohttp
import feedparser

from .html_md import html_to_markdown, html_to_text
from .models import FeedItem, ParsedFeed
from .qqmd import sanitize_inline, to_qq_markdown

RSS_TITLE_MAX_CHARS = 240
_REVISION_TEXT_MAX_CHARS = 4000
_BLOCKED_HOSTNAMES = {
    "localhost",
    "metadata",
    "metadata.google.internal",
}


class FeedError(Exception):
    """带错误码的 Feed 异常，方便上层给出中文提示。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def __str__(self) -> str:  # pragma: no cover - 仅用于日志
        return self.message


def _is_blocked_ip(ip: ipaddress._BaseAddress) -> bool:
    return (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_unspecified
        or ip.is_multicast
        or ip.is_reserved
        or str(ip) == "169.254.169.254"
    )


def _is_blocked_hostname(host: str) -> bool:
    host = host.strip().rstrip(".").lower()
    return host in _BLOCKED_HOSTNAMES or host.endswith(".localhost")


async def validate_feed_url(raw: str, *, allow_private: bool = False) -> str:
    """校验订阅地址，返回去掉空白后的 URL。"""
    url = (raw or "").strip()
    if not url:
        raise FeedError("empty_url", "URL 不能为空")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise FeedError("bad_scheme", "只支持 http/https 的 RSS 地址")
    if parts.username or parts.password:
        raise FeedError("url_credentials", "RSS 地址不能包含用户名或密码")
    host = parts.hostname
    if not host:
        raise FeedError("missing_host", "RSS 地址缺少主机名")
    if allow_private:
        return url
    if _is_blocked_hostname(host):
        raise FeedError("unsafe_host", "地址指向本机、内网或 metadata，已拦截")

    port = parts.port or (443 if parts.scheme == "https" else 80)
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise FeedError("dns", f"DNS 解析失败：{exc}") from exc
    if not infos:
        raise FeedError("dns", "DNS 解析失败：没有可用地址")
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if _is_blocked_ip(ip):
            raise FeedError("unsafe_host", "地址指向本机、内网或 metadata，已拦截")
    return url


async def _read_limited(response: aiohttp.ClientResponse, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.content.iter_chunked(64 * 1024):
        total += len(chunk)
        if total > max_bytes:
            raise FeedError("body_too_large", f"响应体超过 {max_bytes // 1024} KB 上限")
        chunks.append(chunk)
    return b"".join(chunks)


async def fetch_feed(
    url: str,
    *,
    timeout: float = 15.0,
    max_bytes: int = 2 * 1024 * 1024,
    max_redirects: int = 4,
    user_agent: str = "astrbot-rss-pusher/1.0",
    allow_private: bool = False,
    summary_limit: int = 500,
) -> ParsedFeed:
    """抓取并解析订阅源。"""
    timeout_cfg = aiohttp.ClientTimeout(total=timeout)
    headers = {
        "User-Agent": user_agent,
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
    }
    current = url
    connector = aiohttp.TCPConnector(limit=8)
    async with aiohttp.ClientSession(
        timeout=timeout_cfg,
        headers=headers,
        connector=connector,
    ) as session:
        for _ in range(max_redirects + 1):
            current = await validate_feed_url(current, allow_private=allow_private)
            try:
                # 手动处理重定向（allow_redirects=False），才能逐跳做内网校验
                async with session.get(current, allow_redirects=False) as response:
                    if response.status in (301, 302, 303, 307, 308):
                        location = response.headers.get("Location", "")
                        if not location:
                            raise FeedError("redirect", "重定向缺少 Location")
                        current = urljoin(current, location)
                        continue
                    if response.status != 200:
                        raise FeedError("status", f"订阅源返回 HTTP {response.status}")
                    length = response.content_length
                    if length is not None and length > max_bytes:
                        raise FeedError("body_too_large", f"响应体超过 {max_bytes // 1024} KB 上限")
                    body = await _read_limited(response, max_bytes)
                    break
            except TimeoutError as exc:  # aiohttp 的超时也是 asyncio.TimeoutError
                raise FeedError("timeout", "RSS 请求超时") from exc
            except aiohttp.ClientError as exc:
                raise FeedError("request", f"RSS 请求失败：{exc.__class__.__name__}") from exc
        else:
            raise FeedError("redirect", f"重定向次数超过 {max_redirects} 次")

    return await asyncio.to_thread(parse_feed_bytes, body, current, summary_limit)


def parse_feed_bytes(data: bytes, base_url: str, summary_limit: int = 500) -> ParsedFeed:
    """解析 RSS/Atom 字节流。"""
    parsed = feedparser.parse(io.BytesIO(data))
    feed = parsed.get("feed") or {}
    entries = parsed.get("entries") or []
    raw_title = sanitize_rss_title(_feed_meta_text(feed.get("title")))
    feed_title = raw_title or "未命名订阅"
    items: list[FeedItem] = []
    for index, entry in enumerate(entries):
        try:
            items.append(_normalize_entry(feed_title, entry, index, summary_limit, base_url))
        except Exception:
            # 单条目异常不应让整个订阅失败
            continue
    version = (parsed.get("version") or "").strip()
    if not version and not items:
        # 拿到的通常是普通网页（例如 404 页面），直接报解析失败而不是建一个空订阅
        raise FeedError("parse", "不是有效的 RSS/Atom 订阅源")
    return ParsedFeed(title=feed_title, items=items)


def _feed_meta_text(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return str(value.get("value") or "")
    return ""


def sanitize_rss_title(raw: str, limit: int = RSS_TITLE_MAX_CHARS) -> str | None:
    """标题必须保持单行短文本语义，空值/占位值返回 None。"""
    text = sanitize_inline(_feed_meta_text(raw), limit)
    if not text or text.strip().lower() in ("null", "none", "undefined", "(null)"):
        return None
    return text


def _to_iso(value) -> str | None:
    """把 feedparser 的 struct_time（UTC）转成 RFC3339。"""
    if not value:
        return None
    try:
        return datetime(*value[:6], tzinfo=timezone.utc).isoformat()
    except (TypeError, ValueError):
        return None


def _entry_link(entry) -> str | None:
    links = entry.get("links") or []
    for link in links:
        href = (link.get("href") or "").strip()
        if href.startswith(("http://", "https://")):
            return href
    link = (entry.get("link") or "").strip()
    if link.startswith(("http://", "https://")):
        return link
    return None


def _raw_summary(entry) -> tuple[str, str]:
    """返回 (summary 原文, content 原文)。"""
    summary = entry.get("summary") or ""
    content = ""
    contents = entry.get("content") or []
    if contents:
        content = contents[0].get("value") or ""
    return _feed_meta_text(summary), _feed_meta_text(content)


def _clean_summary(raw_summary: str, raw_content: str, limit: int) -> str | None:
    """摘要优先用 summary，缺失或清洗后为空再回退到 content。"""
    for raw in (raw_summary, raw_content):
        if not raw or not raw.strip():
            continue
        markdown = html_to_markdown(raw) if "<" in raw else raw
        cleaned = to_qq_markdown(markdown, limit)
        if cleaned and cleaned.strip().lower() not in ("null", "none", "undefined"):
            return cleaned
    return None


def _stable_item_key(
    entry, feed_title: str, link: str | None, title: str, published: str | None
) -> str:
    entry_id = (entry.get("id") or "").strip()
    if entry_id:
        return f"id:{entry_id}"
    if link:
        return f"link:{link}"
    seed = f"{feed_title}|{title}|{published or ''}"
    return "fallback:" + hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _revision_hash(updated: str | None, title: str, raw_summary: str, raw_content: str) -> str:
    """修订指纹只包含源站内容，不能混进抓取时间，否则每轮都会被判成"有更新"。"""
    parts = [
        updated or "",
        title,
        html_to_text(raw_summary)[:_REVISION_TEXT_MAX_CHARS],
        html_to_text(raw_content)[:_REVISION_TEXT_MAX_CHARS],
    ]
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()


def _normalize_entry(
    feed_title: str, entry, index: int, summary_limit: int, base_url: str
) -> FeedItem:
    raw_title = entry.get("title") or ""
    title = sanitize_rss_title(raw_title) or "无标题"
    link = _entry_link(entry)
    if link and base_url and link.startswith("/"):
        link = urljoin(base_url, link)
    # feedparser 会把缺失的 updated 悄悄映射成 published，用 in 判断才能区分
    # 「源站只给了发布时间」和「源站明确给了更新时间」，从而选对展示标签。
    published = _to_iso(entry.get("published_parsed")) if "published" in entry else None
    updated = _to_iso(entry.get("updated_parsed")) if "updated" in entry else None
    raw_summary, raw_content = _raw_summary(entry)
    summary = _clean_summary(raw_summary, raw_content, summary_limit)
    return FeedItem(
        item_key=_stable_item_key(entry, feed_title, link, title, published or updated),
        revision_hash=_revision_hash(updated or published, title, raw_summary, raw_content),
        title=title,
        link=link,
        summary=summary,
        published_at=published,
        updated_at=updated,
        source_order=index,
    )
