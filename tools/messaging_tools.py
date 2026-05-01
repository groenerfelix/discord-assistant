"""Messaging tool implementations."""

from __future__ import annotations

import logging

from tools.base import ToolDefinition, ToolExecutionResult


logger = logging.getLogger(__name__)


SEND_MESSAGE_PARAMETERS = {
    "type": "object",
    "properties": {
        "message": {
            "type": "string",
            "description": (
                "User-facing Discord message to send before the final response. "
                "Use Discord-flavored markdown."
            )
        }
    },
    "required": [
        "message"
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

    def send_message(arguments:dict[str, object]) -> ToolExecutionResult:
        message = str(arguments["message"])
        logger.info("Sending intermediate Discord message")
        return ToolExecutionResult(
            output = (
                "Successfully sent Discord message. Continue working and provide your "
                "final response normally; it will be sent to Discord automatically."
            ),
            outbound_message = message
        )

    return [
        ToolDefinition(
            name = "send_message",
            description = (
                "Send a user-facing Discord message before the final response. Use this "
                "when the user should receive an update, clarification, or partial result "
                "while you continue working. Do not use this for your final answer; final "
                "output is sent to Discord automatically."
            ),
            parameters = SEND_MESSAGE_PARAMETERS,
            handler = send_message
        )
    ]
