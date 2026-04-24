# Service Installation Guide

## Goal

This guide explains how to run `telecodex` as a `systemd` service.

The repository also includes an install script:

- [`scripts/install_service.sh`](/opt/telecodex/scripts/install_service.sh)

## What the Script Does

The script:

1. validates the repository path
2. creates a virtual environment if missing
3. installs the package into that virtual environment
4. writes a `systemd` unit file
5. reloads `systemd`
6. enables and optionally starts the service

It does not create your Telegram bot token or `.env` automatically.

## Prerequisites

- Linux with `systemd`
- Python 3.11+
- Node.js and the official Codex CLI available to the service user
- prepared `.env` file in the project directory
- existing `APPROVED_DIRECTORY`

## Recommended Layout

Example:

```text
/opt/telecodex
  .env
  .venv/
  src/
  scripts/
```

## Default Service Shape

By default the script installs:

- service name: `telecodex`
- unit file: `/etc/systemd/system/telecodex.service`
- working directory: current repository path
- env file: `<repo>/.env`
- command: `<repo>/.venv/bin/python -m codex_telegram_bot.main`

## Usage

Run as root:

```bash
sudo ./scripts/install_service.sh
```

Custom values:

```bash
sudo ./scripts/install_service.sh \
  --service-name telecodex \
  --repo-dir /opt/telecodex \
  --run-user root \
  --env-file /opt/telecodex/.env \
  --python /usr/bin/python3
```

## Important Options

- `--service-name`: unit name without `.service`
- `--repo-dir`: repository root
- `--run-user`: Linux user that will run the process
- `--run-group`: Linux group for the service
- `--env-file`: path to `.env`
- `--python`: Python interpreter used to create `.venv`
- `--skip-enable`: install unit but do not enable it
- `--skip-start`: install unit but do not start/restart it

## After Installation

Useful commands:

```bash
sudo systemctl status telecodex.service
sudo systemctl restart telecodex.service
sudo journalctl -u telecodex.service -f
```

## Updating After Code Changes

If only Python code changed:

```bash
sudo systemctl restart telecodex.service
```

If dependencies or the unit file changed:

```bash
sudo ./scripts/install_service.sh
```

## Troubleshooting

### Service fails immediately

Check:

- `.env` exists and contains valid values
- `APPROVED_DIRECTORY` exists
- `TELEGRAM_BOT_TOKEN` is valid
- `codex` is available to the service user

### `codex` not found in service

Either:

- install Codex CLI globally for the service user
- or add its directory to `PATH` in the unit `Environment=`
- or set `CODEX_CLI_PATH` explicitly in `.env`

### Voice transcription errors

Check matching env vars:

- `VOICE_PROVIDER`
- `OPENAI_API_KEY`
- `VOICE_API_KEY`
- `VOICE_API_BASE_URL`
- `VOICE_TRANSCRIPTION_MODEL`

### Status line quota values are `unknown`

Current default behavior is expected:

- `STATUS_LINE_LIMITS_PROMPT` is empty
- live quota calls are disabled
- local fallback may still show `unknown` if recent session data is missing
