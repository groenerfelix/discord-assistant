"""Markdown-oriented tool implementations."""

from __future__ import annotations

import logging
from pathlib import Path
import tempfile
from typing import Any, Callable

from app.discord_utils import DiscordChannelCategory
from tools.base import ToolDefinition, ToolExecutionResult


logger = logging.getLogger(__name__)


WORKFLOWS_DIRECTORY_NAME = "workflows"
DATA_DIRECTORY_NAME = "data"
PROMPTS_DIRECTORY_NAME = "prompts"
MEMORIES_FILENAME = "memories.md"
MarkdownPublisher = Callable[[DiscordChannelCategory, str, str], None]
FILENAME_PARAMETERS:dict[str, Any] = {
    "type": "object",
    "properties": {
        "filename": {
            "type": "string",
            "description": "Markdown filename only, such as todo.md or todo. Do not include a path."
        }
    },
    "required": ["filename"],
    "additionalProperties": False
}
WRITE_PARAMETERS:dict[str, Any] = {
    "type": "object",
    "properties": {
        "filename": {
            "type": "string",
            "description": "Markdown filename only, such as todo.md or todo. Do not include a path."
        },
        "content": {
            "type": "string",
            "description": "Complete markdown content to write to the file."
        }
    },
    "required": ["filename", "content"],
    "additionalProperties": False
}
MEMORY_PARAMETERS:dict[str, Any] = {
    "type": "object",
    "properties": {
        "memory": {
            "type": "string",
            "description": (
                "A truly meaningful new fact about the user or their preferences. "
                "Use this only for durable information that is likely to help in future conversations "
                "and is not already present in memories."
            )
        }
    },
    "required": ["memory"],
    "additionalProperties": False
}


def build_markdown_tool_definitions(
    project_root:Path,
    markdown_publisher:MarkdownPublisher | None = None
) -> list[ToolDefinition]:
    """Build markdown-oriented tool definitions.

    Args:
        project_root: Root directory of the project workspace.
        markdown_publisher: Optional callback that mirrors markdown writes into Discord.

    Returns:
        list[ToolDefinition]: Markdown tool definitions.
    """

    workflows_directory = project_root / WORKFLOWS_DIRECTORY_NAME
    data_directory = project_root / DATA_DIRECTORY_NAME
    memories_path = project_root / PROMPTS_DIRECTORY_NAME / MEMORIES_FILENAME
    definitions:list[ToolDefinition] = []

    def read_workflow(arguments:dict[str, Any]) -> ToolExecutionResult:
        filename = str(arguments["filename"])
        return read_markdown(
            filename = filename,
            base_directory = workflows_directory,
            label = "workflow",
            project_root = project_root
        )

    definitions.append(
        ToolDefinition(
            name = "read_workflow",
            description = "Read a workflow markdown file from the workflows directory by filename.",
            parameters = FILENAME_PARAMETERS,
            handler = read_workflow
        )
    )

    def write_workflow(arguments:dict[str, Any]) -> ToolExecutionResult:
        filename = str(arguments["filename"])
        content = str(arguments["content"])
        return write_markdown(
            filename = filename,
            content = content,
            base_directory = workflows_directory,
            label = "workflow",
            project_root = project_root,
            markdown_publisher = markdown_publisher,
            publication_category = DiscordChannelCategory.WORKFLOWS
        )

    definitions.append(
        ToolDefinition(
            name = "write_workflow",
            description = "Write the full contents of a workflow markdown file in the workflows directory by filename.",
            parameters = WRITE_PARAMETERS,
            handler = write_workflow
        )
    )

    def read_data(arguments:dict[str, Any]) -> ToolExecutionResult:
        filename = str(arguments["filename"])
        return read_markdown(
            filename = filename,
            base_directory = data_directory,
            label = "data",
            project_root = project_root
        )

    definitions.append(
        ToolDefinition(
            name = "read_data",
            description = "Read a data markdown file from the data directory by filename.",
            parameters = FILENAME_PARAMETERS,
            handler = read_data
        )
    )

    def write_data(arguments:dict[str, Any]) -> ToolExecutionResult:
        filename = str(arguments["filename"])
        content = str(arguments["content"])
        return write_markdown(
            filename = filename,
            content = content,
            base_directory = data_directory,
            label = "data",
            project_root = project_root,
            markdown_publisher = markdown_publisher,
            publication_category = DiscordChannelCategory.DATA
        )

    definitions.append(
        ToolDefinition(
            name = "write_data",
            description = "Write the full contents of a data markdown file in the data directory by filename.",
            parameters = WRITE_PARAMETERS,
            handler = write_data
        )
    )

    def add_memory(arguments:dict[str, Any]) -> ToolExecutionResult:
        memory = str(arguments["memory"])
        return append_memory(
            memory = memory,
            memories_path = memories_path,
            project_root = project_root,
            markdown_publisher = markdown_publisher
        )

    definitions.append(
        ToolDefinition(
            name = "add_memory",
            description = (
                "Store a truly meaningful new memory about the user or their preferences. "
                "This appends one bullet to prompts/memories.md. "
                "Use it only for durable facts that will likely matter later, and do not use it if the memory is already present."
            ),
            parameters = MEMORY_PARAMETERS,
            handler = add_memory
        )
    )

    return definitions


