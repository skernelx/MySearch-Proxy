# proxy-first 架构

## 核心链路

- 推荐链路是“上游 provider -> MySearch Proxy -> `mysp-` token -> MySearch MCP / OpenClaw / 其他 Agent”。这样客户端只需要一组 `MYSEARCH_PROXY_*`，而不是分别管理 Tavily、Firecrawl、Exa、Social 的 secret。来源：README.md:66, README.md:79, proxy/README.md:106
- `proxy/` 不是单纯 key 面板，而是控制台、token 发放、统计与兼容代理接口的中间层。来源：proxy/README.md:5, proxy/README.md:27
- 但 `proxy-first` 现在不再等于“所有 Tavily 流量都必须绑定本仓库 Proxy”。Tavily 在 runtime 里也支持独立 `gateway` 分支，例如接 `tavily-hikari` 这类上游；只是在没有显式 `MYSEARCH_TAVILY_MODE` 时，`MYSEARCH_PROXY_*` 仍会保持兼容性的 gateway 默认值。来源：mysearch/config.py:198, mysearch/config.py:207, mysearch/config.py:281, mysearch/config.py:320

## 运行时分层

- 架构文档把系统拆成三层：Skill / Decision Layer、MCP / Orchestration Layer、Provider Layer。这个分层解释了为什么 `skill/`、`mysearch/`、provider 配置不应该揉成一个目录。来源：docs/mysearch-architecture.md:3
- `mysearch/server.py` 负责把统一能力暴露成 4 个 MCP tool；真正的 provider 路由与组合逻辑下沉在 `MySearchClient`。来源：mysearch/server.py:34, mysearch/server.py:47
- `proxy/server.py` 负责上游代理端点与控制台管理 API；`proxy/database.py` 负责 SQLite 存储 token、key、usage 和 settings。来源：proxy/server.py:47, proxy/server.py:275, proxy/database.py:11, proxy/database.py:61

## Provider 路由规则

- `search` 的统一入口在 `mysearch/clients.py:385`，会先解析 intent、strategy、sources，再调用 `_route_search` 决定 provider。来源：mysearch/clients.py:418, mysearch/clients.py:439
- 显式指定 `provider` 时，不再做自动路由；`tavily`、`firecrawl`、`exa`、`xai` 都会被直接尊重。来源：mysearch/clients.py:979
- 同时请求网页和 X 时，会走 `hybrid`，并把 web 与 social 两条结果拼接。来源：mysearch/clients.py:1004, mysearch/clients.py:487
- `social` 模式或传入 X handle 过滤时，优先走 xAI。来源：mysearch/clients.py:1009, mysearch/clients.py:1016
- `docs`、`github`、`pdf` 这类文档查询默认是“先发现、后正文”：有内容需求时优先 Firecrawl；没要求正文时优先 Tavily 做页面发现，再把正文留给 Firecrawl。Firecrawl 缺失时才回退 Exa。来源：mysearch/clients.py:1023, mysearch/clients.py:1037, mysearch/clients.py:1043
- 一般 `include_content=true` 的正文型请求也会优先 Firecrawl；Firecrawl 不可用时回退 Exa。来源：mysearch/clients.py:1056
- `news` / `status` 默认 Tavily；普通网页查询默认 Tavily，未配置 Tavily 时回退 Exa。来源：mysearch/clients.py:1070, mysearch/clients.py:1131
- `resource` / docs-like 查询会优先把发现与正文分开处理；`research` 会先做发现，再按策略扩展验证。来源：mysearch/clients.py:1084, mysearch/clients.py:1117

## 执行策略

- `strategy=balanced|verify|deep` 时，web 路由可能触发 Tavily + Firecrawl 的并行 blended 检索，再合并结果和 citations。来源：mysearch/clients.py:1201, mysearch/clients.py:1221
- `extract_url` 独立于 `search`，默认优先 Firecrawl scrape，质量不够或失败时再回退 Tavily extract。来源：mysearch/server.py:97, mysearch/clients.py:677
- `research` 是一个小型编排流程：先跑 web discovery，可选并行 social，再抓取前几条正文并回填 evidence。来源：mysearch/server.py:112, mysearch/clients.py:802

## xAI 与 Social

- 架构文档明确区分 official xAI 与 compatible social gateway，避免把模型网关误当成搜索后端。来源：docs/mysearch-architecture.md:24, docs/mysearch-architecture.md:47
- `MySearchConfig` 用 `MYSEARCH_XAI_SEARCH_MODE` 区分 `official` 与 `compatible`；`_search_xai` 在 `compatible` 模式下会改走 `social_search` 路径。来源：mysearch/config.py:18, mysearch/clients.py:1758, mysearch/clients.py:1832
- `proxy/` 侧还维护了 Social gateway 的 upstream/base URL、fallback model、admin API 对接与缓存。来源：proxy/server.py:43, proxy/server.py:189

