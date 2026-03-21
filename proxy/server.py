"""
多服务 API Proxy — FastAPI 主服务
"""
import asyncio
import hashlib
import hmac
import json
import os
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import database as db
from key_pool import pool

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin")
ADMIN_SESSION_COOKIE = os.environ.get("ADMIN_SESSION_COOKIE", "mysearch_proxy_session")
ADMIN_SESSION_MAX_AGE = max(300, int(os.environ.get("ADMIN_SESSION_MAX_AGE", "2592000")))
TAVILY_API_BASE = "https://api.tavily.com"
TAVILY_SEARCH_PATH = "/search"
TAVILY_EXTRACT_PATH = "/extract"
FIRECRAWL_API_BASE = "https://api.firecrawl.dev"
EXA_API_BASE = "https://api.exa.ai"


def _normalize_path(value, default):
    normalized = (value or "").strip() or default
    if not normalized.startswith("/"):
        return f"/{normalized}"
    return normalized


def _derive_social_gateway_admin_base_url(upstream_base_url):
    if upstream_base_url.endswith("/v1"):
        return upstream_base_url[:-3]
    return upstream_base_url


SOCIAL_GATEWAY_UPSTREAM_BASE_URL = os.environ.get(
    "SOCIAL_GATEWAY_UPSTREAM_BASE_URL",
    "https://api.x.ai/v1",
).rstrip("/")
SOCIAL_GATEWAY_UPSTREAM_RESPONSES_PATH = _normalize_path(
    os.environ.get("SOCIAL_GATEWAY_UPSTREAM_RESPONSES_PATH", "/responses"),
    "/responses",
)
SOCIAL_GATEWAY_UPSTREAM_API_KEY = os.environ.get("SOCIAL_GATEWAY_UPSTREAM_API_KEY", "").strip()
SOCIAL_GATEWAY_MODEL = os.environ.get("SOCIAL_GATEWAY_MODEL", "grok-4.1-fast").strip()
SOCIAL_GATEWAY_FALLBACK_MODEL = os.environ.get(
    "SOCIAL_GATEWAY_FALLBACK_MODEL",
    "grok-4.1-fast",
).strip()
try:
    SOCIAL_GATEWAY_FALLBACK_MIN_RESULTS = max(
        1,
        int(os.environ.get("SOCIAL_GATEWAY_FALLBACK_MIN_RESULTS", "3")),
    )
except (TypeError, ValueError):
    SOCIAL_GATEWAY_FALLBACK_MIN_RESULTS = 3
SOCIAL_GATEWAY_TOKEN = os.environ.get("SOCIAL_GATEWAY_TOKEN", "").strip()
SOCIAL_GATEWAY_ADMIN_BASE_URL = (
    os.environ.get("SOCIAL_GATEWAY_ADMIN_BASE_URL", "").strip().rstrip("/")
    or _derive_social_gateway_admin_base_url(SOCIAL_GATEWAY_UPSTREAM_BASE_URL)
)
SOCIAL_GATEWAY_ADMIN_VERIFY_PATH = _normalize_path(
    os.environ.get("SOCIAL_GATEWAY_ADMIN_VERIFY_PATH", "/v1/admin/verify"),
    "/v1/admin/verify",
)
SOCIAL_GATEWAY_ADMIN_CONFIG_PATH = _normalize_path(
    os.environ.get("SOCIAL_GATEWAY_ADMIN_CONFIG_PATH", "/v1/admin/config"),
    "/v1/admin/config",
)
SOCIAL_GATEWAY_ADMIN_TOKENS_PATH = _normalize_path(
    os.environ.get("SOCIAL_GATEWAY_ADMIN_TOKENS_PATH", "/v1/admin/tokens"),
    "/v1/admin/tokens",
)
SOCIAL_GATEWAY_ADMIN_APP_KEY = os.environ.get("SOCIAL_GATEWAY_ADMIN_APP_KEY", "").strip()
SOCIAL_GATEWAY_CACHE_TTL_SECONDS = max(
    5,
    int(os.environ.get("SOCIAL_GATEWAY_CACHE_TTL_SECONDS", "60")),
)
USAGE_SYNC_TTL_SECONDS = int(os.environ.get("USAGE_SYNC_TTL_SECONDS", "300"))
USAGE_SYNC_CONCURRENCY = max(1, int(os.environ.get("USAGE_SYNC_CONCURRENCY", "4")))
DASHBOARD_AUTO_SYNC_ON_STATS = os.environ.get("DASHBOARD_AUTO_SYNC_ON_STATS", "0").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
STATS_CACHE_TTL_SECONDS = max(0, int(os.environ.get("STATS_CACHE_TTL_SECONDS", "8")))
DASHBOARD_BACKGROUND_SYNC_ON_STATS = os.environ.get(
    "DASHBOARD_BACKGROUND_SYNC_ON_STATS",
    "1",
).strip().lower() in {"1", "true", "yes", "on"}
DASHBOARD_BACKGROUND_SYNC_MIN_INTERVAL_SECONDS = max(
    10,
    int(os.environ.get("DASHBOARD_BACKGROUND_SYNC_MIN_INTERVAL_SECONDS", "45")),
)
SERVICE_LABELS = {
    "tavily": "Tavily",
    "firecrawl": "Firecrawl",
    "exa": "Exa",
    "mysearch": "MySearch",
}

app = FastAPI(title="MySearch Proxy")
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))
http_client = httpx.AsyncClient(timeout=60)
social_gateway_state_cache = {"expires_at": 0.0, "value": None}
social_gateway_state_lock = asyncio.Lock()
stats_payload_cache = {"expires_at": 0.0, "value": None}
stats_payload_lock = asyncio.Lock()
background_sync_tasks = {}
background_sync_last_started = {}
background_sync_lock = asyncio.Lock()


def get_admin_password():
    return db.get_setting("admin_password", ADMIN_PASSWORD)


def build_admin_session_token(password):
    return hmac.new(
        password.encode("utf-8"),
        b"mysearch-proxy-session-v1",
        hashlib.sha256,
    ).hexdigest()


def has_valid_admin_session(request: Request):
    token = (request.cookies.get(ADMIN_SESSION_COOKIE) or "").strip()
    if not token:
        return False
    expected = build_admin_session_token(get_admin_password())
    return hmac.compare_digest(token, expected)


def apply_admin_session_cookie(response: Response, request: Request, password: str):
    response.set_cookie(
        ADMIN_SESSION_COOKIE,
        build_admin_session_token(password),
        max_age=ADMIN_SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        path="/",
    )


def clear_admin_session_cookie(response: Response):
    response.delete_cookie(ADMIN_SESSION_COOKIE, path="/")


def get_service(service_value, default="tavily"):
    try:
        return db.normalize_service(service_value or default)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def get_token_service(service_value, default="tavily"):
    try:
        return db.normalize_token_service(service_value or default)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def reset_social_gateway_cache():
    social_gateway_state_cache["expires_at"] = 0.0
    social_gateway_state_cache["value"] = None


def reset_stats_cache():
    stats_payload_cache["expires_at"] = 0.0
    stats_payload_cache["value"] = None


def get_setting_text(key, default=""):
    value = db.get_setting(key, default)
    if value is None:
        return str(default or "").strip()
    return str(value).strip()


