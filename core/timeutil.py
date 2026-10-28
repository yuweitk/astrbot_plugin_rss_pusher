"""时间工具：数据库统一存 UTC，展示时按配置时区换算。

服务器时区不一定是东八区，而 RSS 源的时间戳五花八门（带时区、不带时区、RFC822、
ISO8601）。统一在入库前转成 UTC 的 ISO 字符串，展示时再换到用户配置的时区，
可以保证「最近更新」列表的排序在所有源之间是一致的。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def now_iso() -> str:
    """当前时间的 UTC ISO 字符串。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    # Python 3.11 之前的 fromisoformat 不认 "Z"
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def format_for_display(value: str | None, offset_hours: float = 8.0) -> str:
    """把 ISO 时间格式化为 `YYYY-MM-DD HH:MM`，无法解析时原样返回。"""
    parsed = parse_iso(value)
    if parsed is None:
        return (value or "").strip() or "未知时间"
    try:
        shifted = parsed.astimezone(timezone(timedelta(hours=offset_hours)))
    except (ValueError, OverflowError):
        shifted = parsed.astimezone(timezone.utc)
    return shifted.strftime("%Y-%m-%d %H:%M")