def resolve_markdown_filename(filename:str, base_directory:Path) -> Path:
    """Resolve a filename within a constrained directory and normalize its suffix.

    Args:
        filename: Filename emitted by the model.
        base_directory: Directory the file must live in.

    Returns:
        Path: Validated absolute markdown file path.
    """

    normalized_filename = filename.strip()
    if not normalized_filename:
        raise ValueError("Filename must not be empty")
    if "/" in normalized_filename or "\\" in normalized_filename:
        raise ValueError(f"Only filenames are allowed, not paths: {filename}")
    if not normalized_filename.lower().endswith(".md"):
        normalized_filename = f"{normalized_filename}.md"

    file_path = (base_directory / normalized_filename).resolve()
    if file_path.parent != base_directory.resolve():
        raise ValueError(f"Filename escapes base directory: {filename}")
    return file_path


def format_relative_path(file_path:Path, project_root:Path) -> str:
    """Format a project-relative path for tool output messages.

    Args:
        file_path: Absolute file path.
        project_root: Project root directory.

    Returns:
        str: Project-relative slash-normalized path.
    """

    return str(file_path.relative_to(project_root)).replace("\\", "/")


def list_markdown_files(directory:Path, project_root:Path) -> list[str]:
    """List markdown files in a directory for internal use.

    Args:
        directory: Directory to scan.
        project_root: Project root directory.

    Returns:
        list[str]: Sorted project-relative markdown paths.
    """

    if not directory.exists():
        return []

    return sorted(
        format_relative_path(
            file_path = path,
            project_root = project_root
        )
        for path in directory.rglob("*.md")
    )


def read_markdown(
    filename:str,
    base_directory:Path,
    label:str,
    project_root:Path
) -> ToolExecutionResult:
    """Read a markdown file from a constrained project directory.

    Args:
        filename: Filename supplied by the model.
        base_directory: Directory the file must live in.
        label: Human-readable directory label.
        project_root: Root directory of the project workspace.

    Returns:
        ToolExecutionResult: File contents or a missing-file message.
    """

    file_path = resolve_markdown_filename(
        filename = filename,
        base_directory = base_directory
    )
    relative_path = format_relative_path(
        file_path = file_path,
        project_root = project_root
    )
    logger.debug("Reading %s file: %s", label, relative_path)

    if not file_path.exists():
        logger.warning("Missing %s file: %s", label, relative_path)
        return ToolExecutionResult(output = f"File not found: {relative_path}")

    content = file_path.read_text(encoding = "utf-8")
    return ToolExecutionResult(output = f"Contents of {relative_path}:\n\n{content}")


def write_markdown(
    filename:str,
    content:str,
    base_directory:Path,
    label:str,
    project_root:Path,
    markdown_publisher:MarkdownPublisher | None = None,
    publication_category:DiscordChannelCategory | None = None,
    publication_channel_name:str | None = None
) -> ToolExecutionResult:
    """Write a markdown file inside a constrained project directory.

    Args:
        filename: Filename supplied by the model.
        content: Full markdown contents to write.
        base_directory: Directory the file must live in.
        label: Human-readable directory label.
        project_root: Root directory of the project workspace.
        markdown_publisher: Optional callback that mirrors markdown writes into Discord.
        publication_category: Optional Discord category that should receive the full file contents.
        publication_channel_name: Optional Discord channel override.

    Returns:
        ToolExecutionResult: Success message for the write.
    """

    file_path = resolve_markdown_filename(
        filename = filename,
        base_directory = base_directory
    )
    relative_path = format_relative_path(
        file_path = file_path,
        project_root = project_root
    )
    logger.info("Writing %s file: %s", label, relative_path)

    if not base_directory.exists():
        raise ValueError(f"Directory not found: {base_directory}")
    if file_path.parent != base_directory.resolve():
        raise ValueError(f"Nested folders are not allowed for {label} files: {filename}")

    atomic_write_text(
        file_path = file_path,
        content = content
    )

    if publication_category is not None:
        publish_markdown_update(
            category = publication_category,
            channel_name = publication_channel_name or file_path.stem,
            content = content,
            markdown_publisher = markdown_publisher
        )

    return ToolExecutionResult(output = f"Wrote markdown file: {relative_path}")


