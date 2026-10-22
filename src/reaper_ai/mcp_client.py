# Copyright (c) 2026 Robert Backhaus

"""Small native MCP client adapter used by Sentinel's existing dispatcher."""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

from mcp import Client, StdioServerParameters

from reaper_ai.persistence import default_database_path


class MCPClientError(ValueError):
    """A configuration, connection, timeout, or MCP tool error."""


# The API passes a project's selected server IDs to the run subprocess here.
SELECTION_ENV = "REAPER_MCP_SERVERS"
# Server definitions captured at run-start discovery. Later edits of mcp.json
# therefore apply to subsequent runs without changing a run that is in flight.
_run_servers: dict[str, dict[str, Any]] | None = None


def _error_text(exc: Exception) -> str:
    """Keep SDK exceptions useful when their string form is empty."""
    return str(exc).strip() or type(exc).__name__


def configuration_path() -> Path:
    """Return the optional local MCP configuration beside REAPER application data."""
    configured = os.environ.get("REAPER_MCP_CONFIG", "").strip()
    return Path(configured).expanduser() if configured else default_database_path().parent / "mcp.json"


def _document(path: Path) -> dict[str, Any]:
    """Read the whole mcp.json document so edits keep unrelated top-level keys."""
    if not path.is_file():
        return {"mcpServers": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MCPClientError(f"Cannot read MCP configuration {path}: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("mcpServers"), dict):
        raise MCPClientError(f"MCP configuration {path} requires an object named mcpServers")
    return payload


def _configuration() -> tuple[Path, dict[str, dict[str, Any]]]:
    path = configuration_path()
    servers = _document(path)["mcpServers"]
    return path, {name: value for name, value in servers.items() if isinstance(name, str) and isinstance(value, dict)}


def _selected_server_ids() -> set[str] | None:
    """Project runs receive their selected server IDs; ``None`` means no project filter."""
    raw = os.environ.get(SELECTION_ENV)
    if raw is None:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MCPClientError(f"{SELECTION_ENV} must be a JSON list of server IDs") from exc
    if not isinstance(value, list):
        raise MCPClientError(f"{SELECTION_ENV} must be a JSON list of server IDs")
    return {item for item in value if isinstance(item, str)}


def _active_servers() -> dict[str, dict[str, Any]]:
    """Servers a run may use: enabled in mcp.json and, for projects, selected by the project."""
    _, servers = _configuration()
    selected = _selected_server_ids()
    return {name: server for name, server in servers.items()
            if not server.get("disabled") and (selected is None or name in selected)}


def _timeout(server: dict[str, Any]) -> float:
    value = server.get("timeout", 60)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise MCPClientError("MCP server timeout must be a positive number of seconds")
    return float(value)


def transport(server: dict[str, Any]) -> str:
    url = server.get("url")
    return "http" if isinstance(url, str) and url.strip() else "stdio"


def _client(server: dict[str, Any]) -> Client:
    timeout = _timeout(server)
    if transport(server) == "http":
        return Client(server["url"], read_timeout_seconds=timeout)
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
    global _run_servers
    try:
        servers = _active_servers()
    except MCPClientError as exc:
        result["errors"].append({"server": "configuration", "error": str(exc)})
        return result
    _run_servers = servers
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
    # A resumed run skips discovery; it then uses the currently allowed servers.
    servers = _run_servers if _run_servers is not None else _active_servers()
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


# Global MCP management for the web settings. mcp.json stays the only source of
# server definitions; projects store just the selected server IDs.
_SERVER_ID_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")


def validate_server(server_id: str, server: Any) -> dict[str, Any]:
    """Check one definition without connecting; return it unchanged when valid."""
    if not isinstance(server_id, str) or not server_id or not set(server_id) <= _SERVER_ID_CHARS:
        raise MCPClientError("MCP server ID may only contain letters, digits, '.', '_' and '-'")
    if not isinstance(server, dict):
        raise MCPClientError("MCP server definition must be a JSON object")
    if "disabled" in server and not isinstance(server["disabled"], bool):
        raise MCPClientError("MCP server disabled must be true or false")
    url = server.get("url")
    if url is not None and (not isinstance(url, str) or
                            not url.strip().lower().startswith(("http://", "https://"))):
        raise MCPClientError("MCP server url must start with http:// or https://")
    _client(server)  # Raises MCPClientError for invalid stdio fields or timeout; does not connect.
    return server


def _write_document(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def list_servers() -> dict[str, Any]:
    path, servers = _configuration()
    items = []
    for name, server in servers.items():
        try:
            validate_server(name, server)
            problem = ""
        except MCPClientError as exc:
            problem = str(exc)
        items.append({"id": name, "transport": transport(server), "enabled": not server.get("disabled"),
                      "config": server, "config_error": problem})
    return {"config_path": str(path), "exists": path.is_file(), "servers": items}


def save_server(server_id: str, server: dict[str, Any], previous_id: str | None = None) -> dict[str, Any]:
    """Add, replace, or rename one server in mcp.json, keeping its position and other keys."""
    validate_server(server_id, server)
    path = configuration_path()
    payload = _document(path)
    current: dict[str, Any] = payload["mcpServers"]
    previous_id = previous_id or server_id
    if previous_id != server_id and server_id in current:
        raise MCPClientError(f"MCP server {server_id} already exists")
    if previous_id in current:
        payload["mcpServers"] = {(server_id if key == previous_id else key): (server if key == previous_id else value)
                                 for key, value in current.items()}
    else:
        current[server_id] = server
    _write_document(path, payload)
    return server


def delete_server(server_id: str) -> bool:
    path = configuration_path()
    payload = _document(path)
    if server_id not in payload["mcpServers"]:
        return False
    del payload["mcpServers"][server_id]
    _write_document(path, payload)
    return True


def _parameters(schema: dict[str, Any]) -> list[dict[str, Any]]:
    """Summarize a tool's top-level JSON-schema arguments for display."""
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    required = set(schema.get("required") or []) if isinstance(schema.get("required"), list) else set()
    return [{"name": name, "type": str(spec.get("type", "")) if isinstance(spec, dict) else "",
             "required": name in required,
             "description": str(spec.get("description", "")) if isinstance(spec, dict) else ""}
            for name, spec in properties.items()]


def check_server(server_id: str) -> dict[str, Any]:
    """Connect once to one configured server and list its tools with descriptions."""
    _, servers = _configuration()
    server = servers.get(server_id)
    if server is None:
        raise MCPClientError(f"Unknown MCP server: {server_id}")
    started = time.monotonic()
    try:
        validate_server(server_id, server)
        tools = asyncio.run(asyncio.wait_for(_list_server_tools(server_id, server), timeout=_timeout(server)))
    except Exception as exc:
        return {"id": server_id, "ok": False, "error": _error_text(exc), "tools": [],
                "duration_ms": int((time.monotonic() - started) * 1000)}
    return {"id": server_id, "ok": True, "error": "", "duration_ms": int((time.monotonic() - started) * 1000),
            "tools": [{"name": tool["name"], "description": tool["description"],
                       "parameters": _parameters(tool["input_schema"])} for tool in tools]}
