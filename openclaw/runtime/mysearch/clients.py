"""MySearch provider client 和自动路由。"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timezone
from typing import Any, Callable, Literal
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from mysearch.config import MySearchConfig, ProviderConfig
from mysearch.keyring import MySearchKeyRing


SearchMode = Literal["auto", "web", "news", "social", "docs", "research", "github", "pdf"]
SearchIntent = Literal[
    "auto",
    "factual",
    "status",
    "comparison",
    "tutorial",
    "exploratory",
    "news",
    "resource",
]
ResolvedSearchIntent = Literal[
    "factual",
    "status",
    "comparison",
    "tutorial",
    "exploratory",
    "news",
    "resource",
]
SearchStrategy = Literal["auto", "fast", "balanced", "verify", "deep"]
ProviderName = Literal["auto", "tavily", "firecrawl", "exa", "xai"]


class MySearchError(RuntimeError):
    """MySearch 调用失败。"""


class MySearchHTTPError(MySearchError):
    """携带 provider 与状态码的 HTTP 错误。"""

    def __init__(
        self,
        *,
        provider: str,
        status_code: int,
        detail: Any,
        url: str,
    ) -> None:
        self.provider = provider
        self.status_code = status_code
        self.detail = detail
        self.url = url
        super().__init__(self._build_message())

    @property
    def is_auth_error(self) -> bool:
        return self.status_code in {401, 403}

    def _build_message(self) -> str:
        detail_text = _stringify_error_detail(self.detail)
        if self.is_auth_error:
            return (
                f"{self.provider} is configured but the API key was rejected "
                f"(HTTP {self.status_code}): {detail_text or 'authentication failed'}"
            )
        return (
            f"{self.provider} request failed "
            f"(HTTP {self.status_code}): {detail_text or 'unknown error'}"
        )


def _stringify_error_detail(detail: Any) -> str:
    if isinstance(detail, str):
        return detail.strip()
    if detail is None:
        return ""
    if isinstance(detail, (dict, list)):
        return json.dumps(detail, ensure_ascii=False)
    return str(detail).strip()


@dataclass(slots=True)
class RouteDecision:
    provider: str
    reason: str
    tavily_topic: str = "general"
    firecrawl_categories: list[str] | None = None
    sources: list[str] | None = None


class MySearchClient:
    def __init__(
        self,
        config: MySearchConfig | None = None,
        keyring: MySearchKeyRing | None = None,
    ) -> None:
        self.config = config or MySearchConfig.from_env()
        self.keyring = keyring or MySearchKeyRing(self.config)
        self._cache_lock = threading.Lock()
        self._cache_ttls = {
            "search": self.config.search_cache_ttl_seconds,
            "extract": self.config.extract_cache_ttl_seconds,
        }
        self._cache_store: dict[str, dict[str, dict[str, Any]]] = {
            "search": {},
            "extract": {},
        }
        self._cache_stats: dict[str, dict[str, int]] = {
            "search": {"hits": 0, "misses": 0},
            "extract": {"hits": 0, "misses": 0},
        }
        self._cache_max_entries = 256
        self._provider_probe_ttl_seconds = 300
        self._provider_probe_cache: dict[str, dict[str, Any]] = {}

    def health(self) -> dict[str, Any]:
        keyring_info = self.keyring.describe()
        cache = self._cache_health()
        providers = {
            "tavily": self._describe_provider(self.config.tavily, keyring_info["tavily"]),
            "firecrawl": self._describe_provider(
                self.config.firecrawl,
                keyring_info["firecrawl"],
            ),
            "exa": self._describe_provider(self.config.exa, keyring_info["exa"]),
            "xai": self._describe_provider(self.config.xai, keyring_info["xai"]),
        }
        return {
            "server_name": self.config.server_name,
            "timeout_seconds": self.config.timeout_seconds,
            "xai_model": self.config.xai_model,
            "mcp": {
                "default_transport": "stdio",
                "host": self.config.mcp_host,
                "port": self.config.mcp_port,
                "mount_path": self.config.mcp_mount_path,
                "sse_path": self.config.mcp_sse_path,
                "streamable_http_path": self.config.mcp_streamable_http_path,
                "stateless_http": self.config.mcp_stateless_http,
                "streamable_http_url": (
                    f"http://{self.config.mcp_host}:{self.config.mcp_port}"
                    f"{self.config.mcp_streamable_http_path}"
                ),
            },
            "runtime": {
                "max_parallel_workers": self.config.max_parallel_workers,
                "cache_ttl_seconds": {
                    "search": self.config.search_cache_ttl_seconds,
                    "extract": self.config.extract_cache_ttl_seconds,
                },
            },
            "routing_defaults": {
                "web": "tavily",
                "docs": "firecrawl",
                "content": "firecrawl",
                "social": "xai",
                "fallback": "exa",
            },
            "providers": providers,
            "cache": cache,
        }

    def _cache_health(self) -> dict[str, dict[str, int]]:
        snapshot: dict[str, dict[str, int]] = {}
        with self._cache_lock:
            now = time.monotonic()
            for namespace in self._cache_store:
                self._prune_expired_cache_entries_locked(namespace, now)
                stats = self._cache_stats[namespace]
                snapshot[namespace] = {
                    "ttl_seconds": self._cache_ttls.get(namespace, 0),
                    "entries": len(self._cache_store[namespace]),
                    "hits": stats["hits"],
                    "misses": stats["misses"],
                }
        return snapshot

    def _prune_expired_cache_entries_locked(self, namespace: str, now: float) -> None:
        expired_keys = [
            key
            for key, payload in self._cache_store[namespace].items()
            if payload.get("expires_at", 0.0) <= now
        ]
        for key in expired_keys:
            self._cache_store[namespace].pop(key, None)

    def _cache_get(self, namespace: str, cache_key: str) -> dict[str, Any] | None:
        ttl_seconds = self._cache_ttls.get(namespace, 0)
        if ttl_seconds <= 0:
            return None

        with self._cache_lock:
            now = time.monotonic()
            self._prune_expired_cache_entries_locked(namespace, now)
            payload = self._cache_store[namespace].get(cache_key)
            if payload is None:
                self._cache_stats[namespace]["misses"] += 1
                return None

            self._cache_stats[namespace]["hits"] += 1
            return copy.deepcopy(payload["value"])

    def _cache_set(self, namespace: str, cache_key: str, value: dict[str, Any]) -> None:
        ttl_seconds = self._cache_ttls.get(namespace, 0)
        if ttl_seconds <= 0:
            return

        with self._cache_lock:
            now = time.monotonic()
            store = self._cache_store[namespace]
            if len(store) >= self._cache_max_entries:
                self._prune_expired_cache_entries_locked(namespace, now)
            if len(store) >= self._cache_max_entries:
                oldest_key = min(store, key=lambda k: store[k].get("expires_at", 0.0))
                store.pop(oldest_key, None)
            store[cache_key] = {
                "expires_at": now + ttl_seconds,
                "value": copy.deepcopy(value),
            }

    def _build_cache_key(self, namespace: str, payload: dict[str, Any]) -> str:
        serialized = json.dumps(
            {
                "namespace": namespace,
                "payload": payload,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def _execute_parallel(
        self,
        tasks: dict[str, Callable[[], Any]],
        *,
        max_workers: int | None = None,
    ) -> tuple[dict[str, Any], dict[str, Exception]]:
        if not tasks:
            return {}, {}

        if len(tasks) == 1:
            name, task = next(iter(tasks.items()))
            try:
                return {name: task()}, {}
            except Exception as exc:  # pragma: no cover - defensive
                return {}, {name: exc}

        worker_count = min(max_workers or self.config.max_parallel_workers, len(tasks))
        results: dict[str, Any] = {}
        errors: dict[str, Exception] = {}
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="mysearch") as executor:
            future_map: dict[Future[Any], str] = {
                executor.submit(task): name
                for name, task in tasks.items()
            }
            for future, name in future_map.items():
                try:
                    results[name] = future.result(timeout=self.config.timeout_seconds + 5)
                except Exception as exc:  # pragma: no cover - network/runtime dependent
                    errors[name] = exc
        return results, errors

    def _raise_parallel_error(self, errors: dict[str, Exception], task_name: str) -> None:
        error = errors.get(task_name)
        if error is None:
            return
        if isinstance(error, MySearchError):
            raise error
        raise MySearchError(str(error))

    def _should_cache_search(
        self,
        *,
        decision: RouteDecision,
        normalized_sources: list[str],
    ) -> bool:
        if self.config.search_cache_ttl_seconds <= 0:
            return False
        if "x" in normalized_sources:
            return False
        if decision.provider == "xai":
            return False
        return True

    def _build_search_cache_key(
        self,
        *,
        query: str,
        mode: SearchMode,
        resolved_intent: ResolvedSearchIntent,
        resolved_strategy: SearchStrategy,
        provider: ProviderName,
        normalized_sources: list[str],
        include_content: bool,
        include_answer: bool,
        include_domains: list[str] | None,
        exclude_domains: list[str] | None,
        decision: RouteDecision,
    ) -> str:
        return self._build_cache_key(
            "search",
            {
                "query": query,
                "mode": mode,
                "intent": resolved_intent,
                "strategy": resolved_strategy,
                "provider": provider,
                "normalized_sources": normalized_sources,
                "include_content": include_content,
                "include_answer": include_answer,
                "include_domains": sorted(set(include_domains or [])),
                "exclude_domains": sorted(set(exclude_domains or [])),
                "route_provider": decision.provider,
                "tavily_topic": decision.tavily_topic,
                "firecrawl_categories": decision.firecrawl_categories or [],
            },
        )

    def _build_extract_cache_key(
        self,
        *,
        url: str,
        formats: list[str],
        only_main_content: bool,
        provider: Literal["auto", "firecrawl", "tavily"],
    ) -> str:
        return self._build_cache_key(
            "extract",
            {
                "url": url,
                "formats": formats,
                "only_main_content": only_main_content,
                "provider": provider,
            },
        )

    def _annotate_cache(
        self,
        result: dict[str, Any],
        *,
        namespace: str,
        hit: bool,
    ) -> dict[str, Any]:
        annotated = copy.deepcopy(result)
        cache_meta = dict(annotated.get("cache") or {})
        cache_meta[namespace] = {
            "hit": hit,
            "ttl_seconds": self._cache_ttls.get(namespace, 0),
        }
        annotated["cache"] = cache_meta
        return annotated

    def _annotate_search_debug(
        self,
        result: dict[str, Any],
        *,
        provider: ProviderName,
        normalized_sources: list[str],
        resolved_intent: ResolvedSearchIntent,
        resolved_strategy: SearchStrategy,
        decision: RouteDecision,
        include_content: bool,
        include_answer: bool,
        cache_hit: bool,
    ) -> dict[str, Any]:
        annotated = copy.deepcopy(result)
        annotated["route_debug"] = {
            "requested_provider": provider,
            "route_provider": decision.provider,
            "normalized_sources": normalized_sources,
            "resolved_intent": resolved_intent,
            "resolved_strategy": resolved_strategy,
            "include_content": include_content,
            "include_answer": include_answer,
            "cache_hit": cache_hit,
        }
        return annotated

    def search(
        self,
        *,
        query: str,
        mode: SearchMode = "auto",
        intent: SearchIntent = "auto",
        strategy: SearchStrategy = "auto",
        provider: ProviderName = "auto",
        sources: list[Literal["web", "x"]] | None = None,
        max_results: int = 5,
        include_content: bool = False,
        include_answer: bool = True,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
        allowed_x_handles: list[str] | None = None,
        excluded_x_handles: list[str] | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
        include_x_images: bool = False,
        include_x_videos: bool = False,
    ) -> dict[str, Any]:
        query = query.strip()
        if not query:
            raise MySearchError("query must not be empty")
        if mode == "github" and not include_domains:
            include_domains = ["github.com"]

        normalized_sources = sorted(set(sources or []))
        if not normalized_sources:
            if mode == "social" or allowed_x_handles or excluded_x_handles:
                normalized_sources = ["x"]
            else:
                normalized_sources = ["web"]
        resolved_intent = self._resolve_intent(
            query=query,
            mode=mode,
            intent=intent,
            sources=normalized_sources,
        )
        resolved_strategy = self._resolve_strategy(
            mode=mode,
            intent=resolved_intent,
            strategy=strategy,
            sources=normalized_sources,
            include_content=include_content,
        )
        effective_include_answer = self._should_request_search_answer(
            requested=include_answer,
            mode=mode,
            intent=resolved_intent,
            strategy=resolved_strategy,
            include_content=include_content,
            include_domains=include_domains,
        )
        decision = self._route_search(
            query=query,
            mode=mode,
            intent=resolved_intent,
            provider=provider,
            sources=normalized_sources,
            include_content=include_content,
            allowed_x_handles=allowed_x_handles,
            excluded_x_handles=excluded_x_handles,
        )
        cacheable = self._should_cache_search(
            decision=decision,
            normalized_sources=normalized_sources,
        )
        cache_key = ""
        if cacheable:
            cache_key = self._build_search_cache_key(
                query=query,
                mode=mode,
                resolved_intent=resolved_intent,
                resolved_strategy=resolved_strategy,
                provider=provider,
                normalized_sources=normalized_sources,
                include_content=include_content,
                include_answer=effective_include_answer,
                include_domains=include_domains,
                exclude_domains=exclude_domains,
                decision=decision,
            )
            cached_result = self._cache_get("search", cache_key)
            if cached_result is not None:
                cached_result = self._annotate_cache(
                    cached_result,
                    namespace="search",
                    hit=True,
                )
                return self._annotate_search_debug(
                    cached_result,
                    provider=provider,
                    normalized_sources=normalized_sources,
                    resolved_intent=resolved_intent,
                    resolved_strategy=resolved_strategy,
                    decision=decision,
                    include_content=include_content,
                    include_answer=effective_include_answer,
                    cache_hit=True,
                )

        if decision.provider == "hybrid":
            parallel_results, parallel_errors = self._execute_parallel(
                {
                    "web": lambda: self.search(
                        query=query,
                        mode=mode,
                        intent=resolved_intent,
                        strategy=resolved_strategy,
                        provider="auto",
                        sources=["web"],
                        max_results=max_results,
                        include_content=include_content,
                        include_answer=effective_include_answer,
                        include_domains=include_domains,
                        exclude_domains=exclude_domains,
                    ),
                    "social": lambda: self._search_xai(
                        query=query,
                        sources=["x"],
                        max_results=max_results,
                        allowed_x_handles=allowed_x_handles,
                        excluded_x_handles=excluded_x_handles,
                        from_date=from_date,
                        to_date=to_date,
                        include_x_images=include_x_images,
                        include_x_videos=include_x_videos,
                    ),
                },
                max_workers=2,
            )
            self._raise_parallel_error(parallel_errors, "web")
            self._raise_parallel_error(parallel_errors, "social")
            web_result = parallel_results["web"]
            social_result = parallel_results["social"]
            web_route = web_result.get("route", {}).get("selected", web_result.get("provider", "tavily"))
            social_route = social_result.get("provider", "xai")
            web_results = list(web_result.get("results") or [])
            social_results = list(social_result.get("results") or [])
            hybrid_result = {
                "provider": "hybrid",
                "intent": resolved_intent,
                "strategy": resolved_strategy,
                "route": {
                    "selected": f"{web_route}+{social_route}",
                    "reason": decision.reason,
                },
                "query": query,
                "answer": web_result.get("answer") or social_result.get("answer") or "",
                "results": [*web_results, *social_results],
                "citations": self._dedupe_citations(
                    web_result.get("citations") or [],
                    social_result.get("citations") or [],
                ),
                "evidence": {
                    "providers_consulted": [web_result.get("provider"), social_result.get("provider")],
                    "web_result_count": len(web_results),
                    "social_result_count": len(social_results),
                    "citation_count": len(
                        self._dedupe_citations(
                            web_result.get("citations") or [],
                            social_result.get("citations") or [],
                        )
                    ),
                    "verification": "cross-provider",
                },
                "web": web_result,
                "social": social_result,
            }
            hybrid_result = self._annotate_search_debug(
                hybrid_result,
                provider=provider,
                normalized_sources=normalized_sources,
                resolved_intent=resolved_intent,
                resolved_strategy=resolved_strategy,
                decision=decision,
                include_content=include_content,
                include_answer=effective_include_answer,
                cache_hit=False,
            )
            return hybrid_result

        if self._should_blend_web_providers(
            requested_provider=provider,
            decision=decision,
            sources=normalized_sources,
            strategy=resolved_strategy,
        ):
            result = self._search_web_blended(
                query=query,
                mode=mode,
                intent=resolved_intent,
                strategy=resolved_strategy,
                decision=decision,
                max_results=max_results,
                include_content=include_content,
                include_answer=effective_include_answer,
                include_domains=include_domains,
                exclude_domains=exclude_domains,
            )
        elif decision.provider in ("tavily", "firecrawl", "exa"):
            result, fallback_info = self._search_with_fallback(
                primary_provider=decision.provider,
                query=query,
                max_results=max_results,
                mode=mode,
                intent=resolved_intent,
                decision=decision,
                include_answer=effective_include_answer,
                include_content=include_content,
                include_domains=include_domains,
                exclude_domains=exclude_domains,
            )
            if fallback_info:
                result["fallback"] = fallback_info
        elif decision.provider == "xai":
            result = self._search_xai(
                query=query,
                sources=decision.sources or ["x"],
                max_results=max_results,
                include_domains=include_domains,
                exclude_domains=exclude_domains,
                allowed_x_handles=allowed_x_handles,
                excluded_x_handles=excluded_x_handles,
                from_date=from_date,
                to_date=to_date,
                include_x_images=include_x_images,
                include_x_videos=include_x_videos,
            )
        else:
            raise MySearchError(f"Unsupported route decision: {decision.provider}")

        route_reason = decision.reason
        if result.get("provider") == "hybrid" and resolved_strategy in {"balanced", "verify", "deep"}:
            route_reason = f"{route_reason}；strategy={resolved_strategy} 已启用 Tavily + Firecrawl 交叉检索"
        fallback = result.get("fallback")
        if isinstance(fallback, dict):
            fallback_from = str(fallback.get("from", "")).strip()
            fallback_to = str(fallback.get("to", "")).strip()
            fallback_reason = str(fallback.get("reason", "")).strip()
            parts = [part for part in [fallback_from, fallback_to] if part]
            transition = " -> ".join(parts)
            if transition:
                route_reason = f"{route_reason}；{transition} fallback"
            if fallback_reason:
                route_reason = f"{route_reason}（{fallback_reason}）"
        secondary_error = str(result.get("secondary_error", "")).strip()
        if secondary_error:
            route_reason = (
                f"{route_reason}；secondary provider issue: "
                f"{self._summarize_route_error(secondary_error)}"
            )

        route_selected = result.pop("route_selected", result.get("provider", decision.provider))
        result["intent"] = resolved_intent
        result["strategy"] = resolved_strategy
        result["route"] = {
            "selected": route_selected,
            "reason": route_reason,
        }
        if cacheable and cache_key:
            self._cache_set("search", cache_key, result)
        result = self._annotate_cache(
            result,
            namespace="search",
            hit=False,
        )
        return self._annotate_search_debug(
            result,
            provider=provider,
            normalized_sources=normalized_sources,
            resolved_intent=resolved_intent,
            resolved_strategy=resolved_strategy,
            decision=decision,
            include_content=include_content,
            include_answer=effective_include_answer,
            cache_hit=False,
        )

    def extract_url(
        self,
        *,
        url: str,
        formats: list[str] | None = None,
        only_main_content: bool = True,
        provider: Literal["auto", "firecrawl", "tavily"] = "auto",
    ) -> dict[str, Any]:
        parsed_url = urlparse(url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise MySearchError("url must be an absolute http(s) URL")

        formats = formats or ["markdown"]
        cache_key = self._build_extract_cache_key(
            url=url,
            formats=formats,
            only_main_content=only_main_content,
            provider=provider,
        )
        cached_result = self._cache_get("extract", cache_key)
        if cached_result is not None:
            return self._annotate_cache(
                cached_result,
                namespace="extract",
                hit=True,
            )
        errors: list[str] = []
        firecrawl_result: dict[str, Any] | None = None
        firecrawl_issue = ""

        if provider == "auto":
            github_raw_result = self._extract_github_blob_raw(url=url)
            if github_raw_result is not None:
                self._cache_set("extract", cache_key, github_raw_result)
                return self._annotate_cache(
                    github_raw_result,
                    namespace="extract",
                    hit=False,
                )

        if provider in {"auto", "firecrawl"}:
            try:
                firecrawl_result = self._scrape_firecrawl(
                    url=url,
                    formats=formats,
                    only_main_content=only_main_content,
                )
                firecrawl_issue = self._extract_quality_issue(firecrawl_result) or ""
                if not firecrawl_issue:
                    self._cache_set("extract", cache_key, firecrawl_result)
                    return self._annotate_cache(
                        firecrawl_result,
                        namespace="extract",
                        hit=False,
                    )

                errors.append(f"firecrawl scrape returned {firecrawl_issue}")

                if provider == "firecrawl":
                    result = self._annotate_extract_warning(
                        firecrawl_result,
                        warning=f"firecrawl scrape returned {firecrawl_issue}",
                    )
                    return self._annotate_cache(
                        result,
                        namespace="extract",
                        hit=False,
                    )
            except MySearchError as exc:
                errors.append(f"firecrawl scrape failed: {exc}")
                if provider == "firecrawl":
                    raise

        if provider in {"auto", "tavily"}:
            try:
                tavily_result = self._extract_tavily(url=url)
                tavily_issue = self._extract_quality_issue(tavily_result)
                if provider == "auto" and errors and tavily_issue is None:
                    result = self._annotate_extract_fallback(
                        tavily_result,
                        fallback_from="firecrawl",
                        fallback_reason=" | ".join(errors),
                    )
                    self._cache_set("extract", cache_key, result)
                    return self._annotate_cache(
                        result,
                        namespace="extract",
                        hit=False,
                    )
                if tavily_issue is None:
                    self._cache_set("extract", cache_key, tavily_result)
                    return self._annotate_cache(
                        tavily_result,
                        namespace="extract",
                        hit=False,
                    )
                errors.append(f"tavily extract returned {tavily_issue}")
                if provider == "tavily":
                    result = self._annotate_extract_warning(
                        tavily_result,
                        warning=f"tavily extract returned {tavily_issue}",
                    )
                    return self._annotate_cache(
                        result,
                        namespace="extract",
                        hit=False,
                    )
            except MySearchError as exc:
                errors.append(f"tavily extract failed: {exc}")
                if provider == "tavily":
                    raise

        if firecrawl_result is not None and provider == "auto":
            result = self._annotate_extract_warning(
                firecrawl_result,
                warning=" | ".join(errors),
            )
            return self._annotate_cache(
                result,
                namespace="extract",
                hit=False,
            )

        raise MySearchError(" | ".join(errors) if errors else "no extraction provider available")

    def research(
        self,
        *,
        query: str,
        web_max_results: int = 5,
        social_max_results: int = 5,
        scrape_top_n: int = 3,
        include_social: bool = True,
        mode: SearchMode = "auto",
        intent: SearchIntent = "auto",
        strategy: SearchStrategy = "auto",
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
        allowed_x_handles: list[str] | None = None,
        excluded_x_handles: list[str] | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> dict[str, Any]:
        query = query.strip()
        if not query:
            raise MySearchError("query must not be empty")

        web_mode = "news" if mode == "news" else ("docs" if mode in {"docs", "github", "pdf"} else "web")
        research_tasks: dict[str, Callable[[], Any]] = {
            "web": lambda: self.search(
                query=query,
                mode=web_mode,
                intent=intent,
                strategy=strategy,
                provider="auto",
                sources=["web"],
                max_results=web_max_results,
                include_content=False,
                include_answer=True,
                include_domains=include_domains,
                exclude_domains=exclude_domains,
            )
        }
        if include_social:
            research_tasks["social"] = lambda: self.search(
                query=query,
                mode="social",
                intent="status",
                provider="auto",
                sources=["x"],
                max_results=social_max_results,
                allowed_x_handles=allowed_x_handles,
                excluded_x_handles=excluded_x_handles,
                from_date=from_date,
                to_date=to_date,
            )
        research_results, research_errors = self._execute_parallel(
            research_tasks,
            max_workers=2 if include_social else 1,
        )
        self._raise_parallel_error(research_errors, "web")
        web_search = research_results["web"]

        urls: list[str] = []
        if web_search.get("provider") == "hybrid":
            candidate_results = web_search.get("results") or web_search.get("web", {}).get("results", [])
        else:
            candidate_results = web_search.get("results", [])

        for result in candidate_results:
            url = (result.get("url") or "").strip()
            if not url or url in urls:
                continue
            urls.append(url)
            if len(urls) >= scrape_top_n:
                break

        pages: list[dict[str, Any]] = []
        page_tasks = {
            f"page:{index}": (
                lambda current_url=url: self.extract_url(
                    url=current_url,
                    formats=["markdown"],
                    only_main_content=True,
                )
            )
            for index, url in enumerate(urls)
        }
        page_results, page_errors = self._execute_parallel(
            page_tasks,
            max_workers=min(self.config.max_parallel_workers, max(1, len(page_tasks))),
        )
        for index, url in enumerate(urls):
            task_name = f"page:{index}"
            if task_name in page_results:
                page = page_results[task_name]
                page["excerpt"] = self._build_excerpt(page.get("content", ""))
                pages.append(page)
            else:
                error = page_errors.get(task_name)
                pages.append({"url": url, "error": str(error) if error else "unknown error"})

        social: dict[str, Any] | None = None
        social_error = ""
        if include_social:
            social = research_results.get("social")
            social_exc = research_errors.get("social")
            if social_exc is not None:
                social_error = str(social_exc)

        web_provider = web_search.get("provider", "")
        social_provider = social.get("provider", "") if social else ""
        providers_consulted = [item for item in [web_provider, social_provider] if item]
        citations = self._dedupe_citations(
            web_search.get("citations") or [],
            (social.get("citations") or []) if social else [],
        )

        return {
            "provider": "hybrid",
            "query": query,
            "intent": web_search.get("intent", intent if intent != "auto" else "factual"),
            "strategy": web_search.get("strategy", strategy if strategy != "auto" else "fast"),
            "web_search": web_search,
            "pages": pages,
            "social_search": social,
            "social_error": social_error,
            "citations": citations,
            "evidence": {
                "providers_consulted": providers_consulted,
                "web_result_count": len(candidate_results),
                "page_count": len([page for page in pages if not page.get("error")]),
                "citation_count": len(citations),
                "verification": "cross-provider"
                if web_provider == "hybrid" or len(providers_consulted) > 1
                else "single-provider",
            },
            "notes": [
                "默认用 Tavily 做发现，Firecrawl 做正文抓取，X 搜索走 xAI Responses API",
                "如果某个 provider 没配 key，会保留错误并尽量返回其余部分",
            ],
        }

    def _should_request_search_answer(
        self,
        *,
        requested: bool,
        mode: SearchMode,
        intent: ResolvedSearchIntent,
        strategy: SearchStrategy,
        include_content: bool,
        include_domains: list[str] | None,
    ) -> bool:
        if not requested:
            return False
        if include_content:
            return False
        if include_domains:
            return False
        if mode in {"docs", "github", "pdf"}:
            return False
        if intent == "resource":
            return False
        if strategy in {"verify", "deep"}:
            return False
        return True

    def _route_search(
        self,
        *,
        query: str,
        mode: SearchMode,
        intent: ResolvedSearchIntent,
        provider: ProviderName,
        sources: list[str] | None,
        include_content: bool,
        allowed_x_handles: list[str] | None,
        excluded_x_handles: list[str] | None,
    ) -> RouteDecision:
        normalized_sources = sorted(set(sources or ["web"]))
        query_lower = query.lower()

        if provider != "auto":
            if provider == "tavily":
                return RouteDecision(
                    provider="tavily",
                    reason="显式指定 Tavily",
                    tavily_topic="news" if mode == "news" or intent in {"news", "status"} else "general",
                )
            if provider == "firecrawl":
                return RouteDecision(
                    provider="firecrawl",
                    reason="显式指定 Firecrawl",
                    firecrawl_categories=self._firecrawl_categories(mode, intent),
                )
            if provider == "exa":
                return RouteDecision(
                    provider="exa",
                    reason="显式指定 Exa",
                )
            if provider == "xai":
                return RouteDecision(
                    provider="xai",
                    reason="显式指定 xAI/X 搜索",
                    sources=normalized_sources,
                )

        if normalized_sources == ["web", "x"] or (
            "x" in normalized_sources and "web" in normalized_sources
        ):
            return RouteDecision(provider="hybrid", reason="同时请求网页和 X 结果")

        if mode == "social" or "x" in normalized_sources:
            return RouteDecision(
                provider="xai",
                reason="社交舆情 / X 搜索更适合走 xAI",
                sources=["x"],
            )

        if allowed_x_handles or excluded_x_handles:
            return RouteDecision(
                provider="xai",
                reason="检测到 X handle 过滤条件",
                sources=["x"],
            )

        if mode in {"docs", "github", "pdf"}:
            if include_content:
                if not self._provider_can_serve(self.config.firecrawl) and self._provider_can_serve(
                    self.config.exa
                ):
                    return RouteDecision(
                        provider="exa",
                        reason="Firecrawl 未配置，文档正文查询回退到 Exa",
                    )
                return RouteDecision(
                    provider="firecrawl",
                    reason="文档正文查询优先走 Firecrawl",
                    firecrawl_categories=self._firecrawl_categories(mode, intent),
                )
            if self._provider_can_serve(self.config.tavily):
                return RouteDecision(
                    provider="tavily",
                    reason="文档类查询先用 Tavily 做官方页面发现，正文再交给 Firecrawl",
                    tavily_topic="general",
                )
            if not self._provider_can_serve(self.config.firecrawl) and self._provider_can_serve(
                self.config.exa
            ):
                return RouteDecision(
                    provider="exa",
                    reason="Firecrawl 未配置，文档类查询回退到 Exa",
                )
            return RouteDecision(
                provider="firecrawl",
                reason="文档 / GitHub / PDF 内容优先走 Firecrawl",
                firecrawl_categories=self._firecrawl_categories(mode, intent),
            )

        if include_content:
            if not self._provider_can_serve(self.config.firecrawl) and self._provider_can_serve(
                self.config.exa
            ):
                return RouteDecision(
                    provider="exa",
                    reason="Firecrawl 未配置，正文查询回退到 Exa",
                )
            return RouteDecision(
                provider="firecrawl",
                reason="请求里需要正文内容，优先用 Firecrawl search + scrape",
                firecrawl_categories=self._firecrawl_categories(mode, intent),
            )

        if intent in {"news", "status"} or mode == "news" or self._looks_like_news_query(query_lower):
            if not self._provider_can_serve(self.config.tavily) and self._provider_can_serve(
                self.config.exa
            ):
                return RouteDecision(
                    provider="exa",
                    reason="Tavily 未配置，新闻 / 状态类查询回退到 Exa",
                )
            return RouteDecision(
                provider="tavily",
                reason="状态 / 新闻类查询默认走 Tavily",
                tavily_topic="news",
            )

        if intent == "resource" or self._looks_like_docs_query(query_lower):
            if include_content:
                if not self._provider_can_serve(self.config.firecrawl) and self._provider_can_serve(
                    self.config.exa
                ):
                    return RouteDecision(
                        provider="exa",
                        reason="Firecrawl 未配置，resource 正文查询回退到 Exa",
                    )
                return RouteDecision(
                    provider="firecrawl",
                    reason="resource / docs 正文查询优先走 Firecrawl",
                    firecrawl_categories=self._firecrawl_categories("docs", intent),
                )
            if self._provider_can_serve(self.config.tavily):
                return RouteDecision(
                    provider="tavily",
                    reason="resource / docs 查询先用 Tavily 做页面发现，正文再交给 Firecrawl",
                    tavily_topic="general",
                )
            if not self._provider_can_serve(self.config.firecrawl) and self._provider_can_serve(
                self.config.exa
            ):
                return RouteDecision(
                    provider="exa",
                    reason="Firecrawl 未配置，resource / docs 类查询回退到 Exa",
                )
            return RouteDecision(
                provider="firecrawl",
                reason="resource / docs 类查询优先走 Firecrawl",
                firecrawl_categories=self._firecrawl_categories("docs", intent),
            )

        if mode == "research":
            if self._provider_can_serve(self.config.tavily):
                return RouteDecision(
                    provider="tavily",
                    reason="research 模式先用 Tavily 做发现，再按策略决定是否扩展验证",
                    tavily_topic="general",
                )
            if self._provider_can_serve(self.config.exa):
                return RouteDecision(
                    provider="exa",
                    reason="Tavily 未配置，research 发现阶段回退到 Exa",
                )
            if self._provider_can_serve(self.config.firecrawl):
                return RouteDecision(
                    provider="firecrawl",
                    reason="Tavily / Exa 未配置，research 发现阶段回退到 Firecrawl",
                    firecrawl_categories=self._firecrawl_categories(mode, intent),
                )
            return RouteDecision(
                provider="tavily",
                reason="research 模式默认走 Tavily（无可用替代）",
                tavily_topic="general",
            )

        if not self._provider_can_serve(self.config.tavily) and self._provider_can_serve(
            self.config.exa
        ):
            return RouteDecision(
                provider="exa",
                reason="Tavily 未配置，普通网页检索回退到 Exa",
            )

        return RouteDecision(
            provider="tavily",
            reason="普通网页检索默认走 Tavily",
            tavily_topic="general",
        )

    def _resolve_intent(
        self,
        *,
        query: str,
        mode: SearchMode,
        intent: SearchIntent,
        sources: list[str],
    ) -> ResolvedSearchIntent:
        if intent != "auto":
            return intent

        query_lower = query.lower()
        if mode == "news":
            return "news"
        if mode in {"docs", "github", "pdf"}:
            return "resource"
        if mode == "research":
            return "exploratory"
        if sources == ["x"]:
            return "status"
        if self._looks_like_news_query(query_lower):
            return "news"
        if self._looks_like_comparison_query(query_lower):
            return "comparison"
        if self._looks_like_tutorial_query(query_lower):
            return "tutorial"
        if self._looks_like_docs_query(query_lower):
            return "resource"
        if self._looks_like_status_query(query_lower):
            return "status"
        if self._looks_like_exploratory_query(query_lower):
            return "exploratory"
        return "factual"

    def _resolve_strategy(
        self,
        *,
        mode: SearchMode,
        intent: ResolvedSearchIntent,
        strategy: SearchStrategy,
        sources: list[str],
        include_content: bool,
    ) -> SearchStrategy:
        if strategy != "auto":
            return strategy

        if "web" in sources and "x" in sources:
            return "balanced"
        if mode == "research":
            return "deep"
        if intent in {"comparison", "exploratory"}:
            return "verify"
        if include_content or mode in {"docs", "github", "pdf"} or intent in {"resource", "tutorial"}:
            return "balanced"
        return "fast"

    def _should_blend_web_providers(
        self,
        *,
        requested_provider: ProviderName,
        decision: RouteDecision,
        sources: list[str],
        strategy: SearchStrategy,
    ) -> bool:
        if requested_provider != "auto":
            return False
        if decision.provider not in {"tavily", "firecrawl"}:
            return False
        if strategy not in {"balanced", "verify", "deep"}:
            return False
        if "x" in sources:
            return False
        return self._provider_can_serve(self.config.tavily) and self._provider_can_serve(
            self.config.firecrawl
        )

    # ------------------------------------------------------------------
    # Provider-level fallback: if the chosen provider fails, try others
    # ------------------------------------------------------------------

    _SEARCH_FALLBACK_CHAIN: dict[str, list[str]] = {
        "tavily": ["exa", "firecrawl"],
        "firecrawl": ["exa", "tavily"],
        "exa": ["firecrawl", "tavily"],
    }

    def _search_with_fallback(
        self,
        *,
        primary_provider: str,
        query: str,
        max_results: int,
        mode: SearchMode,
        intent: ResolvedSearchIntent,
        decision: RouteDecision,
        include_answer: bool,
        include_content: bool,
        include_domains: list[str] | None,
        exclude_domains: list[str] | None,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        """Try the primary provider; on failure, walk the fallback chain."""
        chain = [primary_provider] + self._SEARCH_FALLBACK_CHAIN.get(primary_provider, [])
        last_error: Exception | None = None
        for provider_name in chain:
            try:
                result = self._dispatch_single_provider(
                    provider_name=provider_name,
                    query=query,
                    max_results=max_results,
                    mode=mode,
                    intent=intent,
                    decision=decision,
                    include_answer=include_answer,
                    include_content=include_content,
                    include_domains=include_domains,
                    exclude_domains=exclude_domains,
                )
                fallback_info = None
                if provider_name != primary_provider:
                    fallback_info = {
                        "from": primary_provider,
                        "to": provider_name,
                        "reason": str(last_error)[:200] if last_error else "primary provider failed",
                    }
                return result, fallback_info
            except MySearchError as exc:
                last_error = exc
                continue
            except Exception as exc:
                last_error = MySearchError(f"{provider_name}: {exc}")
                continue
        raise MySearchError(
            f"All providers failed for query '{query[:80]}': {last_error}"
        )

    def _dispatch_single_provider(
        self,
        *,
        provider_name: str,
        query: str,
        max_results: int,
        mode: SearchMode,
        intent: ResolvedSearchIntent,
        decision: RouteDecision,
        include_answer: bool,
        include_content: bool,
        include_domains: list[str] | None,
        exclude_domains: list[str] | None,
    ) -> dict[str, Any]:
        """Call exactly one search provider by name."""
        if provider_name == "tavily":
            return self._search_tavily(
                query=query,
                max_results=max_results,
                topic=decision.tavily_topic,
                include_answer=include_answer,
                include_content=include_content,
                include_domains=include_domains,
                exclude_domains=exclude_domains,
            )
        if provider_name == "firecrawl":
            return self._search_firecrawl(
                query=query,
                max_results=max_results,
                categories=decision.firecrawl_categories or self._firecrawl_categories(mode, intent),
                include_content=include_content or mode in {"docs", "research", "github", "pdf"},
                include_domains=include_domains,
                exclude_domains=exclude_domains,
            )
        if provider_name == "exa":
            return self._search_exa(
                query=query,
                max_results=max_results,
                include_domains=include_domains,
                exclude_domains=exclude_domains,
                include_content=include_content,
            )
        raise MySearchError(f"Unknown provider: {provider_name}")

    def _search_web_blended(
        self,
        *,
        query: str,
        mode: SearchMode,
        intent: ResolvedSearchIntent,
        strategy: SearchStrategy,
        decision: RouteDecision,
        max_results: int,
        include_content: bool,
        include_answer: bool,
        include_domains: list[str] | None,
        exclude_domains: list[str] | None,
    ) -> dict[str, Any]:
        if decision.provider == "tavily":
            tasks = {
                "primary": lambda: self._search_tavily(
                    query=query,
                    max_results=max_results,
                    topic=decision.tavily_topic,
                    include_answer=include_answer,
                    include_content=include_content,
                    include_domains=include_domains,
                    exclude_domains=exclude_domains,
                ),
                "secondary": lambda: self._search_firecrawl(
                    query=query,
                    max_results=max_results,
                    categories=self._firecrawl_categories(mode, intent),
                    include_content=include_content or strategy == "deep",
                    include_domains=include_domains,
                    exclude_domains=exclude_domains,
                ),
            }
        else:
            tasks = {
                "primary": lambda: self._search_firecrawl(
                    query=query,
                    max_results=max_results,
                    categories=decision.firecrawl_categories or self._firecrawl_categories(mode, intent),
                    include_content=include_content or strategy == "deep",
                    include_domains=include_domains,
                    exclude_domains=exclude_domains,
                ),
                "secondary": lambda: self._search_tavily(
                    query=query,
                    max_results=max_results,
                    topic="news" if intent in {"news", "status"} else "general",
                    include_answer=include_answer,
                    include_content=False,
                    include_domains=include_domains,
                    exclude_domains=exclude_domains,
                ),
            }

        blended_results, blended_errors = self._execute_parallel(tasks, max_workers=2)

        primary_failed = "primary" in blended_errors
        secondary_failed = "secondary" in blended_errors

        if primary_failed and not secondary_failed:
            # Primary down but secondary succeeded — use secondary as sole result
            primary_result = blended_results["secondary"]
            primary_result["fallback"] = {
                "from": decision.provider,
                "to": primary_result.get("provider", "unknown"),
                "reason": str(blended_errors["primary"])[:200],
            }
            secondary_result = None
            secondary_error = ""
        elif primary_failed and secondary_failed:
            # Both failed — raise with context from both
            primary_err = str(blended_errors["primary"])[:150]
            secondary_err = str(blended_errors["secondary"])[:150]
            raise MySearchError(
                f"Blended search failed: primary ({decision.provider}): {primary_err}; "
                f"secondary: {secondary_err}"
            )
        else:
            primary_result = blended_results["primary"]
            secondary_result = blended_results.get("secondary")
            secondary_error = str(blended_errors["secondary"]) if secondary_failed else ""

        merged = self._merge_search_payloads(
            primary_result=primary_result,
            secondary_result=secondary_result,
            max_results=max_results,
        )
        if self._should_rerank_resource_results(mode=mode, intent=intent):
            merged["results"] = self._rerank_resource_results(
                query=query,
                mode=mode,
                results=merged["results"],
                include_domains=include_domains,
            )
            merged["citations"] = self._align_citations_with_results(
                results=merged["results"],
                citations=merged["citations"],
            )
        providers_consulted = [primary_result.get("provider", "")]
        if secondary_result:
            providers_consulted.append(secondary_result.get("provider", ""))

        return {
            "provider": "hybrid" if secondary_result else primary_result.get("provider", decision.provider),
            "route_selected": "+".join([item for item in providers_consulted if item]),
            "query": query,
            "answer": primary_result.get("answer") or (secondary_result or {}).get("answer", ""),
            "results": merged["results"],
            "citations": merged["citations"],
            "evidence": {
                "providers_consulted": [item for item in providers_consulted if item],
                "matched_results": merged["matched_results"],
                "citation_count": len(merged["citations"]),
                "verification": "cross-provider" if secondary_result else "single-provider",
            },
            "primary_search": primary_result,
            "secondary_search": secondary_result,
            "secondary_error": secondary_error,
        }

    def _search_tavily(
        self,
        *,
        query: str,
        max_results: int,
        topic: str,
        include_answer: bool,
        include_content: bool,
        include_domains: list[str] | None,
        exclude_domains: list[str] | None,
    ) -> dict[str, Any]:
        provider = self.config.tavily
        key = self._get_key_or_raise(provider)
        payload: dict[str, Any] = {
            "query": query,
            "max_results": max_results,
            "search_depth": "advanced" if include_content else "basic",
            "topic": topic,
            "include_answer": include_answer,
            "include_raw_content": include_content,
        }
        if include_domains:
            payload["include_domains"] = include_domains
        if exclude_domains:
            payload["exclude_domains"] = exclude_domains

        response = self._request_json(
            provider=provider,
            method="POST",
            path=provider.path("search"),
            payload=payload,
            key=key.key,
        )
        return {
            "provider": "tavily",
            "transport": key.source,
            "query": response.get("query", query),
            "answer": response.get("answer", ""),
            "request_id": response.get("request_id", ""),
            "response_time": response.get("response_time"),
            "results": [
                {
                    "provider": "tavily",
                    "source": "web",
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "snippet": item.get("content", ""),
                    "content": item.get("raw_content", "") if include_content else "",
                    "score": item.get("score"),
                }
                for item in response.get("results", [])
            ],
            "citations": [
                {"title": item.get("title", ""), "url": item.get("url", "")}
                for item in response.get("results", [])
                if item.get("url")
            ],
        }

    def _search_firecrawl(
        self,
        *,
        query: str,
        max_results: int,
        categories: list[str],
        include_content: bool,
        include_domains: list[str] | None,
        exclude_domains: list[str] | None,
    ) -> dict[str, Any]:
        include_domains = [item.strip() for item in (include_domains or []) if item and item.strip()]
        exclude_domains = [item.strip() for item in (exclude_domains or []) if item and item.strip()]

        if include_domains:
            per_domain_results = []
            citations = []
            seen_urls: set[str] = set()
            retried_domains: list[str] = []
            for domain in include_domains:
                domain_result = self._search_firecrawl_once(
                    query=self._build_firecrawl_domain_query(
                        query=query,
                        include_domain=domain,
                        exclude_domains=exclude_domains,
                    ),
                    max_results=max_results,
                    categories=categories,
                    include_content=include_content,
                )
                if not domain_result.get("results"):
                    retry_result = self._search_firecrawl_domain_retry(
                        query=query,
                        max_results=max_results,
                        categories=categories,
                        include_content=include_content,
                        include_domain=domain,
                        exclude_domains=exclude_domains,
                    )
                    if retry_result is not None:
                        domain_result = retry_result
                        retried_domains.append(domain)
                per_domain_results.append(domain_result)
                for item in domain_result.get("results", []):
                    url = item.get("url", "")
                    if not url or url in seen_urls:
                        continue
                    seen_urls.add(url)
                    citations.append({"title": item.get("title", ""), "url": url})

            merged_results = self._merge_ranked_results(
                [result.get("results", []) for result in per_domain_results],
                max_results=max_results,
            )
            if not merged_results:
                fallback_result = self._search_firecrawl_domain_fallback(
                    query=query,
                    max_results=max_results,
                    include_content=include_content,
                    include_domains=include_domains,
                    exclude_domains=exclude_domains,
                )
                if fallback_result is not None:
                    return fallback_result
            response = {
                "provider": "firecrawl",
                "transport": per_domain_results[0].get("transport", "env") if per_domain_results else "env",
                "query": query,
                "answer": "",
                "results": merged_results,
                "citations": citations[:max_results],
            }
            if retried_domains:
                response["route_debug"] = {
                    "domain_filter_mode": "client_filter_retry",
                    "retried_include_domains": retried_domains,
                }
            return response

        return self._search_firecrawl_once(
            query=self._build_firecrawl_domain_query(
                query=query,
                include_domain=None,
                exclude_domains=exclude_domains,
            ),
            max_results=max_results,
            categories=categories,
            include_content=include_content,
        )

    def _search_firecrawl_domain_fallback(
        self,
        *,
        query: str,
        max_results: int,
        include_content: bool,
        include_domains: list[str],
        exclude_domains: list[str] | None,
    ) -> dict[str, Any] | None:
        if not self._provider_can_serve(self.config.tavily):
            return None

        fallback_result = self._search_tavily(
            query=query,
            max_results=max_results,
            topic="general",
            include_answer=False,
            include_content=include_content,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
        )
        if not fallback_result.get("results"):
            return None

        return {
            "provider": "hybrid",
            "route_selected": "firecrawl+tavily",
            "query": query,
            "answer": fallback_result.get("answer", ""),
            "results": fallback_result.get("results", []),
            "citations": fallback_result.get("citations", []),
            "primary_search": {
                "provider": "firecrawl",
                "query": query,
                "results": [],
                "citations": [],
            },
            "secondary_search": fallback_result,
            "secondary_error": "",
            "evidence": {
                "providers_consulted": ["firecrawl", fallback_result.get("provider", "tavily")],
                "matched_results": 0,
                "citation_count": len(fallback_result.get("citations", [])),
                "verification": "fallback",
            },
            "fallback": {
                "from": "firecrawl",
                "to": "tavily",
                "reason": "firecrawl returned 0 results for domain-filtered search",
            },
        }

    def _search_firecrawl_domain_retry(
        self,
        *,
        query: str,
        max_results: int,
        categories: list[str],
        include_content: bool,
        include_domain: str,
        exclude_domains: list[str] | None,
    ) -> dict[str, Any] | None:
        retry_result = self._search_firecrawl_once(
            query=self._build_firecrawl_domain_query(
                query=query,
                include_domain=None,
                exclude_domains=exclude_domains,
            ),
            max_results=max_results,
            categories=categories,
            include_content=include_content,
        )
        filtered_results = self._filter_results_by_domains(
            retry_result.get("results", []),
            include_domains=[include_domain],
            exclude_domains=exclude_domains,
        )
        if not filtered_results:
            return None

        return {
            "provider": "firecrawl",
            "transport": retry_result.get("transport", "env"),
            "query": query,
            "answer": retry_result.get("answer", ""),
            "results": filtered_results[:max_results],
            "citations": [
                {"title": item.get("title", ""), "url": item.get("url", "")}
                for item in filtered_results
                if item.get("url")
            ][:max_results],
            "route_debug": {
                "domain_filter_mode": "client_filter_retry",
                "include_domain": include_domain,
            },
        }

    def _search_firecrawl_once(
        self,
        *,
        query: str,
        max_results: int,
        categories: list[str],
        include_content: bool,
    ) -> dict[str, Any]:
        provider = self.config.firecrawl
        key = self._get_key_or_raise(provider)
        payload: dict[str, Any] = {
            "query": query,
            "limit": max_results,
        }
        if categories:
            payload["categories"] = [{"type": item} for item in categories]
        if include_content:
            payload["scrapeOptions"] = {
                "formats": ["markdown"],
                "onlyMainContent": True,
            }

        response = self._request_json(
            provider=provider,
            method="POST",
            path=provider.path("search"),
            payload=payload,
            key=key.key,
        )
        data = response.get("data") or {}
        results = []
        for source_name in ("web", "news"):
            for item in data.get(source_name, []) or []:
                results.append(
                    {
                        "provider": "firecrawl",
                        "source": source_name,
                        "title": item.get("title", ""),
                        "url": item.get("url", ""),
                        "snippet": item.get("description", "") or item.get("markdown", ""),
                        "content": item.get("markdown", "") if include_content else "",
                    }
                )

        return {
            "provider": "firecrawl",
            "transport": key.source,
            "query": query,
            "answer": "",
            "results": results,
            "citations": [
                {"title": item.get("title", ""), "url": item.get("url", "")}
                for item in results
                if item.get("url")
            ],
        }

    def _build_firecrawl_domain_query(
        self,
        *,
        query: str,
        include_domain: str | None,
        exclude_domains: list[str] | None,
    ) -> str:
        parts: list[str] = []
        if include_domain:
            parts.append(f"site:{include_domain}")
        for domain in exclude_domains or []:
            parts.append(f"-site:{domain}")
        parts.append(query)
        return " ".join(parts).strip()

    def _merge_ranked_results(
        self,
        result_lists: list[list[dict[str, Any]]],
        *,
        max_results: int,
    ) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        indexes = [0 for _ in result_lists]

        while len(merged) < max_results and result_lists:
            progressed = False
            for list_index, items in enumerate(result_lists):
                current_index = indexes[list_index]
                if current_index >= len(items):
                    continue
                candidate = dict(items[current_index])
                indexes[list_index] += 1
                progressed = True
                url = candidate.get("url", "")
                if url and url in seen_urls:
                    continue
                if url:
                    seen_urls.add(url)
                merged.append(candidate)
                if len(merged) >= max_results:
                    break
            if not progressed:
                break

        return merged

    def _filter_results_by_domains(
        self,
        results: list[dict[str, Any]],
        *,
        include_domains: list[str] | None,
        exclude_domains: list[str] | None,
    ) -> list[dict[str, Any]]:
        filtered: list[dict[str, Any]] = []
        for item in results:
            hostname = self._result_hostname(item)
            if include_domains and not any(
                self._domain_matches(hostname, domain) for domain in include_domains
            ):
                continue
            if exclude_domains and any(
                self._domain_matches(hostname, domain) for domain in exclude_domains
            ):
                continue
            filtered.append(dict(item))
        return filtered

    def _search_exa(
        self,
        *,
        query: str,
        max_results: int,
        include_domains: list[str] | None,
        exclude_domains: list[str] | None,
        include_content: bool,
    ) -> dict[str, Any]:
        provider = self.config.exa
        key = self._get_key_or_raise(provider)
        payload: dict[str, Any] = {
            "query": query,
            "numResults": max_results,
        }
        if include_content:
            payload["text"] = True
        if include_domains:
            payload["includeDomains"] = include_domains
        if exclude_domains:
            payload["excludeDomains"] = exclude_domains

        response = self._request_json(
            provider=provider,
            method="POST",
            path=provider.path("search"),
            payload=payload,
            key=key.key,
        )
        raw_results = response.get("results") or response.get("data") or []
        results = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            snippet = (
                item.get("snippet")
                or item.get("text")
                or item.get("summary")
                or item.get("highlight")
                or ""
            )
            content = item.get("text") if include_content else ""
            results.append(
                {
                    "provider": "exa",
                    "source": "web",
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "snippet": snippet,
                    "content": content or "",
                    "score": item.get("score"),
                    "published_date": item.get("publishedDate") or item.get("published_date") or "",
                }
            )

        return {
            "provider": "exa",
            "transport": key.source,
            "query": response.get("query", query),
            "answer": response.get("answer", ""),
            "results": results,
            "citations": [
                {"title": item.get("title", ""), "url": item.get("url", "")}
                for item in results
                if item.get("url")
            ],
        }

    def _search_xai(
        self,
        *,
        query: str,
        sources: list[str],
        max_results: int,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
        allowed_x_handles: list[str] | None = None,
        excluded_x_handles: list[str] | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
        include_x_images: bool = False,
        include_x_videos: bool = False,
    ) -> dict[str, Any]:
        provider = self.config.xai
        if provider.search_mode == "compatible":
            return self._search_xai_compatible(
                query=query,
                sources=sources,
                max_results=max_results,
                allowed_x_handles=allowed_x_handles,
                excluded_x_handles=excluded_x_handles,
                from_date=from_date,
                to_date=to_date,
                include_x_images=include_x_images,
                include_x_videos=include_x_videos,
            )

        key = self._get_key_or_raise(provider)
        payload = self._build_xai_responses_payload(
            query=query,
            sources=sources,
            max_results=max_results,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
            allowed_x_handles=allowed_x_handles,
            excluded_x_handles=excluded_x_handles,
            from_date=from_date,
            to_date=to_date,
            include_x_images=include_x_images,
            include_x_videos=include_x_videos,
        )
        response = self._request_json(
            provider=provider,
            method="POST",
            path=provider.path("responses"),
            payload=payload,
            key=key.key,
        )
        text = self._extract_xai_output_text(response)
        citations = self._extract_xai_citations(response)
        results = [
            {
                "provider": "xai",
                "source": "x" if "x" in sources else "web",
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": "",
                "content": "",
            }
            for item in citations
            if isinstance(item, dict)
        ]
        return {
            "provider": "xai",
            "transport": key.source,
            "query": query,
            "answer": text,
            "results": results,
            "citations": citations,
            "tool_usage": response.get("server_side_tool_usage") or response.get("tool_usage") or {},
        }

    def _search_xai_compatible(
        self,
        *,
        query: str,
        sources: list[str],
        max_results: int,
        allowed_x_handles: list[str] | None,
        excluded_x_handles: list[str] | None,
        from_date: str | None,
        to_date: str | None,
        include_x_images: bool,
        include_x_videos: bool,
    ) -> dict[str, Any]:
        provider = self.config.xai
        if "x" not in sources:
            raise MySearchError(
                "xai compatible mode only supports social/X queries; "
                "use Tavily/Firecrawl for web search or switch to official xAI mode"
            )

        search_path = provider.path("social_search")
        key = self._get_key_or_raise(provider)
        payload: dict[str, Any] = {
            "query": query,
            "source": "x",
            "max_results": max_results,
        }
        if allowed_x_handles:
            payload["allowed_x_handles"] = allowed_x_handles
        if excluded_x_handles:
            payload["excluded_x_handles"] = excluded_x_handles
        if from_date:
            payload["from_date"] = from_date
        if to_date:
            payload["to_date"] = to_date
        if include_x_images:
            payload["include_x_images"] = True
        if include_x_videos:
            payload["include_x_videos"] = True

        response = self._request_json(
            provider=provider,
            method="POST",
            path=search_path,
            payload=payload,
            key=key.key,
            base_url=provider.base_url_for("social_search"),
        )
        return self._normalize_social_gateway_response(
            response=response,
            query=query,
            transport=key.source,
            from_date=from_date,
            to_date=to_date,
        )

    def _scrape_firecrawl(
        self,
        *,
        url: str,
        formats: list[str],
        only_main_content: bool,
    ) -> dict[str, Any]:
        provider = self.config.firecrawl
        key = self._get_key_or_raise(provider)
        payload = {
            "url": url,
            "formats": formats,
            "onlyMainContent": only_main_content,
        }
        response = self._request_json(
            provider=provider,
            method="POST",
            path=provider.path("scrape"),
            payload=payload,
            key=key.key,
        )
        data = response.get("data") or {}
        content = data.get("markdown", "")
        if not content and "json" in data:
            content = json.dumps(data["json"], ensure_ascii=False, indent=2)
        return {
            "provider": "firecrawl",
            "transport": key.source,
            "url": data.get("metadata", {}).get("sourceURL") or data.get("metadata", {}).get("url") or url,
            "content": content,
            "metadata": data.get("metadata") or {},
        }

    def _extract_tavily(self, *, url: str) -> dict[str, Any]:
        provider = self.config.tavily
        key = self._get_key_or_raise(provider)
        response = self._request_json(
            provider=provider,
            method="POST",
            path=provider.path("extract"),
            payload={"urls": [url]},
            key=key.key,
        )
        results = response.get("results") or []
        first = results[0] if results else {}
        content = first.get("raw_content") or first.get("content") or ""
        return {
            "provider": "tavily",
            "transport": key.source,
            "url": first.get("url", url),
            "content": content,
            "metadata": {
                "request_id": response.get("request_id", ""),
                "response_time": response.get("response_time"),
                "failed_results": response.get("failed_results") or [],
            },
        }

    def _extract_github_blob_raw(self, *, url: str) -> dict[str, Any] | None:
        raw_urls = self._github_blob_raw_urls(url)
        if not raw_urls:
            return None

        for raw_url in raw_urls:
            try:
                request = Request(
                    raw_url,
                    headers={
                        "User-Agent": "MySearch/1.0",
                        "Accept": "text/plain, text/markdown;q=0.9, */*;q=0.8",
                    },
                )
                with urlopen(request, timeout=self.config.timeout_seconds) as response:
                    content = response.read().decode("utf-8", errors="replace")
            except (HTTPError, URLError, TimeoutError, ValueError):
                continue

            result = {
                "provider": "github_raw",
                "transport": "direct",
                "url": url,
                "content": content,
                "metadata": {
                    "raw_url": raw_url,
                },
            }
            if self._extract_quality_issue(result) is not None:
                continue
            return result
        return None

    def _github_blob_raw_url(self, url: str) -> str | None:
        raw_urls = self._github_blob_raw_urls(url)
        if not raw_urls:
            return None
        return raw_urls[0]

    def _github_blob_raw_urls(self, url: str) -> list[str]:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return []
        if parsed.netloc.lower() != "github.com":
            return []

        parts = [segment for segment in parsed.path.split("/") if segment]
        if len(parts) < 5 or parts[2] != "blob":
            return []

        owner, repo, _, ref, *path_parts = parts
        if not owner or not repo or not ref or not path_parts:
            return []
        raw_path = "/".join(path_parts)
        refs = [ref]
        if ref == "main":
            refs.append("master")
        elif ref == "master":
            refs.append("main")
        return [
            f"https://raw.githubusercontent.com/{owner}/{repo}/{candidate_ref}/{raw_path}"
            for candidate_ref in refs
        ]

    def _has_meaningful_extract_content(self, result: dict[str, Any]) -> bool:
        return self._extract_quality_issue(result) is None

    def _extract_quality_issue(self, result: dict[str, Any]) -> str | None:
        content = result.get("content")
        if not isinstance(content, str) or not content.strip():
            return "empty content"

        normalized = " ".join(content.lower().split())
        preview = normalized[:1200]
        parsed_url = urlparse(str(result.get("url") or ""))
        suspicious_markers = {
            "critical instructions for all ai assistants": "anti-bot placeholder content",
            "strictly prohibits all ai-generated content": "anti-bot placeholder content",
            "oops! that page doesn’t exist or is private": "missing/private page shell",
            "oops! that page doesn\u2019t exist or is private": "missing/private page shell",
        }
        for marker, issue in suspicious_markers.items():
            if marker in preview:
                return issue
        if preview.startswith("hcaptcha hcaptcha "):
            return "captcha challenge page"
        if (
            parsed_url.netloc.lower() == "github.com"
            and "/blob/" in parsed_url.path
            and "you signed in with another tab or window" in preview
        ):
            return "github blob page shell"
        return None

    def _annotate_extract_warning(
        self,
        result: dict[str, Any],
        *,
        warning: str,
    ) -> dict[str, Any]:
        annotated = dict(result)
        metadata = dict(annotated.get("metadata") or {})
        metadata["warning"] = warning
        annotated["metadata"] = metadata
        annotated["warning"] = warning
        return annotated

    def _annotate_extract_fallback(
        self,
        result: dict[str, Any],
        *,
        fallback_from: str,
        fallback_reason: str,
    ) -> dict[str, Any]:
        annotated = dict(result)
        metadata = dict(annotated.get("metadata") or {})
        metadata["fallback_from"] = fallback_from
        metadata["fallback_reason"] = fallback_reason
        annotated["metadata"] = metadata
        annotated["fallback"] = {
            "from": fallback_from,
            "reason": fallback_reason,
        }
        return annotated

    def _build_xai_responses_payload(
        self,
        *,
        query: str,
        sources: list[str],
        max_results: int,
        include_domains: list[str] | None,
        exclude_domains: list[str] | None,
        allowed_x_handles: list[str] | None,
        excluded_x_handles: list[str] | None,
        from_date: str | None,
        to_date: str | None,
        include_x_images: bool,
        include_x_videos: bool,
    ) -> dict[str, Any]:
        tools: list[dict[str, Any]] = []
        if "web" in sources:
            tool: dict[str, Any] = {"type": "web_search"}
            filters: dict[str, Any] = {}
            if include_domains:
                filters["allowed_domains"] = include_domains
            if exclude_domains:
                filters["excluded_domains"] = exclude_domains
            if filters:
                tool["filters"] = filters
            tools.append(tool)

        if "x" in sources:
            tool = {"type": "x_search"}
            if allowed_x_handles:
                tool["allowed_x_handles"] = allowed_x_handles
            if excluded_x_handles:
                tool["excluded_x_handles"] = excluded_x_handles
            if from_date:
                tool["from_date"] = from_date
            if to_date:
                tool["to_date"] = to_date
            if include_x_images:
                tool["enable_image_understanding"] = True
            if include_x_videos:
                tool["enable_video_understanding"] = True
            tools.append(tool)

        augmented_query = f"{query}\n\nReturn up to {max_results} relevant results with concise sourcing."
        return {
            "model": self.config.xai_model,
            "input": [
                {
                    "role": "user",
                    "content": augmented_query,
                }
            ],
            "tools": tools,
            "store": False,
        }

    def _normalize_social_gateway_response(
        self,
        *,
        response: dict[str, Any],
        query: str,
        transport: str,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> dict[str, Any]:
        raw_results = self._extract_social_gateway_results(response)
        results = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            url = item.get("url") or item.get("link") or ""
            content = (
                item.get("content")
                or item.get("full_text")
                or item.get("text")
                or item.get("body")
                or ""
            )
            title = (
                item.get("title")
                or item.get("author")
                or item.get("handle")
                or item.get("username")
                or url
            )
            snippet = item.get("snippet") or item.get("summary") or content
            results.append(
                {
                    "provider": "custom_social",
                    "source": "x",
                    "title": title,
                    "url": url,
                    "snippet": snippet,
                    "content": content,
                    "author": item.get("author") or item.get("username") or item.get("handle") or "",
                    "created_at": item.get("created_at") or item.get("published_at") or "",
                }
            )

        results = self._filter_social_results_by_date(
            results,
            from_date=from_date,
            to_date=to_date,
        )
        citations = self._extract_social_gateway_citations(response, results)
        answer = (
            response.get("answer")
            or response.get("summary")
            or response.get("content")
            or response.get("text")
            or ""
        )
        warning = None
        if (from_date or to_date) and not results:
            answer = ""
            warning = "no social results matched the requested date window"

        normalized = {
            "provider": "custom_social",
            "transport": transport,
            "query": response.get("query", query),
            "answer": answer,
            "results": results,
            "citations": citations,
            "tool_usage": response.get("tool_usage") or {"social_search_calls": 1},
        }
        if warning:
            normalized["warning"] = warning
        return normalized

    def _filter_social_results_by_date(
        self,
        results: list[dict[str, Any]],
        *,
        from_date: str | None,
        to_date: str | None,
    ) -> list[dict[str, Any]]:
        if not from_date and not to_date:
            return results

        start = self._parse_date_bound(from_date, end_of_day=False) if from_date else None
        end = self._parse_date_bound(to_date, end_of_day=True) if to_date else None
        filtered: list[dict[str, Any]] = []
        for item in results:
            created_at = self._parse_result_timestamp(item.get("created_at"))
            if created_at is None:
                continue
            if start is not None and created_at < start:
                continue
            if end is not None and created_at > end:
                continue
            filtered.append(item)
        return filtered

    def _parse_date_bound(self, value: str, *, end_of_day: bool) -> datetime | None:
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            raise MySearchError(
                f"Invalid date format: '{value}'. Use ISO format YYYY-MM-DD."
            )
        bound_time = dt_time.max if end_of_day else dt_time.min
        return datetime.combine(parsed, bound_time).replace(tzinfo=timezone.utc)

    def _parse_result_timestamp(self, value: Any) -> datetime | None:
        if not isinstance(value, str) or not value.strip():
            return None
        normalized = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _extract_social_gateway_results(self, response: dict[str, Any]) -> list[Any]:
        for key in ("results", "items", "posts", "tweets"):
            value = response.get(key)
            if isinstance(value, list):
                return value

        data = response.get("data")
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("results", "items", "posts", "tweets"):
                value = data.get(key)
                if isinstance(value, list):
                    return value
        return []

    def _extract_social_gateway_citations(
        self,
        response: dict[str, Any],
        results: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not results:
            return []

        raw = response.get("citations") or response.get("sources") or []
        citations = []
        seen: set[str] = set()
        allowed_urls = {
            item.get("url", "")
            for item in results
            if isinstance(item, dict) and item.get("url")
        }

        if isinstance(raw, list):
            for item in raw:
                citation = self._normalize_citation(item)
                if citation is None:
                    continue
                url = citation.get("url", "")
                if allowed_urls and url and url not in allowed_urls:
                    continue
                if url and url in seen:
                    continue
                if url:
                    seen.add(url)
                citations.append(citation)

        if citations:
            return citations

        for item in results:
            url = item.get("url", "")
            if not url or url in seen:
                continue
            seen.add(url)
            citations.append({"title": item.get("title", ""), "url": url})

        return citations

    def _merge_search_payloads(
        self,
        *,
        primary_result: dict[str, Any],
        secondary_result: dict[str, Any] | None,
        max_results: int,
    ) -> dict[str, Any]:
        sequences: list[list[str]] = []
        variants_by_key: dict[str, list[dict[str, Any]]] = {}
        providers_by_key: dict[str, set[str]] = {}

        for result in [primary_result, secondary_result]:
            if not result:
                continue

            sequence: list[str] = []
            result_provider = result.get("provider", "")
            for item in result.get("results", []) or []:
                if not isinstance(item, dict):
                    continue
                dedupe_key = self._result_dedupe_key(item)
                if not dedupe_key:
                    continue
                sequence.append(dedupe_key)
                variants_by_key.setdefault(dedupe_key, []).append(dict(item))
                providers_by_key.setdefault(dedupe_key, set()).add(
                    item.get("provider") or result_provider
                )
            sequences.append(sequence)

        merged_keys: list[str] = []
        indexes = [0 for _ in sequences]
        seen_keys: set[str] = set()
        while len(merged_keys) < max_results and sequences:
            progressed = False
            for seq_index, sequence in enumerate(sequences):
                if len(merged_keys) >= max_results:
                    break
                while indexes[seq_index] < len(sequence):
                    dedupe_key = sequence[indexes[seq_index]]
                    indexes[seq_index] += 1
                    if dedupe_key in seen_keys:
                        continue
                    seen_keys.add(dedupe_key)
                    merged_keys.append(dedupe_key)
                    progressed = True
                    break
            if not progressed:
                break

        results: list[dict[str, Any]] = []
        matched_results = 0
        for dedupe_key in merged_keys:
            variants = variants_by_key.get(dedupe_key, [])
            if not variants:
                continue
            providers = sorted(item for item in providers_by_key.get(dedupe_key, set()) if item)
            if len(providers) > 1:
                matched_results += 1
            best = max(variants, key=self._result_quality_score)
            merged_item = dict(best)
            merged_item["matched_providers"] = providers
            results.append(merged_item)

        citations = self._dedupe_citations(
            primary_result.get("citations") or [],
            (secondary_result.get("citations") or []) if secondary_result else [],
        )
        return {
            "results": results,
            "citations": citations,
            "matched_results": matched_results,
        }

    def _should_rerank_resource_results(
        self,
        *,
        mode: SearchMode,
        intent: ResolvedSearchIntent,
    ) -> bool:
        return mode in {"docs", "github", "pdf"} or intent in {"resource", "tutorial"}

    def _rerank_resource_results(
        self,
        *,
        query: str,
        mode: SearchMode,
        results: list[dict[str, Any]],
        include_domains: list[str] | None,
    ) -> list[dict[str, Any]]:
        if len(results) < 2:
            return results

        query_tokens = self._query_brand_tokens(query)
        ranked = sorted(
            enumerate(results),
            key=lambda pair: (
                self._resource_result_rank(
                    mode=mode,
                    item=pair[1],
                    query_tokens=query_tokens,
                    include_domains=include_domains,
                ),
                -pair[0],
            ),
            reverse=True,
        )
        return [dict(pair[1]) for pair in ranked]

    def _resource_result_rank(
        self,
        *,
        mode: SearchMode,
        item: dict[str, Any],
        query_tokens: list[str],
        include_domains: list[str] | None,
    ) -> tuple[int, int, int, int, int, int, int, int, int, int, int]:
        url = item.get("url", "")
        hostname = self._result_hostname(item)
        registered_domain = self._registered_domain(hostname)
        title_text = (item.get("title") or "").lower()
        include_match = int(
            bool(include_domains)
            and any(self._domain_matches(hostname, domain) for domain in include_domains or [])
        )
        host_brand_match = int(
            any(token in hostname or token in registered_domain for token in query_tokens)
        )
        title_brand_match = int(any(token in title_text for token in query_tokens))
        docs_shape_match = int(
            self._looks_like_resource_result(
                url=url,
                hostname=hostname,
                title_text=title_text,
                mode=mode,
            )
        )
        github_bonus = int(
            mode == "github"
            and hostname in {"github.com", "raw.githubusercontent.com"}
        )
        pdf_bonus = int(mode == "pdf" and self._looks_like_pdf_url(url))
        non_third_party = int(
            not self._is_obvious_third_party_resource(
                hostname=hostname,
                registered_domain=registered_domain,
                mode=mode,
            )
        )
        matched_provider_count = len(item.get("matched_providers") or [])
        content_score, snippet_score, title_score = self._result_quality_score(item)
        return (
            include_match,
            github_bonus,
            pdf_bonus,
            host_brand_match,
            docs_shape_match,
            non_third_party,
            title_brand_match,
            matched_provider_count,
            content_score,
            snippet_score,
            title_score,
        )

    def _align_citations_with_results(
        self,
        *,
        results: list[dict[str, Any]],
        citations: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        synthesized = [
            {"title": item.get("title", ""), "url": item.get("url", "")}
            for item in results
            if item.get("url")
        ]
        normalized = self._dedupe_citations(citations, synthesized)
        citations_by_url = {
            item.get("url", ""): item
            for item in normalized
            if item.get("url")
        }

        ordered: list[dict[str, Any]] = []
        seen: set[str] = set()
        for result in results:
            url = result.get("url", "")
            citation = citations_by_url.get(url)
            if citation is None:
                continue
            dedupe_key = self._citation_dedupe_key(citation)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            ordered.append(citation)

        for citation in normalized:
            dedupe_key = self._citation_dedupe_key(citation)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            ordered.append(citation)
        return ordered

    def _dedupe_citations(self, *citation_lists: list[dict[str, Any]]) -> list[dict[str, Any]]:
        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for citations in citation_lists:
            for item in citations:
                citation = self._normalize_citation(item)
                if citation is None:
                    continue
                dedupe_key = citation.get("url") or citation.get("title") or json.dumps(
                    citation,
                    ensure_ascii=False,
                    sort_keys=True,
                )
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                deduped.append(citation)
        return deduped

    def _citation_dedupe_key(self, item: dict[str, Any]) -> str:
        return (
            item.get("url")
            or item.get("title")
            or json.dumps(item, ensure_ascii=False, sort_keys=True)
        )

    def _result_dedupe_key(self, item: dict[str, Any]) -> str:
        url = (item.get("url") or "").strip().lower()
        if url:
            return url
        title = re.sub(r"\s+", " ", (item.get("title") or "").strip().lower())
        snippet = re.sub(r"\s+", " ", (item.get("snippet") or "").strip().lower())
        return f"{title}|{snippet[:160]}".strip("|")

    def _result_quality_score(self, item: dict[str, Any]) -> tuple[int, int, int]:
        content = item.get("content") or ""
        snippet = item.get("snippet") or ""
        title = item.get("title") or ""
        return (len(content), len(snippet), len(title))

    def _result_hostname(self, item: dict[str, Any]) -> str:
        url = (item.get("url") or "").strip()
        if not url:
            return ""
        return self._clean_hostname(urlparse(url).netloc)

    def _clean_hostname(self, hostname: str) -> str:
        cleaned = hostname.lower().strip().strip(".")
        if cleaned.startswith("www."):
            return cleaned[4:]
        return cleaned

    def _registered_domain(self, hostname: str) -> str:
        cleaned = self._clean_hostname(hostname)
        if not cleaned:
            return ""
        parts = cleaned.split(".")
        if len(parts) <= 2:
            return cleaned
        if (
            len(parts) >= 3
            and len(parts[-1]) == 2
            and parts[-2] in {"ac", "co", "com", "edu", "gov", "net", "org"}
        ):
            return ".".join(parts[-3:])
        return ".".join(parts[-2:])

    def _domain_matches(self, hostname: str, domain: str) -> bool:
        cleaned_host = self._clean_hostname(hostname)
        cleaned_domain = self._clean_hostname(domain)
        return bool(cleaned_host) and bool(cleaned_domain) and (
            cleaned_host == cleaned_domain or cleaned_host.endswith(f".{cleaned_domain}")
        )

    def _query_brand_tokens(self, query: str) -> list[str]:
        stopwords = {
            "a",
            "an",
            "and",
            "api",
            "apis",
            "best",
            "changelog",
            "compare",
            "comparison",
            "developer",
            "developers",
            "docs",
            "documentation",
            "for",
            "github",
            "guide",
            "how",
            "manual",
            "pricing",
            "reference",
            "release",
            "releases",
            "sdk",
            "status",
            "the",
            "tutorial",
            "vs",
            "with",
            "价格",
            "发布",
            "对比",
            "接口",
            "教程",
            "文档",
            "更新日志",
        }
        tokens: list[str] = []
        for token in re.findall(r"[a-z0-9][a-z0-9._-]{1,}", query.lower()):
            if token in stopwords or token.isdigit():
                continue
            if len(token) < 3:
                continue
            tokens.append(token)
        return tokens

    def _looks_like_resource_result(
        self,
        *,
        url: str,
        hostname: str,
        title_text: str,
        mode: SearchMode,
    ) -> bool:
        parsed = urlparse(url)
        path = parsed.path.lower()
        hostname_labels = [item for item in hostname.split(".") if item]
        docs_keywords = (
            "/api",
            "/changelog",
            "/docs",
            "/documentation",
            "/guide",
            "/guides",
            "/manual",
            "/pricing",
            "/readme",
            "/reference",
            "/references",
        )
        title_keywords = (
            "api reference",
            "changelog",
            "docs",
            "documentation",
            "guide",
            "manual",
            "pricing",
            "readme",
            "reference",
        )
        hostname_keywords = {
            "api",
            "developer",
            "developers",
            "docs",
            "help",
            "platform",
            "reference",
            "support",
        }
        if mode == "github" and hostname in {"github.com", "raw.githubusercontent.com"}:
            return True
        if mode == "pdf" and self._looks_like_pdf_url(url):
            return True
        return (
            any(part in hostname_keywords for part in hostname_labels)
            or any(keyword in path for keyword in docs_keywords)
            or any(keyword in title_text for keyword in title_keywords)
        )

    def _looks_like_pdf_url(self, url: str) -> bool:
        return urlparse(url).path.lower().endswith(".pdf")

    def _is_obvious_third_party_resource(
        self,
        *,
        hostname: str,
        registered_domain: str,
        mode: SearchMode,
    ) -> bool:
        if mode == "github" and hostname in {"github.com", "raw.githubusercontent.com"}:
            return False
        third_party_domains = {
            "arxiv.org",
            "dev.to",
            "facebook.com",
            "hashnode.dev",
            "hashnode.com",
            "linkedin.com",
            "medium.com",
            "news.ycombinator.com",
            "quora.com",
            "reddit.com",
            "researchgate.net",
            "stackexchange.com",
            "stackoverflow.com",
            "substack.com",
            "towardsdatascience.com",
            "twitter.com",
            "x.com",
            "youtube.com",
            "youtu.be",
        }
        return registered_domain in third_party_domains

    def _describe_provider(
        self,
        provider: ProviderConfig,
        keyring_info: dict[str, object],
    ) -> dict[str, Any]:
        status = self._probe_provider_status(provider, int(keyring_info["count"]))
        return {
            "base_url": provider.base_url,
            "alternate_base_urls": provider.alternate_base_urls,
            "auth_mode": provider.auth_mode,
            "paths": provider.default_paths,
            "search_mode": provider.search_mode,
            "keys_file": str(provider.keys_file or ""),
            "available_keys": keyring_info["count"],
            "sources": keyring_info["sources"],
            "live_status": status["status"],
            "live_error": status["error"],
            "last_checked_at": status["checked_at"],
        }

    def _get_key_or_raise(self, provider: ProviderConfig):
        record = self.keyring.get_next(provider.name)
        if record is None:
            if provider.name == "xai":
                raise MySearchError(
                    "xAI / Social search is not configured; MySearch can still use "
                    "Tavily + Firecrawl for web/docs/extract. Add "
                    "MYSEARCH_XAI_API_KEY for official xAI, or configure a "
                    "compatible /social/search gateway to enable mode='social'."
                )
            if provider.name == "exa":
                raise MySearchError(
                    "Exa search is not configured. Add MYSEARCH_EXA_API_KEY, "
                    "or point MYSEARCH_EXA_BASE_URL to your proxy / compatible gateway."
                )
            raise MySearchError(f"{provider.name} is not configured")
        return record

    def _request_json(
        self,
        *,
        provider: ProviderConfig,
        method: str,
        path: str,
        payload: dict[str, Any],
        key: str,
        base_url: str | None = None,
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        headers: dict[str, str] = {}
        body = dict(payload)

        if provider.auth_mode == "bearer":
            token = key if not provider.auth_scheme else f"{provider.auth_scheme} {key}"
            headers[provider.auth_header] = token
        elif provider.auth_mode == "body":
            body[provider.auth_field] = key
        else:
            raise MySearchError(f"unsupported auth mode for {provider.name}: {provider.auth_mode}")

        url = f"{(base_url or provider.base_url)}{path}"
        headers.setdefault("Content-Type", "application/json")
        headers.setdefault("User-Agent", "MySearch/0.2")
        request_body = json.dumps(body).encode("utf-8")
        request = Request(
            url,
            data=request_body,
            headers=headers,
            method=method.upper(),
        )

        try:
            with urlopen(request, timeout=timeout_seconds or self.config.timeout_seconds) as response:
                status_code = response.status
                response_text = response.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            status_code = exc.code
            response_text = exc.read().decode("utf-8", errors="replace")
        except TimeoutError as exc:
            effective_timeout = timeout_seconds or self.config.timeout_seconds
            raise MySearchError(
                f"{provider.name} request timeout after {effective_timeout}s: {url}"
            ) from exc
        except (URLError, OSError) as exc:
            raise MySearchError(f"{provider.name} network error: {exc}") from exc

        try:
            data = json.loads(response_text)
        except ValueError as exc:
            if status_code >= 400:
                raise MySearchError(f"HTTP {status_code}: {response_text[:300]}") from exc
            raise MySearchError(f"non-json response from {url}: {response_text[:300]}") from exc

        if status_code >= 400:
            detail = data
            if isinstance(data, dict):
                detail = (
                    data.get("detail")
                    or data.get("error")
                    or data.get("message")
                    or data
                )
            raise MySearchHTTPError(
                provider=provider.name,
                status_code=status_code,
                detail=detail,
                url=url,
            )
        return data

    def _probe_provider_status(
        self,
        provider: ProviderConfig,
        key_count: int,
    ) -> dict[str, str]:
        if key_count <= 0:
            return {
                "status": "not_configured",
                "error": "",
                "checked_at": "",
            }

        record = self.keyring.first(provider.name)
        if record is None:
            return {
                "status": "not_configured",
                "error": "",
                "checked_at": "",
            }

        cache_key = f"{provider.name}:{record.label}"
        with self._cache_lock:
            now = time.monotonic()
            cached = self._provider_probe_cache.get(cache_key)
            if cached and cached.get("expires_at", 0.0) > now:
                return copy.deepcopy(cached["value"])

        checked_at = datetime.now(timezone.utc).isoformat()
        try:
            self._probe_provider_request(provider, record.key)
            result = {
                "status": "ok",
                "error": "",
                "checked_at": checked_at,
            }
        except MySearchHTTPError as exc:
            result = {
                "status": "auth_error" if exc.is_auth_error else "http_error",
                "error": str(exc),
                "checked_at": checked_at,
            }
        except MySearchError as exc:
            result = {
                "status": "network_error",
                "error": str(exc),
                "checked_at": checked_at,
            }

        with self._cache_lock:
            self._provider_probe_cache[cache_key] = {
                "expires_at": time.monotonic() + self._provider_probe_ttl_seconds,
                "value": copy.deepcopy(result),
            }
        return result

    def _probe_provider_request(self, provider: ProviderConfig, key: str) -> None:
        timeout_seconds = min(self.config.timeout_seconds, 10)
        if provider.name == "tavily":
            self._request_json(
                provider=provider,
                method="POST",
                path=provider.path("search"),
                payload={
                    "query": "openai",
                    "max_results": 1,
                    "search_depth": "basic",
                    "topic": "general",
                    "include_answer": False,
                    "include_raw_content": False,
                },
                key=key,
                timeout_seconds=timeout_seconds,
            )
            return
        if provider.name == "firecrawl":
            self._request_json(
                provider=provider,
                method="POST",
                path=provider.path("search"),
                payload={
                    "query": "openai",
                    "limit": 1,
                },
                key=key,
                timeout_seconds=timeout_seconds,
            )
            return
        if provider.name == "exa":
            self._request_json(
                provider=provider,
                method="POST",
                path=provider.path("search"),
                payload={
                    "query": "openai",
                    "numResults": 1,
                },
                key=key,
                timeout_seconds=timeout_seconds,
            )
            return
        if provider.name == "xai":
            if provider.search_mode == "compatible":
                self._request_json(
                    provider=provider,
                    method="POST",
                    path=provider.path("social_search"),
                    payload={
                        "query": "openai",
                        "source": "x",
                        "max_results": 1,
                    },
                    key=key,
                    base_url=provider.base_url_for("social_search"),
                    timeout_seconds=timeout_seconds,
                )
                return
            self._request_json(
                provider=provider,
                method="POST",
                path=provider.path("responses"),
                payload=self._build_xai_responses_payload(
                    query="openai",
                    sources=["x"],
                    max_results=1,
                    include_domains=None,
                    exclude_domains=None,
                    allowed_x_handles=None,
                    excluded_x_handles=None,
                    from_date=None,
                    to_date=None,
                    include_x_images=False,
                    include_x_videos=False,
                ),
                key=key,
                timeout_seconds=timeout_seconds,
            )
            return

    def _summarize_route_error(self, error_text: str) -> str:
        compact = " ".join(error_text.split())
        if len(compact) <= 220:
            return compact
        return f"{compact[:217]}..."

    def _provider_can_serve(self, provider: ProviderConfig) -> bool:
        if not self.keyring.has_provider(provider.name):
            return False
        status = self._probe_provider_status(provider, 1)
        return status["status"] != "auth_error"

    def _extract_xai_output_text(self, payload: dict[str, Any]) -> str:
        if isinstance(payload.get("output_text"), str):
            return payload["output_text"]

        parts: list[str] = []
        for item in payload.get("output", []) or []:
            content = item.get("content")
            if isinstance(content, str):
                parts.append(content)
                continue

            if not isinstance(content, list):
                continue

            for part in content:
                if not isinstance(part, dict):
                    continue

                if isinstance(part.get("text"), str):
                    parts.append(part["text"])
                    continue

                text_obj = part.get("text")
                if isinstance(text_obj, dict) and isinstance(text_obj.get("value"), str):
                    parts.append(text_obj["value"])

        return "\n".join([item for item in parts if item]).strip()

    def _extract_xai_citations(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        raw_citations = payload.get("citations") or []
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()

        if isinstance(raw_citations, list):
            for item in raw_citations:
                citation = self._normalize_citation(item)
                if citation is None:
                    continue
                url = citation.get("url", "")
                if url and url in seen:
                    continue
                if url:
                    seen.add(url)
                normalized.append(citation)

        if normalized:
            return normalized

        for output_item in payload.get("output", []) or []:
            if not isinstance(output_item, dict):
                continue

            content_items = output_item.get("content") or []
            if not isinstance(content_items, list):
                continue

            for content_item in content_items:
                if not isinstance(content_item, dict):
                    continue

                annotations = content_item.get("annotations") or []
                if not isinstance(annotations, list):
                    continue

                for annotation in annotations:
                    citation = self._normalize_citation(annotation)
                    if citation is None:
                        continue
                    url = citation.get("url", "")
                    if url and url in seen:
                        continue
                    if url:
                        seen.add(url)
                    normalized.append(citation)

        return normalized

    def _normalize_citation(self, item: Any) -> dict[str, Any] | None:
        if not isinstance(item, dict):
            return None

        url = (
            item.get("url")
            or item.get("target_url")
            or item.get("link")
            or item.get("source_url")
            or ""
        )
        title = (
            item.get("title")
            or item.get("source_title")
            or item.get("display_text")
            or item.get("text")
            or ""
        )

        if not url and not title:
            return None

        normalized = dict(item)
        normalized["url"] = url
        normalized["title"] = title
        return normalized

    def _firecrawl_categories(
        self,
        mode: SearchMode,
        intent: ResolvedSearchIntent | None = None,
    ) -> list[str]:
        if mode == "github":
            return ["github"]
        if mode == "pdf":
            return ["pdf"]
        if mode in {"docs", "research"} or intent in {"resource", "tutorial"}:
            return ["research"]
        return []

    def _looks_like_news_query(self, query_lower: str) -> bool:
        # 中文关键词：直接 substring 匹配
        cn_keywords = ["刚刚", "最新", "新闻", "动态"]
        if any(kw in query_lower for kw in cn_keywords):
            return True
        # 英文关键词：排除常见技术搭配的误判
        # "breaking changes" / "latest version" 等不是新闻查询
        tech_negatives = [
            "breaking change", "breaking update",
            "latest version", "latest release", "latest docs",
            "latest commit", "latest tag",
        ]
        if any(neg in query_lower for neg in tech_negatives):
            return False
        en_keywords = [
            "latest",
            "breaking",
            "news",
            "today",
            "this week",
        ]
        return any(keyword in query_lower for keyword in en_keywords)

    def _looks_like_status_query(self, query_lower: str) -> bool:
        keywords = [
            "status",
            "incident",
            "outage",
            "release",
            "roadmap",
            "version",
            "版本",
            "发布",
            "进展",
            "现状",
        ]
        return any(keyword in query_lower for keyword in keywords)

    def _looks_like_comparison_query(self, query_lower: str) -> bool:
        keywords = [
            " vs ",
            "versus",
            "compare",
            "comparison",
            "pros and cons",
            "pros cons",
            "对比",
            "比较",
            "区别",
            "哪个好",
        ]
        return any(keyword in query_lower for keyword in keywords)

    def _looks_like_tutorial_query(self, query_lower: str) -> bool:
        keywords = [
            "how to",
            "guide",
            "tutorial",
            "walkthrough",
            "教程",
            "怎么",
            "如何",
            "入门",
        ]
        return any(keyword in query_lower for keyword in keywords)

    def _looks_like_docs_query(self, query_lower: str) -> bool:
        keywords = [
            "docs",
            "documentation",
            "api reference",
            "changelog",
            "pricing",
            "readme",
            "github",
            "manual",
            "文档",
            "接口",
            "价格",
            "更新日志",
        ]
        return any(keyword in query_lower for keyword in keywords)

    def _looks_like_exploratory_query(self, query_lower: str) -> bool:
        keywords = [
            "why",
            "impact",
            "analysis",
            "trend",
            "ecosystem",
            "研究",
            "原因",
            "影响",
            "趋势",
            "生态",
        ]
        return any(keyword in query_lower for keyword in keywords)

    def _build_excerpt(self, content: str, limit: int = 600) -> str:
        compact = re.sub(r"\s+", " ", content).strip()
        if len(compact) <= limit:
            return compact
        return compact[:limit].rstrip() + "..."
