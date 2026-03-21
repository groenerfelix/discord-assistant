"""Entry point for the markdown-driven Discord assistant."""

# NOTE: these two have to be the first imports
from __future__ import annotations
from app.config import load_config

from app.agent import MarkdownAgent
from app.discord_bot import AssistantDiscordClient



def main():
    """Run the Discord bot application.

    Args:
        None

    Returns:
        None
    """

    config = load_config()
    print("[App] Configuration loaded")

    if not config.discord_token:
        raise RuntimeError("DISCORD is required")
    if not config.OPENAI:
        raise RuntimeError("OPENAI is required")

    agent = MarkdownAgent(config = config)
    client = AssistantDiscordClient(agent = agent)
    print("[App] Starting Discord client")
    client.run(config.discord_token)


if __name__ == "__main__":
    main()
