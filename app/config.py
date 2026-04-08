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
class LlmClientConfig:
    """Container for one LLM client configuration.

    Args:
        api_key: API key used for requests.
        model: Model name used for requests.
        base_url: Optional base URL for OpenAI-compatible providers.

    Returns:
        LlmClientConfig: Immutable LLM client configuration.
    """

    api_key:str
    model:str
    base_url:str | None


@dataclass(frozen = True)
class AppConfig:
    """Container for runtime configuration values.

    Args:
        project_root: Root directory of the project.
        discord_token: Bot token for Discord.
        guild_id: Discord guild id used for server-side bot channels.
        agent_llm: Core agent LLM configuration loaded from environment variables.
        openai_api_key: OpenAI API key reserved for OpenAI-native tools.
        max_agent_steps: Maximum number of tool iterations per workflow run.

    Returns:
        AppConfig: Immutable configuration object.
    """

    project_root:Path
    discord_token:str
    guild_id:int
    agent_llm:LlmClientConfig
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
        discord_token = os.getenv("DISCORD_2", os.getenv("DISCORD", "")),
        guild_id = int(os.getenv("GUILD_ID", "0")),
        agent_llm = LlmClientConfig(
            api_key = os.getenv("LLM_API_KEY", os.getenv("OPENAI", "")),
            model = os.getenv("LLM_MODEL", "gpt-5-mini"),
            base_url = os.getenv("LLM_API_BASE_URL", None)
        ),
        max_agent_steps = int(os.getenv("AGENT_MAX_STEPS", "8"))
    )
