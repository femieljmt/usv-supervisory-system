#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
SESSIONS_DIR="$PROJECT_ROOT/data/sessions"
REQUESTED_SESSION="${1:-latest}"
OUTPUT_DIRECTORY="${2:-$HOME}"

if [[ ! -d "$SESSIONS_DIR" ]]; then
  echo "ERROR: Folder session belum tersedia: $SESSIONS_DIR" >&2
  exit 1
fi

if [[ "$REQUESTED_SESSION" == "latest" ]]; then
  SESSION_DIR="$(find "$SESSIONS_DIR" -mindepth 1 -maxdepth 1 -type d | sort -r | head -n 1)"
  if [[ -z "$SESSION_DIR" ]]; then
    echo "ERROR: Belum ada session yang dapat diekspor." >&2
    exit 1
  fi
  SESSION_ID="$(basename "$SESSION_DIR")"
else
  SESSION_ID="$REQUESTED_SESSION"
  SESSION_DIR="$SESSIONS_DIR/$SESSION_ID"
fi

if [[ ! -d "$SESSION_DIR" ]]; then
  echo "ERROR: Session tidak ditemukan: $SESSION_ID" >&2
  exit 1
fi

mkdir -p "$OUTPUT_DIRECTORY"
ARCHIVE="$OUTPUT_DIRECTORY/session_${SESSION_ID}.tar.gz"

tar -czf "$ARCHIVE" -C "$SESSIONS_DIR" "$SESSION_ID"

printf 'Session berhasil diekspor.\n'
printf 'Session : %s\n' "$SESSION_ID"
printf 'Archive : %s\n' "$ARCHIVE"
ls -lh "$ARCHIVE"
