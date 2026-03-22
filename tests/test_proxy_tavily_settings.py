from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main()
