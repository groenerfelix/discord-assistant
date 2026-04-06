"""Web search tool implementation."""

from __future__ import annotations

from typing import Any

from app.llm_client import LlmClient
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


def build_web_search_tool_definitions(llm_client:LlmClient) -> list[ToolDefinition]:
    """Build web-search tool definitions.

    Args:
        llm_client: Shared LLM client used to call the OpenAI Responses API.

    Returns:
        list[ToolDefinition]: Web-search tool definitions.
    """

    definitions:list[ToolDefinition] = []

    def web_search(arguments:dict[str, Any]) -> ToolExecutionResult:
        query = str(arguments["query"])
        return ToolExecutionResult(
            output = perform_web_search(
                query = query,
                llm_client = llm_client
            )
        )

    definitions.append(
        ToolDefinition(
            name = "web_search",
            description = (
                "Search the web with a natural-language query that includes all relevant "
                "context and motivation, then return the response."
            ),
            parameters = WEB_SEARCH_PARAMETERS,
            handler = web_search
        )
    )

    return definitions


def perform_web_search(query:str, llm_client:LlmClient) -> str:
    """Search the web with the Responses API and return the answer with sources.

    Args:
        query: Natural-language search query string.
        llm_client: Shared LLM client used to call the OpenAI Responses API.

    Returns:
        str: Search response text or an error message.
    """

    print(f"[WebSearch] Searching the web for: {query}")

    try:
        response = llm_client.create_web_search_response(query = query)
    except Exception as e:
        print(f"[WebSearch] Error during web search: {e}")
        return f"Sorry, I couldn't perform the web search at this time. Pass this error message to the user: {e}"

    response_text = (response.output_text or "").strip()
    if not response_text:
        print("[WebSearch] No response text returned")
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
        # f"Query: {query}\n\n"
        f"{response_text}"
    )
    if source_urls:
        search_results += "\n\nSources:\n"
        for source_url in source_urls:
            search_results += f"- {source_url}\n"

    return search_results
