"""MCP stdio server — thin adapter over synapse.tools.runtime.

Principal is bound at process start from SYNAPSE_MCP_TOKEN (JWT) or a
local service identity (tenant + admin). Tool args never carry tenant_id.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from synapse.auth.principal import Principal
from synapse.auth.tokens import decode_access_token
from synapse.memory.ltm import LongTermMemory
from synapse.platform.config import get_settings
from synapse.platform.db import Database
from synapse.platform.seed import session_factory
from synapse.rag.embeddings import Embedder
from synapse.tools.runtime import TOOL_DOCS, TOOLS, ToolSession, call_tool


def _tool_schemas() -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "description": TOOL_DOCS.get(name, name),
            "inputSchema": schema.model_json_schema(),
        }
        for name, (schema, _) in TOOLS.items()
    ]


def _principal_from_env(settings) -> Principal:  # type: ignore[no-untyped-def]
    token = os.environ.get("SYNAPSE_MCP_TOKEN")
    if token:
        return decode_access_token(token, settings.jwt_secret.get_secret_value())
    tenant = os.environ.get("SYNAPSE_MCP_TENANT_ID", "tnt_nw")
    user = os.environ.get("SYNAPSE_MCP_USER_ID", "usr_mcp_service")
    return Principal(tenant_id=tenant, user_id=user, role="admin", email="mcp@synapse.local")


async def _handle(session: ToolSession, message: dict[str, Any]) -> dict[str, Any]:
    mid = message.get("id")
    method = message.get("method")
    params = message.get("params") or {}
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": mid,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "synapse-cutover", "version": "0.1.0"},
            },
        }
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": _tool_schemas()}}
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        result = await call_tool(session, name, args)
        return {
            "jsonrpc": "2.0",
            "id": mid,
            "result": {"content": [{"type": "text", "text": json.dumps(result)}]},
        }
    return {
        "jsonrpc": "2.0",
        "id": mid,
        "error": {"code": -32601, "message": f"method not found: {method}"},
    }


async def main_async() -> None:
    settings = get_settings()
    principal = _principal_from_env(settings)
    db = Database(settings.database_dsn())
    await db.connect()
    factory = session_factory(db.engine)
    embedder = None
    if settings.openai_api_key:
        embedder = Embedder(settings.openai_api_key.get_secret_value())
    tool_session = ToolSession(
        principal=principal,
        sessions=factory,
        embedder=embedder,
        ltm=LongTermMemory(factory),
    )
    loop = asyncio.get_event_loop()
    try:
        while True:
            line = await loop.run_in_executor(None, sys_stdin_readline)
            if not line:
                break
            message = json.loads(line)
            response = await _handle(tool_session, message)
            print(json.dumps(response), flush=True)
    finally:
        await db.close()


def sys_stdin_readline() -> str:
    import sys

    return sys.stdin.readline()


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