def get_runtime_social_config():
    upstream_base_url = (
        get_setting_text("social_upstream_base_url", SOCIAL_GATEWAY_UPSTREAM_BASE_URL).rstrip("/")
        or SOCIAL_GATEWAY_UPSTREAM_BASE_URL
    )
    admin_base_value = get_setting_text("social_admin_base_url", "")
    cache_ttl_raw = get_setting_text(
        "social_cache_ttl_seconds",
        str(SOCIAL_GATEWAY_CACHE_TTL_SECONDS),
    )
    fallback_min_results_raw = get_setting_text(
        "social_fallback_min_results",
        str(SOCIAL_GATEWAY_FALLBACK_MIN_RESULTS),
    )
    try:
        cache_ttl_seconds = max(5, int(cache_ttl_raw or SOCIAL_GATEWAY_CACHE_TTL_SECONDS))
    except (TypeError, ValueError):
        cache_ttl_seconds = SOCIAL_GATEWAY_CACHE_TTL_SECONDS
    try:
        fallback_min_results = max(
            1,
            int(fallback_min_results_raw or SOCIAL_GATEWAY_FALLBACK_MIN_RESULTS),
        )
    except (TypeError, ValueError):
        fallback_min_results = SOCIAL_GATEWAY_FALLBACK_MIN_RESULTS

    return {
        "upstream_base_url": upstream_base_url,
        "upstream_responses_path": _normalize_path(
            get_setting_text(
                "social_upstream_responses_path",
                SOCIAL_GATEWAY_UPSTREAM_RESPONSES_PATH,
            ),
            SOCIAL_GATEWAY_UPSTREAM_RESPONSES_PATH,
        ),
        "upstream_api_key": get_setting_text(
            "social_upstream_api_key",
            SOCIAL_GATEWAY_UPSTREAM_API_KEY,
        ),
        "model": get_setting_text("social_model", SOCIAL_GATEWAY_MODEL) or SOCIAL_GATEWAY_MODEL,
        "fallback_model": get_setting_text(
            "social_fallback_model",
            SOCIAL_GATEWAY_FALLBACK_MODEL,
        ),
        "fallback_min_results": fallback_min_results,
        "gateway_token": get_setting_text("social_gateway_token", SOCIAL_GATEWAY_TOKEN),
        "admin_base_url": (
            admin_base_value.rstrip("/")
            if admin_base_value
            else _derive_social_gateway_admin_base_url(upstream_base_url)
        ),
        "admin_verify_path": _normalize_path(
            get_setting_text(
                "social_admin_verify_path",
                SOCIAL_GATEWAY_ADMIN_VERIFY_PATH,
            ),
            SOCIAL_GATEWAY_ADMIN_VERIFY_PATH,
        ),
        "admin_config_path": _normalize_path(
            get_setting_text(
                "social_admin_config_path",
                SOCIAL_GATEWAY_ADMIN_CONFIG_PATH,
            ),
            SOCIAL_GATEWAY_ADMIN_CONFIG_PATH,
        ),
        "admin_tokens_path": _normalize_path(
            get_setting_text(
                "social_admin_tokens_path",
                SOCIAL_GATEWAY_ADMIN_TOKENS_PATH,
            ),
            SOCIAL_GATEWAY_ADMIN_TOKENS_PATH,
        ),
        "admin_app_key": get_setting_text(
            "social_admin_app_key",
            SOCIAL_GATEWAY_ADMIN_APP_KEY,
        ),
        "cache_ttl_seconds": cache_ttl_seconds,
    }


def get_runtime_tavily_config():
    mode = get_setting_text("tavily_mode", "pool").lower()
    if mode not in {"pool", "upstream"}:
        mode = "pool"

    upstream_base_url = (
        get_setting_text("tavily_upstream_base_url", TAVILY_API_BASE).rstrip("/")
        or TAVILY_API_BASE
    )
    return {
        "mode": mode,
        "upstream_base_url": upstream_base_url,
        "upstream_search_path": _normalize_path(
            get_setting_text("tavily_upstream_search_path", TAVILY_SEARCH_PATH),
            TAVILY_SEARCH_PATH,
        ),
        "upstream_extract_path": _normalize_path(
            get_setting_text("tavily_upstream_extract_path", TAVILY_EXTRACT_PATH),
            TAVILY_EXTRACT_PATH,
        ),
        "upstream_api_key": get_setting_text("tavily_upstream_api_key", ""),
    }


def build_tavily_routing_meta(config, active_keys):
    using_upstream = config["mode"] == "upstream"
    summary = (
        "当前 Tavily 走上游 Gateway；切回本地 Key 池模式后才会重新使用这里导入的 Tavily keys。"
        if using_upstream
        else "当前 Tavily 走本地 Key 池，请求会从已导入的 Tavily keys 中轮询。"
    )
    return {
        "mode": config["mode"],
        "upstream_base_url": config["upstream_base_url"],
        "upstream_search_path": config["upstream_search_path"],
        "upstream_extract_path": config["upstream_extract_path"],
        "upstream_api_key_configured": bool(config["upstream_api_key"]),
        "local_key_count": len(active_keys),
        "summary": summary,
    }


# ═══ Auth helpers ═══

def verify_admin(request: Request):
    auth = request.headers.get("Authorization", "")
    password = request.headers.get("X-Admin-Password", "")
    pwd = get_admin_password()
    if auth == f"Bearer {pwd}" or password == pwd or has_valid_admin_session(request):
        return True
    raise HTTPException(status_code=401, detail="Unauthorized")


def extract_token(request: Request, body: dict = None):
    """从请求中提取代理 token。"""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    x_api_key = request.headers.get("x-api-key", "")
    if x_api_key.strip():
        return x_api_key.strip()
    if body and body.get("api_key"):
        return body["api_key"]
    return None


def unique_preserve_order(items):
    result = []
    seen = set()
    for item in items:
        value = (item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def parse_secret_values(value):
    if not value:
        return []
    if isinstance(value, str):
        return unique_preserve_order(re.split(r"[\n,]", value))
    if isinstance(value, (list, tuple, set)):
        return unique_preserve_order(str(item) for item in value)
    return []


def build_empty_social_stats():
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


def mask_secret(value):
    if not value:
        return ""
    if len(value) <= 4:
        return "*" * len(value)
    if len(value) <= 8:
        return f"{value[:2]}***{value[-2:]}"
    if len(value) <= 12:
        return f"{value[:3]}***{value[-3:]}"
    return f"{value[:6]}***{value[-4:]}"


def flatten_social_tokens(tokens_payload):
    flat = []
    if not isinstance(tokens_payload, dict):
        return flat

    for pool_name, items in tokens_payload.items():
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, str):
                token_value = item
                status = "active"
                quota = 0
                use_count = 0
                tags = []
            elif isinstance(item, dict):
                token_value = str(item.get("token") or "")
                status = (item.get("status") or "active").strip().lower()
                quota = parse_usage_number(item.get("quota")) or 0
                use_count = parse_usage_number(item.get("use_count")) or 0
                raw_tags = item.get("tags") or []
                tags = [str(tag).strip() for tag in raw_tags if str(tag).strip()] if isinstance(raw_tags, list) else []
            else:
                continue

            flat.append(
                {
                    "pool": str(pool_name),
                    "token_masked": mask_secret(token_value),
                    "status": status,
                    "quota": max(0, quota),
                    "use_count": max(0, use_count),
                    "tags": tags,
                }
            )
    return flat


