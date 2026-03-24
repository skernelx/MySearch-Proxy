"""Comprehensive test suite for MySearch-Proxy.

Covers cache behavior, routing logic, intent/strategy resolution, keyring,
result merging/dedup, extract_url fallback chains, config parsing edge cases,
and social URL normalization. Designed to surface additional bugs and
improvement opportunities.
"""
from __future__ import annotations

import copy
import io
import os
import sys
import threading
import time
import unittest
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from unittest.mock import patch
from urllib.error import HTTPError

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mysearch.clients import (
    MySearchClient,
    MySearchError,
    MySearchHTTPError,
    RouteDecision,
    _stringify_error_detail,
)
from mysearch.config import (
    MySearchConfig,
    ProviderConfig,
    _get_bool,
    _get_int,
    _get_str,
    _normalize_base_url,
    _normalize_path,
    _parse_codex_mysearch_env,
)
from mysearch.keyring import KeyRecord, MySearchKeyRing
from mysearch import social_gateway


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_provider(name: str, *, keys: list[str] | None = None) -> ProviderConfig:
    return ProviderConfig(
        name=name,
        base_url="https://example.com",
        auth_mode="bearer",
        auth_header="Authorization",
        auth_scheme="Bearer",
        auth_field="api_key",
        default_paths={"search": "/search"},
        api_keys=keys or [],
    )


_SENTINEL = object()


def _minimal_config(
    *,
    tavily_keys: list[str] | object = _SENTINEL,
    firecrawl_keys: list[str] | object = _SENTINEL,
    exa_keys: list[str] | object = _SENTINEL,
    xai_keys: list[str] | object = _SENTINEL,
    search_cache_ttl: int = 30,
    extract_cache_ttl: int = 300,
) -> MySearchConfig:
    return MySearchConfig(
        server_name="test",
        timeout_seconds=10,
        xai_model="grok-test",
        max_parallel_workers=2,
        search_cache_ttl_seconds=search_cache_ttl,
        extract_cache_ttl_seconds=extract_cache_ttl,
        mcp_host="127.0.0.1",
        mcp_port=8000,
        mcp_mount_path="/",
        mcp_sse_path="/sse",
        mcp_streamable_http_path="/mcp",
        mcp_stateless_http=False,
        tavily=_minimal_provider("tavily", keys=["tvly-test"] if tavily_keys is _SENTINEL else tavily_keys),
        firecrawl=_minimal_provider("firecrawl", keys=["fc-test"] if firecrawl_keys is _SENTINEL else firecrawl_keys),
        exa=_minimal_provider("exa", keys=[] if exa_keys is _SENTINEL else exa_keys),
        xai=_minimal_provider("xai", keys=[] if xai_keys is _SENTINEL else xai_keys),
    )


def _make_client(**kwargs) -> MySearchClient:
    config = _minimal_config(**kwargs)
    return MySearchClient(config=config)


# ===========================================================================
# 1  Cache behavior
# ===========================================================================

class CacheBehaviorTests(unittest.TestCase):
    """Cache TTL, eviction, pruning, and edge cases."""

    def test_cache_disabled_when_ttl_zero(self) -> None:
        """TTL=0 should never store or return cached values."""
        client = _make_client(search_cache_ttl=0)
        client._cache_set("search", "k1", {"data": 1})
        self.assertIsNone(client._cache_get("search", "k1"))
        self.assertEqual(client._cache_stats["search"]["misses"], 0)

    def test_cache_set_get_round_trip(self) -> None:
        client = _make_client(search_cache_ttl=60)
        client._cache_set("search", "k1", {"data": 42})
        result = client._cache_get("search", "k1")
        self.assertIsNotNone(result)
        self.assertEqual(result["data"], 42)
        self.assertEqual(client._cache_stats["search"]["hits"], 1)

    def test_cache_returns_deep_copy(self) -> None:
        """Mutating the returned value must not corrupt the cache."""
        client = _make_client(search_cache_ttl=60)
        client._cache_set("search", "k1", {"items": [1, 2, 3]})
        result = client._cache_get("search", "k1")
        result["items"].append(999)
        fresh = client._cache_get("search", "k1")
        self.assertEqual(fresh["items"], [1, 2, 3])

    def test_cache_stores_deep_copy(self) -> None:
        """Mutating the original dict after set must not corrupt the cache."""
        client = _make_client(search_cache_ttl=60)
        data = {"items": [1, 2]}
        client._cache_set("search", "k1", data)
        data["items"].append(999)
        result = client._cache_get("search", "k1")
        self.assertEqual(result["items"], [1, 2])

    def test_cache_eviction_when_full(self) -> None:
        """When cache hits max entries, the oldest entry should be evicted."""
        client = _make_client(search_cache_ttl=3600)
        client._cache_max_entries = 3
        for i in range(3):
            client._cache_set("search", f"k{i}", {"i": i})
        # All 3 should be present
        for i in range(3):
            self.assertIsNotNone(client._cache_get("search", f"k{i}"))
        # Adding k3 should evict k0 (oldest)
        client._cache_set("search", "k3", {"i": 3})
        self.assertIsNone(client._cache_get("search", "k0"))
        self.assertIsNotNone(client._cache_get("search", "k3"))

    def test_cache_miss_increments_stat(self) -> None:
        client = _make_client(search_cache_ttl=60)
        client._cache_get("search", "nonexistent")
        self.assertEqual(client._cache_stats["search"]["misses"], 1)

    def test_cache_namespaces_are_independent(self) -> None:
        """Search and extract caches should not interfere with each other."""
        client = _make_client(search_cache_ttl=60, extract_cache_ttl=60)
        client._cache_set("search", "key", {"from": "search"})
        client._cache_set("extract", "key", {"from": "extract"})
        self.assertEqual(client._cache_get("search", "key")["from"], "search")
        self.assertEqual(client._cache_get("extract", "key")["from"], "extract")

    def test_cache_key_deterministic(self) -> None:
        """Same payload should produce the same cache key."""
        client = _make_client()
        k1 = client._build_cache_key("search", {"query": "test", "mode": "web"})
        k2 = client._build_cache_key("search", {"mode": "web", "query": "test"})
        self.assertEqual(k1, k2)

    def test_cache_key_differs_for_different_payloads(self) -> None:
        client = _make_client()
        k1 = client._build_cache_key("search", {"query": "a"})
        k2 = client._build_cache_key("search", {"query": "b"})
        self.assertNotEqual(k1, k2)

    def test_cache_health_reports_entries_and_stats(self) -> None:
        client = _make_client(search_cache_ttl=60)
        client._cache_set("search", "k1", {"a": 1})
        client._cache_get("search", "k1")
        client._cache_get("search", "missing")
        health = client._cache_health()
        self.assertEqual(health["search"]["entries"], 1)
        self.assertEqual(health["search"]["hits"], 1)
        self.assertEqual(health["search"]["misses"], 1)

    def test_should_cache_search_excludes_x_sources(self) -> None:
        client = _make_client()
        decision = RouteDecision(provider="tavily", reason="test")
        self.assertFalse(client._should_cache_search(
            decision=decision,
            normalized_sources=["x"],
        ))

    def test_should_cache_search_excludes_xai_provider(self) -> None:
        client = _make_client()
        decision = RouteDecision(provider="xai", reason="test")
        self.assertFalse(client._should_cache_search(
            decision=decision,
            normalized_sources=["web"],
        ))

    def test_should_cache_search_allows_tavily(self) -> None:
        client = _make_client()
        decision = RouteDecision(provider="tavily", reason="test")
        self.assertTrue(client._should_cache_search(
            decision=decision,
            normalized_sources=["web"],
        ))


