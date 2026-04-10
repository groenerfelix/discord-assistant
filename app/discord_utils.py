"""Shared Discord enums and helpers for Discord message handling."""

from __future__ import annotations

from enum import StrEnum
import logging


DISCORD_MESSAGE_CHARACTER_LIMIT = 2000
logger = logging.getLogger(__name__)


class DiscordChannelCategory(StrEnum):
    """Supported Discord category names used by the assistant."""

    DATA = "Data"
    WORKFLOWS = "Workflows"
    OTHER = "Other"


class DiscordMessageStatus(StrEnum):
    """Supported workflow status values mirrored through reactions."""

    QUEUED = "queued"
    THINKING = "thinking"
    SUCCESS = "success"
    ERROR = "error"


def split_discord_message(
    content:str,
    max_length:int = DISCORD_MESSAGE_CHARACTER_LIMIT
) -> list[str]:
    """Split message content into Discord-safe chunks using smart break points.

    Args:
        content: Raw message content to split.
        max_length: Maximum length allowed per chunk.

    Returns:
        list[str]: One or more message chunks that each fit within the limit.
    """

    if max_length <= 0:
        raise ValueError("max_length must be greater than zero")

    if not content:
        return [""]

    if len(content) <= max_length:
        return [content]

    logger.debug(
        "Splitting Discord message length=%s max_length=%s",
        len(content),
        max_length
    )

    chunks:list[str] = []
    remaining_content = content

    while len(remaining_content) > max_length:
        split_index = _find_discord_split_index(
            content = remaining_content,
            max_length = max_length
        )
        chunk = remaining_content[:split_index].rstrip()
        if not chunk:
            chunk = remaining_content[:max_length]
            split_index = len(chunk)

        chunks.append(chunk)
        remaining_content = remaining_content[split_index:].lstrip("\n")

    if remaining_content:
        chunks.append(remaining_content)

    logger.debug("Split into %s chunk(s)", len(chunks))
    return chunks


def _find_discord_split_index(content:str, max_length:int) -> int:
    """Find the best split index for a Discord message chunk.

    Args:
        content: Message content that still needs splitting.
        max_length: Maximum length allowed per chunk.

    Returns:
        int: Preferred split index that keeps the chunk within the limit.
    """

    candidate_region = content[:max_length]

    newline_index = candidate_region.rfind("\n")
    if newline_index > 0:
        return newline_index

    whitespace_index = max(
        candidate_region.rfind(" "),
        candidate_region.rfind("\t")
    )
    if whitespace_index > 0:
        return whitespace_index

    return max_length
