"""Core markdown-driven agent loop."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field, fields as dataclass_fields, is_dataclass
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import threading
from typing import TYPE_CHECKING, Any

from agents import (
    Agent,
    ItemHelpers,
    RunConfig,
    Runner,
    WebSearchTool,
    set_default_openai_client,
    set_default_openai_key
)
from openai import AsyncOpenAI

from app.agent_runtime import AgentRuntimeContext
from app.config import AppConfig
from app.discord_utils import DiscordChannelCategory, DiscordMessageStatus
from app.markdown_loader import load_optional_markdown
from app.tool_registry import ToolRegistry
from app.util import get_datetime_string
from tools.markdown_tools import list_markdown_files

if TYPE_CHECKING:
    from app.discord_bot import AssistantDiscordClient

logger = logging.getLogger(__name__)

MAX_RETAINED_HISTORY_ITEMS = 60


@dataclass(frozen = True)
class AgentResponse:
    """Terminal result of an agent run.

    Args:
        message: Final message to send back to the user.
        steps_used: Number of model turns that were consumed.

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

    message_id:int | None
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
        instructions: Agents SDK system instructions for the workflow.
        messages: Full conversation state passed back to the SDK.
        participating_messages: Messages already inserted into conversation state.
        sent_messages: User-facing Discord messages sent during the run.
        steps_used: Number of model turns consumed.
        last_activity_at: Most recent workflow activity timestamp.

    Returns:
        ActiveWorkflowState: Current workflow runtime state.
    """

    channel_id:int
    instructions:str
    messages:list[dict[str, Any]]
    participating_messages:list[QueuedDiscordMessage] = field(default_factory = list)
    sent_messages:list[str] = field(default_factory = list)
    steps_used:int = 0
    last_activity_at:datetime = field(default_factory = lambda: datetime.now(timezone.utc))


@dataclass(frozen = True)
class WorkflowStepResult:
    """Result of executing a single workflow step.

    Args:
        status: One of terminal or error.
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
    """Markdown-configured Discord assistant backed by the OpenAI Agents SDK."""

    def __init__(self, config:AppConfig):
        self._config = config
        self._configure_openai_sdk()
        self._tools = ToolRegistry(
            project_root = config.project_root,
            llm_config = config.agent_llm,
            markdown_publisher = self._publish_markdown_update
        )
        self._discord_client:AssistantDiscordClient | None = None
        self._queue_condition = threading.Condition()
        self._queued_messages:deque[QueuedDiscordMessage] = deque()
        self._active_workflow:ActiveWorkflowState | None = None
        self._worker_thread:threading.Thread | None = None

    def _configure_openai_sdk(self) -> None:
        """Configure the Agents SDK with the configured OpenAI client.

        Args:
            None

        Returns:
            None
        """

        if self._config.agent_llm.base_url:
            logger.info(
                "Configuring Agents SDK with custom OpenAI base_url=%s",
                self._config.agent_llm.base_url
            )
            set_default_openai_client(
                AsyncOpenAI(
                    api_key = self._config.agent_llm.api_key,
                    base_url = self._config.agent_llm.base_url
                ),
                use_for_tracing = False
            )
            return

        logger.info("Configuring Agents SDK with default OpenAI key")
        set_default_openai_key(self._config.agent_llm.api_key)

    def _publish_markdown_update(
        self,
        category_name:DiscordChannelCategory,
        channel_name:str,
        content:str
    ) -> None:
        """Mirror one markdown update into the mapped Discord channel.

        Args:
            category_name: Target Discord category.
            channel_name: Target Discord text channel name.
            content: Message content to publish.

        Returns:
            None
        """

        if self._discord_client is None:
            logger.warning("Discord client bridge is unavailable for markdown publication")
            return

        logger.info(
            "Mirroring markdown update to Discord category=%s channel_name=%s",
            category_name,
            channel_name
        )
        self._discord_client.send_guild_channel_message_threadsafe(
            guild_id = self._config.guild_id,
            category_name = category_name,
            channel_name = channel_name,
            content = content
        )

    def start_worker(self, discord_client:AssistantDiscordClient) -> None:
        """Start the dedicated workflow worker thread.

        Args:
            discord_client: Discord client bridge used for reactions and sends.

        Returns:
            None
        """

        self._discord_client = discord_client
        if self._worker_thread is not None and self._worker_thread.is_alive():
            logger.debug("Worker thread already running")
            return

        self._worker_thread = threading.Thread(
            target = self._worker_loop,
            name = "markdown-agent-worker",
            daemon = True
        )
        self._worker_thread.start()
        logger.info("Worker thread started")

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

        logger.info(
            "Enqueued message message_id=%s channel_id=%s queue_size=%s",
            queued_message.message_id,
            queued_message.channel_id,
            queue_size
        )

    def _worker_loop(self) -> None:
        """Run queued workflows sequentially on a background thread.

        Args:
            None

        Returns:
            None
        """

        logger.info("Worker loop is running")

        while True:
            queued_message = self._wait_for_next_message()
            workflow_state:ActiveWorkflowState | None = None

            try:
                workflow_state = self._start_workflow(initial_message = queued_message)
                self._active_workflow = workflow_state
                self._insert_pending_same_channel_messages(workflow_state = workflow_state)
                step_result = self._run_single_step(workflow_state = workflow_state)

                if step_result.status == "terminal":
                    self._finish_workflow_success(
                        workflow_state = workflow_state,
                        step_result = step_result
                    )
                else:
                    self._finish_workflow_error(
                        workflow_state = workflow_state,
                        step_result = step_result
                    )
            except Exception as exc:
                logger.exception("Unhandled workflow error")
                self._log_raw_execution(
                    payload = {
                        "type": "exception",
                        "source": "workflow",
                        "error": str(exc)
                    }
                )

                if workflow_state is not None:
                    self._finish_workflow_error(
                        workflow_state = workflow_state,
                        step_result = WorkflowStepResult(
                            status = "error",
                            termination_reason = "unhandled_exception",
                            error_message = "Unhandled workflow error"
                        )
                    )
                else:
                    self._mark_message_status(
                        queued_message = queued_message,
                        status = DiscordMessageStatus.ERROR
                    )
            finally:
                if workflow_state is None:
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

        logger.debug(
            "Dequeued message message_id=%s channel_id=%s",
            queued_message.message_id,
            queued_message.channel_id
        )
        return queued_message

    def _start_workflow(self, initial_message:QueuedDiscordMessage) -> ActiveWorkflowState:
        """Initialize workflow state from the first queued message.

        Args:
            initial_message: First message that starts the workflow.

        Returns:
            ActiveWorkflowState: Newly initialized or retained workflow state.
        """

        logger.info(
            "Starting workflow channel_id=%s message_id=%s",
            initial_message.channel_id,
            initial_message.message_id
        )
        self._mark_message_status(
            queued_message = initial_message,
            status = DiscordMessageStatus.THINKING
        )

        previous_workflow = self._active_workflow
        if previous_workflow is not None and not self._should_reset_messages_for_new_workflow(
            previous_workflow = previous_workflow,
            initial_message = initial_message
        ):
            logger.info(
                "Reusing retained conversation history channel_id=%s previous_messages=%s",
                initial_message.channel_id,
                len(previous_workflow.messages)
            )
            previous_workflow.channel_id = initial_message.channel_id
            previous_workflow.participating_messages = [initial_message]
            previous_workflow.sent_messages = []
            previous_workflow.steps_used = 0
            previous_workflow.last_activity_at = self._normalize_datetime(value = initial_message.created_at)
            self._append_message_to_history(
                workflow_state = previous_workflow,
                message = self._build_user_input_item(
                    content = self._build_follow_up_user_message(
                        queued_message = initial_message
                    )
                )
            )
            return previous_workflow

        workflow_state = ActiveWorkflowState(
            channel_id = initial_message.channel_id,
            instructions = self._build_system_prompt(),
            messages = [],
            participating_messages = [initial_message],
            last_activity_at = self._normalize_datetime(value = initial_message.created_at)
        )
        for message in self._build_initial_messages(
            user_message = initial_message.content,
            recent_channel_history = initial_message.recent_channel_history
        ):
            self._append_message_to_history(
                workflow_state = workflow_state,
                message = message
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

        logger.info(
            "Inserting queued same-channel follow-ups channel_id=%s count=%s",
            workflow_state.channel_id,
            len(messages_to_insert)
        )

        for queued_message in messages_to_insert:
            self._mark_message_status(
                queued_message = queued_message,
                status = DiscordMessageStatus.THINKING
            )
            workflow_state.participating_messages.append(queued_message)
            workflow_state.last_activity_at = self._normalize_datetime(value = queued_message.created_at)
            self._append_message_to_history(
                workflow_state = workflow_state,
                message = self._build_user_input_item(
                    content = self._build_follow_up_user_message(
                        queued_message = queued_message
                    )
                )
            )

    def _run_single_step(self, workflow_state:ActiveWorkflowState) -> WorkflowStepResult:
        """Execute one complete Agents SDK run for the active workflow.

        Args:
            workflow_state: Current active workflow state.

        Returns:
            WorkflowStepResult: Structured terminal status for this SDK run.
        """

        logger.info(
            "Running Agents SDK workflow max_turns=%s history_items=%s",
            self._config.max_agent_steps,
            len(workflow_state.messages)
        )
        runtime_context = AgentRuntimeContext(
            channel_id = workflow_state.channel_id,
            send_channel_message = self._send_discord_message,
            sent_messages = workflow_state.sent_messages
        )
        agent = self._build_agent(instructions = workflow_state.instructions)
        result, streamed_text_messages = asyncio.run(
            self._run_streamed_agent(
                agent = agent,
                workflow_state = workflow_state,
                runtime_context = runtime_context
            )
        )
        workflow_state.steps_used += max(1, len(result.raw_responses))
        workflow_state.last_activity_at = datetime.now(timezone.utc)

        final_message = str(result.final_output or "").strip()
        if not final_message:
            logger.warning("Agent returned no final output")
            final_message = "I finished, but I did not produce a final response."

        success_log = self._build_agent_success_log(
            workflow_state = workflow_state,
            runtime_context = runtime_context,
            model_turns = len(result.raw_responses),
            final_message = final_message
        )
        if success_log is not None:
            self._log_raw_execution(payload = success_log)

        workflow_state.messages = self._build_retained_messages(
            existing_messages = workflow_state.messages,
            assistant_message = final_message
        )

        if not streamed_text_messages:
            logger.info("No streamed agent text response was sent; sending final output fallback")
            self._send_agent_text_message(
                workflow_state = workflow_state,
                text_message = final_message
            )

        result.release_agents()

        return WorkflowStepResult(
            status = "terminal",
            termination_reason = "agent_final_output",
            final_message = final_message
        )

    async def _run_streamed_agent(
        self,
        agent:Agent[AgentRuntimeContext],
        workflow_state:ActiveWorkflowState,
        runtime_context:AgentRuntimeContext
    ) -> tuple[Any, list[str]]:
        """Run the SDK agent and publish message events as they arrive.

        Args:
            agent: Configured Agents SDK agent.
            workflow_state: Current workflow state receiving streamed text.
            runtime_context: Runtime context passed to SDK tools.

        Returns:
            tuple[Any, list[str]]: The streaming run result and sent assistant texts.
        """

        result = Runner.run_streamed(
            agent,
            workflow_state.messages,
            context = runtime_context,
            max_turns = self._config.max_agent_steps,
            run_config = RunConfig(
                workflow_name = "discord-assistant",
                group_id = str(workflow_state.channel_id),
                trace_include_sensitive_data = False
            )
        )
        streamed_text_messages:list[str] = []

        async for event in result.stream_events():
            text_message = self._extract_streamed_agent_text_message(event = event)
            if not text_message:
                continue

            self._send_agent_text_message(
                workflow_state = workflow_state,
                text_message = text_message
            )
            streamed_text_messages.append(text_message)

        logger.info(
            "Completed streamed agent run text_responses=%s",
            len(streamed_text_messages)
        )
        return result, streamed_text_messages

    def _extract_streamed_agent_text_message(self, event:Any) -> str:
        """Extract one assistant text message from a streaming SDK event.

        Args:
            event: Agents SDK stream event.

        Returns:
            str: Assistant text content, or an empty string for non-message events.
        """

        if getattr(event, "type", "") != "run_item_stream_event":
            return ""

        if getattr(event, "name", "") != "message_output_created":
            return ""

        item = getattr(event, "item", None)
        if getattr(item, "type", "") != "message_output_item":
            return ""

        try:
            return ItemHelpers.text_message_output(item).strip()
        except Exception:
            logger.exception("Failed to extract text from streamed agent message")
            return ""

    def _send_agent_text_message(
        self,
        workflow_state:ActiveWorkflowState,
        text_message:str
    ) -> None:
        """Send one assistant text response to Discord and record it for logs.

        Args:
            workflow_state: Current workflow state receiving the text response.
            text_message: Assistant text message to publish.

        Returns:
            None
        """

        if not text_message:
            logger.warning("Skipping empty agent text response")
            return

        logger.info("Sending streamed agent text response to Discord")
        self._send_discord_message(
            channel_id = workflow_state.channel_id,
            content = text_message
        )
        workflow_state.sent_messages.append(text_message)

    def _build_agent_success_log(
        self,
        workflow_state:ActiveWorkflowState,
        runtime_context:AgentRuntimeContext,
        model_turns:int,
        final_message:str
    ) -> dict[str, Any] | None:
        """Build a compact Discord log payload for a successful agent run.

        Args:
            workflow_state: Workflow state for the completed run.
            runtime_context: Runtime context containing tool execution summaries.
            model_turns: Number of model responses used by the SDK run.
            final_message: Final assistant message, used only for length metadata.

        Returns:
            dict[str, Any] | None: Minimal success log payload, or None when nothing notable ran.
        """

        if not runtime_context.tool_events:
            return None

        return {
            "type": "agent_run",
            "status": "success",
            "channel_id": workflow_state.channel_id,
            "model": self._config.agent_llm.model,
            "model_turns": model_turns,
            "tools": runtime_context.tool_events,
            "final_message_chars": len(final_message)
        }

    def _build_agent(self, instructions:str) -> Agent[AgentRuntimeContext]:
        """Build the SDK agent for one workflow run.

        Args:
            instructions: System instructions assembled from markdown assets.

        Returns:
            Agent[AgentRuntimeContext]: Configured main assistant agent.
        """

        return Agent[AgentRuntimeContext](
            name = "Markdown Discord Assistant",
            model = self._config.agent_llm.model,
            instructions = instructions,
            tools = [
                *self._tools.get_agent_tools(),
                WebSearchTool(search_context_size = "medium")
            ]
        )

    def _build_retained_messages(
        self,
        existing_messages:list[dict[str, Any]],
        assistant_message:str
    ) -> list[dict[str, Any]]:
        """Build safe history for the next Discord turn.

        Args:
            existing_messages: Current app-managed user/assistant message history.
            assistant_message: Final assistant response from the completed SDK run.

        Returns:
            list[dict[str, Any]]: Message-only history without tool or reasoning items.
        """

        retained_messages = [
            message
            for message in existing_messages
            if self._is_plain_message_item(message = message)
        ]
        retained_messages.append(
            {
                "role": "assistant",
                "content": assistant_message
            }
        )
        return self._trim_history_items(messages = retained_messages)

    def _is_plain_message_item(self, message:dict[str, Any]) -> bool:
        """Return whether an item is safe to retain as next-run input.

        Args:
            message: Candidate history item.

        Returns:
            bool: True when the item is a plain user or assistant message.
        """

        if not isinstance(message, dict):
            return False

        return message.get("role") in {"user", "assistant"}

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

        logger.info(
            "Workflow completed successfully channel_id=%s messages=%s",
            workflow_state.channel_id,
            len(workflow_state.participating_messages)
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
                status = DiscordMessageStatus.SUCCESS
            )

        workflow_state.last_activity_at = datetime.now(timezone.utc)
        self._active_workflow = workflow_state

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
        logger.error(
            "Workflow failed channel_id=%s error=%s",
            workflow_state.channel_id,
            error_message
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
                status = DiscordMessageStatus.ERROR
            )

        workflow_state.last_activity_at = datetime.now(timezone.utc)
        self._active_workflow = None

    def _mark_message_status(
        self,
        queued_message:QueuedDiscordMessage,
        status:DiscordMessageStatus
    ) -> None:
        """Update the Discord reaction state for one queued message.

        Args:
            queued_message: Message whose reaction state should change.
            status: Workflow status value.

        Returns:
            None
        """

        logger.debug(
            "Updating message reaction message_id=%s channel_id=%s status=%s",
            queued_message.message_id,
            queued_message.channel_id,
            status
        )

        if queued_message.message_id is None:
            logger.debug(
                "Skipping Discord reaction update for synthetic workflow trigger channel_id=%s status=%s",
                queued_message.channel_id,
                status
            )
            return

        if self._discord_client is None:
            logger.warning("Discord client bridge is unavailable for reaction update")
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
            logger.warning("Discord client bridge is unavailable for sending")
            return

        self._discord_client.send_channel_message_threadsafe(
            channel_id = channel_id,
            content = content
        )

    def _log_raw_execution(self, payload:dict[str, Any]) -> None:
        """Mirror raw JSON payloads into the Discord logs channel.

        Args:
            payload: Raw JSON payload to mirror.

        Returns:
            None
        """

        if self._discord_client is None:
            logger.warning("Discord client bridge is unavailable for logs")
            return

        try:
            serialized_payload = json.dumps(
                self._json_safe(value = payload),
                indent = 2,
                ensure_ascii = False
            )
        except Exception as exc:
            logger.exception("Failed to serialize raw execution payload")
            serialized_payload = json.dumps(
                {
                    "type": "log_serialization_error",
                    "error": str(exc),
                    "payload_type": type(payload).__name__
                },
                indent = 2,
                ensure_ascii = False
            )

        self._discord_client.send_logs_message_threadsafe(content = serialized_payload)

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
        """Construct the initial SDK input items for the agent.

        Args:
            user_message: Raw DM content from the user.
            recent_channel_history: Optional formatted transcript from the same Discord channel.

        Returns:
            list[dict[str, Any]]: Initial user input items for the model.
        """

        return [
            self._build_user_input_item(
                content = self._build_user_message(
                    user_message = user_message,
                    recent_channel_history = recent_channel_history
                )
            )
        ]

    def _should_reset_messages_for_new_workflow(
        self,
        previous_workflow:ActiveWorkflowState,
        initial_message:QueuedDiscordMessage
    ) -> bool:
        """Determine whether a new workflow must start with fresh history.

        Args:
            previous_workflow: Retained state from the previous completed workflow.
            initial_message: New queued message starting the next workflow.

        Returns:
            bool: True when retained history should be discarded.
        """

        if previous_workflow.channel_id != initial_message.channel_id:
            logger.info(
                "Resetting retained history due to channel change previous_channel_id=%s new_channel_id=%s",
                previous_workflow.channel_id,
                initial_message.channel_id
            )
            return True

        elapsed = self._normalize_datetime(value = initial_message.created_at) - previous_workflow.last_activity_at
        if elapsed > timedelta(minutes = 30):
            logger.info(
                "Resetting retained history due to inactivity elapsed_minutes=%.1f",
                elapsed.total_seconds() / 60
            )
            return True

        return False

    def _normalize_datetime(self, value:datetime) -> datetime:
        """Normalize a datetime value to a timezone-aware UTC timestamp.

        Args:
            value: Datetime to normalize.

        Returns:
            datetime: Timezone-aware UTC timestamp.
        """

        if value.tzinfo is None:
            return value.replace(tzinfo = timezone.utc)
        return value.astimezone(timezone.utc)

    def _append_message_to_history(
        self,
        workflow_state:ActiveWorkflowState,
        message:dict[str, Any]
    ) -> None:
        """Append one input item and trim retained history if needed.

        Args:
            workflow_state: Workflow state whose conversation should be updated.
            message: SDK input item payload to append.

        Returns:
            None
        """

        workflow_state.messages.append(message)
        workflow_state.messages = self._trim_history_items(messages = workflow_state.messages)

    def _trim_history_items(self, messages:list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Trim retained conversation history to a bounded size.

        Args:
            messages: Current retained conversation history.

        Returns:
            list[dict[str, Any]]: Trimmed conversation history.
        """

        if len(messages) <= MAX_RETAINED_HISTORY_ITEMS:
            return messages

        trimmed_messages = messages[-MAX_RETAINED_HISTORY_ITEMS:]
        logger.debug(
            "Trimmed conversation history from %s to %s items",
            len(messages),
            len(trimmed_messages)
        )
        return trimmed_messages

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
        if memories_prompt:
            system_prompt += (
                "**Existing memories:**\n\n"
                f"{memories_prompt}\n\n"
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
            str: Newline-separated project-relative markdown paths.
        """

        logger.debug("Building prompt file list for: %s", directory)
        available_files = list_markdown_files(
            directory = directory,
            project_root = self._config.project_root
        )
        if not available_files:
            return "_None_"

        return "\n".join(available_files)

    def _build_user_input_item(self, content:str) -> dict[str, Any]:
        """Build one user input item for the Agents SDK.

        Args:
            content: User-visible text payload.

        Returns:
            dict[str, Any]: User input item.
        """

        return {
            "role": "user",
            "content": content
        }

    def _get_history_item_label(self, message:Any) -> str:
        """Return a compact label for one retained history item.

        Args:
            message: Retained history item.

        Returns:
            str: Role, type, or class-name label for logs.
        """

        if isinstance(message, dict):
            return str(message.get("role") or message.get("type") or "unknown")

        return type(message).__name__

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
            steps_used: Number of model turns consumed.

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
            f"- Model turns used: {steps_used}",
            "",
            "## Final Message",
            "",
            final_message or "_No final message provided._",
            "",
            "## Message History",
            ""
        ]

        for index, message in enumerate(messages, start = 1):
            role = self._get_history_item_label(message = message)
            log_lines.extend(
                [
                    f"### {index}. {role}",
                    "",
                    "```json",
                    json.dumps(
                        self._json_safe(value = message),
                        indent = 2,
                        ensure_ascii = False
                    ),
                    "```",
                    ""
                ]
            )

        log_path.write_text("\n".join(log_lines), encoding = "utf-8")
        logger.info("Wrote interaction log to %s", log_path)

    def _json_safe(self, value:Any, seen:set[int] | None = None) -> Any:
        """Convert SDK objects into JSON-serializable values.

        Args:
            value: Arbitrary value returned by the SDK or local runtime.
            seen: Object ids already traversed during this conversion.

        Returns:
            Any: JSON-serializable representation.
        """

        if value is None or isinstance(value, (str, int, float, bool)):
            return value

        if seen is None:
            seen = set()

        value_id = id(value)
        if value_id in seen:
            return repr(value)
        seen.add(value_id)

        if isinstance(value, Path):
            return str(value)

        if callable(value):
            return repr(value)

        if isinstance(value, dict):
            return {
                str(key): self._json_safe(value = item, seen = seen)
                for key, item in value.items()
            }

        if isinstance(value, (list, tuple, set)):
            return [
                self._json_safe(value = item, seen = seen)
                for item in value
            ]

        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            try:
                return self._json_safe(value = model_dump(mode = "python"), seen = seen)
            except Exception as exc:
                logger.debug(
                    "Falling back from model_dump during JSON serialization type=%s error=%s",
                    type(value).__name__,
                    exc
                )

        if is_dataclass(value) and not isinstance(value, type):
            return {
                dataclass_field.name: self._json_safe(
                    value = getattr(value, dataclass_field.name),
                    seen = seen
                )
                for dataclass_field in dataclass_fields(value)
            }

        if hasattr(value, "__dict__"):
            return self._json_safe(value = vars(value), seen = seen)

        return str(value)