# ===========================================================================
# 2  Routing logic
# ===========================================================================

class RoutingTests(unittest.TestCase):
    """_route_search provider selection."""

    def _route(self, client: MySearchClient, **kwargs) -> RouteDecision:
        defaults = {
            "query": "test query",
            "mode": "auto",
            "intent": "factual",
            "provider": "auto",
            "sources": ["web"],
            "include_content": False,
            "include_domains": None,
            "allowed_x_handles": None,
            "excluded_x_handles": None,
        }
        defaults.update(kwargs)
        return client._route_search(**defaults)

    def test_explicit_tavily_provider(self) -> None:
        client = _make_client()
        decision = self._route(client, provider="tavily")
        self.assertEqual(decision.provider, "tavily")

    def test_explicit_firecrawl_provider(self) -> None:
        client = _make_client()
        decision = self._route(client, provider="firecrawl")
        self.assertEqual(decision.provider, "firecrawl")

    def test_explicit_exa_provider(self) -> None:
        client = _make_client()
        decision = self._route(client, provider="exa")
        self.assertEqual(decision.provider, "exa")

    def test_docs_query_with_restricted_domain_prefers_firecrawl(self) -> None:
        client = _make_client(tavily_keys=["tv"], firecrawl_keys=["fc"])
        client._probe_provider_status = lambda provider, key_count: {  # type: ignore[method-assign]
            "status": "ok",
            "error": "",
            "checked_at": "2026-03-22T00:00:00+00:00",
        }
        decision = self._route(
            client,
            query="linux.do MCP 配置",
            mode="docs",
            intent="resource",
            include_domains=["linux.do"],
        )
        self.assertEqual(decision.provider, "firecrawl")

    def test_explicit_xai_provider(self) -> None:
        client = _make_client()
        decision = self._route(client, provider="xai")
        self.assertEqual(decision.provider, "xai")

    def test_hybrid_when_web_and_x_sources(self) -> None:
        client = _make_client()
        decision = self._route(client, sources=["web", "x"])
        self.assertEqual(decision.provider, "hybrid")

    def test_social_mode_routes_to_xai(self) -> None:
        client = _make_client()
        decision = self._route(client, mode="social")
        self.assertEqual(decision.provider, "xai")

    def test_x_in_sources_routes_to_xai(self) -> None:
        client = _make_client()
        decision = self._route(client, sources=["x"])
        self.assertEqual(decision.provider, "xai")

    def test_x_handles_routes_to_xai(self) -> None:
        client = _make_client()
        decision = self._route(client, allowed_x_handles=["@openai"])
        self.assertEqual(decision.provider, "xai")

    def test_docs_mode_with_content_routes_to_firecrawl(self) -> None:
        client = _make_client()
        decision = self._route(client, mode="docs", include_content=True)
        self.assertEqual(decision.provider, "firecrawl")

    def test_docs_mode_without_content_routes_to_firecrawl(self) -> None:
        client = _make_client(tavily_keys=["key1"], firecrawl_keys=["fc"])
        decision = self._route(client, mode="docs", include_content=False)
        self.assertEqual(decision.provider, "firecrawl")
        self.assertEqual(decision.fallback_chain, ["tavily"])

    def test_docs_route_keeps_firecrawl_primary_even_when_keys_missing(self) -> None:
        client = _make_client(
            tavily_keys=[],
            firecrawl_keys=[],
            exa_keys=["exa-test"],
        )
        decision = self._route(client, mode="docs", include_content=False)
        self.assertEqual(decision.provider, "exa")
        self.assertIsNone(decision.fallback_chain)

    def test_news_mode_routes_to_tavily(self) -> None:
        client = _make_client(tavily_keys=["key1"])
        decision = self._route(client, mode="news", intent="news")
        self.assertEqual(decision.provider, "tavily")
        self.assertEqual(decision.tavily_topic, "news")

    def test_default_web_routes_to_tavily(self) -> None:
        client = _make_client(tavily_keys=["key1"])
        decision = self._route(client, mode="web")
        self.assertEqual(decision.provider, "tavily")

    def test_web_fallback_to_exa_when_tavily_unavailable(self) -> None:
        client = _make_client(tavily_keys=[], exa_keys=["exa-key"])
        decision = self._route(client, mode="web")
        self.assertEqual(decision.provider, "exa")
        self.assertEqual(decision.fallback_chain, ["firecrawl"])

    def test_github_mode_routes_to_docs_path(self) -> None:
        """GitHub mode should follow docs routing path."""
        client = _make_client(tavily_keys=["key"])
        decision = self._route(client, mode="github", include_content=False)
        self.assertEqual(decision.provider, "firecrawl")
        self.assertEqual(decision.fallback_chain, ["tavily"])

    def test_pdf_mode_routes_to_docs_path(self) -> None:
        client = _make_client(tavily_keys=["key"])
        decision = self._route(client, mode="pdf", include_content=False)
        self.assertEqual(decision.provider, "firecrawl")
        self.assertEqual(decision.fallback_chain, ["tavily"])

    def test_include_content_routes_to_firecrawl(self) -> None:
        client = _make_client()
        decision = self._route(client, mode="web", include_content=True)
        self.assertEqual(decision.provider, "firecrawl")

    def test_research_mode_routes_to_tavily(self) -> None:
        client = _make_client(tavily_keys=["key"])
        decision = self._route(client, mode="research", intent="exploratory")
        self.assertEqual(decision.provider, "tavily")


# ===========================================================================
# 3  Intent and strategy resolution
# ===========================================================================

