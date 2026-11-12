"""Build LiteTwin models from successful demonstrations."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

from litetwin.policy import BimanualDynaMAC, DynaMAC
from litetwin.policy.feedback import (
    LiteTwinMultiStreamPolicy,
    LiteTwinPolicyConfig,
    LiteTwinTaskModelBuilder,
    LiteTwinTaskModelConfig,
)
from integrations.rlbench.rlbench_dynamac.core.records import atomic_json
from integrations.rlbench.rlbench_dynamac.data.demo_adapter import (
    load_low_dim_obs_pickles,
    make_bimanual_demonstrations,
    make_unimanual_demonstrations,
)


BUILD_SCHEMA = "litetwin.task-model-build.v1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"Configuration must be a JSON object: {path}")
    return value


def demonstration_paths(task_data_dir: Path, count: int) -> tuple[Path, ...]:
    episode_root = task_data_dir / "all_variations" / "episodes"
    paths = sorted(
        episode_root.glob("episode*/low_dim_obs.pkl"),
        key=lambda path: int(path.parent.name.removeprefix("episode")),
    )
    if count < 1 or len(paths) < count:
        raise FileNotFoundError(
            f"Expected {count} demonstrations under {episode_root}, found {len(paths)}"
        )
    return tuple(paths[:count])


def build_task(
    task: str,
    *,
    task_data_dir: Path,
    base_models: Path,
    output_root: Path,
    demonstration_count: int,
    task_model_config: Path,
    belief_config: Path,
    execution_config: Path,
    recovery_config: Path,
    boundary_config: Path,
    task_spec,
    segmentation_config,
) -> Path:
    output = output_root / task
    if output.exists():
        raise FileExistsError(f"Task-state model already exists: {output}")
    paths = demonstration_paths(task_data_dir, demonstration_count)
    episodes = load_low_dim_obs_pickles(paths)
    names = [path.parent.name for path in paths]
    converted = (
        make_bimanual_demonstrations(
            episodes, task_spec, names=names, config=segmentation_config
        )
        if task_spec.bimanual
        else make_unimanual_demonstrations(
            episodes, task_spec, names=names, config=segmentation_config
        )
    )
    builder = LiteTwinTaskModelBuilder(LiteTwinTaskModelConfig(**_json(task_model_config)))
    task_dir = base_models / task
    if task_spec.bimanual:
        base = BimanualDynaMAC(
            left=DynaMAC.load(task_dir / "left.npz"),
            right=DynaMAC.load(task_dir / "right.npz"),
        )
        left, right = builder.build_bimanual(
            base,
            converted.left_demonstrations,
            converted.right_demonstrations,
            recoverable_frames=task_spec.recoverable_relation_frames,
        )
        models = {"left": left, "right": right}
        checkpoints = (task_dir / "left.npz", task_dir / "right.npz")
    else:
        base = DynaMAC.load(task_dir / "model.npz")
        models = {
            "single": builder.build(
                base,
                converted.demonstrations,
                arm_id="single",
                recoverable_frames=task_spec.recoverable_relation_frames,
            )
        }
        checkpoints = (task_dir / "model.npz",)
    policy = LiteTwinMultiStreamPolicy(
        models,
        LiteTwinPolicyConfig.from_files(
            belief=belief_config,
            execution=execution_config,
            boundary=boundary_config,
            recovery=recovery_config,
        ),
    )
    output_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{task}.staging-", dir=output_root))
    try:
        policy.save(staging)
        inputs = (
            task_model_config,
            belief_config,
            execution_config,
            recovery_config,
            boundary_config,
            *checkpoints,
            *paths,
        )
        atomic_json(
            staging / "build.json",
            {
                "schema": BUILD_SCHEMA,
                "task": task,
                "bimanual": task_spec.bimanual,
                "demonstrations": names,
                "input_sha256": {str(path): _sha256(path) for path in inputs},
                "policy_summary": policy.summary(),
            },
        )
        os.rename(staging, output)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return output / "policy.json"


__all__ = ["build_task", "demonstration_paths"]
