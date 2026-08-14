#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
MAVPROXY_VENV="${MAVPROXY_VENV:-$HOME/mavproxy-env}"
PIXHAWK_DEVICE="${PIXHAWK_DEVICE:-/dev/ttyACM0}"
PIXHAWK_BAUD="${PIXHAWK_BAUD:-115200}"
LOCAL_MAVLINK_PORT="${LOCAL_MAVLINK_PORT:-14551}"
MISSION_PLANNER_IP="${MISSION_PLANNER_IP:-}"
MISSION_PLANNER_PORT="${MISSION_PLANNER_PORT:-14550}"
MAVPROXY_LOG="$PROJECT_DIR/data/logs/mavproxy_runtime.log"

if [[ ! -d "$PROJECT_DIR/.venv" ]]; then
    echo "Virtual environment onboard tidak ditemukan: $PROJECT_DIR/.venv" >&2
    exit 1
fi

if [[ ! -d "$MAVPROXY_VENV" ]]; then
    echo "Virtual environment MAVProxy tidak ditemukan: $MAVPROXY_VENV" >&2
    exit 1
fi

if [[ ! -e "$PIXHAWK_DEVICE" ]]; then
    echo "Pixhawk belum terdeteksi pada $PIXHAWK_DEVICE" >&2
    echo "Periksa dengan: ls -l /dev/ttyACM*" >&2
    exit 1
fi

mkdir -p "$PROJECT_DIR/data/logs"

MAVPROXY_PID=""
cleanup() {
    if [[ -n "$MAVPROXY_PID" ]] && kill -0 "$MAVPROXY_PID" 2>/dev/null; then
        kill "$MAVPROXY_PID" 2>/dev/null || true
        wait "$MAVPROXY_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

source "$MAVPROXY_VENV/bin/activate"

MAVPROXY_ARGS=(
    --master="$PIXHAWK_DEVICE"
    --baudrate="$PIXHAWK_BAUD"
    --out="udp:127.0.0.1:$LOCAL_MAVLINK_PORT"
    --streamrate=10
    --show-errors
)

if [[ -n "$MISSION_PLANNER_IP" ]]; then
    MAVPROXY_ARGS+=(--out="udp:$MISSION_PLANNER_IP:$MISSION_PLANNER_PORT")
fi

mavproxy.py "${MAVPROXY_ARGS[@]}" >>"$MAVPROXY_LOG" 2>&1 &
MAVPROXY_PID=$!

sleep 2

if ! kill -0 "$MAVPROXY_PID" 2>/dev/null; then
    echo "MAVProxy gagal dijalankan. Log terakhir:" >&2
    tail -n 30 "$MAVPROXY_LOG" >&2 || true
    exit 1
fi

echo "MAVProxy aktif (PID=$MAVPROXY_PID)"
echo "  Pixhawk     : $PIXHAWK_DEVICE"
echo "  Supervisory : udp:127.0.0.1:$LOCAL_MAVLINK_PORT"
if [[ -n "$MISSION_PLANNER_IP" ]]; then
    echo "  MissionPlan.: udp:$MISSION_PLANNER_IP:$MISSION_PLANNER_PORT"
else
    echo "  MissionPlan.: disabled (set MISSION_PLANNER_IP to enable)"
fi
echo "  Full log    : $MAVPROXY_LOG"
echo

source "$PROJECT_DIR/.venv/bin/activate"
cd "$PROJECT_DIR"
exec python -m onboard.app
