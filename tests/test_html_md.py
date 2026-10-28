"""HTML → Markdown 转换测试。"""

from __future__ import annotations

from astrbot_plugin_rss_pusher.core.html_md import (
    html_to_markdown,
    html_to_text,
    strip_script_style,
)


def test_paragraph_and_break():
    md = html_to_markdown("<p>第一段</p><p>第二段<br>换行</p>")
    assert "第一段" in md and "第二段" in md
    assert "换行" in md


def test_link_and_emphasis():
    md = html_to_markdown(
        '<p>看 <a href="https://example.com/x">这个</a> <strong>重点</strong></p>'
    )
    assert "[这个](https://example.com/x)" in md
    assert "**重点**" in md


def test_unordered_and_ordered_list():
    md = html_to_markdown("<ul><li>一</li><li>二</li></ul><ol><li>甲</li></ol>")
    assert "- 一" in md
    assert "- 二" in md
    assert "1. 甲" in md


def test_code_block_with_language():
    md = html_to_markdown('<pre><code class="language-python">print(1)</code></pre>')
    assert md.startswith("```python")
    assert "print(1)" in md
    assert md.rstrip().endswith("```")


def test_script_and_style_are_stripped():
    raw = "<style>p{color:red}</style><p>正文</p><script>alert(1)</script>"
    assert "color:red" not in strip_script_style(raw)
    text = html_to_text(raw)
    assert text.strip() == "正文"


def test_table_is_flattened():
    html = "<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>"
    text = html_to_text(html)
    assert "A / B" in text
    assert "1 / 2" in text


def test_plain_text_mode_has_no_markdown():
    html = '<p><a href="https://a.com">标题</a> <em>斜体</em></p>'
    text = html_to_text(html)
    assert "[" not in text and "](" not in text
    assert "标题" in text


def test_empty_input():
    assert html_to_markdown("") == ""
    assert html_to_text("   ") == ""
