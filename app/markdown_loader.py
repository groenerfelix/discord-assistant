"""Utilities for loading markdown-defined workflows and prompts."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path


logger = logging.getLogger(__name__)


@dataclass(frozen = True)
class MarkdownDocument:
    """Representation of a markdown file that the agent can read.

    Args:
        name: Logical document name.
        path: Absolute path to the markdown file.
        content: File contents.

    Returns:
        MarkdownDocument: Loaded markdown document metadata and text.
    """

    name:str
    path:Path
    content:str


def load_markdown_documents(directory:Path) -> list[MarkdownDocument]:
    """Load all markdown files from a directory.

    Args:
        directory: Directory that may contain markdown files.

    Returns:
        list[MarkdownDocument]: Sorted list of loaded markdown documents.
    """

    if not directory.exists():
        logger.debug("Directory missing: %s", directory)
        return []

    documents:list[MarkdownDocument] = []
    for path in sorted(directory.glob("*.md")):
        logger.debug("Loading markdown file: %s", path)
        documents.append(
            MarkdownDocument(
                name = path.stem,
                path = path,
                content = path.read_text(encoding = "utf-8")
            )
        )

    return documents


def load_optional_markdown(path:Path) -> str:
    """Load a markdown file if it exists.

    Args:
        path: File path to load.

    Returns:
        str: File contents or an empty string when missing.
    """

    if not path.exists():
        logger.debug("Optional markdown missing: %s", path)
        return ""

    logger.debug("Loading optional markdown file: %s", path)
    return path.read_text(encoding = "utf-8")
