# MySearch OpenClaw Skill

[English Guide](./README_EN.md) · [返回仓库](../README.md)

`openclaw/` 这一层是给 `OpenClaw` 和 `ClawHub` 准备的独立 skill bundle。

它和 `skill/` 的区别是：

- `skill/` 主要是给 `Codex` / `Claude Code`
- `openclaw/` 是给 `OpenClaw` / `ClawHub`
- runtime 已经内置在 skill 包里，不需要再额外下载远端代码

这里的目标同样很直接：

- 让人一眼知道 OpenClaw 版怎么安装
- 让 AI 读完 README 和 SKILL 就能直接帮用户安装、配置、验收

## 如果你是用户，最简单的用法

直接把下面这句话发给 AI：

```text
请打开这个仓库里的 openclaw/README.md 和 openclaw/SKILL.md，按文档为我安装 MySearch OpenClaw skill；如果是本地安装就复制到 ~/.openclaw/skills/mysearch，带上 .env，再跑 health 验证，并告诉我结果。
```

如果你发的是 GitHub 链接，也可以直接这样说：

```text
请阅读 https://github.com/skernelx/MySearch-Proxy/tree/main/openclaw 里的 README 和 SKILL，帮我自动安装并验证 MySearch OpenClaw skill。
```

## 如果你是 AI 助手，收到这个目录后应该怎么做

### 1. 先判断这是 OpenClaw skill 入口

当用户给你的是下面任意一种内容时：

- 仓库根链接
- `openclaw/` 目录链接
- 本地 `openclaw/` 路径

默认把这里当成 MySearch OpenClaw skill 的安装入口。

### 2. 优先决定安装方式

有两种常见路径：

- ClawHub 安装
- 本地 bundle 安装

如果用户明确说“从 Hub 安装”，优先引导或执行 Hub 路线。

如果用户是在本地仓库里调试、改造、替换旧 skill，优先走本地 bundle 安装。

### 3. 本地 bundle 安装

先准备配置：

```bash
cp openclaw/.env.example openclaw/.env
```

推荐先填：

```env
MYSEARCH_PROXY_BASE_URL=https://search.hunters.works
MYSEARCH_PROXY_API_KEY=your-mysearch-token
```

这两项配好后，`Tavily / Firecrawl / Exa` 会默认一起走统一 proxy。
如果你的 proxy 同时接通了 `Social / X`，这套 token 也会继续复用，不需要再给
OpenClaw 单独拆一套客户端 token。

如果你暂时没有 proxy，再退回直连 provider：

```env
MYSEARCH_TAVILY_API_KEY=tvly-...
MYSEARCH_FIRECRAWL_API_KEY=fc-...
```

然后执行：

```bash
bash openclaw/scripts/install_openclaw_skill.sh \
  --install-to ~/.openclaw/skills/mysearch \
  --copy-env openclaw/.env
```

这个脚本会：

1. 把 `openclaw/` skill bundle 复制到目标目录
2. 保留 bundled runtime
3. 把 `.env` 一起复制过去
4. 不去下载远端 runtime
5. 不修改别的 skill

### 4. 本地安装后的验收

优先执行：

```bash
python3 ~/.openclaw/skills/mysearch/scripts/mysearch_openclaw.py health
```

如果用户还想补一轮功能烟测，再执行：

```bash
python3 ~/.openclaw/skills/mysearch/scripts/mysearch_openclaw.py search \
  --query "OpenAI latest announcements" \
  --mode web
```

```bash
python3 ~/.openclaw/skills/mysearch/scripts/mysearch_openclaw.py search \
  --query "OpenAI Responses API docs" \
  --mode docs \
  --intent resource
```

如果配置了 X / Social，再补：

```bash
python3 ~/.openclaw/skills/mysearch/scripts/mysearch_openclaw.py search \
  --query "Model Context Protocol" \
  --mode social \
  --intent status
```

## ClawHub 安装怎么理解

公开页面在这里：

- [clawhub.ai/skernelx/mysearch](https://clawhub.ai/skernelx/mysearch)

如果用户的环境已经接好 `ClawHub`，可以先：

```bash
clawhub search "mysearch"
clawhub install <skill-slug>
```

安装后的 env 注入和启用方式，以当前用户的 OpenClaw / ClawHub 运行环境为准。

如果用户要的是“我本机就有这个仓库，直接替换掉现有 skill”，不要绕 Hub，
直接走本地 bundle 安装更稳。

## 最推荐的 provider 接法

最推荐的组合现在是：

- 先部署 `MySearch Proxy`
- 再让 OpenClaw 版 MySearch skill 只接：
  - `MYSEARCH_PROXY_BASE_URL`
  - `MYSEARCH_PROXY_API_KEY`

如果你还没有统一 proxy，也可以继续：

- 先用
  [skernelx/tavily-key-generator](https://github.com/skernelx/tavily-key-generator)
  作为 Tavily / Firecrawl provider 层
- 再让 OpenClaw 版 MySearch skill 直连 provider key

如果用户没有 X / Social：

- 不要判定 skill 无法工作
- `web / news / docs / extract / research` 仍然都可以用

## OpenClaw 装好后 AI 应该怎么用

优先顺序：

1. 先跑 `health`
2. 外部搜索默认优先 MySearch
3. 要正文时用 `extract`
4. 要小型研究包时用 `research`
5. 只有 MySearch 没配好或用户明确要求时，才回退到旧的 Tavily-only skill 或 raw `web_search`

更完整的调用规则见：

- [SKILL.md](./SKILL.md)

## 相关文档

- 仓库总览：
  [../README.md](../README.md)
- MCP 文档：
  [../mysearch/README.md](../mysearch/README.md)
- Codex / Claude Code Skill：
  [../skill/README.md](../skill/README.md)
