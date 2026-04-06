"""Runtime configuration helpers."""

# NOTE: these two have to be the first imports
from __future__ import annotations
import os
if os.path.exists(".env"):
    from dotenv import load_dotenv
    load_dotenv()


from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen = True)
class AppConfig:
    """Container for runtime configuration values.

    Args:
        project_root: Root directory of the project.
        discord_token: Bot token for Discord.
        OPENAI: API key for the configured LLM provider.
        openai_model: Model name for the chat completion API.
        openai_base_url: Optional base URL for OpenAI-compatible providers.
        max_agent_steps: Maximum number of tool iterations per workflow run.

    Returns:
        AppConfig: Immutable configuration object.
    """

    project_root:Path
    discord_token:str
    OPENAI:str
    openai_model:str
    openai_base_url:str | None
    max_agent_steps:int


def load_config() -> AppConfig:
    """Load runtime configuration from environment variables.

    Args:
        None

    Returns:
        AppConfig: Loaded application configuration.
    """

    project_root = Path(__file__).resolve().parent.parent

    return AppConfig(
        project_root = project_root,
        discord_token = os.getenv("DISCORD_2", ""),
        OPENAI = os.getenv("OPENAI", ""),
        openai_model = os.getenv("LLM_MODEL", "gpt-5-mini"),
        openai_base_url = os.getenv("LLM_API_BASE_URL", None),
        max_agent_steps = int(os.getenv("AGENT_MAX_STEPS", "8"))
    )
