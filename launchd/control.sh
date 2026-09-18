#!/bin/zsh
set -euo pipefail
PROJECT_DIR="${0:A:h:h}"
exec "$PROJECT_DIR/.venv/bin/python" "$PROJECT_DIR/launchd/manage.py" "${1:-status}"