def normalize_memory(memory:str) -> str:
    """Normalize a memory entry into plain bullet text.

    Args:
        memory: Raw memory string supplied by the model.

    Returns:
        str: Clean memory text without the bullet prefix.
    """

    normalized_memory = memory.strip()
    if normalized_memory.startswith("- "):
        normalized_memory = normalized_memory[2:].strip()
    elif normalized_memory == "-":
        normalized_memory = ""

    if not normalized_memory:
        raise ValueError("Memory must not be empty")

    return normalized_memory


def parse_memory_entries(content:str) -> list[str]:
    """Extract normalized memory bullet texts from the memories file.

    Args:
        content: Existing memories markdown content.

    Returns:
        list[str]: Normalized bullet texts.
    """

    entries:list[str] = []
    for line in content.splitlines():
        stripped_line = line.strip()
        if stripped_line.startswith("- "):
            entries.append(normalize_memory(memory = stripped_line))

    return entries


def atomic_write_text(file_path:Path, content:str) -> None:
    """Atomically replace a text file on disk.

    Args:
        file_path: File path to replace.
        content: Full text content to write.

    Returns:
        None
    """

    logger.debug("Performing atomic write: %s", file_path)
    file_path.parent.mkdir(parents = True, exist_ok = True)

    with tempfile.NamedTemporaryFile(
        mode = "w",
        encoding = "utf-8",
        dir = str(file_path.parent),
        delete = False
    ) as temporary_file:
        temporary_file.write(content)
        temporary_path = Path(temporary_file.name)

    temporary_path.replace(file_path)


def publish_markdown_update(
    category:DiscordChannelCategory,
    channel_name:str,
    content:str,
    markdown_publisher:MarkdownPublisher | None
) -> None:
    """Mirror one markdown update into Discord when a publisher is available.

    Args:
        category: Target Discord category.
        channel_name: Target Discord channel name.
        content: Discord message content to publish.
        markdown_publisher: Optional publisher callback.

    Returns:
        None
    """

    if markdown_publisher is None:
        logger.debug(
            "Skipping Discord publish because no publisher is configured category=%s channel_name=%s",
            category,
            channel_name
        )
        return

    logger.info(
        "Publishing markdown update to Discord category=%s channel_name=%s",
        category,
        channel_name
    )
    markdown_publisher(
        category,
        channel_name,
        content
    )


def append_memory(
    memory:str,
    memories_path:Path,
    project_root:Path,
    markdown_publisher:MarkdownPublisher | None = None
) -> ToolExecutionResult:
    """Append a new memory bullet to the dedicated memories file.

    Args:
        memory: Raw memory string supplied by the model.
        memories_path: Absolute path to prompts/memories.md.
        project_root: Root directory of the project workspace.
        markdown_publisher: Optional callback that mirrors markdown writes into Discord.

    Returns:
        ToolExecutionResult: Outcome of the memory append operation.
    """

    relative_path = format_relative_path(
        file_path = memories_path,
        project_root = project_root
    )
    normalized_memory = normalize_memory(memory = memory)
    logger.info("Adding memory to %s: %s", relative_path, normalized_memory)

    existing_content = ""
    if memories_path.exists():
        existing_content = memories_path.read_text(encoding = "utf-8")

    existing_entries = parse_memory_entries(content = existing_content)
    if normalized_memory in existing_entries:
        logger.info("Memory already present in %s", relative_path)
        return ToolExecutionResult(output = f"Memory already present in {relative_path}: - {normalized_memory}")

    appended_entry = f"- {normalized_memory}"
    if not existing_content:
        updated_content = appended_entry
    else:
        updated_content = f"{existing_content.rstrip()}\n{appended_entry}"

    atomic_write_text(
        file_path = memories_path,
        content = updated_content
    )
    publish_markdown_update(
        category = DiscordChannelCategory.OTHER,
        channel_name = "memories",
        content = appended_entry,
        markdown_publisher = markdown_publisher
    )

    return ToolExecutionResult(output = f"Added memory to {relative_path}: {appended_entry}")