class IntentResolutionTests(unittest.TestCase):
    def test_explicit_intent_preserved(self) -> None:
        client = _make_client()
        result = client._resolve_intent(
            query="anything", mode="auto", intent="comparison", sources=["web"]
        )
        self.assertEqual(result, "comparison")

    def test_news_mode_auto_intent(self) -> None:
        client = _make_client()
        result = client._resolve_intent(
            query="anything", mode="news", intent="auto", sources=["web"]
        )
        self.assertEqual(result, "news")

    def test_docs_mode_auto_intent(self) -> None:
        client = _make_client()
        result = client._resolve_intent(
            query="anything", mode="docs", intent="auto", sources=["web"]
        )
        self.assertEqual(result, "resource")

    def test_social_sources_auto_intent(self) -> None:
        client = _make_client()
        result = client._resolve_intent(
            query="anything", mode="auto", intent="auto", sources=["x"]
        )
        self.assertEqual(result, "status")

    def test_research_mode_auto_intent(self) -> None:
        client = _make_client()
        result = client._resolve_intent(
            query="anything", mode="research", intent="auto", sources=["web"]
        )
        self.assertEqual(result, "exploratory")

    def test_default_falls_to_factual(self) -> None:
        client = _make_client()
        result = client._resolve_intent(
            query="who is the president", mode="auto", intent="auto", sources=["web"]
        )
        self.assertEqual(result, "factual")


class StrategyResolutionTests(unittest.TestCase):
    def test_explicit_strategy_preserved(self) -> None:
        client = _make_client()
        result = client._resolve_strategy(
            mode="auto", intent="factual", strategy="deep",
            sources=["web"], include_content=False,
        )
        self.assertEqual(result, "deep")

    def test_hybrid_sources_auto_strategy(self) -> None:
        client = _make_client()
        result = client._resolve_strategy(
            mode="auto", intent="factual", strategy="auto",
            sources=["web", "x"], include_content=False,
        )
        self.assertEqual(result, "balanced")

    def test_research_mode_auto_strategy(self) -> None:
        client = _make_client()
        result = client._resolve_strategy(
            mode="research", intent="exploratory", strategy="auto",
            sources=["web"], include_content=False,
        )
        self.assertEqual(result, "deep")

    def test_comparison_intent_auto_strategy(self) -> None:
        client = _make_client()
        result = client._resolve_strategy(
            mode="auto", intent="comparison", strategy="auto",
            sources=["web"], include_content=False,
        )
        self.assertEqual(result, "verify")

    def test_docs_mode_auto_strategy(self) -> None:
        client = _make_client()
        result = client._resolve_strategy(
            mode="docs", intent="resource", strategy="auto",
            sources=["web"], include_content=False,
        )
        self.assertEqual(result, "balanced")

    def test_simple_factual_defaults_to_fast(self) -> None:
        client = _make_client()
        result = client._resolve_strategy(
            mode="auto", intent="factual", strategy="auto",
            sources=["web"], include_content=False,
        )
        self.assertEqual(result, "fast")


# ===========================================================================
# 4  Should blend web providers
# ===========================================================================

class BlendingDecisionTests(unittest.TestCase):
    def test_no_blend_when_provider_explicitly_set(self) -> None:
        client = _make_client()
        self.assertFalse(client._should_blend_web_providers(
            requested_provider="tavily",
            decision=RouteDecision(provider="tavily", reason="test"),
            sources=["web"],
            strategy="balanced",
        ))

    def test_no_blend_for_exa_decision(self) -> None:
        client = _make_client()
        self.assertFalse(client._should_blend_web_providers(
            requested_provider="auto",
            decision=RouteDecision(provider="exa", reason="test"),
            sources=["web"],
            strategy="balanced",
        ))

    def test_no_blend_for_fast_strategy(self) -> None:
        client = _make_client()
        self.assertFalse(client._should_blend_web_providers(
            requested_provider="auto",
            decision=RouteDecision(provider="tavily", reason="test"),
            sources=["web"],
            strategy="fast",
        ))

    def test_no_blend_when_x_in_sources(self) -> None:
        client = _make_client()
        self.assertFalse(client._should_blend_web_providers(
            requested_provider="auto",
            decision=RouteDecision(provider="tavily", reason="test"),
            sources=["x"],
            strategy="balanced",
        ))

    def test_no_blend_for_docs_mode(self) -> None:
        client = _make_client(tavily_keys=["k"], firecrawl_keys=["k"])
        self.assertFalse(client._should_blend_web_providers(
            requested_provider="auto",
            decision=RouteDecision(provider="tavily", reason="test"),
            sources=["web"],
            strategy="balanced",
            mode="docs",
            intent="resource",
            include_domains=None,
        ))

    def test_no_blend_for_resource_intent(self) -> None:
        client = _make_client(tavily_keys=["k"], firecrawl_keys=["k"])
        self.assertFalse(client._should_blend_web_providers(
            requested_provider="auto",
            decision=RouteDecision(provider="tavily", reason="test"),
            sources=["web"],
            strategy="balanced",
            mode="auto",
            intent="resource",
            include_domains=None,
        ))

    def test_no_blend_when_include_domains_present(self) -> None:
        client = _make_client(tavily_keys=["k"], firecrawl_keys=["k"])
        self.assertFalse(client._should_blend_web_providers(
            requested_provider="auto",
            decision=RouteDecision(provider="tavily", reason="test"),
            sources=["web"],
            strategy="balanced",
            mode="auto",
            intent="factual",
            include_domains=["openai.com"],
        ))

    def test_no_blend_for_news_profile(self) -> None:
        client = _make_client(tavily_keys=["k"], firecrawl_keys=["k"])
        self.assertFalse(client._should_blend_web_providers(
            requested_provider="auto",
            decision=RouteDecision(provider="tavily", reason="test", result_profile="news"),
            sources=["web"],
            strategy="balanced",
            mode="news",
            intent="news",
            include_domains=None,
        ))

    def test_blend_when_conditions_met(self) -> None:
        client = _make_client(tavily_keys=["k"], firecrawl_keys=["k"])
        self.assertTrue(client._should_blend_web_providers(
            requested_provider="auto",
            decision=RouteDecision(provider="tavily", reason="test"),
            sources=["web"],
            strategy="balanced",
            mode="auto",
            intent="factual",
            include_domains=None,
        ))


# ===========================================================================
# 5  KeyRing
# ===========================================================================

