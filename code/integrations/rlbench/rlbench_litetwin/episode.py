"""Execute one LiteTwin episode with physical feedback and relation-loss recovery."""

from __future__ import annotations

import importlib
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Mapping

import numpy as np

from integrations.rlbench.rlbench_dynamac.core.runtime import (
    DEFAULT_FINAL_SETTLING_PHYSICS_STEPS,
    commit_joint_hold_after_primary_failure,
    initialize_fresh_task_generation,
    policy_action_execution_status,
    policy_action_execution_statuses,
    run_final_settling,
    set_policy_gripper_authorization,
)
from integrations.rlbench.rlbench_dynamac.core.task_specs import load_task_specs
from integrations.rlbench.rlbench_litetwin.disturbance import (
    RelationLossEnvironment,
    RelationLossSpec,
)
from integrations.rlbench.rlbench_litetwin.environment import PolicyProcess
from integrations.rlbench.rlbench_litetwin.episode_records import EpisodeWriter


CODE_ROOT = Path(__file__).resolve().parents[3]
INTEGRATION_ROOT = CODE_ROOT / "integrations" / "rlbench"
TASK_CONFIG = INTEGRATION_ROOT / "configs" / "open_drawer" / "task.json"
BOUNDARY_CONFIG = INTEGRATION_ROOT / "configs" / "open_drawer" / "boundary.json"
BASE_MODELS = INTEGRATION_ROOT / "models" / "dynamac"


def _task_state(observation):
    value = observation.task_low_dim_state
    if isinstance(value, tuple) and len(value) == 1:
        value = value[0]
    return np.asarray(value, dtype=np.float64).reshape(-1)


def _relation_state(environment):
    gripper = environment._scene.robot.gripper
    grasped = tuple(gripper.get_grasped_objects())
    if grasped:
        return "linked"
    amount = np.asarray(gripper.get_open_amount(), dtype=np.float64)
    if np.all(amount > 0.9):
        return "external"
    sensor = getattr(gripper, "_proximity_sensor", None)
    detects = getattr(sensor, "is_detected", None)
    task = environment._scene.task
    if callable(detects):
        candidates = list(task.get_graspable_objects())
        for obj in task.get_base().get_objects_in_tree(exclude_base=False):
            dynamic = getattr(obj, "is_dynamic", None)
            respondable = getattr(obj, "is_respondable", None)
            if (
                callable(dynamic)
                and callable(respondable)
                and dynamic()
                and respondable()
            ):
                candidates.append(obj)
        if any(detects(obj) for obj in candidates):
            return "linked"
    return "external"


def _feature(response, observation, action, cycle):
    state = response.get("policy_state", {})
    return {
        "cycle": int(cycle),
        "arms": {
            "single": {
                "ee_pose_xyzw": np.asarray(observation.gripper_pose).tolist(),
                "gripper_open": float(observation.gripper_open),
            }
        },
        "task_state": _task_state(observation).tolist(),
        "action": np.asarray(action).tolist(),
        "policy_state": {
            "reference_state_if_available": {
                "skill": state.get("skill_index"),
                "progress": state.get("time_index"),
                "mode": state.get("mode"),
            },
            "active_streams_if_available": list(state.get("active_frames", ())),
            "poe_weights": dict(state.get("poe_weights", {})),
        },
    }


