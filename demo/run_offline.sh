#!/usr/bin/env bash
# Build and visualize the task model from successful demonstrations.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
POLICY_PYTHON="${LITETWIN_POLICY_PYTHON:-}"

if [[ -z "$POLICY_PYTHON" ]]; then
  echo "Set LITETWIN_POLICY_PYTHON to the Python 3.10 policy environment." >&2
  exit 2
fi

cd "$PROJECT_ROOT"
export PYTHONPATH="$PROJECT_ROOT/code${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONUNBUFFERED=1

"$POLICY_PYTHON" -c "import litetwin; import numpy" >/dev/null
"$POLICY_PYTHON" demo/reset_outputs.py \
  --output-root "$PROJECT_ROOT/outputs" \
  --stage offline \
  --demo open_drawer

"$POLICY_PYTHON" demo/build_models.py --output "$PROJECT_ROOT/outputs"
"$POLICY_PYTHON" demo/visualize_offline.py \
  --summary "$PROJECT_ROOT/outputs/offline/model_summary.json" \
  --output-stem "$PROJECT_ROOT/outputs/offline/task_structure"
