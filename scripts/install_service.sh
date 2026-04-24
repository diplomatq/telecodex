#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="telecodex"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_USER="root"
RUN_GROUP=""
ENV_FILE=""
PYTHON_BIN="python3"
SKIP_ENABLE="0"
SKIP_START="0"

usage() {
  cat <<'EOF'
Usage:
  sudo ./scripts/install_service.sh [options]

Options:
  --service-name NAME   systemd unit name without .service
  --repo-dir PATH       repository root
  --run-user USER       service user
  --run-group GROUP     service group
  --env-file PATH       path to .env file
  --python PATH         Python binary used to create .venv
  --skip-enable         do not enable service
  --skip-start          do not start/restart service
  -h, --help            show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --service-name)
      SERVICE_NAME="$2"
      shift 2
      ;;
    --repo-dir)
      REPO_DIR="$2"
      shift 2
      ;;
    --run-user)
      RUN_USER="$2"
      shift 2
      ;;
    --run-group)
      RUN_GROUP="$2"
      shift 2
      ;;
    --env-file)
      ENV_FILE="$2"
      shift 2
      ;;
    --python)
      PYTHON_BIN="$2"
      shift 2
      ;;
    --skip-enable)
      SKIP_ENABLE="1"
      shift
      ;;
    --skip-start)
      SKIP_START="1"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ "$(id -u)" -ne 0 ]]; then
  echo "This script must run as root." >&2
  exit 1
fi

REPO_DIR="$(cd "$REPO_DIR" && pwd)"
if [[ -z "$ENV_FILE" ]]; then
  ENV_FILE="$REPO_DIR/.env"
fi

if [[ ! -f "$REPO_DIR/pyproject.toml" ]]; then
  echo "Repository does not look valid: pyproject.toml not found in $REPO_DIR" >&2
  exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo ".env file not found: $ENV_FILE" >&2
  exit 1
fi

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Python binary not found: $PYTHON_BIN" >&2
  exit 1
fi

VENV_DIR="$REPO_DIR/.venv"
VENV_PYTHON="$VENV_DIR/bin/python"
UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"

echo "Installing virtual environment in $VENV_DIR"
if [[ ! -x "$VENV_PYTHON" ]]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

echo "Upgrading pip"
"$VENV_PYTHON" -m pip install --upgrade pip

echo "Installing project"
"$VENV_PYTHON" -m pip install -e "$REPO_DIR"

echo "Writing systemd unit to $UNIT_PATH"
{
  echo "[Unit]"
  echo "Description=Telecodex Telegram Bot"
  echo "After=network.target"
  echo
  echo "[Service]"
  echo "Type=simple"
  echo "User=$RUN_USER"
  if [[ -n "$RUN_GROUP" ]]; then
    echo "Group=$RUN_GROUP"
  fi
  echo "WorkingDirectory=$REPO_DIR"
  echo "EnvironmentFile=$ENV_FILE"
  echo "ExecStart=$VENV_PYTHON -m codex_telegram_bot.main"
  echo "Restart=always"
  echo "RestartSec=5"
  echo
  echo "[Install]"
  echo "WantedBy=multi-user.target"
} > "$UNIT_PATH"

echo "Reloading systemd"
systemctl daemon-reload

if [[ "$SKIP_ENABLE" != "1" ]]; then
  echo "Enabling ${SERVICE_NAME}.service"
  systemctl enable "${SERVICE_NAME}.service"
fi

if [[ "$SKIP_START" != "1" ]]; then
  echo "Starting ${SERVICE_NAME}.service"
  systemctl restart "${SERVICE_NAME}.service"
  systemctl status "${SERVICE_NAME}.service" --no-pager
else
  echo "Skipping start/restart"
fi

echo
echo "Done."
echo "Useful commands:"
echo "  systemctl status ${SERVICE_NAME}.service"
echo "  systemctl restart ${SERVICE_NAME}.service"
echo "  journalctl -u ${SERVICE_NAME}.service -f"
