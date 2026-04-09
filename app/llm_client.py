"""Minimal OpenAI-compatible Responses client."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openai import OpenAI

from app.config import LlmClientConfig


@dataclass(frozen = True)
class ResponseFunctionCall:
    """Normalized function call emitted by the Responses API.

    Args:
        call_id: Stable call id used to correlate tool outputs.
        name: Name of the function to execute.
        arguments_json: Raw JSON argument payload.

    Returns:
        ResponseFunctionCall: Parsed function call metadata.
    """

    call_id:str
    name:str
    arguments_json:str


@dataclass(frozen = True)
class ToolResponseResult:
    """Normalized result returned from one Responses API turn.

    Args:
        response_id: Response id returned by the SDK.
        text_output: Flattened assistant text output, if any.
        function_calls: Function calls returned in this turn.
        output_items: Raw output items normalized into dictionaries.

    Returns:
        ToolResponseResult: Parsed response payload for the agent loop.
    """

    response_id:str
    text_output:str
    function_calls:list[ResponseFunctionCall]
    output_items:list[dict[str, Any]]


class LlmClient:
    """Thin wrapper around an OpenAI-compatible Responses client."""

    def __init__(self, config:LlmClientConfig, name:str):
        self._config = config
        self._name = name
        self._client = OpenAI(
            api_key = config.api_key,
            base_url = config.base_url
        )

    def create_tool_response(
        self,
        instructions:str,
        input_items:list[dict[str, Any]],
        tools:list[dict[str, Any]]
    ) -> ToolResponseResult:
        """Request the next model turn with function tools enabled.

        Args:
            instructions: System instructions passed through the Responses API.
            input_items: Responses input items for the current conversation state.
            tools: OpenAI-compatible function tool definitions.

        Returns:
            ToolResponseResult: Normalized response payload.
        """

        print(
            f"[LlmClient:{self._name}] Sending tool response with {len(input_items)} input items and "
            f"{len(tools)} tools"
        )

        try:
            response = self._client.responses.create(
                model = self._config.model,
                instructions = instructions,
                input = input_items,
                tools = tools,
                parallel_tool_calls = False
            )
        except Exception as exc:
            provider_error_message = self._build_provider_error_message(exc = exc)
            if provider_error_message:
                raise RuntimeError(provider_error_message) from exc
            raise

        function_calls = self._extract_function_calls(response = response)
        output_items = self._normalize_output_items(response = response)
        print(
            f"[LlmClient:{self._name}] Received tool response. "
            f"response_id={response.id}, output_items={len(output_items)}, "
            f"function_calls={len(function_calls)}, text_output={response.output_text!r}"
        )
        return ToolResponseResult(
            response_id = response.id,
            text_output = response.output_text or "",
            function_calls = function_calls,
            output_items = output_items
        )

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

    def _extract_function_calls(self, response:Any) -> list[ResponseFunctionCall]:
        """Extract normalized function calls from a Responses SDK payload.

        Args:
            response: Raw Responses SDK object.

        Returns:
            list[ResponseFunctionCall]: Normalized function calls in order.
        """

        function_calls:list[ResponseFunctionCall] = []
        for output_item in getattr(response, "output", []):
            if getattr(output_item, "type", "") != "function_call":
                continue

            function_calls.append(
                ResponseFunctionCall(
                    call_id = getattr(output_item, "call_id", ""),
                    name = getattr(output_item, "name", ""),
                    arguments_json = getattr(output_item, "arguments", "") or "{}"
                )
            )

        return function_calls

    def _normalize_output_items(self, response:Any) -> list[dict[str, Any]]:
        """Convert Responses SDK output items into serializable dictionaries.

        Args:
            response: Raw Responses SDK object.

        Returns:
            list[dict[str, Any]]: Serializable output items.
        """

        normalized_items:list[dict[str, Any]] = []
        for output_item in getattr(response, "output", []):
            normalized_items.append(self._normalize_sdk_item(item = output_item))
        return normalized_items

    def _normalize_sdk_item(self, item:Any) -> dict[str, Any]:
        """Convert one SDK item into a serializable dictionary.

        Args:
            item: SDK item object or plain dict.

        Returns:
            dict[str, Any]: Serializable item payload.
        """

        if isinstance(item, dict):
            return item

        model_dump = getattr(item, "model_dump", None)
        if callable(model_dump):
            return model_dump(mode = "python")

        if hasattr(item, "__dict__"):
            return dict(vars(item))

        raise TypeError(f"Unsupported Responses SDK output item: {type(item)!r}")

    def _build_provider_error_message(self, exc:Exception) -> str | None:
        """Return a clearer error when a custom provider lacks Responses support.

        Args:
            exc: Original SDK exception.

        Returns:
            str | None: Friendly provider-specific message when applicable.
        """

        if not self._config.base_url:
            return None

        error_text = str(exc).lower()
        unsupported_markers = [
            "/responses",
            "responses",
            "404",
            "not found",
            "unsupported",
            "unknown"
        ]
        if not any(marker in error_text for marker in unsupported_markers):
            return None

        return (
            f"Configured provider at {self._config.base_url} does not appear to support the "
            f"OpenAI Responses API or Responses-style tool calling. Original error: {exc}"
        )
