"""Web search through Tavily (tavily-python)."""

from __future__ import annotations

import os
from typing import Any

TOOL_SPEC: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Search the live web with Tavily. Returns a short answer plus sources.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to search for"},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
            },
            "required": ["query"],
        },
    },
}


def tavily_search(
    query: str,
    max_results: int = 5,
    api_key: str | None = None,
    client: Any | None = None,
) -> dict[str, Any]:
    if client is None:
        key = api_key or os.environ.get("TAVILY_API_KEY", "")
        if not key:
            return {"error": "TAVILY_API_KEY is not set", "results": []}
        from tavily import TavilyClient

        client = TavilyClient(api_key=key)

    raw = client.search(query=query, max_results=max_results, include_answer=True)
    results = [
        {
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "content": (r.get("content") or "")[:500],
        }
        for r in raw.get("results", [])
    ]
    return {"query": query, "answer": raw.get("answer"), "results": results}
