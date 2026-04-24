# Telecodex Project Guide

## Overview

`telecodex` is a Telegram bot wrapper around the official OpenAI Codex CLI.

The bot solves four main problems:

- keeps a stable project context per user
- persists Codex session state per project
- exposes Codex through Telegram text, document, voice, and image inputs
- gives operational control over long-running Codex runs

At runtime the bot polls Telegram, routes each update to the correct flow, starts `codex exec --json`, streams progress back to Telegram, and stores session/run metadata in SQLite.

## Runtime Model

The process entrypoint is [`main.py`](/opt/telecodex/src/codex_telegram_bot/main.py).

Startup sequence:

1. Load `.env` through `Settings`.
2. Configure structured logging.
3. Initialize SQLite and run schema migrations.
4. Finalize orphaned runs left from previous restarts.
5. Validate that Codex CLI is available.
6. Build the Telegram application and start polling.

Shutdown sequence:

1. Stop Telegram polling.
2. Stop and shutdown the PTB application.
3. Close the SQLite store.

## Main Components

### Entry and App Assembly

- [`main.py`](/opt/telecodex/src/codex_telegram_bot/main.py): process entrypoint, startup/shutdown orchestration
- [`bot.py`](/opt/telecodex/src/codex_telegram_bot/bot.py): wires settings, services, flows, handlers, and Telegram commands

### Configuration

- [`config.py`](/opt/telecodex/src/codex_telegram_bot/config.py): parses `.env`, validates settings, exposes typed runtime config

Important operational settings:

- Telegram: token, username, allowed users
- Workspace: approved directory, SQLite path
- Codex: CLI path, model, reasoning effort, timeout, launch mode
- UX: verbose level, status line, active-run limit
- Inputs: files, voice, images

### Flows

- [`flows/navigation.py`](/opt/telecodex/src/codex_telegram_bot/flows/navigation.py): menu screens, repo selection, session selection, workspace screens
- [`flows/execution.py`](/opt/telecodex/src/codex_telegram_bot/flows/execution.py): request execution, progress updates, stop handling, result persistence

Responsibilities are split intentionally:

- navigation flow changes user-visible state
- execution flow owns active run state and Codex lifecycle

### Handlers

- [`handlers/commands.py`](/opt/telecodex/src/codex_telegram_bot/handlers/commands.py): slash commands
- [`handlers/callbacks.py`](/opt/telecodex/src/codex_telegram_bot/handlers/callbacks.py): inline keyboard callbacks
- [`handlers/messages.py`](/opt/telecodex/src/codex_telegram_bot/handlers/messages.py): text, document, voice, photo input
- [`handlers/errors.py`](/opt/telecodex/src/codex_telegram_bot/handlers/errors.py): Telegram-side error handler

### Services

- [`services/projects.py`](/opt/telecodex/src/codex_telegram_bot/services/projects.py): project discovery, validation, auto-create, remembering current/recent projects
- [`services/observability.py`](/opt/telecodex/src/codex_telegram_bot/services/observability.py): structured logs and audit log writes
- [`services/status_line.py`](/opt/telecodex/src/codex_telegram_bot/services/status_line.py): bottom status line rendering, quota parsing, local fallback

### Codex Integration

- [`codex_runner.py`](/opt/telecodex/src/codex_telegram_bot/codex_runner.py): launches Codex CLI, normalizes JSON stream events, supports resume fallback, discovers local sessions
- [`processes.py`](/opt/telecodex/src/codex_telegram_bot/processes.py): subprocess process-group isolation and full process-tree termination

### Telegram UI

- [`telegram/ui/texts.py`](/opt/telecodex/src/codex_telegram_bot/telegram/ui/texts.py): all screen text and final-response wrappers
- [`telegram/ui/keyboards.py`](/opt/telecodex/src/codex_telegram_bot/telegram/ui/keyboards.py): inline keyboard layouts
- [`telegram/ui/responder.py`](/opt/telecodex/src/codex_telegram_bot/telegram/ui/responder.py): robust Telegram sending/editing
- [`telegram_formatting.py`](/opt/telecodex/src/codex_telegram_bot/telegram_formatting.py): Markdown-to-HTML handling and message chunking

### Inputs

- [`telegram/inputs.py`](/opt/telecodex/src/codex_telegram_bot/telegram/inputs.py): prepares Codex requests from Telegram messages
- [`voice.py`](/opt/telecodex/src/codex_telegram_bot/voice.py): voice transcription through OpenAI or OpenAI-compatible API

### Persistence and CLI Tools

- [`session_store.py`](/opt/telecodex/src/codex_telegram_bot/session_store.py): SQLite schema, migrations, sessions, runs, preferences, audit
- [`workspace_cli.py`](/opt/telecodex/src/codex_telegram_bot/workspace_cli.py): local operator CLI for inspecting runs and switching project state

## Telegram UX

Current UI model:

