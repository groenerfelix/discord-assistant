"""Core markdown-driven agent loop."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
from typing import TYPE_CHECKING, Any

from app.config import AppConfig, LlmClientConfig
from app.llm_client import LlmClient
from app.markdown_loader import load_optional_markdown
from app.tool_registry import ToolRegistry
from tools.markdown_tools import list_markdown_files

from app.util import get_datetime_string


if TYPE_CHECKING:
    from app.discord_bot import AssistantDiscordClient


@dataclass(frozen = True)
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


@dataclass(frozen = True)
class QueuedDiscordMessage:
    """Serialized Discord message queued for workflow processing.

    Args:
        message_id: Discord message id.
        channel_id: Discord channel id.
        author_id: Discord author id.
        content: Stripped message content.
        created_at: Timestamp from Discord.
        recent_channel_history: Bounded same-channel history for the first turn.
        status: Current workflow status for this message.

    Returns:
        QueuedDiscordMessage: Immutable queued message record.
    """

    message_id:int
    channel_id:int
    author_id:int
    content:str
    created_at:datetime
    recent_channel_history:str = ""
    status:str = "queued"


@dataclass
class ActiveWorkflowState:
    """Mutable state for an in-flight queued workflow.

    Args:
        channel_id: Active Discord channel id.
        messages: Full LLM conversation state.
        participating_messages: Messages already inserted into conversation state.
        sent_messages: User-facing Discord messages sent during the run.
        steps_used: Number of LLM turns consumed.

    Returns:
        ActiveWorkflowState: Current workflow runtime state.
    """

    channel_id:int
    messages:list[dict[str, Any]]
    participating_messages:list[QueuedDiscordMessage] = field(default_factory = list)
    sent_messages:list[str] = field(default_factory = list)
    steps_used:int = 0


@dataclass(frozen = True)
class WorkflowStepResult:
    """Result of executing a single workflow step.

    Args:
        status: One of continue, terminal, or error.
        termination_reason: Optional reason string for terminal or error states.
        final_message: Final user-facing message for terminal states.
        error_message: Human-readable error summary for failure states.

    Returns:
        WorkflowStepResult: Structured step outcome.
    """

    status:str
    termination_reason:str | None = None
    final_message:str = ""
    error_message:str = ""


class MarkdownAgent:
    """Minimal agent that is configured by markdown assets."""

    def __init__(self, config:AppConfig):
        self._config = config
        self.llm_agent = LlmClient(
            config = config.agent_llm,
            name = "agent"
        )
        self._tools = ToolRegistry(
            project_root = config.project_root
        )
        self._discord_client:AssistantDiscordClient | None = None
        self._queue_condition = threading.Condition()
        self._queued_messages:deque[QueuedDiscordMessage] = deque()
        self._active_workflow:ActiveWorkflowState | None = None
        self._worker_thread:threading.Thread | None = None

    def start_worker(self, discord_client:AssistantDiscordClient) -> None:
        """Start the dedicated workflow worker thread.

        Args:
            discord_client: Discord client bridge used for reactions and sends.

        Returns:
            None
        """

        self._discord_client = discord_client
        if self._worker_thread is not None and self._worker_thread.is_alive():
            print("[Agent] Worker thread already running")
            return

        self._worker_thread = threading.Thread(
            target = self._worker_loop,
            name = "markdown-agent-worker",
            daemon = True
        )
        self._worker_thread.start()
        print("[Agent] Worker thread started")

    def enqueue_message(self, queued_message:QueuedDiscordMessage) -> None:
        """Queue an incoming Discord message for worker processing.

        Args:
            queued_message: Serialized Discord message payload.

        Returns:
            None
        """

        with self._queue_condition:
            self._queued_messages.append(queued_message)
            queue_size = len(self._queued_messages)
            self._queue_condition.notify()

        print(
            "[Agent] Enqueued message "
            f"message_id={queued_message.message_id} channel_id={queued_message.channel_id} "
            f"queue_size={queue_size}"
        )

    def _worker_loop(self) -> None:
        """Run queued workflows sequentially on a background thread.

        Args:
            None

        Returns:
            None
        """

        print("[Agent] Worker loop is running")

        while True:
            queued_message = self._wait_for_next_message()
            workflow_state:ActiveWorkflowState | None = None

            try:
                workflow_state = self._start_workflow(initial_message = queued_message)
                self._active_workflow = workflow_state

                while True:
                    self._insert_pending_same_channel_messages(workflow_state = workflow_state)
                    step_result = self._run_single_step(workflow_state = workflow_state)

                    if step_result.status == "continue":
                        continue

                    if step_result.status == "terminal":
                        self._finish_workflow_success(
                            workflow_state = workflow_state,
                            step_result = step_result
                        )
                        break

                    self._finish_workflow_error(
                        workflow_state = workflow_state,
                        step_result = step_result
                    )
                    break
            except Exception as exc:
                error_message = f"Unhandled workflow error: {exc}"
                print(f"[Agent] {error_message}")

                if workflow_state is not None:
                    self._finish_workflow_error(
                        workflow_state = workflow_state,
                        step_result = WorkflowStepResult(
                            status = "error",
                            termination_reason = "unhandled_exception",
                            error_message = error_message
                        )
                    )
                else:
                    self._mark_message_status(
                        queued_message = queued_message,
                        status = "error"
                    )
            finally:
                self._active_workflow = None

    def _wait_for_next_message(self) -> QueuedDiscordMessage:
        """Block until a queued message is available.

        Args:
            None

        Returns:
            QueuedDiscordMessage: Next queued message in FIFO order.
        """

        with self._queue_condition:
            while not self._queued_messages:
                self._queue_condition.wait()

            queued_message = self._queued_messages.popleft()

        print(
            "[Agent] Dequeued message "
            f"message_id={queued_message.message_id} channel_id={queued_message.channel_id}"
        )
        return queued_message

    def _start_workflow(self, initial_message:QueuedDiscordMessage) -> ActiveWorkflowState:
        """Initialize workflow state from the first queued message.

        Args:
            initial_message: First message that starts the workflow.

        Returns:
            ActiveWorkflowState: Newly initialized workflow state.
        """

        print(
            "[Agent] Starting workflow "
            f"channel_id={initial_message.channel_id} message_id={initial_message.message_id}"
        )
        self._mark_message_status(
            queued_message = initial_message,
            status = "thinking"
        )

        workflow_state = ActiveWorkflowState(
            channel_id = initial_message.channel_id,
            messages = self._build_initial_messages(
                user_message = initial_message.content,
                recent_channel_history = initial_message.recent_channel_history
            ),
            participating_messages = [initial_message]
        )
        return workflow_state

    def _insert_pending_same_channel_messages(self, workflow_state:ActiveWorkflowState) -> None:
        """Insert queued same-channel follow-ups into the active workflow.

        Args:
            workflow_state: Current active workflow state.

        Returns:
            None
        """

        messages_to_insert:list[QueuedDiscordMessage] = []

        with self._queue_condition:
            if not self._queued_messages:
                return

            remaining_messages:deque[QueuedDiscordMessage] = deque()
            while self._queued_messages:
                queued_message = self._queued_messages.popleft()
                if queued_message.channel_id == workflow_state.channel_id:
                    messages_to_insert.append(queued_message)
                else:
                    remaining_messages.append(queued_message)

            self._queued_messages = remaining_messages

        if not messages_to_insert:
            return

        print(
            "[Agent] Inserting queued same-channel follow-ups "
            f"channel_id={workflow_state.channel_id} count={len(messages_to_insert)}"
        )

        for queued_message in messages_to_insert:
            self._mark_message_status(
                queued_message = queued_message,
                status = "thinking"
            )
            workflow_state.participating_messages.append(queued_message)
            workflow_state.messages.append(
                {
                    "role": "user",
                    "content": self._build_follow_up_user_message(
                        queued_message = queued_message
                    )
                }
            )

    def _run_single_step(self, workflow_state:ActiveWorkflowState) -> WorkflowStepResult:
        """Execute one LLM/tool step for the active workflow.

        Args:
            workflow_state: Current active workflow state.

        Returns:
            WorkflowStepResult: Structured status for this step.
        """

        if workflow_state.steps_used >= self._config.max_agent_steps:
            print("[Agent] Workflow hit hard step limit")
            limit_message = (
                "I hit the workflow step limit before I could finish safely. "
                "Please send a shorter follow-up or refine the request."
            )
            self._send_discord_message(
                channel_id = workflow_state.channel_id,
                content = limit_message
            )
            workflow_state.sent_messages.append(limit_message)
            return WorkflowStepResult(
                status = "terminal",
                termination_reason = "step_limit",
                final_message = limit_message
            )

        step_number = workflow_state.steps_used + 1
        print(f"[Agent] Running step {step_number}/{self._config.max_agent_steps}")

        assistant_message = self.llm_agent.create_tool_completion(
            messages = workflow_state.messages,
            tools = self._tools.get_openai_tools()
        )
        workflow_state.steps_used += 1
        workflow_state.messages.append(self._message_to_dict(message = assistant_message))

        tool_calls = assistant_message.tool_calls or []
        if not tool_calls:
            final_message = (assistant_message.content or "").strip()
            if final_message:
                print("[Agent] Workflow completed via assistant content fallback")
                self._send_discord_message(
                    channel_id = workflow_state.channel_id,
                    content = final_message
                )
                workflow_state.sent_messages.append(final_message)
                return WorkflowStepResult(
                    status = "terminal",
                    termination_reason = "assistant_content",
                    final_message = final_message
                )

            print("[Agent] Assistant returned neither tool call nor content")
            workflow_state.messages.append(
                {
                    "role": "user",
                    "content": "You must either call a tool or provide a final response."
                }
            )
            return WorkflowStepResult(status = "continue")

        tool_call = tool_calls[0]

        try:
            executed_call = self._tools.execute_tool_call(
                tool_name = tool_call.function.name,
                arguments_json = tool_call.function.arguments
            )
        except Exception as exc:
            print(f"[Agent] Tool execution error: {exc}")
            workflow_state.messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": f"Tool error: {exc}"
                }
            )
            return WorkflowStepResult(status = "continue")

        if executed_call.result.outbound_message:
            print(
                "[Agent] Sending outbound Discord message from tool "
                f"tool_name={executed_call.tool_name}"
            )
            self._send_discord_message(
                channel_id = workflow_state.channel_id,
                content = executed_call.result.outbound_message
            )
            workflow_state.sent_messages.append(executed_call.result.outbound_message)

        workflow_state.messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": executed_call.result.output
            }
        )

        if executed_call.result.is_terminal:
            print(f"[Agent] Workflow completed via tool:{executed_call.tool_name}")
            final_message = executed_call.result.outbound_message or executed_call.result.output
            return WorkflowStepResult(
                status = "terminal",
                termination_reason = f"tool:{executed_call.tool_name}",
                final_message = final_message
            )

        return WorkflowStepResult(status = "continue")

    def _finish_workflow_success(
        self,
        workflow_state:ActiveWorkflowState,
        step_result:WorkflowStepResult
    ) -> None:
        """Finalize a workflow that ended successfully.

        Args:
            workflow_state: Completed workflow state.
            step_result: Terminal step result.

        Returns:
            None
        """

        print(
            "[Agent] Workflow completed successfully "
            f"channel_id={workflow_state.channel_id} messages={len(workflow_state.participating_messages)}"
        )
        self._write_interaction_log(
            messages = workflow_state.messages,
            final_message = self._build_log_final_message(
                workflow_state = workflow_state,
                fallback_message = step_result.final_message
            ),
            termination_reason = step_result.termination_reason or "completed",
            steps_used = workflow_state.steps_used
        )

        for queued_message in workflow_state.participating_messages:
            self._mark_message_status(
                queued_message = queued_message,
                status = "success"
            )

    def _finish_workflow_error(
        self,
        workflow_state:ActiveWorkflowState,
        step_result:WorkflowStepResult
    ) -> None:
        """Finalize a workflow that failed unexpectedly.

        Args:
            workflow_state: Failed workflow state.
            step_result: Error step result.

        Returns:
            None
        """

        error_message = step_result.error_message or "Workflow failed unexpectedly."
        print(
            "[Agent] Workflow failed "
            f"channel_id={workflow_state.channel_id} error={error_message}"
        )
        self._write_interaction_log(
            messages = workflow_state.messages,
            final_message = error_message,
            termination_reason = step_result.termination_reason or "error",
            steps_used = workflow_state.steps_used
        )

        for queued_message in workflow_state.participating_messages:
            self._mark_message_status(
                queued_message = queued_message,
                status = "error"
            )

    def _mark_message_status(self, queued_message:QueuedDiscordMessage, status:str) -> None:
        """Update the Discord reaction state for one queued message.

        Args:
            queued_message: Message whose reaction state should change.
            status: Workflow status name.

        Returns:
            None
        """

        print(
            "[Agent] Updating message reaction "
            f"message_id={queued_message.message_id} channel_id={queued_message.channel_id} status={status}"
        )

        if self._discord_client is None:
            print("[Agent] Discord client bridge is unavailable for reaction update")
            return

        self._discord_client.update_message_status_threadsafe(
            channel_id = queued_message.channel_id,
            message_id = queued_message.message_id,
            status = status
        )

    def _send_discord_message(self, channel_id:int, content:str) -> None:
        """Send a user-facing Discord message through the bot bridge.

        Args:
            channel_id: Discord channel id.
            content: Message content to send.

        Returns:
            None
        """

        if self._discord_client is None:
            print("[Agent] Discord client bridge is unavailable for sending")
            return

        self._discord_client.send_channel_message_threadsafe(
            channel_id = channel_id,
            content = content
        )

    def _build_log_final_message(
        self,
        workflow_state:ActiveWorkflowState,
        fallback_message:str
    ) -> str:
        """Build the final message summary that is stored in logs.

        Args:
            workflow_state: Completed workflow state.
            fallback_message: Terminal message returned by the step result.

        Returns:
            str: Final message summary for logging.
        """

        if workflow_state.sent_messages:
            return "\n\n---\n\n".join(workflow_state.sent_messages)

        if fallback_message:
            return fallback_message

        return "_No final message provided._"

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

    def _build_follow_up_user_message(self, queued_message:QueuedDiscordMessage) -> str:
        """Render a same-channel follow-up turn for an active workflow.

        Args:
            queued_message: Queued message to insert into conversation state.

        Returns:
            str: Model-facing user turn content.
        """

        timestamp_string = queued_message.created_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        return (
            "## Follow-Up User Message\n"
            f"Received at {timestamp_string} in the same Discord channel.\n\n"
            f"{queued_message.content}"
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


