"""Connect the loop to an MCP server over stdio."""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .loop import Executor
from .models import ToolSpec


@asynccontextmanager
async def mcp_tools(command: str, args: list[str], cwd: str | None = None
                    ) -> AsyncIterator[tuple[list[ToolSpec], Executor, str]]:
    """Yields (tool specs, executor, the server's instructions text or "")."""
    params = StdioServerParameters(command=command, args=args, cwd=cwd)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            instructions = getattr(init, "instructions", None) or ""
            listed = await session.list_tools()
            specs = [ToolSpec(t.name, t.description or "", t.input_schema) for t in listed.tools]

            async def execute(name: str, arguments: dict) -> tuple[str, bool]:
                res = await session.call_tool(name, arguments)
                if getattr(res, "structured_content", None) is not None:
                    text = json.dumps(res.structured_content)
                else:
                    text = "\n".join(getattr(c, "text", "") for c in getattr(res, "content", []))
                return text, bool(getattr(res, "is_error", False))

            yield specs, execute, instructions
