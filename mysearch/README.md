# MySearch

[English Guide](./README_EN.md) · [返回仓库](../README.md)

`mysearch/` 是这个仓库里真正可安装的 MCP 服务。

它负责把 Tavily、Firecrawl、Exa 和可选 X / Social 收成一个统一搜索入口，
并暴露给 Codex、Claude Code 或其他支持 MCP 的客户端。

如果你只想先把搜索能力装到本机 AI 助手里，优先看这一份文档。

## 提供的工具

### `search`

统一搜索入口。

支持的模式：

- `auto`
- `web`
- `news`
- `social`
- `docs`
- `research`
- `github`
- `pdf`

### `extract_url`

抓取单个页面正文。

默认行为：

- 优先 Firecrawl
- Firecrawl 失败或正文为空时回退 Tavily extract

### `research`

小型研究工作流。

默认流程：

- 先做网页发现
- 再抓取前几条正文
- 可选补充 X / Social 讨论

### `mysearch_health`

返回当前 provider 配置、base URL、search mode、auth mode 和 key 可用性。

## 当前推荐接法

最推荐的是 `proxy-first`：

```env
MYSEARCH_PROXY_BASE_URL=https://your-mysearch-proxy.example.com
MYSEARCH_PROXY_API_KEY=mysp-...
```

好处：

- Tavily / Firecrawl / Exa 默认一起走统一 proxy
- 如果 proxy 同时接通了 Social / X，同一个 token 还能继续复用
- 客户端配置最少
- 更适合团队共享和公开部署

如果你还没有 Proxy，也可以直接连 provider。

## 直连 provider 的最小配置

最小直连通常至少需要：

```env
MYSEARCH_TAVILY_API_KEY=tvly-...
MYSEARCH_FIRECRAWL_API_KEY=fc-...
```

如果你也要接 Exa：

```env
MYSEARCH_EXA_API_KEY=exa-...
```

如果你也要接 X / Social：

```env
MYSEARCH_XAI_API_KEY=xai-...
```

没有 X / Social 时，下面这些仍然可用：

- `web`
- `news`
- `docs`
- `github`
- `pdf`
- `extract_url`
- `research`

## 安装到 Codex / Claude Code

在仓库根目录执行：

```bash
python3 -m venv venv
```

优先把配置放进宿主 config，而不是先复制 `.env`：

- `Codex`：`~/.codex/config.toml` 的 `mcp_servers.mysearch.env`
- `Claude Code`：注册 MCP 时直接把 `MYSEARCH_*` 注入 env
- `mysearch/.env`：只建议本地单仓调试时使用

填好配置后安装：

```bash
./install.sh
```

`install.sh` 会做两件事：

- 安装 `mysearch/requirements.txt`
- 如果本机有 `codex` 或 `claude` 命令，就自动注册 `mysearch` MCP
- 如果宿主已有 `mysearch` config，会直接复用其中的 `MYSEARCH_*`

## 推荐验收

### 1. 看 MCP 是否注册成功

```bash
codex mcp get mysearch
```

或者：

```bash
claude mcp list
```

### 2. 跑健康检查

```bash
python3 skill/scripts/check_mysearch.py --health-only
```

### 3. 跑一轮 smoke test

```bash
python3 skill/scripts/check_mysearch.py --web-query "OpenAI latest announcements"
python3 skill/scripts/check_mysearch.py --docs-query "OpenAI Responses API docs"
```

如果你配置了 Social / X，再补：

```bash
python3 skill/scripts/check_mysearch.py --social-query "Model Context Protocol"
```

如果你要测正文抓取：

```bash
python3 skill/scripts/check_mysearch.py \
  --extract-url "https://www.anthropic.com/news/model-context-protocol"
```

## 作为 HTTP MCP 单独启动

默认注册给 Codex / Claude Code 时，`MySearch` 走的是 `stdio`。

如果你想把它作为远程 MCP 暴露出来，可以直接启动：

```bash
python -m mysearch --transport streamable-http --host 127.0.0.1 --port 8000
```

