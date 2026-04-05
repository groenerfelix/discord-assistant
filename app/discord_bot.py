"""Discord integration for the markdown-driven assistant."""

from __future__ import annotations
import os
from datetime import datetime, timedelta, timezone, time

import discord
from discord.ext import tasks

from app.agent import MarkdownAgent


MAX_HISTORY_HOURS = 50
MAX_HISTORY_MESSAGES = 10
MAX_HISTORY_TOKENS = 10000
HISTORY_FETCH_LIMIT = 50


class AssistantDiscordClient(discord.Client):
    """Discord client that runs the markdown agent on direct messages."""

    def __init__(self, agent:MarkdownAgent):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents = intents)
        self._agent = agent
        self._allowed_user_id = int(os.getenv("DISCORD_ID", "0"))

    @tasks.loop(time = time(hour = 6, tzinfo = timezone(timedelta(hours = -7))))
    async def daily_routine(self) -> None:
        pass

    async def on_ready(self) -> None:
        print(f"[DiscordBot] Logged in as {self.user}")

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
            # await message.channel.send("Please send a text request so I can start a workflow.")
            return

        print(f"[DiscordBot] Received DM from {message.author}: {content}")
        recent_channel_history = await self._build_recent_channel_history(message = message)
        await message.add_reaction("\N{THINKING FACE}")
        response = self._agent.run_dm_workflow(
            user_message = content,
            recent_channel_history = recent_channel_history
        )
        await message.remove_reaction("\N{THINKING FACE}", self.user)
        await message.add_reaction("\N{WHITE HEAVY CHECK MARK}")
        print(f"[DiscordBot] Sending DM response after {response.steps_used} steps")
        await message.channel.send(response.message)

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