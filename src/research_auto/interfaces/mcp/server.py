from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from research_auto.interfaces.mcp.tools import (
    McpPaperLookupError,
    get_paper_tool,
    search_context_tool,
    search_papers_tool,
)


def build_mcp_server(*, read_service: Any, read_repository: Any) -> FastMCP:
    server = FastMCP(
        "research-auto",
        streamable_http_path="/",
        transport_security=TransportSecuritySettings(
            allowed_hosts=["127.0.0.1", "localhost", "testserver"]
        ),
    )

    @server.tool()
    async def search_papers(query: str, limit: int = 10) -> dict[str, Any]:
        return search_papers_tool(read_service=read_service, query=query, limit=limit)

    @server.tool()
    async def get_paper(id: str) -> dict[str, Any]:
        try:
            return get_paper_tool(read_service=read_service, paper_id=id)
        except McpPaperLookupError as exc:
            raise ValueError(f"paper not found: {exc}") from exc

    @server.tool()
    async def search_context(
        query: str, paper_id: str | None = None, limit: int = 8
    ) -> dict[str, Any]:
        return search_context_tool(
            repository=read_repository,
            query=query,
            paper_id=paper_id,
            limit=limit,
        )

    return server


def build_mcp_http_app(*, read_service: Any, read_repository: Any) -> Any:
    server = build_mcp_server(
        read_service=read_service,
        read_repository=read_repository,
    )
    return server.streamable_http_app()