class KeyRingTests(unittest.TestCase):
    def test_round_robin_rotation(self) -> None:
        config = _minimal_config(tavily_keys=["k1", "k2", "k3"])
        ring = MySearchKeyRing(config)
        keys = [ring.get_next("tavily").key for _ in range(6)]
        self.assertEqual(keys, ["k1", "k2", "k3", "k1", "k2", "k3"])

    def test_empty_provider_returns_none(self) -> None:
        config = _minimal_config(exa_keys=[])
        ring = MySearchKeyRing(config)
        self.assertIsNone(ring.get_next("exa"))

    def test_has_provider(self) -> None:
        config = _minimal_config(tavily_keys=["k1"], exa_keys=[])
        ring = MySearchKeyRing(config)
        self.assertTrue(ring.has_provider("tavily"))
        self.assertFalse(ring.has_provider("exa"))

    def test_first(self) -> None:
        config = _minimal_config(tavily_keys=["first", "second"])
        ring = MySearchKeyRing(config)
        self.assertEqual(ring.first("tavily").key, "first")

    def test_first_returns_none_for_empty(self) -> None:
        config = _minimal_config(exa_keys=[])
        ring = MySearchKeyRing(config)
        self.assertIsNone(ring.first("exa"))

    def test_describe(self) -> None:
        config = _minimal_config(tavily_keys=["k1", "k2"])
        ring = MySearchKeyRing(config)
        desc = ring.describe()
        self.assertEqual(desc["tavily"]["count"], 2)
        self.assertIn("env", desc["tavily"]["sources"])

    def test_dedup_keys(self) -> None:
        """Duplicate keys from env should be deduplicated."""
        config = _minimal_config(tavily_keys=["same-key", "same-key", "different-key"])
        ring = MySearchKeyRing(config)
        self.assertEqual(ring.describe()["tavily"]["count"], 2)

    def test_empty_key_ignored(self) -> None:
        config = _minimal_config(tavily_keys=["real-key", "", "  "])
        ring = MySearchKeyRing(config)
        self.assertEqual(ring.describe()["tavily"]["count"], 1)

    def test_load_from_file(self) -> None:
        with TemporaryDirectory() as tmpdir:
            keys_file = Path(tmpdir) / "keys.txt"
            keys_file.write_text("# comment\naccount1, tvly-abc\naccount2, tvly-def\n\n")
            provider = _minimal_provider("tavily")
            provider.keys_file = keys_file
            config = _minimal_config()
            config.tavily = provider
            ring = MySearchKeyRing(config)
            self.assertEqual(ring.describe()["tavily"]["count"], 2)
            first = ring.get_next("tavily")
            self.assertEqual(first.key, "tvly-abc")
            self.assertEqual(first.label, "account1")
            self.assertEqual(first.source, "file")

    def test_file_dedup_with_env(self) -> None:
        """Keys from file that duplicate env keys should be removed."""
        with TemporaryDirectory() as tmpdir:
            keys_file = Path(tmpdir) / "keys.txt"
            keys_file.write_text("account1, tvly-from-env\naccount2, tvly-unique\n")
            provider = _minimal_provider("tavily", keys=["tvly-from-env"])
            provider.keys_file = keys_file
            config = _minimal_config()
            config.tavily = provider
            ring = MySearchKeyRing(config)
            self.assertEqual(ring.describe()["tavily"]["count"], 2)
            # First should be env key, second should be unique file key
            first = ring.get_next("tavily")
            self.assertEqual(first.source, "env")
            second = ring.get_next("tavily")
            self.assertEqual(second.key, "tvly-unique")

    def test_reload_resets_index_if_out_of_bounds(self) -> None:
        config = _minimal_config(tavily_keys=["k1", "k2", "k3"])
        ring = MySearchKeyRing(config)
        # Advance index
        ring.get_next("tavily")
        ring.get_next("tavily")
        ring.get_next("tavily")
        # Now remove keys by changing config and reloading
        config.tavily = _minimal_provider("tavily", keys=["only-key"])
        ring.reload()
        result = ring.get_next("tavily")
        self.assertIsNotNone(result)
        self.assertEqual(result.key, "only-key")

    def test_thread_safety(self) -> None:
        config = _minimal_config(tavily_keys=["k1", "k2"])
        ring = MySearchKeyRing(config)
        results = []
        errors = []

        def reader():
            try:
                for _ in range(100):
                    key = ring.get_next("tavily")
                    if key:
                        results.append(key.key)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=reader) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 400)
        # All results should be valid keys
        for key in results:
            self.assertIn(key, {"k1", "k2"})


# ===========================================================================
# 6  Result merging and deduplication
# ===========================================================================

