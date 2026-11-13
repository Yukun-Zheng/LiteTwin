#!/usr/bin/env bash
# Run the offline and online demonstration stages in sequence.
set -euo pipefail

DEMO_NAME="${1:-open_drawer}"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "[1/2] Offline: extracting task structure from five demonstrations"
bash "$PROJECT_ROOT/demo/run_offline.sh"

echo "[2/2] Online: running execution monitoring and recovery and recording the rollout"
bash "$PROJECT_ROOT/demo/run_online.sh" "$DEMO_NAME"