## Tavily 官方与 Gateway

- Tavily 现在也和 xAI 一样有显式模式切分，但命名更直接：`MYSEARCH_TAVILY_MODE=official|gateway`。来源：mysearch/config.py:17, mysearch/config.py:198
- `official` 模式下，runtime 会继续读取 `MYSEARCH_TAVILY_API_KEY`、`MYSEARCH_TAVILY_API_KEYS`、`MYSEARCH_TAVILY_KEYS_FILE`，并忽略 `MYSEARCH_PROXY_API_KEY` 对 Tavily 的注入。来源：mysearch/config.py:356, mysearch/config.py:395, mysearch/config.py:402
- `gateway` 模式下，runtime 会改读 `MYSEARCH_TAVILY_GATEWAY_BASE_URL`、`MYSEARCH_TAVILY_GATEWAY_TOKEN` 和 `MYSEARCH_TAVILY_GATEWAY_*` 路径与鉴权配置，适合对接 `tavily-hikari` 这类上游。来源：mysearch/config.py:211, mysearch/config.py:287, mysearch/config.py:325, mysearch/config.py:383
- 如果调用方没显式写 `MYSEARCH_TAVILY_MODE`，但配置了 `MYSEARCH_PROXY_*`，Tavily 默认仍落到 `gateway` 分支，保持现有 `proxy-first` 客户端最小配置不变。来源：mysearch/config.py:199, mysearch/config.py:206
- `mysearch_health` 现在会把 Tavily 的 `provider_mode` 暴露出来，排查时先看这里，避免把“上游 gateway token 缺失”和“本地官方 key 池为空”混成一个问题。来源：mysearch/server.py:157, mysearch/clients.py:2717

## 配置优先级

- MySearch runtime 会先读 `~/.codex/config.toml` 的 `mcp_servers.mysearch.env`，再把 `.env` 当本地兜底，不覆盖宿主已注入配置。来源：README.md:91, mysearch/config.py:85, mysearch/config.py:98
- `install.sh` 会先继承宿主已有的 `MYSEARCH_*`，再用 `mysearch/.env` 补缺省值，并将这些 env 注册给 `claude` 与 `codex`。来源：README.md:93, install.sh:16, install.sh:158, install.sh:174

## 数据与控制面

