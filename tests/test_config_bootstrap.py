from __future__ import annotations

import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_module(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class ConfigBootstrapTests(unittest.TestCase):
    def _preserve_env(self, *keys: str) -> dict[str, str | None]:
        snapshot: dict[str, str | None] = {}
        for key in keys:
            snapshot[key] = os.environ.get(key)
            os.environ.pop(key, None)
        return snapshot

    def _restore_env(self, snapshot: dict[str, str | None]) -> None:
        for key, value in snapshot.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_codex_config_env_wins_over_dotenv_and_dotenv_fills_missing_values(self) -> None:
        snapshot = self._preserve_env(
            "CODEX_HOME",
            "MYSEARCH_PROXY_BASE_URL",
            "MYSEARCH_PROXY_API_KEY",
            "MYSEARCH_TIMEOUT_SECONDS",
        )
        try:
            with TemporaryDirectory() as tmpdir:
                temp_root = Path(tmpdir)
                codex_home = temp_root / ".codex"
                codex_home.mkdir(parents=True)
                (codex_home / "config.toml").write_text(
                    """
[mcp_servers.mysearch]
command = "python3"

[mcp_servers.mysearch.env]
MYSEARCH_PROXY_BASE_URL = "https://config.example.com"
MYSEARCH_PROXY_API_KEY = "config-token"
""".strip(),
                    encoding="utf-8",
                )

                module_dir = temp_root / "mysearch"
                module_dir.mkdir(parents=True)
                (module_dir / ".env").write_text(
                    "\n".join(
                        [
                            "MYSEARCH_PROXY_BASE_URL=https://dotenv.example.com",
                            "MYSEARCH_PROXY_API_KEY=dotenv-token",
                            "MYSEARCH_TIMEOUT_SECONDS=91",
                        ]
                    ),
                    encoding="utf-8",
                )

                os.environ["CODEX_HOME"] = str(codex_home)
                module = _load_module(
                    "test_mysearch_config_bootstrap",
                    REPO_ROOT / "mysearch" / "config.py",
                )
                module.MODULE_DIR = module_dir
                module.ROOT_DIR = temp_root
                module._bootstrap_runtime_env()

                self.assertEqual(
                    os.environ.get("MYSEARCH_PROXY_BASE_URL"),
                    "https://config.example.com",
                )
                self.assertEqual(
                    os.environ.get("MYSEARCH_PROXY_API_KEY"),
                    "config-token",
                )
                self.assertEqual(os.environ.get("MYSEARCH_TIMEOUT_SECONDS"), "91")
        finally:
            self._restore_env(snapshot)

    def test_openclaw_wrapper_reads_skill_env_from_openclaw_json(self) -> None:
        snapshot = self._preserve_env(
            "OPENCLAW_CONFIG_PATH",
            "MYSEARCH_PROXY_BASE_URL",
            "MYSEARCH_PROXY_API_KEY",
        )
        try:
            with TemporaryDirectory() as tmpdir:
                temp_root = Path(tmpdir)
                state_dir = temp_root / ".openclaw"
                skill_dir = state_dir / "skills" / "mysearch"
                skill_dir.mkdir(parents=True)
                (state_dir / "openclaw.json").write_text(
                    json.dumps(
                        {
                            "skills": {
                                "entries": {
                                    "mysearch": {
                                        "env": {
                                            "MYSEARCH_PROXY_BASE_URL": "https://openclaw.example.com",
                                            "MYSEARCH_PROXY_API_KEY": "openclaw-token",
                                        }
                                    }
                                }
                            }
                        }
                    ),
                    encoding="utf-8",
                )

                module = _load_module(
                    "test_mysearch_openclaw_wrapper",
                    REPO_ROOT / "openclaw" / "scripts" / "mysearch_openclaw.py",
                )
                module._load_openclaw_skill_env(skill_dir)

                self.assertEqual(
                    os.environ.get("MYSEARCH_PROXY_BASE_URL"),
                    "https://openclaw.example.com",
                )
                self.assertEqual(
                    os.environ.get("MYSEARCH_PROXY_API_KEY"),
                    "openclaw-token",
                )
        finally:
            self._restore_env(snapshot)


if __name__ == "__main__":
    unittest.main()