def run_episode(
    row: Mapping[str, Any],
    output_root: Path,
    *,
    policy_python: Path,
    litetwin_models: Path,
    environment_factory: Callable[[], tuple[Any, Callable[[], None]]],
    on_initialized: Callable[[Any, Any], None] | None = None,
    diagnostics_dir: Path | None = None,
):
    from rlbench.backend.exceptions import InvalidActionError

    task_spec = load_task_specs(TASK_CONFIG)[str(row["task"])]
    task_class = getattr(
        importlib.import_module(task_spec.module), task_spec.class_name
    )
    writer = EpisodeWriter(Path(output_root), str(row["episode_id"]))
    environment = None
    restore_scene = lambda: None
    worker = None
    launched = False
    started = time.monotonic()
    cycles = 0
    invalid_actions = 0
    success = False
    reason = "infrastructure_error"
    error = None
    generation = None
    wrapped = None
    reentry_cycles = []
    try:
        environment, restore_scene = environment_factory()
        environment.launch()
        launched = True
        task_environment, descriptions, observation, generation = (
            initialize_fresh_task_generation(
                environment,
                task_class,
                episode_seed=int(row["seed"]),
                variation=int(row["variation"]),
                verify_instance=False,
            )
        )
        if on_initialized is not None:
            on_initialized(task_environment, observation)
        worker = PolicyProcess(
            policy_python,
            str(row["task"]),
            BASE_MODELS,
            litetwin_models,
            TASK_CONFIG,
            BOUNDARY_CONFIG,
            diagnostics_dir=diagnostics_dir,
        )
        worker.request("reset", observation)
        disturbance = row["disturbance"]
        earliest = max(
            0, int(round(float(disturbance["trigger_fraction"]) * worker.policy_steps))
        )
        wrapped = RelationLossEnvironment(
            task_environment,
            RelationLossSpec(
                earliest_step=earliest,
                minimum_stable_cycles=int(disturbance["minimum_stable_cycles"]),
                translation=tuple(disturbance["translation_m"]),
            ),
        )
        for cycle in range(int(row["horizon"])):
            response = worker.request("act", observation)
            if response.get("policy_failed"):
                worker.request(
                    "commit",
                    transaction_id=int(response["transaction_id"]),
                    primary_action_status="stopped",
                    primary_action_applied=False,
                )
                reason = "policy_failure"
                break
            action = response.get("action")
            if action is None:
                settling = run_final_settling(
                    wrapped,
                    physics_steps=DEFAULT_FINAL_SETTLING_PHYSICS_STEPS,
                )
                success = bool(settling["success"])
                reason = (
                    "success_after_final_settling" if success else "policy_complete"
                )
                break
            command = np.asarray(action, dtype=np.float64)
            feature = _feature(response, observation, command, cycle)
            state = response.get("policy_state", {})
            mode = str(state.get("execution_mode", "task")).lower()
            reentry = bool(state.get("reentry_committed", False))
            if reentry:
                reentry_cycles.append(cycle)
            transaction_id = int(response["transaction_id"])
            set_policy_gripper_authorization(
                wrapped, response.get("gripper_authorization")
            )
            applied = True
            try:
                observation, reward, terminate = wrapped.step(command)
            except InvalidActionError:
                invalid_actions += 1
                applied = False
                observation, reward, terminate, policy_complete = (
                    commit_joint_hold_after_primary_failure(
                        wrapped,
                        worker,
                        transaction_id=transaction_id,
                    )
                )
                wrapped.record_committed_fallback()
                aggregate = "stopped"
                per_arm = {"single": "stopped"}
            else:
                aggregate = policy_action_execution_status(wrapped)
                per_arm = policy_action_execution_statuses(wrapped)
                committed = worker.request(
                    "commit",
                    transaction_id=transaction_id,
                    primary_action_status=aggregate,
                    primary_action_statuses=per_arm,
                )
                policy_complete = bool(committed.get("complete"))
            injector = wrapped.protocol_metadata()
            writer.write_cycle(
                cycle,
                feature,
                {
                    "cycle": cycle,
                    "physical_relation": {"single": _relation_state(wrapped)},
                },
                {
                    "action_resolution": {
                        "aggregate": aggregate,
                        "per_arm": per_arm,
                        "primary_action_applied": applied,
                    },
                    "reward": float(reward),
                    "terminate": bool(terminate),
                    "policy_complete": bool(policy_complete),
                    "injector": injector,
                    "policy_audit": {
                        "arms": {
                            "single": {
                                "mode_after": mode,
                                "reentry_committed": reentry,
                            }
                        }
                    },
                    "applied_action": command.tolist(),
                },
            )
            cycles = cycle + 1
            if float(reward) > 0.0:
                success = True
                reason = "success"
                break
            if bool(terminate):
                reason = "task_terminated"
                break
            if policy_complete:
                settling = run_final_settling(
                    wrapped,
                    physics_steps=DEFAULT_FINAL_SETTLING_PHYSICS_STEPS,
                )
                success = bool(settling["success"])
                reason = (
                    "success_after_final_settling" if success else "policy_complete"
                )
                break
        else:
            reason = "episode_horizon"
    except Exception as exc:
        error = {
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
        }
    finally:
        try:
            if worker is not None:
                worker.close()
            if launched and environment is not None:
                environment.shutdown()
        finally:
            restore_scene()
    audit = (
        {"eligible": False, "physically_triggered": False}
        if wrapped is None
        else {
            "eligible": True,
            "physically_triggered": bool(wrapped.protocol_metadata()["triggered"]),
            "relation_restored_cycle": wrapped.protocol_metadata()[
                "relation_restoration_policy_step"
            ],
            "legal_reentry_cycle": reentry_cycles[0] if reentry_cycles else None,
        }
    )
    summary = {
        "episode_id": str(row["episode_id"]),
        "task": str(row["task"]),
        "variation": int(row["variation"]),
        "seed": int(row["seed"]),
        "success": bool(success),
        "reason": reason,
        "cycles": int(cycles),
        "invalid_actions": int(invalid_actions),
        "wall_seconds": time.monotonic() - started,
        "audit": audit,
        "fault_protocol": None if wrapped is None else wrapped.protocol_metadata(),
        "fresh_task_generation": generation,
        "descriptions": descriptions if "descriptions" in locals() else None,
        "error": error,
    }
    writer.finalize(summary)
    return summary


__all__ = ["run_episode"]
