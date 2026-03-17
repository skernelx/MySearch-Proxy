---
name: mysearch
description: >-
  DEFAULT search skill for OpenClaw. Aggregates Tavily, Firecrawl, and optional
  X/social search behind one search path. Use for ANY external lookup that needs
  current web results, docs, GitHub, changelog, pricing, URL extraction, or X
  discussion. Prefer this over legacy Tavily-only skill or raw web_search when
  MySearch is healthy.
homepage: https://github.com/skernelx/MySearch-Proxy/tree/main/openclaw
metadata: {"clawdbot":{"emoji":"🔎","requires":{"bins":["bash","python3","curl"]}}}
---

# MySearch For OpenClaw

MySearch 是给 OpenClaw 用的默认搜索 skill。

它不是只包一个搜索源，而是把：

- Tavily
- Firecrawl
- X / Social（可选）

统一成一个搜索入口。

## 一次性安装

如果这个 skill 已经被放进你的 OpenClaw skills 目录，先做一次 bootstrap：

```bash
cp {baseDir}/.env.example {baseDir}/.env
# 编辑 {baseDir}/.env，至少填 Tavily + Firecrawl

bash {baseDir}/scripts/install_openclaw_skill.sh
python3 {baseDir}/scripts/mysearch_openclaw.py health
```

如果你要直接替换旧的 Tavily skill：

```bash
bash {baseDir}/scripts/install_openclaw_skill.sh --replace-skill tavily
```

说明：

- 不填 X / Social 也能正常用
- `install_openclaw_skill.sh` 会安装 skill 本地 runtime 和 `.venv`
- 如果你是从仓库源码安装，也可以传 `--repo-root /path/to/MySearch-Proxy`

## MySearch-First 规则

只要 `health` 显示至少有一个 provider 可用：

- 外部搜索任务优先走 MySearch
- 不要把 raw `web_search` 当主流程
- 不要优先走旧的 Tavily-only skill

只有这些情况才回退：

- MySearch 还没 bootstrap
- 需要的 provider 没配好
- MySearch 返回冲突结果，你要额外复核
- 用户明确要求你换别的搜索方式

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

## 路由原则

- 普通网页、最新动态：Tavily
- 文档、GitHub、pricing、changelog、PDF：Firecrawl
- X / Twitter / 社交舆情：xAI / compatible social gateway
- 单页正文：优先 Firecrawl，失败或空正文时回退 Tavily extract

## 降级规则

- 没配 X / Social 时：
  - `web`
  - `docs`
  - `extract`
  - `research`
  
  仍可用

- 只有 `mode="social"` 会提示你补充 X / Social 配置
- `research --include-social` 在 X 不可用时也不应该把整次任务判成失败

## 输出要求

- 优先给结论，再给来源
- 保留 URL
- 区分事实、引文和推断
- 同时包含网页和 X 时，明确分区，不要混成一句模糊总结
- `max_results` 默认保持小一些，先拿 3 到 5 条

