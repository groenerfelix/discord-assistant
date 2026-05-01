"""Tool definition entrypoint."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from app.config import LlmClientConfig
from app.discord_utils import DiscordChannelCategory
from tools.base import ToolDefinition
from tools.email_tools import build_email_tool_definitions
from tools.markdown_tools import build_markdown_tool_definitions
from tools.messaging_tools import build_messaging_tool_definitions


def build_tool_definitions(
    project_root:Path,
    llm_config:LlmClientConfig,
    markdown_publisher:Callable[[DiscordChannelCategory, str, str], None] | None = None
) -> list[ToolDefinition]:
    """Build the full list of callable tools.

    Args:
        project_root: Root directory of the project workspace.
        llm_config: Main agent LLM configuration used by model-backed tools.
        markdown_publisher: Optional callback that mirrors markdown writes into Discord.

    Returns:
        list[ToolDefinition]: Tool definitions available to the agent.
    """

    return [
        *build_markdown_tool_definitions(
            project_root = project_root,
            markdown_publisher = markdown_publisher
        ),
        *build_email_tool_definitions(
            project_root = project_root,
            llm_config = llm_config
        ),
        *build_messaging_tool_definitions()
    ]