def build_social_token_stats(tokens_payload):
    flat_tokens = flatten_social_tokens(tokens_payload)
    stats = build_empty_social_stats()
    if not flat_tokens:
        return stats

    active_tokens = [item for item in flat_tokens if item["status"] == "active"]
    cooling_tokens = [item for item in flat_tokens if item["status"] == "cooling"]
    invalid_tokens = [
        item for item in flat_tokens if item["status"] not in {"active", "cooling"}
    ]
    chat_remaining = sum(item["quota"] for item in active_tokens)
    pools = {}
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


def build_social_gateway_mode(state):
    if state["admin_connected"] and (state["manual_upstream_key"] or state["manual_gateway_token"]):
        return "hybrid"
    if state["admin_connected"]:
        return "admin-auto"
    return "manual"


def build_social_token_source(state):
    if state["manual_gateway_token"]:
        return "manual SOCIAL_GATEWAY_TOKEN"
    if state["admin_connected"] and state["admin_api_keys"]:
        return "grok2api app.api_key"
    if state["manual_upstream_key"]:
        return "SOCIAL_GATEWAY_UPSTREAM_API_KEY"
    return "not_configured"


async def fetch_social_admin_json(config, path):
    if not config["admin_app_key"]:
        raise RuntimeError("Missing SOCIAL_GATEWAY_ADMIN_APP_KEY")
    response = await http_client.get(
        f"{config['admin_base_url']}{path}",
        headers={"Authorization": f"Bearer {config['admin_app_key']}"},
    )
    try:
        payload = response.json()
    except Exception:
        payload = None
    if response.status_code >= 400:
        detail = ""
        if isinstance(payload, dict):
            detail = payload.get("detail") or payload.get("message") or ""
        if not detail:
            detail = response.text.strip()[:240] or f"HTTP {response.status_code}"
        raise RuntimeError(f"{path} -> {detail}")
    return payload if isinstance(payload, dict) else {}


async def resolve_social_gateway_state(force=False):
    now = time.time()
    cached = social_gateway_state_cache.get("value")
    if not force and cached and social_gateway_state_cache.get("expires_at", 0) > now:
        return cached

    async with social_gateway_state_lock:
        now = time.time()
        cached = social_gateway_state_cache.get("value")
        if not force and cached and social_gateway_state_cache.get("expires_at", 0) > now:
            return cached

        config = get_runtime_social_config()
        state = {
            "upstream_base_url": config["upstream_base_url"],
            "upstream_responses_path": config["upstream_responses_path"],
            "admin_base_url": config["admin_base_url"],
            "admin_verify_path": config["admin_verify_path"],
            "admin_config_path": config["admin_config_path"],
            "admin_tokens_path": config["admin_tokens_path"],
            "admin_configured": bool(config["admin_base_url"] and config["admin_app_key"]),
            "admin_connected": False,
            "manual_upstream_key": bool(config["upstream_api_key"]),
            "manual_gateway_token": bool(config["gateway_token"]),
            "upstream_api_keys": parse_secret_values(config["upstream_api_key"]),
            "accepted_tokens": parse_secret_values(config["gateway_token"]),
            "admin_api_keys": [],
            "resolved_upstream_api_key": "",
            "default_client_token": "",
            "token_source": "",
            "mode": "manual",
            "model": config["model"],
            "fallback_model": config["fallback_model"],
            "fallback_min_results": config["fallback_min_results"],
            "cache_ttl_seconds": config["cache_ttl_seconds"],
            "stats": build_empty_social_stats(),
            "error": "",
        }

        if state["admin_configured"]:
            try:
                admin_config, admin_tokens = await asyncio.gather(
                    fetch_social_admin_json(config, config["admin_config_path"]),
                    fetch_social_admin_json(config, config["admin_tokens_path"]),
                )
                app_api_keys = parse_secret_values((admin_config.get("app") or {}).get("api_key"))
                state["admin_connected"] = True
                state["admin_api_keys"] = app_api_keys
                if not state["upstream_api_keys"]:
                    state["upstream_api_keys"] = app_api_keys
                if not state["accepted_tokens"]:
                    state["accepted_tokens"] = app_api_keys
                state["stats"] = build_social_token_stats(admin_tokens)
            except Exception as exc:
                state["error"] = str(exc)

        if not state["accepted_tokens"] and state["upstream_api_keys"]:
            state["accepted_tokens"] = list(state["upstream_api_keys"])

        state["upstream_api_keys"] = unique_preserve_order(state["upstream_api_keys"])
        state["accepted_tokens"] = unique_preserve_order(state["accepted_tokens"])
        state["resolved_upstream_api_key"] = state["upstream_api_keys"][0] if state["upstream_api_keys"] else ""
        state["default_client_token"] = state["accepted_tokens"][0] if state["accepted_tokens"] else ""
        state["token_source"] = build_social_token_source(state)
        state["mode"] = build_social_gateway_mode(state)

        social_gateway_state_cache["value"] = state
        social_gateway_state_cache["expires_at"] = now + state["cache_ttl_seconds"]
        return state


def verify_social_gateway_token(token_value, accepted_tokens):
    if token_value:
        token_row = db.get_token_by_value(token_value)
        if token_row and token_row["service"] == "mysearch":
            return token_row
    if not accepted_tokens:
        raise HTTPException(status_code=503, detail="Social gateway is not configured")
    if not token_value:
        raise HTTPException(status_code=401, detail="Missing API token")
    if not any(hmac.compare_digest(token_value, expected) for expected in accepted_tokens):
        raise HTTPException(status_code=401, detail="Invalid token")
    return None


def get_token_row_or_401(token_value, service):
    if not token_value:
        raise HTTPException(status_code=401, detail="Missing API token")
    token_row = db.get_token_by_value(token_value)
    if not token_row or token_row["service"] not in {service, "mysearch"}:
        raise HTTPException(status_code=401, detail="Invalid token")
    return token_row


def get_token_usage_scope(token_row, default_service):
    token_service = default_service
    if token_row is not None:
        try:
            token_service = token_row["service"] or default_service
        except Exception:
            token_service = default_service
    return None if token_service == "mysearch" else default_service


def parse_usage_number(value):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None


def compute_remaining(limit_value, used_value):
    if limit_value is None or used_value is None:
        return None
    return max(0, limit_value - used_value)


