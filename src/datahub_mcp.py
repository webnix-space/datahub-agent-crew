"""
DataHub MCP client wrapper.

Wraps mcp-server-datahub over stdio. This is the ONLY module that talks to
DataHub directly — every agent goes through here, never around it.

Verified working (2026-07-26, manual scratch scripts) end-to-end for:
  - TOOLS_IS_MUTATION_ENABLED=true must be passed explicitly via env=,
    the subprocess does NOT reliably inherit shell env otherwise.
  - update_description(entity_urn, operation, description, column_path)
    writes to EditableDatasetProperties (the correct overlay layer —
    never write to the ingested DatasetProperties, re-ingestion wipes it).

NOT yet verified in this build — confirm tool names via list_tools() the
first time you run this against the live Codespace, then delete this
comment:
  - whatever search/browse tool this server exposes for "find datasets
    with X gap" (search_across_entities? list_datasets? unconfirmed)
  - the read-side call to fetch current EditableDatasetProperties /
    ownership / glossary terms for a given urn

Do not hardcode a tool name you haven't seen in list_tools() output.
Call list_tools() once at startup, log it, and fail loud if a tool this
code depends on isn't present — silent fallback to guessed names is how
you burn a day debugging a typo three days before deadline.
"""
import asyncio
import json
import logging
import os
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

logger = logging.getLogger(__name__)


class DataHubMCPError(RuntimeError):
    pass


class DataHubMCP:
    """
    Thin async wrapper around one long-lived MCP session to mcp-server-datahub.

    Usage:
        async with DataHubMCP() as dh:
            tools = await dh.list_tool_names()
            result = await dh.call_tool("update_description", {...})
    """

    def __init__(self, command: str = "uvx", args: list[str] | None = None):
        self.command = command
        self.args = args or ["mcp-server-datahub"]
        self._session: ClientSession | None = None
        self._stdio_ctx = None
        self._tool_names: set[str] = set()

    async def __aenter__(self) -> "DataHubMCP":
        env = {**os.environ, "TOOLS_IS_MUTATION_ENABLED": "true"}
        params = StdioServerParameters(command=self.command, args=self.args, env=env)
        self._stdio_ctx = stdio_client(params)
        read, write = await self._stdio_ctx.__aenter__()
        self._session = ClientSession(read, write)
        await self._session.__aenter__()
        await self._session.initialize()

        tools_resp = await self._session.list_tools()
        self._tool_names = {t.name for t in tools_resp.tools}
        logger.info(f"[DataHubMCP] connected, {len(self._tool_names)} tools: {sorted(self._tool_names)}")
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self._session is not None:
            await self._session.__aexit__(exc_type, exc, tb)
        if self._stdio_ctx is not None:
            await self._stdio_ctx.__aexit__(exc_type, exc, tb)

    def has_tool(self, name: str) -> bool:
        return name in self._tool_names

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        if name not in self._tool_names:
            raise DataHubMCPError(
                f"Tool '{name}' not in server's tool list: {sorted(self._tool_names)}. "
                f"Don't guess names — run list_tools() and check the real schema."
            )
        result = await self._session.call_tool(name, arguments)
        if result.isError:
            raise DataHubMCPError(f"Tool '{name}' returned error: {result.content}")
        # MCP tool results come back as a list of content blocks; text blocks
        # are the common case for this server.
        texts = [c.text for c in result.content if getattr(c, "type", None) == "text"]
        joined = "\n".join(texts)
        try:
            return json.loads(joined)
        except (json.JSONDecodeError, TypeError):
            return joined

    async def update_description(
        self,
        entity_urn: str,
        description: str,
        operation: str = "replace",
        column_path: str | None = None,
    ) -> Any:
        """Verified working. operation in {replace, append, remove}."""
        return await self.call_tool(
            "update_description",
            {
                "entity_urn": entity_urn,
                "operation": operation,
                "description": description,
                "column_path": column_path,
            },
        )


def run_async(coro):
    """Convenience for calling from sync agent code."""
    return asyncio.run(coro)
