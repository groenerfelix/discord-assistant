# Markdown-Driven Discord Assistant

This project is an autonomous Discord assistant whose "brain" lives mostly in markdown.


## Features

### Markdown-driven agent with minimal code

Instead of baking behaviour into application code, most logic and data lives in markdown files. The agent can read and write workflows and data. This makes the assistant easy to evolve: Simply prompt it to add new behaviours! The Python runtime stays focused on orchestration, Discord integration, and safe tool execution.

- The agent creates and edits **worklow and data files** with read/write tools.
- Restricted prompt components (e.g., **personality**) are injected in every system prompt.
- **Tool calling** allows the agent to read emails, search the web, and send discord messages.

### Discord interface

This agent interfaces with discord to receive instructions, send responses, and make the agent state transparent. Interaction primary happens in direct messages but backend states are published in a discord server.

At runtime, the bot listens for direct messages from one allowed Discord user, turns each message into a workflow run, and lets the model work through the request with strict tool calls.

Current capabilities include:

- Discord reactions indicate progress (⏳ -> 🤔 -> ✅/❌)
- Updates to workflows and data are sent in discord channels that are automatically created for each markdown file and organised in categories.
- All message history is put into a dedicated logs channel.
- Workflow can be scheduled by adding a discord task routine that queues an automated message. 


## Repository Structure

### Runtime code

- `app.py`: main entrypoint that loads config, builds the agent, and starts the Discord client.
- `app/agent.py`: the core workflow loop, queue handling, prompt assembly, Agents SDK execution, and transcript logging.
- `app/discord_bot.py`: Discord client, DM intake, reactions, channel creation, history gathering, and scheduled jobs.
- `app/content_processor.py`: reusable instruction-driven content processor used by model-backed tools.
- `app/tool_registry.py`: registers local tools and wraps them as OpenAI Agents SDK function tools.
- `app/config.py`: environment loading and runtime configuration.
- `app/discord_utils.py`, `app/markdown_loader.py`, `app/util.py`: shared helpers.

### Markdown-driven behaviour

- `prompts/core.md`: global operating instructions for the assistant.
- `prompts/persona.md`: optional personality and response-style prompt.
- `prompts/memories.md`: durable memory bullets the assistant can append to.
- `workflows/`: reusable markdown workflows that define how a task should be handled.
- `data/`: markdown files that act as the assistant's editable state.

Current examples in the repo include calendar and to-do workflows, plus markdown data files such as `data/todo.md`, `data/calendar.md`, and `data/gifs.md`.

### Tools

- `tools/markdown_tools.py`: constrained read/write access for markdown workflows, data, and memories.
- `tools/messaging_tools.py`: lets the model send Discord replies and optionally end the workflow.
- Hosted web search is attached directly to the main OpenAI Agents SDK agent.
- `tools/email_tools.py`: Email integration to retrieve the last 24hr inbox and process it through explicit instructions supplied by the main agent.


## Setup

### 1. Create a Python environment and install dependencies

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

If you want the app to load variables from a local `.env` file, also install `python-dotenv`:

```powershell
pip install python-dotenv
```

### 2. Configure environment variables

Discord integration
- `DISCORD`: Discord bot token with the appropriate scope.
- `DISCORD_ADMIN_ID`: the only user ID whose DMs will be processed.
- `DISCORD_ADMIN_DM_CHANNEL_ID`: DM channel used by the scheduled daily routine.
- `GUILD_ID`: Discord server ID used for mirrored channels.

LLM agent
- `OPENAI_API_KEY`: OpenAI API key for the main agent and model-backed tools. `LLM_API_KEY` or `OPENAI` are accepted as fallbacks.
- `OPENAI_BASE_URL` or `LLM_API_BASE_URL`: optional base URL for an OpenAI-compatible endpoint.
- `LLM_MODEL`: model name for the main agent.
- `AGENT_MAX_STEPS`: hard limit for workflow turns.
- `TIMEZONE`: timezone string used in the generated prompt context.

Hosted web search is attached through the OpenAI Agents SDK and requires a model/provider path that supports the hosted `WebSearchTool`.

Zoho email integration 
- `ZOHO_ID` and `ZOHO_TOKEN` of the application, 
- `ZOHO_MAIL_ACCESS` or `ZOHO_MAIL_REFRESH` or `ZOHO_MAIL_GRANT` for the permission token with read mail scope,
- `ZOHO_ACC_ID` and `ZOHO_FOLDER_ID` of the inbox user, 
- `ZOHO_SENDER_WHITELIST`, a comma-separated list of allowed senders

### 3. Prepare your markdown assets

Before running the bot, review:

- review `prompts/core.md` for global operating rules
- create `prompts/persona.md` for tone/personality/character

This is the main design surface of the project. Most behaviour changes should happen here, not in Python.

### 5. Run the assistant

```powershell
python app.py
```

## Notes

- The bot currently processes direct messages only.
- Only one allowed user is supported out of the box.
- Markdown writes are full rewrites, not partial patches.
- The assistant keeps recent conversation context for a channel and resets retained history after 30 minutes of inactivity or when the channel changes.
- Raw execution payloads are mirrored to Discord for debugging, so use this carefully with private data.