def parse_sync_time(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def is_usage_sync_stale(key_row, ttl_seconds=USAGE_SYNC_TTL_SECONDS):
    synced_at = parse_sync_time(key_row.get("usage_synced_at"))
    if not synced_at:
        return True
    return (datetime.now(timezone.utc) - synced_at).total_seconds() >= ttl_seconds


async def fetch_remote_usage_tavily(key_value):
    resp = await http_client.get(
        f"{TAVILY_API_BASE}/usage",
        headers={"Authorization": f"Bearer {key_value}"},
    )
    if resp.status_code != 200:
        detail = ""
        try:
            payload = resp.json()
            detail = payload.get("detail") or payload.get("message") or ""
        except Exception:
            detail = resp.text.strip()
        detail = detail[:200] if detail else f"HTTP {resp.status_code}"
        raise HTTPException(status_code=resp.status_code, detail=detail)
    return resp.json()


async def fetch_remote_usage_firecrawl(key_value):
    headers = {"Authorization": f"Bearer {key_value}"}
    current_resp, history_resp = await asyncio.gather(
        http_client.get(f"{FIRECRAWL_API_BASE}/v2/team/credit-usage", headers=headers),
        http_client.get(
            f"{FIRECRAWL_API_BASE}/v2/team/credit-usage/historical",
            params={"byApiKey": "true"},
            headers=headers,
        ),
    )

    for resp in (current_resp, history_resp):
        if resp.status_code != 200:
            detail = resp.text.strip()[:200] or f"HTTP {resp.status_code}"
            raise HTTPException(status_code=resp.status_code, detail=detail)

    return {
        "current": current_resp.json(),
        "historical": history_resp.json(),
    }


def normalize_usage_payload(service, payload):
    if service == "tavily":
        key_info = payload.get("key") or {}
        account_info = payload.get("account") or {}

        key_used = parse_usage_number(key_info.get("usage"))
        key_limit = parse_usage_number(key_info.get("limit"))
        account_used = parse_usage_number(account_info.get("plan_usage"))
        account_limit = parse_usage_number(account_info.get("plan_limit"))

        return {
            "key_used": key_used,
            "key_limit": key_limit,
            "key_remaining": compute_remaining(key_limit, key_used),
            "account_plan": (account_info.get("current_plan") or "").strip(),
            "account_used": account_used,
            "account_limit": account_limit,
            "account_remaining": compute_remaining(account_limit, account_used),
        }

    current_data = (payload.get("current") or {}).get("data") or {}
    history_periods = (payload.get("historical") or {}).get("periods") or []
    if history_periods:
        latest_period = max(
            history_periods,
            key=lambda item: ((item.get("endDate") or ""), (item.get("startDate") or "")),
        )
        current_period_rows = [
            item for item in history_periods
            if item.get("startDate") == latest_period.get("startDate")
            and item.get("endDate") == latest_period.get("endDate")
        ]
    else:
        current_period_rows = []

    account_remaining = parse_usage_number(current_data.get("remainingCredits"))
    plan_credits = parse_usage_number(current_data.get("planCredits"))
    account_used = sum(parse_usage_number(item.get("creditsUsed")) or 0 for item in current_period_rows)
    account_limit = None
    if account_remaining is not None:
        account_limit = account_remaining + account_used

    if plan_credits is None:
        account_plan = "Firecrawl"
    else:
        account_plan = f"Plan credits {plan_credits}"

    return {
        "key_used": None,
        "key_limit": None,
        "key_remaining": None,
        "account_plan": account_plan,
        "account_used": account_used,
        "account_limit": account_limit,
        "account_remaining": account_remaining,
    }


async def sync_usage_for_key_row(key_row):
    service = key_row.get("service") or "tavily"
    try:
        if service == "firecrawl":
            payload = await fetch_remote_usage_firecrawl(key_row["key"])
        else:
            payload = await fetch_remote_usage_tavily(key_row["key"])

        normalized = normalize_usage_payload(service, payload)
        db.update_key_remote_usage(
            key_row["id"],
            key_used=normalized["key_used"],
            key_limit=normalized["key_limit"],
            key_remaining=normalized["key_remaining"],
            account_plan=normalized["account_plan"],
            account_used=normalized["account_used"],
            account_limit=normalized["account_limit"],
            account_remaining=normalized["account_remaining"],
        )
        return {"key_id": key_row["id"], "status": "synced"}
    except HTTPException as exc:
        db.update_key_remote_usage_error(key_row["id"], exc.detail)
        return {"key_id": key_row["id"], "status": "error", "detail": exc.detail}
    except Exception as exc:
        db.update_key_remote_usage_error(key_row["id"], str(exc))
        return {"key_id": key_row["id"], "status": "error", "detail": str(exc)}


async def sync_usage_cache(force=False, key_id=None, service=None):
    if service == "exa":
        rows = []
        if key_id is not None:
            row = db.get_key_by_id(key_id)
            if row and row["service"] == "exa":
                rows = [dict(row)]
        else:
            rows = [dict(row) for row in db.get_all_keys("exa")]
        return {
            "requested": len(rows),
            "synced": 0,
            "skipped": len(rows),
            "errors": 0,
            "supported": False,
            "detail": "Exa 当前未接入官方额度同步",
        }

    rows = []
    if key_id is not None:
        row = db.get_key_by_id(key_id)
        if row and (service is None or row["service"] == service):
            rows = [dict(row)]
    else:
        rows = [dict(row) for row in db.get_all_keys(service)]

    if rows and all((row.get("service") or "tavily") == "tavily" for row in rows):
        tavily_config = get_runtime_tavily_config()
        if tavily_config["mode"] == "upstream":
            return {
                "requested": len(rows),
                "synced": 0,
                "skipped": len(rows),
                "errors": 0,
                "supported": False,
                "detail": "当前走 Tavily 上游 Gateway，本地 Key 池额度同步已停用",
            }

    if not rows:
        return {"requested": 0, "synced": 0, "skipped": 0, "errors": 0}

    to_sync = rows if force else [row for row in rows if is_usage_sync_stale(row)]
    if not to_sync:
        return {"requested": len(rows), "synced": 0, "skipped": len(rows), "errors": 0}

    semaphore = asyncio.Semaphore(USAGE_SYNC_CONCURRENCY)

    async def worker(row):
        async with semaphore:
            return await sync_usage_for_key_row(row)

    results = await asyncio.gather(*(worker(row) for row in to_sync))
    synced = sum(1 for item in results if item["status"] == "synced")
    errors = sum(1 for item in results if item["status"] == "error")
    return {
        "requested": len(rows),
        "synced": synced,
        "skipped": len(rows) - len(to_sync),
        "errors": errors,
    }


def build_usage_sync_meta_for_dashboard(service, active_keys):
    if service == "tavily" and get_runtime_tavily_config()["mode"] == "upstream":
        return {
            "supported": False,
            "requested": len(active_keys),
            "synced": 0,
            "skipped": len(active_keys),
            "errors": 0,
            "stale_keys": 0,
            "detail": "当前走 Tavily 上游 Gateway，本地 Key 池额度同步已停用",
        }

    if service == "exa":
        return {
            "supported": False,
            "requested": len(active_keys),
            "synced": 0,
            "skipped": len(active_keys),
            "errors": 0,
            "stale_keys": 0,
            "detail": "Exa 实时额度暂时无法查询",
        }

    stale_keys = sum(1 for key in active_keys if is_usage_sync_stale(key))
    detail = "已启用后台同步，页面优先快速返回。"
    if stale_keys > 0:
        detail = f"检测到 {stale_keys} 个 Key 额度信息较旧，后台会按节流策略刷新。"
    return {
        "supported": True,
        "auto_sync": False,
        "requested": len(active_keys),
        "synced": 0,
        "skipped": len(active_keys),
        "errors": 0,
        "stale_keys": stale_keys,
        "detail": detail,
    }


async def schedule_background_usage_sync(service, active_keys):
    if not DASHBOARD_BACKGROUND_SYNC_ON_STATS:
        return
    if service == "tavily" and get_runtime_tavily_config()["mode"] == "upstream":
        return
    if service == "exa":
        return
    if not active_keys:
        return
    if not any(is_usage_sync_stale(key) for key in active_keys):
        return

    now = time.monotonic()
    async with background_sync_lock:
        running = background_sync_tasks.get(service)
        if running and not running.done():
            return

        last_started = background_sync_last_started.get(service, 0.0)
        if now - last_started < DASHBOARD_BACKGROUND_SYNC_MIN_INTERVAL_SECONDS:
            return
        background_sync_last_started[service] = now

        async def _run():
            try:
                await sync_usage_cache(force=False, service=service)
            except Exception:
                # 后台同步失败不影响页面主流程，下次节流窗口后再尝试。
                pass
            finally:
                reset_stats_cache()

        background_sync_tasks[service] = asyncio.create_task(_run())


def build_real_quota_summary(keys):
    synced_keys = [
        key for key in keys
        if key.get("usage_key_used") is not None or key.get("usage_account_used") is not None
    ]
    total_limit = 0
    total_used = 0
    total_remaining = 0
    key_level_count = 0
    account_fallback_count = 0
    accounted_groups = set()
    latest_sync = None
    for key in synced_keys:
        key_limit = key.get("usage_key_limit")
        key_used = key.get("usage_key_used")
        account_limit = key.get("usage_account_limit")
        account_used = key.get("usage_account_used")

        if key_limit is not None and key_used is not None:
            total_limit += key_limit
            total_used += key_used
            total_remaining += key.get("usage_key_remaining") or compute_remaining(key_limit, key_used) or 0
            key_level_count += 1
        elif account_limit is not None and account_used is not None:
            group_id = (key.get("email") or "").strip().lower() or f"key:{key.get('id')}"
            if group_id not in accounted_groups:
                accounted_groups.add(group_id)
                total_limit += account_limit
                total_used += account_used
                total_remaining += key.get("usage_account_remaining") or compute_remaining(account_limit, account_used) or 0
                account_fallback_count += 1

        synced_at = parse_sync_time(key.get("usage_synced_at"))
        if synced_at and (latest_sync is None or synced_at > latest_sync):
            latest_sync = synced_at

    error_count = sum(1 for key in keys if (key.get("usage_sync_error") or "").strip())
    return {
        "synced_keys": len(synced_keys),
        "total_keys": len(keys),
        "total_limit": total_limit,
        "total_used": total_used,
        "total_remaining": total_remaining,
        "error_keys": error_count,
        "last_synced_at": latest_sync.isoformat() if latest_sync else "",
        "key_level_count": key_level_count,
        "account_fallback_count": account_fallback_count,
    }


def mask_key_rows(keys):
    for key in keys:
        raw = key["key"]
        key["key_masked"] = raw[:8] + "***" + raw[-4:] if len(raw) > 12 else raw
    return keys


async def build_service_dashboard(service, auto_sync=False):
    service = get_service(service)
    overview = db.get_usage_stats(service=service)
    tokens = [dict(token) for token in db.get_all_tokens(service)]
    for token in tokens:
        token["stats"] = db.get_usage_stats(token_id=token["id"], service=service)
    keys = mask_key_rows([dict(key) for key in db.get_all_keys(service)])
    active_keys = [key for key in keys if key["active"]]
    routing = None
    if service == "tavily":
        routing = build_tavily_routing_meta(get_runtime_tavily_config(), active_keys)
    if auto_sync:
        sync_result = await sync_usage_cache(force=False, service=service)
    else:
        sync_result = build_usage_sync_meta_for_dashboard(service, active_keys)
        await schedule_background_usage_sync(service, active_keys)
    payload = {
        "service": service,
        "label": SERVICE_LABELS[service],
        "overview": overview,
        "tokens": tokens,
        "keys": keys,
        "keys_total": len(keys),
        "keys_active": len(active_keys),
        "real_quota": build_real_quota_summary(active_keys),
        "usage_sync": sync_result,
    }
    if routing is not None:
        payload["routing"] = routing
    return payload


async def build_mysearch_dashboard():
    tokens = [dict(token) for token in db.get_all_tokens("mysearch")]
    for token in tokens:
        token["stats"] = db.get_usage_stats(token_id=token["id"], service="mysearch")

    overview = {
        "today_count": sum(int((token.get("stats") or {}).get("today_count") or 0) for token in tokens),
        "today_success": sum(int((token.get("stats") or {}).get("today_success") or 0) for token in tokens),
        "today_failed": sum(int((token.get("stats") or {}).get("today_failed") or 0) for token in tokens),
        "month_count": sum(int((token.get("stats") or {}).get("month_count") or 0) for token in tokens),
        "month_success": sum(int((token.get("stats") or {}).get("month_success") or 0) for token in tokens),
    }
    return {
        "service": "mysearch",
        "label": SERVICE_LABELS["mysearch"],
        "token_prefix": db.TOKEN_PREFIX["mysearch"],
        "tokens": tokens,
        "token_count": len(tokens),
        "overview": overview,
    }


async def build_social_dashboard():
    state = await resolve_social_gateway_state(force=False)
    return {
        "service": "social",
        "label": "Social / X",
        "mode": state["mode"],
        "model": state["model"],
        "fallback_model": state["fallback_model"],
        "fallback_min_results": state["fallback_min_results"],
        "token_source": state["token_source"],
        "upstream_base_url": state["upstream_base_url"],
        "upstream_responses_path": state["upstream_responses_path"],
        "admin_base_url": state["admin_base_url"],
        "admin_configured": state["admin_configured"],
        "admin_connected": state["admin_connected"],
        "upstream_key_configured": bool(state["resolved_upstream_api_key"]),
        "client_auth_configured": bool(state["accepted_tokens"]),
        "accepted_token_count": len(state["accepted_tokens"]),
        "upstream_api_key_count": len(state["upstream_api_keys"]),
        "client_token": state["default_client_token"],
        "client_token_masked": mask_secret(state["default_client_token"]),
        "stats": state["stats"],
        "error": state["error"],
    }


async def build_settings_payload():
    tavily = get_runtime_tavily_config()
    config = get_runtime_social_config()
    state = await resolve_social_gateway_state(force=False)
    return {
        "tavily": {
            "mode": tavily["mode"],
            "upstream_base_url": tavily["upstream_base_url"],
            "upstream_search_path": tavily["upstream_search_path"],
            "upstream_extract_path": tavily["upstream_extract_path"],
            "upstream_api_key_configured": bool(tavily["upstream_api_key"]),
            "upstream_api_key_masked": mask_secret(tavily["upstream_api_key"]),
        },
        "social": {
            "upstream_base_url": config["upstream_base_url"],
            "upstream_responses_path": config["upstream_responses_path"],
            "admin_base_url": config["admin_base_url"],
            "admin_verify_path": config["admin_verify_path"],
            "admin_config_path": config["admin_config_path"],
            "admin_tokens_path": config["admin_tokens_path"],
            "model": config["model"],
            "fallback_model": config["fallback_model"],
            "fallback_min_results": config["fallback_min_results"],
            "cache_ttl_seconds": config["cache_ttl_seconds"],
            "admin_app_key_configured": bool(config["admin_app_key"]),
            "admin_app_key_masked": mask_secret(config["admin_app_key"]),
            "upstream_api_key_configured": bool(config["upstream_api_key"]),
            "upstream_api_key_masked": mask_secret(config["upstream_api_key"]),
            "gateway_token_configured": bool(config["gateway_token"]),
            "gateway_token_masked": mask_secret(config["gateway_token"]),
            "mode": state["mode"],
            "token_source": state["token_source"],
            "admin_connected": state["admin_connected"],
            "error": state["error"],
        }
    }


async def build_stats_payload(auto_sync=False):
    tavily_stats, firecrawl_stats, exa_stats, social_stats, mysearch_stats = await asyncio.gather(
        build_service_dashboard("tavily", auto_sync=auto_sync),
        build_service_dashboard("firecrawl", auto_sync=auto_sync),
        build_service_dashboard("exa", auto_sync=auto_sync),
        build_social_dashboard(),
        build_mysearch_dashboard(),
    )
    return {
        "services": {
            "tavily": tavily_stats,
            "firecrawl": firecrawl_stats,
            "exa": exa_stats,
        },
        "social": social_stats,
        "mysearch": mysearch_stats,
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "stats_cache_ttl_seconds": STATS_CACHE_TTL_SECONDS,
            "dashboard_auto_sync_on_stats": DASHBOARD_AUTO_SYNC_ON_STATS,
        },
    }


