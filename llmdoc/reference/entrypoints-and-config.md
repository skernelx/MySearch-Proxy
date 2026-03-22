# 运行入口与配置入口

## 关键运行入口

### 根安装入口

- `install.sh:1`
- 负责安装 `mysearch/requirements.txt`，然后尝试向 `Claude Code` 与 `Codex` 注册本地 `stdio` MCP。见 `install.sh:13`、`install.sh:174`、`install.sh:183`。
- 它会先读取宿主 `~/.codex/config.toml` 里 `mcp_servers.mysearch.env`，再用 `mysearch/.env` 只补缺省值。见 `install.sh:74`、`install.sh:131`、`install.sh:158`。

### MySearch MCP 启动入口

- `mysearch/__main__.py:8`
  - 解析 `--transport`、`--host`、`--port`、`--sse-path`、`--streamable-http-path` 等参数。
- `mysearch/server.py:168`
  - 最终调用 FastMCP 的 `run(...)`。
- `mysearch/server.py:34`
  - `build_mcp()` 里定义 4 个 MCP tools。

### Proxy Console / API 入口

- `proxy/server.py:1642`
  - 启动时初始化 SQLite。
- `proxy/server.py:1649`、`proxy/server.py:1680`、`proxy/server.py:1719`、`proxy/server.py:1783`
  - 对外搜索代理与 Social/X 代理入口。
- `proxy/server.py:1877`
  - 控制台页面入口。
- 运行方式见 `proxy/README.md:144`、`proxy/README.md:166`、`proxy/README.md:173` 与 `proxy/Dockerfile:7`。

### OpenClaw wrapper 入口

- `openclaw/scripts/mysearch_openclaw.py:303`
  - bundle CLI 总入口，支持 `health`、`search`、`extract`、`research`。
- `openclaw/scripts/mysearch_openclaw.py:74`
  - wrapper 会先检查 bundled runtime 是否存在，再把 `openclaw/runtime/` 注入 `sys.path`。

## 配置优先级

## MySearch runtime

稳定规则：**已存在的进程环境变量优先，bootstrap 逻辑只补缺失值。**

原因是 `mysearch/config.py` 的加载器统一用 `os.environ.setdefault(...)`，见 `mysearch/config.py:23`、`mysearch/config.py:40`。

推荐按下面顺序理解：

1. 进程已注入的环境变量。
2. `~/.codex/config.toml` 中 `mcp_servers.mysearch.env`，见 `mysearch/config.py:85`。
3. `mysearch/.env` 或仓库根 `.env` 作为本地单仓兜底，见 `mysearch/config.py:98`。

回归测试明确要求：`config.toml` 覆盖 `.env`，而 `.env` 只补缺失字段；Python 3.10 无 `tomllib` 时仍可回退解析。当前运行时还额外兼容 Python 3.9：dataclass 装配层会自动去掉 `slots`，避免 `mysearch/config.py`、`mysearch/clients.py`、`mysearch/keyring.py` 以及 OpenClaw 对应 runtime 在导入阶段直接报错。见 `mysearch/config.py:17`、`mysearch/clients.py:24`、`mysearch/keyring.py:12`、`openclaw/runtime/mysearch/config.py:17`、`openclaw/runtime/mysearch/clients.py:24`、`openclaw/runtime/mysearch/keyring.py:12`、`tests/test_config_bootstrap.py:39`、`tests/test_config_bootstrap.py:144`。

## Proxy-first 默认映射

`MySearchConfig.from_env()` 会先读 `MYSEARCH_PROXY_BASE_URL` 与 `MYSEARCH_PROXY_API_KEY`，但当前语义已经比早期版本更细。Firecrawl / Exa 仍然会直接切到 Proxy 语义；Tavily 则先看 `MYSEARCH_TAVILY_MODE`，只有在 `gateway` 分支下才会继承 proxy/gateway 语义，显式 `official` 时会继续使用自己的官方 key 池。见 `mysearch/config.py:167`、`mysearch/config.py:181`、`mysearch/config.py:198`、`mysearch/config.py:281`、`mysearch/config.py:320`、`mysearch/.env.example:7`。

这意味着对下游客户端来说，最小配置通常只需要：

- `MYSEARCH_PROXY_BASE_URL`
- `MYSEARCH_PROXY_API_KEY`

但如果你希望 Tavily 不走统一 Proxy，而是自己维护 key 池或对接独立上游，就要显式指定：

