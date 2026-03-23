from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mysearch import social_gateway

PROXY_DIR = REPO_ROOT / "proxy"
if str(PROXY_DIR) not in sys.path:
    sys.path.insert(0, str(PROXY_DIR))


def _load_proxy_server_module():
    spec = importlib.util.spec_from_file_location(
        "test_proxy_server_module",
        PROXY_DIR / "server.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


proxy_server = _load_proxy_server_module()


def _payload(*, text: str, citations: list[dict[str, str]] | None = None) -> dict[str, object]:
    return {
        "output": [
            {
                "content": [
                    {
                        "type": "output_text",
                        "text": text,
                        "annotations": citations or [],
                    }
                ]
            }
        ]
    }


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, object], text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text or str(payload)

    def json(self) -> dict[str, object]:
        return self._payload


class _FakeHttpClient:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, object]] = []

    async def post(self, url: str, json: dict[str, object], headers: dict[str, str]) -> _FakeResponse:
        self.calls.append({"url": url, "json": json, "headers": headers})
        return self._responses.pop(0)


class _FakeRequest:
    def __init__(self, body: dict[str, object]) -> None:
        self._body = body
        self.headers = {"Authorization": "Bearer test-token"}

    async def json(self) -> dict[str, object]:
        return self._body


class SocialNormalizationTests(unittest.TestCase):
    def test_fake_model_url_is_dropped_when_citation_disagrees(self) -> None:
        payload = _payload(
            text='{"answer":"summary","results":[{"url":"https://x.com/fake/status/1234567890123456789","text":"fabricated"}]}',
            citations=[
                {
                    "url": "https://x.com/OpenAI/status/1901234567890123456",
                    "title": "OpenAI launches update",
                }
            ],
        )

        for module in (social_gateway, proxy_server):
            result = module.normalize_social_search_response("OpenAI", payload, 5)
            self.assertEqual(len(result["results"]), 1)
            self.assertEqual(
                result["results"][0]["url"],
                "https://x.com/OpenAI/status/1901234567890123456",
            )
            self.assertEqual(result["results"][0]["text"], "")
            self.assertEqual(
                result["citations"],
                [
                    {
                        "title": "OpenAI launches update",
                        "url": "https://x.com/OpenAI/status/1901234567890123456",
                    }
                ],
            )

    def test_matching_twitter_alias_merges_model_fields_into_trusted_citation(self) -> None:
        payload = _payload(
            text='{"answer":"summary","results":[{"url":"https://twitter.com/openai/status/1901234567890123456","text":"real post text","author":"OpenAI","handle":"@OpenAI","created_at":"2026-03-19T12:00:00Z","why_relevant":"launch context"}]}',
            citations=[
                {
                    "url": "https://x.com/OpenAI/status/1901234567890123456?utm_source=test",
                    "title": "OpenAI launches update",
                }
            ],
        )

        for module in (social_gateway, proxy_server):
            result = module.normalize_social_search_response("OpenAI", payload, 5)
            self.assertEqual(len(result["results"]), 1)
            item = result["results"][0]
            self.assertEqual(
                item["url"],
                "https://x.com/OpenAI/status/1901234567890123456?utm_source=test",
            )
            self.assertEqual(item["text"], "real post text")
            self.assertEqual(item["author"], "OpenAI")
            self.assertEqual(item["handle"], "OpenAI")
            self.assertEqual(item["why_relevant"], "launch context")

    def test_citation_only_payload_still_returns_usable_results(self) -> None:
        payload = _payload(
            text="No structured JSON here.",
            citations=[
                {
                    "url": "https://x.com/modelcontextproto/status/1902234567890123456",
                    "title": "MCP ecosystem update",
                },
                {
                    "url": "https://x.com/modelcontextproto/status/1902234567890123456",
                    "title": "duplicate",
                },
            ],
        )

        for module in (social_gateway, proxy_server):
            result = module.normalize_social_search_response(
                "Model Context Protocol",
                payload,
                5,
            )
            self.assertEqual(len(result["results"]), 1)
            self.assertEqual(
                result["results"][0]["title"],
                "MCP ecosystem update",
            )
            self.assertEqual(result["results"][0]["text"], "")

    def test_without_citations_only_plausible_status_urls_survive(self) -> None:
        payload = _payload(
            text='{"answer":"summary","results":[{"url":"https://x.com/openai/status/1903234567890123456","text":"kept"},{"url":"https://x.com/OpenAI/status/1234567890123456789","text":"drop synthetic"},{"url":"https://modelcontextprotocol.io/docs/getting-started/intro","text":"drop non-social"},{"url":"https://x.com/openai","text":"drop profile"},{"url":"notaurl","text":"drop invalid"}]}'
        )

        for module in (social_gateway, proxy_server):
            result = module.normalize_social_search_response("OpenAI", payload, 5)
            self.assertEqual(
                [item["url"] for item in result["results"]],
                ["https://x.com/openai/status/1903234567890123456"],
            )


