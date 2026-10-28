# astrbot_plugin_rss_pusher

把 RSS/Atom 订阅搬进 AstrBot：`/rss add` 订阅一个源，之后有新文章时，机器人以 **QQ 官方 Markdown** 主动推送到群里或私聊。

本插件是 [`kuliantnt/qq-maid-bot`](https://github.com/kuliantnt/qq-maid-bot)（Rust，MIT）RSS 模块的 Python 移植，按 AstrBot 插件规范重写，不是简单的功能对拷。

- 移植来源：`qq-maid-core/src/runtime/tools/rss/{feed,scheduler,storage}`、`qq-maid-core/src/runtime/respond/rss_flow.rs`、`qq-maid-common/src/markdown/qq.rs`
- 开发依据：[AstrBot 插件开发指南](https://docs.astrbot.app/dev/star/plugin-new.html)
- Markdown 发送参考：[`Astrhub/astrbot_plugin_qq_official_v2adapter`](https://github.com/Astrhub/astrbot_plugin_qq_official_v2adapter) 与 AstrBot 内置 `qq_official` 适配器对 `use_markdown_` 的处理

---

## 特性

| 能力 | 说明 |
| --- | --- |
| 订阅管理 | `/rss` 列表、`add`、`delete`、`on/off`、`recent`、`test`，支持中文别名 `/订阅` |
| 会话隔离 | 订阅绑定 AstrBot 的 `unified_msg_origin`，群订阅只推该群，私聊只推本人，删除也只在当前会话生效 |
| 精准去重 | 条目按 `id → link → 哈希` 生成稳定 `item_key`；同一篇文章被编辑时 `revision_hash` 变化，会重新推送 |
| 首次不刷屏 | 添加订阅时把当前历史条目标记为已见，不会一次性推送几十条旧文章 |
| 单轮限流 | 单订阅单轮最多推 N 条（默认 3），剩余条目下一轮继续，避免大更新刷屏 |
| 失败退避 | 抓取失败记录错误与连续失败次数；推送失败累计次数，超过上限放弃该条目 |
| QQ Markdown | 推送按 QQ 支持的子集渲染：标题、列表、HTTP 链接、代码块保留，引用/表格/强调降级，行首 `#`、`>` 等外部文本不会伪造结构 |
| 自动降级 | 平台不支持 Markdown 或发送被拒绝时自动改发纯文本；指令回复也按平台切换排版 |
| 安全抓取 | 只允许 http(s)，拦截 localhost/内网/link-local/云 metadata，**每一跳重定向都重新校验**，另有超时与响应体大小上限 |
| 可选翻译 | 开启后把外文标题/摘要翻成中文（用 AstrBot 当前默认模型），翻译前后链接不一致则丢弃译文 |

## 安装

```bash
cd AstrBot/data/plugins
git clone https://github.com/yuweitk/astrbot_plugin_rss_pusher.git
```

1. 也可以直接把本目录拷到 `AstrBot/data/plugins/astrbot_plugin_rss_pusher/`，或在 WebUI 插件页用仓库地址安装。
2. 重启 AstrBot 或点击插件卡片的「重载插件」。`requirements.txt` 会被自动安装（`aiohttp`、`feedparser`）。
3. 在 WebUI 的插件配置里按需调整轮询间隔等参数。

> 需要 AstrBot `>= 4.16`。Markdown 推送依赖平台适配器支持 `use_markdown_`，内置 `qq_official` / `qq_official_webhook` 与社区 `qq_official_v2` 适配器都支持。

## 指令

| 指令 | 说明 |
| --- | --- |
| `/rss` / `/rss list` / `/订阅 列表` | 查看当前会话的订阅 |
| `/rss add RSS地址 [名称]` | 添加订阅，名称可选（自定义名称不会被源站标题覆盖） |
| `/rss add` + 多行 | 一行名称、下一行地址，可一次加多个；带 `1.` `2、` 编号也能识别 |
| `/rss delete 1` / `/rss delete 1 3` | 按列表编号删除，也支持 ID 前缀；空格或逗号分隔可批量 |
| `/rss off 1` / `/rss on 1` | 暂停 / 恢复订阅（暂停后不再抓取和推送） |
| `/rss recent [数量]` | 查看最近更新，数量 1~20，默认 5 |
| `/rss test RSS地址` | 只测试能否解析，不保存订阅 |

群聊里默认只有管理员能执行 `add` / `delete` / `on` / `off`，普通成员仍可看列表与更新，可用 `commands.group_admin_only` 关闭该限制。

## 配置

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 关闭后不轮询，但订阅仍可管理 |
| `poll_interval_seconds` | `300` | 轮询间隔，最小 10 秒（代码内再兜底），建议 ≥120 |
| `fetch.timeout_seconds` | `15` | 单次请求超时 |
| `fetch.max_body_kb` | `2048` | 响应体上限，超过即放弃（防超大响应） |
| `fetch.max_redirects` | `4` | 最大重定向次数 |
| `fetch.allow_private_network` | `false` | 允许抓内网地址（自建内网 RSS 才需要开） |
| `push.message_format` | `auto` | `auto`：按平台决定；`markdown`：强制；`text`：只发纯文本 |
| `push.markdown_platforms` | `qq_official`, `qq_official_webhook`, `qq_official_v2` | 视为支持 Markdown 的平台名 |
| `push.max_per_feed` | `3` | 单订阅单轮最多推送条数 |
| `push.max_failures` | `3` | 单条目推送失败上限 |
| `push.summary_max_chars` | `500` | 摘要最大字符数 |
| `push.max_message_chars` | `3600` | 单条消息最大字符数 |
| `storage.seen_retention` | `500` | 每订阅保留的去重指纹数 |
| `commands.group_admin_only` | `true` | 群内仅管理员可增删 |
| `commands.max_per_session` | `20` | 单会话订阅上限 |
| `commands.markdown_replies` | `true` | 指令回复是否用 Markdown 排版 |
| `commands.timezone_offset_hours` | `8.0` | 时间展示时区（数据库统一存 UTC） |
| `translate.enabled` | `false` | 外文标题/摘要翻译（会产生模型费用） |
| `translate.max_chars` | `400` | 参与翻译的最大字符数 |

## 推送长什么样

开启 Markdown 时，一条更新大致是：

```markdown
## RSS 更新：少数派

### [这篇文章的标题](<https://sspai.com/post/123>)

正文摘要，链接会保留为 [可点击文本](<https://example.com>)。

发布时间：2026-10-20 10:00

原文：[查看条目](<https://sspai.com/post/123>)
```

纯文本平台（例如 NapCat 走的 `aiocqhttp`）会收到：

```text
【RSS 更新】少数派

这篇文章的标题
正文摘要，链接会保留为 可点击文本（https://example.com）
发布时间：2026-10-20 10:00
链接：https://sspai.com/post/123
```

## QQ 官方 Markdown 的注意事项

这些限制来自 QQ 官方接口本身，不是插件的问题：

1. **主动消息有额度**：群聊/C2C 的主动推送受官方频率与额度限制，超了会发送失败。轮询间隔不要调得太短。
2. **重启后群聊首次推送可能被跳过**：AstrBot 的 `qq_official` 适配器需要先缓存该群的会话场景/`msg_id` 才会发主动消息。AstrBot 重启后，群里先随便有人说句话即可恢复（私聊不受影响）。
3. **链接与图片域名需报备**：QQ 会过滤未报备域名的链接/图片，未报备时可能整条不渲染或链接失效。
4. **只支持部分语法**：标题、列表、链接、代码块稳定；引用、表格、加粗斜体在 QQ 上表现不稳定，插件会主动降级，避免整条消息渲染错乱。

## 数据与目录

```
astrbot_plugin_rss_pusher/
├── main.py              # Star 插件：指令处理 + 调度器装配
├── metadata.yaml        # 插件元数据
├── _conf_schema.json    # WebUI 配置 Schema
├── requirements.txt
├── ruff.toml            # 代码风格（AstrBot 要求提交前跑 ruff）
├── core/
│   ├── commands.py      # /rss 参数解析（对应 rss_flow.rs）
│   ├── feed.py          # 抓取 + 解析 + SSRF 防护（对应 feed.rs）
│   ├── store.py         # SQLite 订阅/条目/推送状态（对应 storage）
│   ├── scheduler.py     # 后台轮询与推送（对应 scheduler.rs）
│   ├── qqmd.py          # QQ Markdown 子集渲染（对应 markdown/qq.rs）
│   ├── html_md.py       # RSS 摘要 HTML → Markdown
│   ├── formatter.py     # 推送模板与指令回复排版
│   ├── sender.py        # use_markdown 消息链与纯文本兜底
│   ├── translate.py     # 可选翻译
│   ├── models.py        # 数据结构
│   └── timeutil.py      # UTC 存储 / 本地时区展示
├── scripts/
│   └── smoke_preview.py # 不启动 AstrBot，直接看推送长什么样
└── tests/               # 95 个用例，含本地 HTTP 抓取、推送链路与翻译降级
```

运行数据在 `AstrBot/data/plugin_data/astrbot_plugin_rss_pusher/rss.db`（SQLite，WAL 模式）。卸载插件不会删数据，删掉该目录即可重置。

## 开发与测试

```bash
pip install -r requirements.txt pytest ruff
python -m pytest tests -q                  # 不需要启动 AstrBot
python scripts/smoke_preview.py            # 打印完整推送消息，肉眼验收排版
ruff check . && ruff format .
```

`tests/astrbot_stub.py` 只实现插件真正用到的宿主接口（`Star`、`StarTools`、`filter.command`、`MessageChain` 等），因此核心逻辑可以在没有 AstrBot 的环境里单独验证。

## 已知取舍

- **不做 LLM 摘要/改写**：摘要直接用源站内容，避免二次幻觉；原文优先用 `summary`，缺失才回退 `content:encoded`（很多聚合源会把全文塞进 content）。
- **标题单行化**：源站标题里的换行、`**`、`[]()` 会被换成全角字符，防止破坏消息结构。
- **用户自定义名称优先**：上游在每次成功检查后用源站标题覆盖订阅名，这里改成「用户起过名就锁定」，避免名字被改掉。
- **不做翻译缓存**：翻译结果只用于本次展示，不写库、不参与去重指纹，避免模型输出波动导致重复推送。

## 许可

MIT。移植了 [`kuliantnt/qq-maid-bot`](https://github.com/kuliantnt/qq-maid-bot)（MIT, Copyright (c) 2026 kuliantnt）的设计与算法，原始版权声明见 `LICENSE`。
