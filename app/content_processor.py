"""Reusable Responses-based content processing helper."""

from __future__ import annotations

import logging
from pathlib import Path

from openai import OpenAI

from app.config import LlmClientConfig


logger = logging.getLogger(__name__)

PROCESSOR_SYSTEM_INSTRUCTIONS = (
    "You process tool-internal content for a larger Discord assistant. "
    "Follow the processing instructions exactly when provided. Return only the processed result "
    "that should be passed back to the main agent."
)


def process_content_with_workflow(
    config:LlmClientConfig,
    workflow_path:Path | None,
    content_label:str,
    content_payload:str,
    fallback_instructions:str
) -> str:
    """Process content through an optional workflow file and fallback instructions.

    Args:
        config: Main agent LLM configuration. The same model is used for processing.
        workflow_path: Optional workflow markdown path to include when it exists.
        content_label: Human-readable label for the content payload.
        content_payload: Raw content payload to process.
        fallback_instructions: Instructions used when the workflow file is unavailable.

    Returns:
        str: Processed text returned by the Responses API.
    """

    workflow_content = _load_optional_workflow(workflow_path = workflow_path)
    instructions = workflow_content.strip() or fallback_instructions.strip()
    logger.info(
        "Processing content with workflow path=%s workflow_found=%s model=%s payload_chars=%s",
        workflow_path,
        bool(workflow_content),
        config.model,
        len(content_payload)
    )
    return process_content_with_instructions(
        config = config,
        content_label = content_label,
        content_payload = content_payload,
        instructions = instructions
    )


def process_content_with_instructions(
    config:LlmClientConfig,
    content_label:str,
    content_payload:str,
    instructions:str
) -> str:
    """Process content through explicit instructions.

    Args:
        config: Main agent LLM configuration. The same model is used for processing.
        content_label: Human-readable label for the content payload.
        content_payload: Raw content payload to process.
        instructions: Processing instructions to apply to the content payload.

    Returns:
        str: Processed text returned by the Responses API.
    """

    normalized_instructions = instructions.strip()
    if not normalized_instructions:
        raise ValueError("Processing instructions must not be empty")

    prompt = build_content_processing_prompt(
        instructions = normalized_instructions,
        content_label = content_label,
        content_payload = content_payload
    )
    logger.info(
        "Processing content with explicit instructions model=%s payload_chars=%s instructions_chars=%s",
        config.model,
        len(content_payload),
        len(normalized_instructions)
    )

    client = OpenAI(
        api_key = config.api_key,
        base_url = config.base_url
    )
    response = client.responses.create(
        model = config.model,
        instructions = PROCESSOR_SYSTEM_INSTRUCTIONS,
        input = prompt
    )

    output_text = (response.output_text or "").strip()
    if not output_text:
        logger.warning("Content processor returned no output text")
        return "No processed content was returned."

    logger.info("Content processor returned %s character(s)", len(output_text))
    return output_text


def build_content_processing_prompt(
    instructions:str,
    content_label:str,
    content_payload:str
) -> str:
    """Build the prompt sent to the reusable content processor.

    Args:
        instructions: Processing instructions to apply to the content payload.
        content_label: Human-readable label for the content payload.
        content_payload: Raw content payload to process.

    Returns:
        str: Prompt template containing processing instructions and content.
    """

    return (
        "## Processing Instructions\n"
        f"{instructions}\n\n"
        "---\n\n"
        f"## Content: {content_label}\n"
        f"{content_payload}"
    )


def _load_optional_workflow(workflow_path:Path | None) -> str:
    """Load optional workflow markdown content.

    Args:
        workflow_path: Workflow path to read when present.

    Returns:
        str: Workflow content, or an empty string when unavailable.
    """

    if workflow_path is None:
        return ""

    if not workflow_path.exists():
        logger.info("Optional workflow file is not present: %s", workflow_path)
        return ""

    logger.info("Loading optional workflow file: %s", workflow_path)
    return workflow_path.read_text(encoding = "utf-8")
