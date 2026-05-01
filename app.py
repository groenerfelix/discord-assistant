"""Entry point for the markdown-driven Discord assistant."""

# NOTE: these two have to be the first imports
from __future__ import annotations
from app.config import load_config

from app.agent import MarkdownAgent
from app.discord_bot import AssistantDiscordClient


def main():

    config = load_config()

    if not config.discord_token:
        raise RuntimeError("DISCORD_2 or DISCORD is required")
    if not config.agent_llm.api_key:
        raise RuntimeError("OPENAI_API_KEY, LLM_API_KEY, or OPENAI is required for the core agent")

    agent = MarkdownAgent(config = config)
    client = AssistantDiscordClient(
        agent = agent,
        config = config
    )

    client.run(config.discord_token)


if __name__ == "__main__":
    main()
