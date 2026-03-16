# MySearch Proxy

`MySearch Proxy` 是一个可独立发布的搜索基础设施仓库，面向 `Codex`、
`Claude Code` 和自建代理场景。

它把三层内容收在同一个产品仓库里：

- `mysearch/`
  - 可安装的 MCP server
  - 统一聚合 `Tavily + Firecrawl + X`
- `proxy/`
  - 可视化控制台
  - Key 池、Token 池、额度同步、`/social/search`
- `skill/`
  - 可直接给智能体安装的 MySearch Skill
- `docs/mysearch-architecture.md`
  - 架构说明

## 仓库结构

```text
MySearch-Proxy/
├── docs/
│   └── mysearch-architecture.md
├── mysearch/
├── proxy/
├── skill/
└── install.sh
```

## 这份仓库适合做什么

- 给 `Codex` 或 `Claude Code` 安装统一搜索 MCP
- 用 `proxy/` 暴露自己的聚合搜索 API
- 接 Tavily、Firecrawl 和 Social / X 搜索
- 作为公开发布给他人使用的主仓库

## 快速开始

### 1. 安装 MySearch MCP

先准备 MySearch 配置：

```bash
cp mysearch/.env.example mysearch/.env
```

填入你自己的 API 配置后执行：

```bash
./install.sh
```

安装脚本会：

- 安装 `mysearch/requirements.txt`
- 自动注册到 `Claude Code`
- 自动注册到 `Codex`
- 自动读取 `mysearch/.env` 里的 `MYSEARCH_*` 和 `SOCIAL_GATEWAY_*`

### 2. 启动 Proxy 控制台

先准备 Proxy 配置：

```bash
cp proxy/.env.example proxy/.env
```

然后启动：

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

## 文档入口

- [mysearch/README.md](./mysearch/README.md)
- [proxy/README.md](./proxy/README.md)
- [docs/mysearch-architecture.md](./docs/mysearch-architecture.md)
- [skill/SKILL.md](./skill/SKILL.md)
