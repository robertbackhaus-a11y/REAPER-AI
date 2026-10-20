# Copyright (c) 2026 Robert Backhaus

"""Small native MCP client adapter used by Sentinel's existing dispatcher."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from mcp import Client, StdioServerParameters

from reaper_ai.persistence import default_database_path


class MCPClientError(ValueError):
    """A configuration, connection, timeout, or MCP tool error."""


def _error_text(exc: Exception) -> str:
    """Keep SDK exceptions useful when their string form is empty."""
    return str(exc).strip() or type(exc).__name__


def configuration_path() -> Path:
    """Return the optional local MCP configuration beside REAPER application data."""
    configured = os.environ.get("REAPER_MCP_CONFIG", "").strip()
    return Path(configured).expanduser() if configured else default_database_path().parent / "mcp.json"


def _configuration() -> tuple[Path, dict[str, dict[str, Any]]]:
    path = configuration_path()
    if not path.is_file():
        return path, {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MCPClientError(f"Cannot read MCP configuration {path}: {exc}") from exc
    servers = payload.get("mcpServers") if isinstance(payload, dict) else None
    if not isinstance(servers, dict):
        raise MCPClientError(f"MCP configuration {path} requires an object named mcpServers")
    return path, {name: value for name, value in servers.items() if isinstance(name, str) and isinstance(value, dict)}


def _timeout(server: dict[str, Any]) -> float:
    value = server.get("timeout", 60)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise MCPClientError("MCP server timeout must be a positive number of seconds")
    return float(value)


def _client(server: dict[str, Any]) -> Client:
    timeout = _timeout(server)
    url = server.get("url")
    if isinstance(url, str) and url.strip():
        return Client(url, read_timeout_seconds=timeout)
    command = server.get("command")
    args = server.get("args", [])
    if not isinstance(command, str) or not command.strip():
        raise MCPClientError("MCP server requires either a url or a command")
    if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
        raise MCPClientError("MCP server args must be a list of strings")
    environment = server.get("env")
    if environment is not None and (not isinstance(environment, dict) or
                                    not all(isinstance(key, str) and isinstance(value, str)
                                            for key, value in environment.items())):
        raise MCPClientError("MCP server env must be an object of string values")
    cwd = server.get("cwd")
    if cwd is not None and not isinstance(cwd, str):
        raise MCPClientError("MCP server cwd must be a string")
    return Client(StdioServerParameters(command=command, args=args, env=environment, cwd=cwd),
                  read_timeout_seconds=timeout)


def _tool_data(server_name: str, tool: Any) -> dict[str, Any]:
    schema = getattr(tool, "input_schema", {})
    return {
        "tool": f"mcp:{server_name}:{getattr(tool, 'name', '')}",
        "server": server_name,
        "name": getattr(tool, "name", ""),
        "description": getattr(tool, "description", "") or "",
        "input_schema": schema if isinstance(schema, dict) else {},
    }


async def _list_server_tools(server_name: str, server: dict[str, Any]) -> list[dict[str, Any]]:
    async with _client(server) as client:
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            response = await client.list_tools(cursor=cursor)
            tools.extend(_tool_data(server_name, tool) for tool in response.tools)
            cursor = getattr(response, "next_cursor", None)
            if not cursor:
                return tools


def discover_mcp_tools() -> dict[str, Any]:
    """Connect once per run preparation and return dynamically advertised tools."""
    path = configuration_path()
    result: dict[str, Any] = {"config_path": str(path), "tools": [], "errors": []}
    try:
        _, servers = _configuration()
    except MCPClientError as exc:
        result["errors"].append({"server": "configuration", "error": str(exc)})
        return result
    for name, server in servers.items():
        try:
            tools = asyncio.run(asyncio.wait_for(_list_server_tools(name, server), timeout=_timeout(server)))
            result["tools"].extend(tools)
        except Exception as exc:
            # Discovery is advisory: an unavailable external server must not
            # prevent normal local tools from starting a run.
            result["errors"].append({"server": name, "error": _error_text(exc)})
    return result


def _resolve_tool(qualified_name: str) -> tuple[str, str, dict[str, Any]]:
    _, servers = _configuration()
    for server_name in sorted(servers, key=len, reverse=True):
        prefix = f"mcp:{server_name}:"
        if qualified_name.startswith(prefix):
            tool_name = qualified_name[len(prefix):]
            if tool_name:
                return server_name, tool_name, servers[server_name]
    raise MCPClientError(f"Unknown MCP tool: {qualified_name}")


def _json_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return value


async def _call_server_tool(server: dict[str, Any], tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    async with _client(server) as client:
        # The SDK retains the current schema for validation/header support. This
        # is an action-local refresh, not a discovery pass for every decision.
        await client.list_tools()
        response = await client.call_tool(tool_name, arguments, read_timeout_seconds=_timeout(server))
        content = _json_value(getattr(response, "content", []))
        structured = _json_value(getattr(response, "structured_content", None))
        is_error = bool(getattr(response, "is_error", False))
        text = "\n".join(item.get("text", "") for item in content if isinstance(item, dict) and
                         isinstance(item.get("text"), str))
        return {"ok": not is_error, "content": content, "structured_content": structured,
                "is_error": is_error, "error": text if is_error else ""}


def call_mcp_tool(qualified_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Execute one configured MCP tool and normalize its SDK result."""
    if not isinstance(arguments, dict):
        raise MCPClientError("MCP tool arguments must be an object")
    server_name, tool_name, server = _resolve_tool(qualified_name)
    try:
        result = asyncio.run(asyncio.wait_for(_call_server_tool(server, tool_name, arguments),
                                               timeout=_timeout(server)))
    except Exception as exc:
        raise MCPClientError(f"MCP {server_name}/{tool_name} failed: {_error_text(exc)}") from exc
    result.update({"server": server_name, "name": tool_name})
    return result
