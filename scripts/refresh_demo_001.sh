#!/bin/zsh

set -euo pipefail

ROOT_DIR="/Users/qiudejia/project/vb01_python"
PYTHON_BIN="${PYTHON_BIN:-/Users/qiudejia/.pyenv/versions/3.10.14/bin/python}"

CAPTURE_DIR="${DEMO_CAPTURE_DIR:-$ROOT_DIR/data/captures/01_both_windows_10s_5s}"
BASELINE_JSON="${DEMO_BASELINE_JSON:-$ROOT_DIR/data/baselines/elevator_001/baseline.json}"
LATEST_JSON="${DEMO_LATEST_JSON:-$ROOT_DIR/data/diagnosis/elevator_001/latest_status.json}"
HISTORY_JSONL="${DEMO_HISTORY_JSONL:-$ROOT_DIR/data/diagnosis/elevator_001/history.jsonl}"
MAX_FILES="${DEMO_MAX_FILES:-6}"

mkdir -p "$ROOT_DIR/logs"
mkdir -p "$(dirname "$LATEST_JSON")"
mkdir -p "$(dirname "$HISTORY_JSONL")"

cd "$ROOT_DIR"

"$PYTHON_BIN" -m elevator_monitor.batch_diagnosis \
  --input-dir "$CAPTURE_DIR" \
  --max-files "$MAX_FILES" \
  --baseline-json "$BASELINE_JSON" \
  --latest-json "$LATEST_JSON" \
  --history-jsonl "$HISTORY_JSONL"

echo "refreshed demo status"
echo "capture_dir=$CAPTURE_DIR"
echo "latest_json=$LATEST_JSON"
