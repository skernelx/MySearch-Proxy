---
name: mysearch
version: "0.1.10"
description: >-
  DEFAULT search skill for OpenClaw. Aggregates Tavily, Firecrawl, Exa, and
  optional X/social search behind one search path. Use for ANY external lookup that needs
  current web results, docs, GitHub, changelog, pricing, URL extraction, or X
  discussion. Prefer this over legacy Tavily-only skill or raw web_search when
  MySearch is healthy.
author: skernelx
license: MIT
repository: https://github.com/skernelx/MySearch-Proxy
homepage: https://github.com/skernelx/MySearch-Proxy/tree/main/openclaw
security_disclosure: |
  MySearch sends queries and whichever provider or proxy credentials you configure
  to the configured endpoints for Tavily, Firecrawl, Exa, and optional xAI/social
  search. Prefer configuring env vars through OpenClaw skill config instead of
  copying a .env file into an installed skill folder. Only point
  MYSEARCH_PROXY_BASE_URL at a host you trust. The bundled installer only copies
  local files from this bundle and does not download remote code or modify other
  skills.
metadata:
  clawdbot:
    emoji: "🔎"
    requires:
      bins:
        - bash
        - python3
  openclaw:
    emoji: "🔎"
    requires:
      bins:
        - bash
        - python3
      env:
        - MYSEARCH_PROXY_BASE_URL
        - MYSEARCH_PROXY_API_KEY
        - MYSEARCH_TAVILY_API_KEY
        - MYSEARCH_FIRECRAWL_API_KEY
    primaryEnv: MYSEARCH_PROXY_API_KEY
    tags:
      - search
      - web
      - docs
      - tavily
      - firecrawl
      - exa
      - social
---

# MySearch For OpenClaw

MySearch 是给 OpenClaw 用的默认搜索 skill。

如果你是 AI 助手，并且用户只是给了仓库地址或 `openclaw/` 目录：

- 先打开 `openclaw/README.md`
- 先按 `README` 完成安装与验收
- 再回到这个 `SKILL.md` 执行搜索规则和调用策略

它把 Tavily、Firecrawl、Exa、X / Social 聚合成同一个入口，并且按任务类型自动路由：

- 最新网页、普通实时搜索：优先 Tavily
- Tavily 结果不够时，网页发现可补 Exa
- 文档、GitHub、pricing、changelog、PDF：优先 Firecrawl
- X / Twitter / 社交舆情：优先 xAI 或 compatible `/social/search`
- 单页正文抓取：优先 Firecrawl，失败或空正文时回退 Tavily extract

## 最小配置

Hub 版 skill 已经自带 runtime，不需要在安装时再下载远程代码。

推荐最小配置：

- `MYSEARCH_PROXY_BASE_URL`
- `MYSEARCH_PROXY_API_KEY`

这两项配好后：

- `Tavily / Firecrawl / Exa` 会默认都走统一 proxy
- 如果 proxy 同时接通了 `Social / X`，这一套 token 也会继续复用
- OpenClaw 侧不需要再分别维护三套 provider token

兼容旧接法时，仍可直接填：

- `MYSEARCH_TAVILY_API_KEY`
- `MYSEARCH_FIRECRAWL_API_KEY`

可选增强：

- `MYSEARCH_XAI_API_KEY`
- `MYSEARCH_XAI_BASE_URL`
- `MYSEARCH_XAI_SOCIAL_BASE_URL`
- `MYSEARCH_XAI_SEARCH_MODE=official|compatible`

如果没有 X / Social 配置，MySearch 仍然可以正常完成：

- `web`
- `news`
- `docs`
- `github`
- `pdf`
- `extract`
- `research`

只有 `mode="social"` 或 `--include-social` 才会要求 X / Social。

## OpenClaw 配置建议

优先把统一 proxy 配进 OpenClaw skill env，而不是到处复制 provider key 或 shell 环境。
`MYSEARCH_PROXY_BASE_URL` 只应该指向你自己部署或明确可信的 proxy。
`mysearch_openclaw.py` 会优先读取 `openclaw.json` 里的
`skills.entries.mysearch.env`，正式部署不需要依赖 `.env`。