def build_forward_headers(request, real_key):
    skip_headers = {
        "authorization",
        "content-length",
        "host",
        "x-admin-password",
    }
    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in skip_headers
    }
    headers["Authorization"] = f"Bearer {real_key}"
    return headers


def build_exa_forward_headers(request, real_key):
    # Do not forward arbitrary inbound headers to Exa. We observed intermittent
    # upstream failures when proxying edge/CDN headers verbatim.
    headers = {
        "x-api-key": real_key,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "MySearch-Proxy/1.0",
    }
    return headers


async def parse_json_body(request):
    raw_body = await request.body()
    if not raw_body:
        return raw_body, None
    content_type = request.headers.get("content-type", "").lower()
    if "application/json" not in content_type:
        return raw_body, None
    try:
        return raw_body, json.loads(raw_body.decode("utf-8"))
    except Exception:
        return raw_body, None


def forward_raw_response(resp):
    """尽量保留上游返回格式，避免把非 JSON Firecrawl 响应再包一层。"""
    content_type = resp.headers.get("content-type", "")
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        media_type=content_type or None,
    )


def extract_response_text(payload):
    if isinstance(payload.get("output_text"), str) and payload.get("output_text").strip():
        return payload["output_text"].strip()

    parts = []
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


def extract_json_object(text):
    candidates = []
    stripped = text.strip()
    if stripped:
        candidates.append(stripped)

    fenced = re.findall(r"```(?:json)?\s*(.*?)```", stripped, flags=re.DOTALL | re.IGNORECASE)
    candidates.extend(item.strip() for item in fenced if item.strip())

    decoder = json.JSONDecoder()
    seen = set()
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


