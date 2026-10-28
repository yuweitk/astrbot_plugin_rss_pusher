"""消息排版。

推送模板与 qq-maid-bot 的 `format_push_markdown` / `format_push_message` 对齐，
另外为每条指令回复提供 Markdown 与纯文本两套排版：QQ 官方走 Markdown，其他平台
（例如 NapCat 的 aiocqhttp）走纯文本，避免用户看到一堆 `###`。
"""

from __future__ import annotations

from .models import StoredItem, Subscription
from .qqmd import markdown_link, sanitize_inline, to_plain_text, truncate_chars
from .timeutil import format_for_display

_TITLE_MAX = 120
_SUB_TITLE_MAX = 60
_RECENT_TITLE_MAX = 120


def _sub_title(raw: str) -> str:
    return sanitize_inline(raw, _SUB_TITLE_MAX) or "未命名订阅"


def _item_title(raw: str) -> str:
    return sanitize_inline(raw, _TITLE_MAX) or "无标题"


def format_push_markdown(
    subscription_title: str, item: StoredItem, *, offset_hours: float = 8.0, max_chars: int = 3600
) -> str:
    """QQ Markdown 推送模板。"""
    title = _item_title(item.title)
    sub = _sub_title(subscription_title)
    head = f"## RSS 更新：{sub}"
    body: list[str] = [head, ""]
    if item.link:
        body.append(f"### {markdown_link(title, item.link)}")
    else:
        body.append(f"### {title}")

    summary = (item.summary or "").strip()
    if summary:
        body.append("")
        body.append(summary)

    display = item.display_time
    if display:
        label, value = display
        body.append("")
        body.append(f"{label}：{format_for_display(value, offset_hours)}")

    if item.link:
        body.append("")
        body.append(f"原文：{markdown_link('查看条目', item.link)}")

    return _clip("\n".join(body), max_chars)


def format_push_text(
    subscription_title: str, item: StoredItem, *, offset_hours: float = 8.0, max_chars: int = 3600
) -> str:
    """纯文本推送模板（不支持 Markdown 的平台用）。"""
    title = _item_title(item.title)
    sub = _sub_title(subscription_title)
    lines = [f"【RSS 更新】{sub}", "", title]

    summary = to_plain_text(item.summary or "").strip()
    if summary:
        lines.append(summary)

    display = item.display_time
    if display:
        label, value = display
        lines.append(f"{label}：{format_for_display(value, offset_hours)}")

    if item.link:
        lines.append(f"链接：{item.link}")

    return _clip("\n".join(lines), max_chars)


def _clip(text: str, max_chars: int) -> str:
    if max_chars > 0 and len(text) > max_chars:
        return text[: max_chars - 1].rstrip() + "…"
    return text


def format_subscription_list(
    subscriptions: list[Subscription], *, markdown: bool = True, offset_hours: float = 8.0
) -> str:
    """`/rss` 订阅列表。"""
    if not subscriptions:
        if markdown:
            return "\n".join(
                [
                    "# 📰 RSS 订阅",
                    "",
                    "当前会话还没有订阅。",
                    "",
                    "## 快速操作",
                    "",
                    "- 添加订阅：`/rss add RSS地址 [名称]`",
                    "- 测试订阅源：`/rss test RSS地址`",
                    "- 查看最近更新：`/rss recent [数量]`",
                ]
            )
        return "\n".join(
            [
                "📰 RSS 订阅",
                "",
                "当前会话还没有订阅。",
                "",
                "添加订阅：/rss add RSS地址 [名称]",
                "测试订阅源：/rss test RSS地址",
                "查看最近更新：/rss recent [数量]",
            ]
        )

    if markdown:
        rows = ["# 📰 RSS 订阅", "", f"共 {len(subscriptions)} 个订阅", ""]
    else:
        rows = [f"📰 RSS 订阅（共 {len(subscriptions)} 个）", ""]

    for index, sub in enumerate(subscriptions, start=1):
        state = "✅ 已启用" if sub.enabled else "⏸️ 已停用"
        if markdown:
            rows.append(f"{index}. **{_sub_title(sub.title)}** · {state}")
            rows.append(f"   地址：{markdown_link('打开订阅源', sub.url)}")
            rows.append(f"   最近检查：{_check_time(sub.last_checked_at, offset_hours)}")
            if sub.failed:
                rows.append(f"   ⚠️ 最近错误：{sanitize_inline(sub.last_error or '', 80)}")
        else:
            rows.append(f"{index}. {_sub_title(sub.title)} · {state}")
            rows.append(f"   地址：{sub.url}")
            rows.append(f"   最近检查：{_check_time(sub.last_checked_at, offset_hours)}")
            if sub.failed:
                rows.append(f"   ⚠️ 最近错误：{truncate_chars(sub.last_error or '', 80)}")
        rows.append("")

    if markdown:
        rows.extend(
            [
                "## 快速操作",
                "",
                "- 查看更新：`/rss recent [数量]`",
                "- 添加订阅：`/rss add RSS地址 [名称]`",
                "- 删除订阅：`/rss delete 1`",
                "- 启停订阅：`/rss off 1`",
                "- 测试订阅源：`/rss test RSS地址`",
            ]
        )
    else:
        rows.extend(
            [
                "快速操作：",
                "查看更新 /rss recent [数量]",
                "添加订阅 /rss add RSS地址 [名称]",
                "删除订阅 /rss delete 1",
                "启停订阅 /rss off 1",
                "测试订阅源 /rss test RSS地址",
            ]
        )
    return "\n".join(rows).strip()


