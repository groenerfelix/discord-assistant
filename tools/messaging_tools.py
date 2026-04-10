"""Terminal messaging tool implementations."""

from __future__ import annotations

import logging

from tools.base import ToolDefinition, ToolExecutionResult


logger = logging.getLogger(__name__)


SEND_MESSAGE_PARAMETERS = {
    "type": "object",
    "properties": {
        "message": {
            "type": "string",
            "description": "User-facing Discord message to send immediately."
        },
        "is_terminal": {
            "type": "boolean",
            "description": "Whether sending this message should end the current workflow.",
            "default": True
        }
    },
    "required": [
        "message",
        "is_terminal"
    ],
    "additionalProperties": False
}


def build_messaging_tool_definitions() -> list[ToolDefinition]:
    """Build messaging-oriented tool definitions.

    Args:
        None

    Returns:
        list[ToolDefinition]: Messaging tool definitions.
    """

    def send_message(arguments:dict) -> ToolExecutionResult:
        message = str(arguments["message"])
        is_terminal = bool(arguments.get("is_terminal", True))
        logger.info("Sending Discord message. is_terminal=%s", is_terminal)
        return ToolExecutionResult(
            output = "Successfully sent Discord message",
            outbound_message = message,
            is_terminal = is_terminal
        )

    return [
        ToolDefinition(
            name = "send_message",
            description = (
                "Send a user-facing Discord reply. "
                "By default this ends the workflow, but you may set is_terminal to false "
                "to continue working after sending the message."
            ),
            parameters = SEND_MESSAGE_PARAMETERS,
            handler = send_message
        )
    ]
