from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request


def _normalize_base_url(value: str) -> str:
    return value.rstrip("/")


def main() -> int:
    base_url = _normalize_base_url(os.environ.get("MYSEARCH_PROXY_BASE_URL", ""))
    bootstrap_token = os.environ.get("MYSEARCH_PROXY_BOOTSTRAP_TOKEN", "").strip()
    token_name = os.environ.get("MYSEARCH_PROXY_BOOTSTRAP_NAME", "docker-mysearch").strip() or "docker-mysearch"
    timeout_seconds = max(1.0, float(os.environ.get("MYSEARCH_PROXY_BOOTSTRAP_TIMEOUT_SECONDS", "60")))
    interval_seconds = max(0.2, float(os.environ.get("MYSEARCH_PROXY_BOOTSTRAP_INTERVAL_SECONDS", "1.5")))

    if not base_url:
        print("Missing MYSEARCH_PROXY_BASE_URL for proxy token bootstrap.", file=sys.stderr)
        return 1
    if not bootstrap_token:
        print("Missing MYSEARCH_PROXY_BOOTSTRAP_TOKEN for proxy token bootstrap.", file=sys.stderr)
        return 1

    target = f"{base_url}/api/internal/mysearch/token"
    payload = json.dumps({"name": token_name}).encode("utf-8")
    deadline = time.time() + timeout_seconds
    last_error = "proxy token bootstrap did not start"

    while time.time() < deadline:
        request = urllib.request.Request(
            target,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {bootstrap_token}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                data = json.loads(response.read().decode("utf-8"))
                token = (data.get("token") or "").strip()
                if token:
                    print(token)
                    return 0
                last_error = "bootstrap endpoint returned empty token"
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace").strip()
            last_error = f"HTTP {exc.code}: {detail or exc.reason}"
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
        time.sleep(interval_seconds)

    print(f"Failed to bootstrap MYSEARCH_PROXY_API_KEY: {last_error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
