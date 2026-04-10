"""Web search tool implementation."""

from __future__ import annotations

import logging
import os
from typing import Any

from tools.base import ToolDefinition, ToolExecutionResult

from app.llm_client import LlmClient, LlmClientConfig


logger = logging.getLogger(__name__)

LLM_SEARCH_CLIENT = LlmClient(
    config = LlmClientConfig(
        api_key = os.getenv("OPENAI", ""),
        model = "gpt-5-nano",
        base_url = None
    ),
    name = "web_search"
)


WEB_SEARCH_PARAMETERS:dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "Search query to look up on the web. Use full sentences, natural language, and provide necessary context such as motivation."
        }
    },
    "required": ["query"],
    "additionalProperties": False
}


def build_web_search_tool_definitions() -> list[ToolDefinition]:
    """Build web-search tool definitions.

    Args:
        llm_client: Dedicated OpenAI client used to call the Responses API.

    Returns:
        list[ToolDefinition]: Web-search tool definitions.
    """

    definitions:list[ToolDefinition] = []

    def web_search(arguments:dict[str, Any]) -> ToolExecutionResult:
        query = str(arguments["query"])
        return ToolExecutionResult(
            output = perform_web_search(query = query)
        )

    definitions.append(
        ToolDefinition(
            name = "web_search",
            description = (
                "Search the web."
            ),
            parameters = WEB_SEARCH_PARAMETERS,
            handler = web_search
        )
    )

    return definitions


def perform_web_search(query:str) -> str:
    """Search the web with the Responses API and return the answer with sources.

    Args:
        query: Natural-language search query string.

    Returns:
        str: Search response text or an error message.
    """

    logger.info("Searching the web for: %s", query)

    try:
        response = LLM_SEARCH_CLIENT.create_web_search_response(query = query)
    except Exception as e:
        logger.error("Error during web search: %s", e)
        return f"Sorry, I couldn't perform the web search at this time. Pass this error message to the user: {e}"

    response_text = (response.output_text or "").strip()
    if not response_text:
        logger.warning("No response text returned")
        return f"No web results found for: {query}"

    source_urls:list[str] = []
    for output_item in response.output:
        if output_item.type != "web_search_call":
            continue

        action = getattr(output_item, "action", None)
        sources = getattr(action, "sources", None)
        if not sources:
            continue

        for source in sources:
            source_url = getattr(source, "url", "")
            if source_url and source_url not in source_urls:
                source_urls.append(source_url)

    search_results = (
        "## Web Search Results\n"
        f"{response_text}"
    )
    if source_urls:
        search_results += "\n\nSources:\n"
        for source_url in source_urls:
            search_results += f"- {source_url}\n"

    return search_results
