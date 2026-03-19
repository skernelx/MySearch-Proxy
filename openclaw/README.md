# MySearch OpenClaw Skill

[English Guide](./README_EN.md) · [返回仓库](../README.md)

`openclaw/` 是给 OpenClaw 和 ClawHub 准备的独立 skill bundle。

这一层和 `skill/` 的区别很明确：

- `skill/`
  - 主要负责让 Codex / Claude Code 知道怎么安装与使用 MySearch
- `openclaw/`
  - 负责给 OpenClaw 提供真正可安装的 skill bundle

这个 bundle 自带 runtime，不需要再额外下载远端代码。

## 适合什么时候用

- 你想把 MySearch 装进 OpenClaw
- 你想从 ClawHub 安装或更新
- 你想在本地仓库里直接替换 skill
- 你希望 OpenClaw 也走统一的 `proxy-first` 搜索链

公开页面：

- ClawHub：
  [clawhub.ai/skernelx/mysearch](https://clawhub.ai/skernelx/mysearch)

## 当前推荐配置

最推荐的是只给 skill 注入两项环境变量：

```env
MYSEARCH_PROXY_BASE_URL=https://your-mysearch-proxy.example.com
MYSEARCH_PROXY_API_KEY=mysp-...
```

这样做的好处：

- Tavily / Firecrawl / Exa 默认共用同一个 token
- 如果 Proxy 已接通 Social / X，也可以继续走同一套 token
- OpenClaw 侧配置最少
- 不需要把一堆 provider key 再散到 skill 里

如果你暂时没有 Proxy，再回退到直连 provider：

```env
MYSEARCH_TAVILY_API_KEY=tvly-...
MYSEARCH_FIRECRAWL_API_KEY=fc-...
MYSEARCH_EXA_API_KEY=exa-...
MYSEARCH_XAI_API_KEY=xai-...
```

## 安装方式

### 方式 A：从 ClawHub 安装

如果你的环境已经接好了 ClawHub，这是最省心的方式。

技能页面：

- [clawhub.ai/skernelx/mysearch](https://clawhub.ai/skernelx/mysearch)

安装后重点不是改 bundle 文件，而是给 OpenClaw skill config 注入 env。

`mysearch_openclaw.py` 现在会优先读取 OpenClaw 的 `openclaw.json` 里
`skills.entries.mysearch.env`，所以正式部署不需要把 key 再复制进 skill 目录。

推荐做法：

- 在 OpenClaw 的 skill 配置里填 `MYSEARCH_PROXY_BASE_URL`
- 再填 `MYSEARCH_PROXY_API_KEY`
- 不要把 secret 明文写进仓库文件

### 方式 B：从本地仓库安装 bundle

如果你正在本地调试，或者就是想直接覆盖掉现有 skill，执行：

```bash
bash openclaw/scripts/install_openclaw_skill.sh \
  --install-to ~/.openclaw/skills/mysearch
```

这个脚本会：

- 复制 `openclaw/` skill bundle
- 保留 bundled runtime
- 带上 `.env.example`
- 不下载远端代码
- 不修改其他 skill

## 推荐的 OpenClaw skill env 注入方式

优先通过 OpenClaw 的 skill env 配置注入，不要把 secret 放进安装目录。

推荐最小配置：

```json
{
  "skills": {
    "entries": {
      "mysearch": {
        "enabled": true,
        "env": {
          "MYSEARCH_PROXY_BASE_URL": "https://your-mysearch-proxy.example.com",
          "MYSEARCH_PROXY_API_KEY": "mysp-..."
        }
      }
    }
  }
}
```

如果你确实在本地调试 bundle，也可以：

```bash
cp openclaw/.env.example openclaw/.env
```

但这只建议用于本地调试，不建议作为正式部署方式。

## 安装后怎么验收

优先跑健康检查：

```bash
python3 ~/.openclaw/skills/mysearch/scripts/mysearch_openclaw.py health
```

再跑一轮搜索 smoke test：

```bash
python3 ~/.openclaw/skills/mysearch/scripts/mysearch_openclaw.py search \
  --query "OpenAI latest announcements" \
  --mode web
```

如果你要测文档搜索：

```bash
python3 ~/.openclaw/skills/mysearch/scripts/mysearch_openclaw.py search \
  --query "OpenAI Responses API docs" \
  --mode docs \
  --intent resource
```

如果你配置了 Social / X，再补：

```bash
python3 ~/.openclaw/skills/mysearch/scripts/mysearch_openclaw.py search \
  --query "Model Context Protocol" \
  --mode social \
  --intent status
```

## 这个 skill 到底提供了什么

OpenClaw 版 MySearch 本质上还是同一套能力，只是打成了 skill bundle：

- `search`
- `extract`
- `research`
- `health`

默认理解方式：

- `web / news`
  - 优先 Tavily
- `docs / github / pdf`
  - 优先 Firecrawl
- 普通网页补充发现
  - 可回退 Exa
- `social`
  - 走 xAI 或兼容 `/social/search`

## 版本与优化说明（v0.1.10）

`mysearch@0.1.10` 已同步以下运行时优化：

- 配置入口收口：
  - runtime 现在会优先读取宿主配置里的 skill env，不再默认把 `.env` 当主入口。
  - `mysearch_openclaw.py` 会优先读取 `openclaw.json` 中 `skills.entries.mysearch.env`。
  - `.env` 仍可用，但只建议本地 bundle 调试时使用。
  - 读取宿主配置时不再强依赖 Python 3.11 的 `tomllib`，Python 3.10 也能直接跑。

- 文档结果质量：
  - `docs / github / pdf / resource / tutorial` 的混合结果现在会优先官方文档域名与文档路径。
  - 显式传 `include_domains` 时，命中域名的结果会更稳定地排在前面。
  - `reddit / arxiv / researchgate / medium / youtube` 这类第三方页面会被后置，不再轻易抢前排。
  - `citations` 会跟随重排后的结果顺序，避免正文结果和引用顺序打架。
  - Firecrawl 若在 `site:` 域名约束查询下回空，会自动补一轮无 `site:` 的 Firecrawl 检索并在本地按域名过滤，减少 docs 查询被迫切到 Tavily 的概率。

- 路由稳定性：
  - 显式指定 provider 时，不再被 balanced 策略自动混成 hybrid。
- 抓取质量：
  - `extract` 新增更多假正文识别。
  - `auto` 模式可识别验证码页、GitHub blob 页面壳，并针对公开 GitHub blob 优先抓 raw 正文。
- 社交结果一致性：
  - 日期过滤后若没有命中，`results / citations / answer` 会保持一致。
- Social / X fallback：
  - `social/search` 支持主模型结果太少或上游失败时自动 fallback。
  - 返回新增 `route` 字段，可直接查看主模型、fallback 模型和每轮尝试结果。

- 并行执行：
  - 混合检索与 `research` 分支支持并行调用，减少总耗时。
- 缓存能力：
  - `search` 与 `extract` 增加 TTL 缓存。
- 可观测性：
  - 返回中新增 `route_debug` 与 `cache` 字段，便于定位路由和命中行为。
- 健康信息增强：
  - `health` 返回新增 `runtime`、`routing_defaults`、`cache`。

配套环境变量：

```env
MYSEARCH_MAX_PARALLEL_WORKERS=4
MYSEARCH_SEARCH_CACHE_TTL_SECONDS=30
MYSEARCH_EXTRACT_CACHE_TTL_SECONDS=300
```

注意：

- 若通过 `mysearch_openclaw.py` 每次单独执行命令，属于一次一进程，缓存不会跨命令持久。
- 在 OpenClaw 常驻会话中，缓存才会体现连续命中。

## 安全建议

这份 skill 的推荐安全边界很明确：

- 优先通过 OpenClaw skill env 注入 secret
- 不要把正式 token 复制进 skill 目录
- 不要把 `MYSEARCH_PROXY_BASE_URL` 指向不可信主机
- 如果你直连 provider，也尽量把 key 放到 OpenClaw 配置层，而不是 bundle 文件里

`public.json` 里也保留了对应的公开安全说明，方便 ClawHub 校验和展示。

## 本地调试入口

如果你就在仓库里开发这份 skill，可以直接：

```bash
cp openclaw/.env.example openclaw/.env
python3 openclaw/scripts/mysearch_openclaw.py health
```

## 相关文档

- 仓库总览：
  [../README.md](../README.md)
- MCP：
  [../mysearch/README.md](../mysearch/README.md)
- Proxy 控制台：
  [../proxy/README.md](../proxy/README.md)
- Codex / Claude Code skill：
  [../skill/README.md](../skill/README.md)
- OpenClaw skill 规则：
  [./SKILL.md](./SKILL.md)