```json
{
  "skills": {
    "entries": {
      "mysearch": {
        "enabled": true,
        "env": {
          "MYSEARCH_PROXY_BASE_URL": "https://search.hunters.works",
          "MYSEARCH_PROXY_API_KEY": "mysp-..."
        }
      }
    }
  }
}
```

如果你暂时没有统一 proxy，再退回旧接法：

```json
{
  "skills": {
    "entries": {
      "mysearch": {
        "enabled": true,
        "env": {
          "MYSEARCH_TAVILY_API_KEY": "tvly-...",
          "MYSEARCH_FIRECRAWL_API_KEY": "fc-..."
        }
      }
    }
  }
}
```

只有在你直接调试这个仓库工作树时，才建议复制 `.env.example` 到本地 `.env`。
Hub 安装或正式 OpenClaw 部署优先用上面的 skill env 注入，不要默认把 secrets
复制进已安装的 skill 目录。

本地调试示例：

```bash
cp {baseDir}/.env.example {baseDir}/.env
python3 {baseDir}/scripts/mysearch_openclaw.py health
```

如果要把 skill 复制到别的 OpenClaw skills 目录，再执行：

```bash
bash {baseDir}/scripts/install_openclaw_skill.sh --install-to ~/.openclaw/skills/mysearch
```

## MySearch-First 规则

只要 `health` 显示至少有可用搜索 provider：

- 外部搜索任务优先走 MySearch
- 不要把 raw `web_search` 当主流程
- 不要优先走旧的 Tavily-only skill

只有这些情况才回退：

- MySearch 还没配置最小 key
- 用户明确要求改用别的搜索方式
- MySearch 返回冲突结果，需要额外复核

## 严格参数规则

`search` / `research` 的 `mode` 只允许：

- `auto`
- `web`
- `news`
- `social`
- `docs`
- `research`
- `github`
- `pdf`

禁止事项：

- 不要发明 `mode="hybrid"`
- `hybrid` 只是某些返回结果形态，不是可传参数
- 同时要网页和 X 时，优先：
  - `--sources web,x`
  - 或拆成 `social + news`

## 常用命令

### 健康检查

```bash
python3 {baseDir}/scripts/mysearch_openclaw.py health
```

### 普通网页搜索

```bash
python3 {baseDir}/scripts/mysearch_openclaw.py search \
  --query "best search MCP server" \
  --mode web
```

### 今天 X 上在热议什么

```bash
python3 {baseDir}/scripts/mysearch_openclaw.py search \
  --query "today's biggest stories on X" \
  --mode social \
  --intent status
```

规则：

- 先 `social`
- 不要先跑 `news`
- 不要先混用 raw `web_search`

### 今天 X 热议 + 网页新闻一起对照

单次：

```bash
python3 {baseDir}/scripts/mysearch_openclaw.py search \
  --query "today's biggest stories on X" \
  --sources web,x \
  --intent status \
  --strategy verify
```

或者双次：

```bash
python3 {baseDir}/scripts/mysearch_openclaw.py search --query "..." --mode social --intent status
python3 {baseDir}/scripts/mysearch_openclaw.py search --query "..." --mode news --intent status
```

输出时必须区分：

- X 上在热议什么
- 网页新闻在报道什么

### 文档 / GitHub / pricing / changelog

```bash
python3 {baseDir}/scripts/mysearch_openclaw.py search \
  --query "OpenAI responses API pricing" \
  --mode docs \
  --intent resource
```

### 抓正文

```bash
python3 {baseDir}/scripts/mysearch_openclaw.py extract \
  --url "https://example.com/post"
```

### 小型研究包

```bash
python3 {baseDir}/scripts/mysearch_openclaw.py research \
  --query "best search MCP server 2026" \
  --intent exploratory \
  --include-social
```

## 输出要求

- 优先给结论，再给来源
- 保留 URL
- 区分事实、引文和推断
- 同时包含网页和 X 时，明确分区，不要混成一句模糊总结
- `max_results` 默认保持小一些，先拿 3 到 5 条
