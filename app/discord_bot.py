"""Discord integration for the markdown-driven assistant."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone, time

import discord
from discord.ext import tasks

from app.agent import MarkdownAgent, QueuedDiscordMessage


MAX_HISTORY_HOURS = 50
MAX_HISTORY_MESSAGES = 10
MAX_HISTORY_TOKENS = 10000
HISTORY_FETCH_LIMIT = 50
QUEUED_REACTION = "\N{HOURGLASS WITH FLOWING SAND}"
THINKING_REACTION = "\N{THINKING FACE}"
SUCCESS_REACTION = "\N{WHITE HEAVY CHECK MARK}"
ERROR_REACTION = "\N{CROSS MARK}"
STATUS_REACTIONS = {
    "queued": QUEUED_REACTION,
    "thinking": THINKING_REACTION,
    "success": SUCCESS_REACTION,
    "error": ERROR_REACTION
}


class AssistantDiscordClient(discord.Client):
    """Discord client that queues incoming messages for the markdown agent."""

    def __init__(self, agent:MarkdownAgent):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents = intents)
        self._agent = agent
        self._allowed_user_id = int(os.getenv("DISCORD_ADMIN_ID", "0"))
        self._admin_dm_channel_id = int(os.getenv("DISCORD_ADMIN_DM_CHANNEL_ID", "0"))
        self._worker_started = False
        self._bot_loop:asyncio.AbstractEventLoop | None = None

    @tasks.loop(time = time(hour = 6, tzinfo = timezone(timedelta(hours = -7))))
    async def daily_routine(self) -> None:
        print("[DiscordBot] Kicking off morning routine")

        if self._admin_dm_channel_id == 0:
            print("[DiscordBot] Skipping morning routine because DISCORD_ADMIN_DM_CHANNEL_ID is not configured")
            return

        self._enqueue_synthetic_workflow(
            channel_id = self._admin_dm_channel_id,
            content = "Start my morning routine workflow"
        )


    async def on_ready(self) -> None:
        self._bot_loop = asyncio.get_running_loop()
        print(f"[DiscordBot] Logged in as {self.user}")

        if not self._worker_started:
            self._agent.start_worker(discord_client = self)
            self._worker_started = True
            print("[DiscordBot] Agent worker started")

        if not self.daily_routine.is_running():
            self.daily_routine.start()
            print("[DiscordBot] Daily routine loop started")

    async def on_message(self, message:discord.Message) -> None:
        if message.author == self.user:
            return

        if message.author.id != self._allowed_user_id:
            return

        if message.guild is not None:
            # TODO: we will have future workflows that operate in servers, but for now we only want to respond to DMs
            return

        content = message.content.strip()
        if not content:
            return

        print(f"[DiscordBot] Received DM from {message.author}: {content}")
        recent_channel_history = await self._build_recent_channel_history(message = message)
        await self.update_message_status(
            channel_id = message.channel.id,
            message_id = message.id,
            status = "queued"
        )
        self._agent.enqueue_message(
            QueuedDiscordMessage(
                message_id = message.id,
                channel_id = message.channel.id,
                author_id = message.author.id,
                content = content,
                created_at = message.created_at,
                recent_channel_history = recent_channel_history
            )
        )

    def _enqueue_synthetic_workflow(self, channel_id:int, content:str) -> None:
        """Queue a synthetic workflow trigger without a backing Discord message.

        Args:
            channel_id: Discord channel id that should receive workflow replies.
            content: Synthetic user message that kicks off the workflow.

        Returns:
            None
        """

        print(
            "[DiscordBot] Enqueuing synthetic workflow trigger "
            f"channel_id={channel_id} content={content}"
        )
        self._agent.enqueue_message(
            QueuedDiscordMessage(
                message_id = None,
                channel_id = channel_id,
                author_id = self._allowed_user_id,
                content = content,
                created_at = datetime.now(timezone.utc),
                recent_channel_history = ""
            )
        )

    def update_message_status_threadsafe(
        self,
        channel_id:int,
        message_id:int | None,
        status:str
    ) -> None:
        """Schedule a message reaction status update from a worker thread.

        Args:
            channel_id: Discord channel id.
            message_id: Discord message id.
            status: Target status name.

        Returns:
            None
        """

        if self._bot_loop is None:
            print("[DiscordBot] Cannot update reactions before the bot loop is ready")
            return

        future = asyncio.run_coroutine_threadsafe(
            self.update_message_status(
                channel_id = channel_id,
                message_id = message_id,
                status = status
            ),
            self._bot_loop
        )

        try:
            future.result(timeout = 30)
        except Exception as exc:
            print(f"[DiscordBot] Failed to update message status: {exc}")

    def send_channel_message_threadsafe(self, channel_id:int, content:str) -> None:
        """Schedule a channel send from a worker thread.

        Args:
            channel_id: Discord channel id.
            content: User-facing message content.

        Returns:
            None
        """

        if self._bot_loop is None:
            print("[DiscordBot] Cannot send messages before the bot loop is ready")
            return

        future = asyncio.run_coroutine_threadsafe(
            self.send_channel_message(
                channel_id = channel_id,
                content = content
            ),
            self._bot_loop
        )

        try:
            future.result(timeout = 30)
        except Exception as exc:
            print(f"[DiscordBot] Failed to send channel message: {exc}")

    async def update_message_status(
        self,
        channel_id:int,
        message_id:int | None,
        status:str
    ) -> None:
        """Replace the bot's known status reactions on one message.

        Args:
            channel_id: Discord channel id.
            message_id: Discord message id.
            status: Target status name.

        Returns:
            None
        """

        if status not in STATUS_REACTIONS:
            raise ValueError(f"Unknown Discord message status: {status}")

        if message_id is None:
            print(
                "[DiscordBot] Skipping reaction update because no Discord message id was provided "
                f"channel_id={channel_id} status={status}"
            )
            return

        message = await self._fetch_message(
            channel_id = channel_id,
            message_id = message_id
        )
        if message is None or self.user is None:
            return

        print(
            "[DiscordBot] Updating reactions "
            f"message_id={message_id} channel_id={channel_id} status={status}"
        )
        
        await self._safe_add_reaction(
            message = message,
            reaction = STATUS_REACTIONS[status]
        )

        for reaction in STATUS_REACTIONS.values():
            if reaction == STATUS_REACTIONS[status]:
                continue
            await self._safe_remove_reaction(
                message = message,
                reaction = reaction
            )


    async def send_channel_message(self, channel_id:int, content:str) -> None:
        """Send a Discord message to a channel by id.

        Args:
            channel_id: Discord channel id.
            content: User-facing message content.

        Returns:
            None
        """

        channel = await self._fetch_channel(channel_id = channel_id)
        if channel is None:
            return

        print(f"[DiscordBot] Sending channel message channel_id={channel_id}")

        try:
            await channel.send(content)
        except discord.HTTPException as exc:
            print(f"[DiscordBot] Failed to send channel message: {exc}")

    async def _fetch_channel(self, channel_id:int):
        """Fetch a Discord channel from cache or API.

        Args:
            channel_id: Discord channel id.

        Returns:
            Any: Discord channel object or None.
        """

        channel = self.get_channel(channel_id)
        if channel is not None:
            return channel

        try:
            channel = await self.fetch_channel(channel_id)
        except discord.HTTPException as exc:
            print(f"[DiscordBot] Failed to fetch channel {channel_id}: {exc}")
            return None

        return channel

    async def _fetch_message(self, channel_id:int, message_id:int) -> discord.Message | None:
        """Fetch a Discord message by channel and message id.

        Args:
            channel_id: Discord channel id.
            message_id: Discord message id.

        Returns:
            discord.Message | None: Message object when available.
        """

        channel = await self._fetch_channel(channel_id = channel_id)
        if channel is None or not hasattr(channel, "fetch_message"):
            print(f"[DiscordBot] Channel {channel_id} cannot fetch messages")
            return None

        try:
            return await channel.fetch_message(message_id)
        except discord.HTTPException as exc:
            print(f"[DiscordBot] Failed to fetch message {message_id}: {exc}")
            return None

    async def _safe_add_reaction(self, message:discord.Message, reaction:str) -> None:
        """Add a reaction while swallowing Discord transport errors.

        Args:
            message: Target Discord message.
            reaction: Emoji string to add.

        Returns:
            None
        """

        try:
            await message.add_reaction(reaction)
        except discord.HTTPException as exc:
            print(f"[DiscordBot] Failed to add reaction {reaction}: {exc}")

    async def _safe_remove_reaction(self, message:discord.Message, reaction:str) -> None:
        """Remove one bot-owned reaction while swallowing Discord transport errors.

        Args:
            message: Target Discord message.
            reaction: Emoji string to remove.

        Returns:
            None
        """

        if self.user is None:
            return

        try:
            await message.remove_reaction(reaction, self.user)
        except discord.HTTPException:
            pass

    async def _build_recent_channel_history(self, message:discord.Message) -> str:
        """Collect recent same-channel messages to prepend to the agent input.

        Args:
            message: Incoming Discord message that triggered the workflow.

        Returns:
            str: Formatted recent channel history, or an empty string when no history qualifies.
        """

        cutoff_time = message.created_at - timedelta(hours = MAX_HISTORY_HOURS)
        formatted_messages:list[str] = []

        print(f"[DiscordBot] Fetching up to {MAX_HISTORY_MESSAGES} recent messages from the same channel")
        async for historical_message in message.channel.history(
            limit = HISTORY_FETCH_LIMIT,
            before = message.created_at,
            oldest_first = False
        ):
            if historical_message.created_at < cutoff_time:
                continue

            historical_content = historical_message.content.strip()
            if not historical_content:
                continue

            formatted_messages.insert(
                0,
                self._format_history_message(
                    historical_message = historical_message,
                    reference_time = message.created_at
                )
            )

            if len(formatted_messages) >= MAX_HISTORY_MESSAGES:
                break

        if not formatted_messages:
            print("[DiscordBot] No qualifying channel history found")
            return ""

        while self._estimate_token_count(messages = formatted_messages) > MAX_HISTORY_TOKENS:
            removed_message = formatted_messages.pop(0)
            print(
                "[DiscordBot] Dropped oldest history message to stay within token budget: "
                f"{removed_message[:80]}"
            )

        print(f"[DiscordBot] Prepared {len(formatted_messages)} history messages for the agent")
        return "\n\n".join(formatted_messages)

    def _estimate_token_count(self, messages:list[str]) -> int:
        """Estimate token count for formatted history messages.

        Args:
            messages: Formatted messages that may be sent to the model.

        Returns:
            int: Approximate token count using a conservative character-based estimate.
        """

        return sum(max(1, (len(message) + 3) // 4) for message in messages)

    def _format_history_message(
        self,
        historical_message:discord.Message,
        reference_time:datetime
    ) -> str:
        """Render one historical Discord message for the agent prompt.

        Args:
            historical_message: Historical message from the same Discord channel.
            reference_time: Timestamp of the newly received Discord message.

        Returns:
            str: Agent-ready history entry with relative time and timestamp.
        """

        speaker = "you" if historical_message.author.id == self._allowed_user_id else "they"
        relative_age = self._format_relative_age(
            newer_time = reference_time,
            older_time = historical_message.created_at
        )
        timestamp_string = historical_message.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        return (
            f"{relative_age} ago {speaker} wrote ({timestamp_string}):\n"
            f"{historical_message.content.strip()}"
        )

    def _format_relative_age(
        self,
        newer_time:datetime,
        older_time:datetime
    ) -> str:
        """Create a compact human-readable age string.

        Args:
            newer_time: More recent timestamp.
            older_time: Older timestamp.

        Returns:
            str: Relative age such as `5m`, `2h 4m`, or `1d 3h`.
        """

        age_delta = newer_time - older_time
        total_seconds = max(0, int(age_delta.total_seconds()))
        days, remainder = divmod(total_seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, _ = divmod(remainder, 60)

        if days > 0:
            return f"{days}d {hours}h"

        if hours > 0:
            return f"{hours}h {minutes}m"

        if minutes > 0:
            return f"{minutes}m"

        return "0m"

