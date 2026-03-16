"""
多服务 API Proxy — FastAPI 主服务
"""
import asyncio
import hmac
import json
import os
import re
import time
from datetime import datetime, timezone

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates

import database as db
from key_pool import pool

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin")
TAVILY_API_BASE = "https://api.tavily.com"
FIRECRAWL_API_BASE = "https://api.firecrawl.dev"


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
SERVICE_LABELS = {
    "tavily": "Tavily",
    "firecrawl": "Firecrawl",
}

app = FastAPI(title="Tavily / Firecrawl API Proxy")
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))
http_client = httpx.AsyncClient(timeout=60)
social_gateway_state_cache = {"expires_at": 0.0, "value": None}
social_gateway_state_lock = asyncio.Lock()


def get_admin_password():
    return db.get_setting("admin_password", ADMIN_PASSWORD)


def get_service(service_value, default="tavily"):
    try:
        return db.normalize_service(service_value or default)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def reset_social_gateway_cache():
    social_gateway_state_cache["expires_at"] = 0.0
    social_gateway_state_cache["value"] = None


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
    try:
        cache_ttl_seconds = max(5, int(cache_ttl_raw or SOCIAL_GATEWAY_CACHE_TTL_SECONDS))
    except (TypeError, ValueError):
        cache_ttl_seconds = SOCIAL_GATEWAY_CACHE_TTL_SECONDS

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


# ═══ Auth helpers ═══

def verify_admin(request: Request):
    auth = request.headers.get("Authorization", "")
    password = request.headers.get("X-Admin-Password", "")
    pwd = get_admin_password()
    if auth == f"Bearer {pwd}" or password == pwd:
        return True
    raise HTTPException(status_code=401, detail="Unauthorized")


def extract_token(request: Request, body: dict = None):
    """从请求中提取代理 token。"""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
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
    if not accepted_tokens:
        raise HTTPException(status_code=503, detail="Social gateway is not configured")
    if not token_value:
        raise HTTPException(status_code=401, detail="Missing API token")
    if not any(hmac.compare_digest(token_value, expected) for expected in accepted_tokens):
        raise HTTPException(status_code=401, detail="Invalid token")


def get_token_row_or_401(token_value, service):
    if not token_value:
        raise HTTPException(status_code=401, detail="Missing API token")
    token_row = db.get_token_by_value(token_value)
    if not token_row or token_row["service"] != service:
        raise HTTPException(status_code=401, detail="Invalid token")
    return token_row


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
    rows = []
    if key_id is not None:
        row = db.get_key_by_id(key_id)
        if row and (service is None or row["service"] == service):
            rows = [dict(row)]
    else:
        rows = [dict(row) for row in db.get_all_keys(service)]

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


