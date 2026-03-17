"""MySearch provider client 和自动路由。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal

import requests

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
ProviderName = Literal["auto", "tavily", "firecrawl", "xai"]


class MySearchError(RuntimeError):
    """MySearch 调用失败。"""


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
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "MySearch/0.2"})

    def health(self) -> dict[str, Any]:
        keyring_info = self.keyring.describe()
        return {
            "server_name": self.config.server_name,
            "timeout_seconds": self.config.timeout_seconds,
            "xai_model": self.config.xai_model,
            "providers": {
                "tavily": self._describe_provider(self.config.tavily, keyring_info["tavily"]),
                "firecrawl": self._describe_provider(
                    self.config.firecrawl,
                    keyring_info["firecrawl"],
                ),
                "xai": self._describe_provider(self.config.xai, keyring_info["xai"]),
            },
        }

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
        normalized_sources = sorted(set(sources or ["web"]))
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

        if decision.provider == "hybrid":
            web_result = self.search(
                query=query,
                mode=mode,
                intent=resolved_intent,
                strategy=resolved_strategy,
                provider="auto",
                sources=["web"],
                max_results=max_results,
                include_content=include_content,
                include_answer=include_answer,
                include_domains=include_domains,
                exclude_domains=exclude_domains,
            )
            social_result = self._search_xai(
                query=query,
                sources=["x"],
                max_results=max_results,
                allowed_x_handles=allowed_x_handles,
                excluded_x_handles=excluded_x_handles,
                from_date=from_date,
                to_date=to_date,
                include_x_images=include_x_images,
                include_x_videos=include_x_videos,
            )
            web_route = web_result.get("route", {}).get("selected", web_result.get("provider", "tavily"))
            social_route = social_result.get("provider", "xai")
            return {
                "provider": "hybrid",
                "intent": resolved_intent,
                "strategy": resolved_strategy,
                "route": {
                    "selected": f"{web_route}+{social_route}",
                    "reason": decision.reason,
                },
                "query": query,
                "web": web_result,
                "social": social_result,
            }

        if self._should_blend_web_providers(
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
                include_answer=include_answer,
                include_domains=include_domains,
                exclude_domains=exclude_domains,
            )
        elif decision.provider == "tavily":
            result = self._search_tavily(
                query=query,
                max_results=max_results,
                topic=decision.tavily_topic,
                include_answer=include_answer,
                include_content=include_content,
                include_domains=include_domains,
                exclude_domains=exclude_domains,
            )
        elif decision.provider == "firecrawl":
            result = self._search_firecrawl(
                query=query,
                max_results=max_results,
                categories=decision.firecrawl_categories or [],
                include_content=include_content or mode in {"docs", "research", "github", "pdf"},
            )
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

        route_selected = result.pop("route_selected", result.get("provider", decision.provider))
        result["intent"] = resolved_intent
        result["strategy"] = resolved_strategy
        result["route"] = {
            "selected": route_selected,
            "reason": route_reason,
        }
        return result

    def extract_url(
        self,
        *,
        url: str,
        formats: list[str] | None = None,
        only_main_content: bool = True,
        provider: Literal["auto", "firecrawl", "tavily"] = "auto",
    ) -> dict[str, Any]:
        formats = formats or ["markdown"]
        errors: list[str] = []
        firecrawl_warning = ""

        if provider in {"auto", "firecrawl"}:
            try:
                firecrawl_result = self._scrape_firecrawl(
                    url=url,
                    formats=formats,
                    only_main_content=only_main_content,
                )
                if self._has_meaningful_extract_content(firecrawl_result):
                    return firecrawl_result

                firecrawl_warning = "firecrawl scrape returned empty content"
                errors.append(firecrawl_warning)

                if provider == "firecrawl":
                    return self._annotate_extract_warning(
                        firecrawl_result,
                        warning=firecrawl_warning,
                    )
            except MySearchError as exc:
                errors.append(f"firecrawl scrape failed: {exc}")
                if provider == "firecrawl":
                    raise

        if provider in {"auto", "tavily"}:
            try:
                tavily_result = self._extract_tavily(url=url)
                if provider == "auto" and errors:
                    return self._annotate_extract_fallback(
                        tavily_result,
                        fallback_from="firecrawl",
                        fallback_reason=" | ".join(errors),
                    )
                return tavily_result
            except MySearchError as exc:
                errors.append(f"tavily extract failed: {exc}")
                if provider == "tavily":
                    raise

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
        web_mode = "news" if mode == "news" else ("docs" if mode in {"docs", "github", "pdf"} else "web")
        web_search = self.search(
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

        urls: list[str] = []
        if web_search.get("provider") == "hybrid":
            candidate_results = web_search.get("web", {}).get("results", [])
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
        for url in urls:
            try:
                page = self.extract_url(url=url, formats=["markdown"], only_main_content=True)
                page["excerpt"] = self._build_excerpt(page.get("content", ""))
                pages.append(page)
            except MySearchError as exc:
                pages.append({"url": url, "error": str(exc)})

        social: dict[str, Any] | None = None
        social_error = ""
        if include_social:
            try:
                social = self.search(
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
            except MySearchError as exc:
                social_error = str(exc)

        web_provider = web_search.get("provider", "")
        social_provider = social.get("provider", "") if social else ""
        providers_consulted = [item for item in [web_provider, social_provider] if item]
        citations = self._dedupe_citations(
            web_search.get("citations") or [],
            social.get("citations") or [] if social else [],
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
                    tavily_topic="news" if mode == "news" else "general",
                )
            if provider == "firecrawl":
                return RouteDecision(
                    provider="firecrawl",
                    reason="显式指定 Firecrawl",
                    firecrawl_categories=self._firecrawl_categories(mode, intent),
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
            return RouteDecision(
                provider="firecrawl",
                reason="文档 / GitHub / PDF 内容优先走 Firecrawl",
                firecrawl_categories=self._firecrawl_categories(mode, intent),
            )

        if include_content:
            return RouteDecision(
                provider="firecrawl",
                reason="请求里需要正文内容，优先用 Firecrawl search + scrape",
                firecrawl_categories=self._firecrawl_categories(mode, intent),
            )

        if intent in {"news", "status"} or mode == "news" or self._looks_like_news_query(query_lower):
            return RouteDecision(
                provider="tavily",
                reason="状态 / 新闻类查询默认走 Tavily",
                tavily_topic="news",
            )

        if intent == "resource" or self._looks_like_docs_query(query_lower):
            return RouteDecision(
                provider="firecrawl",
                reason="resource / docs 类查询优先走 Firecrawl",
                firecrawl_categories=self._firecrawl_categories("docs", intent),
            )

        if mode == "research":
            return RouteDecision(
                provider="tavily",
                reason="research 模式先用 Tavily 做发现，再按策略决定是否扩展验证",
                tavily_topic="general",
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
        decision: RouteDecision,
        sources: list[str],
        strategy: SearchStrategy,
    ) -> bool:
        if decision.provider not in {"tavily", "firecrawl"}:
            return False
        if strategy not in {"balanced", "verify", "deep"}:
            return False
        if "x" in sources:
            return False
        return self.keyring.has_provider("tavily") and self.keyring.has_provider("firecrawl")

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
            primary_result = self._search_tavily(
                query=query,
                max_results=max_results,
                topic=decision.tavily_topic,
                include_answer=include_answer,
                include_content=include_content,
                include_domains=include_domains,
                exclude_domains=exclude_domains,
            )
            secondary_call = lambda: self._search_firecrawl(
                query=query,
                max_results=max_results,
                categories=self._firecrawl_categories(mode, intent),
                include_content=include_content or strategy == "deep",
            )
        else:
            primary_result = self._search_firecrawl(
                query=query,
                max_results=max_results,
                categories=decision.firecrawl_categories or self._firecrawl_categories(mode, intent),
                include_content=include_content or strategy == "deep",
            )
            secondary_call = lambda: self._search_tavily(
                query=query,
                max_results=max_results,
                topic="news" if intent in {"news", "status"} else "general",
                include_answer=include_answer,
                include_content=False,
                include_domains=include_domains,
                exclude_domains=exclude_domains,
            )

        secondary_result: dict[str, Any] | None = None
        secondary_error = ""
        try:
            secondary_result = secondary_call()
        except MySearchError as exc:
            secondary_error = str(exc)

        merged = self._merge_search_payloads(
            primary_result=primary_result,
            secondary_result=secondary_result,
            max_results=max_results,
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

    def _has_meaningful_extract_content(self, result: dict[str, Any]) -> bool:
        content = result.get("content")
        return isinstance(content, str) and bool(content.strip())

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

        citations = self._extract_social_gateway_citations(response, results)
        answer = (
            response.get("answer")
            or response.get("summary")
            or response.get("content")
            or response.get("text")
            or ""
        )

        return {
            "provider": "custom_social",
            "transport": transport,
            "query": response.get("query", query),
            "answer": answer,
            "results": results,
            "citations": citations,
            "tool_usage": response.get("tool_usage") or {"social_search_calls": 1},
        }

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
        raw = response.get("citations") or response.get("sources") or []
        citations = []
        seen: set[str] = set()

        if isinstance(raw, list):
            for item in raw:
                citation = self._normalize_citation(item)
                if citation is None:
                    continue
                url = citation.get("url", "")
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
            secondary_result.get("citations") or [] if secondary_result else [],
        )
        return {
            "results": results,
            "citations": citations,
            "matched_results": matched_results,
        }

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

    def _describe_provider(
        self,
        provider: ProviderConfig,
        keyring_info: dict[str, object],
    ) -> dict[str, Any]:
        return {
            "base_url": provider.base_url,
            "alternate_base_urls": provider.alternate_base_urls,
            "auth_mode": provider.auth_mode,
            "paths": provider.default_paths,
            "search_mode": provider.search_mode,
            "keys_file": str(provider.keys_file or ""),
            "available_keys": keyring_info["count"],
            "sources": keyring_info["sources"],
        }

    def _get_key_or_raise(self, provider: ProviderConfig):
        record = self.keyring.get_next(provider.name)
        if record is None:
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
        try:
            response = self.session.request(
                method,
                url,
                json=body,
                headers=headers,
                timeout=self.config.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise MySearchError(str(exc)) from exc

        try:
            data = response.json()
        except ValueError as exc:
            if response.status_code >= 400:
                raise MySearchError(f"HTTP {response.status_code}: {response.text[:300]}") from exc
            raise MySearchError(f"non-json response from {url}: {response.text[:300]}") from exc

        if response.status_code >= 400:
            detail = data.get("detail") if isinstance(data, dict) else data
            raise MySearchError(f"HTTP {response.status_code}: {detail}")
        return data

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
        keywords = [
            "latest",
            "breaking",
            "news",
            "today",
            "this week",
            "刚刚",
            "最新",
            "新闻",
            "动态",
        ]
        return any(keyword in query_lower for keyword in keywords)

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
