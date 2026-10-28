"""`/rss` 指令的解析逻辑。

从 qq-maid-bot 的 `rss_flow.rs` 移植，保持同样的行为：

- `/rss` 不带参数 = 列表；
- 未知子命令给用法提示，而不是默默当成列表；
- `/rss add` 既支持单行 `地址 [名称]`，也支持「一行名称、下一行地址」的多行写法，
  还会吃掉行首的 `1.` `2、` 这类编号（用户从列表里直接复制过来）；
- `/rss delete` 支持编号、ID 前缀，以及空格/逗号分隔的批量删除。

这些函数都是纯函数，方便单测。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Subscription

COMMAND_ALIASES = ("rss订阅", "订阅", "rss")
"""指令名与别名，长的放前面，避免 "rss" 抢先匹配 "rss订阅"。"""

_URL_RE = re.compile(r"https?://[^\s<>]+")
_LEADING_PREFIX_RE = re.compile(r"^[\s/／!！.。]+")

ACTIONS = {
    "list": "list",
    "ls": "list",
    "列表": "list",
    "查看": "list",
    "recent": "recent",
    "updates": "recent",
    "update": "recent",
    "最近": "recent",
    "更新": "recent",
    "add": "add",
    "new": "add",
    "create": "add",
    "添加": "add",
    "新增": "add",
    "订阅": "add",
    "delete": "delete",
    "del": "delete",
    "rm": "delete",
    "remove": "delete",
    "删除": "delete",
    "取消订阅": "delete",
    "test": "test",
    "测试": "test",
    "on": "enable",
    "enable": "enable",
    "启用": "enable",
    "开启": "enable",
    "off": "disable",
    "disable": "disable",
    "停用": "disable",
    "暂停": "disable",
    "关闭": "disable",
    "help": "help",
    "帮助": "help",
    "?": "help",
}

RECENT_DEFAULT_LIMIT = 5
RECENT_MAX_LIMIT = 20


@dataclass(slots=True)
class AddEntry:
    url: str
    name: str | None = None


def is_url(value: str) -> bool:
    return value.startswith("http://") or value.startswith("https://")


def extract_argument(message_str: str) -> str:
    """从整条消息里剥掉唤醒前缀和指令名，得到参数字符串（保留换行）。"""
    text = (message_str or "").strip()
    text = _LEADING_PREFIX_RE.sub("", text)
    lowered = text.lower()
    for alias in COMMAND_ALIASES:
        if not lowered.startswith(alias):
            continue
        rest = text[len(alias) :]
        if rest and not rest[0].isspace():
            continue  # 例如 "rss推送" 不是本指令
        return rest.strip()
    return text


def classify(argument: str) -> tuple[str, str]:
    """把参数拆成 (动作, 剩余参数)。"""
    argument = (argument or "").strip()
    if not argument:
        return "list", ""
    parts = argument.split(None, 1)
    first = parts[0]
    rest = parts[1].strip() if len(parts) > 1 else ""
    if is_url(first):
        # 用户直接 `/rss https://...`，按添加处理
        return "add", argument
    action = ACTIONS.get(first.lower())
    if action is None:
        return "help", argument
    return action, rest


def strip_list_marker(value: str) -> str:
    """去掉行首的 `1.` `2、` `3)` 之类编号。"""
    value = value.strip()
    match = re.match(r"^(\d+)\s*[.。、):：]\s*(.*)$", value)
    if match:
        return match.group(2).strip()
    return value


def extract_url_from_line(line: str) -> tuple[str, str, str] | None:
    """从一行里找出 (URL 前的文本, URL, URL 后的文本)。"""
    match = _URL_RE.search(line)
    if not match:
        return None
    url = match.group(0)
    before = line[: match.start()].strip()
    after = line[match.end() :].strip()
    return before, url, after


def _clean_name(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if text.lower() in ("null", "none", "undefined"):
        return None
    return text


def parse_add_arguments(argument: str) -> list[AddEntry] | None:
    """解析添加参数，返回 None 表示用法错误。"""
    argument = (argument or "").strip()
    if not argument:
        return None

    parts = argument.split(None, 1)
    first = parts[0]
    if is_url(first):
        return [AddEntry(url=first, name=_clean_name(parts[1] if len(parts) > 1 else None))]

    entries: list[AddEntry] = []
    pending_name: str | None = None
    for raw_line in argument.splitlines():
        line = strip_list_marker(raw_line.strip())
        if not line:
            continue
        found = extract_url_from_line(line)
        if found:
            before, url, after = found
            name = _clean_name(before) or pending_name or _clean_name(after)
            pending_name = None
            entries.append(AddEntry(url=url, name=name))
        else:
            pending_name = _clean_name(line)
    return entries or None


def parse_delete_arguments(argument: str) -> list[str]:
    tokens: list[str] = []
    for line in (argument or "").splitlines():
        for token in re.split(r"[\s,，]+", line):
            token = strip_list_marker(token)
            if token:
                tokens.append(token)
    return tokens


def parse_recent_limit(argument: str) -> int:
    """解析数量；不合法时抛 ValueError（携带用法提示）。"""
    argument = (argument or "").strip()
    if not argument:
        return RECENT_DEFAULT_LIMIT
    parts = argument.split()
    if len(parts) > 1:
        raise ValueError(f"用法：/rss recent [数量]；数量需为 1～{RECENT_MAX_LIMIT}。")
    try:
        limit = int(parts[0])
    except ValueError as exc:
        raise ValueError(f"用法：/rss recent [数量]；数量需为 1～{RECENT_MAX_LIMIT}。") from exc
    if not 1 <= limit <= RECENT_MAX_LIMIT:
        raise ValueError(f"用法：/rss recent [数量]；数量需为 1～{RECENT_MAX_LIMIT}。")
    return limit


def resolve_subscription(subscriptions: list[Subscription], target: str) -> Subscription | None:
    """编号（从 1 开始）或 ID 前缀 → 订阅。"""
    token = target.split()[0] if target.split() else ""
    if not token:
        return None
    if token.isdigit():
        index = int(token)
        if 1 <= index <= len(subscriptions):
            return subscriptions[index - 1]
        return None
    for sub in subscriptions:
        if sub.id == token or sub.id.startswith(token):
            return sub
    return None


def looks_like_url_argument(argument: str) -> str | None:
    """取出参数里的第一个 URL，用于 `/rss test`。"""
    match = _URL_RE.search(argument or "")
    return match.group(0) if match else None
