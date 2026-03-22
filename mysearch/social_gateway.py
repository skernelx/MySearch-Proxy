"""Structured social search gateway for MySearch compatible mode."""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import re
import time
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlparse

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request


def _env_str(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _normalize_path(value: str, default: str) -> str:
    normalized = value.strip() or default
    if not normalized.startswith("/"):
        return f"/{normalized}"
    return normalized


def _derive_admin_base_url(upstream_base_url: str) -> str:
    if upstream_base_url.endswith("/v1"):
        return upstream_base_url[:-3]
    return upstream_base_url


UPSTREAM_BASE_URL = _env_str("SOCIAL_GATEWAY_UPSTREAM_BASE_URL", "https://api.x.ai/v1").rstrip("/")
UPSTREAM_RESPONSES_PATH = _normalize_path(
    _env_str("SOCIAL_GATEWAY_UPSTREAM_RESPONSES_PATH", "/responses"),
    "/responses",
)
UPSTREAM_API_KEY = _env_str("SOCIAL_GATEWAY_UPSTREAM_API_KEY")
MODEL = _env_str("SOCIAL_GATEWAY_MODEL", "grok-4.1-fast")
FALLBACK_MODEL = _env_str("SOCIAL_GATEWAY_FALLBACK_MODEL", "grok-4.1-fast")
GATEWAY_TOKEN = _env_str("SOCIAL_GATEWAY_TOKEN")
ADMIN_BASE_URL = _env_str("SOCIAL_GATEWAY_ADMIN_BASE_URL") or _derive_admin_base_url(UPSTREAM_BASE_URL)
ADMIN_VERIFY_PATH = _normalize_path(
    _env_str("SOCIAL_GATEWAY_ADMIN_VERIFY_PATH", "/v1/admin/verify"),
    "/v1/admin/verify",
)
ADMIN_CONFIG_PATH = _normalize_path(
    _env_str("SOCIAL_GATEWAY_ADMIN_CONFIG_PATH", "/v1/admin/config"),
    "/v1/admin/config",
)
ADMIN_TOKENS_PATH = _normalize_path(
    _env_str("SOCIAL_GATEWAY_ADMIN_TOKENS_PATH", "/v1/admin/tokens"),
    "/v1/admin/tokens",
)
ADMIN_APP_KEY = _env_str("SOCIAL_GATEWAY_ADMIN_APP_KEY")
CACHE_TTL_SECONDS = max(5, int(_env_str("SOCIAL_GATEWAY_CACHE_TTL_SECONDS", "60")))
try:
    FALLBACK_MIN_RESULTS = max(1, int(_env_str("SOCIAL_GATEWAY_FALLBACK_MIN_RESULTS", "3")))
except (TypeError, ValueError):
    FALLBACK_MIN_RESULTS = 3
SOCIAL_SEARCH_MODEL = MODEL

http_client = httpx.AsyncClient(timeout=60)
state_cache: dict[str, Any] = {"expires_at": 0.0, "value": None}
state_lock: asyncio.Lock | None = None


def get_state_lock() -> asyncio.Lock:
    global state_lock
    if state_lock is None:
        state_lock = asyncio.Lock()
    return state_lock


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        yield
    finally:
        await http_client.aclose()


app = FastAPI(title="MySearch Social Gateway", lifespan=lifespan)


def extract_token(request: Request, body: dict[str, Any] | None) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    if body and isinstance(body.get("api_key"), str):
        return body["api_key"]
    return None


def unique_preserve_order(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in values:
        value = (item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def parse_secret_values(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return unique_preserve_order(re.split(r"[\n,]", value))
    if isinstance(value, (list, tuple, set)):
        return unique_preserve_order([str(item) for item in value])
    return []


def build_empty_social_stats() -> dict[str, Any]:
    return {
        "token_total": 0,
        "token_normal": 0,
        "token_limited": 0,
        "token_invalid": 0,
        "chat_remaining": 0,
        "image_remaining": 0,
        "video_remaining": None,
        "total_calls": 0,
        "nsfw_enabled": 0,
        "nsfw_disabled": 0,
        "pool_count": 0,
        "pools": [],
    }


def _parse_int(value: Any) -> int:
    if value in (None, ""):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return 0


def mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 4:
        return "*" * len(value)
    if len(value) <= 8:
        return f"{value[:2]}***{value[-2:]}"
    if len(value) <= 12:
        return f"{value[:3]}***{value[-3:]}"
    return f"{value[:6]}***{value[-4:]}"


def unwrap_social_tokens_payload(tokens_payload: Any) -> Any:
    if isinstance(tokens_payload, dict):
        for key_name in ("tokens", "data", "items", "result", "pools"):
            candidate = tokens_payload.get(key_name)
            if isinstance(candidate, dict):
                return candidate
            if isinstance(candidate, list):
                return {"default": candidate}
        return tokens_payload
    if isinstance(tokens_payload, list):
        return {"default": tokens_payload}
    return {}


def flatten_social_tokens(tokens_payload: Any) -> list[dict[str, Any]]:
    flat: list[dict[str, Any]] = []
    normalized = unwrap_social_tokens_payload(tokens_payload)
    if not isinstance(normalized, dict):
        return flat

    for pool_name, items in normalized.items():
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, str):
                token_value = item
                status = "active"
                quota = 0
                use_count = 0
                tags: list[str] = []
            elif isinstance(item, dict):
                token_value = str(item.get("token") or "")
                status = str(item.get("status") or "active").strip().lower()
                quota = max(0, _parse_int(item.get("quota")))
                use_count = max(0, _parse_int(item.get("use_count")))
                raw_tags = item.get("tags") or []
                tags = (
                    [str(tag).strip() for tag in raw_tags if str(tag).strip()]
                    if isinstance(raw_tags, list)
                    else []
                )
            else:
                continue

            flat.append(
                {
                    "pool": str(pool_name),
                    "token_masked": mask_secret(token_value),
                    "status": status,
                    "quota": quota,
                    "use_count": use_count,
                    "tags": tags,
                }
            )
    return flat


def build_social_token_stats(tokens_payload: Any) -> dict[str, Any]:
    stats = build_empty_social_stats()
    flat_tokens = flatten_social_tokens(tokens_payload)

    if not flat_tokens:
        return stats

    active_tokens = [item for item in flat_tokens if item["status"] == "active"]
    cooling_tokens = [item for item in flat_tokens if item["status"] == "cooling"]
    invalid_tokens = [
        item for item in flat_tokens if item["status"] not in {"active", "cooling"}
    ]
    chat_remaining = sum(item["quota"] for item in active_tokens)
    pools: dict[str, dict[str, Any]] = {}
    for item in flat_tokens:
        pool = pools.setdefault(
            item["pool"],
            {"pool": item["pool"], "count": 0, "active": 0, "cooling": 0, "invalid": 0},
        )
        pool["count"] += 1
        if item["status"] == "active":
            pool["active"] += 1
        elif item["status"] == "cooling":
            pool["cooling"] += 1
        else:
            pool["invalid"] += 1

    stats.update(
        {
            "token_total": len(flat_tokens),
            "token_normal": len(active_tokens),
            "token_limited": len(cooling_tokens),
            "token_invalid": len(invalid_tokens),
            "chat_remaining": chat_remaining,
            "image_remaining": chat_remaining // 2,
            "total_calls": sum(item["use_count"] for item in flat_tokens),
            "nsfw_enabled": sum("nsfw" in item["tags"] for item in flat_tokens),
            "nsfw_disabled": sum("nsfw" not in item["tags"] for item in flat_tokens),
            "pool_count": len(pools),
            "pools": sorted(pools.values(), key=lambda item: item["pool"]),
        }
    )
    return stats


def build_gateway_mode(state: dict[str, Any]) -> str:
    if state["admin_connected"] and (state["manual_upstream_key"] or state["manual_gateway_token"]):
        return "hybrid"
    if state["admin_connected"]:
        return "admin-auto"
    return "manual"


def build_token_source(state: dict[str, Any]) -> str:
    if state["manual_gateway_token"]:
        return "manual SOCIAL_GATEWAY_TOKEN"
    if state["admin_connected"] and state["admin_api_keys"]:
        return "grok2api app.api_key"
    if state["manual_upstream_key"]:
        return "SOCIAL_GATEWAY_UPSTREAM_API_KEY"
    return "not_configured"


async def fetch_admin_json(path: str) -> dict[str, Any]:
    if not ADMIN_APP_KEY:
        raise RuntimeError("Missing SOCIAL_GATEWAY_ADMIN_APP_KEY")
    response = await http_client.get(
        f"{ADMIN_BASE_URL}{path}",
        headers={"Authorization": f"Bearer {ADMIN_APP_KEY}"},
    )
    try:
        payload = response.json()
    except Exception:
        payload = None
    if response.status_code >= 400:
        detail = ""
        if isinstance(payload, dict):
            detail = str(payload.get("detail") or payload.get("message") or "")
        if not detail:
            detail = response.text[:240] or f"HTTP {response.status_code}"
        raise RuntimeError(f"{path} -> {detail}")
    return payload if isinstance(payload, dict) else {}


async def resolve_gateway_state(force: bool = False) -> dict[str, Any]:
    now = time.time()
    cached = state_cache.get("value")
    if not force and cached and state_cache.get("expires_at", 0) > now:
        return cached

    async with get_state_lock():
        now = time.time()
        cached = state_cache.get("value")
        if not force and cached and state_cache.get("expires_at", 0) > now:
            return cached

        state = {
            "upstream_base_url": UPSTREAM_BASE_URL,
            "upstream_responses_path": UPSTREAM_RESPONSES_PATH,
            "admin_base_url": ADMIN_BASE_URL,
            "admin_verify_path": ADMIN_VERIFY_PATH,
            "admin_config_path": ADMIN_CONFIG_PATH,
            "admin_tokens_path": ADMIN_TOKENS_PATH,
            "manual_upstream_key": bool(UPSTREAM_API_KEY),
            "manual_gateway_token": bool(GATEWAY_TOKEN),
            "upstream_api_keys": parse_secret_values(UPSTREAM_API_KEY),
            "accepted_tokens": parse_secret_values(GATEWAY_TOKEN),
            "admin_api_keys": [],
            "resolved_upstream_api_key": "",
            "stats": build_empty_social_stats(),
            "admin_configured": bool(ADMIN_BASE_URL and ADMIN_APP_KEY),
            "admin_connected": False,
            "token_source": "not_configured",
            "error": "",
            "mode": "manual",
            "model": MODEL,
            "fallback_model": FALLBACK_MODEL,
            "fallback_min_results": FALLBACK_MIN_RESULTS,
        }

        if state["admin_configured"]:
            try:
                admin_config, admin_tokens = await asyncio.gather(
                    fetch_admin_json(ADMIN_CONFIG_PATH),
                    fetch_admin_json(ADMIN_TOKENS_PATH),
                )
                admin_api_keys = parse_secret_values((admin_config.get("app") or {}).get("api_key"))
                state["admin_connected"] = True
                state["admin_api_keys"] = admin_api_keys
                if not state["upstream_api_keys"]:
                    state["upstream_api_keys"] = admin_api_keys
                if not state["accepted_tokens"]:
                    state["accepted_tokens"] = admin_api_keys
                state["stats"] = build_social_token_stats(admin_tokens)
            except Exception as exc:
                state["error"] = str(exc)

        if not state["accepted_tokens"] and state["upstream_api_keys"]:
            state["accepted_tokens"] = list(state["upstream_api_keys"])

        state["upstream_api_keys"] = unique_preserve_order(state["upstream_api_keys"])
        state["accepted_tokens"] = unique_preserve_order(state["accepted_tokens"])
        state["resolved_upstream_api_key"] = state["upstream_api_keys"][0] if state["upstream_api_keys"] else ""
        state["token_source"] = build_token_source(state)
        state["mode"] = build_gateway_mode(state)

        state_cache["value"] = state
        state_cache["expires_at"] = now + CACHE_TTL_SECONDS
        return state


def verify_gateway_token(token_value: str | None, accepted_tokens: list[str]) -> None:
    if not accepted_tokens:
        raise HTTPException(status_code=503, detail="Social gateway is not configured")
    if not token_value:
        raise HTTPException(status_code=401, detail="Missing API token")
    if not any(hmac.compare_digest(token_value, expected) for expected in accepted_tokens):
        raise HTTPException(status_code=401, detail="Invalid token")


def extract_response_text(payload: dict[str, Any]) -> str:
    if isinstance(payload.get("output_text"), str) and payload.get("output_text").strip():
        return payload["output_text"].strip()

    parts: list[str] = []
    for item in payload.get("output", []) or []:
        if not isinstance(item, dict):
            continue
        content = item.get("content") or []
        if isinstance(content, str) and content.strip():
            parts.append(content.strip())
            continue
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
                continue
            if isinstance(text, dict) and isinstance(text.get("value"), str) and text["value"].strip():
                parts.append(text["value"].strip())
    return "\n".join(parts).strip()


def extract_json_object(text: str) -> dict[str, Any]:
    candidates: list[str] = []
    stripped = text.strip()
    if stripped:
        candidates.append(stripped)

    fenced = re.findall(r"```(?:json)?\s*(.*?)```", stripped, flags=re.DOTALL | re.IGNORECASE)
    candidates.extend(item.strip() for item in fenced if item.strip())

    decoder = json.JSONDecoder()
    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            parsed = json.loads(candidate)
        except Exception:
            parsed = None
        if isinstance(parsed, dict):
            return parsed

        start = candidate.find("{")
        while start != -1:
            try:
                parsed, _ = decoder.raw_decode(candidate[start:])
            except Exception:
                start = candidate.find("{", start + 1)
                continue
            if isinstance(parsed, dict):
                return parsed
            start = candidate.find("{", start + 1)
    return {}


def normalize_citation(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    url = item.get("url") or item.get("target_url") or item.get("link") or item.get("source_url") or ""
    title = (
        item.get("title")
        or item.get("source_title")
        or item.get("display_text")
        or item.get("text")
        or ""
    )
    if not url and not title:
        return None
    normalized = dict(item)
    normalized["url"] = url
    normalized["title"] = title
    return normalized


def extract_upstream_citations(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_citations = payload.get("citations") or []
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()

    if isinstance(raw_citations, list):
        for item in raw_citations:
            citation = normalize_citation(item)
            if citation is None:
                continue
            url = citation.get("url", "")
            if url and url in seen:
                continue
            if url:
                seen.add(url)
            normalized.append(citation)

    if normalized:
        return normalized

    for output_item in payload.get("output", []) or []:
        if not isinstance(output_item, dict):
            continue
        content_items = output_item.get("content") or []
        if not isinstance(content_items, list):
            continue
        for content_item in content_items:
            if not isinstance(content_item, dict):
                continue
            for annotation in content_item.get("annotations") or []:
                citation = normalize_citation(annotation)
                if citation is None:
                    continue
                url = citation.get("url", "")
                if url and url in seen:
                    continue
                if url:
                    seen.add(url)
                normalized.append(citation)
    return normalized


def normalize_result_item(item: Any) -> dict[str, str] | None:
    if not isinstance(item, dict):
        return None
    url = (item.get("url") or item.get("link") or "").strip()
    title = (item.get("title") or item.get("author") or item.get("handle") or url).strip()
    text = (
        item.get("text")
        or item.get("content")
        or item.get("body")
        or item.get("snippet")
        or item.get("summary")
        or ""
    ).strip()
    result = {
        "title": title,
        "url": url,
        "text": text,
        "content": (item.get("content") or text).strip(),
        "snippet": (item.get("snippet") or item.get("summary") or text).strip(),
        "author": (item.get("author") or item.get("username") or item.get("handle") or "").strip(),
        "handle": (item.get("handle") or item.get("username") or "").strip().lstrip("@"),
        "created_at": (item.get("created_at") or item.get("published_at") or "").strip(),
        "why_relevant": (item.get("why_relevant") or item.get("reason") or "").strip(),
    }
    if not result["url"] and not result["title"] and not result["text"]:
        return None
    return result


SOCIAL_HOST_ALIASES = {
    "x.com",
    "www.x.com",
    "twitter.com",
    "www.twitter.com",
    "mobile.x.com",
    "mobile.twitter.com",
}


def looks_synthetic_social_status_id(status_id: str) -> bool:
    digits = (status_id or "").strip()
    if len(digits) < 12 or not digits.isdigit():
        return False

    repeated_sequences = [
        "0123456789" * 4,
        "1234567890" * 4,
        "9876543210" * 4,
        "0987654321" * 4,
        "".join(f"{i}{i}" for i in range(10)) * 3,
        "".join(f"{i}{i}" for i in range(9, -1, -1)) * 3,
    ]
    for sequence in repeated_sequences:
        if digits in sequence or digits[:-1] in sequence:
            return True

    for size in range(1, 5):
        pattern = digits[:size]
        if pattern and (pattern * ((len(digits) // size) + 1))[: len(digits)] == digits:
            return True
    return False


def normalize_social_match_url(url: str) -> str:
    raw_url = (url or "").strip()
    if not raw_url:
        return ""
    try:
        parsed = urlparse(raw_url)
    except Exception:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""

    host = parsed.netloc.lower()
    path = re.sub(r"/+", "/", parsed.path or "/").rstrip("/")
    if not path:
        path = "/"

    if host not in SOCIAL_HOST_ALIASES:
        return ""

    parts = [part for part in path.split("/") if part]
    if len(parts) < 3 or parts[1].lower() != "status" or not parts[2].isdigit():
        return ""
    if looks_synthetic_social_status_id(parts[2]):
        return ""
    handle = parts[0].lstrip("@").lower()
    return f"https://x.com/{handle}/status/{parts[2]}"


def is_supported_social_result_url(url: str) -> bool:
    return bool(normalize_social_match_url(url))


def build_trusted_social_citations(payload: dict[str, Any]) -> list[dict[str, str]]:
    citations: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in extract_upstream_citations(payload):
        url = (item.get("url") or "").strip()
        match_url = normalize_social_match_url(url)
        if not match_url or match_url in seen:
            continue
        seen.add(match_url)
        citations.append(
            {
                "title": (item.get("title") or "").strip(),
                "url": url,
                "match_url": match_url,
            }
        )
    return citations


def build_social_result(
    citation: dict[str, str] | None = None,
    matched: dict[str, str] | None = None,
) -> dict[str, str]:
    citation = citation or {}
    matched = matched or {}
    url = (citation.get("url") or matched.get("url") or "").strip()
    title = (
        citation.get("title")
        or matched.get("title")
        or matched.get("author")
        or matched.get("handle")
        or url
    ).strip()
    text = (matched.get("text") or "").strip()
    content = (matched.get("content") or text).strip()
    snippet = (matched.get("snippet") or text).strip()
    author = (matched.get("author") or "").strip()
    handle = (matched.get("handle") or "").strip().lstrip("@")
    created_at = (matched.get("created_at") or "").strip()
    why_relevant = (matched.get("why_relevant") or "").strip()
    return {
        "title": title,
        "url": url,
        "text": text,
        "content": content,
        "snippet": snippet,
        "author": author,
        "handle": handle,
        "created_at": created_at,
        "why_relevant": why_relevant,
    }


def build_upstream_payload(body: dict[str, Any], model: str | None = None) -> tuple[dict[str, Any], int]:
    query = (body.get("query") or "").strip()
    max_results = max(1, min(int(body.get("max_results") or 5), 10))
    tools: list[dict[str, Any]] = [{"type": "x_search"}]
    tool = tools[0]
    if body.get("allowed_x_handles"):
        tool["allowed_x_handles"] = body["allowed_x_handles"]
    if body.get("excluded_x_handles"):
        tool["excluded_x_handles"] = body["excluded_x_handles"]
    if body.get("from_date"):
        tool["from_date"] = body["from_date"]
    if body.get("to_date"):
        tool["to_date"] = body["to_date"]
    if body.get("include_x_images"):
        tool["enable_image_understanding"] = True
    if body.get("include_x_videos"):
        tool["enable_video_understanding"] = True

    prompt = (
        "Use x_search to find relevant X posts.\n"
        f"Query: {query}\n"
        f'Return JSON only with this schema and no markdown: {{"answer": string, "results": [{{"title": string, '
        f'"url": string, "text": string, "author": string, "handle": string, "created_at": string, '
        f'"why_relevant": string}}]}}.\n'
        f"Return up to {max_results} results. Prefer direct x.com status URLs. "
        "Use empty strings for unknown fields."
    )
    return (
        {
            "model": (model or MODEL).strip() or MODEL,
            "input": [{"role": "user", "content": prompt}],
            "tools": tools,
            "temperature": 0,
            "store": False,
        },
        max_results,
    )


def build_social_search_upstream_payload(body: dict[str, Any]) -> dict[str, Any]:
    payload, _ = build_upstream_payload(body)
    return payload


def count_social_results(payload: dict[str, Any] | None) -> int:
    return len((payload or {}).get("results") or [])


def count_social_citations(payload: dict[str, Any] | None) -> int:
    return len((payload or {}).get("citations") or [])


def build_social_attempt_summary(
    model: str,
    ok: bool,
    *,
    response: dict[str, Any] | None = None,
    error: str = "",
    status_code: int | None = None,
    latency_ms: int | None = None,
) -> dict[str, Any]:
    attempt: dict[str, Any] = {
        "model": model,
        "ok": bool(ok),
        "status_code": status_code,
        "result_count": count_social_results(response),
        "citation_count": count_social_citations(response),
    }
    if latency_ms is not None:
        attempt["latency_ms"] = latency_ms
    if error:
        attempt["error"] = error
    if response is not None:
        attempt["response"] = response
    return attempt


def has_social_fallback(primary_model: str, fallback_model: str) -> bool:
    primary = (primary_model or "").strip()
    fallback = (fallback_model or "").strip()
    return bool(primary and fallback and fallback != primary)


def should_retry_social_with_fallback(
    primary_model: str,
    fallback_model: str,
    response: dict[str, Any] | None,
    min_results: int,
) -> tuple[bool, str]:
    if not has_social_fallback(primary_model, fallback_model):
        return False, ""
    threshold = max(1, int(min_results or 1))
    if count_social_results(response) >= threshold:
        return False, ""
    return True, "result_count_below_threshold"


def choose_preferred_social_attempt(
    primary_attempt: dict[str, Any] | None,
    fallback_attempt: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not fallback_attempt or not fallback_attempt.get("ok"):
        return primary_attempt
    if not primary_attempt or not primary_attempt.get("ok"):
        return fallback_attempt

    primary_count = int(primary_attempt.get("result_count") or 0)
    fallback_count = int(fallback_attempt.get("result_count") or 0)
    if fallback_count > primary_count:
        return fallback_attempt

    primary_citations = int(primary_attempt.get("citation_count") or 0)
    fallback_citations = int(fallback_attempt.get("citation_count") or 0)
    if fallback_count == primary_count and fallback_citations > primary_citations:
        return fallback_attempt

    return primary_attempt


def build_social_route_metadata(
    selected_attempt: dict[str, Any] | None,
    attempts: list[dict[str, Any]],
    *,
    fallback_model: str,
    fallback_reason: str,
    fallback_min_results: int,
) -> dict[str, Any]:
    primary_model = attempts[0]["model"] if attempts else ""
    selected_model = (selected_attempt or {}).get("model") or primary_model
    route_attempts: list[dict[str, Any]] = []
    for item in attempts:
        route_item: dict[str, Any] = {
            "model": item.get("model", ""),
            "ok": bool(item.get("ok")),
            "status_code": item.get("status_code"),
            "result_count": int(item.get("result_count") or 0),
            "citation_count": int(item.get("citation_count") or 0),
        }
        if item.get("latency_ms") is not None:
            route_item["latency_ms"] = item["latency_ms"]
        if item.get("error"):
            route_item["error"] = item["error"]
        route_attempts.append(route_item)

    fallback_attempted = len(attempts) > 1
    fallback_target = attempts[1]["model"] if fallback_attempted else (fallback_model or "").strip()
    return {
        "selected_model": selected_model,
        "attempt_count": len(attempts),
        "attempts": route_attempts,
        "fallback": {
            "configured": has_social_fallback(primary_model, fallback_model),
            "triggered": fallback_attempted,
            "used": bool(fallback_attempted and selected_model == fallback_target),
            "reason": fallback_reason or "",
            "threshold": max(1, int(fallback_min_results or 1)),
            "from": primary_model,
            "to": fallback_target,
            "selected_model": selected_model,
        },
    }


def attach_social_route_metadata(
    response: dict[str, Any] | None,
    selected_attempt: dict[str, Any] | None,
    attempts: list[dict[str, Any]],
    *,
    fallback_model: str,
    fallback_reason: str,
    fallback_min_results: int,
) -> dict[str, Any]:
    payload = dict(response or {})
    tool_usage = dict(payload.get("tool_usage") or {})
    tool_usage["social_search_calls"] = len(attempts)
    tool_usage["model"] = (selected_attempt or {}).get("model") or tool_usage.get("model") or ""
    payload["tool_usage"] = tool_usage
    payload["route"] = build_social_route_metadata(
        selected_attempt,
        attempts,
        fallback_model=fallback_model,
        fallback_reason=fallback_reason,
        fallback_min_results=fallback_min_results,
    )
    return payload


def extract_social_upstream_error(
    upstream_body: dict[str, Any] | Any,
    fallback_detail: str = "Social search failed",
) -> str:
    detail = ""
    if isinstance(upstream_body, dict):
        error = upstream_body.get("error") or {}
        if isinstance(error, dict):
            detail = error.get("message") or ""
        if not detail:
            detail = upstream_body.get("detail") or ""
    if not detail:
        detail = fallback_detail
    return str(detail)[:300]


async def execute_social_search_attempt(
    query: str,
    body: dict[str, Any],
    state: dict[str, Any],
    model: str,
    max_results: int,
) -> dict[str, Any]:
    upstream_payload, _ = build_upstream_payload(body, model=model)
    start = time.monotonic()
    try:
        response = await http_client.post(
            f"{state['upstream_base_url']}{state['upstream_responses_path']}",
            json=upstream_payload,
            headers={"Authorization": f"Bearer {state['resolved_upstream_api_key']}"},
        )
    except Exception as exc:
        latency_ms = int((time.monotonic() - start) * 1000)
        return build_social_attempt_summary(
            model,
            False,
            error=str(exc),
            status_code=502,
            latency_ms=latency_ms,
        )

    latency_ms = int((time.monotonic() - start) * 1000)
    try:
        upstream_body = response.json()
    except Exception:
        return build_social_attempt_summary(
            model,
            False,
            error=response.text[:300] or "Upstream returned non-JSON",
            status_code=502,
            latency_ms=latency_ms,
        )

    if response.status_code >= 400:
        return build_social_attempt_summary(
            model,
            False,
            error=extract_social_upstream_error(upstream_body, str(upstream_body)[:300]),
            status_code=response.status_code,
            latency_ms=latency_ms,
        )

    normalized = normalize_search_response(
        query,
        upstream_body,
        max_results,
        model=model,
    )
    return build_social_attempt_summary(
        model,
        True,
        response=normalized,
        status_code=response.status_code,
        latency_ms=latency_ms,
    )


def normalize_search_response(
    query: str,
    payload: dict[str, Any],
    max_results: int,
    *,
    model: str | None = None,
) -> dict[str, Any]:
    text = extract_response_text(payload)
    structured = extract_json_object(text)
    parsed_results = structured.get("results") if isinstance(structured, dict) else []
    answer = (structured.get("answer") or "").strip() if isinstance(structured, dict) else ""
    if not answer:
        answer = text

    trusted_citations = build_trusted_social_citations(payload)
    trusted_map = {item["match_url"]: item for item in trusted_citations}
    matched_results: dict[str, dict[str, str]] = {}
    fallback_results: list[dict[str, str]] = []
    seen_fallback: set[str] = set()

    for item in parsed_results or []:
        normalized = normalize_result_item(item)
        if normalized is None:
            continue
        match_url = normalize_social_match_url(normalized.get("url", ""))
        if trusted_map:
            if match_url and match_url in trusted_map and match_url not in matched_results:
                matched_results[match_url] = normalized
            continue
        if not match_url or match_url in seen_fallback:
            continue
        seen_fallback.add(match_url)
        fallback_results.append(normalized)
        if len(fallback_results) >= max_results:
            break

    if trusted_citations:
        citations = [
            {"title": item.get("title", ""), "url": item.get("url", "")}
            for item in trusted_citations[:max_results]
        ]
        results = [
            build_social_result(citation=item, matched=matched_results.get(item["match_url"]))
            for item in trusted_citations[:max_results]
        ]
    else:
        results = fallback_results[:max_results]
        citations = [
            {"title": item.get("title", ""), "url": item.get("url", "")}
            for item in results
            if is_supported_social_result_url(item.get("url", ""))
        ]

    return {
        "query": query,
        "answer": answer,
        "results": results,
        "citations": citations,
        "tool_usage": {
            "social_search_calls": 1,
            "model": model or payload.get("model") or MODEL,
        },
        "raw_text": text,
    }


def normalize_social_search_response(
    query: str,
    payload: dict[str, Any],
    max_results: int,
) -> dict[str, Any]:
    return normalize_search_response(query, payload, max_results)


async def _build_health_payload() -> dict[str, Any]:
    state = await resolve_gateway_state(force=False)
    return {
        "ok": bool(state["resolved_upstream_api_key"] and state["accepted_tokens"]),
        "mode": state["mode"],
        "upstream_base_url": state["upstream_base_url"],
        "upstream_responses_path": state["upstream_responses_path"],
        "admin_base_url": state["admin_base_url"],
        "admin_verify_path": state["admin_verify_path"],
        "admin_config_path": state["admin_config_path"],
        "admin_tokens_path": state["admin_tokens_path"],
        "model": state["model"],
        "fallback_model": state["fallback_model"],
        "fallback_min_results": state["fallback_min_results"],
        "token_source": state["token_source"],
        "admin_configured": state["admin_configured"],
        "admin_connected": state["admin_connected"],
        "accepted_token_count": len(state["accepted_tokens"]),
        "upstream_api_key_count": len(state["upstream_api_keys"]),
        "token_configured": bool(state["accepted_tokens"]),
        "upstream_key_configured": bool(state["resolved_upstream_api_key"]),
        "stats": state["stats"],
        "error": state["error"],
    }


@app.get("/health")
async def health() -> dict[str, Any]:
    return await _build_health_payload()


@app.get("/social/health")
async def social_health() -> dict[str, Any]:
    return await _build_health_payload()


@app.post("/social/search")
async def social_search(request: Request) -> dict[str, Any]:
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Expected JSON request body")

    query = (body.get("query") or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="Missing query")

    source = (body.get("source") or "x").strip().lower()
    if source != "x":
        raise HTTPException(status_code=400, detail="Only source=x is supported")

    state = await resolve_gateway_state(force=False)
    token_value = extract_token(request, body)
    verify_gateway_token(token_value, state["accepted_tokens"])
    if not state["resolved_upstream_api_key"]:
        raise HTTPException(status_code=503, detail="Missing social upstream API key")
    _, max_results = build_upstream_payload(body)
    attempts = []
    primary_attempt = await execute_social_search_attempt(
        query,
        body,
        state,
        state["model"],
        max_results,
    )
    attempts.append(primary_attempt)

    fallback_model = state.get("fallback_model", "")
    fallback_min_results = state.get("fallback_min_results", FALLBACK_MIN_RESULTS)
    fallback_reason = ""

    if primary_attempt.get("ok"):
        selected_attempt = primary_attempt
        should_retry, fallback_reason = should_retry_social_with_fallback(
            state["model"],
            fallback_model,
            primary_attempt.get("response"),
            fallback_min_results,
        )
        if should_retry:
            fallback_attempt = await execute_social_search_attempt(
                query,
                body,
                state,
                fallback_model,
                max_results,
            )
            attempts.append(fallback_attempt)
            selected_attempt = choose_preferred_social_attempt(primary_attempt, fallback_attempt)

        return attach_social_route_metadata(
            selected_attempt.get("response"),
            selected_attempt,
            attempts,
            fallback_model=fallback_model,
            fallback_reason=fallback_reason,
            fallback_min_results=fallback_min_results,
        )

    if has_social_fallback(state["model"], fallback_model):
        fallback_reason = "upstream_error"
        fallback_attempt = await execute_social_search_attempt(
            query,
            body,
            state,
            fallback_model,
            max_results,
        )
        attempts.append(fallback_attempt)
        if fallback_attempt.get("ok"):
            return attach_social_route_metadata(
                fallback_attempt.get("response"),
                fallback_attempt,
                attempts,
                fallback_model=fallback_model,
                fallback_reason=fallback_reason,
                fallback_min_results=fallback_min_results,
            )
        detail = fallback_attempt.get("error") or primary_attempt.get("error") or "Social search failed"
        status_code = fallback_attempt.get("status_code") or primary_attempt.get("status_code") or 502
        raise HTTPException(status_code=status_code, detail=detail)

    raise HTTPException(
        status_code=primary_attempt.get("status_code") or 502,
        detail=primary_attempt.get("error") or "Social search failed",
    )


def main() -> None:
    host = _env_str("SOCIAL_GATEWAY_HOST", "127.0.0.1")
    port = int(_env_str("SOCIAL_GATEWAY_PORT", "9875"))
    uvicorn.run("mysearch.social_gateway:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
