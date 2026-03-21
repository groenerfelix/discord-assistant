"""Discord integration for the markdown-driven assistant."""

from __future__ import annotations
import os

import discord

from app.agent import MarkdownAgent


class AssistantDiscordClient(discord.Client):
    """Discord client that runs the markdown agent on direct messages."""

    def __init__(self, agent:MarkdownAgent):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents = intents)
        self._agent = agent

    async def on_ready(self):
        print(f"[DiscordBot] Logged in as {self.user}")

    async def on_message(self, message:discord.Message):
        if message.author == self.user:
            return

        if message.author.id != int(os.getenv("DISCORD_ID", "0")):
            return

        if message.guild is not None:
            # TODO: we will have future workflows that operate in servers, but for now we only want to respond to DMs
            return

        content = message.content.strip()
        if not content:
            # await message.channel.send("Please send a text request so I can start a workflow.")
            return

        print(f"[DiscordBot] Received DM from {message.author}: {content}")
        await message.add_reaction("🤔")
        response = self._agent.run_dm_workflow(user_message = content)
        await message.remove_reaction("🤔", self.user)
        await message.add_reaction("✅")
        print(f"[DiscordBot] Sending DM response after {response.steps_used} steps")
        await message.channel.send(response.message)
