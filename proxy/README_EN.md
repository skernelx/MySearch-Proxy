# MySearch Proxy Console

[中文说明](./README.md) · [Back to repo](../README_EN.md)

`proxy/` is the console and gateway layer inside `MySearch Proxy`.

It is not just a key panel. It brings Tavily, Firecrawl, and Social / X into
one operational workspace so you can manage:

- upstream provider keys
- downstream access tokens
- official quota sync
- compatible gateway wiring
- the final connection pattern used by MySearch MCP and skills

![MySearch Console Hero](../docs/images/mysearch-console-hero.jpg)

## What this console solves

Most proxy panels only handle one small slice of the problem:

- they store keys but do not issue downstream tokens
- they issue tokens but do not sync upstream quotas
- they support Tavily but not Firecrawl
- they assume official APIs only and ignore compatible social gateways
- they are usable by humans, but not designed as a backend for MCP / Skills

`MySearch Proxy Console` pulls those pieces back together:

- a dedicated Tavily workspace
- a dedicated Firecrawl workspace
- a dedicated Social / X workspace
- one place to inspect key pools, token pools, real quota state, proxy stats,
  and MySearch wiring instructions

## Why it is better than a generic key panel

### 1. It separates services instead of mixing them

This is not one shared bucket for every credential.

It treats these as different operational surfaces:

- Tavily
- Firecrawl
- Social / X

That gives you:

- cleaner quota accounting
- isolated token usage
- clearer statistics
- more obvious downstream endpoints

### 2. It is built for a real MySearch runtime

This console is not a standalone dashboard experiment.

It is designed to feed:

- the `mysearch/` MCP
- the `skill/` bundle for Codex / Claude Code
- the `openclaw/` skill bundle

So it is a real backend for search agents, not only a UI for manual ops.

### 3. It can pull `grok2api` into the same control plane

If your X / Social layer comes from `grok2api`, the console can:

- read `/v1/admin/config`
- read `/v1/admin/tokens`
- inherit `app.api_key`
- display token state in the same workspace

That is much cleaner than keeping a separate social script on the side.

### 4. It still matters even without X

If you do not have `grok2api` or official `xAI` yet:

- the Tavily workspace still works
- the Firecrawl workspace still works
- MySearch still works as a unified `web + docs + extract` backend

So Social / X is an enhancement, not the only reason this console exists.

## Recommended connection pattern

The default recommended full stack is:

```text
tavily-key-generator
  -> provides Tavily / Firecrawl provider access or aggregation APIs

MySearch Proxy Console
  -> manages keys, tokens, quotas, /social/search, and grok2api wiring

MySearch MCP / Skill / OpenClaw Skill
  -> becomes the final search path used by AI agents
```

Recommended companion project:

- [skernelx/tavily-key-generator](https://github.com/skernelx/tavily-key-generator)

Why that is the default recommendation:

- Tavily / Firecrawl are cleaner when normalized at the provider layer first
- MySearch Proxy can then focus on organizing them into an agent-ready workflow

## UI preview

Active workspace view:

![MySearch Console Workspaces](../docs/images/mysearch-console-workspaces.jpg)

## What it supports

### Tavily

Gateway endpoints:

- `POST /api/search`
- `POST /api/extract`

Console features:

- key pool
- token pool
- Tavily `/usage` quota sync
- proxy call statistics

### Firecrawl

Gateway endpoints:

- `/firecrawl/*`
- for example `POST /firecrawl/v2/scrape`

Console features:

- key pool
- token pool
- Firecrawl credit sync
- proxy call statistics

### Social / X

Gateway endpoints:

- `POST /social/search`
- `GET /social/health`

Console features:

- upstream base URL management
- gateway token management
- grok2api admin integration
- token status and quota display

## What happens when a provider is missing

### No `grok2api` or official `xAI`

The console still works.

You can still manage:

- Tavily
- Firecrawl
- the MySearch routes used for web, docs, and extraction

Only the Social / X workspace remains unconfigured.

### No Tavily

The console still works for:

- Firecrawl
- Social / X

But MySearch will lose much of its general `web / news` strength.

### No Firecrawl

The console still works for:

- Tavily
- Social / X

But MySearch becomes weaker for `docs / github / pdf / extract`.

If what you are missing is official Tavily / Firecrawl access, the default
recommendation is to connect:

- [skernelx/tavily-key-generator](https://github.com/skernelx/tavily-key-generator)

## Deployment

### 1. Docker Hub or your own image

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

Open:

```text
http://localhost:9874
```

### 2. docker compose

```bash
cd proxy
docker compose up -d
```

### 3. Run from source

```bash
cd proxy
pip install -r requirements.txt
ADMIN_PASSWORD=your-admin-password uvicorn server:app --host 0.0.0.0 --port 9874
```

## Upgrade path

If you already run an older container, keep the same data volume and replace
the image:

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

As long as the `/app/data` volume is preserved, your existing:

- keys
- tokens
- console password
- historical stats

remain available.

## Configuration

Baseline console config:

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

### Recommended `grok2api` setup

If the upstream is `grok2api`, it is better to set only:

```env
SOCIAL_GATEWAY_UPSTREAM_BASE_URL=https://media.example.com/v1
SOCIAL_GATEWAY_ADMIN_BASE_URL=https://media.example.com
SOCIAL_GATEWAY_ADMIN_APP_KEY=YOUR_GROK2API_APP_KEY
SOCIAL_GATEWAY_MODEL=grok-4.1-fast
```

The console will then:

- inherit `app.api_key` from `/v1/admin/config`
- read token state from `/v1/admin/tokens`
- reuse inherited credentials when `SOCIAL_GATEWAY_UPSTREAM_API_KEY` or
  `SOCIAL_GATEWAY_TOKEN` are not explicitly set

### Manual mode

If you do not want to connect the admin API, you can fill credentials manually:

```env
SOCIAL_GATEWAY_UPSTREAM_BASE_URL=https://media.example.com/v1
SOCIAL_GATEWAY_UPSTREAM_API_KEY=YOUR_UPSTREAM_KEY
SOCIAL_GATEWAY_TOKEN=YOUR_SOCIAL_GATEWAY_TOKEN
```

## API examples

Supported auth styles:

- `Authorization: Bearer YOUR_TOKEN`
- `api_key` inside the request body

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

Health check:

```bash
curl http://localhost:9874/social/health
```

## Quick verification

### Console

Open:

```text
http://localhost:9874
```

Then confirm:

- login works
- workspace switching works
- keys and tokens can be created and listed
- Tavily / Firecrawl quota sync is visible
- Social / X workspace shows upstream state correctly

### Admin API

```bash
curl http://localhost:9874/api/stats \
  -H "X-Admin-Password: your-admin-password"
```

```bash
curl "http://localhost:9874/api/keys?service=tavily" \
  -H "X-Admin-Password: your-admin-password"
```

## Related docs

- Repository overview:
  [../README_EN.md](../README_EN.md)
- MySearch MCP:
  [../mysearch/README_EN.md](../mysearch/README_EN.md)
- Architecture:
  [../docs/mysearch-architecture.md](../docs/mysearch-architecture.md)
