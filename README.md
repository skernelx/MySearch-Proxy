# MySearch Proxy

[English Guide](./README_EN.md)

`MySearch Proxy` 是一个面向公开分发的通用搜索项目。

它把三件原本分散的事情收成了一套完整方案：

- 给 `Codex` / `Claude Code` 安装的搜索 `MCP`
- 给 `Codex` / `Claude Code` / `OpenClaw` 安装的搜索 `Skill`
- 给团队或自建 Agent 使用的统一搜索 `Proxy Console`

项目入口：

- GitHub：
  [skernelx/MySearch-Proxy](https://github.com/skernelx/MySearch-Proxy)
- OpenClaw Hub Skill：
  [clawhub.ai/skernelx/mysearch](https://clawhub.ai/skernelx/mysearch)
- Tavily / Firecrawl 默认推荐搭档：
  [skernelx/tavily-key-generator](https://github.com/skernelx/tavily-key-generator)

![MySearch Console Hero](./docs/images/mysearch-console-hero.jpg)

这不是一个“只包一层 Tavily”的小工具。

它的核心目标是：把 `Tavily`、`Firecrawl`、`X / Social` 三条搜索能力线
统一到一个可安装、可复用、可公开发布的产品里，并且让调用方尽量少关心
底层 provider 差异。

公开页面状态可以直接看 ClawHub：

- 技能页面：
  [clawhub.ai/skernelx/mysearch](https://clawhub.ai/skernelx/mysearch)
- 下图是 `2026-03-17` 从公开页面截取的真实截图
- 当前最新结果请始终以 ClawHub 实时页面为准

![MySearch Skill Security Scan](./docs/images/mysearch-skill-security-scan.jpg)

## 这个仓库里有什么

- [`mysearch/`](./mysearch/README.md)
  - 真正可安装的 MySearch MCP
  - 统一提供 `search`、`extract_url`、`research`、`mysearch_health`
- [`proxy/`](./proxy/README.md)
  - 统一控制台与代理层
  - 管理 Tavily / Firecrawl key 池、下游 token、额度同步、`/social/search`
- `skill/`
  - 给 `Codex` / `Claude Code` 用的 MySearch skill
- `openclaw/`
  - 给 OpenClaw / ClawHub 用的独立 skill bundle
  - runtime 已内置，便于分发、审计和复用
- [`docs/mysearch-architecture.md`](./docs/mysearch-architecture.md)
  - 架构与设计边界说明

## 这个项目到底在解决什么问题

很多“搜索类 MCP / Skill”都有一个共同问题：

- 只会搜网页，不会抓正文
- 只适合新闻，不适合文档站、GitHub、PDF、pricing、changelog
- 只解决 prompt，不解决真正的 MCP 安装和运行
- 只做 key 面板，不解决 AI 怎么调用
- 只支持官方接口，接自己网关时要重写一遍逻辑
- 一旦没有 X / Social provider，整套链路就失去价值

`MySearch Proxy` 的做法不是继续叠一个“万能大接口”，而是把系统分成四层：

```text
tavily-key-generator
  -> 提供 Tavily / Firecrawl provider 层与聚合 API

MySearch Proxy
  -> 提供 MCP、Skill、OpenClaw Skill、Proxy Console、Social / X 路由

Codex / Claude Code / OpenClaw / 自建 Agent
  -> 直接复用同一套搜索能力
```

默认推荐组合不是“到处手填官方 key”，而是：

- `tavily-key-generator` 负责 Tavily / Firecrawl 的 provider 来源
- `MySearch Proxy` 负责统一搜索逻辑、MCP、Skill 和 Proxy Console

## 和同类项目相比，优势在哪里

### 1. 不是单一 provider MCP

`MySearch` 会按任务类型自动选路由：

- 普通网页、新闻、快速发现：优先 Tavily
- 文档站、GitHub、PDF、pricing、changelog、正文抓取：优先 Firecrawl
- X / Social：优先 xAI 或 compatible `/social/search`

这意味着它不是把所有问题都硬塞给一个 provider。

### 2. 不是只有 prompt，没有真正运行时

这个仓库同时给你：

- MCP
- Codex / Claude Code skill
- OpenClaw skill
- Proxy Console

所以它既能给本地开发助手用，也能给 OpenClaw 和团队网关用，而不是换一个
运行环境就推倒重来。

### 3. 不是只有“搜索”，还包括提取和小型研究

这里的核心能力不是只有 `search`：

- `extract_url`
  - 优先 Firecrawl，失败或空正文时回退 Tavily extract
- `research`
  - 负责把搜索、抓取、证据整合成一个小型研究工作流

这比“搜完扔几个链接”更适合真实 Agent 使用。

### 4. 官方优先，但不绑定死官方接口

你可以：

- 直接填官方 Tavily / Firecrawl / xAI key
- 也可以改 `BASE_URL + PATH + AUTH_*`
- 还能把 Tavily / Firecrawl 接到自己的聚合 API
- X / Social 还能接到 compatible `/social/search`

这点对准备公开发布、又想兼容自建网关的人很重要。

### 5. X / Social 是增强项，不是安装门槛

如果没有官方 `xAI` 或 `grok2api`，这套项目仍然可以正常提供：

- `web`
- `news`
- `docs`
- `github`
- `pdf`
- `extract`
- `research`

只有明确的 `social` 路由会降级，而不是整套系统一起失效。

## 可以用在哪里

### 1. 本地开发助手的默认搜索入口

适合：

- `Codex`
- `Claude Code`
- 其他支持 MCP 的本地 AI 助手

用途：

- 最新网页搜索
- 技术文档 / GitHub / 价格页 / changelog 检索
- 单页正文抓取
- 小型研究包
- X / Social 舆情补充

### 2. OpenClaw 的默认搜索 skill

适合：

- 想替换旧的 Tavily-only 搜索 skill
- 想让 OpenClaw 拥有更完整的 web + docs + social 搜索能力
- 想发布到 ClawHub 供别人直接安装

### 3. 团队共享搜索网关

适合：

- 多个下游程序共用一套搜索入口
- 想分离上游 key 和下游 token
- 需要可视化管理 Tavily / Firecrawl / Social / X

### 4. 自己的聚合 API / compatible 网关接线

适合：

- 你已经有自己的 Tavily / Firecrawl 聚合 API
- 你有 `grok2api` 或其他 xAI-compatible 服务
- 你想把调用逻辑统一回 MySearch，而不是散在脚本里

## 默认推荐怎么接

最推荐的完整组合是：

```text
tavily-key-generator
  -> 提供 Tavily / Firecrawl 官方 provider 或聚合 API

MySearch Proxy
  -> 接入 Tavily / Firecrawl / X
  -> 暴露 MCP、Skill、OpenClaw Skill、Proxy Console

Codex / Claude Code / OpenClaw / 自建 Agent
  -> 使用 MySearch 作为统一搜索入口
```

为什么默认推荐 `tavily-key-generator`：

- 它可以作为 Tavily / Firecrawl 的 provider 层
- 你不需要在每个下游实例里都直接暴露官方 key
- MySearch 只需要接它暴露出来的统一入口即可

如果你已经有官方 key，也完全可以直接接官方接口；只是对公开部署和团队共用来
说，`tavily-key-generator -> MySearch Proxy` 这条链路通常更稳。

## Provider 支持与缺失后的行为

### Tavily

主要负责：

- 普通网页搜索
- 新闻检索
- 快速发现和默认 research 发现阶段

推荐接法：

- 官方 API
- 或通过
  [skernelx/tavily-key-generator](https://github.com/skernelx/tavily-key-generator)
  提供的聚合 API / provider source

不接 Tavily 时：

- `web`
- `news`
- 默认 `research` 的发现阶段

会明显变弱，但 `docs / github / pdf / extract` 仍可以部分依赖 Firecrawl。

### Firecrawl

主要负责：

- docs
- GitHub
- PDF
- pricing
- changelog
- 正文抓取

推荐接法：

- 官方 API
- 或通过
  [skernelx/tavily-key-generator](https://github.com/skernelx/tavily-key-generator)
  提供的聚合 API / provider source

不接 Firecrawl 时：

- `docs / github / pdf / pricing / changelog`
- 正文抓取质量

会下降，但普通网页和新闻仍可以由 Tavily 承担，`extract_url` 也会尽量回退到
Tavily extract。

### X / Social

主要负责：

- X / Social 搜索
- 舆情
- 开发者讨论

推荐接法：

- 官方 xAI
- 或 compatible `/social/search`

不接 X / Social 时：

- `mode="social"` 不可用
- `research(include_social=true)` 仍然会返回网页结果，并附带 `social_error`

也就是说，没有 X 不会阻止这个项目作为通用搜索 MCP / Skill 工作。

## 安装方式

你不需要一次把所有部分都装上，可以按目标选路径。

### 1. 安装 MySearch MCP 到 Codex / Claude Code

```bash
python3 -m venv venv
cp mysearch/.env.example mysearch/.env
./install.sh
```

最小配置：

```env
MYSEARCH_TAVILY_API_KEY=tvly-...
MYSEARCH_FIRECRAWL_API_KEY=fc-...
```

如果你准备直接接
[tavily-key-generator](https://github.com/skernelx/tavily-key-generator)，
可以改成：

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

`install.sh` 会：

1. 安装 `mysearch/requirements.txt`
2. 自动检测并注册 `Claude Code`
3. 自动检测并注册 `Codex`
4. 把 `mysearch/.env` 里的 `MYSEARCH_*` 注入 MCP 配置

### 2. 安装 Codex / Claude Code skill

如果你希望 AI 不只“看见一个 MCP”，还知道该怎么调用它，再装 skill：

```bash
bash skill/scripts/install_codex_skill.sh
```

如果要覆盖旧版本：

```bash
bash skill/scripts/install_codex_skill.sh --force
```

### 3. 安装 OpenClaw skill

先看公开页面：

- [clawhub.ai/skernelx/mysearch](https://clawhub.ai/skernelx/mysearch)

ClawHub CLI 的通用安装方式请以官方文档为准，当前官方文档写法是：

```bash
clawhub search "mysearch"
clawhub install <skill-slug>
```

如果你要从本地 bundle 安装：

```bash
cp openclaw/.env.example openclaw/.env
bash openclaw/scripts/install_openclaw_skill.sh \
  --install-to ~/.openclaw/skills/mysearch \
  --copy-env openclaw/.env
```

### 4. 部署 Proxy Console

```bash
cd proxy
docker compose up -d
```

或者：

```bash
docker run -d \
  --name mysearch-proxy \
  --restart unless-stopped \
  -p 9874:9874 \
  -e ADMIN_PASSWORD=your-admin-password \
  -v $(pwd)/mysearch-proxy-data:/app/data \
  your-registry/mysearch-proxy:latest
```

启动后访问：

```text
http://localhost:9874
```

![MySearch Console Workspaces](./docs/images/mysearch-console-workspaces.jpg)

## X / Social 怎么配置

### 官方 xAI 模式

```env
MYSEARCH_XAI_BASE_URL=https://api.x.ai/v1
MYSEARCH_XAI_RESPONSES_PATH=/responses
MYSEARCH_XAI_SEARCH_MODE=official
MYSEARCH_XAI_API_KEY=xai-...
```

### compatible / 自定义 `/social/search` 模式

```env
MYSEARCH_XAI_BASE_URL=https://media.example.com/v1
MYSEARCH_XAI_SOCIAL_BASE_URL=https://your-social-gateway.example.com
MYSEARCH_XAI_SEARCH_MODE=compatible
MYSEARCH_XAI_API_KEY=your-social-gateway-token
```

说明：

- `MYSEARCH_XAI_BASE_URL` 指向模型或 `/responses` 网关
- `MYSEARCH_XAI_SOCIAL_BASE_URL` 指向 social gateway 根地址
- MySearch 默认会自动追加 `/social/search`

如果你的 social 侧来自 `grok2api`，`proxy/` 还能直接对接后台管理接口，自动
继承 `app.api_key` 并读取 token 状态。

## 快速验收

MCP 安装后：

```bash
claude mcp list
codex mcp list
codex mcp get mysearch
```

本地烟测：

```bash
python skill/scripts/check_mysearch.py --health-only
python skill/scripts/check_mysearch.py --web-query "OpenAI latest announcements"
python skill/scripts/check_mysearch.py --docs-query "OpenAI Responses API docs"
```

如果你配置了 X / Social：

```bash
python skill/scripts/check_mysearch.py --social-query "Model Context Protocol"
```

OpenClaw bundle 验收：

```bash
python3 openclaw/scripts/mysearch_openclaw.py health
```

## 如果少一项支持，会发生什么

### 没有 `grok2api` 或官方 `xAI`

项目仍然可用。

你仍然可以正常使用：

- `web`
- `news`
- `docs`
- `github`
- `pdf`
- `extract`
- `research`

只有明确依赖 `social` 的请求会降级。

### 没有 Tavily / Firecrawl 官方 key

默认建议不是放弃，而是优先接入：

- [skernelx/tavily-key-generator](https://github.com/skernelx/tavily-key-generator)

也就是说，这个项目默认就支持“官方接口”与“自己的聚合 API”两种接法。

## 子目录文档

- 根仓库说明：
  [README.md](./README.md)
- MCP 文档：
  [mysearch/README.md](./mysearch/README.md)
- MCP English：
  [mysearch/README_EN.md](./mysearch/README_EN.md)
- Proxy 文档：
  [proxy/README.md](./proxy/README.md)
- Proxy English：
  [proxy/README_EN.md](./proxy/README_EN.md)
- 架构文档：
  [docs/mysearch-architecture.md](./docs/mysearch-architecture.md)

## 这个仓库适合谁

如果你是下面几类人，这个项目会比较合适：

- 想给 `Codex` / `Claude Code` 一个比单一搜索源更稳的默认搜索 MCP
- 想给 OpenClaw 提供一个可公开发布、可安装、可审计的搜索 skill
- 想把 Tavily、Firecrawl、X / Social 收到同一个控制台里
- 想默认走自己的聚合 API，而不是把官方 key 暴露到所有下游

如果你只需要“单个脚本查一下网页”，那这个仓库会偏完整。

如果你需要“一个可安装、可发布、可复用的通用搜索基础设施”，它就是为这个
场景准备的。
