"""后台轮询调度。

对应 qq-maid-bot 的 `rss/scheduler.rs`：只有一个循环，逐个处理启用中的订阅，
避免同一订阅被并发拉取；网络请求不做任何全局锁持有；条目只有在发送成功后才
写 `pushed_at`，发送失败会累计次数，超过上限就放弃，防止一条坏数据把日志刷满。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from .feed import FeedError, fetch_feed
from .models import StoredItem, Subscription
from .store import RssStore


@dataclass(slots=True)
class SchedulerConfig:
    enabled: bool = True
    interval_seconds: int = 300
    max_push_per_feed: int = 3
    max_failures: int = 3
    summary_max_chars: int = 500
    seen_retention: int = 500
    fetch_timeout: float = 15.0
    max_body_bytes: int = 2 * 1024 * 1024
    max_redirects: int = 4
    user_agent: str = "astrbot-rss-pusher/1.0"
    allow_private: bool = False
    stagger_seconds: float = 0.3


class RssScheduler:
    def __init__(
        self,
        store: RssStore,
        sender,
        translator,
        config: SchedulerConfig,
        logger,
        fetcher=fetch_feed,
    ) -> None:
        self.store = store
        self.sender = sender
        self.translator = translator
        self.config = config
        self.logger = logger
        self._fetcher = fetcher
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ 主循环
    async def run_loop(self, *, initial_delay: float = 10.0) -> None:
        """启动后延迟一轮再开始，避免和 AstrBot 启动竞争 IO。"""
        try:
            await asyncio.sleep(initial_delay)
            while True:
                started = time.monotonic()
                try:
                    await self.run_once()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self.logger.exception("[rss] 本轮检查异常，将在下一轮重试")
                elapsed = time.monotonic() - started
                wait = max(10.0, self.config.interval_seconds - elapsed)
                await asyncio.sleep(wait)
        except asyncio.CancelledError:
            self.logger.info("[rss] 轮询任务已停止")

    async def run_once(self) -> tuple[int, int]:
        """检查所有启用中的订阅，返回 (订阅数, 推送成功条数)。"""
        async with self._lock:
            subscriptions = await self.store.all_enabled()
            if not subscriptions:
                return 0, 0
            pushed = 0
            for index, subscription in enumerate(subscriptions):
                if index and self.config.stagger_seconds > 0:
                    # 同一轮里错开一点，避免同时打满某个源站
                    await asyncio.sleep(self.config.stagger_seconds)
                try:
                    pushed += await self.process_subscription(subscription)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self.logger.exception(f"[rss] 处理订阅失败：{subscription.url}")
            return len(subscriptions), pushed

    # -------------------------------------------------------------- 单条订阅
    async def process_subscription(self, subscription: Subscription) -> int:
        try:
            feed = await self._fetcher(
                subscription.url,
                timeout=self.config.fetch_timeout,
                max_bytes=self.config.max_body_bytes,
                max_redirects=self.config.max_redirects,
                user_agent=self.config.user_agent,
                allow_private=self.config.allow_private,
                summary_limit=self.config.summary_max_chars,
            )
        except FeedError as exc:
            await self.store.record_check_failure(subscription.id, exc.message)
            self.logger.warning(f"[rss] 订阅抓取失败（{subscription.url}）：{exc.message}")
            return 0
        except Exception as exc:
            await self.store.record_check_failure(
                subscription.id, f"{exc.__class__.__name__}: {exc}"
            )
            self.logger.exception(f"[rss] 订阅抓取异常：{subscription.url}")
            return 0

        new_count = await self.store.enqueue_items(
            subscription.id, feed.items, self.config.seen_retention
        )
        await self.store.record_check_success(subscription.id, feed.title)
        if new_count:
            self.logger.info(f"[rss] {subscription.title} 检测到 {new_count} 条新条目")

        pending = await self.store.pending_items(
            subscription.id,
            self.config.max_push_per_feed,
            self.config.max_failures,
        )
        pushed = 0
        for item in pending:
            if await self._push(subscription, item):
                pushed += 1
        return pushed

    async def _push(self, subscription: Subscription, item: StoredItem) -> bool:
        display_item = item
        try:
            display_item = await self.translator.translate_item(subscription.scope_key, item)
        except Exception:
            self.logger.exception("[rss] 翻译异常，改用原文推送")

        ok, error = await self.sender.send(subscription, display_item)
        if ok:
            await self.store.mark_pushed(subscription.id, item.item_key)
            self.logger.info(f"[rss] 已推送 {subscription.title} → {display_item.title}")
            return True

        await self.store.record_push_failure(subscription.id, item.item_key, error or "未知错误")
        self.logger.warning(f"[rss] 推送失败（{subscription.title} / {item.title}）：{error}")
        return False
