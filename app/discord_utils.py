"""Shared Discord enums for channels and message state."""

from __future__ import annotations

from enum import StrEnum


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