def normalize_citation(item):
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


def extract_upstream_citations(payload):
    raw_citations = payload.get("citations") or []
    normalized = []
    seen = set()

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


def normalize_result_item(item):
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


def looks_synthetic_social_status_id(status_id):
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


def normalize_social_match_url(url):
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


def is_supported_social_result_url(url):
    return bool(normalize_social_match_url(url))


def build_trusted_social_citations(payload):
    citations = []
    seen = set()
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


def build_social_result(citation=None, matched=None):
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


def build_social_search_upstream_payload(body, model):
    query = (body.get("query") or "").strip()
    max_results = max(1, min(int(body.get("max_results") or 5), 10))
    tools = [{"type": "x_search"}]
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
    return {
        "model": model,
        "input": [{"role": "user", "content": prompt}],
        "tools": tools,
        "temperature": 0,
        "store": False,
    }


def count_social_results(payload):
    return len((payload or {}).get("results") or [])


def count_social_citations(payload):
    return len((payload or {}).get("citations") or [])


def build_social_attempt_summary(
    model,
    ok,
    *,
    response=None,
    error="",
    status_code=None,
    latency_ms=None,
):
    attempt = {
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


def has_social_fallback(primary_model, fallback_model):
    primary = (primary_model or "").strip()
    fallback = (fallback_model or "").strip()
    return bool(primary and fallback and fallback != primary)


def should_retry_social_with_fallback(primary_model, fallback_model, response, min_results):
    if not has_social_fallback(primary_model, fallback_model):
        return False, ""
    threshold = max(1, int(min_results or 1))
    if count_social_results(response) >= threshold:
        return False, ""
    return True, "result_count_below_threshold"


def choose_preferred_social_attempt(primary_attempt, fallback_attempt):
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
    selected_attempt,
    attempts,
    *,
    fallback_model,
    fallback_reason,
    fallback_min_results,
):
    primary_model = attempts[0]["model"] if attempts else ""
    selected_model = (selected_attempt or {}).get("model") or primary_model
    route_attempts = []
    for item in attempts:
        route_item = {
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
    response,
    selected_attempt,
    attempts,
    *,
    fallback_model,
    fallback_reason,
    fallback_min_results,
):
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


def extract_social_upstream_error(upstream_body, fallback_detail="Social search failed"):
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


async def execute_social_search_attempt(query, body, state, model, max_results):
    upstream_payload = build_social_search_upstream_payload(body, model)
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

    normalized = normalize_social_search_response(
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


def normalize_social_search_response(query, payload, max_results, *, model=None):
    text = extract_response_text(payload)
    structured = extract_json_object(text)
    parsed_results = structured.get("results") if isinstance(structured, dict) else []
    answer = (structured.get("answer") or "").strip() if isinstance(structured, dict) else ""
    if not answer:
        answer = text

    trusted_citations = build_trusted_social_citations(payload)
    trusted_map = {item["match_url"]: item for item in trusted_citations}
    matched_results = {}
    fallback_results = []
    seen_fallback = set()

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
            "model": model or payload.get("model") or SOCIAL_GATEWAY_MODEL,
        },
        "raw_text": text,
    }


# ═══ 启动 ═══

@app.on_event("startup")
def startup():
    db.init_db()


@app.on_event("shutdown")
async def shutdown():
    await http_client.aclose()


# ═══ Tavily 代理端点 ═══

