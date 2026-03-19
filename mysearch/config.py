"""MySearch 通用配置。"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


MODULE_DIR = Path(__file__).resolve().parent
ROOT_DIR = MODULE_DIR.parent
AuthMode = Literal["bearer", "body"]
XAISearchMode = Literal["official", "compatible"]
MCPTransport = Literal["stdio", "sse", "streamable-http"]


def _load_env_file(env_path: Path) -> None:
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value[:1] == value[-1:] and value[:1] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _load_mapping_env(raw_env: dict[str, object]) -> None:
    for key, value in raw_env.items():
        if not isinstance(value, str):
            continue
        cleaned = value.strip()
        if not cleaned:
            continue
        os.environ.setdefault(key, cleaned)


def _load_codex_mcp_env() -> None:
    config_path = Path(os.getenv("CODEX_HOME", "~/.codex")).expanduser() / "config.toml"
    if not config_path.exists():
        return

    try:
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return

    env = ((data.get("mcp_servers") or {}).get("mysearch") or {}).get("env") or {}
    if isinstance(env, dict):
        _load_mapping_env(env)


def _load_dotenv() -> None:
    # .env 只作为本地单仓调试兜底，不覆盖宿主已注入的配置。
    for env_path in (MODULE_DIR / ".env", ROOT_DIR / ".env"):
        _load_env_file(env_path)


def _bootstrap_runtime_env() -> None:
    _load_codex_mcp_env()
    _load_dotenv()


def _get_str(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value is not None and value.strip():
            return value.strip()
    return default


def _get_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return int(value.strip())


def _get_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _get_list(*names: str) -> list[str]:
    for name in names:
        value = os.getenv(name)
        if value is None or not value.strip():
            continue
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def _normalize_base_url(url: str) -> str:
    return url.rstrip("/")


def _normalize_path(path: str) -> str:
    if not path:
        return ""
    if not path.startswith("/"):
        return f"/{path}"
    return path


def _resolve_path(*names: str, default_name: str | None = None) -> Path | None:
    raw = _get_str(*names)
    if raw:
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = ROOT_DIR / candidate
        return candidate

    if default_name:
        candidate = ROOT_DIR / default_name
        if candidate.exists():
            return candidate
    return None


def _provider_base_url(
    *,
    explicit_names: tuple[str, ...],
    proxy_base_url: str,
    default: str,
) -> str:
    explicit = _get_str(*explicit_names)
    if explicit:
        return _normalize_base_url(explicit)
    if proxy_base_url:
        return _normalize_base_url(proxy_base_url)
    return _normalize_base_url(default)


def _provider_path(
    *,
    explicit_name: str,
    proxy_base_url: str,
    proxy_default: str,
    default: str,
) -> str:
    explicit = _get_str(explicit_name)
    if explicit:
        return _normalize_path(explicit)
    if proxy_base_url:
        return _normalize_path(proxy_default)
    return _normalize_path(default)


_bootstrap_runtime_env()


@dataclass(slots=True)
class ProviderConfig:
    name: str
    base_url: str
    auth_mode: AuthMode
    auth_header: str
    auth_scheme: str
    auth_field: str
    default_paths: dict[str, str]
    alternate_base_urls: dict[str, str] = field(default_factory=dict)
    search_mode: XAISearchMode = "official"
    api_keys: list[str] = field(default_factory=list)
    keys_file: Path | None = None

    def path(self, key: str) -> str:
        return self.default_paths.get(key, "")

    def base_url_for(self, key: str) -> str:
        return self.alternate_base_urls.get(key) or self.base_url


@dataclass(slots=True)
class MySearchConfig:
    server_name: str
    timeout_seconds: int
    xai_model: str
    max_parallel_workers: int
    search_cache_ttl_seconds: int
    extract_cache_ttl_seconds: int
    mcp_host: str
    mcp_port: int
    mcp_mount_path: str
    mcp_sse_path: str
    mcp_streamable_http_path: str
    mcp_stateless_http: bool
    tavily: ProviderConfig
    firecrawl: ProviderConfig
    exa: ProviderConfig
    xai: ProviderConfig

    @classmethod
    def from_env(cls) -> "MySearchConfig":
        proxy_base_url = _get_str("MYSEARCH_PROXY_BASE_URL")
        proxy_api_key = _get_str("MYSEARCH_PROXY_API_KEY")
        return cls(
            server_name=_get_str("MYSEARCH_NAME", "MYSEARCH_SERVER_NAME", default="MySearch"),
            timeout_seconds=_get_int("MYSEARCH_TIMEOUT_SECONDS", 45),
            xai_model=_get_str(
                "MYSEARCH_XAI_MODEL",
                default="grok-4.20-beta-latest-non-reasoning",
            ),
            max_parallel_workers=max(1, _get_int("MYSEARCH_MAX_PARALLEL_WORKERS", 4)),
            search_cache_ttl_seconds=max(0, _get_int("MYSEARCH_SEARCH_CACHE_TTL_SECONDS", 30)),
            extract_cache_ttl_seconds=max(0, _get_int("MYSEARCH_EXTRACT_CACHE_TTL_SECONDS", 300)),
            mcp_host=_get_str("MYSEARCH_MCP_HOST", default="127.0.0.1"),
            mcp_port=_get_int("MYSEARCH_MCP_PORT", 8000),
            mcp_mount_path=_normalize_path(_get_str("MYSEARCH_MCP_MOUNT_PATH", default="/")),
            mcp_sse_path=_normalize_path(_get_str("MYSEARCH_MCP_SSE_PATH", default="/sse")),
            mcp_streamable_http_path=_normalize_path(
                _get_str("MYSEARCH_MCP_STREAMABLE_HTTP_PATH", default="/mcp")
            ),
            mcp_stateless_http=_get_bool("MYSEARCH_MCP_STATELESS_HTTP", False),
            tavily=ProviderConfig(
                name="tavily",
                base_url=_provider_base_url(
                    explicit_names=("MYSEARCH_TAVILY_BASE_URL",),
                    proxy_base_url=proxy_base_url,
                    default="https://api.tavily.com",
                ),
                auth_mode=_get_str(
                    "MYSEARCH_TAVILY_AUTH_MODE",
                    default="bearer" if proxy_base_url else "body",
                ),  # type: ignore[arg-type]
                auth_header=_get_str("MYSEARCH_TAVILY_AUTH_HEADER", default="Authorization"),
                auth_scheme=_get_str("MYSEARCH_TAVILY_AUTH_SCHEME", default="Bearer"),
                auth_field=_get_str("MYSEARCH_TAVILY_AUTH_FIELD", default="api_key"),
                default_paths={
                    "search": _provider_path(
                        explicit_name="MYSEARCH_TAVILY_SEARCH_PATH",
                        proxy_base_url=proxy_base_url,
                        proxy_default="/api/search",
                        default="/search",
                    ),
                    "extract": _provider_path(
                        explicit_name="MYSEARCH_TAVILY_EXTRACT_PATH",
                        proxy_base_url=proxy_base_url,
                        proxy_default="/api/extract",
                        default="/extract",
                    ),
                },
                api_keys=[
                    *_get_list("MYSEARCH_TAVILY_API_KEYS"),
                    *(
                        [_get_str("MYSEARCH_TAVILY_API_KEY")]
                        if _get_str("MYSEARCH_TAVILY_API_KEY")
                        else ([proxy_api_key] if proxy_api_key else [])
                    ),
                ],
                keys_file=_resolve_path(
                    "MYSEARCH_TAVILY_KEYS_FILE",
                    "MYSEARCH_TAVILY_ACCOUNTS_FILE",
                    default_name="accounts.txt",
                ),
            ),
            firecrawl=ProviderConfig(
                name="firecrawl",
                base_url=_provider_base_url(
                    explicit_names=("MYSEARCH_FIRECRAWL_BASE_URL",),
                    proxy_base_url=proxy_base_url,
                    default="https://api.firecrawl.dev",
                ),
                auth_mode=_get_str("MYSEARCH_FIRECRAWL_AUTH_MODE", default="bearer"),  # type: ignore[arg-type]
                auth_header=_get_str("MYSEARCH_FIRECRAWL_AUTH_HEADER", default="Authorization"),
                auth_scheme=_get_str("MYSEARCH_FIRECRAWL_AUTH_SCHEME", default="Bearer"),
                auth_field=_get_str("MYSEARCH_FIRECRAWL_AUTH_FIELD", default="api_key"),
                default_paths={
                    "search": _provider_path(
                        explicit_name="MYSEARCH_FIRECRAWL_SEARCH_PATH",
                        proxy_base_url=proxy_base_url,
                        proxy_default="/firecrawl/v2/search",
                        default="/v2/search",
                    ),
                    "scrape": _provider_path(
                        explicit_name="MYSEARCH_FIRECRAWL_SCRAPE_PATH",
                        proxy_base_url=proxy_base_url,
                        proxy_default="/firecrawl/v2/scrape",
                        default="/v2/scrape",
                    ),
                },
                api_keys=[
                    *_get_list("MYSEARCH_FIRECRAWL_API_KEYS"),
                    *(
                        [_get_str("MYSEARCH_FIRECRAWL_API_KEY")]
                        if _get_str("MYSEARCH_FIRECRAWL_API_KEY")
                        else ([proxy_api_key] if proxy_api_key else [])
                    ),
                ],
                keys_file=_resolve_path(
                    "MYSEARCH_FIRECRAWL_KEYS_FILE",
                    "MYSEARCH_FIRECRAWL_ACCOUNTS_FILE",
                    default_name="firecrawl_accounts.txt",
                ),
            ),
            exa=ProviderConfig(
                name="exa",
                base_url=_provider_base_url(
                    explicit_names=("MYSEARCH_EXA_BASE_URL",),
                    proxy_base_url=proxy_base_url,
                    default="https://api.exa.ai",
                ),
                auth_mode=_get_str("MYSEARCH_EXA_AUTH_MODE", default="bearer"),  # type: ignore[arg-type]
                auth_header=_get_str(
                    "MYSEARCH_EXA_AUTH_HEADER",
                    default="Authorization" if proxy_base_url else "x-api-key",
                ),
                auth_scheme=_get_str(
                    "MYSEARCH_EXA_AUTH_SCHEME",
                    default="Bearer" if proxy_base_url else "",
                ),
                auth_field=_get_str("MYSEARCH_EXA_AUTH_FIELD", default="api_key"),
                default_paths={
                    "search": _provider_path(
                        explicit_name="MYSEARCH_EXA_SEARCH_PATH",
                        proxy_base_url=proxy_base_url,
                        proxy_default="/exa/search",
                        default="/search",
                    ),
                },
                api_keys=[
                    *_get_list("MYSEARCH_EXA_API_KEYS"),
                    *(
                        [_get_str("MYSEARCH_EXA_API_KEY")]
                        if _get_str("MYSEARCH_EXA_API_KEY")
                        else ([proxy_api_key] if proxy_api_key else [])
                    ),
                ],
                keys_file=_resolve_path(
                    "MYSEARCH_EXA_KEYS_FILE",
                    "MYSEARCH_EXA_ACCOUNTS_FILE",
                    default_name="exa_accounts.txt",
                ),
            ),
            xai=ProviderConfig(
                name="xai",
                base_url=_normalize_base_url(
                    _get_str("MYSEARCH_XAI_BASE_URL", default="https://api.x.ai/v1")
                ),
                auth_mode=_get_str("MYSEARCH_XAI_AUTH_MODE", default="bearer"),  # type: ignore[arg-type]
                auth_header=_get_str("MYSEARCH_XAI_AUTH_HEADER", default="Authorization"),
                auth_scheme=_get_str("MYSEARCH_XAI_AUTH_SCHEME", default="Bearer"),
                auth_field=_get_str("MYSEARCH_XAI_AUTH_FIELD", default="api_key"),
                default_paths={
                    "responses": _normalize_path(
                        _get_str("MYSEARCH_XAI_RESPONSES_PATH", default="/responses")
                    ),
                    "social_search": _provider_path(
                        explicit_name="MYSEARCH_XAI_SOCIAL_SEARCH_PATH",
                        proxy_base_url=proxy_base_url,
                        proxy_default="/social/search",
                        default="/social/search",
                    ),
                },
                alternate_base_urls={
                    "social_search": _normalize_base_url(
                        _get_str("MYSEARCH_XAI_SOCIAL_BASE_URL") or proxy_base_url
                    )
                },
                search_mode=_get_str(
                    "MYSEARCH_XAI_SEARCH_MODE",
                    default="compatible" if proxy_base_url else "official",
                ),  # type: ignore[arg-type]
                api_keys=[
                    *_get_list("MYSEARCH_XAI_API_KEYS"),
                    *(
                        [_get_str("MYSEARCH_XAI_API_KEY")]
                        if _get_str("MYSEARCH_XAI_API_KEY")
                        else ([proxy_api_key] if proxy_api_key else [])
                    ),
                ],
                keys_file=_resolve_path("MYSEARCH_XAI_KEYS_FILE"),
            ),
        )
