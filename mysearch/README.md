# MySearch

[English Guide](./README_EN.md)

`MySearch` 是独立的搜索聚合 MCP。

它的产品定位是：

- 可安装到 `Codex` 或 `Claude Code`
- 把 `Tavily + Firecrawl + X` 聚合成一个统一搜索入口
- 默认优先官方 API，也支持自定义兼容网关

这里的 `mysearch/` 是 `MySearch Proxy` 仓库里的 MCP 实现主体。

## 能力

- Tavily：普通网页发现、新闻搜索、快速 answer
- Firecrawl：文档站、GitHub、pricing、changelog、PDF、正文抓取
- xAI Responses API：X / Twitter 搜索与社交舆情

## MCP 工具

- `search`
- `extract_url`
- `research`
- `mysearch_health`

`extract_url` 默认优先 `Firecrawl`，如果抓取失败或返回空正文，会自动回退到
`Tavily extract`。

## 自动路由

- 普通网页检索 -> Tavily
- 新闻 / 最新动态 -> Tavily news
- 文档 / GitHub / PDF / changelog / pricing -> Firecrawl
- X / Twitter / 社交舆情 -> xAI X search
- 同时请求网页 + X -> hybrid 聚合

## Intent 与 Strategy

- `intent`: `factual` / `status` / `comparison` / `tutorial` /
  `exploratory` / `news` / `resource`
- `strategy`: `fast` / `balanced` / `verify` / `deep`

默认行为：

- `comparison` / `exploratory` -> 自动倾向 `verify`
- `docs` / `resource` / `tutorial` / `include_content=true` -> 自动倾向 `balanced`
- `research` -> 自动倾向 `deep`

## 安装

先准备环境变量：

```bash
cp mysearch/.env.example mysearch/.env
```

最小配置：

```env
MYSEARCH_TAVILY_API_KEY=tvly-...
MYSEARCH_FIRECRAWL_API_KEY=fc-...
MYSEARCH_XAI_API_KEY=xai-...
```

安装：

```bash
./install.sh
```

安装脚本会：

1. 安装 `mysearch/requirements.txt`
2. 如果检测到 `Claude Code`，自动注册 `mysearch`
3. 如果检测到 `Codex`，自动注册 `mysearch`
4. 自动把当前 shell 中已有的 `MYSEARCH_*` 环境变量注入 MCP

`install.sh` 会优先读取 `mysearch/.env`。

验证：

```bash
claude mcp list
codex mcp list
```

## 官方模式与 compatible 模式

官方模式：

```env
MYSEARCH_XAI_BASE_URL=https://api.x.ai/v1
MYSEARCH_XAI_RESPONSES_PATH=/responses
MYSEARCH_XAI_SEARCH_MODE=official
```

compatible 模式：

```env
MYSEARCH_XAI_BASE_URL=https://media.example.com/v1
MYSEARCH_XAI_SOCIAL_BASE_URL=https://your-social-gateway.example.com
MYSEARCH_XAI_SEARCH_MODE=compatible
MYSEARCH_XAI_API_KEY=your-gateway-token
```

这时：

- `MYSEARCH_XAI_BASE_URL` 指向模型 / `/responses` 网关
- `MYSEARCH_XAI_SOCIAL_BASE_URL` 指向 social gateway 根地址
- `MySearch` 默认自动追加 `/social/search`

## 内置 social gateway

参考实现：

- 模块：`mysearch.social_gateway`
- 作用：把 xAI-compatible `/responses` 结果归一化成稳定的 social 搜索结构

最小配置：

```env
SOCIAL_GATEWAY_UPSTREAM_BASE_URL=https://media.example.com/v1
SOCIAL_GATEWAY_UPSTREAM_RESPONSES_PATH=/responses
SOCIAL_GATEWAY_UPSTREAM_API_KEY=your-upstream-key
SOCIAL_GATEWAY_MODEL=grok-4.1-fast
SOCIAL_GATEWAY_TOKEN=your-social-gateway-token
```

如果上游是 grok2api，也可以改成后台自动继承模式：

```env
SOCIAL_GATEWAY_UPSTREAM_BASE_URL=https://media.example.com/v1
SOCIAL_GATEWAY_ADMIN_BASE_URL=https://media.example.com
SOCIAL_GATEWAY_ADMIN_APP_KEY=your-grok2api-app-key
SOCIAL_GATEWAY_MODEL=grok-4.1-fast
```

这时 social gateway 会自动读取 grok2api 的 `app.api_key` 和 token 池，
不需要再手动重复填写上游 key / gateway token。

启动：

```bash
../venv/bin/python -m mysearch.social_gateway
```

或者：

```bash
uvicorn mysearch.social_gateway:app --host 127.0.0.1 --port 9875
```

## 常见用法

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

X 舆情：

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

对比型搜索：

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

更多架构说明见 [../docs/mysearch-architecture.md](../docs/mysearch-architecture.md)。