class SocialFallbackRouteTests(unittest.IsolatedAsyncioTestCase):
    async def _run_route(
        self,
        module,
        *,
        responses: list[_FakeResponse],
        query: str = "Model Context Protocol",
        max_results: int = 5,
    ) -> tuple[dict[str, object], _FakeHttpClient]:
        fake_client = _FakeHttpClient(responses)
        request = _FakeRequest({"query": query, "source": "x", "max_results": max_results})
        original_http_client = module.http_client

        if module is social_gateway:
            original_resolve = module.resolve_gateway_state
            original_verify = module.verify_gateway_token
            module.resolve_gateway_state = _fake_gateway_state  # type: ignore[assignment]
            module.verify_gateway_token = lambda token, accepted_tokens: None  # type: ignore[assignment]
            route = module.social_search
        else:
            original_resolve = module.resolve_social_gateway_state
            original_verify = module.verify_social_gateway_token
            module.resolve_social_gateway_state = _fake_proxy_state  # type: ignore[assignment]
            module.verify_social_gateway_token = lambda token, accepted_tokens: None  # type: ignore[assignment]
            route = module.proxy_social_search

        module.http_client = fake_client
        try:
            result = await route(request)
        finally:
            module.http_client = original_http_client
            if module is social_gateway:
                module.resolve_gateway_state = original_resolve  # type: ignore[assignment]
                module.verify_gateway_token = original_verify  # type: ignore[assignment]
            else:
                module.resolve_social_gateway_state = original_resolve  # type: ignore[assignment]
                module.verify_social_gateway_token = original_verify  # type: ignore[assignment]

        return result, fake_client

    async def test_low_result_count_triggers_fallback_and_prefers_better_model(self) -> None:
        primary = _payload(
            text='{"answer":"mini","results":[{"url":"https://x.com/mcp/status/1904234567890123456","text":"one"}]}',
            citations=[{"url": "https://x.com/mcp/status/1904234567890123456", "title": "one"}],
        )
        fallback = _payload(
            text='{"answer":"fast","results":[{"url":"https://x.com/mcp/status/1904234567890123456","text":"one"},{"url":"https://x.com/openai/status/1904234567890123457","text":"two"},{"url":"https://x.com/anthropic/status/1904234567890123458","text":"three"}]}',
            citations=[
                {"url": "https://x.com/mcp/status/1904234567890123456", "title": "one"},
                {"url": "https://x.com/openai/status/1904234567890123457", "title": "two"},
                {"url": "https://x.com/anthropic/status/1904234567890123458", "title": "three"},
            ],
        )

        for module in (social_gateway, proxy_server):
            result, client = await self._run_route(
                module,
                responses=[
                    _FakeResponse(200, primary),
                    _FakeResponse(200, fallback),
                ],
            )
            self.assertEqual(len(client.calls), 2)
            self.assertEqual(client.calls[0]["json"]["model"], "grok-3-mini")
            self.assertEqual(client.calls[1]["json"]["model"], "grok-4.1-fast")
            self.assertEqual(result["tool_usage"]["social_search_calls"], 2)
            self.assertEqual(result["tool_usage"]["model"], "grok-4.1-fast")
            self.assertEqual(result["route"]["selected_model"], "grok-4.1-fast")

    async def test_requested_model_overrides_primary_model(self) -> None:
        primary = _payload(
            text='{"answer":"custom","results":[{"url":"https://x.com/mcp/status/1904234567890123456","text":"one"}]}',
            citations=[{"url": "https://x.com/mcp/status/1904234567890123456", "title": "one"}],
        )

        for module in (social_gateway, proxy_server):
            fake_client = _FakeHttpClient([_FakeResponse(200, primary)])
            request = _FakeRequest(
                {
                    "query": "Model Context Protocol",
                    "source": "x",
                    "max_results": 1,
                    "model": "grok-4.20-beta-latest-non-reasoning",
                }
            )
            original_http_client = module.http_client

            if module is social_gateway:
                original_resolve = module.resolve_gateway_state
                original_verify = module.verify_gateway_token
                module.resolve_gateway_state = _fake_gateway_state  # type: ignore[assignment]
                module.verify_gateway_token = lambda token, accepted_tokens: None  # type: ignore[assignment]
                route = module.social_search
            else:
                original_resolve = module.resolve_social_gateway_state
                original_verify = module.verify_social_gateway_token
                module.resolve_social_gateway_state = _fake_proxy_state  # type: ignore[assignment]
                module.verify_social_gateway_token = lambda token, accepted_tokens: None  # type: ignore[assignment]
                route = module.proxy_social_search

            module.http_client = fake_client
            try:
                result = await route(request)
            finally:
                module.http_client = original_http_client
                if module is social_gateway:
                    module.resolve_gateway_state = original_resolve  # type: ignore[assignment]
                    module.verify_gateway_token = original_verify  # type: ignore[assignment]
                else:
                    module.resolve_social_gateway_state = original_resolve  # type: ignore[assignment]
                    module.verify_social_gateway_token = original_verify  # type: ignore[assignment]

            self.assertEqual(fake_client.calls[0]["json"]["model"], "grok-4.20-beta-latest-non-reasoning")
            self.assertEqual(result["route"]["selected_model"], "grok-4.20-beta-latest-non-reasoning")
            self.assertFalse(result["route"]["fallback"]["triggered"])
            self.assertFalse(result["route"]["fallback"]["used"])
            self.assertEqual(result["route"]["fallback"]["reason"], "")
            self.assertEqual(len(result["results"]), 1)

    async def test_enough_results_keeps_primary_model(self) -> None:
        primary = _payload(
            text='{"answer":"mini","results":[{"url":"https://x.com/mcp/status/1905234567890123456","text":"one"},{"url":"https://x.com/openai/status/1905234567890123457","text":"two"},{"url":"https://x.com/anthropic/status/1905234567890123458","text":"three"}]}',
            citations=[
                {"url": "https://x.com/mcp/status/1905234567890123456", "title": "one"},
                {"url": "https://x.com/openai/status/1905234567890123457", "title": "two"},
                {"url": "https://x.com/anthropic/status/1905234567890123458", "title": "three"},
            ],
        )

        for module in (social_gateway, proxy_server):
            result, client = await self._run_route(
                module,
                responses=[_FakeResponse(200, primary)],
            )
            self.assertEqual(len(client.calls), 1)
            self.assertEqual(result["tool_usage"]["social_search_calls"], 1)
            self.assertEqual(result["tool_usage"]["model"], "grok-3-mini")
            self.assertEqual(result["route"]["selected_model"], "grok-3-mini")
            self.assertFalse(result["route"]["fallback"]["triggered"])
            self.assertFalse(result["route"]["fallback"]["used"])

    async def test_max_results_one_does_not_force_fallback(self) -> None:
        primary = _payload(
            text='{"answer":"mini","results":[{"url":"https://x.com/mcp/status/1906234567890123456","text":"one"}]}',
            citations=[{"url": "https://x.com/mcp/status/1906234567890123456", "title": "one"}],
        )

        for module in (social_gateway, proxy_server):
            result, client = await self._run_route(
                module,
                responses=[_FakeResponse(200, primary)],
                max_results=1,
            )
            self.assertEqual(len(client.calls), 1)
            self.assertEqual(result["tool_usage"]["social_search_calls"], 1)
            self.assertFalse(result["route"]["fallback"]["triggered"])
            self.assertEqual(result["route"]["fallback"]["threshold"], 1)

    async def test_upstream_error_falls_back_to_secondary_model(self) -> None:
        fallback = _payload(
            text='{"answer":"fast","results":[{"url":"https://x.com/mcp/status/1907234567890123456","text":"one"},{"url":"https://x.com/openai/status/1907234567890123457","text":"two"}]}',
            citations=[
                {"url": "https://x.com/mcp/status/1907234567890123456", "title": "one"},
                {"url": "https://x.com/openai/status/1907234567890123457", "title": "two"},
            ],
        )

        for module in (social_gateway, proxy_server):
            result, client = await self._run_route(
                module,
                responses=[
                    _FakeResponse(500, {"error": {"message": "primary unavailable"}}),
                    _FakeResponse(200, fallback),
                ],
            )
            self.assertEqual(len(client.calls), 2)
            self.assertEqual(result["tool_usage"]["social_search_calls"], 2)
            self.assertEqual(result["tool_usage"]["model"], "grok-4.1-fast")
            self.assertEqual(result["route"]["selected_model"], "grok-4.1-fast")
            self.assertEqual(result["route"]["fallback"]["reason"], "upstream_error")
            self.assertTrue(result["route"]["fallback"]["used"])
            self.assertEqual(len(result["results"]), 2)


async def _fake_gateway_state(force: bool = False) -> dict[str, object]:
    return {
        "upstream_base_url": "http://example.test/v1",
        "upstream_responses_path": "/responses",
        "accepted_tokens": ["test-token"],
        "resolved_upstream_api_key": "upstream-key",
        "model": "grok-3-mini",
        "fallback_model": "grok-4.1-fast",
        "fallback_min_results": 3,
    }


async def _fake_proxy_state(force: bool = False) -> dict[str, object]:
    return {
        "upstream_base_url": "http://example.test/v1",
        "upstream_responses_path": "/responses",
        "accepted_tokens": ["test-token"],
        "resolved_upstream_api_key": "upstream-key",
        "model": "grok-3-mini",
        "fallback_model": "grok-4.1-fast",
        "fallback_min_results": 3,
    }


if __name__ == "__main__":
    unittest.main()
