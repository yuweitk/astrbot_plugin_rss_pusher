"""astrbot_plugin_rss_pusher —— RSS/Atom 订阅与 QQ Markdown 主动推送。

能力与指令设计移植自 `kuliantnt/qq-maid-bot` 的 RSS 模块（`/rss` 指令、
`feed.rs` 抓取解析、`scheduler.rs` 轮询、`qq.rs` 的 QQ Markdown 子集渲染），
按 AstrBot 插件规范（`data/plugins/<plugin>/main.py` + `Star` 类）重写：

- 指令：`/rss`、`/rss add`、`/rss delete`、`/rss recent`、`/rss test`、`/rss on|off`；
- 订阅按 unified_msg_origin 隔离，群里的订阅只推该群，私聊只推本人；
- 后台单循环轮询，条目按 `item_key` 去重、按 `revision_hash` 识别内容更新；
- 推送使用 QQ 官方 Markdown（`use_markdown(True)`），失败自动回落纯文本。
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools

from .core import commands as cmd
from .core.feed import FeedError, fetch_feed
from .core.formatter import (
    format_add_batch,
    format_add_single,
    format_help,
    format_recent,
    format_subscription_list,
)
from .core.models import Subscription
from .core.qqmd import truncate_chars
from .core.scheduler import RssScheduler, SchedulerConfig
from .core.sender import PushSender
from .core.store import RssStore
from .core.translate import FeedTranslator

PLUGIN_NAME = "astrbot_plugin_rss_pusher"
_TRIM_CHARS = " \t\r\n。，,；;）)]】\"'"


def _cfg(config, *keys, default=None):
    """安全读取嵌套配置，缺项时给默认值。"""
    node = config
    for key in keys:
        if not hasattr(node, "get"):
            return default
        value = node.get(key)
        if value is None:
            return default
        node = value
    return node


def feed_error_text(exc: FeedError) -> str:
    """把 FeedError 转成用户能看懂的一句话。"""
    if exc.code == "status":
        return exc.message
    if exc.code == "unsafe_host":
        return "地址指向本机、内网或 metadata，已拦截"
    if exc.code == "timeout":
        return "请求超时，稍后再试"
    if exc.code == "body_too_large":
        return "订阅源响应过大，已放弃"
    if exc.code == "empty_url":
        return "请填写 RSS 地址"
    if exc.code == "bad_scheme":
        return "只支持 http/https 的 RSS 地址"
    if exc.code == "dns":
        return "域名解析失败"
    return exc.message


class RssPusherPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config = config or {}
        self.logger = logger

        data_dir = Path(StarTools.get_data_dir(PLUGIN_NAME))
        self.store = RssStore(data_dir / "rss.db")

        self.scheduler_config = self._build_scheduler_config()
        self.sender = PushSender(
            context,
            message_format=_cfg(self.config, "push", "message_format", default="auto"),
            markdown_platforms=_cfg(self.config, "push", "markdown_platforms", default=[]),
            offset_hours=float(_cfg(self.config, "commands", "timezone_offset_hours", default=8.0)),
            max_chars=int(_cfg(self.config, "push", "max_message_chars", default=3600)),
        )
        self.translator = FeedTranslator(
            context,
            enabled=bool(_cfg(self.config, "translate", "enabled", default=False)),
            max_chars=int(_cfg(self.config, "translate", "max_chars", default=400)),
            logger=self.logger,
        )
        self.scheduler = RssScheduler(
            self.store,
            self.sender,
            self.translator,
            self.scheduler_config,
            self.logger,
        )

        self._task: asyncio.Task | None = None
        if self.scheduler_config.enabled:
            try:
                self._task = asyncio.create_task(self.scheduler.run_loop())
                self.logger.info(
                    f"[rss] 轮询已启动，间隔 {self.scheduler_config.interval_seconds}s"
                )
            except RuntimeError:
                # 极少数情况下插件在无事件循环的线程里被实例化
                self.logger.warning("[rss] 事件循环未就绪，后台轮询未启动")

    # ------------------------------------------------------------------ 配置
    def _build_scheduler_config(self) -> SchedulerConfig:
        return SchedulerConfig(
            enabled=bool(_cfg(self.config, "enabled", default=True)),
            interval_seconds=max(10, int(_cfg(self.config, "poll_interval_seconds", default=300))),
            max_push_per_feed=max(1, int(_cfg(self.config, "push", "max_per_feed", default=3))),
            max_failures=max(1, int(_cfg(self.config, "push", "max_failures", default=3))),
            summary_max_chars=max(
                50, int(_cfg(self.config, "push", "summary_max_chars", default=500))
            ),
            seen_retention=max(
                20, int(_cfg(self.config, "storage", "seen_retention", default=500))
            ),
            fetch_timeout=float(
                max(3, int(_cfg(self.config, "fetch", "timeout_seconds", default=15)))
            ),
            max_body_bytes=max(64, int(_cfg(self.config, "fetch", "max_body_kb", default=2048)))
            * 1024,
            max_redirects=max(0, int(_cfg(self.config, "fetch", "max_redirects", default=4))),
            user_agent=str(
                _cfg(
                    self.config,
                    "fetch",
                    "user_agent",
                    default="astrbot-rss-pusher/1.0 (+https://github.com/AstrBotDevs/AstrBot)",
                )
            ),
            allow_private=bool(_cfg(self.config, "fetch", "allow_private_network", default=False)),
        )

    @property
    def offset_hours(self) -> float:
        return float(_cfg(self.config, "commands", "timezone_offset_hours", default=8.0))

    def _use_markdown(self, event: AstrMessageEvent) -> bool:
        if not bool(_cfg(self.config, "commands", "markdown_replies", default=True)):
            return False
        return self.sender.use_markdown(event.get_platform_name())

    # ------------------------------------------------------------------ 指令
    @filter.command("rss", alias={"订阅", "rss订阅"})
    async def rss_command(self, event: AstrMessageEvent):
        """RSS 订阅管理：/rss、/rss add 地址、/rss delete 编号、/rss recent、/rss test 地址"""
        markdown = self._use_markdown(event)
        try:
            argument = cmd.extract_argument(event.message_str)
            action, rest = cmd.classify(argument)
            if action == "list":
                text = await self._handle_list(event, markdown)
            elif action == "add":
                text = await self._handle_add(event, rest)
            elif action == "delete":
                text = await self._handle_delete(event, rest)
            elif action == "recent":
                text = await self._handle_recent(event, rest, markdown)
            elif action == "test":
                text = await self._handle_test(rest)
            elif action in ("enable", "disable"):
                text = await self._handle_toggle(event, rest, action == "enable")
            else:
                text = format_help(markdown)
        except Exception:
            self.logger.exception("[rss] 指令处理失败")
            text = "处理 RSS 指令时出错，请查看 AstrBot 日志。"
        result = event.plain_result(text)
        if hasattr(result, "use_markdown"):
            # 显式设置：False 时即使平台默认发 Markdown，也按纯文本排版发出去
            result.use_markdown(markdown)
        yield result

    # -------------------------------------------------------------- 各子指令
    async def _handle_list(self, event: AstrMessageEvent, markdown: bool) -> str:
        subscriptions = await self.store.list_by_scope(event.unified_msg_origin)
        return format_subscription_list(
            subscriptions, markdown=markdown, offset_hours=self.offset_hours
        )

    async def _handle_add(self, event: AstrMessageEvent, argument: str) -> str:
        denied = self._admin_denied(event)
        if denied:
            return denied
        entries = cmd.parse_add_arguments(argument)
        if not entries:
            return "用法：/rss add RSS地址 [名称]；也支持多行：一行标题，下一行 RSS 地址。"

        scope_key = event.unified_msg_origin
        existing = await self.store.list_by_scope(scope_key)
        existing_urls = {sub.url for sub in existing}
        limit = max(1, int(_cfg(self.config, "commands", "max_per_session", default=20)))

        if len(entries) == 1 and len(existing) >= limit:
            return f"当前会话订阅数已达上限（{limit} 个），请先删除一些订阅。"

        target_type = "group" if event.get_group_id() else "private"
        target_id = event.get_group_id() or event.get_sender_id()
        created: list[tuple[Subscription, int]] = []
        failed: list[tuple[str, str]] = []

        for entry in entries:
            url = entry.url.strip(_TRIM_CHARS)
            if url in existing_urls:
                failed.append((url, "该地址已经订阅过了"))
                continue
            if len(existing) + len(created) >= limit:
                failed.append((url, f"已达到单会话订阅上限（{limit} 个）"))
                continue
            try:
                feed = await self._fetch(url)
            except FeedError as exc:
                failed.append((url, feed_error_text(exc)))
                continue
            except Exception as exc:  # 网络层的意外错误
                failed.append((url, f"{exc.__class__.__name__}"))
                continue

            title = entry.name or feed.title
            try:
                subscription = await self.store.create_subscription(
                    scope_key=scope_key,
                    platform=event.get_platform_name(),
                    target_type=target_type,
                    target_id=str(target_id),
                    url=url,
                    title=title,
                    items=feed.items,
                    seen_retention=self.scheduler_config.seen_retention,
                    title_locked=bool(entry.name),
                )
            except Exception as exc:
                if "UNIQUE" in str(exc).upper():
                    failed.append((url, "该地址已经订阅过了"))
                else:
                    failed.append((url, f"保存失败：{exc.__class__.__name__}"))
                continue
            existing_urls.add(url)
            created.append((subscription, len(feed.items)))

        if len(entries) == 1:
            if created:
                subscription, item_count = created[0]
                return format_add_single(subscription, item_count)
            url, message = failed[0]
            return f"RSS 地址无法访问或无法解析：{message}\n地址：{url}"
        return format_add_batch(created, failed)

    async def _handle_delete(self, event: AstrMessageEvent, argument: str) -> str:
        denied = self._admin_denied(event)
        if denied:
            return denied
        targets = cmd.parse_delete_arguments(argument)
        if not targets:
            return "用法：/rss delete 编号或订阅ID；支持 `1 3` 或 `1,3` 批量删除。"

        scope_key = event.unified_msg_origin
        subscriptions = await self.store.list_by_scope(scope_key)
        resolved: list[Subscription] = []
        missing: list[str] = []
        for target in targets:
            subscription = cmd.resolve_subscription(subscriptions, target)
            if subscription is None:
                missing.append(target)
            elif all(item.id != subscription.id for item in resolved):
                resolved.append(subscription)

        if not resolved:
            return "没有找到当前会话下对应的 RSS 订阅。"

        deleted: list[str] = []
        for subscription in resolved:
            if await self.store.delete_for_scope(scope_key, subscription.id):
                deleted.append(subscription.title)

        if len(targets) == 1 and not missing:
            if deleted:
                return f"已删除 RSS 订阅：{deleted[0]}"
            return "没有找到当前会话下对应的 RSS 订阅。"

        rows = [f"已删除 {len(deleted)} 个 RSS 订阅："]
        for index, title in enumerate(deleted, start=1):
            rows.append(f"{index}. {truncate_chars(title, 60)}")
        if missing:
            rows.append(f"未找到 {len(missing)} 个目标：{'、'.join(missing)}")
        return "\n".join(rows)

    async def _handle_recent(self, event: AstrMessageEvent, argument: str, markdown: bool) -> str:
        try:
            limit = cmd.parse_recent_limit(argument)
        except ValueError as exc:
            return str(exc)
        subscriptions = await self.store.list_by_scope(event.unified_msg_origin)
        items = await self.store.recent_items(event.unified_msg_origin, limit)
        return format_recent(
            subscriptions,
            items,
            markdown=markdown,
            offset_hours=self.offset_hours,
            max_chars=int(_cfg(self.config, "push", "max_message_chars", default=3600)),
        )

    async def _handle_test(self, argument: str) -> str:
        url = cmd.looks_like_url_argument(argument)
        if not url:
            return "用法：/rss test RSS地址"
        try:
            feed = await self._fetch(url)
        except FeedError as exc:
            return f"RSS 测试失败：{feed_error_text(exc)}"
        except Exception as exc:
            return f"RSS 测试失败：{exc.__class__.__name__}"
        return f"RSS 测试成功：{feed.title}\n当前条目数：{len(feed.items)}"

    async def _handle_toggle(self, event: AstrMessageEvent, argument: str, enabled: bool) -> str:
        denied = self._admin_denied(event)
        if denied:
            return denied
        targets = cmd.parse_delete_arguments(argument)
        if not targets:
            return f"用法：/rss {'on' if enabled else 'off'} 编号"
        scope_key = event.unified_msg_origin
        subscriptions = await self.store.list_by_scope(scope_key)
        changed: list[str] = []
        missing: list[str] = []
        for target in targets:
            subscription = cmd.resolve_subscription(subscriptions, target)
            if subscription is None:
                missing.append(target)
                continue
            if await self.store.set_enabled(scope_key, subscription.id, enabled):
                # 本地对象同步一下状态，避免回复里展示旧值
                subscription.enabled = enabled
                changed.append(subscription.title)
        action = "恢复" if enabled else "暂停"
        rows = []
        if changed:
            rows.append(f"已{action} {len(changed)} 个订阅：")
            rows.extend(
                f"{index}. {truncate_chars(title, 60)}"
                for index, title in enumerate(changed, start=1)
            )
        if missing:
            rows.append(f"未找到 {len(missing)} 个目标：{'、'.join(missing)}")
        return "\n".join(rows) if rows else "没有找到当前会话下对应的 RSS 订阅。"

    # ------------------------------------------------------------------ 工具
    def _admin_denied(self, event: AstrMessageEvent) -> str | None:
        """群聊里限制增删权限，和 qq-maid-bot 的 group_management_allowed 对齐。"""
        if not event.get_group_id():
            return None
        if not bool(_cfg(self.config, "commands", "group_admin_only", default=True)):
            return None
        try:
            if event.is_admin():
                return None
        except Exception:
            return None
        return "群里只有管理员可以添加、删除或启停 RSS 订阅。"

    async def _fetch(self, url: str):
        return await fetch_feed(
            url,
            timeout=self.scheduler_config.fetch_timeout,
            max_bytes=self.scheduler_config.max_body_bytes,
            max_redirects=self.scheduler_config.max_redirects,
            user_agent=self.scheduler_config.user_agent,
            allow_private=self.scheduler_config.allow_private,
            summary_limit=self.scheduler_config.summary_max_chars,
        )

    async def terminate(self):
        """插件停用/重载时收尾：取消轮询任务并关闭数据库。"""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None
        self.store.close()
        self.logger.info("[rss] 插件已卸载")