- `/start`, `/menu`, `/controls`: open the main hub
- `/repo`: choose or create a project
- `/sessions`: choose a local Codex session for the current project
- `/workspace`, `/tasks`: see active and recent runs across projects
- `/mode`: switch `sandbox` or `full_access`
- `/new`: clear current session for the project
- `/status`: technical status and status line
- `/verbose`: output verbosity

Main menu actions:

- `📁 Проект`
- `🗂 Сессии`
- `📊 Сводка`
- `⚙️ Режим`
- `🆕 Новая сессия`

During a running request the main action is:

- `⏹ Остановить`

## Execution Lifecycle

For a normal text/document/voice/photo request:

1. Handler builds `RequestContext`.
2. Authorization is checked.
3. Input is normalized into `PreparedCodexRequest`.
4. Current project is resolved.
5. Previous thread is loaded from SQLite or discovered from local Codex sessions.
6. A new `project_runs` row is created.
7. Codex is started with `codex exec --json`.
8. Streamed events update Telegram progress and SQLite run state.
9. Final response is persisted to session state and delivered back to Telegram.

Failure cases:

- CLI launch failure
- timeout
- manual stop
- resume failure with automatic fallback to a new run
- protocol errors in Codex stream

## Active Runs and Stop Handling

Active runs are tracked in memory by user and run id.

Important behavior:

- multiple active runs per user are allowed up to `MAX_ACTIVE_RUNS_PER_USER`
- Telegram callback handling is parallelized via `concurrent_updates`
- `Stop` sets an `interrupt_event` and marks `stop_requested` in SQLite
- on timeout or stop, the whole Codex process group is killed

This was added to fix hangs where child processes from `codex exec` survived after the parent process ended.

## Status Line

The bot can append a compact status line to technical and final responses.

Supported data:

- project
- model
- launch mode
- short session id
- context usage
- 5-hour quota
- weekly quota

Quota sources:

1. live Codex CLI query using `STATUS_LINE_LIMITS_PROMPT`
2. local fallback from saved Codex session files and `token_count` events

Current operational default:

- `STATUS_LINE_LIMITS_PROMPT` is empty
- live quota calls are disabled
- only local fallback is used

Reason:

- live quota calls created technical Codex sessions
- they were part of the observed hanging behavior

## Workspace and Project Model

Projects are directories directly under `APPROVED_DIRECTORY`.

Rules:

- nested projects are not first-class selectable entities
- current project must stay inside the approved workspace
- if no project exists and workspace is effectively empty, the bot may auto-create one

Persistence:

- current project is stored in `user_preferences.current_project_path`
- recent projects are stored in `user_recent_projects`
- current project is restored after restart

## Session Model

Per `(user, project)` the bot stores:

- current `thread_id`
- last status
- last error

The bot also reads local Codex session JSONL files to:

- discover a missing thread id
- list local sessions in `/sessions`
- derive local status-line fallback data

## Run Model

SQLite tracks project runs in `project_runs`.

A run stores:

- run id
- user id
- project path
- thread id
- status
- timestamps
- prompt preview
- progress summary
- first tool name
- tool count
- error message
- stop flag

This powers `/workspace`, `/tasks`, run detail screens, and operator CLI tooling.

## SQLite Schema

Key tables:

- `schema_version`
- `project_sessions`
- `project_preferences`
- `project_session_resets`
- `user_preferences`
- `project_runs`
- `user_recent_projects`
- `audit_log`

Migration history in [`session_store.py`](/opt/telecodex/src/codex_telegram_bot/session_store.py):

- v1: sessions and audit log
- v2: extra session result fields
- v3: per-project launch mode
- v4: session reset tracking
- v5: current project persistence
- v6: project runs
- v7: recent projects

## Observability

There are two observability layers:

- structured logs through `structlog`
- append-only audit records in SQLite

Audit examples:

- command opened
- project selected
- run started/finished/failed
- stop requested
- resume fallback used

## Operator CLI

The package exposes `codex-telegram-workspace`.

Examples:

```bash
codex-telegram-workspace workspace
codex-telegram-workspace workspace --project my-project
codex-telegram-workspace run show 42
codex-telegram-workspace run stop 42
codex-telegram-workspace run attach 42
codex-telegram-workspace project switch my-project
```

## Installation and Deployment

For service installation, see:

- [`docs/SERVICE.md`](/opt/telecodex/docs/SERVICE.md)
- [`scripts/install_service.sh`](/opt/telecodex/scripts/install_service.sh)

## Tests

The repository has unit and integration-style tests for:

- config parsing and validation
- SQLite migrations and persistence
- Codex runner and timeout/interrupt behavior
- status line and quota parsing
- Telegram UI rendering
- navigation and execution flows
- voice, document, and image request preparation
- workspace CLI

Main command:

```bash
.venv/bin/python -m pytest -q
```

## Known Operational Constraints

- one polling process per bot token
- active requests do not survive process restart
- rate limiting is in-process memory only
- photo support depends on Codex CLI image support
- voice transcription depends on external API availability
- live quota calls are disabled by default
