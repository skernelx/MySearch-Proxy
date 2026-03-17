import unittest
from unittest.mock import Mock

from mysearch.clients import MySearchClient, MySearchError


class ExtractUrlFallbackTests(unittest.TestCase):
    def test_auto_extract_falls_back_when_firecrawl_returns_empty_content(self) -> None:
        client = object.__new__(MySearchClient)
        client._scrape_firecrawl = Mock(
            return_value={
                "provider": "firecrawl",
                "url": "https://example.com/post",
                "content": "   ",
                "metadata": {"source": "firecrawl"},
            }
        )
        client._extract_tavily = Mock(
            return_value={
                "provider": "tavily",
                "url": "https://example.com/post",
                "content": "Tavily extracted content",
                "metadata": {"request_id": "req_123"},
            }
        )

        result = MySearchClient.extract_url(client, url="https://example.com/post")

        self.assertEqual(result["provider"], "tavily")
        self.assertEqual(result["fallback"]["from"], "firecrawl")
        self.assertIn("empty content", result["fallback"]["reason"])
        self.assertEqual(result["metadata"]["fallback_from"], "firecrawl")
        self.assertIn("empty content", result["metadata"]["fallback_reason"])

    def test_auto_extract_falls_back_when_firecrawl_raises(self) -> None:
        client = object.__new__(MySearchClient)
        client._scrape_firecrawl = Mock(side_effect=MySearchError("firecrawl unavailable"))
        client._extract_tavily = Mock(
            return_value={
                "provider": "tavily",
                "url": "https://example.com/post",
                "content": "Recovered by Tavily",
                "metadata": {},
            }
        )

        result = MySearchClient.extract_url(client, url="https://example.com/post")

        self.assertEqual(result["provider"], "tavily")
        self.assertEqual(result["fallback"]["from"], "firecrawl")
        self.assertIn("firecrawl unavailable", result["fallback"]["reason"])

    def test_explicit_firecrawl_keeps_result_and_surfaces_warning(self) -> None:
        client = object.__new__(MySearchClient)
        client._scrape_firecrawl = Mock(
            return_value={
                "provider": "firecrawl",
                "url": "https://example.com/post",
                "content": "",
                "metadata": {},
            }
        )
        client._extract_tavily = Mock()

        result = MySearchClient.extract_url(
            client,
            url="https://example.com/post",
            provider="firecrawl",
        )

        self.assertEqual(result["provider"], "firecrawl")
        self.assertIn("empty content", result["warning"])
        self.assertIn("empty content", result["metadata"]["warning"])
        client._extract_tavily.assert_not_called()


if __name__ == "__main__":
    unittest.main()
