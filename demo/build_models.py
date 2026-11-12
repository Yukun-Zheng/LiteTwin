"""Build the Open Drawer LiteTwin task model from five packaged demonstrations."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CODE_ROOT = PROJECT_ROOT / "code"
sys.path.insert(0, str(CODE_ROOT))
os.chdir(CODE_ROOT)

from integrations.rlbench.rlbench_dynamac.core.records import atomic_json
from integrations.rlbench.rlbench_dynamac.core.task_specs import load_task_specs
from integrations.rlbench.rlbench_dynamac.data.tapas_segmentation import (
    load_rlbench_segmentation_config,
)
from integrations.rlbench.rlbench_dynamac.data.demo_adapter import (
    load_low_dim_obs_pickles,
    make_unimanual_demonstrations,
)
from integrations.rlbench.rlbench_litetwin.build_models import build_task


TASK = "open_drawer"
DEMONSTRATION_COUNT = 5
DATA_ROOT = CODE_ROOT / "integrations" / "rlbench" / "data" / "demo"
BASE_MODELS = CODE_ROOT / "integrations" / "rlbench" / "models" / "dynamac"
SEGMENTATION_CONFIG = (
    CODE_ROOT
    / "integrations"
    / "rlbench"
    / "configs"
    / "open_drawer"
    / "segmentation.json"
)
TASK_CONFIG = (
    CODE_ROOT / "integrations" / "rlbench" / "configs" / "open_drawer" / "task.json"
)
BOUNDARY_CONFIG = (
    CODE_ROOT / "integrations" / "rlbench" / "configs" / "open_drawer" / "boundary.json"
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _display_path(path: Path) -> str:
    path = path.resolve()
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(path)
    return value


def build(output_root: Path) -> dict[str, Any]:
    output_root = output_root.resolve()
    model_root = output_root / "models" / "litetwin"
    task_model_root = model_root / TASK
    offline_root = output_root / "offline"
    summary_path = offline_root / "model_summary.json"
    visualization_path = offline_root / "task_structure.svg"
    if task_model_root.exists() or summary_path.exists():
        raise FileExistsError(
            f"model output already exists under {output_root}; replace the current "
            "offline stage before rebuilding"
        )

    demonstrations = tuple(
        DATA_ROOT
        / TASK
        / "all_variations"
        / "episodes"
        / f"episode{index}"
        / "low_dim_obs.pkl"
        for index in range(DEMONSTRATION_COUNT)
    )
    missing = [path for path in demonstrations if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"missing packaged demonstrations: {missing}")

    base_checkpoint = BASE_MODELS / TASK / "model.npz"
    if not base_checkpoint.is_file():
        raise FileNotFoundError(base_checkpoint)

    started = time.monotonic()
    task_spec = load_task_specs(TASK_CONFIG)[TASK]
    segmentation = load_rlbench_segmentation_config(SEGMENTATION_CONFIG)
    converted = make_unimanual_demonstrations(
        load_low_dim_obs_pickles(demonstrations),
        task_spec,
        names=[path.parent.name for path in demonstrations],
        config=segmentation,
    )
    segmentation_audit = converted.segmentation.audit
    policy_path = build_task(
        TASK,
        base_models=BASE_MODELS,
        output_root=model_root,
        demonstration_count=DEMONSTRATION_COUNT,
        task_model_config=CODE_ROOT / "configs" / "litetwin_task_model.json",
        belief_config=CODE_ROOT / "configs" / "litetwin_inference.json",
        execution_config=CODE_ROOT / "configs" / "litetwin_execution.json",
        recovery_config=CODE_ROOT / "configs" / "litetwin_recovery.json",
        task_data_dir=DATA_ROOT / TASK,
        boundary_config=BOUNDARY_CONFIG,
        task_spec=task_spec,
        segmentation_config=segmentation,
    )
    build_record = _load(policy_path.parent / "build.json")
    model_record = build_record["policy_summary"]["models"]["single"]
    generated = sorted(path for path in policy_path.parent.iterdir() if path.is_file())
    structure = {
        "state_count": model_record["state_count"],
        "skill_state_counts": model_record["skill_state_counts"],
        "boundary_count": model_record["boundary_count"],
        "relation_frames": model_record["relation_frames"],
        "link_anchor_count": model_record["link_anchor_count"],
        "link_pending_count": model_record["link_pending_count"],
        "unlink_event_count": model_record["unlink_event_count"],
        "scene_factor_count": model_record["scene_factor_count"],
    }
    payload = {
        "schema": "litetwin.complete-demo-model.v1",
        "task": TASK,
        "elapsed_seconds": time.monotonic() - started,
        "inputs": {
            "successful_demonstrations": [
                {
                    "name": path.parent.name,
                    "path": str(path.relative_to(PROJECT_ROOT)),
                    "sha256": _sha256(path),
                }
                for path in demonstrations
            ],
            "frozen_motion_backbone": {
                "path": str(base_checkpoint.relative_to(PROJECT_ROOT)),
                "sha256": _sha256(base_checkpoint),
            },
        },
        "extracted_task_structure": structure,
        "demonstration_segmentation": {
            "strategy": segmentation_audit["strategy"],
            "alignment_method": segmentation_audit["alignment_method"],
            "boundary_signals": segmentation_audit["boundary_components"],
            "demonstrations": [
                {
                    "name": path.parent.name,
                    "observation_count": int(length),
                    "boundaries": [int(value) for value in boundaries],
                    "skill_bounds": [[int(start), int(stop)] for start, stop in bounds],
                }
                for path, length, boundaries, bounds in zip(
                    demonstrations,
                    converted.segmentation.trajectory_lengths,
                    converted.segmentation.boundaries,
                    segmentation_audit["skill_bounds"],
                    strict=True,
                )
            ],
        },
        "generated_model": {
            "root": _display_path(policy_path.parent),
            "files": {path.name: _sha256(path) for path in generated},
        },
        "visualization": _display_path(visualization_path),
    }
    offline_root.mkdir(parents=True, exist_ok=True)
    atomic_json(summary_path, payload)
    return payload


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "outputs",
    )
    args = parser.parse_args(argv)
    print(json.dumps(build(args.output), indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
