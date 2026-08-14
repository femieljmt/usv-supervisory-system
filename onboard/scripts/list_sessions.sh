#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${1:-$HOME/usv_supervisory_final}"
SESSIONS_DIR="$PROJECT_ROOT/data/sessions"

if [[ ! -d "$SESSIONS_DIR" ]]; then
  echo "Belum ada folder session: $SESSIONS_DIR"
  exit 0
fi

printf '%-32s  %-12s  %-8s  %s\n' "SESSION" "STATUS" "PENDING" "DIRECTORY"
printf '%-32s  %-12s  %-8s  %s\n' "--------------------------------" "------------" "--------" "---------"

found=0
while IFS= read -r session_dir; do
  found=1
  session_id="$(basename "$session_dir")"
  info_file="$session_dir/session_info.json"

  status="UNKNOWN"
  pending="-"

  if [[ -f "$info_file" ]]; then
    if command -v python3 >/dev/null 2>&1; then
      read -r status pending < <(
        python3 - "$info_file" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    print("INVALID -")
else:
    status = data.get("status", "UNKNOWN")
    pending = data.get("final_pending_outbox")
    if pending is None:
        pending = data.get("initial_pending_outbox", "-")
    print(status, pending)
PY
      )
    fi
  fi

  printf '%-32s  %-12s  %-8s  %s\n' \
    "$session_id" "$status" "$pending" "$session_dir"
done < <(find "$SESSIONS_DIR" -mindepth 1 -maxdepth 1 -type d | sort -r)

if [[ "$found" -eq 0 ]]; then
  echo "Belum ada session di $SESSIONS_DIR"
fi