默认 endpoint：

- `streamableHTTP`
  - `http://127.0.0.1:8000/mcp`
- `SSE`
  - `http://127.0.0.1:8000/sse`

如果你已经有远程 URL，也可以直接注册到 Codex：

```bash
codex mcp add mysearch --url https://your-mysearch.example.com/mcp
codex mcp get mysearch
```

需要 Bearer Token 时：

```bash
export MYSEARCH_MCP_BEARER_TOKEN=your-token
codex mcp add mysearch \
  --url https://your-mysearch.example.com/mcp \
  --bearer-token-env-var MYSEARCH_MCP_BEARER_TOKEN
```

## 路由逻辑怎么理解

MySearch 不是单一 provider 的壳。

默认可以这样理解：

- `web / news`
  - 优先 Tavily
- `docs / github / pdf`
  - 优先 Firecrawl
- 补充网页发现
  - 可回退 Exa
- `social`
  - 走 xAI 或 compatible `/social/search`
- `extract_url`
  - Firecrawl 优先，Tavily 回退
- `research`
  - 搜索 + 抓取 + 可选 social 补充

## Intent 和 Strategy

`search` 与 `research` 同时支持：

- `intent`
  - `auto`
  - `factual`
  - `status`
  - `comparison`
  - `tutorial`
  - `exploratory`
  - `news`
  - `resource`
- `strategy`
  - `auto`
  - `fast`
  - `balanced`
  - `verify`
  - `deep`

适合记忆的简单规则：

- 想快一点：
  - `fast`
- 想稳一点：
  - `balanced`
- 想多做交叉验证：
  - `verify`
- 想做小研究：
  - `deep`

## 关键环境变量

优先关注这几组：

### 通用

```env
MYSEARCH_NAME=MySearch
MYSEARCH_TIMEOUT_SECONDS=45
```

### Proxy-first

```env
MYSEARCH_PROXY_BASE_URL=
MYSEARCH_PROXY_API_KEY=
```

### 运行时优化参数（v0.1.5+）

```env
MYSEARCH_MAX_PARALLEL_WORKERS=4
MYSEARCH_SEARCH_CACHE_TTL_SECONDS=30
MYSEARCH_EXTRACT_CACHE_TTL_SECONDS=300
```

含义：

- `MYSEARCH_MAX_PARALLEL_WORKERS`
  - 控制混合查询与 `research` 中并行请求的工作线程数。
- `MYSEARCH_SEARCH_CACHE_TTL_SECONDS`
  - 控制 `search` 缓存生存时间。
- `MYSEARCH_EXTRACT_CACHE_TTL_SECONDS`
  - 控制 `extract_url` 缓存生存时间。

返回结构的新增字段：

- `route_debug`
  - 展示路由决策、解析后的 sources、cache 命中状态等调试信息。
- `cache`
  - 展示当前请求是否命中缓存与 TTL。

`mysearch_health` 新增：

- `runtime`
- `routing_defaults`
- `cache`

### MCP 传输配置

```env
MYSEARCH_MCP_HOST=127.0.0.1
MYSEARCH_MCP_PORT=8000
MYSEARCH_MCP_MOUNT_PATH=/
MYSEARCH_MCP_SSE_PATH=/sse
MYSEARCH_MCP_STREAMABLE_HTTP_PATH=/mcp
MYSEARCH_MCP_STATELESS_HTTP=false
```

### 直连 provider

可分别配置：

- `MYSEARCH_TAVILY_*`
- `MYSEARCH_FIRECRAWL_*`
- `MYSEARCH_EXA_*`
- `MYSEARCH_XAI_*`

完整示例见：
[.env.example](./.env.example)

## 什么时候该看别的文档

- 你要部署控制台和 token 管理：
  看 [../proxy/README.md](../proxy/README.md)
- 你要让 AI 自动理解怎么安装：
  看 [../skill/README.md](../skill/README.md)
- 你要装到 OpenClaw：
  看 [../openclaw/README.md](../openclaw/README.md)
