"""Core markdown-driven agent loop."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
from typing import Any

from app.config import AppConfig
from app.llm_client import LlmClient
from app.markdown_loader import load_optional_markdown
from app.tool_registry import ToolRegistry
from tools.markdown_tools import list_markdown_files

from app.util import get_datetime_string


@dataclass
class AgentResponse:
    """Terminal result of an agent run.

    Args:
        message: Final message to send back to the user.
        steps_used: Number of tool loop iterations that were consumed.

    Returns:
        AgentResponse: Final workflow outcome.
    """

    message:str
    steps_used:int


class MarkdownAgent:
    """Minimal agent that is configured by markdown assets."""

    def __init__(self, config:AppConfig):
        self._config = config
        self._llm_client = LlmClient(config = config)
        self._tools = ToolRegistry(
            project_root = config.project_root,
            llm_client = self._llm_client
        )

    def run_dm_workflow(
        self,
        user_message:str,
        recent_channel_history:str = ""
    ) -> AgentResponse:
        """Run the DM workflow loop for a single incoming user message.

        Args:
            user_message: The raw text from the Discord direct message.
            recent_channel_history: Optional formatted transcript from the same Discord channel.

        Returns:
            AgentResponse: Final assistant response and usage metadata.
        """

        print("[Agent] Starting DM workflow")
        messages = self._build_initial_messages(
            user_message = user_message,
            recent_channel_history = recent_channel_history
        )
        openai_tools = self._tools.get_openai_tools()

        for step_index in range(1, self._config.max_agent_steps + 1):
            print(f"[Agent] Running step {step_index}/{self._config.max_agent_steps}")
            assistant_message = self._llm_client.create_tool_completion(
                messages = messages,
                tools = openai_tools
            )
            messages.append(self._message_to_dict(message = assistant_message))

            tool_calls = assistant_message.tool_calls or []
            if not tool_calls:
                final_message = (assistant_message.content or "").strip()
                if final_message:
                    print("[Agent] Workflow completed via assistant content fallback")
                    self._write_interaction_log(
                        messages = messages,
                        final_message = final_message,
                        termination_reason = "assistant_content",
                        steps_used = step_index
                    )
                    return AgentResponse(
                        message = final_message,
                        steps_used = step_index
                    )

                print("[Agent] Assistant returned neither tool call nor content")
                messages.append(
                    {
                        "role": "user",
                        "content": "You must either call a tool or provide a final response."
                    }
                )
                continue

            tool_call = tool_calls[0]

            try:
                executed_call = self._tools.execute_tool_call(
                    tool_name = tool_call.function.name,
                    arguments_json = tool_call.function.arguments
                )
            except Exception as exc:
                print(f"[Agent] Tool execution error: {exc}")
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": f"Tool error: {exc}"
                    }
                )
                continue

            if executed_call.result.is_terminal:
                print("[Agent] Workflow completed via send_message")
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": executed_call.result.output
                    }
                )
                self._write_interaction_log(
                    messages = messages,
                    final_message = executed_call.result.output,
                    termination_reason = f"tool:{tool_call.function.name}",
                    steps_used = step_index
                )
                return AgentResponse(
                    message = executed_call.result.output,
                    steps_used = step_index
                )

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": executed_call.result.output
                }
            )

        print("[Agent] Workflow hit hard step limit")
        limit_message = (
            "I hit the workflow step limit before I could finish safely. "
            "Please send a shorter follow-up or refine the request."
        )
        self._write_interaction_log(
            messages = messages,
            final_message = limit_message,
            termination_reason = "step_limit",
            steps_used = self._config.max_agent_steps
        )
        return AgentResponse(
            message = limit_message,
            steps_used = self._config.max_agent_steps
        )

    def _build_initial_messages(
        self,
        user_message:str,
        recent_channel_history:str = ""
    ) -> list[dict[str, Any]]:
        """Construct the initial prompt context for the agent.

        Args:
            user_message: Raw DM content from the user.
            recent_channel_history: Optional formatted transcript from the same Discord channel.

        Returns:
            list[dict[str, Any]]: Initial chat messages for the model.
        """

        system_prompt = self._build_system_prompt()
        user_content = self._build_user_message(
            user_message = user_message,
            recent_channel_history = recent_channel_history
        )
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content}
        ]

    def _build_user_message(
        self,
        user_message:str,
        recent_channel_history:str = ""
    ) -> str:
        """Build the initial user payload for the model.

        Args:
            user_message: Raw DM content from the user.
            recent_channel_history: Optional formatted transcript from the same Discord channel.

        Returns:
            str: User message with optional bounded channel context.
        """

        if not recent_channel_history:
            return user_message

        return (
            "## Recent Channel History\n"
            f"{recent_channel_history}\n\n"
            "---\n\n"
            "## Latest User Message\n"
            f"{user_message}"
        )

    def _build_system_prompt(self) -> str:
        """Assemble the system prompt from markdown assets.

        Args:
            None

        Returns:
            str: Full system prompt content.
        """

        project_root = self._config.project_root
        system_prompt = ""

        current_date_string = "It is currently " + get_datetime_string(timezone = os.getenv("TIMEZONE", "UTC")) + "."
        system_prompt += f"{current_date_string}\n\n"

        persona_prompt = load_optional_markdown(path = project_root / "prompts" / "persona.md")
        if persona_prompt:
            system_prompt += f"{persona_prompt}\n\n"

        general_instructions = load_optional_markdown(path = project_root / "prompts" / "core.md")
        if general_instructions:
            system_prompt += f"{general_instructions}\n\n"

        memories_prompt = load_optional_markdown(path = project_root / "prompts" / "memories.md")
        memories_content = memories_prompt or "_No stored memories yet._"
        system_prompt += (
            "## memories\n"
            f"{memories_content}\n\n"
        )

        workflow_files = self._format_available_files(directory = project_root / "workflows")
        data_files = self._format_available_files(directory = project_root / "data")

        system_prompt += (
            "## Available workflow files\n"
            f"{workflow_files}\n\n"
            "## Available data files\n"
            f"{data_files}\n\n"
        )

        return system_prompt

    def _format_available_files(self, directory:Path) -> str:
        """List markdown files relative to the project root for prompt context.

        Args:
            directory: Directory to scan for markdown files.

        Returns:
            str: Newline-separated project-relative markdown file paths.
        """

        print(f"[Agent] Building prompt file list for: {directory}")
        available_files = list_markdown_files(
            directory = directory,
            project_root = self._config.project_root
        )
        if not available_files:
            return "_None_"

        return "\n".join(available_files)

    def _message_to_dict(self, message:Any) -> dict[str, Any]:
        """Convert an SDK assistant message into a chat-completions message dict.

        Args:
            message: SDK assistant message object.

        Returns:
            dict[str, Any]: Message payload suitable for a follow-up API call.
        """

        payload = message.model_dump()
        print("[Agent] Appending assistant message to conversation state")
        return payload

    def _write_interaction_log(
        self,
        messages:list[dict[str, Any]],
        final_message:str,
        termination_reason:str,
        steps_used:int
    ) -> None:
        """Write the full interaction transcript to a markdown logfile.

        Args:
            messages: Full conversation state accumulated during the workflow.
            final_message: Final user-facing message for the interaction.
            termination_reason: Why the workflow ended.
            steps_used: Number of steps consumed before termination.

        Returns:
            None
        """

        logs_directory = self._config.project_root / "logs"
        logs_directory.mkdir(parents = True, exist_ok = True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        log_path = logs_directory / f"interaction_{timestamp}.md"
        log_lines = [
            "# Agent Interaction Log",
            "",
            f"- Timestamp: {datetime.now().isoformat()}",
            f"- Termination reason: {termination_reason}",
            f"- Steps used: {steps_used}",
            "",
            "## Final Message",
            "",
            final_message or "_No final message provided._",
            "",
            "## Message History",
            ""
        ]

        for index, message in enumerate(messages, start = 1):
            role = str(message.get("role", "unknown"))
            log_lines.extend(
                [
                    f"### {index}. {role}",
                    "",
                    "```json",
                    json.dumps(message, indent = 2, ensure_ascii = False),
                    "```",
                    ""
                ]
            )

        log_path.write_text("\n".join(log_lines), encoding = "utf-8")
        print(f"[Agent] Wrote interaction log to {log_path}")


