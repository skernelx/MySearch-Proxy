from __future__ import annotations

import importlib.util
import os
import sqlite3
import sys
import tempfile
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


class ProxyDatabasePathTests(unittest.TestCase):
    def test_get_conn_honors_env_db_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "custom-data" / "proxy.db"
            with patch.dict(os.environ, {"MYSEARCH_PROXY_DB_PATH": str(db_path)}):
                module = _load_module(
                    "test_proxy_database_path_module",
                    PROXY_ROOT / "database.py",
                )
                module.init_db()
                self.assertEqual(Path(module.get_db_path()), db_path)
                self.assertTrue(db_path.exists())

                conn = sqlite3.connect(db_path)
                try:
                    rows = conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                    ).fetchall()
                finally:
                    conn.close()

        self.assertIn(("api_keys",), rows)
        self.assertIn(("tokens",), rows)
        self.assertIn(("settings",), rows)


if __name__ == "__main__":
    unittest.main()
