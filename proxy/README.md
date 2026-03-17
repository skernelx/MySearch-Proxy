# MySearch Proxy Console

[English Guide](./README_EN.md) · [返回仓库](../README.md)

`proxy/` 是 `MySearch Proxy` 里的控制台与代理层。

它不是单纯的 key 面板，而是把 Tavily、Firecrawl、Social / X 三条能力线
收进一个统一工作台里，让你同时管理：

- 上游 provider key
- 下游调用 token
- 官方额度同步
- compatible gateway 接线
- MySearch 最终应该怎么接这套搜索基础设施

![MySearch Console Hero](../docs/images/mysearch-console-hero.jpg)

## 这个控制台解决什么问题

很多代理面板只做其中一小段：

- 只存 key，不管下游怎么发 token
- 只做 token，不同步官方额度
- 只支持 Tavily，不支持 Firecrawl
- 只支持官方接口，不支持自己的 compatible social gateway
- 只适合人工维护，不适合真正给 MCP / Skill 当后端

`MySearch Proxy Console` 把这些拆散的问题重新收口：

- Tavily 独立工作台
- Firecrawl 独立工作台
- Social / X 独立工作台
- 同一个页面里看清 Key 池、Token 池、真实额度、代理统计和接线方式

## 为什么它比普通 key 面板更好用

### 1. 按服务拆开，而不是混成一个池子

这里不是一个“所有 key 全堆在一起”的控制台。

它明确把三类能力拆开：

- Tavily
- Firecrawl
- Social / X

这样做的好处是：

- provider 额度不会混算
- token 不会串用
- 统计更清楚
- 下游调用地址也更直观

### 2. 它服务于真正的 MySearch 运行时

这个控制台不是孤立产品。

它的设计目标就是给：

- `mysearch/` MCP
- `skill/` Codex / Claude Code skill
- `openclaw/` OpenClaw skill

提供后端与配置落点。

### 3. 它能把 `grok2api` 收进同一控制平面

如果你的 X / Social 侧来自 `grok2api`：

- 可以直接接 `/v1/admin/config`
- 可以接 `/v1/admin/tokens`
- 可以自动继承 `app.api_key`
- 可以把 token 状态也统一展示在自己的工作台里

这点比“再手写一个 social 脚本”整齐很多。

### 4. 没有 X 时，它也不是废的

即使你暂时没有 `grok2api` 或官方 `xAI`：

- Tavily 工作台仍可用
- Firecrawl 工作台仍可用
- MySearch 仍可作为 `web + docs + extract` 的统一入口

也就是说，Social / X 是增强项，不是控制台存在的唯一价值。

## 默认推荐怎么接

最推荐的完整链路是：

```text
tavily-key-generator
  -> 提供 Tavily / Firecrawl provider 或聚合 API

MySearch Proxy Console
  -> 管理 key、token、额度、/social/search、grok2api 接线

MySearch MCP / Skill / OpenClaw Skill
  -> 作为最终给 AI 用的统一搜索入口
```

推荐项目：

