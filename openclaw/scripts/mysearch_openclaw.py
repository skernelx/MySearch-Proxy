#!/usr/bin/env python3
"""OpenClaw wrapper for MySearch."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parents[1]


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value[:1] == value[-1:] and value[:1] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _load_mapping_env(raw_env: dict[str, object]) -> None:
    for key, value in raw_env.items():
        if not isinstance(value, str):
            continue
        cleaned = value.strip()
        if not cleaned:
            continue
        os.environ.setdefault(key, cleaned)


def _load_openclaw_skill_env(base_dir: Path) -> None:
    candidates: list[Path] = []
    explicit = os.getenv("OPENCLAW_CONFIG_PATH", "").strip()
    if explicit:
        candidates.append(Path(explicit).expanduser())
    candidates.append(base_dir.parents[1] / "openclaw.json")

    seen: set[Path] = set()
    for path in candidates:
        if path in seen or not path.exists():
            continue
        seen.add(path)
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        env = (((config.get("skills") or {}).get("entries") or {}).get("mysearch") or {}).get("env") or {}
        if isinstance(env, dict):
            _load_mapping_env(env)
            return


def _bootstrap_error(message: str) -> int:
    print(message, file=sys.stderr)
    print(
        f"Run: bash {BASE_DIR / 'scripts' / 'install_openclaw_skill.sh'}",
        file=sys.stderr,
    )
    return 2


def _runtime_imports():
    runtime_dir = BASE_DIR / "runtime"
    runtime_pkg = runtime_dir / "mysearch"
    if not runtime_pkg.exists():
        raise RuntimeError("MySearch runtime is not installed inside this skill yet.")

    if str(runtime_dir) not in sys.path:
        sys.path.insert(0, str(runtime_dir))

    try:
        from mysearch.clients import MySearchClient, MySearchError  # type: ignore
    except ModuleNotFoundError as exc:  # pragma: no cover - runtime bootstrap path
        raise RuntimeError(
            "MySearch bundled runtime is incomplete. Reinstall the skill package."
        ) from exc

    return MySearchClient, MySearchError


def _parse_csv(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    values = [item.strip() for item in raw.split(",") if item.strip()]
    return values or None


def _snippet(item: dict[str, Any]) -> str:
    for key in ("content", "snippet", "summary", "text", "excerpt"):
        value = (item.get(key) or "").strip()
        if value:
            return value
    return ""


def _result_lines(items: list[dict[str, Any]], heading: str) -> list[str]:
    lines = [heading, ""]
    if not items:
        lines.append("- 无结果")
        lines.append("")
        return lines

    for index, item in enumerate(items, start=1):
        title = (item.get("title") or item.get("url") or f"结果 {index}").strip()
        url = (item.get("url") or "").strip()
        snippet = _snippet(item)
        meta_bits: list[str] = []

        for key in ("source", "published_date", "date", "handle", "author"):
            value = item.get(key)
            if value:
                meta_bits.append(f"{key}={value}")

        lines.append(f"{index}. {title}")
        if url:
            lines.append(f"   {url}")
        if meta_bits:
            lines.append(f"   {' | '.join(meta_bits)}")
        if snippet:
            compact = " ".join(snippet.split())
            if len(compact) > 400:
                compact = f"{compact[:397]}..."
            lines.append(f"   {compact}")
        lines.append("")
    return lines


def _render_nested_search(title: str, payload: dict[str, Any]) -> list[str]:
    lines = [f"## {title}", ""]
    route = payload.get("route") or {}
    if route:
        lines.append(f"- route: `{route.get('selected', '')}`")
        if route.get("reason"):
            lines.append(f"- reason: {route['reason']}")
        lines.append("")

    answer = (payload.get("answer") or "").strip()
    if answer:
        lines.extend(["### Answer", "", answer, ""])

    lines.extend(_result_lines(payload.get("results") or [], "### Results"))
    return lines


def _render_health(payload: dict[str, Any]) -> str:
    lines = [
        "# MySearch Health",
        "",
        f"- server_name: `{payload.get('server_name', '')}`",
        f"- timeout_seconds: `{payload.get('timeout_seconds', '')}`",
        f"- xai_model: `{payload.get('xai_model', '')}`",
        "",
    ]

    for provider_name, info in (payload.get("providers") or {}).items():
        lines.extend(
            [
                f"## {provider_name}",
                "",
                f"- base_url: `{info.get('base_url', '')}`",
                f"- auth_mode: `{info.get('auth_mode', '')}`",
                f"- search_mode: `{info.get('search_mode', '')}`",
                f"- available_keys: `{info.get('available_keys', 0)}`",
                f"- live_status: `{info.get('live_status', '')}`",
            ]
        )
        if info.get("live_error"):
            lines.append(f"- live_error: {info['live_error']}")
        alternate = info.get("alternate_base_urls") or {}
        for key, value in alternate.items():
            if value:
                lines.append(f"- alternate.{key}: `{value}`")
        lines.append("")

    return "\n".join(lines).strip()


def _render_search(payload: dict[str, Any]) -> str:
    lines = [
        "# MySearch Search",
        "",
        f"- query: {payload.get('query', '')}",
        f"- provider: `{payload.get('provider', '')}`",
        f"- intent: `{payload.get('intent', '')}`",
        f"- strategy: `{payload.get('strategy', '')}`",
    ]
    route = payload.get("route") or {}
    if route:
        lines.append(f"- route: `{route.get('selected', '')}`")
        if route.get("reason"):
            lines.append(f"- reason: {route['reason']}")
    lines.append("")

    if payload.get("provider") == "hybrid" and (payload.get("web") or payload.get("social")):
        if payload.get("web"):
            lines.extend(_render_nested_search("Web", payload["web"]))
        if payload.get("social"):
            lines.extend(_render_nested_search("Social / X", payload["social"]))
    else:
        answer = (payload.get("answer") or "").strip()
        if answer:
            lines.extend(["## Answer", "", answer, ""])
        lines.extend(_result_lines(payload.get("results") or [], "## Results"))

    citations = payload.get("citations") or []
    if citations:
        lines.extend(["## Citations", ""])
        for item in citations:
            title = item.get("title") or item.get("url") or "citation"
            url = item.get("url") or ""
            if url:
                lines.append(f"- {title}: {url}")
            else:
                lines.append(f"- {title}")
        lines.append("")

    return "\n".join(lines).strip()


def _render_extract(payload: dict[str, Any]) -> str:
    lines = [
        "# MySearch Extract",
        "",
        f"- url: {payload.get('url', '')}",
        f"- provider: `{payload.get('provider', '')}`",
    ]
    if payload.get("warning"):
        lines.append(f"- warning: {payload['warning']}")
    if payload.get("fallback"):
        fallback = payload["fallback"]
        lines.append(
            f"- fallback: `{fallback.get('from', '')}` -> `{fallback.get('to', '')}` ({fallback.get('reason', '')})"
        )
    lines.extend(["", "## Content", "", payload.get("content", "").strip() or "(empty)"])
    return "\n".join(lines).strip()


def _render_research(payload: dict[str, Any]) -> str:
    lines = [
        "# MySearch Research",
        "",
        f"- query: {payload.get('query', '')}",
        f"- provider: `{payload.get('provider', '')}`",
        f"- intent: `{payload.get('intent', '')}`",
        f"- strategy: `{payload.get('strategy', '')}`",
        "",
    ]

    if payload.get("web_search"):
        lines.extend(_render_nested_search("Web Search", payload["web_search"]))

    pages = payload.get("pages") or []
    lines.extend(["## Pages", ""])
    if not pages:
        lines.append("- 无抓取页面")
        lines.append("")
    else:
        for index, page in enumerate(pages, start=1):
            url = page.get("url") or f"page-{index}"
            provider = page.get("provider") or ""
            excerpt = (page.get("excerpt") or "").strip()
            lines.append(f"{index}. {url}")
            if provider:
                lines.append(f"   provider={provider}")
            if page.get("error"):
                lines.append(f"   error={page['error']}")
            if page.get("warning"):
                lines.append(f"   warning={page['warning']}")
            if excerpt:
                compact = " ".join(excerpt.split())
                if len(compact) > 400:
                    compact = f"{compact[:397]}..."
                lines.append(f"   {compact}")
            lines.append("")

    if payload.get("social_search"):
        lines.extend(_render_nested_search("Social / X", payload["social_search"]))
    elif payload.get("social_error"):
        lines.extend(["## Social / X", "", f"- {payload['social_error']}", ""])

    evidence = payload.get("evidence") or {}
    if evidence:
        lines.extend(["## Evidence", ""])
        for key, value in evidence.items():
            lines.append(f"- {key}: {value}")
        lines.append("")

    return "\n".join(lines).strip()


def main() -> int:
    _load_openclaw_skill_env(BASE_DIR)
    _load_env_file(BASE_DIR / ".env")
    _load_env_file(BASE_DIR / "runtime" / ".env")

    try:
        MySearchClient, MySearchError = _runtime_imports()
    except RuntimeError as exc:
        return _bootstrap_error(str(exc))

    parser = argparse.ArgumentParser(description="OpenClaw wrapper for MySearch")
    parser.add_argument("--format", choices=("md", "json"), default="md")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("health", help="Show provider health")

    search_parser = subparsers.add_parser("search", help="Run a MySearch search")
    search_parser.add_argument("--query", required=True)
    search_parser.add_argument("--mode", default="auto", choices=("auto", "web", "news", "social", "docs", "research", "github", "pdf"))
    search_parser.add_argument("--intent", default="auto", choices=("auto", "factual", "status", "comparison", "tutorial", "exploratory", "news", "resource"))
    search_parser.add_argument("--strategy", default="auto", choices=("auto", "fast", "balanced", "verify", "deep"))
    search_parser.add_argument("--provider", default="auto", choices=("auto", "tavily", "firecrawl", "exa", "xai"))
    search_parser.add_argument("--sources", default="")
    search_parser.add_argument("-n", "--max-results", type=int, default=5)
    search_parser.add_argument("--include-content", action="store_true")
    search_parser.add_argument("--no-answer", action="store_true")
    search_parser.add_argument("--domains", default="")
    search_parser.add_argument("--exclude-domains", default="")
    search_parser.add_argument("--x-handles", default="")
    search_parser.add_argument("--exclude-x-handles", default="")
    search_parser.add_argument("--from-date", default="")
    search_parser.add_argument("--to-date", default="")

    extract_parser = subparsers.add_parser("extract", help="Extract a single URL")
    extract_parser.add_argument("--url", required=True)
    extract_parser.add_argument("--provider", default="auto", choices=("auto", "firecrawl", "tavily"))

    research_parser = subparsers.add_parser("research", help="Run a MySearch research bundle")
    research_parser.add_argument("--query", required=True)
    research_parser.add_argument("--mode", default="auto", choices=("auto", "web", "news", "social", "docs", "research", "github", "pdf"))
    research_parser.add_argument("--intent", default="auto", choices=("auto", "factual", "status", "comparison", "tutorial", "exploratory", "news", "resource"))
    research_parser.add_argument("--strategy", default="auto", choices=("auto", "fast", "balanced", "verify", "deep"))
    research_parser.add_argument("--web-max-results", type=int, default=5)
    research_parser.add_argument("--social-max-results", type=int, default=5)
    research_parser.add_argument("--scrape-top-n", type=int, default=3)
    research_parser.add_argument("--include-social", action="store_true", default=False)
    research_parser.add_argument("--domains", default="")
    research_parser.add_argument("--exclude-domains", default="")
    research_parser.add_argument("--x-handles", default="")
    research_parser.add_argument("--exclude-x-handles", default="")
    research_parser.add_argument("--from-date", default="")
    research_parser.add_argument("--to-date", default="")

    args = parser.parse_args()
    client = MySearchClient()

    try:
        if args.command == "health":
            payload = client.health()
            renderer = _render_health
        elif args.command == "search":
            payload = client.search(
                query=args.query,
                mode=args.mode,
                intent=args.intent,
                strategy=args.strategy,
                provider=args.provider,
                sources=_parse_csv(args.sources),
                max_results=args.max_results,
                include_content=args.include_content,
                include_answer=not args.no_answer,
                include_domains=_parse_csv(args.domains),
                exclude_domains=_parse_csv(args.exclude_domains),
                allowed_x_handles=_parse_csv(args.x_handles),
                excluded_x_handles=_parse_csv(args.exclude_x_handles),
                from_date=args.from_date or None,
                to_date=args.to_date or None,
            )
            renderer = _render_search
        elif args.command == "extract":
            payload = client.extract_url(
                url=args.url,
                formats=["markdown"],
                provider=args.provider,
            )
            renderer = _render_extract
        else:
            payload = client.research(
                query=args.query,
                web_max_results=args.web_max_results,
                social_max_results=args.social_max_results,
                scrape_top_n=args.scrape_top_n,
                include_social=args.include_social,
                mode=args.mode,
                intent=args.intent,
                strategy=args.strategy,
                include_domains=_parse_csv(args.domains),
                exclude_domains=_parse_csv(args.exclude_domains),
                allowed_x_handles=_parse_csv(args.x_handles),
                excluded_x_handles=_parse_csv(args.exclude_x_handles),
                from_date=args.from_date or None,
                to_date=args.to_date or None,
            )
            renderer = _render_research
    except MySearchError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(renderer(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
