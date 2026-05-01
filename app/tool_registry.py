"""Central registry for callable agent tools."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from agents import FunctionTool, RunContextWrapper

from app.agent_runtime import AgentRuntimeContext
from app.config import LlmClientConfig
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
    """Registry of executable tools and Agents SDK schemas."""

    def __init__(
        self,
        project_root:Path,
        llm_config:LlmClientConfig,
        markdown_publisher:Callable[[DiscordChannelCategory, str, str], None] | None = None
    ):
        self._project_root = project_root.resolve()
        self._definitions = {
            definition.name: definition
            for definition in build_tool_definitions(
                project_root = self._project_root,
                llm_config = llm_config,
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

    def get_agent_tools(self) -> list[FunctionTool]:
        """Build OpenAI Agents SDK function tools.

        Args:
            None

        Returns:
            list[FunctionTool]: Function tools available to the SDK agent.
        """

        return [
            self._build_agent_tool(definition_name = definition_name)
            for definition_name in self.get_tool_names()
        ]

    def _build_agent_tool(self, definition_name:str) -> FunctionTool:
        """Wrap a local tool definition as an Agents SDK FunctionTool.

        Args:
            definition_name: Registered tool definition name.

        Returns:
            FunctionTool: SDK-compatible tool wrapper.
        """

        definition = self._definitions[definition_name]

        async def invoke_tool(ctx:RunContextWrapper[Any], arguments_json:str) -> str:
            logger.info("Invoking agent function tool: %s", definition.name)
            runtime_context = self._get_runtime_context(ctx = ctx)
            try:
                executed_call = self.execute_tool_call(
                    tool_name = definition.name,
                    arguments_json = arguments_json
                )
            except Exception as exc:
                logger.exception("Tool execution error")
                if runtime_context is not None:
                    runtime_context.record_tool_event(
                        tool_name = definition.name,
                        status = "error",
                        error = str(exc)
                    )
                return f"Tool error: {exc}"

            if runtime_context is not None:
                runtime_context.record_tool_event(
                    tool_name = definition.name,
                    status = "success"
                )

            self._publish_outbound_tool_message(
                ctx = ctx,
                result = executed_call.result
            )
            return executed_call.result.output

        return FunctionTool(
            name = definition.name,
            description = definition.description,
            params_json_schema = definition.parameters,
            on_invoke_tool = invoke_tool,
            strict_json_schema = True
        )

    def _publish_outbound_tool_message(
        self,
        ctx:RunContextWrapper[Any],
        result:ToolExecutionResult
    ) -> None:
        """Send outbound tool messages through the current run context.

        Args:
            ctx: Agents SDK run context wrapper.
            result: Tool execution result that may contain an outbound message.

        Returns:
            None
        """

        if not result.outbound_message:
            return

        runtime_context = self._get_runtime_context(ctx = ctx)
        if runtime_context is None:
            logger.warning("Cannot publish outbound tool message without runtime context")
            return

        runtime_context.send_discord_message(
            content = result.outbound_message
        )

    def _get_runtime_context(self, ctx:RunContextWrapper[Any]) -> AgentRuntimeContext | None:
        """Extract the runtime context from an Agents SDK context wrapper.

        Args:
            ctx: Agents SDK run context wrapper.

        Returns:
            AgentRuntimeContext | None: Runtime context when present.
        """

        runtime_context = getattr(ctx, "context", None)
        if not isinstance(runtime_context, AgentRuntimeContext):
            return None
        return runtime_context

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