- [skernelx/tavily-key-generator](https://github.com/skernelx/tavily-key-generator)

默认推荐原因：

- Tavily / Firecrawl 更适合先收口在 provider 层
- MySearch Proxy 再负责把它们组织成真正给 AI 使用的统一工作流

## 界面预览

当前工作台展开区：

![MySearch Console Workspaces](../docs/images/mysearch-console-workspaces.jpg)

## 支持的能力

### Tavily

代理入口：

- `POST /api/search`
- `POST /api/extract`

控制台能力：

- Key 池
- Token 池
- Tavily `/usage` 额度同步
- 代理调用统计

### Firecrawl

代理入口：

- `/firecrawl/*`
- 例如 `POST /firecrawl/v2/scrape`

控制台能力：

- Key 池
- Token 池
- Firecrawl credits 同步
- 代理调用统计

### Social / X

代理入口：

- `POST /social/search`
- `GET /social/health`

控制台能力：

- upstream base URL 管理
- gateway token 管理
- grok2api admin 读取
- token 状态与额度展示

## 没有某一项支持时会怎样

### 没有 `grok2api` 或官方 `xAI`

控制台仍然可用。

你仍然可以正常管理：

- Tavily
- Firecrawl
- MySearch 对应的 web / docs / extract 路由

只有 Social / X 工作台会变成未配置状态。

### 没有 Tavily

控制台仍然可以正常承担：

- Firecrawl
- Social / X

但 MySearch 里的普通 `web / news` 路由会明显变弱。

### 没有 Firecrawl

控制台仍然可以正常承担：

- Tavily
- Social / X

但 MySearch 里的 `docs / github / pdf / extract` 体验会下降。

如果缺的是官方 Tavily / Firecrawl key，默认推荐先接：

- [skernelx/tavily-key-generator](https://github.com/skernelx/tavily-key-generator)

## 部署

### 1. Docker Hub 或自建镜像

```bash
mkdir -p mysearch-proxy-data

docker run -d \
  --name mysearch-proxy \
  --restart unless-stopped \
  -p 9874:9874 \
  -e ADMIN_PASSWORD=your-admin-password \
  -v $(pwd)/mysearch-proxy-data:/app/data \
  your-registry/mysearch-proxy:latest
```

访问：

```text
http://localhost:9874
```

### 2. docker compose

```bash
cd proxy
docker compose up -d
```

### 3. 本地源码运行

```bash
cd proxy
pip install -r requirements.txt
ADMIN_PASSWORD=your-admin-password uvicorn server:app --host 0.0.0.0 --port 9874
```

## 更新方式

如果你已经有一个旧容器，保留数据卷即可直接替换镜像：

```bash
docker pull your-registry/mysearch-proxy:latest

docker rm -f mysearch-proxy

docker run -d \
  --name mysearch-proxy \
  --restart unless-stopped \
  -p 9874:9874 \
  -e ADMIN_PASSWORD=your-admin-password \
  -v /your/data/path:/app/data \
  your-registry/mysearch-proxy:latest
```

只要保留 `/app/data` 对应的数据卷，已有：

- key
- token
- 控制台密码
- 历史统计

都会继续保留。

## 配置项

最基础的控制台配置：

```env
ADMIN_PASSWORD=change-me
SOCIAL_GATEWAY_UPSTREAM_BASE_URL=https://api.x.ai/v1
SOCIAL_GATEWAY_UPSTREAM_RESPONSES_PATH=/responses
SOCIAL_GATEWAY_ADMIN_BASE_URL=https://media.example.com
SOCIAL_GATEWAY_ADMIN_APP_KEY=
SOCIAL_GATEWAY_ADMIN_VERIFY_PATH=/v1/admin/verify
SOCIAL_GATEWAY_ADMIN_CONFIG_PATH=/v1/admin/config
SOCIAL_GATEWAY_ADMIN_TOKENS_PATH=/v1/admin/tokens
SOCIAL_GATEWAY_CACHE_TTL_SECONDS=60
SOCIAL_GATEWAY_UPSTREAM_API_KEY=
SOCIAL_GATEWAY_MODEL=grok-4.1-fast
SOCIAL_GATEWAY_TOKEN=
```

### 推荐的 `grok2api` 接法

如果上游是 `grok2api`，优先只填：

```env
SOCIAL_GATEWAY_UPSTREAM_BASE_URL=https://media.example.com/v1
SOCIAL_GATEWAY_ADMIN_BASE_URL=https://media.example.com
SOCIAL_GATEWAY_ADMIN_APP_KEY=YOUR_GROK2API_APP_KEY
SOCIAL_GATEWAY_MODEL=grok-4.1-fast
```

这样控制台会自动：

- 从 `/v1/admin/config` 继承 `app.api_key`
- 从 `/v1/admin/tokens` 读取 token 状态与额度
- 在没显式填 `SOCIAL_GATEWAY_UPSTREAM_API_KEY` /
  `SOCIAL_GATEWAY_TOKEN` 时直接复用继承结果

### 手动模式

如果你不想接 admin API，也可以手动填：

```env
SOCIAL_GATEWAY_UPSTREAM_BASE_URL=https://media.example.com/v1
SOCIAL_GATEWAY_UPSTREAM_API_KEY=YOUR_UPSTREAM_KEY
SOCIAL_GATEWAY_TOKEN=YOUR_SOCIAL_GATEWAY_TOKEN
```

## API 调用示例

认证方式支持：

- `Authorization: Bearer YOUR_TOKEN`
- body 里的 `api_key`

### Tavily

```bash
curl -X POST http://localhost:9874/api/search \
  -H "Authorization: Bearer YOUR_TAVILY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "hello world", "max_results": 1}'
```

```bash
curl -X POST http://localhost:9874/api/extract \
  -H "Authorization: Bearer YOUR_TAVILY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"urls": ["https://example.com"]}'
```

### Firecrawl

```bash
curl -X POST http://localhost:9874/firecrawl/v2/scrape \
  -H "Authorization: Bearer YOUR_FIRECRAWL_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com", "formats": ["markdown"]}'
```

### Social / X

```bash
curl -X POST http://localhost:9874/social/search \
  -H "Authorization: Bearer YOUR_SOCIAL_GATEWAY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "what are people saying about MCP on X",
    "source": "x",
    "max_results": 3
  }'
```

健康检查：

```bash
curl http://localhost:9874/social/health
```

## 快速验收

### 控制台

打开：

```text
http://localhost:9874
```

确认下面几项是否正常：

- 能登录
- 顶部工作台切换正常
- 当前服务的 key / token 能创建和展示
- Tavily / Firecrawl 额度同步正常
- Social / X 工作台能读到 upstream 状态

### 管理 API

```bash
curl http://localhost:9874/api/stats \
  -H "X-Admin-Password: your-admin-password"
```

```bash
curl "http://localhost:9874/api/keys?service=tavily" \
  -H "X-Admin-Password: your-admin-password"
```

## 相关文档

- 仓库总览：
  [../README.md](../README.md)
- MySearch MCP：
  [../mysearch/README.md](../mysearch/README.md)
- 架构说明：
  [../docs/mysearch-architecture.md](../docs/mysearch-architecture.md)