- Proxy 的启动时机会执行 `db.init_db()`；SQLite 默认路径仍是 `proxy/data/proxy.db`，但容器部署已经统一通过 `MYSEARCH_PROXY_DB_PATH=/data/proxy.db` 覆盖，避免独立 `proxy` 镜像和 `mysearch-stack` 因内部目录不同各写一份库。来源：proxy/server.py:42, proxy/database.py:11, proxy/database.py:15, proxy/database.py:59, proxy/Dockerfile:1, Dockerfile.stack:1
- Proxy 的 token 体系里包含 `mysearch` 服务，生成前缀为 `mysp-` 的统一 token，默认只做鉴权与统计，不做配额拦截。来源：proxy/database.py:13, proxy/database.py:18, proxy/README.md:74, proxy/README.md:83
- `proxy-first` 的容器部署边界现在同时支持“两服务一套 stack”和“单容器一体化镜像”两种形态。仓库根的 `docker-compose.yml` 会同时编排 `proxy` 与 `mysearch`：前者负责控制台、token 与统一代理，后者负责对 Codex/Claude 暴露远程 MCP；`mysearch` 在同一个 compose 网络里继续用 `MYSEARCH_PROXY_BASE_URL=http://proxy:9874` 访问 Proxy，并通过受限的 `MYSEARCH_PROXY_BOOTSTRAP_TOKEN` 向 `/api/internal/mysearch/token` 申请或复用专用的 `mysp-` token。对于更看重部署步骤最少的场景，`Dockerfile.stack` 与 `docker/combined-entrypoint.sh` 又把这两个进程收成单容器镜像 `mysearch-stack`：`proxy` 默认对外监听 `9874`，而 `mysearch` 继续监听 `8000/mcp` 并通过容器内 `127.0.0.1:9874` 回连 Proxy。GitHub Actions 侧也已经从“只发 Proxy 镜像”扩成三镜像 matrix：`.github/workflows/docker-publish.yml` 会分别构建/发布 `proxy`、`mysearch` 与 `stack`，而根目录、`proxy/` 和 `mysearch/` 的 `.dockerignore` 则分别收口各自上下文，避免把本地 SQLite、`.env`、accounts 文件与缓存一起打进镜像。来源：docker-compose.yml:1, Dockerfile.stack:1, docker/combined-entrypoint.sh:1, .github/workflows/docker-publish.yml:1, .dockerignore:1, proxy/.dockerignore:1, mysearch/.dockerignore:1
- Proxy 控制台现在已经从单文件模板拆成 `console.html + _hero.html + _settings_modal.html + console.css + console.js` 这套 live 前端；页面布局已经回到 `summary-strip + dashboard-flow` 的纵向结构，默认首页下半区固定为 `Workspace Navigator -> provider workspace`，而统一客户端接入则拆到独立的 `/mysearch` 页面。`Workspace Navigator` 仍然只保留工作台名称、状态和 2 个核心指标，次要信息下沉到 badge 与 footnote，不再展示 `/api/search`、`/social/search` 这类具体请求路径；但它现在不再纵向堆叠，而是由 `service-switcher` 横向卡阵列承接，`Social Compatibility` 提示卡也继续收在 switcher 区块底部。登录入口也不再是孤立小表单，而是通过 `auth-meta` 把“统一入口 / provider / 控制面”三个概念先交代清楚，并在登录成功后由 `showDashboard({ animate: true })` 做一轮轻量 staged reveal，而在 `prefers-reduced-motion` 下会自动压平动效。hero 右侧原先那张“当前工作台”大卡已经移除，不再在首屏重复展示当前控制台状态。`/mysearch` 页则收成 `MySearch 接入台`，模块标题进一步压成 `统一接入配置`，避免页级标题和模块标题重复。该页内部继续保持“接入配置 / 安装路径 / 通用 Token 管理”三层，而且“一键配置”内部进一步拆成左侧 `quickstart-visual-col` 可视化 readiness 区和右侧 `quickstart-config-col` 配置区：`getQuickstartProviderCards()` 继续把 Tavily `effective_mode`、Exa / Firecrawl key 状态和 Social / X 接线结果汇总成 `quickstart-route-strip`，`getQuickstartInstallHint()` 继续把当前最短安装路径压成 `quickstart-install-strip`，同时也把旧版更直接的 `stdio / streamable-http` 安装形态补回到 `quickstart-install-meta`。这些状态会一起写入生成的 `MYSEARCH_PROXY_*` 配置说明；除了复制块旁边的普通复制按钮，现在还额外提供 `copyEnvAndRevealInstall()` 这个组合动作，直接复制 `.env` 并把视口定位到安装命令。默认首页的 `summary-strip` 也已经收窄成项目级概览：`当前工作台 / 已接通工作台 / Provider 代理 Token / 今日调用 / 本月调用 / MySearch Token`，不再塞入工作台内部已经会单独展示的上游额度或本地 API Key。主题切换则扩成 `浅色 / 深色 / 自动` 三态，`自动` 依据打开页面那台机器的本地时区与本地时间决定实际主题，不依赖服务端所在系统或容器时区。`MySearch 通用 Token` 摘要表继续共享和 provider 面板一致的本地搜索/排序逻辑。provider 页面仍然保持“摘要表 + `detail-drawer`”的运维视图，`Token 池 / API Key 池` 的本地搜索、筛选和排序，以及 `table-row-clickable.is-danger|is-warn|is-busy|is-off` 风险行态都保留不变；`detail-drawer` 底部动作也继续通过 `renderDrawerActionGroup()` 拆成“维护动作 / 危险动作”两组。设置面板仍是带 `settings-summary-strip`、sticky footer 和 Tavily `mode-switch` 分段控件的配置中心，并保留 `/api/settings/test/tavily` 与 `/api/settings/test/social` 这两条结构化 probe 链路。控制台刷新仍然通过 `normalizeRefreshScope()`、`getRefreshScopeForService()`、`renderDashboardScope()` 做局部更新，可访问性层也仍然保留 `handleSegmentedControlKey()`、toast live region、overlay focus remember/restore 与 `trapOverlayFocus()` 这一组统一逻辑。来源：proxy/templates/components/_hero.html:25, proxy/templates/components/_hero.html:51, proxy/templates/console.html:51, proxy/templates/console.html:54, proxy/templates/console.html:65, proxy/templates/mysearch.html:21, proxy/static/js/console.js:644, proxy/static/js/console.js:1425, proxy/static/js/console.js:1478, proxy/static/js/console.js:1695, proxy/static/js/console.js:2251, proxy/static/js/console.js:2777, proxy/static/css/console.css:622, proxy/static/css/console.css:717, proxy/static/css/console.css:1061, proxy/static/css/console.css:1508, proxy/static/css/console.css:1533, proxy/static/css/console.css:2923, proxy/static/css/console.css:2943, proxy/server.py:2259
