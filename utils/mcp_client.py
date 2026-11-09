"""
MCP client used by the agents to call core banking tools.

It launches mcp_server/banking_mcp_server.py as a subprocess and talks to it
over stdio (the standard MCP local transport). One client is shared by all
emails in a batch; tool calls can run concurrently (asyncio.gather).
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

from mcp import Client, StdioServerParameters

from config.settings import PROJECT_ROOT, get_settings


class BankingMCPClient:
    def __init__(self):
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "mcp_server.banking_mcp_server"],
            cwd=str(PROJECT_ROOT),
            # the SDK only forwards a few "safe" variables by default; the server
            # needs our settings (CORE_API_PORT, ...) too
            env=dict(os.environ),
        )
        self._client = Client(params)
        self.available_tools: list[str] = []

    async def __aenter__(self) -> "BankingMCPClient":
        await self._client.__aenter__()
        tools = await self._client.list_tools()
        self.available_tools = [t.name for t in tools.tools]
        return self

    async def __aexit__(self, *exc) -> None:
        await self._client.__aexit__(*exc)

    async def call(self, tool: str, arguments: dict[str, Any]) -> tuple[dict, int]:
        """Call a tool. Returns (envelope, duration_ms). Never raises."""
        settings = get_settings()
        started = time.perf_counter()
        # generous outer timeout: the server already applies per-request timeouts + retries
        outer_timeout = settings.core_api_timeout_seconds * (settings.core_api_max_retries + 2)
        try:
            result = await self._client.call_tool(tool, arguments, read_timeout_seconds=outer_timeout)
            text = "".join(getattr(block, "text", "") for block in result.content)
            envelope = result.structured_content or (json.loads(text) if text else {})
            if result.is_error:
                envelope = {"ok": False, "error": {"code": "TOOL_ERROR", "message": text, "retryable": False}}
            # MCP may wrap non-object results in {"result": ...}
            if "ok" not in envelope and isinstance(envelope.get("result"), dict):
                envelope = envelope["result"]
        except Exception as exc:  # noqa: BLE001
            envelope = {"ok": False, "error": {"code": "MCP_ERROR", "message": str(exc), "retryable": True}}
        return envelope, int((time.perf_counter() - started) * 1000)
