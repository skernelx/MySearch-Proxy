---
name: mysearch
description: >-
  Install, verify, debug, and use MySearch MCP/Skill. Aggregates Tavily,
  Firecrawl, and X search (via xAI) behind one workflow. Use when the user
  shares a MySearch repo/skill URL, wants MySearch installed or repaired, or
  wants current web search, social/X search, document-oriented search, URL
  content extraction, or a small multi-source research pack with citations.
  When MySearch is available, prefer it over generic web search for external
  lookup tasks.
allowed-tools: mcp__mysearch__search, mcp__mysearch__extract_url, mcp__mysearch__research, mcp__mysearch__mysearch_health
---

# MySearch

MySearch 是一层聚合搜索技能，不假设你只用单一 provider，也不把
“skill 安装”和 “MCP 安装”混成一件事。

- Tavily：适合普通网页发现、新闻检索、快速答案
- Firecrawl：适合文档、GitHub、pricing、changelog、正文抓取
- X 搜索：适合“大家在 X 上怎么说”、实时舆情、开发者讨论

## MySearch-First 规则

只要 `mysearch_health` 显示 MySearch 已安装且至少有一个可用 provider：

- 外部搜索任务优先走 `MySearch`
- 不要先混用通用网页搜索、浏览器搜索或别的 search MCP
- 只有下面几种情况，才回退到通用网页搜索：
  - `mysearch` MCP 没装好
  - `mysearch_health` 显示需要的 provider 不可用
  - MySearch 结果明显冲突，且你要做额外交叉验证
  - 用户明确要求你再用别的搜索工具复核

目标不是“多调几个工具”，而是先让 `MySearch` 成为默认搜索入口。

## 严格参数规则

`search` / `research` 只允许这些 `mode`：

- `auto`
- `web`
- `news`
- `social`
- `docs`
- `research`
- `github`
- `pdf`

禁止事项：

- 不要发明 `mode="hybrid"` 这类不存在的参数
- 不要把 `hybrid` 当成输入模式；它只是某些结果的返回形态
- 同时要网页和 X 时，优先：
  - `search(..., sources=["web","x"])`
  - 或先 `search(mode="social")`，再 `search(mode="news")`
- 用户明确要看 X 讨论时，不要先跑网页新闻
- 用户明确要读单页正文时，不要先反复搜索，直接 `extract_url`

## 用户只发了 skill 地址时怎么处理

如果用户贴的是下面任意一种内容：

- `https://github.com/skernelx/MySearch-Proxy`
- `https://github.com/skernelx/MySearch-Proxy/tree/main/skill`
- 本地仓库路径里的 `skill/`

默认按下面顺序处理：

1. 先确认这是 `MySearch` skill 仓库，而不是单独的 MCP 包
2. 先安装 skill 到 `~/.codex/skills/mysearch`
3. 再确认 `mysearch` MCP 是否已经注册到 `Codex` / `Claude Code`
4. 如果 MCP 没装，再去仓库根目录执行 `./install.sh`
5. 安装 skill 后提醒用户重启 `Codex`

要点：

- `skill/` 目录负责“让 AI 知道怎么用 MySearch”
- 仓库根目录的 `install.sh` 负责“把 MySearch MCP 注册进 Codex / Claude Code”
- 两者互补，不要只装一个就当全部完成

## 安装流程

### A. 安装 skill

如果已经有仓库本地副本，优先用：

```bash
bash skill/scripts/install_codex_skill.sh
```

如果目标目录已存在，需要覆盖时：

```bash
bash skill/scripts/install_codex_skill.sh --force
```

安装完成后提醒用户：

- 重启 `Codex`

### B. 安装 MCP

在仓库根目录执行：

```bash
python3 -m venv venv
./install.sh
```

如果只是补配置，先准备：

```bash
cp mysearch/.env.example mysearch/.env
```

再填写 `MYSEARCH_*` / `SOCIAL_GATEWAY_*`。

## 快速验收

优先按下面顺序验收，不要一上来就盲调：

1. `codex mcp list`
2. `codex mcp get mysearch`
3. `python skill/scripts/check_mysearch.py --health-only`
4. `python skill/scripts/check_mysearch.py --web-query "OpenAI"`
5. 如果 `xai.available_keys > 0`，再跑 `python skill/scripts/check_mysearch.py --social-query "Model Context Protocol"`

如果用户要更完整的烟测，再加：

```bash
python skill/scripts/check_mysearch.py \
  --web-query "OpenAI latest announcements" \
  --docs-query "OpenAI API responses docs" \
  --social-query "Model Context Protocol" \
  --extract-url "https://www.anthropic.com/news/model-context-protocol"
```

