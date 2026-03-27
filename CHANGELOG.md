# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Web management console at `http://localhost:8080/` with Dashboard, Chat, Agents, Skills, Cron Tasks, Sessions, Config, and Events pages
- Real-time chat with agents via SSE streaming in web console
- Live event stream (agent runs, cron executions) in web console
- Per-agent `api_key` and `base_url` fields for third-party LLM providers (DeepSeek, Qwen, GLM, etc.)
- Global `enable_thinking` / `thinking_budget` configuration for Cody SDK
- Chat history persistence to SQLite (`chat_messages` table)
- `GET /api/skills` endpoint to list all skill packages
- `GET /api/dashboard` endpoint for overview stats
- `POST /api/chat/send` SSE endpoint for web chat
- `GET /api/events/stream` SSE endpoint for live event monitoring

### Fixed
- `config.py`: `db_path` from YAML was silently ignored (always used default)
- `router.py`: Default agent did not check `trigger_mode` for group messages
- `dispatcher.py`: `_user_pending` not cleaned up on interaction timeout
- `cron.py`: `_parse_interval` crashed on malformed interval strings (e.g., "abc3h")
- `lark_impl.py`: `update_card` silently ignored API failures
- `cards.py`: Content truncation at 4096 chars was silent (now shows indicator)

### Changed
- `download_resource` parameter renamed from `type` to `resource_type` (was shadowing Python builtin)
- All import blocks sorted per ruff isort rules
- Added `TYPE_CHECKING` imports to `boot.py`, `cron.py`, `lark_impl.py`, `router.py`

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