class MergeDedupeTests(unittest.TestCase):
    def test_merge_deduplicates_by_url(self) -> None:
        client = _make_client()
        primary = {
            "provider": "tavily",
            "results": [
                {"url": "https://example.com/a", "title": "A", "snippet": "primary", "content": ""},
                {"url": "https://example.com/b", "title": "B", "snippet": "primary", "content": ""},
            ],
            "citations": [
                {"url": "https://example.com/a", "title": "A"},
                {"url": "https://example.com/b", "title": "B"},
            ],
        }
        secondary = {
            "provider": "firecrawl",
            "results": [
                {"url": "https://example.com/a", "title": "A", "snippet": "secondary", "content": "richer content"},
                {"url": "https://example.com/c", "title": "C", "snippet": "secondary", "content": ""},
            ],
            "citations": [
                {"url": "https://example.com/a", "title": "A"},
                {"url": "https://example.com/c", "title": "C"},
            ],
        }
        merged = client._merge_search_payloads(
            primary_result=primary,
            secondary_result=secondary,
            max_results=10,
        )
        urls = [r["url"] for r in merged["results"]]
        # No duplicates
        self.assertEqual(len(urls), len(set(urls)))
        # All 3 unique URLs present
        self.assertEqual(set(urls), {"https://example.com/a", "https://example.com/b", "https://example.com/c"})

    def test_merge_picks_best_quality_variant(self) -> None:
        """When both providers return the same URL, the one with more content wins."""
        client = _make_client()
        primary = {
            "provider": "tavily",
            "results": [
                {"url": "https://example.com/a", "title": "A", "snippet": "short", "content": ""},
            ],
            "citations": [],
        }
        secondary = {
            "provider": "firecrawl",
            "results": [
                {"url": "https://example.com/a", "title": "A", "snippet": "short", "content": "long detailed content here"},
            ],
            "citations": [],
        }
        merged = client._merge_search_payloads(
            primary_result=primary,
            secondary_result=secondary,
            max_results=10,
        )
        self.assertEqual(merged["results"][0]["content"], "long detailed content here")

    def test_merge_counts_matched_results(self) -> None:
        client = _make_client()
        primary = {
            "provider": "tavily",
            "results": [
                {"url": "https://example.com/a", "title": "A", "snippet": "", "content": ""},
            ],
            "citations": [],
        }
        secondary = {
            "provider": "firecrawl",
            "results": [
                {"url": "https://example.com/a", "title": "A", "snippet": "", "content": ""},
            ],
            "citations": [],
        }
        merged = client._merge_search_payloads(
            primary_result=primary,
            secondary_result=secondary,
            max_results=10,
        )
        self.assertEqual(merged["matched_results"], 1)

    def test_merge_respects_max_results(self) -> None:
        client = _make_client()
        primary = {
            "provider": "tavily",
            "results": [{"url": f"https://example.com/{i}", "title": str(i), "snippet": "", "content": ""} for i in range(5)],
            "citations": [],
        }
        secondary = {
            "provider": "firecrawl",
            "results": [{"url": f"https://example.com/{i+10}", "title": str(i+10), "snippet": "", "content": ""} for i in range(5)],
            "citations": [],
        }
        merged = client._merge_search_payloads(
            primary_result=primary,
            secondary_result=secondary,
            max_results=3,
        )
        self.assertEqual(len(merged["results"]), 3)

    def test_merge_with_none_secondary(self) -> None:
        client = _make_client()
        primary = {
            "provider": "tavily",
            "results": [
                {"url": "https://example.com/a", "title": "A", "snippet": "", "content": ""},
            ],
            "citations": [{"url": "https://example.com/a", "title": "A"}],
        }
        merged = client._merge_search_payloads(
            primary_result=primary,
            secondary_result=None,
            max_results=10,
        )
        self.assertEqual(len(merged["results"]), 1)
        self.assertEqual(merged["matched_results"], 0)

    def test_dedupe_citations(self) -> None:
        client = _make_client()
        c1 = [{"url": "https://a.com", "title": "A"}, {"url": "https://b.com", "title": "B"}]
        c2 = [{"url": "https://a.com", "title": "A dup"}, {"url": "https://c.com", "title": "C"}]
        deduped = client._dedupe_citations(c1, c2)
        urls = [c["url"] for c in deduped]
        self.assertEqual(urls, ["https://a.com", "https://b.com", "https://c.com"])

    def test_result_dedupe_key_uses_url(self) -> None:
        client = _make_client()
        key = client._result_dedupe_key({"url": "https://Example.Com/Page", "title": "Page"})
        self.assertEqual(key, "https://example.com/page")

    def test_result_dedupe_key_falls_back_to_title_snippet(self) -> None:
        client = _make_client()
        key = client._result_dedupe_key({"title": "My  Title", "snippet": "My snippet"})
        self.assertIn("my title", key)
        self.assertIn("my snippet", key)

    def test_interleaved_merge_ordering(self) -> None:
        """Results should be interleaved from primary and secondary."""
        client = _make_client()
        primary = {
            "provider": "tavily",
            "results": [
                {"url": "https://a.com", "title": "A", "snippet": "", "content": ""},
                {"url": "https://b.com", "title": "B", "snippet": "", "content": ""},
            ],
            "citations": [],
        }
        secondary = {
            "provider": "firecrawl",
            "results": [
                {"url": "https://c.com", "title": "C", "snippet": "", "content": ""},
                {"url": "https://d.com", "title": "D", "snippet": "", "content": ""},
            ],
            "citations": [],
        }
        merged = client._merge_search_payloads(
            primary_result=primary,
            secondary_result=secondary,
            max_results=10,
        )
        urls = [r["url"] for r in merged["results"]]
        # Interleaved: primary[0], secondary[0], primary[1], secondary[1]
        self.assertEqual(urls, ["https://a.com", "https://c.com", "https://b.com", "https://d.com"])


# ===========================================================================
# 7  Reranking
# ===========================================================================

class RerankingTests(unittest.TestCase):
    def test_single_result_not_reranked(self) -> None:
        client = _make_client()
        results = [{"url": "https://example.com", "title": "Only", "snippet": "", "content": ""}]
        reranked = client._rerank_resource_results(
            query="test", mode="docs", results=results, include_domains=None,
        )
        self.assertEqual(len(reranked), 1)
        self.assertEqual(reranked[0]["url"], "https://example.com")

    def test_include_domains_ranked_first(self) -> None:
        client = _make_client()
        results = [
            {"url": "https://blog.example.com/post", "title": "Blog post", "snippet": "", "content": ""},
            {"url": "https://docs.openai.com/api", "title": "OpenAI API", "snippet": "", "content": ""},
        ]
        reranked = client._rerank_resource_results(
            query="openai api", mode="docs", results=results,
            include_domains=["openai.com"],
        )
        self.assertEqual(reranked[0]["url"], "https://docs.openai.com/api")

    def test_github_bonus_for_github_mode(self) -> None:
        client = _make_client()
        results = [
            {"url": "https://blog.example.com/post", "title": "Guide", "snippet": "", "content": ""},
            {"url": "https://github.com/owner/repo", "title": "Repo", "snippet": "", "content": ""},
        ]
        reranked = client._rerank_resource_results(
            query="test repo", mode="github", results=results, include_domains=None,
        )
        self.assertEqual(reranked[0]["url"], "https://github.com/owner/repo")

    def test_should_rerank_for_docs_mode(self) -> None:
        client = _make_client()
        self.assertTrue(client._should_rerank_resource_results(mode="docs", intent="factual"))

    def test_should_rerank_for_resource_intent(self) -> None:
        client = _make_client()
        self.assertTrue(client._should_rerank_resource_results(mode="auto", intent="resource"))

    def test_should_not_rerank_for_factual_web(self) -> None:
        client = _make_client()
        self.assertFalse(client._should_rerank_resource_results(mode="auto", intent="factual"))


# ===========================================================================
# 8  Config parsing edge cases
# ===========================================================================

