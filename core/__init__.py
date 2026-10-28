"""RSS 订阅推送插件的内部模块。

模块划分对齐 qq-maid-bot 的 Rust 实现：

| 本插件 | qq-maid-bot |
| --- | --- |
| `feed.py` | `runtime/tools/rss/feed.rs` |
| `store.py` | `runtime/tools/rss/storage` |
| `scheduler.py` | `runtime/tools/rss/scheduler.rs` |
| `qqmd.py` | `qq-maid-common/src/markdown/qq.rs` |
| `commands.py` | `runtime/respond/rss_flow.rs` |
| `formatter.py` | `scheduler.rs` / `rss_flow.rs` 的排版函数 |
"""