async def build_service_dashboard(service):
    service = get_service(service)
    sync_result = await sync_usage_cache(force=False, service=service)
    overview = db.get_usage_stats(service=service)
    tokens = [dict(token) for token in db.get_all_tokens(service)]
    for token in tokens:
        token["stats"] = db.get_usage_stats(token_id=token["id"], service=service)
    keys = mask_key_rows([dict(key) for key in db.get_all_keys(service)])
    active_keys = [key for key in keys if key["active"]]
    return {
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


async def build_social_dashboard():
    state = await resolve_social_gateway_state(force=False)
    return {
        "service": "social",
        "label": "Social / X",
        "mode": state["mode"],
        "model": state["model"],
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
    config = get_runtime_social_config()
    state = await resolve_social_gateway_state(force=False)
    return {
        "social": {
            "upstream_base_url": config["upstream_base_url"],
            "upstream_responses_path": config["upstream_responses_path"],
            "admin_base_url": config["admin_base_url"],
            "admin_verify_path": config["admin_verify_path"],
            "admin_config_path": config["admin_config_path"],
            "admin_tokens_path": config["admin_tokens_path"],
            "model": config["model"],
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


def normalize_social_search_response(query, payload, max_results):
    text = extract_response_text(payload)
    structured = extract_json_object(text)
    parsed_results = structured.get("results") if isinstance(structured, dict) else []
    answer = (structured.get("answer") or "").strip() if isinstance(structured, dict) else ""
    if not answer:
        answer = text

    results = []
    for item in parsed_results or []:
        normalized = normalize_result_item(item)
        if normalized is None:
            continue
        results.append(normalized)
        if len(results) >= max_results:
            break

    citations = []
    seen_urls = set()
    for item in extract_upstream_citations(payload):
        url = item.get("url", "")
        if url and url in seen_urls:
            continue
        if url:
            seen_urls.add(url)
        citations.append({"title": item.get("title", ""), "url": url})

    if citations:
        existing_urls = {item.get("url", "") for item in results if item.get("url")}
        for citation in citations:
            url = citation.get("url", "")
            if not url or url in existing_urls or len(results) >= max_results:
                continue
            results.append(
                {
                    "title": citation.get("title", "") or url,
                    "url": url,
                    "text": "",
                    "content": "",
                    "snippet": "",
                    "author": "",
                    "handle": "",
                    "created_at": "",
                    "why_relevant": "",
                }
            )
            existing_urls.add(url)
    else:
        citations = [
            {"title": item.get("title", ""), "url": item.get("url", "")}
            for item in results
            if item.get("url")
        ]

    return {
        "query": query,
        "answer": answer,
        "results": results,
        "citations": citations,
        "tool_usage": {
            "social_search_calls": 1,
            "model": payload.get("model") or SOCIAL_GATEWAY_MODEL,
        },
        "raw_text": text,
    }


# ═══ 启动 ═══

@app.on_event("startup")
def startup():
    db.init_db()


# ═══ Tavily 代理端点 ═══

@app.post("/api/search")
@app.post("/api/extract")
async def proxy_tavily(request: Request):
    body = await request.json()
    endpoint = request.url.path.replace("/api/", "")

    token_value = extract_token(request, body)
    token_row = get_token_row_or_401(token_value, "tavily")

    ok, reason = db.check_quota(
        token_row["id"],
        token_row["hourly_limit"],
        token_row["daily_limit"],
        token_row["monthly_limit"],
        service="tavily",
    )
    if not ok:
        raise HTTPException(status_code=429, detail=reason)

    key_info = pool.get_next_key("tavily")
    if not key_info:
        raise HTTPException(status_code=503, detail="No available API keys")

    body["api_key"] = key_info["key"]
    start = time.time()
    try:
        resp = await http_client.post(f"{TAVILY_API_BASE}/{endpoint}", json=body)
        latency = int((time.time() - start) * 1000)
        success = resp.status_code == 200
        pool.report_result("tavily", key_info["id"], success)
        db.log_usage(token_row["id"], key_info["id"], endpoint, int(success), latency, service="tavily")
        return JSONResponse(content=resp.json(), status_code=resp.status_code)
    except Exception as exc:
        latency = int((time.time() - start) * 1000)
        pool.report_result("tavily", key_info["id"], False)
        db.log_usage(token_row["id"], key_info["id"], endpoint, 0, latency, service="tavily")
        raise HTTPException(status_code=502, detail=str(exc))


# ═══ Firecrawl 代理端点 ═══

@app.api_route("/firecrawl/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy_firecrawl(path: str, request: Request):
    raw_body, body_json = await parse_json_body(request)
    token_value = extract_token(request, body_json)
    token_row = get_token_row_or_401(token_value, "firecrawl")

    ok, reason = db.check_quota(
        token_row["id"],
        token_row["hourly_limit"],
        token_row["daily_limit"],
        token_row["monthly_limit"],
        service="firecrawl",
    )
    if not ok:
        raise HTTPException(status_code=429, detail=reason)

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

    upstream_payload = build_social_search_upstream_payload(body, state["model"])
    max_results = max(1, min(int(body.get("max_results") or 5), 10))

    try:
        response = await http_client.post(
            f"{state['upstream_base_url']}{state['upstream_responses_path']}",
            json=upstream_payload,
            headers={"Authorization": f"Bearer {state['resolved_upstream_api_key']}"},
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    try:
        upstream_body = response.json()
    except Exception:
        raise HTTPException(
            status_code=502,
            detail=response.text[:300] or "Upstream returned non-JSON",
        )

    if response.status_code >= 400:
        detail = ""
        if isinstance(upstream_body, dict):
            error = upstream_body.get("error") or {}
            if isinstance(error, dict):
                detail = error.get("message") or ""
            if not detail:
                detail = upstream_body.get("detail") or ""
        if not detail:
            detail = str(upstream_body)[:300]
        raise HTTPException(status_code=response.status_code, detail=detail)

    return normalize_social_search_response(query, upstream_body, max_results)


# ═══ 控制台 ═══

@app.get("/", response_class=HTMLResponse)
async def console(request: Request):
    return templates.TemplateResponse(
        "console.html",
        {
            "request": request,
            "base_url": str(request.base_url).rstrip("/"),
        },
    )


# ═══ 管理 API ═══

@app.get("/api/stats")
async def stats(request: Request, _=Depends(verify_admin)):
    tavily_stats, firecrawl_stats, social_stats = await asyncio.gather(
        build_service_dashboard("tavily"),
        build_service_dashboard("firecrawl"),
        build_social_dashboard(),
    )
    return {
        "services": {
            "tavily": tavily_stats,
            "firecrawl": firecrawl_stats,
        },
        "social": social_stats,
    }


@app.get("/api/settings")
async def get_settings(request: Request, _=Depends(verify_admin)):
    return await build_settings_payload()


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
        return {"imported": count, "service": service}
    if "key" in body:
        db.add_key(body["key"], body.get("email", ""), service=service)
        pool.reload(service)
        return {"ok": True, "service": service}
    raise HTTPException(status_code=400, detail="Provide 'key' or 'file'")


@app.delete("/api/keys/{key_id}")
async def remove_key(key_id: int, _=Depends(verify_admin)):
    key_row = db.get_key_by_id(key_id)
    db.delete_key(key_id)
    if key_row:
        pool.reload(key_row["service"])
    return {"ok": True}


@app.put("/api/keys/{key_id}/toggle")
async def toggle_key(key_id: int, request: Request, _=Depends(verify_admin)):
    body = await request.json()
    db.toggle_key(key_id, body.get("active", 1))
    key_row = db.get_key_by_id(key_id)
    if key_row:
        pool.reload(key_row["service"])
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
    service = get_service(body.get("service"), default="tavily")
    token = db.create_token(body.get("name", ""), service=service)
    return {"token": dict(token)}


@app.delete("/api/tokens/{token_id}")
async def remove_token(token_id: int, _=Depends(verify_admin)):
    db.delete_token(token_id)
    return {"ok": True}


@app.put("/api/password")
async def change_password(request: Request, _=Depends(verify_admin)):
    body = await request.json()
    new_pwd = body.get("password", "").strip()
    if not new_pwd or len(new_pwd) < 4:
        raise HTTPException(status_code=400, detail="Password too short (min 4)")
    db.set_setting("admin_password", new_pwd)
    return {"ok": True}
