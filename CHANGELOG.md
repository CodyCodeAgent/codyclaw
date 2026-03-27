# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Setup wizard**: First-run guided configuration via web UI — no manual YAML editing required
  - Auto-detects missing config, starts in setup-only mode
  - Step-by-step form with Chinese guidance text and operation hints
  - "Test Connection" button validates Feishu credentials before saving
  - Auto-restart after save (user never touches the terminal)
  - Model dropdown with descriptions (Sonnet/Opus/Haiku) + custom input
- **Web management console** at `http://localhost:8080/` with 8 pages:
  Dashboard, Chat, Agents, Skills, Cron Tasks, Sessions, Config, Events
- Real-time chat with agents via SSE streaming in web console
  - Welcome message with clickable suggestion chips
  - Auto-approves InteractionRequestChunk (web users are admins)
- Live event stream (agent runs, cron executions) via SSE
- Dashboard status alerts: "Feishu Connected" / "Disconnected" with guidance
- Config Quick Edit: edit API Key, Model, gateway settings from web UI
  - `PUT /api/config/quick` endpoint for sensitive field updates
- Feishu connection status in sidebar (real-time green/red indicator)
- Per-agent `api_key` and `base_url` fields for third-party LLM providers
- Global `enable_thinking` / `thinking_budget` configuration for Cody SDK
- Chat history persistence to SQLite (`chat_messages` table)
- New API endpoints: `/api/dashboard`, `/api/skills`, `/api/config`,
  `/api/config/quick`, `/api/chat/send`, `/api/chat/history`,
  `/api/events/stream`, `/api/setup/status`, `/api/setup/save`,
  `/api/setup/test-lark`
- Open-source project files: LICENSE (Apache 2.0), CONTRIBUTING.md,
  CHANGELOG.md, CODE_OF_CONDUCT.md, SECURITY.md
- GitHub Actions CI (lint + test on Python 3.10-3.12)
- Issue/PR templates for bug reports, feature requests
- Dockerfile with non-root user, healthcheck, volume mount
- docker-compose.yml for one-command deployment
- Makefile with common dev commands (install, dev, lint, test, check, docker)
- .editorconfig for consistent formatting

### Fixed
- `config.py`: `db_path` from YAML was silently ignored (always used default)
- `config.py`: `load_config` crashed with `FileNotFoundError` when no config
  exists — now returns empty defaults and enters setup mode
- `router.py`: Default agent did not check `trigger_mode` for group messages
- `dispatcher.py`: `_user_pending` not cleaned up on interaction timeout
- `cron.py`: `_parse_interval` crashed on malformed interval strings
- `lark_impl.py`: `update_card` silently ignored API failures
- `cards.py`: Content truncation at 4096 chars was silent (now shows indicator)
- `web/api.py`: Flask-style `return dict, 404` pattern (FastAPI ignores status)
- `web/api.py`: Chat history used `list.pop(0)` O(n) — replaced with `deque`
- `web/api.py`: Web chat hung on `InteractionRequestChunk` (now auto-approves)
- `web/api.py`: `load_chat_messages()` was dead code (now used as DB fallback)
- `app.js`: Config page `input` listener registered on every visit (leak)
- `app.js`: Config "Save" button was non-functional (just showed alert)
- `app.js`: Unescaped `agent_id` in HTML attribute (potential injection)

### Changed
- `download_resource` parameter renamed from `type` to `resource_type`
- `load_config` returns `(CodyClawConfig, str)` tuple instead of just config
- Added `TYPE_CHECKING` imports to `boot.py`, `cron.py`, `lark_impl.py`, `router.py`
- All import blocks sorted per ruff isort rules
- Removed unused imports across codebase
- Added public methods to `AgentDispatcher`: `get_session()`, `set_session()`,
  `active_run_count` property (replaced private attribute access)

## [0.1.0] - 2025-03-01

### Added
- Initial release
- Feishu WebSocket integration via lark-oapi SDK
- Multi-agent support with per-user/per-group routing
- Streaming output with real-time Feishu card updates
- Human-in-the-loop approval via message replies
- Cron task scheduling (static from config + AI-created dynamic tasks)
- Event bus for system-wide monitoring
- BOOT.md startup script execution
- Skills: `feishu-notify`, `cron-manager`
- Session management with 24-hour idle timeout
- Message deduplication (1-hour window, 10K capacity)
- Management API: `/health`, `/api/agents`, `/api/cron`, `/api/sessions`
