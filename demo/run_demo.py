"""Run one outcome-checked LiteTwin visualization episode."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CODE_ROOT = PROJECT_ROOT / "code"
sys.path.insert(0, str(CODE_ROOT))
os.chdir(CODE_ROOT)

from integrations.rlbench.rlbench_litetwin.episode import run_episode
from integrations.rlbench.rlbench_litetwin.episode_records import atomic_json
from integrations.rlbench.rlbench_litetwin.video import (
    ObservationRecorder,
    PhysicsStepCapture,
    create_video_environment,
)


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(path)
    return value


def _resolve_project_path(relative: str) -> Path:
    path = (PROJECT_ROOT / relative).resolve()
    path.relative_to(PROJECT_ROOT)
    return path


def run(
    name: str,
    output_root: Path,
    policy_python: Path,
    litetwin_models_root: Path,
) -> dict[str, Any]:
    configuration = _load(PROJECT_ROOT / "demo" / "config.json")
    try:
        selected = configuration["demos"][name]
    except KeyError as error:
        choices = ", ".join(sorted(configuration.get("demos", {})))
        raise ValueError(f"unknown demo {name!r}; choose one of: {choices}") from error

    row = _load(_resolve_project_path(selected["episode"]))
    episode_id = str(row["episode_id"])

    litetwin_models_root = litetwin_models_root.resolve()
    expected_policy = litetwin_models_root / row["task"] / "policy.json"
    if not expected_policy.is_file():
        raise FileNotFoundError(
            "the demonstration-derived LiteTwin model is missing; run "
            "demo/build_models.py before online execution"
        )

    output_root = output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    video = output_root / f"{name}.mp4"
    sidecar = output_root / f"{name}.json"
    records = output_root / f"{name}_records"
    log = output_root / f"{name}.log"
    video.unlink(missing_ok=True)
    sidecar.unlink(missing_ok=True)
    log.unlink(missing_ok=True)
    if records.exists():
        shutil.rmtree(records)

    staging = output_root / f".{name}.staging.mp4"
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("ffmpeg is required to record the online demo")
    recorder = ObservationRecorder(
        staging,
        cameras=("front", "overhead"),
        fps=10,
        ffmpeg=Path(ffmpeg),
    )
    physics_capture = PhysicsStepCapture(recorder, stride=2)

    def environment_factory():
        environment, restore_scene = create_video_environment(
            (320, 240),
            front_image_plane_shift=(0.0, 0.0),
        )
        original_shutdown = environment.shutdown

        def shutdown_without_reset_capture():
            physics_capture.restore()
            return original_shutdown()

        environment.shutdown = shutdown_without_reset_capture
        return environment, restore_scene

    def initialized(task_environment, observation):
        recorder.capture(observation)
        physics_capture.install(task_environment)

    captured_output = io.StringIO()
    try:
        with contextlib.redirect_stdout(captured_output):
            result = run_episode(
                row,
                records,
                policy_python=policy_python,
                litetwin_models=litetwin_models_root,
                environment_factory=environment_factory,
                on_initialized=initialized,
            )
    finally:
        physics_capture.restore()
    runtime_output = captured_output.getvalue()
    if not runtime_output.strip():
        runtime_output = (
            f"episode_id={episode_id}\n"
            f"success={bool(result.get('success'))}\n"
            f"reason={result.get('reason')}\n"
            f"cycles={result.get('cycles')}\n"
        )
    log.write_text(runtime_output, encoding="utf-8")

    try:
        if result.get("reason") == "infrastructure_error":
            raise RuntimeError(f"demo infrastructure error: {result.get('error')}")
        if bool(result.get("success")) is not bool(selected["expected_success"]):
            raise RuntimeError(
                "demo outcome did not match the packaged reference; inspect the "
                "runtime versions and generated episode record"
            )
        if not (result.get("audit") or {}).get("physically_triggered"):
            raise RuntimeError("The specified disturbance did not take effect")
        recorder.close()
        os.replace(staging, video)
    except Exception:
        recorder.abort()
        staging.unlink(missing_ok=True)
        raise

    payload = {
        "schema": "litetwin.quick-demo-result.v1",
        "demo": name,
        "episode_id": episode_id,
        "success": bool(result.get("success")),
        "reason": result.get("reason"),
        "cycles": result.get("cycles"),
        "physically_triggered": bool(
            isinstance(result.get("audit"), dict)
            and result["audit"].get("physically_triggered")
        ),
        "video": str(video.relative_to(PROJECT_ROOT)),
        "reference_video": selected["reference_video"],
        "task_model_summary": "outputs/offline/model_summary.json",
        "generated_litetwin_model": str(
            expected_policy.parent.relative_to(PROJECT_ROOT)
        ),
        "runtime_log": str(log.relative_to(PROJECT_ROOT)),
        "frames": int(recorder.frames),
        "physics_steps": int(physics_capture.physics_steps),
    }
    atomic_json(sidecar, payload)
    return payload


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--demo", default="open_drawer")
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "outputs",
    )
    parser.add_argument(
        "--policy-python",
        type=Path,
        default=Path(os.environ.get("LITETWIN_POLICY_PYTHON", sys.executable)),
    )
    parser.add_argument("--litetwin-models-root", type=Path, required=True)
    args = parser.parse_args(argv)
    payload = run(
        args.demo,
        args.output,
        args.policy_python.resolve(),
        args.litetwin_models_root,
    )
    print(json.dumps(payload, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
