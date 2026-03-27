# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

CodyClaw is a Python gateway that connects Cody AI Agents to Feishu (Lark) via WebSocket. Users interact with AI agents through Feishu messages, group chats, and scheduled cron tasks. AI agents can dynamically create cron tasks via the `cron-manager` skill.

## Commands

```bash
# Setup
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Run (config loaded from ~/.codyclaw/config.yaml)
export ANTHROPIC_API_KEY="sk-ant-..."
codyclaw
# or: python -m codyclaw.main

# Lint
ruff check codyclaw/

# Run all tests
pytest tests/ -v

# Run a single test
pytest tests/test_router.py::test_function_name -v
```

## Architecture

Four-layer design, all async (asyncio):

1. **Channel Layer** (`codyclaw/channel/`) — Feishu WebSocket adapter. Lark SDK runs in a separate thread, bridged to asyncio via `run_coroutine_threadsafe()`.

2. **Gateway Layer** (`codyclaw/gateway/`) — Message routing and agent dispatch.
   - `router.py`: Routes messages to agents based on chat type, user/group whitelists, and trigger mode (mention/all/prefix).
   - `dispatcher.py`: Executes agents via Cody SDK streaming. Manages human-in-the-loop (message-based: user replies 允许/拒绝/全部允许) and throttles card updates (1.5s interval). Registers custom tools and skill directory when building each Cody client.
   - `session_strategy.py`: Per-user (P2P) or per-group session persistence with 24-hour idle timeout.
   - `tools.py`: Custom tool factory (`make_cron_tools`) — closures over `CronScheduler` for create/list/delete operations.

3. **Automation Layer** (`codyclaw/automation/`) — Cron scheduler (APScheduler 3.x), pub/sub event bus, and BOOT.md startup script execution.

4. **Skills** (`codyclaw/skills/`) — SKILL.md packages loaded by Cody SDK. Each skill guides the AI on when and how to use tools. Current skills: `feishu-notify`, `cron-manager`.

5. **Database** (`codyclaw/db.py`) — SQLite via Python's built-in `sqlite3`. Stores AI-created cron tasks. Cody's own DB (sessions, memory) is stored per-agent under `~/.codyclaw/agents/{agent_id}/cody.db`.

**Entry point**: `main.py` initializes DB → channel → router → event bus → dispatcher → cron → starts FastAPI + uvicorn.

**Config**: Loaded from `~/.codyclaw/config.yaml`. Supports `${ENV_VAR}` expansion. `db_path` defaults to `~/.codyclaw/codyclaw.db`.

## Key Patterns

- **Human-in-the-loop**: Message-based (no card callbacks needed). Dispatcher holds `_user_pending: dict[str, str]` (user_id → request_id). `handle_message` intercepts 允许/拒绝/全部允许 before dispatching to agent.
- **Cron persistence**: Static tasks from config.yaml are not persisted to DB (restored from config on restart). AI-created tasks (`persist=True`) are written to DB and reloaded on startup.
- **Cody client setup**: Each agent gets its own `AsyncCodyClient` with skill_dir, custom tools, and db_path configured. Built lazily with double-checked locking (`asyncio.Lock`).
- **Message deduplication**: LRU OrderedDict (1-hour window, 10K cap).

## Code Style

- Ruff rules: E, F, I (isort) with 100-char line length
- Python 3.10+ (dataclasses, type hints, asyncio)
- Tests use pytest-asyncio and monkeypatch for time-dependent tests
