"""Central registry for callable agent tools."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from app.discord_utils import DiscordChannelCategory
from tools import build_tool_definitions
from tools.base import ToolExecutionResult


logger = logging.getLogger(__name__)


@dataclass(frozen = True)
class ExecutedToolCall:
    """Structured result of executing a registered tool call.

    Args:
        tool_name: Registered tool name.
        arguments: Parsed tool arguments.
        result: Return payload from the tool implementation.

    Returns:
        ExecutedToolCall: Executed call metadata and result.
    """

    tool_name:str
    arguments:dict[str, Any]
    result:ToolExecutionResult


class ToolRegistry:
    """Registry of executable tools and their OpenAI schemas."""

    def __init__(
        self,
        project_root:Path,
        markdown_publisher:Callable[[DiscordChannelCategory, str, str], None] | None = None
    ):
        self._project_root = project_root.resolve()
        self._definitions = {
            definition.name: definition
            for definition in build_tool_definitions(
                project_root = self._project_root,
                markdown_publisher = markdown_publisher
            )
        }
        logger.info("Registered tools: %s", ", ".join(sorted(self._definitions)))

    def get_tool_names(self) -> list[str]:
        """Return the list of supported tool names.

        Args:
            None

        Returns:
            list[str]: Supported tool names.
        """

        return sorted(self._definitions)

    def get_openai_tools(self) -> list[dict[str, Any]]:
        """Build OpenAI-compatible tool definitions.

        Args:
            None

        Returns:
            list[dict[str, Any]]: Tool schema payloads for the API request.
        """

        return [
            definition.to_openai_tool()
            for definition in self._definitions.values()
        ]

    def execute_tool_call(self, tool_name:str, arguments_json:str) -> ExecutedToolCall:
        """Execute a registered tool from a model-emitted tool call.

        Args:
            tool_name: Name of the tool selected by the model.
            arguments_json: Raw JSON arguments from the tool call.

        Returns:
            ExecutedToolCall: Parsed arguments and execution result.
        """

        logger.info("Executing tool call: %s", tool_name)
        definition = self._definitions.get(tool_name)
        if definition is None:
            raise ValueError(f"Unknown tool: {tool_name}")

        arguments = self._parse_arguments(
            tool_name = tool_name,
            arguments_json = arguments_json
        )
        result = definition.handler(arguments)
        return ExecutedToolCall(
            tool_name = tool_name,
            arguments = arguments,
            result = result
        )

    def _parse_arguments(self, tool_name:str, arguments_json:str) -> dict[str, Any]:
        """Parse JSON arguments emitted by a model tool call.

        Args:
            tool_name: Name of the tool being invoked.
            arguments_json: Raw JSON arguments string.

        Returns:
            dict[str, Any]: Parsed argument object.
        """

        try:
            parsed_arguments = json.loads(arguments_json or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON arguments for {tool_name}: {exc}") from exc

        if not isinstance(parsed_arguments, dict):
            raise ValueError(f"Tool arguments for {tool_name} must be an object")

        logger.debug("Parsed args for %s: %s", tool_name, parsed_arguments)
        return parsed_arguments
