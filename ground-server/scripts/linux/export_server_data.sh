#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${1:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
OUTPUT_DIRECTORY="${2:-$HOME}"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
OUTPUT="$OUTPUT_DIRECTORY/usv_server_data_${TIMESTAMP}.tar.gz"

mkdir -p "$OUTPUT_DIRECTORY"
cd "$PROJECT_DIR"

if [[ -d data/logs ]]; then
    tar -czf "$OUTPUT" data/database data/logs
else
    tar -czf "$OUTPUT" data/database
fi

printf '%s
' "$OUTPUT"
