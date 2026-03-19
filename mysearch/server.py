"""MySearch MCP Server."""

from __future__ import annotations

from dataclasses import replace
from typing import Literal

from mcp.server.fastmcp import FastMCP

from mysearch.clients import MySearchClient
from mysearch.config import MCPTransport, MySearchConfig


def _ensure_list(value: str | list[str] | None) -> list[str] | None:
    """兼容单值字符串输入，避免模型把单元素数组写成标量时直接校验失败。"""
    if value is None:
        return None
    if isinstance(value, str):
        return [value]
    return value


def _ensure_sources(
    value: Literal["web", "x"] | list[Literal["web", "x"]] | None,
) -> list[Literal["web", "x"]] | None:
    """兼容 sources 传单个字符串的情况。"""
    if value is None:
        return None
    if isinstance(value, str):
        return [value]
    return value


def build_mcp(config: MySearchConfig) -> tuple[MySearchClient, FastMCP]:
    client = MySearchClient(config)
    mcp = FastMCP(
        config.server_name,
        json_response=True,
        host=config.mcp_host,
        port=config.mcp_port,
        mount_path=config.mcp_mount_path,
        sse_path=config.mcp_sse_path,
        streamable_http_path=config.mcp_streamable_http_path,
        stateless_http=config.mcp_stateless_http,
    )

    @mcp.tool()
    def search(
        query: str,
        mode: Literal["auto", "web", "news", "social", "docs", "research", "github", "pdf"] = "auto",
        intent: Literal[
            "auto",
            "factual",
            "status",
            "comparison",
            "tutorial",
            "exploratory",
            "news",
            "resource",
        ] = "auto",
        strategy: Literal["auto", "fast", "balanced", "verify", "deep"] = "auto",
        provider: Literal["auto", "tavily", "firecrawl", "exa", "xai"] = "auto",
        sources: list[Literal["web", "x"]] | None = None,
        max_results: int = 5,
        include_content: bool = False,
        include_answer: bool = True,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
        allowed_x_handles: list[str] | None = None,
        excluded_x_handles: list[str] | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
        include_x_images: bool = False,
        include_x_videos: bool = False,
    ) -> dict:
        """统一搜索入口。按任务类型自动选择 Tavily / Firecrawl / Exa / xAI。"""
        return client.search(
            query=query,
            mode=mode,
            intent=intent,
            strategy=strategy,
            provider=provider,
            sources=sources,
            max_results=max_results,
            include_content=include_content,
            include_answer=include_answer,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
            allowed_x_handles=allowed_x_handles,
            excluded_x_handles=excluded_x_handles,
            from_date=from_date,
            to_date=to_date,
            include_x_images=include_x_images,
            include_x_videos=include_x_videos,
        )

    @mcp.tool()
    def extract_url(
        url: str,
        formats: list[str] | None = None,
        only_main_content: bool = True,
        provider: Literal["auto", "firecrawl", "tavily"] = "auto",
    ) -> dict:
        """抓取单个 URL 的正文，默认优先 Firecrawl；失败或空正文时回退 Tavily extract。"""
        return client.extract_url(
            url=url,
            formats=formats,
            only_main_content=only_main_content,
            provider=provider,
        )

    @mcp.tool()
    def research(
        query: str,
        web_max_results: int = 5,
        social_max_results: int = 5,
        scrape_top_n: int = 3,
        include_social: bool = True,
        mode: Literal["auto", "web", "news", "social", "docs", "research", "github", "pdf"] = "auto",
        intent: Literal[
            "auto",
            "factual",
            "status",
            "comparison",
            "tutorial",
            "exploratory",
            "news",
            "resource",
        ] = "auto",
        strategy: Literal["auto", "fast", "balanced", "verify", "deep"] = "auto",
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
        allowed_x_handles: list[str] | None = None,
        excluded_x_handles: list[str] | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> dict:
        """小型研究工作流：网页发现 + 正文抓取 + 可选 X 舆情补充。"""
        return client.research(
            query=query,
            web_max_results=web_max_results,
            social_max_results=social_max_results,
            scrape_top_n=scrape_top_n,
            include_social=include_social,
            mode=mode,
            intent=intent,
            strategy=strategy,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
            allowed_x_handles=allowed_x_handles,
            excluded_x_handles=excluded_x_handles,
            from_date=from_date,
            to_date=to_date,
        )

    @mcp.tool()
    def mysearch_health() -> dict:
        """查看 MySearch 当前 provider 配置、search mode、auth 模式、base URL 和 key 可用性。"""
        return client.health()

    return client, mcp


CONFIG = MySearchConfig.from_env()
CLIENT, mcp = build_mcp(CONFIG)


def main(
    *,
    transport: MCPTransport = "stdio",
    host: str | None = None,
    port: int | None = None,
    mount_path: str | None = None,
    sse_path: str | None = None,
    streamable_http_path: str | None = None,
    stateless_http: bool | None = None,
) -> None:
    config = replace(
        CONFIG,
        mcp_host=host or CONFIG.mcp_host,
        mcp_port=port or CONFIG.mcp_port,
        mcp_mount_path=mount_path or CONFIG.mcp_mount_path,
        mcp_sse_path=sse_path or CONFIG.mcp_sse_path,
        mcp_streamable_http_path=streamable_http_path or CONFIG.mcp_streamable_http_path,
        mcp_stateless_http=CONFIG.mcp_stateless_http if stateless_http is None else stateless_http,
    )
    _, server = build_mcp(config)
    server.run(transport=transport, mount_path=config.mcp_mount_path)


if __name__ == "__main__":
    main()
