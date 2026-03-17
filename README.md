# MySearch Proxy

`MySearch Proxy` 是一个面向 `Codex`、`Claude Code` 和自建 Agent 工作流
的通用搜索基础设施项目。

它不是单一 provider 的薄封装，而是一整套可以直接落地的搜索工作台：

- `mysearch/`
  - 可安装的 MCP server
  - 统一聚合 `Tavily + Firecrawl + Social / X`
- `proxy/`
  - 可视化控制台
  - Key 池、Token 池、额度同步、`/social/search`
- `skill/`
  - 可直接给智能体安装的 MySearch Skill
- `openclaw/`
  - 可上传到 OpenClaw Hub 的独立搜索 skill
  - 自带 OpenClaw bootstrap 脚本和运行时安装脚本

## 界面预览

### 首屏

![MySearch Console Hero](./docs/images/mysearch-console-hero.jpg)

### 工作台

![MySearch Console Workspaces](./docs/images/mysearch-console-workspaces.jpg)

## 这个项目的优势

和很多“只包一层 API”或“只做单一搜索源”的项目相比，`MySearch Proxy`
更适合真实的 Agent 场景。

- 自动路由，不用手动挑 provider
  - 普通网页、新闻、快速答案优先走 Tavily
  - 文档站、GitHub、PDF、pricing、changelog 优先走 Firecrawl
  - 社交舆情、X 搜索走 Social / X
- 不只是 MCP
  - 同一个仓库里同时提供 `MCP + Proxy Console + Skill`
  - 既能给本地 Agent 安装，也能作为团队共享搜索网关部署
- 官方优先，也支持兼容网关
  - 可以直接接官方 API
  - 也可以接你自己的 compatible 网关或聚合 API
- 更适合研究型搜索
  - 内置 `intent` 和 `strategy`
  - 支持 `search`、`extract_url`、`research`
  - 不只是返回链接，还能做更稳定的证据组织
- X / Social 可选启用
  - 没有 `grok2api` 或官方 `xAI` key 时，`MySearch` 仍可正常提供
    `web`、`docs`、`extract_url`、`research`
  - 只有 `mode="social"` 这类 X 路由会被关闭，不会把整个 MCP 一起拖挂
- Social / X 是一等公民
  - 不再把 X 搜索塞成附属脚本
  - 支持单独的 `/social/search`、兼容 `grok2api`、统一输出结构
- 适合公开发布
  - 有完整控制台
  - 有独立 Key 池和 Token 池
  - 有服务隔离和额度同步

## 和 `tavily-key-generator` 的关系

`MySearch Proxy` 是产品仓库。  
`tavily-key-generator` 是它的重要 provider 基础设施来源之一。

推荐搭配项目：

- [skernelx/tavily-key-generator](https://github.com/skernelx/tavily-key-generator)

这个项目可以负责：

- Tavily / Firecrawl key 的注册
- key 真实可用性验证
- 可选上传到代理池
- 作为 `MySearch Proxy` 的 Tavily / Firecrawl provider 来源

也就是说，`MySearch Proxy` 并不是孤立工作的。

一个更完整的部署方式通常是：

```text
tavily-key-generator
  -> 提供 Tavily / Firecrawl key 来源
  -> 可作为聚合 API / provider source

MySearch Proxy
  -> 负责 MCP、Proxy Console、Skill、Social / X 路由
  -> 统一对外提供搜索能力
```

如果你想直接把 Tavily / Firecrawl 的 provider 层也接进来，推荐先看
[`tavily-key-generator`](https://github.com/skernelx/tavily-key-generator)。

## 适合谁用

- 想给 `Codex` 或 `Claude Code` 安装一个更强的通用搜索 MCP
- 想把 `Tavily + Firecrawl + X` 聚合成一个统一搜索入口
- 想让团队共用一套可视化搜索网关
- 想保留官方 API 兼容性，同时又能接自己的聚合服务

## 快速开始

### 1. 安装 MySearch MCP

准备配置：

```bash
cp mysearch/.env.example mysearch/.env
```

最小可用配置只需要先填 `Tavily + Firecrawl`：

```env
MYSEARCH_TAVILY_API_KEY=tvly-...
MYSEARCH_FIRECRAWL_API_KEY=fc-...
```

如果你还要启用 X / Social，再额外填写：

```env
MYSEARCH_XAI_API_KEY=xai-...
```

或者：

```env
MYSEARCH_XAI_BASE_URL=https://your-compatible-gateway.example.com/v1
MYSEARCH_XAI_SOCIAL_BASE_URL=https://your-social-gateway.example.com
MYSEARCH_XAI_SEARCH_MODE=compatible
MYSEARCH_XAI_API_KEY=your-gateway-token
```

填入配置后执行：

```bash
./install.sh
```

安装脚本会：

- 安装 `mysearch/requirements.txt`
- 自动注册到 `Claude Code`
- 自动注册到 `Codex`
- 自动读取 `mysearch/.env` 里的 `MYSEARCH_*` 和 `SOCIAL_GATEWAY_*`

如果暂时没有 `grok2api` 或 `xAI`：

- `search(mode="web")` 仍可用
- `search(mode="docs")` 仍可用
- `extract_url(...)` 仍可用
- `research(...)` 仍会返回网页部分，只是在结果里给出 `social_error`
- 只有 `search(mode="social")` 会提示你补充 `xAI` / social gateway 配置

### 1.5 安装到 OpenClaw

如果你要把 `MySearch` 作为 OpenClaw 的默认搜索 skill，使用仓库里的
`openclaw/`：

```bash
cp openclaw/.env.example openclaw/.env
# 编辑 openclaw/.env

bash openclaw/scripts/install_openclaw_skill.sh \
  --install-to ~/.openclaw/skills/mysearch \
  --repo-root "$(pwd)"
```

如果你要直接替换旧的 Tavily skill：

```bash
bash openclaw/scripts/install_openclaw_skill.sh \
  --install-to ~/.openclaw/skills/mysearch \
  --repo-root "$(pwd)" \
  --replace-skill tavily
```

安装完成后先验收：

```bash
python3 ~/.openclaw/skills/mysearch/scripts/mysearch_openclaw.py health
```

### 2. 启动 Proxy 控制台

准备 Proxy 配置：

```bash
cp proxy/.env.example proxy/.env
```

启动：

```bash
cd proxy
docker compose up -d
```

或者本地运行：

```bash
cd proxy
pip install -r requirements.txt
set -a && source .env && set +a
uvicorn server:app --host 0.0.0.0 --port 9874
```

### 3. 推荐的完整组合

如果你想把 Tavily / Firecrawl 这一层也搭完整，推荐组合是：

1. 先部署 [tavily-key-generator](https://github.com/skernelx/tavily-key-generator)
2. 用它准备 Tavily / Firecrawl 的 key 来源或聚合 API
3. 再部署 `MySearch Proxy`
4. 最后把 `mysearch` 安装到 `Codex` 或 `Claude Code`

## 仓库结构

```text
MySearch-Proxy/
├── docs/
│   └── mysearch-architecture.md
├── mysearch/
├── openclaw/
├── proxy/
├── skill/
└── install.sh
```

## 文档入口

- [mysearch/README.md](./mysearch/README.md)
- [openclaw/SKILL.md](./openclaw/SKILL.md)
- [proxy/README.md](./proxy/README.md)
- [docs/mysearch-architecture.md](./docs/mysearch-architecture.md)
- [skill/SKILL.md](./skill/SKILL.md)
