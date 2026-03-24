from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mysearch.clients import MySearchClient, MySearchError, MySearchHTTPError, RouteDecision


class _FakeResponse:
    def __init__(self, text: str, status: int = 200) -> None:
        self._text = text
        self.status = status

    def read(self) -> bytes:
        return self._text.encode("utf-8")

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class MySearchClientTests(unittest.TestCase):
    def test_pricing_keywords_alone_do_not_trigger_docs_mode(self) -> None:
        client = MySearchClient()

        self.assertFalse(client._looks_like_docs_query("openai pricing"))
        self.assertFalse(client._looks_like_docs_query("苹果 m4 macbook air 价格"))
        self.assertEqual(
            client._resolve_intent(
                query="苹果 M4 MacBook Air 国行价格 官方",
                mode="auto",
                intent="auto",
                sources=["web"],
            ),
            "factual",
        )

    def test_request_json_auth_error_mentions_rejected_key(self) -> None:
        client = MySearchClient()
        provider = client.config.tavily
        error = HTTPError(
            url="https://example.com/search",
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=io.BytesIO(
                b'{"error":"The account associated with this API key has been deactivated."}'
            ),
        )

        with patch("mysearch.clients.urlopen", side_effect=error):
            with self.assertRaises(MySearchHTTPError) as ctx:
                client._request_json(
                    provider=provider,
                    method="POST",
                    path=provider.path("search"),
                    payload={"query": "openai"},
                    key="test-key",
                )

        self.assertEqual(ctx.exception.provider, "tavily")
        self.assertEqual(ctx.exception.status_code, 401)
        self.assertIn("configured but the API key was rejected", str(ctx.exception))
        self.assertIn("deactivated", str(ctx.exception))

    def test_health_reports_live_auth_error(self) -> None:
        client = MySearchClient()
        client._probe_provider_status = lambda provider, key_count: {  # type: ignore[method-assign]
            "status": "auth_error" if provider.name == "tavily" else "ok",
            "error": "tavily is configured but the API key was rejected (HTTP 401): deactivated"
            if provider.name == "tavily"
            else "",
            "checked_at": "2026-03-20T00:00:00+00:00",
        }

        payload = client.health()

        self.assertEqual(payload["providers"]["tavily"]["live_status"], "auth_error")
        self.assertIn("deactivated", payload["providers"]["tavily"]["live_error"])
        self.assertEqual(payload["providers"]["firecrawl"]["live_status"], "ok")

    def test_xai_compatible_health_probe_uses_root_health_endpoint(self) -> None:
        client = MySearchClient()
        provider = client.config.xai
        provider.search_mode = "compatible"
        provider.default_paths["social_search"] = "/social/search"
        provider.default_paths["social_health"] = "/social/health"
        provider.alternate_base_urls["social_search"] = "http://gateway.example/v1"
        provider.alternate_base_urls["social_health"] = "http://gateway.example/v1"
        calls: list[dict[str, object]] = []

        def fake_request_json(**kwargs):  # type: ignore[no-untyped-def]
            calls.append(kwargs)
            return {"status": "ok"}

        client._request_json = fake_request_json  # type: ignore[method-assign]

        client._probe_provider_request(provider, "gateway-token")

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["method"], "GET")
        self.assertEqual(calls[0]["path"], "/health")
        self.assertEqual(calls[0]["base_url"], "http://gateway.example")

    def test_xai_compatible_health_probe_falls_back_when_root_health_missing(self) -> None:
        client = MySearchClient()
        provider = client.config.xai
        provider.search_mode = "compatible"
        provider.default_paths["social_search"] = "/social/search"
        provider.default_paths["social_health"] = "/social/health"
        provider.alternate_base_urls["social_search"] = "http://gateway.example/admin?foo=1"
        provider.alternate_base_urls["social_health"] = "http://gateway.example/admin?foo=1"
        calls: list[dict[str, object]] = []

        def fake_request_json(**kwargs):  # type: ignore[no-untyped-def]
            calls.append(kwargs)
            if kwargs["path"] == "/health":
                raise MySearchHTTPError(
                    provider="xai",
                    status_code=404,
                    detail="not found",
                    url="http://gateway.example/health",
                )
            return {"provider": "custom_social", "results": [{"url": "https://x.com/openai/status/1"}]}

        client._request_json = fake_request_json  # type: ignore[method-assign]

        client._probe_provider_request(provider, "gateway-token")

        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["method"], "GET")
        self.assertEqual(calls[0]["path"], "/health")
        self.assertEqual(calls[1]["method"], "POST")
        self.assertEqual(calls[1]["path"], "/social/search")
        self.assertEqual(calls[1]["payload"]["max_results"], 1)
        self.assertEqual(calls[1]["payload"]["model"], "grok-4.1-fast")

    def test_xai_official_health_probe_uses_status_page(self) -> None:
        client = MySearchClient()
        provider = client.config.xai
        provider.search_mode = "official"
        calls: list[dict[str, object]] = []

        def fake_request_text(**kwargs):  # type: ignore[no-untyped-def]
            calls.append(kwargs)
            return 200, "API (us-east-1.api.x.ai) available"

        client._request_text = fake_request_text  # type: ignore[method-assign]

        client._probe_provider_request(provider, "official-key")

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["url"], "https://status.x.ai/")

    def test_xai_official_health_probe_falls_back_to_fast_responses_when_status_check_fails(self) -> None:
        client = MySearchClient()
        provider = client.config.xai
        provider.search_mode = "official"
        provider.default_paths["responses"] = "/responses"
        text_calls: list[dict[str, object]] = []
        json_calls: list[dict[str, object]] = []

        def fake_request_text(**kwargs):  # type: ignore[no-untyped-def]
            text_calls.append(kwargs)
            raise MySearchError("unable to determine xAI API status from status.x.ai")

        def fake_request_json(**kwargs):  # type: ignore[no-untyped-def]
            json_calls.append(kwargs)
            return {"id": "resp_123", "status": "completed"}

        client._request_text = fake_request_text  # type: ignore[method-assign]
        client._request_json = fake_request_json  # type: ignore[method-assign]

        client._probe_provider_request(provider, "official-key")

        self.assertEqual(len(text_calls), 1)
        self.assertEqual(len(json_calls), 1)
        self.assertEqual(json_calls[0]["path"], "/responses")
        self.assertEqual(json_calls[0]["payload"]["model"], "grok-4.1-fast")

    def test_github_blob_raw_urls_try_common_branch_aliases(self) -> None:
        client = MySearchClient()

        raw_urls = client._github_blob_raw_urls(
            "https://github.com/openai/openai-node/blob/main/README.md"
        )

        self.assertEqual(
            raw_urls,
            [
                "https://raw.githubusercontent.com/openai/openai-node/main/README.md",
                "https://raw.githubusercontent.com/openai/openai-node/master/README.md",
            ],
        )

    def test_extract_github_blob_raw_falls_back_to_master(self) -> None:
        client = MySearchClient()

        def fake_urlopen(request, timeout):
            if request.full_url.endswith("/main/README.md"):
                raise ValueError("404")
            return _FakeResponse("# OpenAI Node README")

        with patch("mysearch.clients.urlopen", side_effect=fake_urlopen):
            result = client._extract_github_blob_raw(
                url="https://github.com/openai/openai-node/blob/main/README.md"
            )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["provider"], "github_raw")
        self.assertEqual(
            result["metadata"]["raw_url"],
            "https://raw.githubusercontent.com/openai/openai-node/master/README.md",
        )

    def test_firecrawl_domain_filtered_search_falls_back_to_tavily(self) -> None:
        client = MySearchClient()
        client.keyring.has_provider = lambda provider: provider == "tavily"  # type: ignore[method-assign]
        client._probe_provider_status = lambda provider, key_count: {  # type: ignore[method-assign]
            "status": "ok",
            "error": "",
            "checked_at": "2026-03-20T00:00:00+00:00",
        }
        client._search_firecrawl_once = lambda **kwargs: {  # type: ignore[method-assign]
            "provider": "firecrawl",
            "transport": "env",
            "query": kwargs["query"],
            "answer": "",
            "results": [],
            "citations": [],
        }
        client._search_tavily = lambda **kwargs: {  # type: ignore[method-assign]
            "provider": "tavily",
            "transport": "env",
            "query": kwargs["query"],
            "answer": "",
            "results": [
                {
                    "provider": "tavily",
                    "source": "web",
                    "title": "Responses | OpenAI API Reference",
                    "url": "https://platform.openai.com/docs/api-reference/responses",
                    "snippet": "OpenAI Responses API docs",
                    "content": "",
                }
            ],
            "citations": [
                {
                    "title": "Responses | OpenAI API Reference",
                    "url": "https://platform.openai.com/docs/api-reference/responses",
                }
            ],
        }

        result = client._search_firecrawl(
            query="OpenAI Responses API docs",
            max_results=5,
            categories=["technical"],
            include_content=False,
            include_domains=["openai.com"],
            exclude_domains=None,
        )

        self.assertEqual(result["provider"], "hybrid")
        self.assertEqual(result["route_selected"], "firecrawl+tavily")
        self.assertEqual(result["fallback"]["from"], "firecrawl")
        self.assertEqual(result["fallback"]["to"], "tavily")
        self.assertEqual(len(result["results"]), 1)

    def test_firecrawl_domain_filtered_search_retries_without_site_filter(self) -> None:
        client = MySearchClient()
        client.keyring.has_provider = lambda provider: False  # type: ignore[method-assign]

        def fake_search_firecrawl_once(**kwargs):  # type: ignore[no-untyped-def]
            query = kwargs["query"]
            if query.startswith("site:docs.firecrawl.dev "):
                return {
                    "provider": "firecrawl",
                    "transport": "env",
                    "query": query,
                    "answer": "",
                    "results": [],
                    "citations": [],
                }
            return {
                "provider": "firecrawl",
                "transport": "env",
                "query": query,
                "answer": "",
                "results": [
                    {
                        "provider": "firecrawl",
                        "source": "web",
                        "title": "Scrape - Firecrawl Docs",
                        "url": "https://docs.firecrawl.dev/api-reference/endpoint/scrape",
                        "snippet": "Official Firecrawl docs",
                        "content": "",
                    },
                    {
                        "provider": "firecrawl",
                        "source": "web",
                        "title": "Firecrawl tutorial recap",
                        "url": "https://example.com/firecrawl-scrape-guide",
                        "snippet": "Third-party recap",
                        "content": "",
                    },
                ],
                "citations": [
                    {
                        "title": "Scrape - Firecrawl Docs",
                        "url": "https://docs.firecrawl.dev/api-reference/endpoint/scrape",
                    },
                    {
                        "title": "Firecrawl tutorial recap",
                        "url": "https://example.com/firecrawl-scrape-guide",
                    },
                ],
            }

        client._search_firecrawl_once = fake_search_firecrawl_once  # type: ignore[method-assign]

        result = client._search_firecrawl(
            query="Firecrawl docs scrape api",
            max_results=5,
            categories=["technical"],
            include_content=False,
            include_domains=["docs.firecrawl.dev"],
            exclude_domains=None,
        )

        self.assertEqual(result["provider"], "firecrawl")
        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(
            result["results"][0]["url"],
            "https://docs.firecrawl.dev/api-reference/endpoint/scrape",
        )
        self.assertEqual(
            result["route_debug"]["domain_filter_mode"],
            "client_filter_retry",
        )
        self.assertEqual(
            result["route_debug"]["retried_include_domains"],
            ["docs.firecrawl.dev"],
        )

    def test_firecrawl_domain_filtered_search_skips_tavily_auth_error_fallback(self) -> None:
        client = MySearchClient()
        client.keyring.has_provider = lambda provider: provider in {"tavily", "firecrawl"}  # type: ignore[method-assign]
        client._probe_provider_status = lambda provider, key_count: {  # type: ignore[method-assign]
            "status": "auth_error" if provider.name == "tavily" else "ok",
            "error": "tavily rejected" if provider.name == "tavily" else "",
            "checked_at": "2026-03-20T00:00:00+00:00",
        }
        client._search_firecrawl_once = lambda **kwargs: {  # type: ignore[method-assign]
            "provider": "firecrawl",
            "transport": "env",
            "query": kwargs["query"],
            "answer": "",
            "results": [],
            "citations": [],
        }
        client._search_tavily = lambda **kwargs: (_ for _ in ()).throw(AssertionError("should not call tavily"))  # type: ignore[method-assign]

        result = client._search_firecrawl(
            query="Firecrawl docs scrape api",
            max_results=5,
            categories=["technical"],
            include_content=False,
            include_domains=["docs.firecrawl.dev"],
            exclude_domains=None,
        )

        self.assertEqual(result["provider"], "firecrawl")
        self.assertEqual(result["results"], [])

    def test_tavily_domain_filtered_search_retries_with_site_query(self) -> None:
        client = MySearchClient()
        calls: list[dict[str, object]] = []

        def fake_search_tavily_once(**kwargs):  # type: ignore[no-untyped-def]
            calls.append(dict(kwargs))
            if kwargs["query"] == "OpenAI Responses API docs":
                return {
                    "provider": "tavily",
                    "transport": "env",
                    "query": kwargs["query"],
                    "answer": "",
                    "results": [],
                    "citations": [],
                }
            return {
                "provider": "tavily",
                "transport": "env",
                "query": kwargs["query"],
                "answer": "",
                "results": [
                    {
                        "provider": "tavily",
                        "source": "web",
                        "title": "Responses | OpenAI API Reference",
                        "url": "https://platform.openai.com/docs/api-reference/responses",
                        "snippet": "Official OpenAI docs",
                        "content": "",
                    },
                    {
                        "provider": "tavily",
                        "source": "web",
                        "title": "Community recap",
                        "url": "https://example.com/openai-responses-guide",
                        "snippet": "Third-party article",
                        "content": "",
                    }
                ],
                "citations": [
                    {
                        "title": "Responses | OpenAI API Reference",
                        "url": "https://platform.openai.com/docs/api-reference/responses",
                    },
                    {
                        "title": "Community recap",
                        "url": "https://example.com/openai-responses-guide",
                    }
                ],
            }

        client._search_tavily_once = fake_search_tavily_once  # type: ignore[method-assign]

        result = client._search_tavily(
            query="OpenAI Responses API docs",
            max_results=5,
            topic="general",
            include_answer=False,
            include_content=False,
            include_domains=["openai.com"],
            exclude_domains=None,
        )

        self.assertEqual(
            result["results"][0]["url"],
            "https://platform.openai.com/docs/api-reference/responses",
        )
        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(result["route_debug"]["domain_filter_mode"], "site_query_retry")
        self.assertEqual(result["route_debug"]["retried_include_domains"], ["openai.com"])
        self.assertEqual(calls[1]["query"], "site:openai.com OpenAI Responses API docs")
        self.assertIsNone(calls[1]["include_domains"])

    def test_tavily_domain_filtered_search_falls_back_to_firecrawl(self) -> None:
        client = MySearchClient()
        client.keyring.has_provider = lambda provider: provider == "firecrawl"  # type: ignore[method-assign]
        client._probe_provider_status = lambda provider, key_count: {  # type: ignore[method-assign]
            "status": "ok",
            "error": "",
            "checked_at": "2026-03-20T00:00:00+00:00",
        }
        client._search_tavily_once = lambda **kwargs: {  # type: ignore[method-assign]
            "provider": "tavily",
            "transport": "env",
            "query": kwargs["query"],
            "answer": "",
            "results": [],
            "citations": [],
        }
        client._search_firecrawl_once = lambda **kwargs: {  # type: ignore[method-assign]
            "provider": "firecrawl",
            "transport": "env",
            "query": kwargs["query"],
            "answer": "",
            "results": [
                {
                    "provider": "firecrawl",
                    "source": "web",
                    "title": "Responses | OpenAI API Reference",
                    "url": "https://platform.openai.com/docs/api-reference/responses",
                    "snippet": "Official OpenAI docs",
                    "content": "",
                }
            ],
            "citations": [
                {
                    "title": "Responses | OpenAI API Reference",
                    "url": "https://platform.openai.com/docs/api-reference/responses",
                }
            ],
        }

        result = client._search_tavily(
            query="OpenAI Responses API docs",
            max_results=5,
            topic="general",
            include_answer=False,
            include_content=False,
            include_domains=["openai.com"],
            exclude_domains=None,
        )

        self.assertEqual(result["provider"], "hybrid")
        self.assertEqual(result["route_selected"], "tavily+firecrawl")
        self.assertEqual(result["fallback"]["from"], "tavily")
        self.assertEqual(result["fallback"]["to"], "firecrawl")
        self.assertEqual(len(result["results"]), 1)

    def test_docs_blended_search_reranks_official_results_ahead_of_third_party(self) -> None:
        client = MySearchClient()
        official_url = "https://platform.openai.com/docs/api-reference/responses"
        reddit_url = "https://www.reddit.com/r/OpenAI/comments/example"
        arxiv_url = "https://arxiv.org/abs/2401.00001"

        client._search_tavily = lambda **kwargs: {  # type: ignore[method-assign]
            "provider": "tavily",
            "transport": "env",
            "query": kwargs["query"],
            "answer": "",
            "results": [
                {
                    "provider": "tavily",
                    "source": "web",
                    "title": "OpenAI Responses API docs discussion",
                    "url": reddit_url,
                    "snippet": "Reddit thread about the Responses API",
                    "content": "",
                },
                {
                    "provider": "tavily",
                    "source": "web",
                    "title": "Responses | OpenAI API Reference",
                    "url": official_url,
                    "snippet": "Official OpenAI Responses API reference",
                    "content": "",
                },
            ],
            "citations": [
                {"title": "OpenAI Responses API docs discussion", "url": reddit_url},
                {"title": "Responses | OpenAI API Reference", "url": official_url},
            ],
        }
        client._search_firecrawl = lambda **kwargs: {  # type: ignore[method-assign]
            "provider": "firecrawl",
            "transport": "env",
            "query": kwargs["query"],
            "answer": "",
            "results": [
                {
                    "provider": "firecrawl",
                    "source": "web",
                    "title": "Attention Is All You Need for OpenAI responses",
                    "url": arxiv_url,
                    "snippet": "Paper result that should not outrank official docs",
                    "content": "",
                },
                {
                    "provider": "firecrawl",
                    "source": "web",
                    "title": "Responses | OpenAI API Reference",
                    "url": official_url,
                    "snippet": "Official OpenAI Responses API reference",
                    "content": "Request and response schema details",
                },
            ],
            "citations": [
                {"title": "Attention Is All You Need for OpenAI responses", "url": arxiv_url},
                {"title": "Responses | OpenAI API Reference", "url": official_url},
            ],
        }

        result = client._search_web_blended(
            query="OpenAI Responses API docs",
            mode="docs",
            intent="resource",
            strategy="balanced",
            decision=RouteDecision(provider="tavily", reason="test", tavily_topic="general"),
            max_results=5,
            include_content=False,
            include_answer=False,
            include_domains=None,
            exclude_domains=None,
        )

        self.assertEqual(result["results"][0]["url"], official_url)
        self.assertEqual(result["citations"][0]["url"], official_url)
        self.assertIn(reddit_url, [item["url"] for item in result["results"][1:]])
        self.assertIn(arxiv_url, [item["url"] for item in result["results"][1:]])

    def test_docs_blended_search_prioritizes_include_domains(self) -> None:
        client = MySearchClient()
        official_url = "https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering"
        medium_url = "https://medium.com/@writer/anthropic-prompting-notes"
        youtube_url = "https://www.youtube.com/watch?v=anthropic-docs"

        client._search_tavily = lambda **kwargs: {  # type: ignore[method-assign]
            "provider": "tavily",
            "transport": "env",
            "query": kwargs["query"],
            "answer": "",
            "results": [
                {
                    "provider": "tavily",
                    "source": "web",
                    "title": "Anthropic prompt engineering notes",
                    "url": medium_url,
                    "snippet": "Third-party write-up",
                    "content": "",
                },
                {
                    "provider": "tavily",
                    "source": "web",
                    "title": "Prompt engineering - Anthropic",
                    "url": official_url,
                    "snippet": "Official Anthropic docs",
                    "content": "",
                },
            ],
            "citations": [
                {"title": "Anthropic prompt engineering notes", "url": medium_url},
                {"title": "Prompt engineering - Anthropic", "url": official_url},
            ],
        }
        client._search_firecrawl = lambda **kwargs: {  # type: ignore[method-assign]
            "provider": "firecrawl",
            "transport": "env",
            "query": kwargs["query"],
            "answer": "",
            "results": [
                {
                    "provider": "firecrawl",
                    "source": "web",
                    "title": "Anthropic docs overview video",
                    "url": youtube_url,
                    "snippet": "Third-party video recap",
                    "content": "",
                },
                {
                    "provider": "firecrawl",
                    "source": "web",
                    "title": "Prompt engineering - Anthropic",
                    "url": official_url,
                    "snippet": "Official Anthropic docs",
                    "content": "Official prompt engineering guidance",
                },
            ],
            "citations": [
                {"title": "Anthropic docs overview video", "url": youtube_url},
                {"title": "Prompt engineering - Anthropic", "url": official_url},
            ],
        }

        result = client._search_web_blended(
            query="Anthropic prompt engineering docs",
            mode="docs",
            intent="resource",
            strategy="balanced",
            decision=RouteDecision(provider="tavily", reason="test", tavily_topic="general"),
            max_results=5,
            include_content=False,
            include_answer=False,
            include_domains=["anthropic.com"],
            exclude_domains=None,
        )

        urls = [item["url"] for item in result["results"]]
        first_non_anthropic = next(
            index for index, url in enumerate(urls) if "anthropic.com" not in url
        )
        first_anthropic = next(
            index for index, url in enumerate(urls) if "anthropic.com" in url
        )

        self.assertEqual(result["results"][0]["url"], official_url)
        self.assertEqual(result["citations"][0]["url"], official_url)

    def test_search_route_reason_surfaces_secondary_provider_auth_error(self) -> None:
        client = MySearchClient()
        client.keyring.has_provider = lambda provider: provider in {"tavily", "firecrawl"}  # type: ignore[method-assign]
        client._probe_provider_status = lambda provider, key_count: {  # type: ignore[method-assign]
            "status": "ok",
            "error": "",
            "checked_at": "2026-03-20T00:00:00+00:00",
        }
        client._route_search = lambda **kwargs: RouteDecision(  # type: ignore[method-assign]
            provider="tavily",
            reason="普通网页检索默认走 Tavily",
            tavily_topic="general",
        )
        client._search_tavily = lambda **kwargs: {  # type: ignore[method-assign]
            "provider": "tavily",
            "transport": "env",
            "query": kwargs["query"],
            "answer": "",
            "results": [
                {
                    "provider": "tavily",
                    "source": "web",
                    "title": "Official docs",
                    "url": "https://docs.example.com/page",
                    "snippet": "Official docs",
                    "content": "",
                }
            ],
            "citations": [{"title": "Official docs", "url": "https://docs.example.com/page"}],
        }

        def fail_firecrawl(**kwargs):  # type: ignore[no-untyped-def]
            raise MySearchHTTPError(
                provider="firecrawl",
                status_code=401,
                detail="The account associated with this API key has been deactivated.",
                url="https://example.com/search",
            )

        client._search_firecrawl = fail_firecrawl  # type: ignore[method-assign]

        result = client.search(
            query="example search",
            mode="auto",
            strategy="balanced",
            provider="auto",
            include_answer=False,
        )

        self.assertEqual(result["provider"], "tavily")
        self.assertIn("secondary provider issue", result["route"]["reason"])
        self.assertIn("configured but the API key was rejected", result["route"]["reason"])

    def test_docs_route_skips_tavily_when_live_probe_reports_auth_error(self) -> None:
        client = MySearchClient()
        client.keyring.has_provider = lambda provider: provider in {"tavily", "firecrawl"}  # type: ignore[method-assign]
        client._probe_provider_status = lambda provider, key_count: {  # type: ignore[method-assign]
            "status": "auth_error" if provider.name == "tavily" else "ok",
            "error": "tavily rejected" if provider.name == "tavily" else "",
            "checked_at": "2026-03-20T00:00:00+00:00",
        }

        decision = client._route_search(
            query="newapi cache 官方文档",
            mode="docs",
            intent="resource",
            provider="auto",
            sources=["web"],
            include_content=False,
            include_domains=None,
            allowed_x_handles=None,
            excluded_x_handles=None,
        )

        self.assertEqual(decision.provider, "firecrawl")

    def test_search_reranks_direct_docs_results_to_official_first(self) -> None:
        client = MySearchClient()
        client._search_tavily = lambda **kwargs: {  # type: ignore[method-assign]
            "provider": "tavily",
            "transport": "env",
            "query": kwargs["query"],
            "answer": "",
            "results": [
                {
                    "provider": "tavily",
                    "source": "web",
                    "title": "Playwright test.step Guide",
                    "url": "https://www.checklyhq.com/blog/playwright-test-step-guide/",
                    "snippet": "Third-party guide",
                    "content": "",
                },
                {
                    "provider": "tavily",
                    "source": "web",
                    "title": "test.step | Playwright",
                    "url": "https://playwright.dev/docs/api/class-test",
                    "snippet": "Official Playwright docs",
                    "content": "",
                },
            ],
            "citations": [
                {
                    "title": "Playwright test.step Guide",
                    "url": "https://www.checklyhq.com/blog/playwright-test-step-guide/",
                },
                {
                    "title": "test.step | Playwright",
                    "url": "https://playwright.dev/docs/api/class-test",
                },
            ],
        }

        result = client.search(
            query="Playwright test.step docs",
            mode="docs",
            strategy="fast",
            provider="tavily",
            include_answer=False,
        )

        self.assertEqual(result["results"][0]["url"], "https://playwright.dev/docs/api/class-test")
        self.assertEqual(result["citations"][0]["url"], "https://playwright.dev/docs/api/class-test")
        self.assertEqual(result["evidence"]["official_source_count"], 1)
        self.assertEqual(result["evidence"]["confidence"], "high")
        self.assertNotIn("mixed-official-and-third-party", result["evidence"]["conflicts"])

    def test_search_strict_official_mode_filters_to_official_results(self) -> None:
        client = MySearchClient()
        client._search_tavily = lambda **kwargs: {  # type: ignore[method-assign]
            "provider": "tavily",
            "transport": "env",
            "query": kwargs["query"],
            "answer": "",
            "results": [
                {
                    "provider": "tavily",
                    "source": "web",
                    "title": "Playwright test.step Guide",
                    "url": "https://www.checklyhq.com/blog/playwright-test-step-guide/",
                    "snippet": "Third-party guide",
                    "content": "",
                },
                {
                    "provider": "tavily",
                    "source": "web",
                    "title": "test.step | Playwright",
                    "url": "https://playwright.dev/docs/api/class-test",
                    "snippet": "Official Playwright docs",
                    "content": "",
                },
            ],
            "citations": [
                {
                    "title": "Playwright test.step Guide",
                    "url": "https://www.checklyhq.com/blog/playwright-test-step-guide/",
                },
                {
                    "title": "test.step | Playwright",
                    "url": "https://playwright.dev/docs/api/class-test",
                },
            ],
        }

        result = client.search(
            query="Playwright test.step official docs",
            mode="docs",
            strategy="fast",
            provider="tavily",
            include_domains=["playwright.dev"],
            include_answer=False,
        )

        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(result["results"][0]["url"], "https://playwright.dev/docs/api/class-test")
        self.assertEqual(result["evidence"]["official_mode"], "strict")
        self.assertTrue(result["evidence"]["official_filter_applied"])
        self.assertEqual(result["evidence"]["official_source_count"], 1)
        self.assertNotIn("mixed-official-and-third-party", result["evidence"]["conflicts"])

    def test_search_strict_official_mode_keeps_results_but_flags_unmet(self) -> None:
        client = MySearchClient()
        client._search_tavily = lambda **kwargs: {  # type: ignore[method-assign]
            "provider": "tavily",
            "transport": "env",
            "query": kwargs["query"],
            "answer": "",
            "results": [
                {
                    "provider": "tavily",
                    "source": "web",
                    "title": "OpenAI API Pricing Guide",
                    "url": "https://apidog.com/blog/openai-api-pricing/",
                    "snippet": "Third-party pricing guide",
                    "content": "",
                },
            ],
            "citations": [
                {
                    "title": "OpenAI API Pricing Guide",
                    "url": "https://apidog.com/blog/openai-api-pricing/",
                },
            ],
        }

        result = client.search(
            query="OpenAI pricing official",
            mode="web",
            strategy="fast",
            provider="tavily",
            include_answer=False,
        )

        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(result["evidence"]["official_mode"], "strict")
        self.assertFalse(result["evidence"]["official_filter_applied"])
        self.assertIn("strict-official-unmet", result["evidence"]["conflicts"])
        self.assertEqual(result["evidence"]["confidence"], "low")

    def test_search_strict_official_mode_counts_official_hits_for_web_queries(self) -> None:
        client = MySearchClient()
        client._search_tavily = lambda **kwargs: {  # type: ignore[method-assign]
            "provider": "tavily",
            "transport": "env",
            "query": kwargs["query"],
            "answer": "",
            "results": [
                {
                    "provider": "tavily",
                    "source": "web",
                    "title": "API Pricing | OpenAI",
                    "url": "https://openai.com/api/pricing/",
                    "snippet": "Official pricing page",
                    "content": "",
                },
                {
                    "provider": "tavily",
                    "source": "web",
                    "title": "OpenAI API Pricing Guide",
                    "url": "https://apidog.com/blog/openai-api-pricing/",
                    "snippet": "Third-party pricing guide",
                    "content": "",
                },
            ],
            "citations": [
                {"title": "API Pricing | OpenAI", "url": "https://openai.com/api/pricing/"},
                {
                    "title": "OpenAI API Pricing Guide",
                    "url": "https://apidog.com/blog/openai-api-pricing/",
                },
            ],
        }

        result = client.search(
            query="OpenAI pricing official",
            mode="web",
            strategy="fast",
            provider="tavily",
            include_answer=False,
        )

        self.assertEqual(result["results"][0]["url"], "https://openai.com/api/pricing/")
        self.assertEqual(result["evidence"]["official_mode"], "strict")
        self.assertEqual(result["evidence"]["official_source_count"], 1)
        self.assertTrue(result["evidence"]["official_filter_applied"])
        self.assertNotIn("strict-official-unmet", result["evidence"]["conflicts"])

    def test_docs_mode_enters_strict_resource_policy(self) -> None:
        client = MySearchClient()

        mode = client._resolve_official_result_mode(
            query="Next.js generateMetadata",
            mode="docs",
            intent="resource",
            include_domains=None,
        )

        self.assertEqual(mode, "strict")

    def test_search_uses_exa_rescue_for_sparse_web_results(self) -> None:
        client = MySearchClient()
        client._provider_can_serve = lambda provider: True  # type: ignore[method-assign]
        client._search_tavily = lambda **kwargs: {  # type: ignore[method-assign]
            "provider": "tavily",
            "transport": "env",
            "query": kwargs["query"],
            "answer": "",
            "results": [
                {
                    "provider": "tavily",
                    "source": "web",
                    "title": "Sparse result",
                    "url": "https://example.com/one",
                    "snippet": "Only one result",
                    "content": "",
                }
            ],
            "citations": [
                {"title": "Sparse result", "url": "https://example.com/one"},
            ],
        }
        client._search_exa = lambda **kwargs: {  # type: ignore[method-assign]
            "provider": "exa",
            "transport": "env",
            "query": kwargs["query"],
            "answer": "",
            "results": [
                {
                    "provider": "exa",
                    "source": "web",
                    "title": "Long tail reference",
                    "url": "https://exa.example.com/two",
                    "snippet": "Recovered long-tail source",
                    "content": "",
                },
                {
                    "provider": "exa",
                    "source": "web",
                    "title": "Another result",
                    "url": "https://exa.example.com/three",
                    "snippet": "Recovered another source",
                    "content": "",
                },
            ],
            "citations": [
                {"title": "Long tail reference", "url": "https://exa.example.com/two"},
                {"title": "Another result", "url": "https://exa.example.com/three"},
            ],
        }

        result = client.search(
            query="best open source vector database comparison for offline agents",
            mode="web",
            strategy="fast",
            provider="tavily",
            max_results=3,
            include_answer=False,
        )

        self.assertEqual(result["provider"], "hybrid")
        self.assertEqual(result["fallback"]["to"], "exa")
        self.assertEqual(result["results"][0]["url"], "https://exa.example.com/two")

    def test_rerank_general_news_prefers_mainstream_article_shape(self) -> None:
        client = MySearchClient()

        reranked = client._rerank_general_results(
            query="2026 oscar winners",
            result_profile="news",
            include_domains=None,
            results=[
                {
                    "provider": "tavily",
                    "title": "Oscars 2026 winners list",
                    "url": "https://news-aggregate.example.com/oscars-winners",
                    "snippet": "aggregated summary",
                    "content": "",
                },
                {
                    "provider": "tavily",
                    "title": "Oscars 2026 winners list",
                    "url": "https://www.latimes.com/entertainment-arts/awards/story/2026-03-15/oscars-2026-winners-list-full-results",
                    "snippet": "Los Angeles Times coverage",
                    "content": "",
                    "published_date": "2026-03-15T09:00:00+00:00",
                },
            ],
        )

        self.assertEqual(
            reranked[0]["url"],
            "https://www.latimes.com/entertainment-arts/awards/story/2026-03-15/oscars-2026-winners-list-full-results",
        )

    def test_resolve_research_plan_adapts_docs_and_news_budgets(self) -> None:
        client = MySearchClient()

        docs_plan = client._resolve_research_plan(
            query="OpenAI pricing official",
            mode="docs",
            intent="resource",
            strategy="balanced",
            web_max_results=5,
            social_max_results=5,
            scrape_top_n=4,
            include_social=True,
            include_domains=None,
        )
        news_plan = client._resolve_research_plan(
            query="2026 oscars winners",
            mode="news",
            intent="news",
            strategy="deep",
            web_max_results=5,
            social_max_results=2,
            scrape_top_n=3,
            include_social=True,
            include_domains=None,
        )

        self.assertEqual(docs_plan["web_mode"], "docs")
        self.assertEqual(docs_plan["scrape_top_n"], 2)
        self.assertGreaterEqual(news_plan["web_max_results"], 6)
        self.assertGreaterEqual(news_plan["social_max_results"], 4)
        self.assertGreaterEqual(news_plan["scrape_top_n"], 4)


if __name__ == "__main__":
    unittest.main()
