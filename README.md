# Markdown-Driven Discord Assistant

This repository contains a minimal prototype for a self-evolving assistant that is configured through markdown workflows and code-defined tools.

## Structure

- `app/`: Python runtime code
- `prompts/`: Core agent instructions
- `workflows/`: Markdown workflow definitions
- `tools/`: Code-based tool implementations grouped by concern
- `data/`: Data storage

## Environment

Set these environment variables before running:

- `DISCORD` bot token
- `DISCORD_ADMIN_ID` single, whitelisted user id
- `OPENAI` OpenAI API key used by the web search tool
- `LLM_API_KEY` optional core agent API key, falls back to `OPENAI`
- `LLM_MODEL` optional core agent model, defaults to `gpt-5-mini`
- `LLM_API_BASE_URL` optional base URL for the core agent's OpenAI-compatible provider
- `AGENT_MAX_STEPS` optional, defaults to `5`

## Run

```powershell
pip install -r requirements.txt
python -m app.app
```

## Current scope

- Direct messages trigger the core workflow loop
- The agent is instructed by markdown prompts and workflows
- Tools are registered with strict function schemas and executed through OpenAI tool calling
- Workflow/data tools are constrained by filename and semantic area instead of arbitrary paths
- The first workflow manages `data/todo.md`
- The loop ends only when the model calls `send_message` or the hard step limit is reached
