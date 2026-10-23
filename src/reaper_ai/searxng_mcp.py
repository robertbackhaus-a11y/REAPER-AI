# Copyright (c) 2026 Robert Backhaus

"""Small stdio MCP bridge for an operator-provided SearXNG JSON endpoint."""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from mcp.server.mcpserver import MCPServer


DEFAULT_SEARXNG_URL = "http://127.0.0.1/searxng"
server = MCPServer(
    name="reaper-searxng",
    description="Search an operator-provided SearXNG instance through its JSON API.",
)


def _base_url() -> str:
    return os.environ.get("SEARXNG_URL", DEFAULT_SEARXNG_URL).strip().rstrip("/") or DEFAULT_SEARXNG_URL


@server.tool(
    name="web_search",
    description="Search the configured SearXNG instance and return result titles, URLs, and descriptions.",
)
def web_search(query: str, limit: int = 5) -> dict[str, Any]:
    """Run one bounded SearXNG JSON search without fetching result pages."""
    if not isinstance(query, str) or not query.strip():
        return {"ok": False, "error": "query must be a non-empty string", "results": []}
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
        return {"ok": False, "error": "limit must be an integer from 1 to 20", "results": []}

    url = f"{_base_url()}/search?{urlencode({'q': query.strip(), 'format': 'json'})}"
    try:
        with urlopen(url, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": f"SearXNG search failed: {exc}", "results": []}

    raw_results = payload.get("results", []) if isinstance(payload, dict) else []
    results = [
        {
            "title": str(item.get("title", "")),
            "url": str(item.get("url", "")),
            "description": str(item.get("content", "")),
        }
        for item in raw_results
        if isinstance(item, dict) and item.get("url")
    ]
    return {"ok": True, "query": query.strip(), "results": results[:limit]}


if __name__ == "__main__":
    server.run(transport="stdio")
