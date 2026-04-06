"""Minimal OpenAI-compatible chat client."""

from __future__ import annotations

from typing import Any

from openai import OpenAI

from app.config import LlmClientConfig


class LlmClient:
    """Thin wrapper around an OpenAI-compatible chat completion client."""

    def __init__(self, config:LlmClientConfig, name:str):
        self._config = config
        self._name = name
        self._client = OpenAI(
            api_key = config.api_key,
            base_url = config.base_url
        )

    def create_tool_completion(
        self,
        messages:list[dict[str, Any]],
        tools:list[dict[str, Any]]
    ) -> Any:
        """Request the next model turn with function tools enabled.

        Args:
            messages: Chat messages to send to the model.
            tools: OpenAI-compatible function tool definitions.

        Returns:
            Any: Assistant message returned by the OpenAI SDK.
        """

        print(
            f"[LlmClient:{self._name}] Sending tool completion with {len(messages)} messages and "
            f"{len(tools)} tools"
        )
        response = self._client.chat.completions.create(
            model = self._config.model,
            messages = messages,
            tools = tools,
            parallel_tool_calls = False
        )
        message = response.choices[0].message
        print(
            f"[LlmClient:{self._name}] Received assistant message. "
            f"tool_calls={len(message.tool_calls or [])}, content={message.content!r}"
        )
        return message

    def create_web_search_response(self, query:str) -> Any:
        """Request a Responses API web-search completion for a natural-language query.

        Args:
            query: Natural-language search prompt with full context and motivation.

        Returns:
            Any: Responses API payload returned by the OpenAI SDK.
        """

        print(f"[LlmClient:{self._name}] Sending web search response request for query: {query}")
        response = self._client.responses.create(
            model = self._config.model,
            input = query,
            include = ["web_search_call.action.sources"],
            tools = [
                {
                    "type": "web_search",
                    "search_context_size": "medium"
                }
            ]
        )
        print(
            f"[LlmClient:{self._name}] Received web search response. "
            f"output_items={len(response.output)}, output_text={response.output_text!r}"
        )
        return response