def _check_time(value: str | None, offset_hours: float) -> str:
    if not value:
        return "未检查"
    return format_for_display(value, offset_hours)


def format_recent(
    subscriptions: list[Subscription],
    items: list[tuple[str, StoredItem]],
    *,
    markdown: bool = True,
    offset_hours: float = 8.0,
    max_chars: int = 3600,
) -> str:
    """`/rss recent` 最近更新列表。"""
    if not subscriptions:
        return "当前会话还没有 RSS 订阅，可使用 /rss add 地址 添加。"
    if not items:
        reply = (
            "当前会话已有 RSS 订阅，但还没有抓到更新。可以等待后台检查，"
            "或使用 /rss test 地址 检查订阅源。"
        )
        failed = sum(1 for sub in subscriptions if sub.failed)
        if failed:
            reply += f"\n提示：{failed} 个订阅源最近检查失败，可能有更新延迟。"
        return reply

    rows = ["# 最近 RSS 更新", ""] if markdown else ["最近 RSS 更新：", ""]
    for index, (sub_title, item) in enumerate(items, start=1):
        title = sanitize_inline(item.title.replace("\n", " "), _RECENT_TITLE_MAX) or "无标题"
        sub = _sub_title(sub_title)
        if markdown:
            if item.link:
                rows.append(
                    f"{index}. [{sanitize_inline(sub, 40)}] {markdown_link(title, item.link)}"
                )
            else:
                rows.append(f"{index}. **{sanitize_inline(sub, 40)}** {title}")
            display = item.display_time
            if display:
                label, value = display
                rows.append(f"   {label}：{format_for_display(value, offset_hours)}")
        else:
            rows.append(f"{index}. [{sub}] {title}")
            if item.link:
                rows.append(f"   {item.link}")
            display = item.display_time
            if display:
                label, value = display
                rows.append(f"   {label}：{format_for_display(value, offset_hours)}")
        rows.append("")

    failed = sum(1 for sub in subscriptions if sub.failed)
    if failed:
        rows.append(f"提示：{failed} 个订阅源最近检查失败，可能有更新延迟。")
    return _clip("\n".join(rows).strip(), max_chars)


def format_help(markdown: bool = True) -> str:
    if not markdown:
        return "\n".join(
            [
                "📰 RSS 订阅管理",
                "",
                "/rss 或 /rss list：查看当前会话的订阅",
                "/rss add RSS地址 [名称]：添加订阅（首次添加不推送历史文章）",
                "/rss add 也支持多行：一行写名称，下一行写地址",
                "/rss delete 编号：删除订阅，支持 1 3 或 1,3 批量",
                "/rss off 编号 / /rss on 编号：暂停或恢复某个订阅",
                "/rss recent [数量]：查看最近更新，数量 1~20",
                "/rss test RSS地址：测试订阅源是否可用",
                "",
                "订阅按会话隔离：群里的订阅只推到该群，私聊订阅只推给本人。",
            ]
        )
    return "\n".join(
        [
            "# 📰 RSS 订阅管理",
            "",
            "- `/rss` 或 `/rss list`：查看当前会话的订阅",
            "- `/rss add RSS地址 [名称]`：添加订阅（首次添加不推送历史文章）",
            "- `/rss add` 也支持多行：一行写名称，下一行写地址",
            "- `/rss delete 编号`：删除订阅，支持 `1 3` 或 `1,3` 批量",
            "- `/rss off 编号` / `/rss on 编号`：暂停或恢复某个订阅",
            "- `/rss recent [数量]`：查看最近更新，数量 1~20",
            "- `/rss test RSS地址`：测试订阅源是否可用",
            "",
            "订阅按会话隔离：群里的订阅只推到该群，私聊订阅只推给本人。",
        ]
    )


def format_add_single(sub: Subscription, item_count: int) -> str:
    return "\n".join(
        [
            f"已添加 RSS 订阅：{_sub_title(sub.title)}",
            f"地址：{sub.url}",
            f"已将当前 {item_count} 条历史条目标记为已见，首次添加不会推送历史文章。",
        ]
    )


def format_add_batch(created: list[tuple[Subscription, int]], failed: list[tuple[str, str]]) -> str:
    rows = ["RSS 批量添加结果："]
    if created:
        rows.append(f"已添加 {len(created)} 个订阅：")
        for index, (sub, count) in enumerate(created, start=1):
            rows.append(
                f"{index}. {truncate_chars(_sub_title(sub.title), 60)} {sub.url}"
                f"（历史条目 {count} 条已标记为已见）"
            )
    if failed:
        rows.append(f"失败 {len(failed)} 个：")
        for index, (url, message) in enumerate(failed, start=1):
            rows.append(f"{index}. {truncate_chars(url, 100)}：{message}")
    return "\n".join(rows)
