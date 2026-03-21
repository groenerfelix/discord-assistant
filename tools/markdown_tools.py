"""Markdown-oriented tool implementations."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.base import ToolDefinition, ToolExecutionResult


WORKFLOWS_DIRECTORY_NAME = "workflows"
DATA_DIRECTORY_NAME = "data"
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


def build_markdown_tool_definitions(project_root:Path) -> list[ToolDefinition]:
    """Build markdown-oriented tool definitions.

    Args:
        project_root: Root directory of the project workspace.

    Returns:
        list[ToolDefinition]: Markdown tool definitions.
    """

    workflows_directory = project_root / WORKFLOWS_DIRECTORY_NAME
    data_directory = project_root / DATA_DIRECTORY_NAME
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
            project_root = project_root
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
            project_root = project_root
        )

    definitions.append(
        ToolDefinition(
            name = "write_data",
            description = "Write the full contents of a data markdown file in the data directory by filename.",
            parameters = WRITE_PARAMETERS,
            handler = write_data
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
    print(f"[MarkdownTools] Reading {label} file: {relative_path}")

    if not file_path.exists():
        print(f"[MarkdownTools] Missing {label} file: {relative_path}")
        return ToolExecutionResult(output = f"File not found: {relative_path}")

    content = file_path.read_text(encoding = "utf-8")
    return ToolExecutionResult(output = f"Contents of {relative_path}:\n\n{content}")


def write_markdown(
    filename:str,
    content:str,
    base_directory:Path,
    label:str,
    project_root:Path
) -> ToolExecutionResult:
    """Write a markdown file inside a constrained project directory.

    Args:
        filename: Filename supplied by the model.
        content: Full markdown contents to write.
        base_directory: Directory the file must live in.
        label: Human-readable directory label.
        project_root: Root directory of the project workspace.

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
    print(f"[MarkdownTools] Writing {label} file: {relative_path}")

    if not base_directory.exists():
        raise ValueError(f"Directory not found: {base_directory}")
    if file_path.parent != base_directory.resolve():
        raise ValueError(f"Nested folders are not allowed for {label} files: {filename}")

    file_path.write_text(content, encoding = "utf-8")
    return ToolExecutionResult(output = f"Wrote markdown file: {relative_path}")
