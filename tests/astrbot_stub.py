"""测试用的 astrbot 轻量替身。

插件本身依赖 AstrBot 运行时（`astrbot.api.*`），但绝大多数逻辑（解析、去重、排版）
不应该依赖宿主。为了在没有 AstrBot 的环境里跑测试，这里往 `sys.modules` 注入
最小的 `astrbot` 模块：`logger`、`AstrBotConfig`、`Star`、`StarTools`、
`filter.command`、`MessageChain`、`Plain`。行为只覆盖插件真正用到的接口。
"""

from __future__ import annotations

import logging
import sys
import types
from pathlib import Path


class Plain:
    def __init__(self, text: str = "") -> None:
        self.text = text

    def __repr__(self) -> str:  # pragma: no cover
        return f"Plain({self.text!r})"


class MessageChain:
    def __init__(self, chain=None, **kwargs) -> None:
        self.chain = list(chain or [])
        self.use_markdown_ = None
        self.use_t2i_ = None

    def message(self, text: str):
        self.chain.append(Plain(text))
        return self

    def use_markdown(self, use: bool | None = True):
        self.use_markdown_ = use
        return self

    def get_plain_text(self) -> str:
        return " ".join(comp.text for comp in self.chain if isinstance(comp, Plain))


class AstrBotConfig(dict):
    def save_config(self) -> None:  # pragma: no cover
        pass


class Star:
    def __init__(self, context=None) -> None:
        self.context = context


class StarTools:
    data_root = Path(".")

    @classmethod
    def get_data_dir(cls, plugin_name: str | None = None) -> Path:
        target = cls.data_root / (plugin_name or "plugin")
        target.mkdir(parents=True, exist_ok=True)
        return target


class Context:
    """记录 send_message 调用的假上下文。"""

    def __init__(self) -> None:
        self.sent: list[tuple[str, MessageChain]] = []
        self.fail_with: Exception | None = None
        self.provider = None

    async def send_message(self, session, message_chain) -> bool:
        if self.fail_with is not None:
            raise self.fail_with
        self.sent.append((session, message_chain))
        return True

    async def get_using_provider_async(self, umo=None):
        return self.provider


class _Filter:
    @staticmethod
    def command(*args, **kwargs):
        def decorator(func):
            func.__command__ = (args, kwargs)
            return func

        return decorator

    @staticmethod
    def event_message_type(*args, **kwargs):  # pragma: no cover
        def decorator(func):
            return func

        return decorator

    @staticmethod
    def permission_type(*args, **kwargs):  # pragma: no cover
        def decorator(func):
            return func

        return decorator


class AstrMessageEvent:
    """最小事件替身，字段与插件用到的方法保持一致。"""

    def __init__(
        self,
        message_str: str = "",
        *,
        umo: str = "qq_official:GroupMessage:g1",
        group_id: str = "g1",
        sender_id: str = "u1",
        platform: str = "qq_official",
        admin: bool = False,
    ) -> None:
        self.message_str = message_str
        self.unified_msg_origin = umo
        self._group_id = group_id
        self._sender_id = sender_id
        self._platform = platform
        self._admin = admin

    def get_group_id(self) -> str:
        return self._group_id

    def get_sender_id(self) -> str:
        return self._sender_id

    def get_platform_name(self) -> str:
        return self._platform

    def is_admin(self) -> bool:
        return self._admin

    def plain_result(self, text: str) -> MessageChain:
        return MessageChain([Plain(text)])


def install() -> None:
    if "astrbot.api" in sys.modules:
        return

    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    api.logger = logging.getLogger("astrbot.test")
    api.AstrBotConfig = AstrBotConfig

    components = types.ModuleType("astrbot.api.message_components")
    components.Plain = Plain

    event_module = types.ModuleType("astrbot.api.event")
    event_module.AstrMessageEvent = AstrMessageEvent
    event_module.MessageChain = MessageChain
    event_module.filter = _Filter

    star_module = types.ModuleType("astrbot.api.star")
    star_module.Context = Context
    star_module.Star = Star
    star_module.StarTools = StarTools

    imports_module = types.ModuleType("astrbot.api.imports")
    imports_module.AstrBotConfig = AstrBotConfig
    imports_module.MessageChain = MessageChain

    sys.modules.update(
        {
            "astrbot": astrbot,
            "astrbot.api": api,
            "astrbot.api.message_components": components,
            "astrbot.api.event": event_module,
            "astrbot.api.star": star_module,
            "astrbot.api.imports": imports_module,
        }
    )
