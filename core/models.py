"""插件内部的数据结构。

对应 qq-maid-bot 的 `qq-maid-core/src/runtime/tools/rss/storage`：订阅、已见条目
与待推送条目都用纯 dataclass 表达，方便在数据库、调度器和指令层之间传递。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class FeedItem:
    """Feed 中的单条目（已规范化，不含 HTML）。"""

    item_key: str
    """稳定标识：id/guid 优先，其次链接，最后是标题+时间的哈希。"""
    revision_hash: str
    """内容修订指纹，用于识别"同一篇文章被编辑过"。"""
    title: str
    link: str | None
    summary: str | None
    """已渲染为 QQ Markdown 安全子集的摘要，可能为 None。"""
    published_at: str | None
    """RFC3339 字符串或 None。"""
    updated_at: str | None
    source_order: int
    """在本次解析结果中的顺序，用于排序兜底。"""


@dataclass(slots=True)
class ParsedFeed:
    title: str
    items: list[FeedItem] = field(default_factory=list)


@dataclass(slots=True)
class Subscription:
    """一条订阅。scope_key 是 AstrBot 的 unified_msg_origin。"""

    id: str
    scope_key: str
    platform: str
    target_type: str
    """group / private。"""
    target_id: str
    url: str
    title: str
    enabled: bool = True
    created_at: str = ""
    last_checked_at: str | None = None
    last_success_at: str | None = None
    last_error: str | None = None
    consecutive_failures: int = 0

    @property
    def failed(self) -> bool:
        return bool(self.last_error and self.last_error.strip())


@dataclass(slots=True)
class StoredItem:
    """数据库中待推送（或已推送）的条目。"""

    item_key: str
    revision_hash: str
    title: str
    link: str | None
    summary: str | None
    published_at: str | None
    updated_at: str | None
    first_seen_at: str
    pushed_at: str | None = None
    push_attempts: int = 0
    last_push_error: str | None = None

    @property
    def display_time(self) -> tuple[str, str] | None:
        """返回 (展示标签, 时间字符串)：优先发布时间，缺失时用更新时间。"""
        if self.published_at and self.published_at.strip():
            return ("发布时间", self.published_at)
        if self.updated_at and self.updated_at.strip():
            return ("更新时间", self.updated_at)
        return None
