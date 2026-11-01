#!/usr/bin/env bash
# Start capacity-forge locally.
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
if [ -d ".venv" ]; then
  source .venv/bin/activate
fi

if ! "$PY" -c "import flask" >/dev/null 2>&1; then
  echo "Flask not found. Install it with: pip install -r requirements.txt"
  exit 1
fi

PORT="${PORT:-8788}"
echo "capacity-forge -> http://127.0.0.1:${PORT}"
exec "$PY" app.py
