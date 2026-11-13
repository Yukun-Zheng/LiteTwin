#!/usr/bin/env bash
# Execute and visualize one online execution monitoring and recovery episode.
set -euo pipefail

DEMO_NAME="${1:-open_drawer}"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SIM_PYTHON="${LITETWIN_SIM_PYTHON:-}"
POLICY_PYTHON="${LITETWIN_POLICY_PYTHON:-}"
RLBENCH_ROOT="${LITETWIN_RLBENCH_ROOT:-}"
PYTRACIK_ROOT="${LITETWIN_PYTRACIK_PREFIX:-}"
MODEL_ROOT="$PROJECT_ROOT/outputs/models/litetwin"
ONLINE_ROOT="$PROJECT_ROOT/outputs/online"

if [[ -z "$SIM_PYTHON" || -z "$POLICY_PYTHON" ]]; then
  echo "Set LITETWIN_SIM_PYTHON and LITETWIN_POLICY_PYTHON to the two environment interpreters." >&2
  exit 2
fi
if [[ -z "${COPPELIASIM_ROOT:-}" || ! -d "$COPPELIASIM_ROOT" ]]; then
  echo "Set COPPELIASIM_ROOT to a CoppeliaSim Edu 4.1 installation." >&2
  exit 2
fi
if [[ -z "$RLBENCH_ROOT" || ! -d "$RLBENCH_ROOT/rlbench" ]]; then
  echo "Set LITETWIN_RLBENCH_ROOT to the pinned RLBench checkout." >&2
  exit 2
fi
if [[ ! -f "$MODEL_ROOT/open_drawer/policy.json" ]]; then
  echo "No generated LiteTwin model found. Run: bash demo/run_offline.sh" >&2
  exit 2
fi
for command in xvfb-run ffmpeg; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "Required command not found: $command" >&2
    exit 2
  fi
done

cd "$PROJECT_ROOT"
export PYTHONPATH="$PROJECT_ROOT/code:$RLBENCH_ROOT${PYTRACIK_ROOT:+:$PYTRACIK_ROOT/bounded-overlay}${PYTHONPATH:+:$PYTHONPATH}"
export QT_QPA_PLATFORM_PLUGIN_PATH="$COPPELIASIM_ROOT"
export QT_QPA_PLATFORM=xcb
export LIBGL_ALWAYS_SOFTWARE=1
export LD_LIBRARY_PATH="$COPPELIASIM_ROOT${PYTRACIK_ROOT:+:$PYTRACIK_ROOT/lib}${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export DYNAMAC_POLICY_PYTHON="$POLICY_PYTHON"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONUNBUFFERED=1

"$POLICY_PYTHON" -c "import litetwin; import numpy" >/dev/null
"$SIM_PYTHON" -c "import pyrep, rlbench" >/dev/null
"$POLICY_PYTHON" demo/reset_outputs.py \
  --output-root "$PROJECT_ROOT/outputs" \
  --stage online \
  --demo "$DEMO_NAME"

xvfb-run --auto-servernum \
  --server-args="-screen 0 1280x1024x24 -nolisten tcp" \
  "$SIM_PYTHON" demo/run_demo.py \
  --demo "$DEMO_NAME" \
  --policy-python "$POLICY_PYTHON" \
  --litetwin-models-root "$MODEL_ROOT" \
  --output "$ONLINE_ROOT"

"$POLICY_PYTHON" demo/visualize_online.py \
  --records "$ONLINE_ROOT/${DEMO_NAME}_records" \
  --model-summary "$PROJECT_ROOT/outputs/offline/model_summary.json" \
  --output-stem "$ONLINE_ROOT/execution_timeline"
