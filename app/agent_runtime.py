"""Runtime context shared with OpenAI Agents SDK tools."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Callable


logger = logging.getLogger(__name__)


@dataclass
class AgentRuntimeContext:
    """Mutable per-run context available to tool calls.

    Args:
        channel_id: Discord channel receiving user-facing messages.
        send_channel_message: Thread-safe callback for sending Discord messages.
        sent_messages: Shared list of Discord-visible messages sent during the run.
        tool_events: Tool execution summaries collected during the run.

    Returns:
        AgentRuntimeContext: Tool-visible runtime context for one agent run.
    """

    channel_id:int
    send_channel_message:Callable[[int, str], None] | None
    sent_messages:list[str] = field(default_factory = list)
    tool_events:list[dict[str, str]] = field(default_factory = list)

    def send_discord_message(self, content:str) -> None:
        """Send one Discord message and record it for logs.

        Args:
            content: User-facing message content.

        Returns:
            None
        """

        if self.send_channel_message is None:
            logger.warning("Discord send callback is unavailable for tool message")
            return

        self.send_channel_message(
            self.channel_id,
            content
        )
        self.sent_messages.append(content)

    def record_tool_event(self, tool_name:str, status:str, error:str = "") -> None:
        """Record a compact tool execution event for Discord logs.

        Args:
            tool_name: Name of the tool that ran.
            status: Tool outcome, such as success or error.
            error: Optional short error message.

        Returns:
            None
        """

        event = {
            "tool_name": tool_name,
            "status": status
        }
        if error:
            event["error"] = error[:500]
        self.tool_events.append(event)