@app.post("/api/search")
@app.post("/api/extract")
async def proxy_tavily(request: Request):
    body = await request.json()
    endpoint = request.url.path.replace("/api/", "")

    token_value = extract_token(request, body)
    token_row = get_token_row_or_401(token_value, "tavily")

    config = get_runtime_tavily_config()
    path_map = {
        "search": config["upstream_search_path"],
        "extract": config["upstream_extract_path"],
    }
    upstream_path = path_map.get(endpoint)
    if not upstream_path:
        raise HTTPException(status_code=400, detail=f"Unsupported Tavily endpoint: {endpoint}")

    upstream_base_url = TAVILY_API_BASE
    upstream_key = ""
    key_info = None
    if config["mode"] == "upstream":
        upstream_base_url = config["upstream_base_url"]
        upstream_key = config["upstream_api_key"]
        if not upstream_key:
            raise HTTPException(status_code=503, detail="Missing Tavily upstream API key")
    else:
        key_info = pool.get_next_key("tavily")
        if not key_info:
            raise HTTPException(status_code=503, detail="No available API keys")
        upstream_key = key_info["key"]

    body["api_key"] = upstream_key
    start = time.time()
    try:
        resp = await http_client.post(f"{upstream_base_url}{upstream_path}", json=body)
        latency = int((time.time() - start) * 1000)
        success = resp.status_code == 200
        if key_info is not None:
            pool.report_result("tavily", key_info["id"], success)
        db.log_usage(
            token_row["id"],
            key_info["id"] if key_info is not None else None,
            endpoint,
            int(success),
            latency,
            service="tavily",
        )
        return JSONResponse(content=resp.json(), status_code=resp.status_code)
    except Exception as exc:
        latency = int((time.time() - start) * 1000)
        if key_info is not None:
            pool.report_result("tavily", key_info["id"], False)
        db.log_usage(
            token_row["id"],
            key_info["id"] if key_info is not None else None,
            endpoint,
            0,
            latency,
            service="tavily",
        )
        raise HTTPException(status_code=502, detail=str(exc))


# ═══ Firecrawl 代理端点 ═══

@app.api_route("/firecrawl/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy_firecrawl(path: str, request: Request):
    raw_body, body_json = await parse_json_body(request)
    token_value = extract_token(request, body_json)
    token_row = get_token_row_or_401(token_value, "firecrawl")

    key_info = pool.get_next_key("firecrawl")
    if not key_info:
        raise HTTPException(status_code=503, detail="No available API keys")

    forward_content = raw_body
    if body_json is not None and "api_key" in body_json:
        body_json["api_key"] = key_info["key"]
        forward_content = json.dumps(body_json).encode("utf-8")

    start = time.time()
    try:
        resp = await http_client.request(
            request.method,
            f"{FIRECRAWL_API_BASE}/{path}",
            params=dict(request.query_params),
            content=forward_content if request.method != "GET" else None,
            headers=build_forward_headers(request, key_info["key"]),
        )
        latency = int((time.time() - start) * 1000)
        success = resp.status_code < 400
        pool.report_result("firecrawl", key_info["id"], success)
        db.log_usage(token_row["id"], key_info["id"], path, int(success), latency, service="firecrawl")
        content_type = resp.headers.get("content-type", "").lower()
        if "application/json" in content_type:
            return JSONResponse(content=resp.json(), status_code=resp.status_code)
        return forward_raw_response(resp)
    except Exception as exc:
        latency = int((time.time() - start) * 1000)
        pool.report_result("firecrawl", key_info["id"], False)
        db.log_usage(token_row["id"], key_info["id"], path, 0, latency, service="firecrawl")
        raise HTTPException(status_code=502, detail=str(exc))


@app.post("/exa/search")
async def proxy_exa_search(request: Request):
    raw_body, body_json = await parse_json_body(request)
    token_value = extract_token(request, body_json)
    token_row = get_token_row_or_401(token_value, "exa")

    key_info = pool.get_next_key("exa")
    if not key_info:
        raise HTTPException(status_code=503, detail="No available API keys")

    forward_content = raw_body
    if body_json is not None:
        sanitized_body = dict(body_json)
        sanitized_body.pop("api_key", None)
        forward_content = json.dumps(sanitized_body).encode("utf-8")

    start = time.time()
    try:
        resp = await http_client.post(
            f"{EXA_API_BASE}/search",
            params=dict(request.query_params),
            content=forward_content,
            headers=build_exa_forward_headers(request, key_info["key"]),
        )
        latency = int((time.time() - start) * 1000)
        success = resp.status_code < 400
        pool.report_result("exa", key_info["id"], success)
        db.log_usage(token_row["id"], key_info["id"], "search", int(success), latency, service="exa")
        content_type = resp.headers.get("content-type", "").lower()
        if "application/json" in content_type:
            return JSONResponse(content=resp.json(), status_code=resp.status_code)
        return forward_raw_response(resp)
    except Exception as exc:
        latency = int((time.time() - start) * 1000)
        pool.report_result("exa", key_info["id"], False)
        db.log_usage(token_row["id"], key_info["id"], "search", 0, latency, service="exa")
        raise HTTPException(status_code=502, detail=str(exc))


# ═══ Social / X 代理端点 ═══

@app.get("/social/health")
async def social_health():
    state = await resolve_social_gateway_state(force=False)
    return {
        "ok": bool(state["resolved_upstream_api_key"] and state["accepted_tokens"]),
        "mode": state["mode"],
        "upstream_base_url": state["upstream_base_url"],
        "upstream_responses_path": state["upstream_responses_path"],
        "admin_base_url": state["admin_base_url"],
        "model": state["model"],
        "fallback_model": state["fallback_model"],
        "fallback_min_results": state["fallback_min_results"],
        "token_source": state["token_source"],
        "admin_configured": state["admin_configured"],
        "admin_connected": state["admin_connected"],
        "accepted_token_count": len(state["accepted_tokens"]),
        "token_configured": bool(state["accepted_tokens"]),
        "upstream_key_configured": bool(state["resolved_upstream_api_key"]),
        "stats": state["stats"],
        "error": state["error"],
    }


@app.post("/social/search")
async def proxy_social_search(request: Request):
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Expected JSON request body")

    query = (body.get("query") or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="Missing query")

    source = (body.get("source") or "x").strip().lower()
    if source != "x":
        raise HTTPException(status_code=400, detail="Only source=x is supported")

    state = await resolve_social_gateway_state(force=False)
    token_value = extract_token(request, body)
    verify_social_gateway_token(token_value, state["accepted_tokens"])
    if not state["resolved_upstream_api_key"]:
        raise HTTPException(status_code=503, detail="Missing social upstream API key")

    max_results = max(1, min(int(body.get("max_results") or 5), 10))
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
    fallback_min_results = state.get("fallback_min_results", SOCIAL_GATEWAY_FALLBACK_MIN_RESULTS)
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


# ═══ 控制台 ═══

@app.get("/", response_class=HTMLResponse)
async def console(request: Request):
    return templates.TemplateResponse(
        "console.html",
        {
            "request": request,
            "base_url": str(request.base_url).rstrip("/"),
            "initial_authenticated": has_valid_admin_session(request),
        },
    )


# ═══ 管理 API ═══

@app.get("/api/session")
async def get_session(request: Request, _=Depends(verify_admin)):
    return {"ok": True}


@app.post("/api/session/login")
async def login_session(request: Request):
    body = await request.json()
    password = str((body or {}).get("password") or "").strip()
    if not password or not hmac.compare_digest(password, get_admin_password()):
        raise HTTPException(status_code=401, detail="Unauthorized")
    response = JSONResponse({"ok": True})
    apply_admin_session_cookie(response, request, password)
    return response


@app.post("/api/session/logout")
async def logout_session():
    response = JSONResponse({"ok": True})
    clear_admin_session_cookie(response)
    return response

@app.get("/api/stats")
async def stats(request: Request, _=Depends(verify_admin)):
    force = request.query_params.get("force", "").strip().lower() in {"1", "true", "yes", "on"}
    if force or STATS_CACHE_TTL_SECONDS <= 0:
        return await build_stats_payload(auto_sync=DASHBOARD_AUTO_SYNC_ON_STATS)

    now = time.monotonic()
    cached_value = stats_payload_cache["value"]
    if cached_value is not None and now < stats_payload_cache["expires_at"]:
        return cached_value

    async with stats_payload_lock:
        now = time.monotonic()
        cached_value = stats_payload_cache["value"]
        if cached_value is not None and now < stats_payload_cache["expires_at"]:
            return cached_value

        payload = await build_stats_payload(auto_sync=DASHBOARD_AUTO_SYNC_ON_STATS)
        stats_payload_cache["value"] = payload
        stats_payload_cache["expires_at"] = now + STATS_CACHE_TTL_SECONDS
        return payload


@app.get("/api/settings")
async def get_settings(request: Request, _=Depends(verify_admin)):
    return await build_settings_payload()


@app.put("/api/settings/tavily")
async def update_tavily_settings(request: Request, _=Depends(verify_admin)):
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Expected JSON request body")

    if "mode" in body:
        mode = str(body.get("mode") or "").strip().lower() or "pool"
        if mode not in {"pool", "upstream"}:
            raise HTTPException(status_code=400, detail="mode must be 'pool' or 'upstream'")
        db.set_setting("tavily_mode", mode)

    text_fields = {
        "upstream_base_url": "tavily_upstream_base_url",
        "upstream_search_path": "tavily_upstream_search_path",
        "upstream_extract_path": "tavily_upstream_extract_path",
    }
    for field, setting_key in text_fields.items():
        if field not in body:
            continue
        value = str(body.get(field) or "").strip()
        db.set_setting(setting_key, value)

    if body.get("clear_upstream_api_key"):
        db.set_setting("tavily_upstream_api_key", "")
    elif "upstream_api_key" in body:
        value = str(body.get("upstream_api_key") or "").strip()
        if value:
            db.set_setting("tavily_upstream_api_key", value)

    reset_stats_cache()
    return {
        "ok": True,
        **(await build_settings_payload()),
    }


@app.put("/api/settings/social")
async def update_social_settings(request: Request, _=Depends(verify_admin)):
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Expected JSON request body")

    text_fields = {
        "upstream_base_url": "social_upstream_base_url",
        "upstream_responses_path": "social_upstream_responses_path",
        "admin_base_url": "social_admin_base_url",
        "admin_verify_path": "social_admin_verify_path",
        "admin_config_path": "social_admin_config_path",
        "admin_tokens_path": "social_admin_tokens_path",
        "model": "social_model",
        "fallback_model": "social_fallback_model",
    }
    secret_fields = {
        "admin_app_key": "social_admin_app_key",
        "upstream_api_key": "social_upstream_api_key",
        "gateway_token": "social_gateway_token",
    }

    for field, setting_key in text_fields.items():
        if field not in body:
            continue
        value = str(body.get(field) or "").strip()
        db.set_setting(setting_key, value)

    if "cache_ttl_seconds" in body:
        try:
            cache_ttl_seconds = max(5, int(body.get("cache_ttl_seconds") or SOCIAL_GATEWAY_CACHE_TTL_SECONDS))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="cache_ttl_seconds must be an integer")
        db.set_setting("social_cache_ttl_seconds", str(cache_ttl_seconds))

    if "fallback_min_results" in body:
        try:
            fallback_min_results = max(
                1,
                int(body.get("fallback_min_results") or SOCIAL_GATEWAY_FALLBACK_MIN_RESULTS),
            )
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="fallback_min_results must be an integer")
        db.set_setting("social_fallback_min_results", str(fallback_min_results))

    for field, setting_key in secret_fields.items():
        if body.get(f"clear_{field}"):
            db.set_setting(setting_key, "")
            continue
        if field not in body:
            continue
        value = str(body.get(field) or "").strip()
        if value:
            db.set_setting(setting_key, value)

    reset_social_gateway_cache()
    return {
        "ok": True,
        **(await build_settings_payload()),
    }


