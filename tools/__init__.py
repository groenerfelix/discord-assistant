"""Tool definition entrypoint."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from app.discord_utils import DiscordChannelCategory
from tools.base import ToolDefinition
from tools.email_tools import build_email_tool_definitions
from tools.markdown_tools import build_markdown_tool_definitions
from tools.messaging_tools import build_messaging_tool_definitions
from tools.web_search import build_web_search_tool_definitions


def build_tool_definitions(
    project_root:Path,
    markdown_publisher:Callable[[DiscordChannelCategory, str, str], None] | None = None
) -> list[ToolDefinition]:
    """Build the full list of callable tools.

    Args:
        project_root: Root directory of the project workspace.
        markdown_publisher: Optional callback that mirrors markdown writes into Discord.

    Returns:
        list[ToolDefinition]: Tool definitions available to the agent.
    """

    return [
        *build_markdown_tool_definitions(
            project_root = project_root,
            markdown_publisher = markdown_publisher
        ),
        *build_email_tool_definitions(),
        *build_messaging_tool_definitions(),
        *build_web_search_tool_definitions()
    ]
