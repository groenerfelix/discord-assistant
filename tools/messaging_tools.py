"""Terminal messaging tool implementations."""

from __future__ import annotations

from tools.base import ToolDefinition, ToolExecutionResult


def build_messaging_tool_definitions() -> list[ToolDefinition]:
    """Build messaging-oriented tool definitions.

    Args:
        None

    Returns:
        list[ToolDefinition]: Messaging tool definitions.
    """

    def send_message(arguments:dict) -> ToolExecutionResult:
        message = str(arguments["message"])
        print("[MessagingTools] Sending final message")
        return ToolExecutionResult(
            output = message,
            is_terminal = True
        )

    return [
        ToolDefinition(
            name = "send_message",
            description = "Finish the workflow and send a final user-facing reply.",
            parameters = {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "Final message that should be sent back to the user."
                    }
                },
                "required": ["message"],
                "additionalProperties": False
            },
            handler = send_message
        )
    ]