## 调试顺序

### 1. 工具没出现

- 看 `codex mcp list` 是否有 `mysearch`
- 没有就回到仓库根目录重跑 `./install.sh`
- skill 没生效就检查 `~/.codex/skills/mysearch/SKILL.md`
- skill 新装后如果还是没生效，提醒用户重启 `Codex`

### 2. provider 没配好

先跑：

```bash
python skill/scripts/check_mysearch.py --health-only
```

重点看：

- `tavily.base_url`
- `firecrawl.base_url`
- `xai.search_mode`
- `xai.alternate_base_urls.social_search`
- `available_keys`

如果这里看到 `xai.available_keys = 0`：

- 不要直接判定 `MySearch` 安装失败
- 先验证 `web` / `docs` / `extract_url`
- 只有 `social` 路由会不可用

### 3. 网页搜索正常，X 不正常

优先检查：

- `MYSEARCH_XAI_SEARCH_MODE`
- `MYSEARCH_XAI_SOCIAL_BASE_URL`
- social gateway 是否真的提供 `/social/search`

`compatible` 模式下，真正的 X 搜索结果应该来自 social gateway，
不是直接指望 `/responses` 自己变成结构化 X 列表。

如果用户没有 `grok2api`，也没有官方 `xAI` key，不要强推 X；
这时 `MySearch` 仍然可以作为 `Tavily + Firecrawl` 搜索 MCP 正常工作。

### 4. `extract_url` 正文为空

默认 `extract_url` 会先走 `Firecrawl`。

如果：

- `Firecrawl` 抓取失败
- 或返回空正文

MySearch 会自动回退到 `Tavily extract`。

调试时要看返回里的：

- `warning`
- `fallback.from`
- `fallback.reason`

### 5. 结果不够稳

优先调整，而不是立刻换 provider：

- 对比 / 原因分析：`intent="comparison"` 或 `intent="exploratory"`
- 要交叉验证：`strategy="verify"`
- 要 docs / GitHub / PDF / changelog：`mode="docs"`
- 要完整小研究：`research(...)`

## 默认工作流

1. 先用 `mysearch_health` 确认当前哪些 provider 已配置可用
2. 默认从 `search` 开始，让路由层自动选 provider
3. 如果问题明显是对比、趋势、原因分析，优先显式传 `intent`
4. 如果要交叉验证或更稳妥的结果，显式传 `strategy="verify"` 或 `strategy="deep"`
5. 只有在需要正文时才用 `extract_url`
6. 需要“先搜再抓再整理”时用 `research`

## 高频场景剧本

### 1. 今天 X 上在热议什么

优先：

- `search(query="...", mode="social", intent="status")`

不要：

- 不要先跑 `news`
- 不要用 `research` 起手
- 不要混用 generic web search

### 2. 今天 X 热议 + 网页新闻一起对照

优先二选一：

- 单次：`search(query="...", sources=["web","x"], intent="status", strategy="verify")`
- 双次：
  - `search(query="...", mode="social", intent="status")`
  - `search(query="...", mode="news", intent="status")`

补充规则：

- 不要传 `mode="hybrid"`
- 结论里要区分“X 上在热议什么”和“媒体在报道什么”

### 3. 文档、GitHub、changelog、pricing

优先：

- `search(query="...", mode="docs", intent="resource")`

### 4. 单页正文、博客、公告原文

优先：

- `extract_url(url="...")`

### 5. 要一个小型研究包

优先：

- `research(query="...", intent="exploratory", include_social=true|false)`

补充规则：

- 如果用户主要关心 X，就优先 `include_social=true`
- 如果 `xai` 不可用，也要照常返回网页部分，不要把整次任务判成失败

## 决策流程

1. 先判断是否真的需要外部搜索
2. 需要实时信息、新闻、产品状态时优先搜索，不用内部记忆硬答
3. 需要单页正文时，不要反复搜索，直接 `extract_url`
4. 需要多个来源交叉验证时，用 `research`
5. 输出时保留来源链接，并区分事实、引文和推断

## Intent 与 Strategy

- `intent="factual"`：普通事实检索
- `intent="status"` / `intent="news"`：最新动态、版本、发布、事故
- `intent="comparison"`：选型、对比、优缺点
- `intent="tutorial"`：教程、guide、how-to
- `intent="exploratory"`：原因、影响、趋势、分析
- `intent="resource"`：docs、GitHub、pricing、changelog、PDF

