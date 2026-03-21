"""Web search tool implementation."""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse

import requests

from tools.base import ToolDefinition, ToolExecutionResult


WEB_SEARCH_PARAMETERS:dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "Search query to look up on the web."
        }
    },
    "required": ["query"],
    "additionalProperties": False
}


def build_web_search_tool_definitions() -> list[ToolDefinition]:
    """Build web-search tool definitions.

    Args:
        None

    Returns:
        list[ToolDefinition]: Web-search tool definitions.
    """

    definitions:list[ToolDefinition] = []

    def web_search(arguments:dict[str, Any]) -> ToolExecutionResult:
        query = str(arguments["query"])
        return ToolExecutionResult(output = perform_web_search(query = query))

    definitions.append(
        ToolDefinition(
            name = "web_search",
            description = "Search the web and return a concise summary of the top results.",
            parameters = WEB_SEARCH_PARAMETERS,
            handler = web_search
        )
    )

    return definitions


def perform_web_search(query:str) -> str:
    """Search the web and summarize the top results.

    Args:
        query: Search query string.

    Returns:
        str: Summary of the top search results or an error message.
    """

    print(f"[WebSearch] Searching the web for: {query}")


    try:
        response = requests.get(
            url = "https://www.googleapis.com/customsearch/v1",
            params = {
                "key": os.getenv("GOOGLE_SEARCH"),
                "cx": "3555a0babf4b94fb6",
                "q": query,
                "lr": "lang_en",
                "num": 3,
                "safe": "active"
            },
            timeout = 10
        )
        response.raise_for_status()
        results = response.json()
    except Exception as e:
        print(f"[WebSearch] Error during web search: {e}")
        return f"Sorry, I couldn't perform the web search at this time. Pass this error message to the user: {e}"

    items = results.get("items", [])
    if not items:
        print("[WebSearch] No search results returned")
        return f"No web results found for: {query}"

    search_results = f"## Web Search\nYou searched for {query} and found the following:\n\n"
    for item in items:
        link = str(item.get("link", ""))
        source = urlparse(link).netloc or "unknown source"
        title = str(item.get("title", "Untitled result"))
        snippet = str(item.get("snippet", "No snippet available."))
        search_results += f"- {snippet} (Source: {title} from {source})\n\n"

    return search_results
