# MySearch

[English Guide](./README_EN.md) · [返回仓库](../README.md)

`MySearch` 是这个仓库里真正可安装的搜索 MCP。

它不是某一个 provider 的轻量封装，而是把 `Tavily`、`Firecrawl`、可选
`X / Social` 收成一个统一搜索入口，并把“搜索”“正文抓取”“小型研究”做成
同一套运行时。

## 这个 MCP 的定位

`mysearch/` 负责：

- 提供统一的 `search` 入口
- 提供单页抓取 `extract_url`
- 提供小型研究工作流 `research`
- 提供健康检查 `mysearch_health`

它适合安装到：

- `Codex`
- `Claude Code`
- 其他支持 MCP 的本地助手

如果你只想给 AI 一个更强的搜索入口，用它就够了。

如果你还要统一 key、token、额度和 social gateway，再看
[../proxy/README.md](../proxy/README.md)。

## 为什么它比常见搜索 MCP 更完整

### 1. 不是单一搜索源

默认路由规则：

- 普通网页、新闻、快速发现：优先 Tavily
- 文档、GitHub、PDF、pricing、changelog：优先 Firecrawl
- X / Social：优先 xAI 或 compatible `/social/search`

### 2. 不是只会搜，不会提取

- `extract_url` 默认优先 Firecrawl
- Firecrawl 失败或正文为空时，会自动回退 Tavily extract

这意味着正文抓取是正式能力，不是搜索结果里的附赠字段。

### 3. 不是只有 prompt，没有运行时

这里直接提供可安装 MCP，不需要把“如何搜”的逻辑塞进长 prompt。

### 4. 不是写死官方 API

你既可以直连官方接口，也可以：

- 把 Tavily / Firecrawl 接到自己的聚合 API
- 把 X / Social 接到 compatible `/social/search`
- 通过 `BASE_URL + PATH + AUTH_*` 精细改写认证和路由

### 5. X / Social 是增强项，不是门槛

没有 `xAI` 或 `grok2api` 时，下面这些能力仍然可用：

- `web`
- `news`
- `docs`
- `github`
- `pdf`
- `extract`
- `research`

只有明确的 `social` 路由会不可用。

## 默认推荐上游

最推荐的接法不是把官方 key 直接散到每台机器，而是：

```text
tavily-key-generator
  -> 提供 Tavily / Firecrawl 官方 provider 或聚合 API

MySearch MCP
  -> 只负责统一搜索逻辑与工具暴露
```

推荐项目：

