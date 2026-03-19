from __future__ import annotations

import unittest
from unittest.mock import patch

from mysearch.clients import MySearchClient, RouteDecision


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
        self.assertLess(first_anthropic, first_non_anthropic)


if __name__ == "__main__":
    unittest.main()
