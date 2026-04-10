"""Discord integration for the markdown-driven assistant."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone, time

import discord
from discord.ext import tasks

from app.agent import MarkdownAgent, QueuedDiscordMessage
from app.config import AppConfig
from app.discord_utils import (
    DISCORD_MESSAGE_CHARACTER_LIMIT,
    DiscordChannelCategory,
    DiscordMessageStatus,
    split_discord_message
)


MAX_HISTORY_HOURS = 50
MAX_HISTORY_MESSAGES = 4
MAX_HISTORY_TOKENS = 10000
HISTORY_FETCH_LIMIT = 50
QUEUED_REACTION = "\N{HOURGLASS WITH FLOWING SAND}"
THINKING_REACTION = "\N{THINKING FACE}"
SUCCESS_REACTION = "\N{WHITE HEAVY CHECK MARK}"
ERROR_REACTION = "\N{CROSS MARK}"
STATUS_REACTIONS:dict[DiscordMessageStatus, str] = {
    DiscordMessageStatus.QUEUED: QUEUED_REACTION,
    DiscordMessageStatus.THINKING: THINKING_REACTION,
    DiscordMessageStatus.SUCCESS: SUCCESS_REACTION,
    DiscordMessageStatus.ERROR: ERROR_REACTION
}


class AssistantDiscordClient(discord.Client):
    """Discord client that queues incoming messages for the markdown agent."""

    def __init__(self, agent:MarkdownAgent, config:AppConfig):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents = intents)
        self._agent = agent
        self._config = config
        self._allowed_user_id = int(os.getenv("DISCORD_ADMIN_ID", "0"))
        self._admin_dm_channel_id = int(os.getenv("DISCORD_ADMIN_DM_CHANNEL_ID", "0"))
        self._logs_channel_id:int | None = None
        self._worker_started = False
        self._bot_loop:asyncio.AbstractEventLoop | None = None


    @tasks.loop(time = time(hour = 13))
    async def daily_routine(self) -> None:

        if self._admin_dm_channel_id == 0:
            print("[DiscordBot] Skipping daily routine because DISCORD_ADMIN_DM_CHANNEL_ID is not configured")
            return

        print("[DiscordBot] Kicking off daily routine")

        self._enqueue_synthetic_workflow(
            channel_id = self._admin_dm_channel_id,
            content = "This is an autmated reminder for you to start my morning routine workflow."
        )


    async def setup_hook(self) -> None:
        self._bot_loop = asyncio.get_running_loop()

        if self._logs_channel_id is None:
            logs_channel = await self.get_or_create_guild_text_channel(
                guild_id = self._config.guild_id,
                category_name = DiscordChannelCategory.OTHER,
                channel_name = "logs"
            )
            if logs_channel is not None:
                self._logs_channel_id = logs_channel.id
                print(f"[DiscordBot] Logs channel ready channel_id={self._logs_channel_id}")

        if not self._worker_started:
            self._agent.start_worker(discord_client = self)
            self._worker_started = True
            print("[DiscordBot] Agent worker started")

        if not self.daily_routine.is_running():
            self.daily_routine.start()
            print("[DiscordBot] Daily routine loop started")

    # async def on_ready(self) -> None:
    #     self._bot_loop = asyncio.get_running_loop()
    #     print(f"[DiscordBot] Logged in as {self.user}")

    #     if self._logs_channel_id is None:
    #         logs_channel = await self.get_or_create_guild_text_channel(
    #             guild_id = self._config.guild_id,
    #             category_name = DiscordChannelCategory.OTHER,
    #             channel_name = "logs"
    #         )
    #         if logs_channel is not None:
    #             self._logs_channel_id = logs_channel.id
    #             print(f"[DiscordBot] Logs channel ready channel_id={self._logs_channel_id}")

    #     if not self._worker_started:
    #         self._agent.start_worker(discord_client = self)
    #         self._worker_started = True
    #         print("[DiscordBot] Agent worker started")

    #     if not self.daily_routine.is_running():
    #         self.daily_routine.start()
    #         print("[DiscordBot] Daily routine loop started")


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
            status = DiscordMessageStatus.QUEUED
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
        status:DiscordMessageStatus
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

    def send_logs_message_threadsafe(self, content:str) -> None:
        """Schedule a raw execution log send from a worker thread.

        Args:
            content: Raw execution text to mirror into the logs channel.

        Returns:
            None
        """

        if self._bot_loop is None:
            print("[DiscordBot] Cannot send logs before the bot loop is ready")
            return

        future = asyncio.run_coroutine_threadsafe(
            self.send_logs_message(content = content),
            self._bot_loop
        )

        try:
            future.result(timeout = 30)
        except Exception as exc:
            print(f"[DiscordBot] Failed to send logs message: {exc}")

    def send_guild_channel_message_threadsafe(
        self,
        guild_id:int,
        category_name:DiscordChannelCategory,
        channel_name:str,
        content:str
    ) -> None:
        """Schedule a guild channel send by category/name from a worker thread.

        Args:
            guild_id: Discord guild id that owns the target channel.
            category_name: Target category name.
            channel_name: Target text channel name.
            content: User-facing message content.

        Returns:
            None
        """

        if self._bot_loop is None:
            print("[DiscordBot] Cannot send guild channel messages before the bot loop is ready")
            return

        future = asyncio.run_coroutine_threadsafe(
            self.send_guild_channel_message(
                guild_id = guild_id,
                category_name = category_name,
                channel_name = channel_name,
                content = content
            ),
            self._bot_loop
        )

        try:
            future.result(timeout = 30)
        except Exception as exc:
            print(f"[DiscordBot] Failed to send guild channel message: {exc}")

    async def update_message_status(
        self,
        channel_id:int,
        message_id:int | None,
        status:DiscordMessageStatus
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

        for content_chunk in split_discord_message(content = content):
            try:
                await channel.send(content_chunk)
            except discord.HTTPException as exc:
                print(f"[DiscordBot] Failed to send channel message: {exc}")
                return

    async def send_logs_message(self, content:str) -> None:
        """Send raw execution text to the logs channel in fenced code blocks.

        Args:
            content: Raw execution text to mirror into the logs channel.

        Returns:
            None
        """

        if self._logs_channel_id is None:
            print("[DiscordBot] Logs channel is unavailable")
            return

        for message_chunk in self._build_logs_messages(content = content):
            await self.send_channel_message(
                channel_id = self._logs_channel_id,
                content = message_chunk
            )

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

    def _build_logs_messages(self, content:str) -> list[str]:
        """Wrap raw log text into Discord-safe fenced code blocks.

        Args:
            content: Raw execution text to mirror into the logs channel.

        Returns:
            list[str]: One or more Discord-ready code block messages.
        """

        fence_prefix = "```json\n"
        fence_suffix = "\n```"
        max_content_length = DISCORD_MESSAGE_CHARACTER_LIMIT - len(fence_prefix) - len(fence_suffix)

        if max_content_length <= 0:
            return [f"{fence_prefix}{content}{fence_suffix}"]

        if not content:
            return [f"{fence_prefix}{fence_suffix}"]

        return [
            f"{fence_prefix}{message_chunk}{fence_suffix}"
            for message_chunk in split_discord_message(
                content = content,
                max_length = max_content_length
            )
        ]

    async def send_guild_channel_message(
        self,
        guild_id:int,
        category_name:DiscordChannelCategory,
        channel_name:str,
        content:str
    ) -> None:
        """Send a Discord message to a named guild text channel.

        Args:
            guild_id: Discord guild id that owns the target channel.
            category_name: Target category name.
            channel_name: Target text channel name.
            content: User-facing message content.

        Returns:
            None
        """

        channel = await self.get_or_create_guild_text_channel(
            guild_id = guild_id,
            category_name = category_name,
            channel_name = channel_name
        )
        if channel is None:
            print(
                "[DiscordBot] Guild channel is unavailable "
                f"guild_id={guild_id} category_name={category_name} channel_name={channel_name}"
            )
            return

        print(
            "[DiscordBot] Sending guild channel message "
            f"guild_id={guild_id} category_name={category_name} channel_name={channel_name} channel_id={channel.id}"
        )
        await self.send_channel_message(
            channel_id = channel.id,
            content = content
        )

    async def get_or_create_guild_text_channel(
        self,
        guild_id:int,
        category_name:DiscordChannelCategory,
        channel_name:str
    ) -> discord.TextChannel | None:
        """Find or create a text channel inside a supported Discord category.

        Args:
            guild_id: Discord guild id that should own the channel.
            category_name: Target category enum.
            channel_name: Target text channel name.

        Returns:
            discord.TextChannel | None: Existing or newly created text channel.
        """

        if guild_id == 0:
            print(
                "[DiscordBot] Skipping text channel lookup because GUILD_ID is not configured "
                f"channel_name={channel_name}"
            )
            return None

        guild = await self._fetch_guild(guild_id = guild_id)
        if guild is None:
            return None

        guild_channels = await self._fetch_guild_channels(guild = guild)
        if guild_channels is None:
            return None

        category_channel = await self._get_or_create_category_channel(
            guild = guild,
            guild_channels = guild_channels,
            category_name = category_name
        )
        if category_channel is None:
            return None

        for guild_channel in guild_channels:
            if (
                isinstance(guild_channel, discord.TextChannel)
                and guild_channel.name == channel_name
                and guild_channel.category_id == category_channel.id
            ):
                print(
                    "[DiscordBot] Found existing text channel "
                    f"guild_id={guild_id} category_name={category_name} "
                    f"channel_name={channel_name} channel_id={guild_channel.id}"
                )
                return guild_channel

        print(
            "[DiscordBot] Creating missing text channel "
            f"guild_id={guild_id} category_name={category_name} channel_name={channel_name}"
        )
        try:
            return await guild.create_text_channel(
                channel_name,
                category = category_channel,
                reason = f"Auto-created by Discord assistant for {category_name}/{channel_name}"
            )
        except discord.Forbidden as exc:
            print(
                "[DiscordBot] Missing permission to create channel. "
                f"The bot needs Manage Channels for {category_name}/{channel_name} in guild {guild_id}: {exc}"
            )
            return None
        except discord.HTTPException as exc:
            print(
                "[DiscordBot] Failed to create channel "
                f"{category_name}/{channel_name} in guild {guild_id}: {exc}"
            )
            return None

    async def _fetch_guild(self, guild_id:int) -> discord.Guild | None:
        """Fetch a Discord guild from cache or API.

        Args:
            guild_id: Discord guild id.

        Returns:
            discord.Guild | None: Guild object when available.
        """

        guild = self.get_guild(guild_id)
        if guild is not None:
            return guild

        print(f"[DiscordBot] Guild {guild_id} was not found in cache, fetching from API")
        try:
            return await self.fetch_guild(guild_id)
        except discord.HTTPException as exc:
            print(f"[DiscordBot] Failed to fetch guild {guild_id}: {exc}")
            return None

    async def _fetch_guild_channels(
        self,
        guild:discord.Guild
    ) -> list[discord.abc.GuildChannel] | None:
        """Fetch the full channel list for a guild.

        Args:
            guild: Discord guild object.

        Returns:
            list[discord.abc.GuildChannel] | None: Guild channels when available.
        """

        try:
            return await guild.fetch_channels()
        except discord.Forbidden as exc:
            print(
                "[DiscordBot] Missing permission to fetch guild channels. "
                f"The bot needs View Channels in guild {guild.id}: {exc}"
            )
            return None
        except discord.HTTPException as exc:
            print(f"[DiscordBot] Failed to fetch channels for guild {guild.id}: {exc}")
            return None

    async def _get_or_create_category_channel(
        self,
        guild:discord.Guild,
        guild_channels:list[discord.abc.GuildChannel],
        category_name:DiscordChannelCategory
    ) -> discord.CategoryChannel | None:
        """Find or create a supported category channel in a guild.

        Args:
            guild: Discord guild object.
            guild_channels: Current guild channels.
            category_name: Target category enum.

        Returns:
            discord.CategoryChannel | None: Existing or newly created category.
        """

        for guild_channel in guild_channels:
            if isinstance(guild_channel, discord.CategoryChannel) and guild_channel.name == category_name.value:
                print(
                    "[DiscordBot] Found existing category "
                    f"guild_id={guild.id} category_name={category_name} category_id={guild_channel.id}"
                )
                return guild_channel

        print(
            "[DiscordBot] Creating missing category "
            f"guild_id={guild.id} category_name={category_name}"
        )
        try:
            return await guild.create_category(
                category_name.value,
                reason = f"Auto-created by Discord assistant for {category_name}"
            )
        except discord.Forbidden as exc:
            print(
                "[DiscordBot] Missing permission to create category. "
                f"The bot needs Manage Channels for {category_name} in guild {guild.id}: {exc}"
            )
            return None
        except discord.HTTPException as exc:
            print(
                "[DiscordBot] Failed to create category "
                f"{category_name} in guild {guild.id}: {exc}"
            )
            return None

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
            limit = MAX_HISTORY_MESSAGES,
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