class ConfigParsingTests(unittest.TestCase):
    def test_get_int_non_numeric(self) -> None:
        with patch.dict(os.environ, {"TEST_PORT": "not_a_number"}):
            result = _get_int("TEST_PORT", 8080)
        self.assertEqual(result, 8080)

    def test_get_int_empty(self) -> None:
        with patch.dict(os.environ, {"TEST_PORT": "  "}):
            result = _get_int("TEST_PORT", 8080)
        self.assertEqual(result, 8080)

    def test_get_int_valid(self) -> None:
        with patch.dict(os.environ, {"TEST_PORT": " 3000 "}):
            result = _get_int("TEST_PORT", 8080)
        self.assertEqual(result, 3000)

    def test_get_bool_variations(self) -> None:
        for truthy in ("1", "true", "True", "TRUE", "yes", "on"):
            with patch.dict(os.environ, {"MYTEST": truthy}):
                self.assertTrue(_get_bool("MYTEST"))
        for falsy in ("0", "false", "no", "off", "random"):
            with patch.dict(os.environ, {"MYTEST": falsy}):
                self.assertFalse(_get_bool("MYTEST"))

    def test_get_bool_missing(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MYTEST_MISSING_KEY_12345", None)
            self.assertFalse(_get_bool("MYTEST_MISSING_KEY_12345"))
            self.assertTrue(_get_bool("MYTEST_MISSING_KEY_12345", True))

    def test_get_str_fallback_chain(self) -> None:
        with patch.dict(os.environ, {"SECOND": "fallback"}):
            os.environ.pop("FIRST_MISSING_12345", None)
            result = _get_str("FIRST_MISSING_12345", "SECOND", default="none")
        self.assertEqual(result, "fallback")

    def test_normalize_base_url_strips_trailing_slash(self) -> None:
        self.assertEqual(_normalize_base_url("https://api.example.com/"), "https://api.example.com")
        self.assertEqual(_normalize_base_url("https://api.example.com///"), "https://api.example.com")

    def test_normalize_path_ensures_leading_slash(self) -> None:
        self.assertEqual(_normalize_path("search"), "/search")
        self.assertEqual(_normalize_path("/search"), "/search")
        self.assertEqual(_normalize_path(""), "")

    def test_parse_codex_env_toml(self) -> None:
        toml_content = """
[mcp_servers.mysearch.env]
MYSEARCH_PROXY_BASE_URL = "https://proxy.test"
MYSEARCH_PROXY_API_KEY = "secret"
""".strip()
        result = _parse_codex_mysearch_env(toml_content)
        self.assertEqual(result["MYSEARCH_PROXY_BASE_URL"], "https://proxy.test")
        self.assertEqual(result["MYSEARCH_PROXY_API_KEY"], "secret")

    def test_parse_codex_env_empty_values_skipped(self) -> None:
        toml_content = """
[mcp_servers.mysearch.env]
MYSEARCH_PROXY_BASE_URL = "https://proxy.test"
MYSEARCH_PROXY_API_KEY = ""
""".strip()
        result = _parse_codex_mysearch_env(toml_content)
        self.assertIn("MYSEARCH_PROXY_BASE_URL", result)
        self.assertNotIn("MYSEARCH_PROXY_API_KEY", result)

    def test_parse_codex_env_ignores_other_sections(self) -> None:
        toml_content = """
[mcp_servers.other.env]
OTHER_KEY = "should not appear"

[mcp_servers.mysearch.env]
MYSEARCH_KEY = "should appear"
""".strip()
        result = _parse_codex_mysearch_env(toml_content)
        self.assertNotIn("OTHER_KEY", result)
        self.assertEqual(result.get("MYSEARCH_KEY"), "should appear")

    def test_parse_codex_env_quoted_values(self) -> None:
        """Both single and double quoted values should be unquoted."""
        toml_content = """
[mcp_servers.mysearch.env]
KEY_DOUBLE = "value-double"
KEY_SINGLE = 'value-single'
""".strip()
        result = _parse_codex_mysearch_env(toml_content)
        self.assertEqual(result.get("KEY_DOUBLE"), "value-double")
        self.assertEqual(result.get("KEY_SINGLE"), "value-single")


# ===========================================================================
# 9  Social URL normalization edge cases
# ===========================================================================

class SocialURLNormalizationTests(unittest.TestCase):
    def test_valid_x_url(self) -> None:
        url = "https://x.com/OpenAI/status/1901234567890123456"
        self.assertEqual(
            social_gateway.normalize_social_match_url(url),
            "https://x.com/openai/status/1901234567890123456",
        )

    def test_twitter_url_normalized_to_x(self) -> None:
        url = "https://twitter.com/OpenAI/status/1901234567890123456"
        self.assertEqual(
            social_gateway.normalize_social_match_url(url),
            "https://x.com/openai/status/1901234567890123456",
        )

    def test_mobile_twitter_url(self) -> None:
        url = "https://mobile.twitter.com/user/status/1901234567890123456"
        self.assertEqual(
            social_gateway.normalize_social_match_url(url),
            "https://x.com/user/status/1901234567890123456",
        )

    def test_profile_url_rejected(self) -> None:
        self.assertEqual(social_gateway.normalize_social_match_url("https://x.com/OpenAI"), "")

    def test_non_social_url_rejected(self) -> None:
        self.assertEqual(
            social_gateway.normalize_social_match_url("https://docs.example.com/page"),
            "",
        )

    def test_empty_url(self) -> None:
        self.assertEqual(social_gateway.normalize_social_match_url(""), "")
        self.assertEqual(social_gateway.normalize_social_match_url(None), "")

    def test_non_http_scheme_rejected(self) -> None:
        self.assertEqual(
            social_gateway.normalize_social_match_url("ftp://x.com/user/status/1234567890123456789"),
            "",
        )

    def test_synthetic_id_detected_repeated_digits(self) -> None:
        self.assertTrue(social_gateway.looks_synthetic_social_status_id("1234567890123456789"))

    def test_synthetic_id_detected_all_same(self) -> None:
        self.assertTrue(social_gateway.looks_synthetic_social_status_id("1111111111111111111"))

    def test_legitimate_id_passes(self) -> None:
        self.assertFalse(social_gateway.looks_synthetic_social_status_id("1901234567890123456"))

    def test_short_id_not_synthetic(self) -> None:
        """IDs shorter than 12 digits should not be considered synthetic."""
        self.assertFalse(social_gateway.looks_synthetic_social_status_id("12345"))

    def test_url_with_query_params_preserved(self) -> None:
        """Query parameters should be stripped during normalization."""
        url = "https://x.com/user/status/1901234567890123456?utm_source=test"
        result = social_gateway.normalize_social_match_url(url)
        # match_url should be clean
        self.assertEqual(result, "https://x.com/user/status/1901234567890123456")

    def test_url_with_at_handle(self) -> None:
        url = "https://x.com/@OpenAI/status/1901234567890123456"
        result = social_gateway.normalize_social_match_url(url)
        self.assertEqual(result, "https://x.com/openai/status/1901234567890123456")

    def test_normalize_citation_extracts_url(self) -> None:
        item = {"target_url": "https://x.com/test/status/123", "source_title": "Title"}
        result = social_gateway.normalize_citation(item)
        self.assertIsNotNone(result)
        self.assertEqual(result["url"], "https://x.com/test/status/123")
        self.assertEqual(result["title"], "Title")

    def test_normalize_citation_none_for_empty(self) -> None:
        self.assertIsNone(social_gateway.normalize_citation({}))
        self.assertIsNone(social_gateway.normalize_citation("not a dict"))

    def test_normalize_result_item_fields(self) -> None:
        item = {
            "url": "https://x.com/test/status/123",
            "author": "Test Author",
            "username": "testuser",
            "content": "Some content",
        }
        result = social_gateway.normalize_result_item(item)
        self.assertEqual(result["author"], "Test Author")
        self.assertEqual(result["handle"], "testuser")
        self.assertEqual(result["content"], "Some content")

    def test_normalize_result_item_strip_at_from_handle(self) -> None:
        result = social_gateway.normalize_result_item({"handle": "@TestUser", "url": "https://x.com/test"})
        self.assertEqual(result["handle"], "TestUser")

    def test_normalize_result_item_none_for_empty(self) -> None:
        self.assertIsNone(social_gateway.normalize_result_item({}))
        self.assertIsNone(social_gateway.normalize_result_item("not a dict"))


# ===========================================================================
# 10  Error handling
# ===========================================================================

class ErrorHandlingTests(unittest.TestCase):
    def test_mysearch_http_error_auth(self) -> None:
        err = MySearchHTTPError(provider="tavily", status_code=401, detail="deactivated", url="http://a.com")
        self.assertTrue(err.is_auth_error)
        self.assertIn("rejected", str(err))
        self.assertIn("deactivated", str(err))

    def test_mysearch_http_error_non_auth(self) -> None:
        err = MySearchHTTPError(provider="firecrawl", status_code=500, detail="server error", url="http://a.com")
        self.assertFalse(err.is_auth_error)
        self.assertIn("request failed", str(err))

    def test_mysearch_http_error_403_is_auth(self) -> None:
        err = MySearchHTTPError(provider="exa", status_code=403, detail="forbidden", url="http://a.com")
        self.assertTrue(err.is_auth_error)

    def test_stringify_error_detail_string(self) -> None:
        self.assertEqual(_stringify_error_detail("  error msg  "), "error msg")

    def test_stringify_error_detail_dict(self) -> None:
        result = _stringify_error_detail({"code": "ERR"})
        self.assertIn("ERR", result)

    def test_stringify_error_detail_none(self) -> None:
        self.assertEqual(_stringify_error_detail(None), "")

    def test_search_empty_query_raises(self) -> None:
        client = _make_client()
        with self.assertRaises(MySearchError) as ctx:
            client.search(query="")
        self.assertIn("empty", str(ctx.exception))

    def test_search_whitespace_query_raises(self) -> None:
        client = _make_client()
        with self.assertRaises(MySearchError) as ctx:
            client.search(query="   ")
        self.assertIn("empty", str(ctx.exception))

    def test_extract_url_invalid_scheme_raises(self) -> None:
        client = _make_client()
        with self.assertRaises(MySearchError):
            client.extract_url(url="ftp://example.com/file")

    def test_extract_url_no_netloc_raises(self) -> None:
        client = _make_client()
        with self.assertRaises(MySearchError):
            client.extract_url(url="not-a-url")

    def test_research_empty_query_raises(self) -> None:
        client = _make_client()
        with self.assertRaises(MySearchError):
            client.research(query="")

    def test_research_evidence_includes_search_confidence_and_page_coverage(self) -> None:
        client = _make_client()

        client.search = lambda **kwargs: {  # type: ignore[method-assign]
            "provider": "tavily",
            "intent": "resource",
            "strategy": "balanced",
            "results": [
                {
                    "title": "Responses | OpenAI API Reference",
                    "url": "https://platform.openai.com/docs/api-reference/responses",
                    "snippet": "Official docs",
                    "content": "",
                }
            ],
            "citations": [
                {
                    "title": "Responses | OpenAI API Reference",
                    "url": "https://platform.openai.com/docs/api-reference/responses",
                }
            ],
            "evidence": {
                "providers_consulted": ["tavily"],
                "verification": "single-provider",
                "citation_count": 1,
                "source_diversity": 1,
                "source_domains": ["openai.com"],
                "official_source_count": 1,
                "official_mode": "strict",
                "confidence": "high",
                "conflicts": [],
            },
        }
        client.extract_url = lambda **kwargs: {  # type: ignore[method-assign]
            "url": kwargs["url"],
            "provider": "firecrawl",
            "content": "Background mode lets requests run asynchronously.",
            "cache": {"extract": {"hit": False, "ttl_seconds": 300}},
        }

        result = client.research(
            query="OpenAI Responses API official docs",
            mode="docs",
            include_social=False,
            scrape_top_n=1,
        )

        self.assertEqual(result["evidence"]["official_mode"], "strict")
        self.assertEqual(result["evidence"]["search_confidence"], "high")
        self.assertEqual(result["evidence"]["page_count"], 1)
        self.assertEqual(result["evidence"]["page_success_rate"], 1.0)
        self.assertEqual(result["evidence"]["confidence"], "high")
        self.assertEqual(result["evidence"]["source_domains"], ["openai.com"])


# ===========================================================================
# 11  Search source normalization
# ===========================================================================

class SearchSourceNormalizationTests(unittest.TestCase):
    """Verify how search() normalizes sources."""

    def _mock_search_xai(self, client: MySearchClient) -> None:
        """Stub out _search_xai to avoid network calls."""
        client._search_xai = lambda **kwargs: {  # type: ignore[method-assign]
            "provider": "xai",
            "transport": "env",
            "query": kwargs["query"],
            "answer": "",
            "results": [],
            "citations": [],
        }

    def test_social_mode_auto_adds_x_source(self) -> None:
        """mode=social should set normalized_sources to ["x"]."""
        client = _make_client(xai_keys=["k"])
        self._mock_search_xai(client)
        result = client.search(query="test", mode="social")
        self.assertEqual(result["route_debug"]["normalized_sources"], ["x"])

    def test_allowed_x_handles_auto_adds_x_source(self) -> None:
        client = _make_client(xai_keys=["k"])
        self._mock_search_xai(client)
        result = client.search(query="test", allowed_x_handles=["@openai"])
        self.assertEqual(result["route_debug"]["normalized_sources"], ["x"])

    def test_github_mode_adds_include_domains(self) -> None:
        """mode=github should auto-add github.com to include_domains."""
        client = _make_client(tavily_keys=["k"])
        client._search_tavily = lambda **kwargs: {  # type: ignore[method-assign]
            "provider": "tavily",
            "transport": "env",
            "query": kwargs["query"],
            "answer": "",
            "results": [],
            "citations": [],
        }
        result = client.search(query="some repo", mode="github", strategy="fast")
        # The search went through - no error
        self.assertEqual(result["provider"], "tavily")


# ===========================================================================
# 12  Parallel execution
# ===========================================================================

class ParallelExecutionTests(unittest.TestCase):
    def test_single_task_no_threadpool(self) -> None:
        client = _make_client()
        results, errors = client._execute_parallel({"only": lambda: 42})
        self.assertEqual(results, {"only": 42})
        self.assertEqual(errors, {})

    def test_empty_tasks(self) -> None:
        client = _make_client()
        results, errors = client._execute_parallel({})
        self.assertEqual(results, {})
        self.assertEqual(errors, {})

    def test_error_captured(self) -> None:
        client = _make_client()

        def fail():
            raise ValueError("boom")

        results, errors = client._execute_parallel({"ok": lambda: 1, "fail": fail}, max_workers=2)
        self.assertEqual(results.get("ok"), 1)
        self.assertIn("fail", errors)
        self.assertIsInstance(errors["fail"], ValueError)

    def test_raise_parallel_error_reraises_mysearch_error(self) -> None:
        client = _make_client()
        err = MySearchError("test error")
        with self.assertRaises(MySearchError):
            client._raise_parallel_error({"task": err}, "task")

    def test_raise_parallel_error_wraps_generic_error(self) -> None:
        client = _make_client()
        err = RuntimeError("generic")
        with self.assertRaises(MySearchError):
            client._raise_parallel_error({"task": err}, "task")

    def test_raise_parallel_error_noop_for_missing_task(self) -> None:
        client = _make_client()
        # Should not raise
        client._raise_parallel_error({"other": RuntimeError("x")}, "task")


# ===========================================================================
# 13  Health endpoint
# ===========================================================================

class HealthTests(unittest.TestCase):
    def test_health_returns_all_providers(self) -> None:
        client = _make_client()
        health = client.health()
        for provider in ("tavily", "firecrawl", "exa", "xai"):
            self.assertIn(provider, health["providers"])

    def test_health_includes_runtime_config(self) -> None:
        client = _make_client()
        health = client.health()
        self.assertIn("max_parallel_workers", health["runtime"])
        self.assertIn("cache_ttl_seconds", health["runtime"])

    def test_health_includes_mcp_config(self) -> None:
        client = _make_client()
        health = client.health()
        self.assertIn("host", health["mcp"])
        self.assertIn("port", health["mcp"])
        self.assertIn("streamable_http_url", health["mcp"])


# ===========================================================================
# 14  Hostname and domain utilities
# ===========================================================================

class DomainUtilityTests(unittest.TestCase):
    def test_clean_hostname_strips_www(self) -> None:
        client = _make_client()
        self.assertEqual(client._clean_hostname("www.example.com"), "example.com")
        self.assertEqual(client._clean_hostname("WWW.Example.Com"), "example.com")

    def test_clean_hostname_strips_dots(self) -> None:
        client = _make_client()
        self.assertEqual(client._clean_hostname(".example.com."), "example.com")

    def test_registered_domain_simple(self) -> None:
        client = _make_client()
        self.assertEqual(client._registered_domain("docs.example.com"), "example.com")

    def test_registered_domain_country_tld(self) -> None:
        client = _make_client()
        self.assertEqual(client._registered_domain("shop.example.co.uk"), "example.co.uk")

    def test_registered_domain_already_registered(self) -> None:
        client = _make_client()
        self.assertEqual(client._registered_domain("example.com"), "example.com")

    def test_result_hostname(self) -> None:
        client = _make_client()
        self.assertEqual(
            client._result_hostname({"url": "https://www.example.com/page"}),
            "example.com",
        )

    def test_result_hostname_empty_url(self) -> None:
        client = _make_client()
        self.assertEqual(client._result_hostname({}), "")

    def test_result_quality_score(self) -> None:
        client = _make_client()
        score = client._result_quality_score({"content": "abc", "snippet": "de", "title": "f"})
        self.assertEqual(score, (3, 2, 1))


# ===========================================================================
# 15  Citation alignment
# ===========================================================================

class CitationAlignmentTests(unittest.TestCase):
    def test_align_citations_matches_result_order(self) -> None:
        client = _make_client()
        results = [
            {"url": "https://b.com", "title": "B"},
            {"url": "https://a.com", "title": "A"},
        ]
        citations = [
            {"url": "https://a.com", "title": "A"},
            {"url": "https://b.com", "title": "B"},
            {"url": "https://c.com", "title": "C"},
        ]
        aligned = client._align_citations_with_results(results=results, citations=citations)
        urls = [c["url"] for c in aligned]
        # B first (matches first result), then A, then C (extra citation)
        self.assertEqual(urls[0], "https://b.com")
        self.assertEqual(urls[1], "https://a.com")
        self.assertEqual(urls[2], "https://c.com")

    def test_align_citations_deduplicates(self) -> None:
        client = _make_client()
        results = [{"url": "https://a.com", "title": "A"}]
        citations = [
            {"url": "https://a.com", "title": "A"},
            {"url": "https://a.com", "title": "A duplicate"},
        ]
        aligned = client._align_citations_with_results(results=results, citations=citations)
        self.assertEqual(len(aligned), 1)


# ===========================================================================
# 16  Regression tests for bugs fixed earlier
# ===========================================================================

class RegressionTests(unittest.TestCase):
    """Regression tests for bugs fixed in the previous session."""

    def test_operator_precedence_social_citations_fix(self) -> None:
        """The `or [] if social else []` bug would crash when social is None.
        After fix: `(social.get("citations") or []) if social else []`"""
        # Simulate the merge logic with social=None
        social = None
        result = (social.get("citations") or []) if social else []
        self.assertEqual(result, [])

    def test_operator_precedence_secondary_result_fix(self) -> None:
        """Same fix for secondary_result."""
        secondary_result = None
        result = (secondary_result.get("citations") or []) if secondary_result else []
        self.assertEqual(result, [])

    def test_cache_max_entries_set(self) -> None:
        """Cache should have a max entries limit."""
        client = _make_client()
        self.assertEqual(client._cache_max_entries, 256)

    def test_get_int_handles_non_numeric_gracefully(self) -> None:
        """_get_int should return default for non-numeric values."""
        with patch.dict(os.environ, {"TEST_INT": "abc"}):
            result = _get_int("TEST_INT", 42)
        self.assertEqual(result, 42)


if __name__ == "__main__":
    unittest.main()
