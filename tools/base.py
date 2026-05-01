"""Shared tool primitives."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen = True)
class ToolExecutionResult:
    """Return value for tool executions.

    Args:
        output: Human-readable output to feed back into the agent loop.
        outbound_message: Optional Discord-visible message to send immediately.

    Returns:
        ToolExecutionResult: Tool execution metadata.
    """

    output:str
    outbound_message:str | None = None


@dataclass(frozen = True)
class ToolDefinition:
    """Definition of a callable tool and its runtime handler.

    Args:
        name: Tool name exposed to the model.
        description: Human-readable tool description.
        parameters: Strict JSON Schema for tool arguments.
        handler: Concrete implementation for the tool.

    Returns:
        ToolDefinition: Metadata and implementation pair.
    """

    name:str
    description:str
    parameters:dict[str, Any]
    handler:Callable[[dict[str, Any]], ToolExecutionResult]
