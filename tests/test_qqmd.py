"""QQ Markdown 子集渲染测试。"""

from __future__ import annotations

from astrbot_plugin_rss_pusher.core.qqmd import (
    markdown_link,
    sanitize_inline,
    to_plain_text,
    to_qq_markdown,
    truncate_markdown,
)


def test_sanitize_inline_neutralizes_structure_chars():
    assert sanitize_inline("**加粗** [链接](http://a)") == "＊＊加粗＊＊ ［链接］（http://a）"
    assert sanitize_inline("多行\n标题") == "多行 标题"
    assert sanitize_inline("", 10) == ""
    assert sanitize_inline("a" * 20, 5).endswith("…")


def test_heading_level_is_clamped_to_three():
    assert to_qq_markdown("##### 五级标题") == "### 五级标题"
    assert to_qq_markdown("# 一级") == "# 一级"


def test_lists_are_preserved():
    assert to_qq_markdown("- 一\n- 二") == "- 一\n- 二"
    assert to_qq_markdown("1. 一\n2. 二") == "1. 一\n2. 二"


def test_links_are_wrapped_and_non_http_dropped():
    assert (
        to_qq_markdown("[官网](https://example.com/a?b=1)") == "[官网](<https://example.com/a?b=1>)"
    )
    assert to_qq_markdown("[恶意](javascript:alert(1))") == "恶意"


def test_emphasis_and_images_degrade():
    assert to_qq_markdown("**重点**和*斜体*") == "重点和斜体"
    assert to_qq_markdown("![示意图](https://example.com/a.png)") == "示意图"


def test_blockquote_and_table_degrade():
    assert to_qq_markdown("> 引用内容") == "引用内容"
    assert to_qq_markdown("| A | B |\n|---|---|\n| 1 | 2 |") == "A / B\n1 / 2"


def test_plain_text_line_start_marker_is_neutralized():
    # 外部正文以 "# " 开头时不能变成标题
    assert to_qq_markdown("#这不是标题") == "＃这不是标题"
    assert to_qq_markdown("> 引用") == "引用"


def test_code_fence_is_kept():
    fence = "```python\nprint(1)\n# 注释\n```"
    assert to_qq_markdown(fence) == fence


def test_html_tags_are_removed():
    # 纯 HTML 标签直接剥掉；换行由 html_md.html_to_markdown 在转 Markdown 阶段处理
    assert to_qq_markdown("前<span>中</span>后") == "前中后"


def test_markdown_link_rejects_unsafe_url():
    assert markdown_link("标题", "javascript:alert(1)") == "标题"
    assert markdown_link("标题", None) == "标题"
    assert markdown_link("标题", "https://a.com") == "[标题](<https://a.com>)"


def test_to_plain_text_keeps_url_in_brackets():
    assert (
        to_plain_text("# 标题\n\n- [条目](https://a.com/x)") == "标题\n\n- 条目（https://a.com/x）"
    )


def test_truncate_markdown_closes_code_fence():
    text = "```\n" + "\n".join(f"line{i}" for i in range(20)) + "\n```"
    clipped = truncate_markdown(text, 40)
    assert len(clipped) <= 41
    assert clipped.endswith("…")
    # 不能把代码块截成没闭合的半个
    assert clipped.count("```") % 2 == 0
