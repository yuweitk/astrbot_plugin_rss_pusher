"""推送发送器：把一条 RSS 更新发到订阅所在的会话。

QQ 官方的 Markdown 消息（`msg_type=2`）由适配器在发送时组装，插件这边只需要
构造一个 ``use_markdown(True)`` 的消息链：

    chain = MessageChain([Comp.Plain(markdown_text)])
    chain.use_markdown(True)
    await context.send_message(umo, chain)

AstrBot 的 qq_official / qq_official_webhook 适配器会读 ``use_markdown_`` 标记；
官方接口拒绝 Markdown 时适配器自身也会回落成普通文本。这里额外再做一层兜底：
Markdown 发送抛异常时，改用纯文本重发一次。
"""

from __future__ import annotations

from astrbot.api import logger as astrbot_logger
from astrbot.api.event import MessageChain
from astrbot.api.message_components import Plain

from .formatter import format_push_markdown, format_push_text
from .models import StoredItem, Subscription


class PushSender:
    def __init__(
        self,
        context,
        *,
        message_format: str = "auto",
        markdown_platforms: list[str] | None = None,
        offset_hours: float = 8.0,
        max_chars: int = 3600,
    ) -> None:
        self.context = context
        self.message_format = (message_format or "auto").strip().lower()
        self.markdown_platforms = {
            platform.strip().lower() for platform in (markdown_platforms or []) if platform.strip()
        }
        self.offset_hours = offset_hours
        self.max_chars = max_chars
        self.logger = astrbot_logger

    def use_markdown(self, platform: str) -> bool:
        if self.message_format == "markdown":
            return True
        if self.message_format == "text":
            return False
        name = (platform or "").strip().lower()
        if name in self.markdown_platforms:
            return True
        # 兼容 "qq_official_v2-xxxx" 这类带实例后缀的 platform_id
        return any(name.startswith(f"{item}") for item in self.markdown_platforms)

    async def send(self, subscription: Subscription, item: StoredItem) -> tuple[bool, str | None]:
        """发送一条更新，返回 (是否成功, 错误信息)。"""
        markdown = self.use_markdown(subscription.platform)
        if markdown:
            text = format_push_markdown(
                subscription.title,
                item,
                offset_hours=self.offset_hours,
                max_chars=self.max_chars,
            )
            ok, error = await self._send_once(subscription.scope_key, text, markdown=True)
            if ok:
                return True, None
            # Markdown 失败时退回纯文本，保证消息尽量送到
            self.logger.warning(f"[rss] Markdown 推送失败，改用纯文本重试：{error}")
        text = format_push_text(
            subscription.title,
            item,
            offset_hours=self.offset_hours,
            max_chars=self.max_chars,
        )
        return await self._send_once(subscription.scope_key, text, markdown=False)

    async def _send_once(
        self, scope_key: str, text: str, *, markdown: bool
    ) -> tuple[bool, str | None]:
        chain = MessageChain([Plain(text)])
        # use_markdown 是较新版本 AstrBot 才有的接口，老版本上跳过标记即可
        if markdown and hasattr(chain, "use_markdown"):
            chain.use_markdown(True)
        try:
            found = await self.context.send_message(scope_key, chain)
        except Exception as exc:
            return False, f"{exc.__class__.__name__}: {exc}"
        if found is False:
            return False, "找不到对应的消息平台会话"
        return True, None