@app.get("/api/keys")
async def list_keys(request: Request, _=Depends(verify_admin)):
    service = request.query_params.get("service")
    keys = mask_key_rows([dict(key) for key in db.get_all_keys(service)])
    return {"keys": keys}


@app.post("/api/usage/sync")
async def sync_usage(request: Request, _=Depends(verify_admin)):
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    service = get_service(body.get("service"), default="tavily")
    force = bool(body.get("force", True))
    key_id = body.get("key_id")
    result = await sync_usage_cache(force=force, key_id=key_id, service=service)
    reset_stats_cache()
    keys = [dict(key) for key in db.get_all_keys(service)]
    active_keys = [key for key in keys if key["active"]]
    return {
        "ok": True,
        "service": service,
        "result": result,
        "real_quota": build_real_quota_summary(active_keys),
    }


@app.post("/api/keys")
async def add_keys(request: Request, _=Depends(verify_admin)):
    body = await request.json()
    service = get_service(body.get("service"), default="tavily")
    if "file" in body:
        count = db.import_keys_from_text(body["file"], service=service)
        pool.reload(service)
        reset_stats_cache()
        return {"imported": count, "service": service}
    if "key" in body:
        db.add_key(body["key"], body.get("email", ""), service=service)
        pool.reload(service)
        reset_stats_cache()
        return {"ok": True, "service": service}
    raise HTTPException(status_code=400, detail="Provide 'key' or 'file'")


@app.delete("/api/keys/{key_id}")
async def remove_key(key_id: int, _=Depends(verify_admin)):
    key_row = db.get_key_by_id(key_id)
    db.delete_key(key_id)
    if key_row:
        pool.reload(key_row["service"])
    reset_stats_cache()
    return {"ok": True}


@app.put("/api/keys/{key_id}/toggle")
async def toggle_key(key_id: int, request: Request, _=Depends(verify_admin)):
    body = await request.json()
    db.toggle_key(key_id, body.get("active", 1))
    key_row = db.get_key_by_id(key_id)
    if key_row:
        pool.reload(key_row["service"])
    reset_stats_cache()
    return {"ok": True}


@app.get("/api/tokens")
async def list_tokens(request: Request, _=Depends(verify_admin)):
    service = request.query_params.get("service")
    tokens = [dict(token) for token in db.get_all_tokens(service)]
    for token in tokens:
        token["stats"] = db.get_usage_stats(token_id=token["id"], service=token["service"])
    return {"tokens": tokens}


@app.post("/api/tokens")
async def create_token(request: Request, _=Depends(verify_admin)):
    body = await request.json()
    service = get_token_service(body.get("service"), default="tavily")
    token = db.create_token(body.get("name", ""), service=service)
    reset_stats_cache()
    return {"token": dict(token)}


@app.delete("/api/tokens/{token_id}")
async def remove_token(token_id: int, _=Depends(verify_admin)):
    db.delete_token(token_id)
    reset_stats_cache()
    return {"ok": True}


@app.put("/api/password")
async def change_password(request: Request, _=Depends(verify_admin)):
    body = await request.json()
    new_pwd = body.get("password", "").strip()
    if not new_pwd or len(new_pwd) < 4:
        raise HTTPException(status_code=400, detail="Password too short (min 4)")
    db.set_setting("admin_password", new_pwd)
    response = JSONResponse({"ok": True})
    apply_admin_session_cookie(response, request, new_pwd)
    return response