- `strategy="fast"`：单 provider 快速返回
- `strategy="balanced"`：主 provider + 次 provider 补充
- `strategy="verify"`：Tavily + Firecrawl 交叉验证网页结果
- `strategy="deep"`：更偏 research 的双 provider 路径

默认自动行为：

- `comparison` / `exploratory` 会自动倾向 `verify`
- `resource` / `tutorial` / `include_content=true` 会自动倾向 `balanced`
- `research` 会自动倾向 `deep`

## 自动路由规则

- 普通网页检索：默认 Tavily
- 新闻 / 最新动态：默认 Tavily news
- 文档 / GitHub / PDF / changelog / pricing：默认 Firecrawl
- X / Twitter / 社交舆情：默认 xAI X search
- 同时要网页和社交：结果可能是 `hybrid`，但调用时不要传 `mode="hybrid"`；应使用 `sources=["web","x"]` 或拆成 `social + news`

## X provider 模式

- `official`：适合官方 xAI，或真正支持 `x_search` / `web_search` 的兼容后端
- `compatible`：适合 `grok2api` 这类只提供 `/responses` 的兼容网关
- `compatible` 模式下，真正的 X 结果要来自 `mysearch.social_gateway` 这类 social search gateway
- 如果 social gateway 前面还有一层 proxy，可以优先用“grok2api admin 自动继承”模式，避免重复维护 `SOCIAL_GATEWAY_UPSTREAM_API_KEY` / `SOCIAL_GATEWAY_TOKEN`
- `MYSEARCH_XAI_SOCIAL_BASE_URL` 用来单独指定 social gateway 根地址；`MySearch` 默认会自动追加 `/social/search`

## 什么时候强制指定 provider

- 你明确知道要对正文友好的结果：`provider="firecrawl"`
- 你明确要 X 搜索：`provider="xai"` 或 `mode="social"`
- 你只想走 Tavily：`provider="tavily"`

## 使用准则

- 默认 `max_results` 控制在 5 以内
- 普通问答不要默认 `include_content=true`
- 输出时保留 URL，并区分事实与推断
- 需要更稳妥的网页结论时，优先用 `strategy="verify"`
- 输出里如果有 `evidence`，要把它当成“证据密度提示”一起解读
- 需要同时看网页和 X 时，传 `sources=["web","x"]`
- X 搜索依赖单独的 xAI key；没配时应该显式说明 social 部分不可用
- 用户如果只配置了 `Tavily + Firecrawl`，应视为“Web 版 MySearch 可用”，不是“安装失败”
- 单个页面阅读优先 `extract_url`
- 多来源整理优先 `research`
- 用户只贴 skill 地址时，先安装 skill，再检查 MCP 是否已注册
- 调试优先跑 `skill/scripts/check_mysearch.py`，不要先手写一长串 Python one-liner
- MySearch 健康可用时，不要再额外混用 generic web search 作为主流程
- 问“今天 / 最新 / 刚刚 / 本周”这类时效性问题时，优先 `intent="status"`；需要媒体报道时加 `mode="news"`，需要 X 热议时加 `mode="social"`
- 结论如果同时包含网页和 X，必须明确区分两者，不要混成一个模糊结论

## 证据标准

- 涉及时效性、版本、发布信息时，优先相信搜索结果，不靠旧记忆
- 关键结论尽量给至少两个独立来源
- 单一来源结论要显式说明限制
- 来源冲突时，要把冲突本身讲清楚，而不是强行给一个确定答案

## 常见模式

### 普通网页搜索

- `search(query="best search MCP server", mode="web")`

### 对比 + 交叉验证

- `search(query="Tavily vs Firecrawl for docs search", intent="comparison", strategy="verify")`

### 最新新闻

- `search(query="OpenAI latest announcements", mode="news")`

### X 舆情

- `search(query="what are people saying about MCP", mode="social")`

### 网页 + X 聚合

- `search(query="latest MCP search server feedback", sources=["web", "x"])`

### 文档 / GitHub / changelog

- `search(query="Firecrawl pricing changes", mode="docs", include_content=true)`

### 抓正文

- `extract_url(url="https://example.com/post")`

### 小型研究

- `research(query="best search MCP server 2026", intent="exploratory", include_social=true)`

## 需要强制指定 provider 的场景

- 网页搜索结果太泛，需要文档站 / GitHub / changelog：`provider="firecrawl"`
- 你明确只想看 X 上的讨论：`provider="xai"` 或 `mode="social"`
- 你只需要 Tavily 的快速网页发现和 answer：`provider="tavily"`