- [skernelx/tavily-key-generator](https://github.com/skernelx/tavily-key-generator)

这样做的好处：

- MySearch 不需要关心 Tavily / Firecrawl 的上游管理细节
- 你可以直接走统一 gateway，而不是在每个客户端散落官方 key

如果你已经有官方 key，直接填官方接口也完全支持。

## 工具列表

### `search`

统一搜索入口。

常用模式：

- `auto`
- `web`
- `news`
- `social`
- `docs`
- `github`
- `pdf`
- `research`

### `extract_url`

单页正文抓取。

默认行为：

- 优先 Firecrawl
- 失败或空正文时回退 Tavily extract

### `research`

小型研究工作流。

适合：

- 对比类问题
- 趋势类问题
- 需要抓取若干结果正文并整理证据的问题

### `mysearch_health`

返回 provider、base URL、key 可用性和当前配置摘要。

## Intent 与 Strategy

`MySearch` 把“搜什么”和“怎么搜”拆成两个维度：

- `intent`
  - `factual`
  - `status`
  - `comparison`
  - `tutorial`
  - `exploratory`
  - `news`
  - `resource`
- `strategy`
  - `fast`
  - `balanced`
  - `verify`
  - `deep`

默认倾向：

- `comparison` / `exploratory` 更偏向 `verify`
- `docs` / `resource` / `tutorial` 更偏向 `balanced`
- `research` 默认更偏向 `deep`

## Provider 支持与缺失行为

### Tavily

负责：

- 普通网页
- 新闻
- 默认 research 发现阶段

如果 Tavily 不可用：

- `web / news / 默认 research`

会变弱，但 Firecrawl 仍可覆盖 docs 与部分内容抓取。

### Firecrawl

负责：

- docs
- GitHub
- PDF
- pricing
- changelog
- 正文抓取

如果 Firecrawl 不可用：

- docs 定向检索能力下降
- `extract_url` 会尽量回退到 Tavily extract

### X / Social

负责：

- X / Social 搜索
- 舆情和讨论

如果 X / Social 不可用：

- `search(mode="social")` 会返回明确提示
- `research(include_social=true)` 不会让整条工作流失败，而是返回网页结果并附带
  `social_error`

## 安装

在仓库根目录执行：

```bash
python3 -m venv venv
cp mysearch/.env.example mysearch/.env
./install.sh
```

最小可用配置：

```env
MYSEARCH_TAVILY_API_KEY=tvly-...
MYSEARCH_FIRECRAWL_API_KEY=fc-...
```

更推荐的公开部署方式，是接到
[skernelx/tavily-key-generator](https://github.com/skernelx/tavily-key-generator)
暴露出来的统一入口：

```env
MYSEARCH_TAVILY_BASE_URL=https://your-search-gateway.example.com
MYSEARCH_TAVILY_SEARCH_PATH=/api/search
MYSEARCH_TAVILY_EXTRACT_PATH=/api/extract
MYSEARCH_TAVILY_AUTH_MODE=bearer
MYSEARCH_TAVILY_API_KEY=your-token

MYSEARCH_FIRECRAWL_BASE_URL=https://your-search-gateway.example.com
MYSEARCH_FIRECRAWL_SEARCH_PATH=/firecrawl/v2/search
MYSEARCH_FIRECRAWL_SCRAPE_PATH=/firecrawl/v2/scrape
MYSEARCH_FIRECRAWL_AUTH_MODE=bearer
MYSEARCH_FIRECRAWL_API_KEY=your-token
```

`install.sh` 会自动：

1. 安装依赖
2. 检测 `Claude Code`
3. 检测 `Codex`
4. 注册 `mysearch` MCP
5. 把 `mysearch/.env` 里的 `MYSEARCH_*` 注入到配置里

## X / Social 配置

### 官方 xAI 模式

```env
MYSEARCH_XAI_BASE_URL=https://api.x.ai/v1
MYSEARCH_XAI_RESPONSES_PATH=/responses
MYSEARCH_XAI_SEARCH_MODE=official
MYSEARCH_XAI_API_KEY=xai-...
```

### compatible 模式

```env
MYSEARCH_XAI_BASE_URL=https://media.example.com/v1
MYSEARCH_XAI_SOCIAL_BASE_URL=https://your-social-gateway.example.com
MYSEARCH_XAI_SEARCH_MODE=compatible
MYSEARCH_XAI_API_KEY=your-social-gateway-token
```

这里的行为是：

- `MYSEARCH_XAI_BASE_URL` 指向模型或 `/responses` 网关
- `MYSEARCH_XAI_SOCIAL_BASE_URL` 指向 social gateway 根地址
- MySearch 会自动追加 `/social/search`

如果你没有 `grok2api` 或官方 `xAI` key，可以先完全不配这一段，
MySearch 仍然会作为 `Tavily + Firecrawl` 聚合 MCP 正常工作。

## 内置 social gateway

参考实现：

- 模块：`mysearch.social_gateway`
- 作用：把 xAI-compatible `/responses` 归一化成稳定的 social 搜索结果结构

最小配置：

```env
SOCIAL_GATEWAY_UPSTREAM_BASE_URL=https://media.example.com/v1
SOCIAL_GATEWAY_UPSTREAM_RESPONSES_PATH=/responses
SOCIAL_GATEWAY_UPSTREAM_API_KEY=your-upstream-key
SOCIAL_GATEWAY_MODEL=grok-4.1-fast
SOCIAL_GATEWAY_TOKEN=your-social-gateway-token
```

启动方式：

```bash
../venv/bin/python -m mysearch.social_gateway
```

或者：

```bash
uvicorn mysearch.social_gateway:app --host 127.0.0.1 --port 9875
```

## 快速验收

注册是否成功：

```bash
claude mcp list
codex mcp list
codex mcp get mysearch
```

健康检查：

```bash
python skill/scripts/check_mysearch.py --health-only
```

网页与文档烟测：

```bash
python skill/scripts/check_mysearch.py --web-query "OpenAI latest announcements"
python skill/scripts/check_mysearch.py --docs-query "OpenAI Responses API docs"
```

如果配置了 X / Social：

```bash
python skill/scripts/check_mysearch.py --social-query "Model Context Protocol"
```

正文抓取烟测：

```bash
python skill/scripts/check_mysearch.py \
  --extract-url "https://www.anthropic.com/news/model-context-protocol"
```

## 常见调用示例

普通网页搜索：

```json
{
  "tool": "search",
  "arguments": {
    "query": "best search MCP server",
    "mode": "web"
  }
}
```

X / Social：

```json
{
  "tool": "search",
  "arguments": {
    "query": "what are people saying about MCP",
    "mode": "social",
    "max_results": 5
  }
}
```

对比型问题：

```json
{
  "tool": "search",
  "arguments": {
    "query": "Tavily vs Firecrawl for docs search",
    "intent": "comparison",
    "strategy": "verify",
    "max_results": 6
  }
}
```

抓正文：

```json
{
  "tool": "extract_url",
  "arguments": {
    "url": "https://example.com/post",
    "formats": ["markdown"]
  }
}
```

小型研究：

```json
{
  "tool": "research",
  "arguments": {
    "query": "best search MCP server 2026",
    "intent": "exploratory",
    "include_social": true,
    "scrape_top_n": 3
  }
}
```

## 相关文档

- 仓库总览：
  [../README.md](../README.md)
- Proxy 控制台：
  [../proxy/README.md](../proxy/README.md)
- 架构说明：
  [../docs/mysearch-architecture.md](../docs/mysearch-architecture.md)
