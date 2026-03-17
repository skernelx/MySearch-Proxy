# MySearch

[中文说明](./README.md)

`MySearch` is a standalone search aggregation MCP.

Its product boundary is:

- installable into `Codex` and `Claude Code`
- a unified search entry for `Tavily + Firecrawl + X`
- official-API-first, while still supporting compatible custom gateways

`mysearch/` is the MCP implementation inside the `MySearch Proxy` repository.

## Capabilities

- Tavily for general web discovery, news, and quick answers
- Firecrawl for docs, GitHub, changelogs, pricing pages, PDFs, and extraction
- xAI Responses API for X / Twitter search and social sentiment

## MCP Tools

- `search`
- `extract_url`
- `research`
- `mysearch_health`

`extract_url` prefers `Firecrawl` first, and automatically falls back to
`Tavily extract` when Firecrawl fails or returns empty content.

## Default Routing

- General web search -> Tavily
- News / latest updates -> Tavily news
- Docs / GitHub / PDF / changelog / pricing -> Firecrawl
- X / Twitter / social sentiment -> xAI X search
- Web + X together -> hybrid aggregation

## Intent and Strategy

- `intent`: `factual`, `status`, `comparison`, `tutorial`, `exploratory`,
  `news`, `resource`
- `strategy`: `fast`, `balanced`, `verify`, `deep`

Defaults:

- `comparison` / `exploratory` -> prefers `verify`
- `docs` / `resource` / `tutorial` / `include_content=true` -> prefers `balanced`
- `research` -> prefers `deep`

## Installation

Prepare environment variables first:

```bash
cp mysearch/.env.example mysearch/.env
```

Minimal config:

```env
MYSEARCH_TAVILY_API_KEY=tvly-...
MYSEARCH_FIRECRAWL_API_KEY=fc-...
MYSEARCH_XAI_API_KEY=xai-...
```

Install:

```bash
./install.sh
```

The installer will:

1. install `mysearch/requirements.txt`
2. register `mysearch` in Claude Code if `claude` is available
3. register `mysearch` in Codex if `codex` is available
4. forward existing `MYSEARCH_*` environment variables into the MCP entry

`install.sh` will load `mysearch/.env` first when it exists.

Verify:

```bash
claude mcp list
codex mcp list
```

## Official mode and compatible mode

Official mode:

```env
MYSEARCH_XAI_BASE_URL=https://api.x.ai/v1
MYSEARCH_XAI_RESPONSES_PATH=/responses
MYSEARCH_XAI_SEARCH_MODE=official
```

Compatible mode:

```env
MYSEARCH_XAI_BASE_URL=https://media.example.com/v1
MYSEARCH_XAI_SOCIAL_BASE_URL=https://your-social-gateway.example.com
MYSEARCH_XAI_SEARCH_MODE=compatible
MYSEARCH_XAI_API_KEY=your-gateway-token
```

In this mode:

- `MYSEARCH_XAI_BASE_URL` points to the model / `/responses` gateway
- `MYSEARCH_XAI_SOCIAL_BASE_URL` points to the social gateway root
- `MySearch` appends `/social/search` by default

## Integrated social gateway

Reference implementation:

- module: `mysearch.social_gateway`
- purpose: normalize xAI-compatible `/responses` output into a stable social
  search result shape

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

## Common Usage

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

X sentiment:

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

Comparison query:

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

Extract content:

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

More architecture notes:
[../docs/mysearch-architecture.md](../docs/mysearch-architecture.md).