- `MYSEARCH_TAVILY_MODE=official`
  - 配合 `MYSEARCH_TAVILY_API_KEY`、`MYSEARCH_TAVILY_API_KEYS`、`MYSEARCH_TAVILY_KEYS_FILE`
- `MYSEARCH_TAVILY_MODE=gateway`
  - 配合 `MYSEARCH_TAVILY_GATEWAY_BASE_URL`、`MYSEARCH_TAVILY_GATEWAY_TOKEN`

## OpenClaw wrapper

OpenClaw 侧也是 host-config-first，但入口不同：

1. 进程已有环境变量。
2. `openclaw.json` 中 `skills.entries.mysearch.env`，见 `openclaw/scripts/mysearch_openclaw.py:43`。
3. `openclaw/.env`，见 `openclaw/scripts/mysearch_openclaw.py:304`。
4. `openclaw/runtime/.env`，见 `openclaw/scripts/mysearch_openclaw.py:306`。

测试已覆盖 wrapper 会从 `openclaw.json` 读取 skill env。见 `tests/test_config_bootstrap.py:97`。

## 关键环境变量分组

| 分组 | 用途 | 入口 |
| --- | --- | --- |
| `MYSEARCH_PROXY_*` | 统一走 MySearch Proxy 的下游接线 | `mysearch/.env.example:7`, `openclaw/.env.example:2` |
| `MYSEARCH_TAVILY_MODE` | 显式选择 Tavily 走官方 key 池还是上游 gateway | `mysearch/.env.example:25`, `openclaw/.env.example:12` |
| `MYSEARCH_TAVILY_*` | Tavily 官方直连与本地 key 池 | `mysearch/.env.example:28`, `openclaw/.env.example:15` |
| `MYSEARCH_TAVILY_GATEWAY_*` | Tavily 上游 gateway token、base URL、path、auth 配置 | `mysearch/.env.example:39`, `openclaw/.env.example:26` |
| `MYSEARCH_FIRECRAWL_*` | Firecrawl 直连或兼容网关 | `mysearch/.env.example:37` |
| `MYSEARCH_EXA_*` | Exa 兜底路由 | `mysearch/.env.example:49` |
| `MYSEARCH_XAI_*` | official xAI 或 compatible social 模式 | `mysearch/.env.example:60`, `openclaw/.env.example:47` |
| `MYSEARCH_MCP_*` | 本地/远程 MCP 传输配置 | `mysearch/.env.example:14` |
| `SOCIAL_GATEWAY_*` | 本地 social gateway 或 Proxy social upstream/admin 配置 | `mysearch/.env.example:82`, `proxy/.env.example:1` |
| `ADMIN_*` | Proxy 控制台管理员认证 | `proxy/.env.example:1`, `proxy/README.md:249` |

## 状态与数据落点

### Proxy SQLite

- 数据库路径：`proxy/data/proxy.db`，见 `proxy/database.py:11`。
- 主要表：`api_keys`、`tokens`、`usage_logs`、`settings`，见 `proxy/database.py:61`。
- 下游 token 服务范围包含 `tavily`、`firecrawl`、`exa`、`mysearch`；`mysearch` token 前缀为 `mysp-`。见 `proxy/database.py:12`、`proxy/database.py:13`、`proxy/database.py:14`。

### 运行时缓存

- `search` 与 `extract` 走 TTL 缓存；provider live probe 也有单独 TTL。见 `mysearch/clients.py:121`、`mysearch/clients.py:125`、`mysearch/clients.py:199`、`mysearch/clients.py:215`、`mysearch/clients.py:2842`。

## 重要验证点

- 路由健康保护：`tests/test_clients.py:488`
- 配置继承与 Python 3.10 fallback：`tests/test_config_bootstrap.py:39`, `tests/test_config_bootstrap.py:97`, `tests/test_config_bootstrap.py:144`
- Tavily official/gateway 分支：`tests/test_config_bootstrap.py:39`, `tests/test_config_bootstrap.py:74`
- Social/X 归一化与 fallback：`tests/test_social_normalization.py:76`, `tests/test_social_normalization.py:171`

如果是直接用 `python tests/test_*.py` 跑单文件，而不是走 `python -m unittest discover`，当前几个脚本测试也已经自带仓库根目录引导，不再要求调用方先手动设置 `PYTHONPATH`。见 `tests/test_clients.py:10`、`tests/test_social_normalization.py:8`、`tests/test_proxy_tavily_settings.py:10`。

这些测试文件就是改动相关行为时最值得先看的“行为契约”。
