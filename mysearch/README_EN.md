# MySearch

[中文说明](./README.md) · [Back to repo](../README_EN.md)

`MySearch` is the installable search MCP inside this repository.

It is not a thin wrapper around one provider. It turns `Tavily`,
`Firecrawl`, and optional `X / Social` into one search runtime and combines
search, extraction, and lightweight research in the same toolset.

## What this MCP is for

`mysearch/` is responsible for:

- a unified `search` entry
- single-page extraction via `extract_url`
- lightweight research flows via `research`
- provider and config checks via `mysearch_health`

It is a good fit for:

- `Codex`
- `Claude Code`
- other local assistants that support MCP

If you only need a stronger search MCP, this directory is enough.

If you also need pooled keys, downstream tokens, quota sync, and a social
gateway UI, see [../proxy/README_EN.md](../proxy/README_EN.md).

## Why it is more complete than typical search MCPs

### 1. It is not single-source

Default routing:

- general web, news, discovery -> Tavily
- docs, GitHub, PDFs, pricing, changelogs -> Firecrawl
- X / Social -> xAI or compatible `/social/search`

### 2. It extracts content instead of only searching

- `extract_url` prefers Firecrawl
- if Firecrawl fails or returns empty content, it falls back to Tavily extract

So content extraction is a first-class feature, not an afterthought.

### 3. It is a real MCP runtime, not just a prompt

You install an MCP with tools and routing, instead of stuffing search behavior
into a long prompt.

### 4. It is not locked to official APIs

You can:

- connect to official providers directly
- route Tavily / Firecrawl through your own gateway
- route X / Social through compatible `/social/search`
- fine-tune auth with `BASE_URL + PATH + AUTH_*`

### 5. X / Social is optional

Without `xAI` or `grok2api`, these still work:

- `web`
- `news`
- `docs`
- `github`
- `pdf`
- `extract`
- `research`

Only explicit social routes become unavailable.

## Recommended upstream

The default recommendation is not to spread official keys across every client.
The recommended shape is:

```text
tavily-key-generator
  -> provides Tavily / Firecrawl official provider access or aggregation APIs

MySearch MCP
  -> only handles routing, tool exposure, and output shaping
```

Recommended project:

- [skernelx/tavily-key-generator](https://github.com/skernelx/tavily-key-generator)

Why this is the preferred setup:

- MySearch does not need to manage Tavily / Firecrawl upstream operations
- you can point clients at one normalized gateway instead of copying official
  keys around

If you already have official keys, direct official mode still works.

## Tool list

### `search`

Unified search entry.

Common modes:

- `auto`
- `web`
- `news`
- `social`
- `docs`
- `github`
- `pdf`
- `research`

### `extract_url`

Single-page content extraction.

Default behavior:

- Firecrawl first
- Tavily extract fallback when Firecrawl fails or returns empty content

### `research`

Lightweight research workflow.

Useful for:

- comparisons
- trends
- questions that need search plus extraction plus evidence packaging

### `mysearch_health`

Returns provider state, base URLs, key availability, and config summary.

## Intent and Strategy

`MySearch` separates "what are you looking for" from "how hard should it
search":

- `intent`
  - `factual`
  - `status`
  - `comparison`
  - `tutorial`
  - `exploratory`
  - `news`
  - `resource`
- `strategy`
  - `fast`
  - `balanced`
  - `verify`
  - `deep`

Default tendencies:

- `comparison` / `exploratory` lean toward `verify`
- `docs` / `resource` / `tutorial` lean toward `balanced`
- `research` leans toward `deep`

## Provider coverage and degraded behavior

### Tavily

Handles:

- general web
- news
- default research discovery

If Tavily is unavailable:

- `web / news / default research`

become weaker, but Firecrawl can still cover docs and some extraction work.

### Firecrawl

Handles:

- docs
- GitHub
- PDFs
- pricing
- changelogs
- content extraction

If Firecrawl is unavailable:

- docs-focused retrieval gets worse
- `extract_url` falls back to Tavily extract where possible

### X / Social

Handles:

- X / Social search
- sentiment and conversations

If X / Social is unavailable:

- `search(mode="social")` returns a clear setup hint
- `research(include_social=true)` still returns web evidence and adds
  `social_error`

## Installation

Run these commands from the repository root:

```bash
python3 -m venv venv
cp mysearch/.env.example mysearch/.env
./install.sh
```

Minimal usable config:

```env
MYSEARCH_TAVILY_API_KEY=tvly-...
MYSEARCH_FIRECRAWL_API_KEY=fc-...
```

The more reusable public deployment pattern is to point MySearch at
[skernelx/tavily-key-generator](https://github.com/skernelx/tavily-key-generator)
instead:

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

The root `install.sh` will:

1. install dependencies
2. detect `Claude Code`
3. detect `Codex`
4. register the `mysearch` MCP
5. inject `MYSEARCH_*` values from `mysearch/.env`

## X / Social configuration

### Official xAI mode

```env
MYSEARCH_XAI_BASE_URL=https://api.x.ai/v1
MYSEARCH_XAI_RESPONSES_PATH=/responses
MYSEARCH_XAI_SEARCH_MODE=official
MYSEARCH_XAI_API_KEY=xai-...
```

### Compatible mode

```env
MYSEARCH_XAI_BASE_URL=https://media.example.com/v1
MYSEARCH_XAI_SOCIAL_BASE_URL=https://your-social-gateway.example.com
MYSEARCH_XAI_SEARCH_MODE=compatible
MYSEARCH_XAI_API_KEY=your-social-gateway-token
```

Behavior:

- `MYSEARCH_XAI_BASE_URL` points to the model or `/responses` gateway
- `MYSEARCH_XAI_SOCIAL_BASE_URL` points to the social gateway root
- MySearch appends `/social/search` automatically

If you do not have `grok2api` or an official `xAI` key yet, you can leave the
entire X section unset and still use MySearch as a `Tavily + Firecrawl` MCP.

## Integrated social gateway

Reference implementation:

- module: `mysearch.social_gateway`
- purpose: normalize xAI-compatible `/responses` output into a stable social
  search schema

Minimal config:

```env
SOCIAL_GATEWAY_UPSTREAM_BASE_URL=https://media.example.com/v1
SOCIAL_GATEWAY_UPSTREAM_RESPONSES_PATH=/responses
SOCIAL_GATEWAY_UPSTREAM_API_KEY=your-upstream-key
SOCIAL_GATEWAY_MODEL=grok-4.1-fast
SOCIAL_GATEWAY_TOKEN=your-social-gateway-token
```

Start it with:

```bash
../venv/bin/python -m mysearch.social_gateway
```

or:

```bash
uvicorn mysearch.social_gateway:app --host 127.0.0.1 --port 9875
```

## Quick verification

Check MCP registration:

```bash
claude mcp list
codex mcp list
codex mcp get mysearch
```

Health check:

```bash
python skill/scripts/check_mysearch.py --health-only
```

Web and docs smoke tests:

```bash
python skill/scripts/check_mysearch.py --web-query "OpenAI latest announcements"
python skill/scripts/check_mysearch.py --docs-query "OpenAI Responses API docs"
```

If X / Social is configured:

```bash
python skill/scripts/check_mysearch.py --social-query "Model Context Protocol"
```

Extraction smoke test:

```bash
python skill/scripts/check_mysearch.py \
  --extract-url "https://www.anthropic.com/news/model-context-protocol"
```

## Example calls

General web search:

```json
{
  "tool": "search",
  "arguments": {
    "query": "best search MCP server",
    "mode": "web"
  }
}
```

X / Social:

```json
{
  "tool": "search",
  "arguments": {
    "query": "what are people saying about MCP",
    "mode": "social",
    "max_results": 5
  }
}
```

Comparison-style query:

```json
{
  "tool": "search",
  "arguments": {
    "query": "Tavily vs Firecrawl for docs search",
    "intent": "comparison",
    "strategy": "verify",
    "max_results": 6
  }
}
```

Extract page content:

```json
{
  "tool": "extract_url",
  "arguments": {
    "url": "https://example.com/post",
    "formats": ["markdown"]
  }
}
```

Research flow:

```json
{
  "tool": "research",
  "arguments": {
    "query": "best search MCP server 2026",
    "intent": "exploratory",
    "include_social": true,
    "scrape_top_n": 3
  }
}
```

## Related docs

- Repository overview:
  [../README_EN.md](../README_EN.md)
- Proxy console:
  [../proxy/README_EN.md](../proxy/README_EN.md)
- Architecture:
  [../docs/mysearch-architecture.md](../docs/mysearch-architecture.md)
