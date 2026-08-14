#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${1:-$(cd "$SCRIPT_DIR/../.." && pwd)}"

cd "$PROJECT_DIR"
source .venv/bin/activate

python -m unittest discover -s tests -v
python - <<'PY'
from server.config import load_settings

s = load_settings()
print(f"Database : {s.database_path}")
print(f"Dashboard: http://{s.dashboard_host}:{s.dashboard_port}")
print(f"MQTT     : {s.mqtt_host}:{s.mqtt_port}")
PY
