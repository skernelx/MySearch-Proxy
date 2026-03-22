from __future__ import annotations

import asyncio
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
PROXY_ROOT = REPO_ROOT / "proxy"


def _load_module(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class ProxyTavilySettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if str(PROXY_ROOT) not in sys.path:
            sys.path.insert(0, str(PROXY_ROOT))
        cls.module = _load_module(
            "test_proxy_server_tavily_settings",
            PROXY_ROOT / "server.py",
        )

    def test_get_runtime_tavily_config_defaults_to_auto(self) -> None:
        with patch.object(self.module.db, "get_setting", side_effect=lambda _key, default=None: default):
            config = self.module.get_runtime_tavily_config()

        self.assertEqual(config["mode"], "auto")
        self.assertEqual(config["upstream_base_url"], "https://api.tavily.com")
        self.assertEqual(config["upstream_search_path"], "/search")
        self.assertEqual(config["upstream_extract_path"], "/extract")
        self.assertEqual(config["upstream_api_key"], "")

    def test_get_runtime_tavily_config_reads_upstream_settings(self) -> None:
        values = {
            "tavily_mode": "upstream",
            "tavily_upstream_base_url": "http://127.0.0.1:8787/api/tavily",
            "tavily_upstream_search_path": "/search",
            "tavily_upstream_extract_path": "/extract",
            "tavily_upstream_api_key": "th-demo-token",
        }

        def fake_get_setting(key, default=None):
            return values.get(key, default)

        with patch.object(self.module.db, "get_setting", side_effect=fake_get_setting):
            config = self.module.get_runtime_tavily_config()

        self.assertEqual(config["mode"], "upstream")
        self.assertEqual(config["upstream_base_url"], "http://127.0.0.1:8787/api/tavily")
        self.assertEqual(config["upstream_api_key"], "th-demo-token")

    def test_usage_sync_meta_is_disabled_in_tavily_upstream_mode(self) -> None:
        values = {
            "tavily_mode": "upstream",
            "tavily_upstream_base_url": "http://127.0.0.1:8787/api/tavily",
            "tavily_upstream_api_key": "th-demo-token",
        }

        def fake_get_setting(key, default=None):
            return values.get(key, default)

        with patch.object(self.module.db, "get_setting", side_effect=fake_get_setting):
            meta = self.module.build_usage_sync_meta_for_dashboard("tavily", [{"id": 1}, {"id": 2}])

        self.assertFalse(meta["supported"])
        self.assertEqual(meta["requested"], 2)
        self.assertIn("上游 Gateway", meta["detail"])

    def test_probe_tavily_connection_falls_back_to_api_tavily_on_404(self) -> None:
        config = {
            "mode": "upstream",
            "upstream_base_url": "http://127.0.0.1:8787",
            "upstream_search_path": "/search",
            "upstream_extract_path": "/extract",
            "upstream_api_key": "gateway-token-without-th-prefix",
        }

        class _Response:
            def __init__(self, status_code, payload):
                self.status_code = status_code
                self._payload = payload
                self.headers = {"content-type": "application/json"}
                self.text = ""

            def json(self):
                return self._payload

        async def _run():
            responses = [
                _Response(404, {"detail": "Not Found"}),
                _Response(200, {"results": [{"title": "ok"}]}),
            ]
            call_urls = []

            async def fake_post(url, json):
                call_urls.append(url)
                return responses.pop(0)

            with patch.object(self.module, "http_client") as fake_client:
                fake_client.post.side_effect = fake_post
                return await self.module.probe_tavily_connection(config, [])

        result = asyncio.run(_run())
        self.assertTrue(result["ok"])
        self.assertEqual(result["request_target"], "http://127.0.0.1:8787/api/tavily/search")
        self.assertIn("/api/tavily", result["detail"])


if __name__ == "__main__":
    unittest.main()
