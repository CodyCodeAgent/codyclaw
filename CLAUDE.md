# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

CodyClaw is a Python gateway that connects Cody AI Agents to Feishu (Lark) via WebSocket. Users interact with AI agents through Feishu messages, group chats, and scheduled cron tasks. AI agents can dynamically create cron tasks via the `cron-manager` skill. A built-in Web console provides real-time chat, configuration management, and monitoring.

## Commands

```bash
# Setup
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Run (first run auto-enters setup wizard if no config exists)
codyclaw
# or: python -m codyclaw.main

# Lint
ruff check codyclaw/
# or: make lint

# Auto-fix lint issues
ruff check codyclaw/ --fix
# or: make lint-fix

# Run all tests
pytest tests/ -v
# or: make test

# Run a single test file or function
pytest tests/test_router.py -v
pytest tests/test_router.py::test_default_agent_resolves_p2p -v

# Lint + tests together
make check
```

## Architecture

Six-layer design, all async (asyncio):

1. **Channel Layer** (`codyclaw/channel/`) — Feishu WebSocket adapter. Lark SDK runs in a separate thread, bridged to asyncio via `run_coroutine_threadsafe()`. `cards.py` builds Feishu interactive cards for streaming agent output (running/done/error states, 4096-char content limit with truncation).

2. **Gateway Layer** (`codyclaw/gateway/`) — Message routing and agent dispatch.
   - `router.py`: Routes messages to agents based on chat type, user/group whitelists, and trigger mode (mention/all/prefix). `AgentConfig` supports per-agent `api_key` and `base_url` for third-party LLM providers.
   - `dispatcher.py`: Executes agents via Cody SDK streaming. Manages human-in-the-loop (message-based: user replies 允许/拒绝/全部允许) and throttles card updates (1.5s interval). Registers custom tools and skill directory when building each Cody client. Applies global and per-agent `api_key`/`base_url`/`enable_thinking` config.
   - `session_strategy.py`: Per-user (P2P) or per-group session persistence with 24-hour idle timeout.
   - `tools.py`: Custom tool factory (`make_cron_tools`) — closures over `CronScheduler` for create/list/delete operations.
   - `user_memory.py`: File-based per-user persistent memory (`UserMemoryStore`). Stored at `~/.codyclaw/users/{user_id}.json`, capped at 100 entries (FIFO eviction). Injected into message context as a `[User profile]` block (≤1500 token budget). Crosses agents, groups, and sessions.

3. **Automation Layer** (`codyclaw/automation/`) — Cron scheduler (APScheduler 3.x), pub/sub event bus, and BOOT.md startup script execution.

4. **Skills** (`codyclaw/skills/`) — SKILL.md packages loaded by Cody SDK. Each skill guides the AI on when and how to use tools. Current skills: `feishu-notify`, `cron-manager`.

5. **Web Layer** (`codyclaw/web/`) — Web management console (vanilla HTML/CSS/JS SPA).
   - `api.py`: FastAPI router with endpoints for setup wizard, real-time chat (SSE), config management, skills listing, event streaming, and dashboard stats.
   - `static/`: Frontend SPA served at `/` with sidebar navigation.

6. **Database** (`codyclaw/db.py`) — SQLite via Python's built-in `sqlite3`. Two tables:
   - `cron_tasks`: AI-created cron tasks (persisted for restart recovery).
   - `chat_messages`: Web chat history.
   - Cody's own DB (sessions, memory) is stored per-agent under `~/.codyclaw/agents/{agent_id}/cody.db`.

**Entry point**: `main.py` detects configuration state:
- **No config / incomplete config** → starts in **setup mode** (lightweight web-only, serves setup wizard at `/`).
- **Config valid** → starts in **normal mode**: DB → channel → router → event bus → dispatcher → cron → FastAPI + uvicorn.

**Config**: Loaded from `~/.codyclaw/config.yaml` via `load_config()` which returns `(CodyClawConfig, config_path)`. Supports `${ENV_VAR}` expansion. `is_configured()` checks if lark credentials and at least one agent are present. `db_path` defaults to `~/.codyclaw/codyclaw.db`.

## Key Patterns

- **Setup mode**: When `is_configured()` returns False, `main()` creates a setup-only FastAPI app (no Feishu connection). The setup wizard saves config via `POST /api/setup/save` and auto-restarts the process with `os.execv()`.
- **Human-in-the-loop**: Message-based (no card callbacks needed). Dispatcher holds `_user_pending: dict[str, str]` (user_id → request_id). `handle_message` intercepts 允许/拒绝/全部允许 before dispatching to agent.
- **Cron persistence**: Static tasks from config.yaml are not persisted to DB (restored from config on restart). AI-created tasks (`persist=True`) are written to DB and reloaded on startup.
- **Cody client setup**: Each agent gets its own `AsyncCodyClient` with skill_dir, custom tools, db_path, api_key, and base_url configured. Built lazily with double-checked locking (`asyncio.Lock`).
- **Message deduplication**: LRU OrderedDict (1-hour window, 10K cap).
- **Web chat**: SSE streaming via `POST /api/chat/send`. Auto-approves `InteractionRequestChunk` (web users are admins). Chat history persisted to `chat_messages` table.
- **Lark SDK event loop workaround**: `lark_oapi.ws.client` captures the event loop into a module-level variable at import time. If imported inside uvicorn's async context, it grabs the running loop and causes `'event loop is already running'`. Fix in `lark_impl.py._run_ws_in_thread()`: create a new event loop in the thread and patch `ws_module.loop` before constructing `lark.ws.Client`.

## Code Style

- Ruff rules: E, F, I (isort) with 100-char line length
- Python 3.10+ (dataclasses, type hints, asyncio)
- Use `Optional[str]` consistently (not `str | None`)
- Tests use pytest-asyncio (`asyncio_mode = "auto"` — no `@pytest.mark.asyncio` decorator needed) and monkeypatch for time-dependent tests
