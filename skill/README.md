# MySearch Skill

[English Guide](./README_EN.md) · [返回仓库](../README.md)

`skill/` 这一层的目标很简单：

- 让 `Codex` 知道怎么安装和怎么使用 `MySearch`
- 让 `Claude Code` 在拿到仓库链接时，也能顺着文档把 MCP 安装好并开始用

这里不是单独的 MCP 实现目录。

这里更像一份“给 AI 看的安装与使用说明书”：

- `README.md`
  - 先告诉 AI 怎么装
  - 再告诉 AI 怎么验收
- `SKILL.md`
  - 再告诉 AI 装好之后该怎么调 MySearch

## 如果你是用户，最简单的用法

直接把下面这句话发给 `Codex` 或 `Claude Code`：

```text
请打开这个仓库里的 skill/README.md 和 skill/SKILL.md，按文档为我安装 MySearch；如果 MCP 还没注册，就在仓库根目录执行 install.sh；安装完成后跑 health 和 smoke test，并告诉我结果。
```

如果你发的是 GitHub 链接，也可以直接这样说：

```text
请阅读 https://github.com/skernelx/MySearch-Proxy/tree/main/skill 里的 README 和 SKILL，帮我自动安装并验证 MySearch。
```

如果你手里已经有一个部署好的 `MySearch streamableHTTP` 地址，也可以直接这样发：

```text
请把这个 MySearch MCP 地址注册到 Codex 并验证可用性：http://127.0.0.1:8000/mcp。如果需要鉴权，请使用环境变量 MYSEARCH_MCP_BEARER_TOKEN。
```

## 如果你是 AI 助手，收到这个目录后应该怎么做

### 1. 先判断这是 skill 入口，不是单独的 MCP 包

当用户给你的是下面任意一种内容时：

- 仓库根链接
- `skill/` 目录链接
- 本地 `skill/` 路径

默认把这里当成 MySearch 的“安装与使用入口”。

### 2. 先安装 Codex skill

如果当前环境是 `Codex`，优先执行：

```bash
bash skill/scripts/install_codex_skill.sh
```

如果目标目录已存在，需要覆盖：

```bash
bash skill/scripts/install_codex_skill.sh --force
```

安装完成后提醒用户：

- 重启 `Codex`

### 3. 再确认 MySearch MCP 是否已安装

如果用户给你的是源码仓库，继续走本地安装：

在仓库根目录执行：

```bash
python3 -m venv venv
```

优先把配置直接写进宿主 config：

- `Codex`：`~/.codex/config.toml` 的 `mcp_servers.mysearch.env`
- `Claude Code`：注册 MCP 时把 `MYSEARCH_*` 注入 env

只有在本地单仓调试、又不方便改宿主配置时，才准备：

```bash
cp mysearch/.env.example mysearch/.env
```

再按需填写：

- `MYSEARCH_TAVILY_*`
- `MYSEARCH_FIRECRAWL_*`
- 可选 `MYSEARCH_XAI_*`

然后执行：

```bash
./install.sh
```

说明：

- `skill/` 负责让 AI 知道怎么用
- 根目录 `install.sh` 负责把 `mysearch` MCP 注册进 `Codex` / `Claude Code`
- 两者互补，不要只做其中一件

如果用户给你的不是源码仓库，而是已经部署好的 `MySearch streamableHTTP` URL，
优先按远程 MCP 处理，不要再让用户本地执行 `./install.sh`：

```bash
codex mcp add mysearch --url http://127.0.0.1:8000/mcp
codex mcp get mysearch
```

如果远程入口需要 Bearer Token：

```bash
export MYSEARCH_MCP_BEARER_TOKEN=your-token
codex mcp add mysearch \
  --url https://mysearch.example.com/mcp \
  --bearer-token-env-var MYSEARCH_MCP_BEARER_TOKEN
codex mcp get mysearch
```

这里要分清：

- 本地仓库安装 = `stdio`
- 远程 URL 接入 = `streamableHTTP`
- `OpenClaw` 走的是 `openclaw/` bundle，不依赖这条远程 MCP URL

## 最推荐的 provider 接法

默认推荐不是手动直填所有官方 key，而是：

- 先用
  [skernelx/tavily-key-generator](https://github.com/skernelx/tavily-key-generator)
  提供 Tavily / Firecrawl provider 或聚合 API
- 再让 MySearch 接这层统一入口

这样做的好处：

- 对公开项目更友好
- 对团队共用更稳
- 更适合让 AI 直接安装和复用

## 安装完成后怎么验收

优先按这个顺序：

```bash
codex mcp list
codex mcp get mysearch
python skill/scripts/check_mysearch.py --health-only
python skill/scripts/check_mysearch.py --web-query "OpenAI latest announcements"
python skill/scripts/check_mysearch.py --docs-query "OpenAI Responses API docs"
```

如果配置了 X / Social，再补：

```bash
python skill/scripts/check_mysearch.py --social-query "Model Context Protocol"
```

如果要测正文抓取：

```bash
python skill/scripts/check_mysearch.py \
  --extract-url "https://www.anthropic.com/news/model-context-protocol"
```

## Claude Code 怎么理解这份 skill

这个目录里目前提供的是：

- 给 `Codex` 的本地 skill 安装脚本
- 给 `Codex / Claude Code` 共用的使用规则和安装说明

也就是说：

- `Codex` 可以直接安装本地 skill
- `Claude Code` 即使没有单独的 skill 安装脚本，也可以通过阅读这份
  `README.md` 和 `SKILL.md` 完成 MCP 安装、验收和后续使用

## 安装完成后 AI 应该怎么用 MySearch

装完以后，不要回到 generic web search。

优先顺序应该是：

1. 先看 `mysearch_health`
2. 默认从 `search` 起手
3. 需要正文时用 `extract_url`
4. 需要小型研究包时用 `research`
5. 只有 MySearch 不可用或用户明确要求时，才回退到别的搜索工具

更完整的调用规则见：

- [SKILL.md](./SKILL.md)

## 相关文档

- 仓库总览：
  [../README.md](../README.md)
- MCP 文档：
  [../mysearch/README.md](../mysearch/README.md)
- Proxy 控制台：
  [../proxy/README.md](../proxy/README.md)
