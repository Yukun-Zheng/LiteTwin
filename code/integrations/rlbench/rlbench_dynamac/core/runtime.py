"""Pose conversion, action execution, and RLBench runtime helpers."""

from __future__ import annotations

import hashlib
import json
import math
import random
import time
from dataclasses import dataclass
from typing import Any, Callable, Literal, Mapping, Protocol

import numpy as np

from integrations.rlbench.rlbench_dynamac.core.pytracik_dependency import (
    pytracik_dependency_identity,
)
from integrations.rlbench.rlbench_dynamac.core.task_specs import (
    TaskSpec,
    get_task_spec,
)
from integrations.rlbench.rlbench_dynamac.core.task_specs import (
    wxyz_to_xyzw as _wxyz_to_xyzw,
)
from integrations.rlbench.rlbench_dynamac.core.task_specs import (
    xyzw_to_wxyz as _xyzw_to_wxyz,
)

Array = np.ndarray

FRESH_TASK_GENERATION_PROTOCOL_ID = (
    "rlbench-prestop-unload-if-present-stop-reload-start-seed-variation-"
    "single-reset-v2"
)
FRESH_TASK_GENERATION_EVIDENCE_SCHEMA = (
    "dynamac-rlbench-fresh-task-generation-evidence-v2"
)
DETERMINISTIC_SOURCE_RESET_PROTOCOL_ID = (
    "rlbench-prestop-unload-if-present-stop-reload-start-source-seed-variation-"
    "single-reset-verify-false-v1"
)
DETERMINISTIC_SOURCE_RESET_EVIDENCE_SCHEMA = (
    "dynamac-rlbench-deterministic-source-reset-evidence-v1"
)
FINAL_SETTLING_PROTOCOL_ID = "rlbench-hold-final-command-up-to-10-raw-physics-steps-v3"
DEFAULT_FINAL_SETTLING_PHYSICS_STEPS = 10


def initialize_fresh_task_generation(
    environment: Any,
    task_class: Any,
    *,
    episode_seed: int,
    variation: int,
    verify_instance: bool = True,
) -> tuple[Any, Any, Any, dict[str, Any]]:

    if isinstance(episode_seed, bool) or not isinstance(episode_seed, int):
        raise TypeError("fresh task-generation seed must be an integer")
    if episode_seed < 0:
        raise ValueError("fresh task-generation seed must be non-negative")
    if isinstance(variation, bool) or not isinstance(variation, int):
        raise TypeError("fresh task-generation variation must be an integer")
    if variation < 0:
        raise ValueError("fresh task-generation variation must be non-negative")
    pyrep = getattr(environment, "_pyrep", None)
    stop = getattr(pyrep, "stop", None)
    get_task = getattr(environment, "get_task", None)
    if pyrep is None or not callable(stop) or not callable(get_task):
        raise RuntimeError("RLBench fresh task-generation lifecycle API is unavailable")

    running_before_stop = getattr(pyrep, "running", None)
    if not isinstance(running_before_stop, bool):
        raise RuntimeError("PyRep running-state audit is unavailable")
    scene = getattr(environment, "_scene", None)
    unload = getattr(scene, "unload", None)
    if scene is None or not callable(unload):
        raise RuntimeError("RLBench scene unload lifecycle API is unavailable")
    previous_task = getattr(scene, "task", None)
    previous_task_present = previous_task is not None
    if running_before_stop is not previous_task_present:
        raise RuntimeError(
            "fresh task-generation physics/task lifecycle state is inconsistent"
        )
    if previous_task_present:
        if running_before_stop is not True:
            raise RuntimeError(
                "preceding task must be unloaded before physics is stopped"
            )
        unload()
        if getattr(scene, "task", None) is not None:
            raise RuntimeError("preceding task did not unload before physics stop")
        if getattr(pyrep, "running", None) is not True:
            raise RuntimeError("preceding task unload unexpectedly stopped physics")
    stop()
    if getattr(pyrep, "running", None) is not False:
        raise RuntimeError("physics did not stop before fresh task reload")

    task_environment = get_task(task_class)
    if getattr(pyrep, "running", None) is not True:
        raise RuntimeError("fresh TaskEnvironment did not restart physics")
    set_variation = getattr(task_environment, "set_variation", None)
    reset = getattr(task_environment, "reset", None)
    if not callable(set_variation) or not callable(reset):
        raise RuntimeError("RLBench fresh TaskEnvironment reset API is unavailable")

    random.seed(episode_seed)
    np.random.seed(episode_seed)
    set_variation(variation)
    if not isinstance(verify_instance, bool):
        raise TypeError("fresh task-generation verification flag must be boolean")
    reset_collision_checks: list[bool] = []
    reset_scene = getattr(task_environment, "_scene", None)
    reset_robot = getattr(reset_scene, "robot", None)
    original_collision_check = getattr(reset_robot, "is_in_collision", None)
    robot_dict = getattr(reset_robot, "__dict__", {})
    had_instance_collision_check = "is_in_collision" in robot_dict
    prior_instance_collision_check = robot_dict.get("is_in_collision")
    if not verify_instance:
        if not callable(original_collision_check):
            raise RuntimeError("RLBench reset robot collision check is unavailable")

        def audited_collision_check(*args: Any, **kwargs: Any) -> bool:
            result = bool(original_collision_check(*args, **kwargs))
            reset_collision_checks.append(result)
            return result

        setattr(reset_robot, "is_in_collision", audited_collision_check)
    try:
        reset_result = reset(verify_instance=verify_instance)
    finally:
        if not verify_instance:
            if had_instance_collision_check:
                setattr(reset_robot, "is_in_collision", prior_instance_collision_check)
            else:
                delattr(reset_robot, "is_in_collision")
    if not isinstance(reset_result, tuple) or len(reset_result) < 2:
        raise RuntimeError("fresh TaskEnvironment.reset returned an invalid result")
    descriptions, observation = reset_result[0], reset_result[1]

    scene = getattr(task_environment, "_scene", None)
    task = getattr(scene, "task", None)
    if task is None or task is previous_task:
        raise RuntimeError("fresh task reload did not create a new task instance")
    get_name = getattr(task, "get_name", None)
    task_name = str(get_name()) if callable(get_name) else type(task).__name__
    generation_index = getattr(environment, "_dynamac_generation_index", 0) + 1
    setattr(environment, "_dynamac_generation_index", generation_index)
    body = {
        "schema": (
            FRESH_TASK_GENERATION_EVIDENCE_SCHEMA
            if verify_instance
            else DETERMINISTIC_SOURCE_RESET_EVIDENCE_SCHEMA
        ),
        "protocol_id": (
            FRESH_TASK_GENERATION_PROTOCOL_ID
            if verify_instance
            else DETERMINISTIC_SOURCE_RESET_PROTOCOL_ID
        ),
        "generation_index": int(generation_index),
        "episode_seed": int(episode_seed),
        "variation": int(variation),
        "task_name": task_name,
        "physics_running_before_stop": running_before_stop,
        "physics_stopped_before_task_reload": True,
        "previous_task_present": previous_task_present,
        "previous_task_unloaded_before_stop": previous_task_present,
        "previous_task_unloaded_while_physics_running": previous_task_present,
        "scene_task_absent_before_stop": True,
        "task_model_loaded_fresh": True,
        "fresh_task_python_instance_created": True,
        "task_model_only_reloaded": True,
        "base_scene_reloaded": False,
        "physics_started_by_task_environment": True,
        "rng_seeded_after_reload_immediately_before_reset": True,
        "variation_set_after_seed_before_reset": True,
        "task_environment_reset_calls": 1,
        "reset_verify_instance": verify_instance,
    }
    if not verify_instance:
        static_positions = bool(getattr(task_environment, "_static_positions", False))
        is_static_workspace = bool(task.is_static_workspace())
        body.update(
            {
                "reset_random_placement_expected": bool(
                    not static_positions and not is_static_workspace
                ),
                "reset_robot_collision_check_count": len(reset_collision_checks),
                "reset_robot_collision_check_results": reset_collision_checks,
            }
        )
    evidence = {
        **body,
        "fingerprint": _canonical_json_fingerprint(body),
    }
    setattr(task_environment, "_dynamac_fresh_generation_evidence", evidence)
    return task_environment, descriptions, observation, evidence


def _canonical_json_fingerprint(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_fresh_task_generation_evidence(
    evidence: Any,
    *,
    episode_seed: int,
    variation: int,
    task_name: str,
    verify_instance: bool = True,
) -> dict[str, Any]:

    expected_fields = {
        "schema",
        "protocol_id",
        "generation_index",
        "episode_seed",
        "variation",
        "task_name",
        "physics_running_before_stop",
        "physics_stopped_before_task_reload",
        "previous_task_present",
        "previous_task_unloaded_before_stop",
        "previous_task_unloaded_while_physics_running",
        "scene_task_absent_before_stop",
        "task_model_loaded_fresh",
        "fresh_task_python_instance_created",
        "task_model_only_reloaded",
        "base_scene_reloaded",
        "physics_started_by_task_environment",
        "rng_seeded_after_reload_immediately_before_reset",
        "variation_set_after_seed_before_reset",
        "task_environment_reset_calls",
        "reset_verify_instance",
        "fingerprint",
    }
    if not verify_instance:
        expected_fields.update(
            {
                "reset_robot_collision_check_count",
                "reset_robot_collision_check_results",
                "reset_random_placement_expected",
            }
        )
    if not isinstance(evidence, dict) or set(evidence) != expected_fields:
        raise RuntimeError("fresh task-generation evidence fields are invalid")
    body = {key: value for key, value in evidence.items() if key != "fingerprint"}
    if (
        evidence.get("schema")
        != (
            FRESH_TASK_GENERATION_EVIDENCE_SCHEMA
            if verify_instance
            else DETERMINISTIC_SOURCE_RESET_EVIDENCE_SCHEMA
        )
        or evidence.get("protocol_id")
        != (
            FRESH_TASK_GENERATION_PROTOCOL_ID
            if verify_instance
            else DETERMINISTIC_SOURCE_RESET_PROTOCOL_ID
        )
        or isinstance(evidence.get("generation_index"), bool)
        or not isinstance(evidence.get("generation_index"), int)
        or evidence["generation_index"] < 1
        or isinstance(evidence.get("episode_seed"), bool)
        or not isinstance(evidence.get("episode_seed"), int)
        or isinstance(evidence.get("variation"), bool)
        or not isinstance(evidence.get("variation"), int)
        or evidence.get("episode_seed") != episode_seed
        or evidence.get("variation") != variation
        or evidence["episode_seed"] < 0
        or evidence["variation"] < 0
        or evidence.get("task_name") != task_name
        or not isinstance(evidence.get("physics_running_before_stop"), bool)
        or evidence.get("physics_running_before_stop")
        is not evidence.get("previous_task_present")
        or evidence.get("physics_stopped_before_task_reload") is not True
        or not isinstance(evidence.get("previous_task_present"), bool)
        or evidence.get("previous_task_unloaded_before_stop")
        is not evidence.get("previous_task_present")
        or evidence.get("previous_task_unloaded_while_physics_running")
        is not evidence.get("previous_task_present")
        or evidence.get("scene_task_absent_before_stop") is not True
        or evidence.get("task_model_loaded_fresh") is not True
        or evidence.get("fresh_task_python_instance_created") is not True
        or evidence.get("task_model_only_reloaded") is not True
        or evidence.get("base_scene_reloaded") is not False
        or evidence.get("physics_started_by_task_environment") is not True
        or evidence.get("rng_seeded_after_reload_immediately_before_reset") is not True
        or evidence.get("variation_set_after_seed_before_reset") is not True
        or isinstance(evidence.get("task_environment_reset_calls"), bool)
        or not isinstance(evidence.get("task_environment_reset_calls"), int)
        or evidence.get("task_environment_reset_calls") != 1
        or evidence.get("reset_verify_instance") is not verify_instance
        or (
            not verify_instance
            and (
                isinstance(evidence.get("reset_robot_collision_check_count"), bool)
                or not isinstance(
                    evidence.get("reset_robot_collision_check_count"), int
                )
                or evidence.get("reset_robot_collision_check_count") < 0
                or not isinstance(evidence.get("reset_random_placement_expected"), bool)
                or not isinstance(
                    evidence.get("reset_robot_collision_check_results"), list
                )
                or any(
                    not isinstance(value, bool)
                    for value in evidence["reset_robot_collision_check_results"]
                )
                or evidence["reset_robot_collision_check_count"]
                != len(evidence["reset_robot_collision_check_results"])
                or evidence["reset_robot_collision_check_results"]
                != ([False] if evidence["reset_random_placement_expected"] else [])
            )
        )
        or evidence.get("fingerprint") != _canonical_json_fingerprint(body)
    ):
        raise RuntimeError("fresh task-generation evidence is invalid")
    return dict(evidence)


DEMONSTRATION_GRIPPER_ACTUATION_VELOCITY = 0.04
DEFAULT_MAX_PRIMARY_ACTION_ATTEMPTS = 1
PRIMARY_RETRY_EXHAUSTION_MODE = "current_joint_hold_commit"
POLICY_CLOCK_SEMANTICS_ID = (
    "policy-tick-single-primary-request-then-raw-joint-hold-commit-v1"
)


FROZEN_PRIMARY_RETRY_EXHAUSTION_MODE = "terminate_episode_legacy_v4"
IK_SAMPLING_TRIALS = 100
IK_SAMPLING_MAX_CONFIGS = 5
IK_SAMPLING_MAX_TIME_MS = 10
IK_JOINT_LIMIT_ATOL = 1.0e-9
GLOBAL_IK_CONTROLLER_PROFILE = "global_pseudo_trac_sampling_path_v1"
HYBRID_IK_CONTROLLER_PROFILE = "hybrid_cartesian_executor_v19"
FROZEN_V4_IK_RESOLUTION_METHOD = "pseudo_inverse"
FROZEN_V4_IK_MAX_ITERATIONS = 6
FROZEN_V4_IK_DAMPING = 0.1


class TracIKDistanceSolver(Protocol):

    chain_source: str

    def solve(self, target_pose: Array) -> Any: ...


TracIKDistanceSolverFactory = Callable[[Any], TracIKDistanceSolver]


@dataclass(frozen=True)
class GlobalIKControllerConfig:

    trac_ik_timeout_s: float = 0.03
    trac_ik_epsilon: float = 1.0e-5
    trac_ik_translation_tolerance_m: float = 0.001
    trac_ik_rotation_tolerance_rad: float = math.radians(1.0)
    trac_ik_joint_window_rad: float = 0.35
    trac_ik_fk_translation_max_m: float = 0.002
    trac_ik_fk_rotation_max_rad: float = math.radians(2.0)
    far_translation_threshold_m: float = 0.10
    max_joint_delta_abs_rad: float = 0.35
    max_joint_delta_l2_rad: float = 0.50
    planner_trials: int = 200
    planner_max_configs: int = 10
    planner_max_time_ms: int = 20
    planner_trials_per_goal: int = 1

    def __post_init__(self) -> None:
        for name in (
            "trac_ik_timeout_s",
            "trac_ik_epsilon",
            "trac_ik_translation_tolerance_m",
            "trac_ik_rotation_tolerance_rad",
            "trac_ik_joint_window_rad",
            "trac_ik_fk_translation_max_m",
            "trac_ik_fk_rotation_max_rad",
            "far_translation_threshold_m",
            "max_joint_delta_abs_rad",
            "max_joint_delta_l2_rad",
        ):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError(f"{name} must be finite and positive")
            object.__setattr__(self, name, value)
        for name in (
            "planner_trials",
            "planner_max_configs",
            "planner_max_time_ms",
            "planner_trials_per_goal",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")

    @property
    def protocol_id(self) -> str:
        return "rlbench-global-pseudo-trac-distance-sampling-path-v1"

    def metadata(self) -> dict[str, Any]:
        return {
            "profile": GLOBAL_IK_CONTROLLER_PROFILE,
            "protocol_id": self.protocol_id,
            "default_controller": True,
            "global_task_special_cases": False,
            "ik_order": (
                "current_seeded_pseudo_inverse_then_bounded_trac_ik_distance_"
                "then_frozen_v4_collision_aware_sampling_then_far_path"
            ),
            "primary_resolution_method": FROZEN_V4_IK_RESOLUTION_METHOD,
            "primary_max_iterations": FROZEN_V4_IK_MAX_ITERATIONS,
            "primary_damping": FROZEN_V4_IK_DAMPING,
            "external_fallback": "bounded_trac_ik_distance_exact_chain",
            "external_fallback_entry_condition": "pseudo_inverse_exhausted",
            "external_solver_live_exact_chain_required": True,
            "external_solver_bounded_cartesian_api_required": True,
            "trac_ik_timeout_s": self.trac_ik_timeout_s,
            "trac_ik_epsilon": self.trac_ik_epsilon,
            "trac_ik_translation_tolerance_m": (self.trac_ik_translation_tolerance_m),
            "trac_ik_rotation_tolerance_rad": (self.trac_ik_rotation_tolerance_rad),
            "trac_ik_joint_window_rad": self.trac_ik_joint_window_rad,
            "trac_ik_fk_translation_max_m": self.trac_ik_fk_translation_max_m,
            "trac_ik_fk_rotation_max_rad": self.trac_ik_fk_rotation_max_rad,
            "sampling_entry_condition": "pseudo_inverse_and_trac_ik_exhausted",
            "sampling_trials": IK_SAMPLING_TRIALS,
            "sampling_max_configs": IK_SAMPLING_MAX_CONFIGS,
            "sampling_max_time_ms": IK_SAMPLING_MAX_TIME_MS,
            "sampling_ignore_collisions": False,
            "sampling_selection": "valid_candidate_nearest_current_joint_state",
            "sampling_hard_joint_delta_rejection": False,
            "sampling_fallback": True,
            "legacy_frozen_ik_helper_used": False,
            "far_translation_threshold_m": self.far_translation_threshold_m,
            "path_entry_condition": (
                "pseudo_inverse_trac_ik_distance_and_sampling_failed_and_"
                "translation_strictly_above_threshold"
            ),
            "rotation_alone_triggers_path": False,
            "path_controller": "collision_aware_get_path_rrt_connect",
            "far_path_ignore_collisions": False,
            "prepare_all_arms_before_physics": True,
            "trac_ik_max_joint_delta_abs_rad": self.max_joint_delta_abs_rad,
            "trac_ik_max_joint_delta_l2_rad": self.max_joint_delta_l2_rad,
            "pseudo_candidate_validation": "shape_finite_and_joint_limits",
            "trac_candidate_validation": (
                "shape_finite_joint_limits_and_joint_continuity"
            ),
            "sampling_candidate_validation": "shape_finite_and_joint_limits",
            "planner_trials": self.planner_trials,
            "planner_max_configs": self.planner_max_configs,
            "planner_max_time_ms": self.planner_max_time_ms,
            "planner_trials_per_goal": self.planner_trials_per_goal,
            "ik_group_post_prepare_method": FROZEN_V4_IK_RESOLUTION_METHOD,
            "ik_group_post_prepare_max_iterations": FROZEN_V4_IK_MAX_ITERATIONS,
            "ik_group_post_prepare_damping": FROZEN_V4_IK_DAMPING,
        }


@dataclass(frozen=True)
class HybridIKControllerConfig(GlobalIKControllerConfig):

    trac_ik_timeout_s: float = 0.05
    trac_ik_translation_tolerance_m: float = 0.0005
    trac_ik_rotation_tolerance_rad: float = math.radians(0.05)
    trac_ik_fk_translation_max_m: float = 0.002
    trac_ik_fk_rotation_max_rad: float = math.radians(1.0)
    physical_completion_translation_tolerance_m: float = 0.0005
    physical_completion_rotation_tolerance_rad: float = math.radians(0.05)
    control_acceptance_translation_tolerance_m: float = 0.001
    control_acceptance_rotation_tolerance_rad: float = math.radians(0.1)
    cartesian_continuation_translation_step_m: float = 0.005
    cartesian_continuation_rotation_step_rad: float = math.radians(2.0)
    cartesian_continuation_backoff_factors: tuple[float, ...] = (
        1.0,
        0.5,
        0.25,
        0.125,
    )
    cartesian_continuation_max_segments: int = 64

    cartesian_continuation_max_raw_physics_steps: int = 64
    linear_path_steps: int = 50
    allow_collision_relaxed_linear_path: bool = True
    allow_collision_relaxed_nonlinear_path: bool = True
    allow_collision_relaxed_sampling: bool = True

    def __post_init__(self) -> None:
        super().__post_init__()
        for name in (
            "physical_completion_translation_tolerance_m",
            "physical_completion_rotation_tolerance_rad",
            "control_acceptance_translation_tolerance_m",
            "control_acceptance_rotation_tolerance_rad",
            "cartesian_continuation_translation_step_m",
            "cartesian_continuation_rotation_step_rad",
        ):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError(f"{name} must be finite and positive")
            object.__setattr__(self, name, value)
        factors = tuple(
            float(value) for value in self.cartesian_continuation_backoff_factors
        )
        if (
            not factors
            or any(
                not math.isfinite(value) or not 0.0 < value <= 1.0 for value in factors
            )
            or any(left <= right for left, right in zip(factors, factors[1:]))
        ):
            raise ValueError(
                "cartesian continuation backoff factors must be finite, "
                "positive, at most one, and strictly descending"
            )
        object.__setattr__(self, "cartesian_continuation_backoff_factors", factors)
        if (
            isinstance(self.cartesian_continuation_max_segments, bool)
            or not isinstance(self.cartesian_continuation_max_segments, int)
            or self.cartesian_continuation_max_segments < 1
        ):
            raise ValueError("cartesian_continuation_max_segments must be positive")
        if (
            isinstance(self.cartesian_continuation_max_raw_physics_steps, bool)
            or not isinstance(self.cartesian_continuation_max_raw_physics_steps, int)
            or self.cartesian_continuation_max_raw_physics_steps < 1
        ):
            raise ValueError(
                "cartesian_continuation_max_raw_physics_steps must be positive"
            )
        if (
            isinstance(self.linear_path_steps, bool)
            or not isinstance(self.linear_path_steps, int)
            or self.linear_path_steps < 2
        ):
            raise ValueError("linear_path_steps must be an integer of at least two")
        if not isinstance(self.allow_collision_relaxed_linear_path, bool):
            raise TypeError("allow_collision_relaxed_linear_path must be boolean")
        if not isinstance(self.allow_collision_relaxed_nonlinear_path, bool):
            raise TypeError("allow_collision_relaxed_nonlinear_path must be boolean")
        if not isinstance(self.allow_collision_relaxed_sampling, bool):
            raise TypeError("allow_collision_relaxed_sampling must be boolean")

    @property
    def protocol_id(self) -> str:
        return "rlbench-hybrid-hybrid-cartesian-continuation-v25"

    def metadata(self) -> dict[str, Any]:
        value = super().metadata()
        value.update(
            {
                "profile": HYBRID_IK_CONTROLLER_PROFILE,
                "protocol_id": self.protocol_id,
                "default_controller": False,
                "ik_order": (
                    "current_seeded_continuous_pseudo_inverse_then_"
                    "bounded_trac_ik_distance_"
                    "then_bounded_cartesian_continuation_"
                    "then_collision_aware_or_relaxed_sampling_"
                    "then_collision_aware_linear_then_bounded_nonlinear_path_"
                    "then_collision_relaxed_linear_then_collision_relaxed_"
                    "nonlinear_path"
                ),
                "primary_resolution_method": (
                    "current_seeded_coppeliasim_pseudo_inverse"
                ),
                "external_fallback_entry_condition": ("pseudo_inverse_exhausted"),
                "pseudo_inverse_continuity_gate": True,
                "post_execution_cartesian_verification": True,
                "cartesian_translation_tolerance_m": (
                    self.physical_completion_translation_tolerance_m
                ),
                "cartesian_rotation_tolerance_rad": (
                    self.physical_completion_rotation_tolerance_rad
                ),
                "control_acceptance_translation_tolerance_m": (
                    self.control_acceptance_translation_tolerance_m
                ),
                "control_acceptance_rotation_tolerance_rad": (
                    self.control_acceptance_rotation_tolerance_rad
                ),
                "cartesian_continuation": {
                    "entry_condition": "full_target_local_ik_exhausted",
                    "translation_step_m": (
                        self.cartesian_continuation_translation_step_m
                    ),
                    "rotation_step_rad": (
                        self.cartesian_continuation_rotation_step_rad
                    ),
                    "backoff_factors": list(
                        self.cartesian_continuation_backoff_factors
                    ),
                    "max_segments_per_policy_action": (
                        self.cartesian_continuation_max_segments
                    ),
                    "max_raw_physics_steps_per_policy_action": (
                        self.cartesian_continuation_max_raw_physics_steps
                    ),
                    "interpolation": "linear_translation_shortest_xyzw_slerp",
                    "progress_feedback": ("physical_pose_reobserved_between_segments"),
                    "commit_semantics": (
                        "reach_or_report_bounded_progress_for_closed_loop_reobservation"
                    ),
                    "task_specific": False,
                },
                "path_fallback": {
                    "order": (
                        "collision_aware_linear_then_collision_aware_rrt_connect_"
                        "after_measured_stall_or_for_far_targets_then_"
                        "collision_relaxed_linear_then_collision_relaxed_"
                        "rrt_connect"
                    ),
                    "linear_steps": self.linear_path_steps,
                    "collision_relaxed_linear_enabled": (
                        self.allow_collision_relaxed_linear_path
                    ),
                    "collision_relaxed_nonlinear_enabled": (
                        self.allow_collision_relaxed_nonlinear_path
                    ),
                    "nonlinear_minimum_translation_m": (
                        self.far_translation_threshold_m
                    ),
                    "near_nonlinear_entry_condition": (
                        "same_target_measured_stall_exhausted_local_solver_tiers"
                    ),
                    "motion_handle_lifetime": (
                        "released_when_complete_or_before_bounded_controller_return"
                    ),
                    "task_specific": False,
                },
                "sampling_collision_policy": (
                    "collision_aware_then_collision_relaxed"
                    if self.allow_collision_relaxed_sampling
                    else "collision_aware_only"
                ),
                "stopped_resolution": "report_observed_stall_to_closed_loop",
                "physical_stall_resolution": (
                    "same_target_bounded_solver_escalation_then_report_stall"
                ),
                "fallback_collision_policy": (
                    "collision_aware_first_then_bounded_relaxation"
                ),
                "same_target_alternate_solver_after_primary_stall": True,
                "same_target_solver_tier_persistence": (
                    "per_arm_exact_target_across_closed_loop_cycles"
                ),
                "same_target_solver_exhaustion_memory": (
                    "reuse_final_exhaustion_while_target_is_solver_equivalent_"
                    "and_tip_joints_are_unchanged"
                ),
                "physical_stall_solver_order": (
                    "pseudo_inverse_then_trac_ik_distance_then_"
                    "cartesian_continuation_then_sampling_then_"
                    "collision_aware_linear_path_then_collision_aware_"
                    "nonlinear_path_then_collision_relaxed_linear_path"
                ),
                "physical_stall_sampling_candidate_validation": (
                    "shape_finite_joint_limits_and_joint_continuity"
                ),
                "unreported_hidden_motion_after_physical_stall": False,
                "joint_target_execution": (
                    "shared_clock_joint_target_until_reached_or_stopped"
                ),
                "same_target_ik_solution_cache": True,
                "same_target_ik_solution_cache_semantics": (
                    "reuse_only_after_measured_cartesian_progress_and_"
                    "invalidate_before_bounded_stall_escalation"
                ),
            }
        )
        return value


CONTROLLER_METADATA_SCHEMA = "dynamac-global-absolute-ee-controller-v1"


def global_ik_controller_metadata(
    config: GlobalIKControllerConfig | None = None,
) -> dict[str, Any]:

    config = config or GlobalIKControllerConfig()
    metadata = {
        "schema": CONTROLLER_METADATA_SCHEMA,
        "profile": (
            HYBRID_IK_CONTROLLER_PROFILE
            if isinstance(config, HybridIKControllerConfig)
            else GLOBAL_IK_CONTROLLER_PROFILE
        ),
        "protocol_id": config.protocol_id,
        "command": "absolute_world_end_effector_pose",
        "ik_order": config.metadata()["ik_order"],
        "primary_ik": config.metadata()["primary_resolution_method"],
        "primary_ik_parameters": {
            "max_iterations": FROZEN_V4_IK_MAX_ITERATIONS,
            "damping": FROZEN_V4_IK_DAMPING,
        },
        "bounded_trac_ik": {
            "solve_type": "Distance",
            "timeout_s": config.trac_ik_timeout_s,
            "epsilon": config.trac_ik_epsilon,
            "cartesian_translation_tolerance_m": (
                config.trac_ik_translation_tolerance_m
            ),
            "cartesian_rotation_tolerance_rad": (config.trac_ik_rotation_tolerance_rad),
            "joint_window_rad": config.trac_ik_joint_window_rad,
            "fk_translation_residual_max_m": (config.trac_ik_fk_translation_max_m),
            "fk_rotation_residual_max_rad": config.trac_ik_fk_rotation_max_rad,
            "exact_live_coppeliasim_chain_required": True,
            "exact_live_chain_schema": "coppeliasim-moving-frame-panda-chain-v1",
            "exact_live_chain_source": "live_coppeliasim_moving_frame_segments",
            "bounded_cartesian_api_required": True,
            "joint_delta_abs_max_rad": config.max_joint_delta_abs_rad,
            "joint_delta_l2_max_rad": config.max_joint_delta_l2_rad,
            "dependency": pytracik_dependency_identity(),
        },
        "sampling_ik": {
            "trials": IK_SAMPLING_TRIALS,
            "max_configs": IK_SAMPLING_MAX_CONFIGS,
            "max_time_ms": IK_SAMPLING_MAX_TIME_MS,
            "ignore_collisions": False,
            "selection": "nearest_current_joint_l2",
            "candidate_validation": "shape_finite_noncyclic_joint_limits",
            "hard_joint_delta_rejection": False,
        },
        "far_path": {
            "translation_threshold_m": config.far_translation_threshold_m,
            "entry_condition": "all_three_ik_methods_failed_and_strictly_far",
            "ignore_collisions": False,
            "trials": config.planner_trials,
            "max_configs": config.planner_max_configs,
            "max_time_ms": config.planner_max_time_ms,
            "trials_per_goal": config.planner_trials_per_goal,
        },
        "prepare_all_arms_before_physics": True,
        "joint_target_max_steps": 200,
        "primary_action_requests_per_policy_tick": 1,
        "primary_failure_resolution": (
            "one_raw_current_joint_hold_then_commit_same_transaction"
        ),
        "policy_clock_semantics_id": POLICY_CLOCK_SEMANTICS_ID,
        "horizon_domain": "committed_policy_ticks",
        "legacy_v4_frozen_profile_used": False,
    }
    if isinstance(config, HybridIKControllerConfig):
        metadata.update(
            {
                "post_execution_cartesian_verification": True,
                "cartesian_translation_tolerance_m": (
                    config.physical_completion_translation_tolerance_m
                ),
                "cartesian_rotation_tolerance_rad": (
                    config.physical_completion_rotation_tolerance_rad
                ),
                "control_acceptance_translation_tolerance_m": (
                    config.control_acceptance_translation_tolerance_m
                ),
                "control_acceptance_rotation_tolerance_rad": (
                    config.control_acceptance_rotation_tolerance_rad
                ),
                "stopped_resolution": "report_observed_stall_to_closed_loop",
                "fallback_collision_policy": (
                    "collision_aware_first_then_bounded_relaxation"
                ),
                "cartesian_continuation": config.metadata()["cartesian_continuation"],
                "path_fallback": config.metadata()["path_fallback"],
                "sampling_collision_policy": config.metadata()[
                    "sampling_collision_policy"
                ],
                "same_target_alternate_solver_after_primary_stall": True,
                "same_target_solver_tier_persistence": config.metadata()[
                    "same_target_solver_tier_persistence"
                ],
                "same_target_solver_exhaustion_memory": config.metadata()[
                    "same_target_solver_exhaustion_memory"
                ],
                "physical_stall_resolution": (
                    "same_target_bounded_solver_escalation_then_report_stall"
                ),
                "physical_stall_solver_order": (
                    "pseudo_inverse_then_trac_ik_distance_then_"
                    "cartesian_continuation_then_sampling_then_"
                    "collision_aware_linear_path_then_collision_aware_"
                    "nonlinear_path_then_collision_relaxed_linear_path"
                ),
                "physical_stall_sampling_candidate_validation": (
                    "shape_finite_joint_limits_and_joint_continuity"
                ),
                "unreported_hidden_motion_after_physical_stall": False,
                "joint_target_execution": (
                    "shared_clock_joint_target_until_reached_or_stopped"
                ),
                "same_target_ik_solution_cache": True,
                "same_target_ik_solution_cache_semantics": config.metadata()[
                    "same_target_ik_solution_cache_semantics"
                ],
            }
        )
    return metadata


@dataclass(frozen=True)
class PreparedEECommand:
    arm: Any
    target_pose: Array
    mode: Literal["joint_target", "planned_path"]
    target_joints: Array | None = None
    path: Any = None

    def __post_init__(self) -> None:
        pose = np.asarray(self.target_pose, dtype=np.float64)
        if pose.shape != (7,) or not np.all(np.isfinite(pose)):
            raise ValueError("prepared EE target pose must be finite shape (7,)")
        object.__setattr__(self, "target_pose", pose.copy())
        if self.mode == "joint_target":
            joints = np.asarray(self.target_joints, dtype=np.float64)
            if joints.ndim != 1 or not np.all(np.isfinite(joints)):
                raise ValueError("prepared joint target must be a finite vector")
            if self.path is not None:
                raise ValueError("joint-target command cannot contain a path")
            object.__setattr__(self, "target_joints", joints.copy())
        elif self.mode == "planned_path":
            if self.path is None or self.target_joints is not None:
                raise ValueError("planned-path command must contain only a path")
            if not callable(getattr(self.path, "step", None)):
                raise ValueError("planned-path command requires a step API")
        else:
            raise ValueError("unsupported prepared EE command mode")


def _release_configuration_path_motion_handle(
    path: Any,
    *,
    remover: Callable[[int], Any] | None = None,
) -> bool:

    handle = getattr(path, "_rml_handle", None)
    if handle is None or bool(getattr(path, "_path_done", False)):
        return False
    if remover is None:
        from pyrep.backend import sim

        remover = sim.simRMLRemove
    remover(int(handle))

    setattr(path, "_rml_handle", None)
    setattr(path, "_path_done", True)
    return True


_HYBRID_JOINT_CACHE_ATTRIBUTE = "_dynamac_hybrid_joint_target_cache_v1"
_HYBRID_SOLVER_TIER_ATTRIBUTE = "_dynamac_hybrid_solver_tier_v1"
_HYBRID_SOLVER_TIER_COUNT = 8


_HYBRID_SOLVER_EXHAUSTION_JOINT_ATOL_RAD = 0.001


def _same_hybrid_cartesian_target(left: Array, right: Array) -> bool:
    translation, rotation = end_effector_pose_distance(left, right)
    return bool(translation <= 1.0e-10 and rotation <= 1.0e-10)


def _cached_hybrid_joint_command(
    arm: Any,
    target: Array,
    *,
    current: Array,
    limits: tuple[tuple[bool, float, float], ...],
    config: HybridIKControllerConfig,
    diagnostics: dict[str, Any],
) -> PreparedEECommand | None:
    cached = getattr(arm, _HYBRID_JOINT_CACHE_ATTRIBUTE, None)
    if not isinstance(cached, dict):
        _increment_ik_diagnostic(diagnostics, "same_target_joint_cache_misses")
        return None
    cached_pose = np.asarray(cached.get("target_pose"), dtype=np.float64)
    if (
        cached_pose.shape != (7,)
        or not np.all(np.isfinite(cached_pose))
        or not _same_hybrid_cartesian_target(cached_pose, target)
    ):
        _increment_ik_diagnostic(diagnostics, "same_target_joint_cache_misses")
        return None
    candidate, rejection = _validated_continuous_ik_candidate(
        cached.get("target_joints"),
        current_joints=current,
        limits=limits,
        config=config,
    )
    if candidate is None:
        _increment_ik_diagnostic(diagnostics, "same_target_joint_cache_rejections")
        _record_ik_candidate_rejection(diagnostics, str(rejection))
        try:
            delattr(arm, _HYBRID_JOINT_CACHE_ATTRIBUTE)
        except AttributeError:
            pass
        return None
    _increment_ik_diagnostic(diagnostics, "same_target_joint_cache_hits")
    _record_selected_joint_delta(diagnostics, current, candidate)
    return PreparedEECommand(
        arm=arm,
        target_pose=target,
        mode="joint_target",
        target_joints=candidate,
    )


def _store_hybrid_joint_command(command: PreparedEECommand, target: Array) -> None:
    if (
        command.mode != "joint_target"
        or command.target_joints is None
        or not _same_hybrid_cartesian_target(command.target_pose, target)
    ):
        return
    setattr(
        command.arm,
        _HYBRID_JOINT_CACHE_ATTRIBUTE,
        {
            "target_pose": np.asarray(target, dtype=np.float64).copy(),
            "target_joints": command.target_joints.copy(),
        },
    )


def _invalidate_hybrid_joint_command(arm: Any, target: Array) -> bool:
    cached = getattr(arm, _HYBRID_JOINT_CACHE_ATTRIBUTE, None)
    if not isinstance(cached, dict):
        return False
    cached_pose = np.asarray(cached.get("target_pose"), dtype=np.float64)
    if (
        cached_pose.shape != (7,)
        or not np.all(np.isfinite(cached_pose))
        or not _same_hybrid_cartesian_target(cached_pose, target)
    ):
        return False
    delattr(arm, _HYBRID_JOINT_CACHE_ATTRIBUTE)
    return True


def _hybrid_same_target_solver_tier(
    arm: Any,
    target: Array,
    *,
    diagnostics: dict[str, Any],
) -> int:

    state = getattr(arm, _HYBRID_SOLVER_TIER_ATTRIBUTE, None)
    if isinstance(state, dict):
        cached_pose = np.asarray(state.get("target_pose"), dtype=np.float64)
        tier = state.get("minimum_solver_tier")
        if (
            cached_pose.shape == (7,)
            and np.all(np.isfinite(cached_pose))
            and _same_hybrid_cartesian_target(cached_pose, target)
            and isinstance(tier, int)
            and not isinstance(tier, bool)
            and tier in range(_HYBRID_SOLVER_TIER_COUNT)
        ):
            _increment_ik_diagnostic(diagnostics, "same_target_solver_tier_cache_hits")
            if tier > 0:
                _increment_ik_diagnostic(
                    diagnostics, "same_target_cross_cycle_solver_resumes"
                )
            return int(tier)
    _increment_ik_diagnostic(diagnostics, "same_target_solver_tier_cache_misses")
    setattr(
        arm,
        _HYBRID_SOLVER_TIER_ATTRIBUTE,
        {
            "target_pose": np.asarray(target, dtype=np.float64).copy(),
            "minimum_solver_tier": 0,
        },
    )
    return 0


def _store_hybrid_same_target_solver_tier(
    arm: Any,
    target: Array,
    tier: int,
) -> None:
    if isinstance(tier, bool) or tier not in range(_HYBRID_SOLVER_TIER_COUNT):
        raise ValueError("hybrid solver tier must be an integer in [0, 7]")
    setattr(
        arm,
        _HYBRID_SOLVER_TIER_ATTRIBUTE,
        {
            "target_pose": np.asarray(target, dtype=np.float64).copy(),
            "minimum_solver_tier": int(tier),
        },
    )


def _clear_hybrid_same_target_solver_tier(arm: Any) -> None:
    try:
        delattr(arm, _HYBRID_SOLVER_TIER_ATTRIBUTE)
    except AttributeError:
        pass


def _store_hybrid_same_target_solver_exhaustion(arm: Any, target: Array) -> None:

    joints = np.asarray(arm.get_joint_positions(), dtype=np.float64)
    tip_pose = _arm_tip_pose(arm)
    if (
        joints.ndim != 1
        or joints.size == 0
        or not np.all(np.isfinite(joints))
        or tip_pose.shape != (7,)
        or not np.all(np.isfinite(tip_pose))
    ):
        return
    setattr(
        arm,
        _HYBRID_SOLVER_TIER_ATTRIBUTE,
        {
            "target_pose": np.asarray(target, dtype=np.float64).copy(),
            "minimum_solver_tier": _HYBRID_SOLVER_TIER_COUNT - 1,
            "exhausted_joint_positions": joints.copy(),
            "exhausted_tip_pose": tip_pose.copy(),
        },
    )


def _hybrid_same_target_solver_exhaustion_state(
    arm: Any,
    target: Array,
    *,
    config: HybridIKControllerConfig,
) -> Literal["absent", "unchanged", "changed"]:

    state = getattr(arm, _HYBRID_SOLVER_TIER_ATTRIBUTE, None)
    if not isinstance(state, dict) or "exhausted_joint_positions" not in state:
        return "absent"
    cached_target = np.asarray(state.get("target_pose"), dtype=np.float64)
    cached_joints = np.asarray(state.get("exhausted_joint_positions"), dtype=np.float64)
    cached_tip = np.asarray(state.get("exhausted_tip_pose"), dtype=np.float64)
    current_joints = np.asarray(arm.get_joint_positions(), dtype=np.float64)
    current_tip = _arm_tip_pose(arm)
    valid = bool(
        cached_target.shape == (7,)
        and cached_tip.shape == (7,)
        and cached_joints.ndim == 1
        and cached_joints.size > 0
        and current_joints.shape == cached_joints.shape
        and current_tip.shape == (7,)
        and np.all(np.isfinite(cached_target))
        and np.all(np.isfinite(cached_tip))
        and np.all(np.isfinite(cached_joints))
        and np.all(np.isfinite(current_joints))
        and np.all(np.isfinite(current_tip))
    )
    target_translation, target_rotation = (
        end_effector_pose_distance(cached_target, target)
        if valid
        else (math.inf, math.inf)
    )
    if (
        not valid
        or target_translation > config.physical_completion_translation_tolerance_m
        or target_rotation > config.physical_completion_rotation_tolerance_rad
    ):
        return "changed"
    tip_translation, tip_rotation = end_effector_pose_distance(cached_tip, current_tip)
    unchanged = bool(
        np.allclose(
            current_joints,
            cached_joints,
            rtol=0.0,
            atol=_HYBRID_SOLVER_EXHAUSTION_JOINT_ATOL_RAD,
        )
        and tip_translation <= config.physical_completion_translation_tolerance_m
        and tip_rotation <= config.physical_completion_rotation_tolerance_rad
    )
    return "unchanged" if unchanged else "changed"


@dataclass
class PrimaryActionRetryBudget:

    max_attempts: int = DEFAULT_MAX_PRIMARY_ACTION_ATTEMPTS
    attempts: int = 0
    peak_attempts: int = 0

    def __post_init__(self) -> None:
        if isinstance(self.max_attempts, bool) or not isinstance(
            self.max_attempts, int
        ):
            raise TypeError("max primary action attempts must be an integer")
        if self.max_attempts < 1:
            raise ValueError("max primary action attempts must be positive")

    def record_failure(self) -> bool:

        self.attempts += 1
        self.peak_attempts = max(self.peak_attempts, self.attempts)
        return self.attempts >= self.max_attempts

    def record_success(self) -> None:

        self.attempts = 0

    def metadata(self) -> dict[str, Any]:
        return {
            "max_primary_action_attempts_per_policy_tick": self.max_attempts,
            "exhaustion_reason": "primary_action_retry_exhausted",
            "counter_reset": "after_successful_primary_action_commit",
        }


def validate_primary_retry_exhaustion_mode(mode: str) -> str:

    if mode not in {
        PRIMARY_RETRY_EXHAUSTION_MODE,
        FROZEN_PRIMARY_RETRY_EXHAUSTION_MODE,
    }:
        raise ValueError("unsupported primary-action retry exhaustion mode")
    return mode


def step_current_joint_hold_noop(
    task_environment: Any,
) -> tuple[Any, Any, Any]:

    if getattr(task_environment, "_reset_called", True) is False:
        raise RuntimeError("Call 'reset' before advancing a joint-hold no-op.")
    scene = getattr(task_environment, "_scene", None)
    robot = getattr(scene, "robot", None)
    task = getattr(scene, "task", None)
    step_scene = getattr(scene, "step", None)
    success_fn = getattr(task, "success", None)
    get_observation = getattr(task_environment, "get_observation", None)
    if (
        robot is None
        or not callable(step_scene)
        or not callable(success_fn)
        or not callable(get_observation)
    ):
        raise RuntimeError("joint-hold no-op requires a live RLBench task scene")

    if bool(getattr(robot, "is_bimanual", False)):
        components = (
            getattr(robot, "right_arm", None),
            getattr(robot, "right_gripper", None),
            getattr(robot, "left_arm", None),
            getattr(robot, "left_gripper", None),
        )
    else:
        components = (
            getattr(robot, "arm", None),
            getattr(robot, "gripper", None),
        )

    snapshots: list[tuple[Any, Array]] = []
    for component in components:
        get_positions = getattr(component, "get_joint_positions", None)
        set_targets = getattr(component, "set_joint_target_positions", None)
        if not callable(get_positions) or not callable(set_targets):
            raise RuntimeError("robot component lacks joint-hold APIs")
        positions = np.asarray(get_positions(), dtype=np.float64)
        if (
            positions.ndim != 1
            or positions.size == 0
            or not np.all(np.isfinite(positions))
        ):
            raise RuntimeError("robot component returned invalid joint positions")
        snapshots.append((component, positions.copy()))

    for component, positions in snapshots:
        component.set_joint_target_positions(positions.tolist())
    step_scene()

    success, terminate = success_fn()
    reward: Any = float(success)
    if bool(getattr(task_environment, "_shaped_rewards", False)):
        reward_fn = getattr(task, "reward", None)
        if not callable(reward_fn):
            raise RuntimeError(
                "shaped rewards requested but task.reward is unavailable"
            )
        reward = reward_fn()
        if reward is None:
            raise RuntimeError(
                "User requested shaped rewards, but the task returned no reward."
            )
    observation = get_observation()
    return observation, reward, terminate


def policy_action_execution_status(task_environment: Any) -> str:

    action_mode = getattr(task_environment, "_action_mode", None)
    arm_action_mode = getattr(action_mode, "arm_action_mode", None)
    status_fn = getattr(arm_action_mode, "policy_action_status", None)
    status = "reached" if not callable(status_fn) else status_fn()
    if status not in {"reached", "progressed", "stopped"}:
        raise RuntimeError(f"unsupported policy action execution status: {status!r}")
    return str(status)


def policy_action_execution_statuses(task_environment: Any) -> dict[str, str]:

    action_mode = getattr(task_environment, "_action_mode", None)
    arm_action_mode = getattr(action_mode, "arm_action_mode", None)
    statuses_fn = getattr(arm_action_mode, "policy_action_statuses", None)
    if callable(statuses_fn):
        statuses = statuses_fn()
        if not isinstance(statuses, dict) or not statuses:
            raise RuntimeError("policy arm action statuses must be a non-empty dict")
    else:
        statuses = {"single": policy_action_execution_status(task_environment)}
    result = {}
    for arm, status in statuses.items():
        if not isinstance(arm, str) or not arm:
            raise RuntimeError("policy arm action status keys must be arm names")
        if status not in {"reached", "progressed", "stopped"}:
            raise RuntimeError(f"unsupported per-arm policy action status: {status!r}")
        result[arm] = str(status)
    return result


def apply_gripper_for_policy_target(
    gripper_action_mode: Any,
    scene: Any,
    gripper_action: Array,
    *,
    arm_status: str,
    gripper_authorized: bool | None = None,
) -> bool:

    if arm_status not in {"reached", "progressed", "stopped"}:
        raise ValueError(f"unsupported arm execution status: {arm_status!r}")
    if gripper_authorized is not None and not isinstance(
        gripper_authorized, (bool, np.bool_)
    ):
        raise TypeError("gripper_authorized must be Boolean or None")
    apply = bool(
        gripper_authorized is True
        or (gripper_authorized is None and arm_status == "reached")
    )
    if not apply:
        return False
    gripper_action_mode.action(scene, gripper_action)
    return True


def set_policy_gripper_authorization(
    task_environment: Any,
    authorization: Mapping[str, bool | None] | None,
) -> None:

    action_mode = getattr(task_environment, "_action_mode", None)
    setter = getattr(action_mode, "set_policy_gripper_authorization", None)
    if callable(setter):
        setter(authorization)
        return
    if authorization is not None and any(
        value is not None for value in authorization.values()
    ):
        raise RuntimeError("action mode cannot consume policy gripper authorization")


def commit_joint_hold_after_primary_failure(
    task_environment: Any,
    worker: Any,
    *,
    transaction_id: int,
) -> tuple[Any, Any, Any, bool]:

    try:
        observation, reward, terminate = step_current_joint_hold_noop(task_environment)
    except Exception:
        worker.request("abort", transaction_id=transaction_id)
        raise
    try:
        commit = worker.request(
            "commit",
            transaction_id=transaction_id,
            primary_action_status="stopped",
            primary_action_applied=False,
        )
    except Exception:

        try:
            worker.request("abort", transaction_id=transaction_id)
        except Exception:
            pass
        raise
    return observation, reward, terminate, bool(commit.get("complete"))


def initialize_ik_solver_diagnostics() -> dict[str, Any]:

    return {
        "jacobian_failures": 0,
        "jacobian_candidate_rejections": 0,
        "sampling_fallback_successes": 0,
        "sampling_fallback_failures": 0,
        "sampling_candidates_evaluated": 0,
        "candidate_rejections_nonfinite": 0,
        "candidate_rejections_joint_limits": 0,
        "candidate_rejections_shape": 0,
        "selected_via_jacobian": 0,
        "selected_via_sampling": 0,
        "selected_joint_delta_l2_max": 0.0,
    }


def initialize_global_ik_controller_diagnostics() -> dict[str, Any]:

    diagnostics = initialize_ik_solver_diagnostics()
    diagnostics.update(
        {
            "selected_joint_delta_abs_max": 0.0,
            "target_pose_count": 0,
            "target_translation_m_max": 0.0,
            "target_rotation_rad_max": 0.0,
            "pseudo_inverse_ik_attempts": 0,
            "pseudo_inverse_ik_successes": 0,
            "pseudo_inverse_ik_failures": 0,
            "pseudo_inverse_ik_candidate_rejections": 0,
            "selected_via_pseudo_inverse": 0,
            "trac_ik_distance_attempts": 0,
            "trac_ik_distance_successes": 0,
            "trac_ik_distance_failures": 0,
            "trac_ik_distance_factory_failures": 0,
            "trac_ik_distance_candidate_rejections": 0,
            "trac_ik_distance_solve_time_ms_total": 0.0,
            "trac_ik_distance_solve_time_ms_max": 0.0,
            "trac_ik_distance_reported_solve_time_ms_total": 0.0,
            "trac_ik_distance_reported_solve_time_ms_max": 0.0,
            "trac_ik_distance_chain_sources": [],
            "trac_ik_distance_chain_schemas": [],
            "trac_ik_distance_chain_source_missing": 0,
            "trac_ik_distance_chain_schema_missing": 0,
            "trac_ik_distance_bounded_cartesian_api_uses": 0,
            "trac_ik_distance_unbounded_cartesian_api_uses": 0,
            "trac_ik_distance_result_metadata_missing": 0,
            "trac_ik_distance_fk_translation_error_m_max": 0.0,
            "trac_ik_distance_fk_rotation_error_rad_max": 0.0,
            "trac_ik_distance_exhaustions": 0,
            "sampling_after_trac_attempts": 0,
            "sampling_after_trac_successes": 0,
            "sampling_after_trac_failures": 0,
            "sampling_collision_relaxed_attempts": 0,
            "sampling_collision_relaxed_successes": 0,
            "sampling_collision_relaxed_failures": 0,
            "selected_via_collision_relaxed_sampling": 0,
            "candidate_rejections_joint_delta_abs": 0,
            "candidate_rejections_joint_delta_l2": 0,
            "selected_via_trac_ik_distance": 0,
            "all_ik_exhaustions": 0,
            "path_after_all_ik_exhaustion": 0,
            "far_target_planner_attempts": 0,
            "far_target_planner_successes": 0,
            "far_target_planner_failures": 0,
            "nonlinear_path_attempts": 0,
            "nonlinear_path_successes": 0,
            "nonlinear_path_failures": 0,
            "near_target_nonlinear_planner_attempts": 0,
            "linear_path_attempts": 0,
            "linear_path_collision_aware_successes": 0,
            "linear_path_collision_aware_failures": 0,
            "linear_path_collision_relaxed_attempts": 0,
            "linear_path_collision_relaxed_successes": 0,
            "linear_path_collision_relaxed_failures": 0,
            "nonlinear_path_collision_relaxed_attempts": 0,
            "nonlinear_path_collision_relaxed_successes": 0,
            "nonlinear_path_collision_relaxed_failures": 0,
            "near_target_nonlinear_planner_skips": 0,
            "commands_prepared_before_physics": 0,
            "ik_group_baseline_restore_attempts": 0,
            "ik_group_baseline_restore_failures": 0,
            "trac_ik_distance_controller_invalid_actions": 0,
            "trac_ik_distance_controller_actions": 0,
            "trac_ik_distance_controller_raw_physics_steps": 0,
            "trac_ik_distance_controller_raw_physics_steps_max": 0,
            "cartesian_verification_checks": 0,
            "cartesian_verification_failures": 0,
            "cartesian_post_translation_error_m_max": 0.0,
            "cartesian_post_rotation_error_rad_max": 0.0,
            "stopped_with_cartesian_residual": 0,
            "reached_joint_target_with_cartesian_residual": 0,
            "controller_raw_physics_budget_exhaustions": 0,
            "cartesian_direct_goal_attempts": 0,
            "cartesian_direct_goal_reaches": 0,
            "cartesian_direct_goal_control_accepts": 0,
            "cartesian_direct_goal_progress_accepts": 0,
            "cartesian_direct_goal_stalls": 0,
            "physical_stall_solver_escalations": 0,
            "physical_stall_solver_escalation_exhaustions": 0,
            "physical_stall_solver_tier_max": 0,
            "same_target_solver_tier_cache_hits": 0,
            "same_target_solver_tier_cache_misses": 0,
            "same_target_cross_cycle_solver_resumes": 0,
            "same_target_cross_cycle_solver_escalations": 0,
            "same_target_solver_tier_resets_after_progress": 0,
            "same_target_solver_exhaustion_records": 0,
            "same_target_solver_exhaustion_fast_returns": 0,
            "same_target_solver_exhaustion_reopens": 0,
            "same_target_joint_cache_hits": 0,
            "same_target_joint_cache_misses": 0,
            "same_target_joint_cache_rejections": 0,
            "same_target_joint_cache_stores": 0,
            "same_target_joint_cache_invalidations": 0,
            "cartesian_goal_directed_progress_accepts": 0,
            "cartesian_partial_arm_progress_accepts": 0,
            "cartesian_continuation_attempts": 0,
            "cartesian_continuation_successes": 0,
            "cartesian_continuation_failures": 0,
            "selected_via_cartesian_continuation": 0,
            "cartesian_continuation_fraction_min": 1.0,
            "cartesian_multi_pass_followup_passes": 0,
            "cartesian_multi_pass_goals_completed": 0,
            "cartesian_multi_pass_limit_exhaustions": 0,
            "cartesian_multi_pass_solver_exhaustions_after_progress": 0,
            "planned_path_motion_handle_releases": 0,
            "planned_path_motion_handle_release_failures": 0,
        }
    )
    return diagnostics


def _ensure_global_ik_controller_diagnostics(
    diagnostics: dict[str, Any],
) -> None:
    for key, value in initialize_global_ik_controller_diagnostics().items():
        diagnostics.setdefault(key, value)


def _increment_ik_diagnostic(
    diagnostics: dict[str, Any], key: str, amount: int = 1
) -> None:
    diagnostics[key] = int(diagnostics.get(key, 0)) + amount


def _record_solver_duration_ms(
    diagnostics: dict[str, Any], prefix: str, started_at: float
) -> None:
    duration_ms = max(0.0, (time.perf_counter() - started_at) * 1000.0)
    total_key = f"{prefix}_solve_time_ms_total"
    max_key = f"{prefix}_solve_time_ms_max"
    diagnostics[total_key] = float(diagnostics.get(total_key, 0.0)) + duration_ms
    diagnostics[max_key] = max(float(diagnostics.get(max_key, 0.0)), duration_ms)


def _record_ik_candidate_rejection(diagnostics: dict[str, Any], reason: str) -> None:
    _increment_ik_diagnostic(diagnostics, f"candidate_rejections_{reason}")


def end_effector_pose_distance(
    current_pose: Any, target_pose: Any
) -> tuple[float, float]:

    current = np.asarray(current_pose, dtype=np.float64)
    target = np.asarray(target_pose, dtype=np.float64)
    if (
        current.shape != (7,)
        or target.shape != (7,)
        or not np.all(np.isfinite(current))
        or not np.all(np.isfinite(target))
    ):
        raise ValueError("end-effector poses must be finite vectors of shape (7,)")
    current_norm = float(np.linalg.norm(current[3:]))
    target_norm = float(np.linalg.norm(target[3:]))
    if current_norm <= 1.0e-12 or target_norm <= 1.0e-12:
        raise ValueError("end-effector quaternion norm must be positive")
    dot = abs(float(np.dot(current[3:], target[3:]) / (current_norm * target_norm)))
    rotation = 2.0 * math.acos(max(-1.0, min(1.0, dot)))
    return float(np.linalg.norm(target[:3] - current[:3])), rotation


def _record_target_pose_diagnostic(
    diagnostics: dict[str, Any], current_pose: Any, target_pose: Any
) -> tuple[float, float]:
    translation, rotation = end_effector_pose_distance(current_pose, target_pose)
    _increment_ik_diagnostic(diagnostics, "target_pose_count")
    diagnostics["target_translation_m_max"] = max(
        float(diagnostics.get("target_translation_m_max", 0.0)), translation
    )
    diagnostics["target_rotation_rad_max"] = max(
        float(diagnostics.get("target_rotation_rad_max", 0.0)), rotation
    )
    return translation, rotation


def _joint_limit_specification(
    arm: Any, current_joints: Array
) -> tuple[tuple[bool, float, float], ...]:
    cyclics, intervals = arm.get_joint_intervals()
    if len(cyclics) != current_joints.size or len(intervals) != current_joints.size:
        raise ValueError("joint interval count does not match current joint state")

    limits = []
    for cyclic, interval in zip(cyclics, intervals):
        if bool(cyclic):
            limits.append((True, -math.inf, math.inf))
            continue
        values = np.asarray(interval, dtype=np.float64)
        if values.shape != (2,) or not np.all(np.isfinite(values)):
            raise ValueError("non-cyclic joint interval must be two finite values")
        lower = float(values[0])
        span = float(values[1])
        upper = lower + span
        if span < 0.0 or not math.isfinite(upper):
            raise ValueError("non-cyclic joint interval range must be non-negative")
        limits.append((False, lower, upper))
    return tuple(limits)


def _validated_ik_candidate(
    candidate: Any,
    *,
    current_joints: Array,
    limits: tuple[tuple[bool, float, float], ...],
) -> tuple[Array | None, str | None]:
    try:
        joints = np.asarray(candidate, dtype=np.float64)
    except (TypeError, ValueError):
        return None, "shape"
    if joints.shape != current_joints.shape:
        return None, "shape"
    if not np.all(np.isfinite(joints)):
        return None, "nonfinite"
    for value, (cyclic, lower, upper) in zip(joints, limits):
        if cyclic:
            continue
        if value < lower - IK_JOINT_LIMIT_ATOL or value > upper + IK_JOINT_LIMIT_ATOL:
            return None, "joint_limits"
    return joints.copy(), None


def solve_absolute_ee_ik_with_sampling_fallback(
    arm: Any,
    target: Array,
    *,
    diagnostics: dict[str, Any],
    ik_error: type[Exception],
    configuration_error: type[Exception],
    invalid_action_error: type[Exception],
    error_message: str,
) -> Array:

    target = np.asarray(target, dtype=np.float64)
    current_joints = np.asarray(arm.get_joint_positions(), dtype=np.float64)
    if current_joints.ndim != 1 or not np.all(np.isfinite(current_joints)):
        raise invalid_action_error(error_message)
    try:
        limits = _joint_limit_specification(arm, current_joints)
    except (TypeError, ValueError) as exc:
        raise invalid_action_error(error_message) from exc

    try:
        jacobian_result = arm.solve_ik_via_jacobian(
            target[:3], quaternion=target[3:], relative_to=None
        )
    except ik_error:
        _increment_ik_diagnostic(diagnostics, "jacobian_failures")
    else:
        candidate, rejection = _validated_ik_candidate(
            jacobian_result,
            current_joints=current_joints,
            limits=limits,
        )
        if candidate is not None:
            _increment_ik_diagnostic(diagnostics, "selected_via_jacobian")
            delta = float(np.linalg.norm(candidate - current_joints))
            diagnostics["selected_joint_delta_l2_max"] = max(
                float(diagnostics.get("selected_joint_delta_l2_max", 0.0)),
                delta,
            )
            return candidate
        _increment_ik_diagnostic(diagnostics, "jacobian_candidate_rejections")
        _record_ik_candidate_rejection(diagnostics, str(rejection))

    try:
        sampling_result = arm.solve_ik_via_sampling(
            target[:3],
            quaternion=target[3:],
            ignore_collisions=False,
            trials=IK_SAMPLING_TRIALS,
            max_configs=IK_SAMPLING_MAX_CONFIGS,
            max_time_ms=IK_SAMPLING_MAX_TIME_MS,
            relative_to=None,
        )
    except configuration_error as exc:
        _increment_ik_diagnostic(diagnostics, "sampling_fallback_failures")
        raise invalid_action_error(error_message) from exc

    if isinstance(sampling_result, np.ndarray):
        if sampling_result.ndim == 2:
            candidates = tuple(sampling_result)
        elif sampling_result.ndim == 1 and sampling_result.size:
            candidates = (sampling_result,)
        elif sampling_result.size == 0:
            candidates = ()
        else:
            candidates = (sampling_result,)
    else:
        try:
            values = tuple(sampling_result)
        except TypeError:
            candidates = (sampling_result,)
        else:
            if values and all(np.asarray(value).ndim == 0 for value in values):
                candidates = (values,)
            else:
                candidates = values

    valid_candidates = []
    for raw_candidate in candidates:
        _increment_ik_diagnostic(diagnostics, "sampling_candidates_evaluated")
        candidate, rejection = _validated_ik_candidate(
            raw_candidate,
            current_joints=current_joints,
            limits=limits,
        )
        if candidate is None:
            _record_ik_candidate_rejection(diagnostics, str(rejection))
            continue
        valid_candidates.append(candidate)

    if not valid_candidates:
        _increment_ik_diagnostic(diagnostics, "sampling_fallback_failures")
        raise invalid_action_error(error_message)

    selected = min(
        valid_candidates,
        key=lambda candidate: float(np.linalg.norm(candidate - current_joints)),
    )
    _increment_ik_diagnostic(diagnostics, "sampling_fallback_successes")
    _increment_ik_diagnostic(diagnostics, "selected_via_sampling")
    delta = float(np.linalg.norm(selected - current_joints))
    diagnostics["selected_joint_delta_l2_max"] = max(
        float(diagnostics.get("selected_joint_delta_l2_max", 0.0)),
        delta,
    )
    return selected.copy()


def final_settling_metadata(
    physics_steps: int = DEFAULT_FINAL_SETTLING_PHYSICS_STEPS,
) -> dict[str, Any]:

    if isinstance(physics_steps, bool) or not isinstance(physics_steps, int):
        raise TypeError("final settling physics steps must be an integer")
    if physics_steps < 0:
        raise ValueError("final settling physics steps must be non-negative")
    return {
        "protocol_id": (
            FINAL_SETTLING_PROTOCOL_ID
            if physics_steps == DEFAULT_FINAL_SETTLING_PHYSICS_STEPS
            else (
                "rlbench-hold-final-command-up-to-"
                f"{physics_steps}-raw-physics-steps-v3"
            )
        ),
        "physics_steps": physics_steps,
        "maximum_physics_steps": physics_steps,
        "entry_condition": "normal_policy_complete_without_terminal_outcome",
        "command_semantics": "hold_existing_joint_targets_and_gripper_state",
        "step_api": "Scene.step",
        "success_and_failure_check": "Task.success_after_each_raw_physics_step",
        "early_stop": "first_success_or_explicit_terminate",
        "policy_clock_advanced": False,
        "dynamic_clock_advanced": False,
        "task_specific_adaptation": False,
    }


def run_final_settling(
    task_environment: Any,
    *,
    physics_steps: int = DEFAULT_FINAL_SETTLING_PHYSICS_STEPS,
) -> dict[str, Any]:

    metadata = final_settling_metadata(physics_steps)
    result = {
        **metadata,
        "attempted": physics_steps > 0,
        "available": True,
        "steps_executed": 0,
        "first_terminal_step": None,
        "stop_reason": "budget_zero" if physics_steps == 0 else None,
        "success": False,
        "terminate": False,
    }
    if physics_steps == 0:
        return result
    scene = getattr(task_environment, "_scene", None)
    step_scene = getattr(scene, "step", None)
    task = getattr(scene, "task", None)
    success_fn = getattr(task, "success", None)
    if not callable(step_scene) or not callable(success_fn):

        result.update(
            {
                "available": False,
                "attempted": False,
                "stop_reason": "raw_scene_or_task_success_api_unavailable",
            }
        )
        return result
    for index in range(1, physics_steps + 1):
        step_scene()
        success, terminate = success_fn()
        result["steps_executed"] = index
        result["success"] = bool(success)
        result["terminate"] = bool(terminate)
        if success or terminate:
            result["first_terminal_step"] = index
            result["stop_reason"] = "success" if success else "explicit_terminate"
            break
    if result["stop_reason"] is None:
        result["stop_reason"] = "maximum_physics_steps_reached"
    return result


def _protocol_float_token(value: float) -> str:

    return format(value, ".12g").replace("-", "m").replace(".", "p")


@dataclass(frozen=True)
class DiscreteGripperProtocol:

    bimanual: bool
    actuation_velocity: float = DEMONSTRATION_GRIPPER_ACTUATION_VELOCITY
    attach_grasped_objects: bool = True
    detach_before_open: bool = True
    ownership_transfer_requires_receiver_detection: bool = True

    def __post_init__(self) -> None:
        for name in (
            "bimanual",
            "attach_grasped_objects",
            "detach_before_open",
            "ownership_transfer_requires_receiver_detection",
        ):
            if not isinstance(getattr(self, name), bool):
                raise TypeError(f"{name} must be a bool")
        velocity = float(self.actuation_velocity)
        if not math.isfinite(velocity) or velocity <= 0.0:
            raise ValueError("gripper actuation velocity must be finite and positive")
        object.__setattr__(self, "actuation_velocity", velocity)

    @property
    def protocol_id(self) -> str:
        layout = "bimanual" if self.bimanual else "unimanual"
        velocity = _protocol_float_token(self.actuation_velocity)
        attach = int(self.attach_grasped_objects)
        detach = int(self.detach_before_open)
        return (
            f"rlbench-discrete-gripper-{layout}-velocity{velocity}"
            f"-attach{attach}-detach-before-open{detach}"
            f"-transfer-detect{int(self.ownership_transfer_requires_receiver_detection)}"
            "-retry-pending-close1-attachment-aware-open-v4"
        )

    def extend_evaluation_protocol_id(self, base_protocol_id: str) -> str:

        base = str(base_protocol_id).strip()
        if not base:
            raise ValueError("base evaluation protocol ID must be non-empty")
        suffix = f"+{self.protocol_id}"
        return base if base.endswith(suffix) else f"{base}{suffix}"

    def metadata(self) -> dict[str, Any]:

        return {
            "protocol_id": self.protocol_id,
            "action_mode": "BimanualDiscrete" if self.bimanual else "Discrete",
            "arm_layout": "bimanual" if self.bimanual else "unimanual",
            "actuation_velocity": self.actuation_velocity,
            "demonstration_actuation_velocity": (
                DEMONSTRATION_GRIPPER_ACTUATION_VELOCITY
            ),
            "velocity_aligned_with_demonstrations": bool(
                math.isclose(
                    self.actuation_velocity,
                    DEMONSTRATION_GRIPPER_ACTUATION_VELOCITY,
                    rel_tol=0.0,
                    abs_tol=1.0e-12,
                )
            ),
            "attach_grasped_objects": self.attach_grasped_objects,
            "detach_before_open": self.detach_before_open,
            "ownership_transfer_requires_receiver_detection": (
                self.ownership_transfer_requires_receiver_detection
            ),
            "delayed_attachment_retry": True,
            "delayed_attachment_retry_scope": (
                "unresolved_open_to_closed_command_until_attach_or_reopen"
            ),
            "implementation": (
                "project_subclass_with_pending_close_attachment_retry_and_"
                "receiver_proximity_checked_bimanual_transfer"
                if self.bimanual and self.ownership_transfer_requires_receiver_detection
                else "project_subclass_with_pending_close_attachment_retry"
            ),
        }

    def make_action_mode(self) -> Any:

        return _make_discrete_gripper_action_mode(self)


def make_discrete_gripper_action_mode(
    *,
    bimanual: bool,
    actuation_velocity: float = DEMONSTRATION_GRIPPER_ACTUATION_VELOCITY,
    attach_grasped_objects: bool = True,
    detach_before_open: bool = True,
) -> Any:

    return DiscreteGripperProtocol(
        bimanual=bimanual,
        actuation_velocity=actuation_velocity,
        attach_grasped_objects=attach_grasped_objects,
        detach_before_open=detach_before_open,
    ).make_action_mode()


def _make_discrete_gripper_action_mode(protocol: DiscreteGripperProtocol) -> Any:

    from rlbench.action_modes.gripper_action_modes import BimanualDiscrete, Discrete

    if protocol.bimanual:

        class ProtocolBimanualDiscrete(BimanualDiscrete):
            dynamac_protocol = protocol

            @staticmethod
            def _receiver_detects(receiver: Any, obj: Any) -> bool:
                sensor = getattr(receiver, "_proximity_sensor", None)
                detects = getattr(sensor, "is_detected", None)
                if not callable(detects):
                    raise RuntimeError(
                        "RLBench bimanual attachment transfer requires the gripper proximity-detection interface"
                    )
                return bool(detects(obj))

            def _grasp_with_ownership(
                self,
                receiver: Any,
                donor: Any,
                obj: Any,
            ) -> bool:
                donor_objects = donor.get_grasped_objects()
                if obj in donor_objects:
                    if (
                        protocol.ownership_transfer_requires_receiver_detection
                        and not self._receiver_detects(receiver, obj)
                    ):
                        return False
                    donor.release()
                return bool(receiver.grasp(obj))

            def _pending_close(self) -> dict[str, bool]:
                pending = getattr(self, "_dynamac_pending_close_attachment", None)
                if pending is None:
                    pending = {"right": False, "left": False}
                    self._dynamac_pending_close_attachment = pending
                return pending

            def _attempt_pending_close(
                self,
                scene: Any,
                *,
                arm_id: str,
            ) -> None:
                pending = self._pending_close()
                suppressed = set(
                    getattr(self, "_dynamac_attachment_suppressed_arms", ())
                )
                if (
                    not pending[arm_id]
                    or not self._attach_grasped_objects
                    or arm_id in suppressed
                ):
                    return
                if arm_id == "right":
                    receiver = scene.robot.right_gripper
                    donor = scene.robot.left_gripper
                else:
                    receiver = scene.robot.left_gripper
                    donor = scene.robot.right_gripper
                if receiver.get_grasped_objects():
                    pending[arm_id] = False
                    return
                attached = any(
                    self._grasp_with_ownership(receiver, donor, obj)
                    for obj in scene.task.get_graspable_objects()
                )
                if attached:
                    pending[arm_id] = False

            def action(self, scene: Any, action: Array) -> None:

                expected_shape = self.action_shape(scene.robot)
                if np.shape(action) != expected_shape:
                    raise ValueError(
                        f"Bimanual gripper action must have shape {expected_shape}; received {np.shape(action)}"
                    )
                values = np.asarray(action, dtype=np.float64)
                if (
                    not np.all(np.isfinite(values))
                    or np.any(values < 0.0)
                    or np.any(values > 1.0)
                ):
                    raise ValueError(
                        "Gripper action expected to be finite within 0 and 1."
                    )

                right_current = float(
                    all(
                        value > 0.9
                        for value in scene.robot.right_gripper.get_open_amount()
                    )
                )
                left_current = float(
                    all(
                        value > 0.9
                        for value in scene.robot.left_gripper.get_open_amount()
                    )
                )
                right_action = float(values[0] > 0.5)
                left_action = float(values[1] > 0.5)
                changed = right_current != right_action or left_current != left_action
                pending = self._pending_close()
                if right_current != right_action:
                    pending["right"] = right_action == 0.0
                if left_current != left_action:
                    pending["left"] = left_action == 0.0
                if changed and not self._detach_before_open:
                    self._actuate(scene, values)

                if right_current != right_action:
                    if right_action == 1.0:
                        scene.robot.right_gripper.release()
                if left_current != left_action:
                    if left_action == 1.0:
                        scene.robot.left_gripper.release()

                self._attempt_pending_close(scene, arm_id="right")
                self._attempt_pending_close(scene, arm_id="left")

                if changed:
                    if self._detach_before_open:
                        self._actuate(scene, values)
                        self._attempt_pending_close(scene, arm_id="right")
                        self._attempt_pending_close(scene, arm_id="left")
                    if right_action == 1.0 or left_action == 1.0:
                        for _ in range(10):
                            scene.pyrep.step()
                            scene.task.step()

            def _actuate(self, scene: Any, action: Array) -> None:
                right_action = action[0]
                left_action = action[1]
                right_done = False
                left_done = False
                while not (right_done and left_done):
                    if not right_done:
                        right_done = scene.robot.right_gripper.actuate(
                            right_action,
                            velocity=protocol.actuation_velocity,
                        )
                    if not left_done:
                        left_done = scene.robot.left_gripper.actuate(
                            left_action,
                            velocity=protocol.actuation_velocity,
                        )
                    scene.pyrep.step()
                    scene.task.step()

        mode_class = ProtocolBimanualDiscrete
    else:

        class ProtocolDiscrete(Discrete):
            dynamac_protocol = protocol

            def _pending_close(self) -> bool:
                return bool(getattr(self, "_dynamac_pending_close_attachment", False))

            def _attempt_pending_close(self, scene: Any) -> None:
                suppressed = set(
                    getattr(self, "_dynamac_attachment_suppressed_arms", ())
                )
                if (
                    not self._pending_close()
                    or not self._attach_grasped_objects
                    or "single" in suppressed
                ):
                    return
                gripper = scene.robot.gripper
                if gripper.get_grasped_objects():
                    self._dynamac_pending_close_attachment = False
                    return
                attached = any(
                    bool(gripper.grasp(obj))
                    for obj in scene.task.get_graspable_objects()
                )
                if attached:
                    self._dynamac_pending_close_attachment = False

            def action(self, scene: Any, action: Array) -> None:
                if np.shape(action) != (1,):
                    raise ValueError(
                        f"Single-arm gripper action must have shape (1,); received {np.shape(action)}"
                    )
                values = np.asarray(action, dtype=np.float64)
                if (
                    not np.all(np.isfinite(values))
                    or np.any(values < 0.0)
                    or np.any(values > 1.0)
                ):
                    raise ValueError(
                        "Gripper action expected to be finite within 0 and 1."
                    )
                gripper = scene.robot.gripper
                current = float(all(value > 0.9 for value in gripper.get_open_amount()))
                requested = float(values[0] > 0.5)

                release_required = bool(
                    requested == 1.0 and gripper.get_grasped_objects()
                )
                changed = current != requested or release_required
                if changed:
                    self._dynamac_pending_close_attachment = requested == 0.0
                    if not self._detach_before_open:
                        self._actuate(scene, requested)
                    if requested == 1.0:
                        gripper.release()

                self._attempt_pending_close(scene)
                if changed:
                    if self._detach_before_open:
                        self._actuate(scene, requested)
                        self._attempt_pending_close(scene)
                    if requested == 1.0:
                        for _ in range(10):
                            scene.pyrep.step()
                            scene.task.step()

            def _actuate(self, scene: Any, action: Array | float) -> None:
                done = False
                while not done:
                    done = scene.robot.gripper.actuate(
                        action,
                        velocity=protocol.actuation_velocity,
                    )
                    scene.pyrep.step()
                    scene.task.step()

        mode_class = ProtocolDiscrete

    return mode_class(
        attach_grasped_objects=protocol.attach_grasped_objects,
        detach_before_open=protocol.detach_before_open,
    )


def execute_joint_target_control(
    scene: Any,
    arm_targets: tuple[tuple[Any, Array], ...],
    *,
    max_steps: int = 200,
    reached_atol: float = 0.01,
    stopped_atol: float = 0.001,
    invalid_action_error: type[Exception] = RuntimeError,
    error_message: str = "absolute end-effector IK execution timed out",
) -> Literal["reached", "stopped"]:

    if max_steps < 1:
        raise ValueError("max_steps must be positive")
    if not arm_targets:
        raise ValueError("at least one arm target is required")
    if reached_atol <= 0.0 or stopped_atol <= 0.0:
        raise ValueError("joint tolerances must be positive")

    normalized = tuple(
        (arm, np.asarray(target, dtype=np.float64).copy())
        for arm, target in arm_targets
    )
    previous: tuple[Array, ...] | None = None
    for _ in range(max_steps):
        scene.step()
        current = tuple(
            np.asarray(arm.get_joint_positions(), dtype=np.float64)
            for arm, _ in normalized
        )
        if all(
            np.allclose(value, target, atol=reached_atol)
            for value, (_, target) in zip(current, normalized)
        ):
            return "reached"
        if previous is not None and all(
            np.allclose(value, prior, atol=stopped_atol)
            for value, prior in zip(current, previous)
        ):
            return "stopped"
        previous = current

    raise invalid_action_error(error_message)


def _arm_tip_pose(arm: Any) -> Array:
    get_tip = getattr(arm, "get_tip", None)
    if not callable(get_tip):
        raise RuntimeError("arm tip API is unavailable")
    get_pose = getattr(get_tip(), "get_pose", None)
    if not callable(get_pose):
        raise RuntimeError("arm tip pose API is unavailable")
    pose = np.asarray(get_pose(), dtype=np.float64)
    if pose.shape != (7,) or not np.all(np.isfinite(pose)):
        raise RuntimeError("arm tip returned an invalid pose")
    return pose


def _normalized_xyzw_quaternion(value: Any) -> Array:
    quaternion = np.asarray(value, dtype=np.float64)
    if quaternion.shape != (4,) or not np.all(np.isfinite(quaternion)):
        raise ValueError("quaternion must be a finite xyzw vector")
    norm = float(np.linalg.norm(quaternion))
    if norm <= 1.0e-12:
        raise ValueError("quaternion norm must be positive")
    return quaternion / norm


def _shortest_xyzw_slerp(left: Any, right: Any, fraction: float) -> Array:
    if not math.isfinite(fraction) or not 0.0 <= fraction <= 1.0:
        raise ValueError("SLERP fraction must lie in [0, 1]")
    source = _normalized_xyzw_quaternion(left)
    target = _normalized_xyzw_quaternion(right)
    dot = float(np.dot(source, target))
    if dot < 0.0:
        target = -target
        dot = -dot
    dot = float(np.clip(dot, -1.0, 1.0))
    if dot > 0.9995:
        return _normalized_xyzw_quaternion(source + fraction * (target - source))
    angle = math.acos(dot)
    denominator = math.sin(angle)
    return _normalized_xyzw_quaternion(
        math.sin((1.0 - fraction) * angle) / denominator * source
        + math.sin(fraction * angle) / denominator * target
    )


def _cartesian_continuation_subgoal(
    current_pose: Any,
    target_pose: Any,
    *,
    translation_step_m: float,
    rotation_step_rad: float,
) -> tuple[Array, float] | None:

    current = np.asarray(current_pose, dtype=np.float64)
    target = np.asarray(target_pose, dtype=np.float64)
    translation, rotation = end_effector_pose_distance(current, target)
    fractions = [1.0]
    if translation > translation_step_m:
        fractions.append(translation_step_m / translation)
    if rotation > rotation_step_rad:
        fractions.append(rotation_step_rad / rotation)
    fraction = float(min(fractions))
    if fraction >= 1.0 - 1.0e-12:
        return None
    subgoal = np.empty(7, dtype=np.float64)
    subgoal[:3] = current[:3] + fraction * (target[:3] - current[:3])
    subgoal[3:] = _shortest_xyzw_slerp(current[3:], target[3:], fraction)
    return subgoal, fraction


def _execute_prepared_ee_commands(
    scene: Any,
    commands: tuple[PreparedEECommand, ...],
    *,
    invalid_action_error: type[Exception],
    error_message: str,
    max_steps: int = 200,
    reached_atol: float = 0.01,
    stopped_atol: float = 0.001,
    step_counter: dict[str, int] | None = None,
    budget_exhaustion_is_stopped: bool = False,
) -> Literal["reached", "stopped"]:

    if not commands:
        raise ValueError("at least one prepared EE command is required")
    if len({id(command.arm) for command in commands}) != len(commands):
        raise ValueError("prepared EE commands must target distinct arms")
    if max_steps < 1:
        raise ValueError("max_steps must be positive")
    path_done = [command.mode != "planned_path" for command in commands]
    path_targets: list[Array | None] = [None] * len(commands)
    previous: tuple[Array, ...] | None = None
    for command in commands:
        if command.mode == "joint_target":
            command.arm.set_joint_target_positions(command.target_joints)

    for _ in range(max_steps):
        for index, command in enumerate(commands):
            if command.mode != "planned_path" or path_done[index]:
                continue
            try:
                path_done[index] = bool(command.path.step())
                get_action = getattr(
                    command.path, "get_executed_joint_position_action", None
                )
                executed = get_action() if callable(get_action) else None
            except Exception as exc:
                raise invalid_action_error(error_message) from exc
            if executed is not None:
                value = np.asarray(executed, dtype=np.float64)
                if value.ndim != 1 or not np.all(np.isfinite(value)):
                    raise invalid_action_error(error_message)
                path_targets[index] = value.copy()

        scene.step()
        if step_counter is not None:
            step_counter["steps"] = int(step_counter.get("steps", 0)) + 1
        current = tuple(
            np.asarray(command.arm.get_joint_positions(), dtype=np.float64)
            for command in commands
        )
        if any(value.ndim != 1 or not np.all(np.isfinite(value)) for value in current):
            raise invalid_action_error(error_message)
        targets = tuple(
            (
                command.target_joints
                if command.mode == "joint_target"
                else path_targets[index]
            )
            for index, command in enumerate(commands)
        )
        if all(path_done) and any(target is None for target in targets):
            raise invalid_action_error(error_message)
        if all(path_done) and all(
            np.allclose(value, target, atol=reached_atol)
            for value, target in zip(current, targets)
        ):
            return "reached"

        if (
            all(path_done)
            and previous is not None
            and all(
                np.allclose(value, prior, atol=stopped_atol)
                for value, prior in zip(current, previous)
            )
        ):
            return "stopped"
        previous = current
    if budget_exhaustion_is_stopped:
        if step_counter is not None:
            step_counter["budget_exhausted"] = 1
        return "stopped"
    raise invalid_action_error(error_message)


def _set_ik_group_properties(
    arm: Any,
    *,
    resolution_method: str,
    max_iterations: int,
    damping: float,
    invalid_action_error: type[Exception],
    error_message: str,
) -> None:
    setter = getattr(arm, "set_ik_group_properties", None)
    if not callable(setter):
        raise invalid_action_error(error_message)
    try:
        setter(
            resolution_method=resolution_method,
            max_iterations=max_iterations,
            dls_damping=damping,
        )
    except Exception as exc:
        raise invalid_action_error(error_message) from exc


def _validated_continuous_ik_candidate(
    candidate: Any,
    *,
    current_joints: Array,
    limits: tuple[tuple[bool, float, float], ...],
    config: GlobalIKControllerConfig,
) -> tuple[Array | None, str | None]:
    joints, rejection = _validated_ik_candidate(
        candidate,
        current_joints=current_joints,
        limits=limits,
    )
    if joints is None:
        return None, rejection
    delta = joints - current_joints
    if float(np.max(np.abs(delta), initial=0.0)) > config.max_joint_delta_abs_rad:
        return None, "joint_delta_abs"
    if float(np.linalg.norm(delta)) > config.max_joint_delta_l2_rad:
        return None, "joint_delta_l2"
    return joints, None


def _record_selected_joint_delta(
    diagnostics: dict[str, Any], current_joints: Array, selected_joints: Array
) -> None:
    delta = selected_joints - current_joints
    diagnostics["selected_joint_delta_abs_max"] = max(
        float(diagnostics.get("selected_joint_delta_abs_max", 0.0)),
        float(np.max(np.abs(delta), initial=0.0)),
    )
    diagnostics["selected_joint_delta_l2_max"] = max(
        float(diagnostics.get("selected_joint_delta_l2_max", 0.0)),
        float(np.linalg.norm(delta)),
    )


def _prepare_trac_ik_distance_command(
    arm: Any,
    target: Array,
    *,
    current: Array,
    limits: tuple[tuple[bool, float, float], ...],
    config: GlobalIKControllerConfig,
    diagnostics: dict[str, Any],
    external_solver_factory: TracIKDistanceSolverFactory,
) -> PreparedEECommand | None:

    _increment_ik_diagnostic(diagnostics, "trac_ik_distance_attempts")
    try:
        solver = external_solver_factory(arm)
    except Exception:
        _increment_ik_diagnostic(diagnostics, "trac_ik_distance_factory_failures")
        _increment_ik_diagnostic(diagnostics, "trac_ik_distance_failures")
        _increment_ik_diagnostic(diagnostics, "trac_ik_distance_exhaustions")
        return None
    if not callable(getattr(solver, "solve", None)):
        _increment_ik_diagnostic(diagnostics, "trac_ik_distance_factory_failures")
        _increment_ik_diagnostic(diagnostics, "trac_ik_distance_failures")
        _increment_ik_diagnostic(diagnostics, "trac_ik_distance_exhaustions")
        return None
    chain_source = getattr(solver, "chain_source", None)
    chain_schema = getattr(solver, "chain_schema", None)
    if isinstance(chain_source, str) and chain_source:
        sources = diagnostics.setdefault("trac_ik_distance_chain_sources", [])
        if chain_source not in sources:
            sources.append(chain_source)
    else:
        _increment_ik_diagnostic(diagnostics, "trac_ik_distance_chain_source_missing")
    if isinstance(chain_schema, str) and chain_schema:
        schemas = diagnostics.setdefault("trac_ik_distance_chain_schemas", [])
        if chain_schema not in schemas:
            schemas.append(chain_schema)
    else:
        _increment_ik_diagnostic(diagnostics, "trac_ik_distance_chain_schema_missing")

    started_at = time.perf_counter()
    try:
        result = solver.solve(target.copy())
    except Exception:
        _record_solver_duration_ms(diagnostics, "trac_ik_distance", started_at)
        _increment_ik_diagnostic(diagnostics, "trac_ik_distance_failures")
        _increment_ik_diagnostic(diagnostics, "trac_ik_distance_exhaustions")
        return None
    _record_solver_duration_ms(diagnostics, "trac_ik_distance", started_at)
    if result is None:
        _increment_ik_diagnostic(diagnostics, "trac_ik_distance_failures")
        _increment_ik_diagnostic(diagnostics, "trac_ik_distance_exhaustions")
        return None
    bounded_api_used = getattr(result, "bounded_cartesian_api_used", None)
    fk_translation_error = getattr(result, "fk_translation_error_m", None)
    fk_rotation_error = getattr(result, "fk_rotation_error_rad", None)
    try:
        fk_translation_error = float(fk_translation_error)
        fk_rotation_error = float(fk_rotation_error)
    except (TypeError, ValueError):
        fk_translation_error = math.nan
        fk_rotation_error = math.nan
    if bounded_api_used is True:
        _increment_ik_diagnostic(
            diagnostics, "trac_ik_distance_bounded_cartesian_api_uses"
        )
    elif bounded_api_used is False:
        _increment_ik_diagnostic(
            diagnostics, "trac_ik_distance_unbounded_cartesian_api_uses"
        )
    if (
        bounded_api_used is not True
        or not math.isfinite(fk_translation_error)
        or fk_translation_error < 0.0
        or not math.isfinite(fk_rotation_error)
        or fk_rotation_error < 0.0
    ):
        _increment_ik_diagnostic(
            diagnostics, "trac_ik_distance_result_metadata_missing"
        )
        _increment_ik_diagnostic(diagnostics, "trac_ik_distance_failures")
        _increment_ik_diagnostic(diagnostics, "trac_ik_distance_exhaustions")
        return None
    diagnostics["trac_ik_distance_fk_translation_error_m_max"] = max(
        float(diagnostics.get("trac_ik_distance_fk_translation_error_m_max", 0.0)),
        fk_translation_error,
    )
    diagnostics["trac_ik_distance_fk_rotation_error_rad_max"] = max(
        float(diagnostics.get("trac_ik_distance_fk_rotation_error_rad_max", 0.0)),
        fk_rotation_error,
    )
    reported_elapsed_ms = getattr(result, "elapsed_ms", None)
    if reported_elapsed_ms is not None:
        try:
            reported_elapsed_ms = float(reported_elapsed_ms)
        except (TypeError, ValueError):
            reported_elapsed_ms = None
        if reported_elapsed_ms is not None and math.isfinite(reported_elapsed_ms):
            diagnostics["trac_ik_distance_reported_solve_time_ms_total"] = float(
                diagnostics.get("trac_ik_distance_reported_solve_time_ms_total", 0.0)
            ) + max(0.0, reported_elapsed_ms)
            diagnostics["trac_ik_distance_reported_solve_time_ms_max"] = max(
                float(
                    diagnostics.get("trac_ik_distance_reported_solve_time_ms_max", 0.0)
                ),
                max(0.0, reported_elapsed_ms),
            )
    raw_candidate = getattr(result, "joints", result)
    candidate, rejection = _validated_continuous_ik_candidate(
        raw_candidate,
        current_joints=current,
        limits=limits,
        config=config,
    )
    if candidate is None:
        _increment_ik_diagnostic(diagnostics, "trac_ik_distance_candidate_rejections")
        _record_ik_candidate_rejection(diagnostics, str(rejection))
        _increment_ik_diagnostic(diagnostics, "trac_ik_distance_failures")
        _increment_ik_diagnostic(diagnostics, "trac_ik_distance_exhaustions")
        return None

    _increment_ik_diagnostic(diagnostics, "trac_ik_distance_successes")
    _increment_ik_diagnostic(diagnostics, "selected_via_trac_ik_distance")
    _record_selected_joint_delta(diagnostics, current, candidate)
    return PreparedEECommand(
        arm=arm,
        target_pose=target,
        mode="joint_target",
        target_joints=candidate,
    )


def _prepare_sampling_after_trac_command(
    arm: Any,
    target: Array,
    *,
    current: Array,
    limits: tuple[tuple[bool, float, float], ...],
    diagnostics: dict[str, Any],
    configuration_error: type[Exception],
    allow_collision_relaxed: bool = False,
    continuity_config: GlobalIKControllerConfig | None = None,
) -> PreparedEECommand | None:

    _increment_ik_diagnostic(diagnostics, "sampling_after_trac_attempts")
    for ignore_collisions in (False, True) if allow_collision_relaxed else (False,):
        if ignore_collisions:
            _increment_ik_diagnostic(diagnostics, "sampling_collision_relaxed_attempts")
        try:
            sampling_result = arm.solve_ik_via_sampling(
                target[:3],
                quaternion=target[3:],
                ignore_collisions=ignore_collisions,
                trials=IK_SAMPLING_TRIALS,
                max_configs=IK_SAMPLING_MAX_CONFIGS,
                max_time_ms=IK_SAMPLING_MAX_TIME_MS,
                relative_to=None,
            )
        except configuration_error:
            if ignore_collisions:
                _increment_ik_diagnostic(
                    diagnostics, "sampling_collision_relaxed_failures"
                )
            continue
        if isinstance(sampling_result, np.ndarray):
            if sampling_result.ndim == 2:
                candidates = tuple(sampling_result)
            elif sampling_result.ndim == 1 and sampling_result.size:
                candidates = (sampling_result,)
            elif sampling_result.size == 0:
                candidates = ()
            else:
                candidates = (sampling_result,)
        else:
            try:
                values = tuple(sampling_result)
            except TypeError:
                candidates = (sampling_result,)
            else:
                if values and all(np.asarray(value).ndim == 0 for value in values):
                    candidates = (values,)
                else:
                    candidates = values

        valid_candidates = []
        for raw_candidate in candidates:
            _increment_ik_diagnostic(diagnostics, "sampling_candidates_evaluated")
            if continuity_config is None:
                candidate, rejection = _validated_ik_candidate(
                    raw_candidate,
                    current_joints=current,
                    limits=limits,
                )
            else:
                candidate, rejection = _validated_continuous_ik_candidate(
                    raw_candidate,
                    current_joints=current,
                    limits=limits,
                    config=continuity_config,
                )
            if candidate is None:
                _record_ik_candidate_rejection(diagnostics, str(rejection))
                continue
            valid_candidates.append(candidate)

        if valid_candidates:
            selected = min(
                valid_candidates,
                key=lambda candidate: float(np.linalg.norm(candidate - current)),
            )
            _increment_ik_diagnostic(diagnostics, "sampling_fallback_successes")
            _increment_ik_diagnostic(diagnostics, "sampling_after_trac_successes")
            _increment_ik_diagnostic(diagnostics, "selected_via_sampling")
            if ignore_collisions:
                _increment_ik_diagnostic(
                    diagnostics, "sampling_collision_relaxed_successes"
                )
                _increment_ik_diagnostic(
                    diagnostics, "selected_via_collision_relaxed_sampling"
                )
            _record_selected_joint_delta(diagnostics, current, selected)
            return PreparedEECommand(
                arm=arm,
                target_pose=target,
                mode="joint_target",
                target_joints=selected,
            )
        if ignore_collisions:
            _increment_ik_diagnostic(diagnostics, "sampling_collision_relaxed_failures")

    _increment_ik_diagnostic(diagnostics, "sampling_fallback_failures")
    _increment_ik_diagnostic(diagnostics, "sampling_after_trac_failures")
    _increment_ik_diagnostic(diagnostics, "all_ik_exhaustions")
    return None


def _prepare_pseudo_trac_sampling_command(
    arm: Any,
    target: Array,
    *,
    config: GlobalIKControllerConfig,
    diagnostics: dict[str, Any],
    external_solver_factory: TracIKDistanceSolverFactory,
    ik_error: type[Exception],
    configuration_error: type[Exception],
    invalid_action_error: type[Exception],
    error_message: str,
) -> PreparedEECommand | None:

    current = np.asarray(arm.get_joint_positions(), dtype=np.float64)
    if current.ndim != 1 or not np.all(np.isfinite(current)):
        raise invalid_action_error(error_message)
    try:
        limits = _joint_limit_specification(arm, current)
    except (TypeError, ValueError) as exc:
        raise invalid_action_error(error_message) from exc

    pseudo_command = _prepare_pseudo_inverse_command(
        arm,
        target,
        current=current,
        limits=limits,
        diagnostics=diagnostics,
        ik_error=ik_error,
        invalid_action_error=invalid_action_error,
        error_message=error_message,
        continuity_config=(
            config if isinstance(config, HybridIKControllerConfig) else None
        ),
    )
    if pseudo_command is not None:
        return pseudo_command

    trac_command = _prepare_trac_ik_distance_command(
        arm,
        target,
        current=current,
        limits=limits,
        config=config,
        diagnostics=diagnostics,
        external_solver_factory=external_solver_factory,
    )
    if trac_command is not None:
        return trac_command
    return _prepare_sampling_after_trac_command(
        arm,
        target,
        current=current,
        limits=limits,
        diagnostics=diagnostics,
        configuration_error=configuration_error,
        allow_collision_relaxed=(
            isinstance(config, HybridIKControllerConfig)
            and config.allow_collision_relaxed_sampling
        ),
    )


def _prepare_pseudo_inverse_command(
    arm: Any,
    target: Array,
    *,
    current: Array,
    limits: tuple[tuple[bool, float, float], ...],
    diagnostics: dict[str, Any],
    ik_error: type[Exception],
    invalid_action_error: type[Exception],
    error_message: str,
    continuity_config: GlobalIKControllerConfig | None = None,
) -> PreparedEECommand | None:

    _set_ik_group_properties(
        arm,
        resolution_method=FROZEN_V4_IK_RESOLUTION_METHOD,
        max_iterations=FROZEN_V4_IK_MAX_ITERATIONS,
        damping=FROZEN_V4_IK_DAMPING,
        invalid_action_error=invalid_action_error,
        error_message=error_message,
    )
    _increment_ik_diagnostic(diagnostics, "pseudo_inverse_ik_attempts")
    try:
        raw_pseudo_candidate = arm.solve_ik_via_jacobian(
            target[:3], quaternion=target[3:], relative_to=None
        )
    except ik_error:
        _increment_ik_diagnostic(diagnostics, "pseudo_inverse_ik_failures")
        _increment_ik_diagnostic(diagnostics, "jacobian_failures")
    else:
        pseudo_candidate, rejection = (
            _validated_ik_candidate(
                raw_pseudo_candidate,
                current_joints=current,
                limits=limits,
            )
            if continuity_config is None
            else _validated_continuous_ik_candidate(
                raw_pseudo_candidate,
                current_joints=current,
                limits=limits,
                config=continuity_config,
            )
        )
        if pseudo_candidate is not None:
            _increment_ik_diagnostic(diagnostics, "pseudo_inverse_ik_successes")
            _increment_ik_diagnostic(diagnostics, "selected_via_pseudo_inverse")
            _increment_ik_diagnostic(diagnostics, "selected_via_jacobian")
            _record_selected_joint_delta(diagnostics, current, pseudo_candidate)
            return PreparedEECommand(
                arm=arm,
                target_pose=target,
                mode="joint_target",
                target_joints=pseudo_candidate,
            )
        _increment_ik_diagnostic(diagnostics, "pseudo_inverse_ik_candidate_rejections")
        _increment_ik_diagnostic(diagnostics, "jacobian_candidate_rejections")
        _record_ik_candidate_rejection(diagnostics, str(rejection))
    return None


def _prepare_hybrid_command(
    arm: Any,
    target: Array,
    *,
    config: HybridIKControllerConfig,
    diagnostics: dict[str, Any],
    external_solver_factory: TracIKDistanceSolverFactory,
    ik_error: type[Exception],
    configuration_error: type[Exception],
    invalid_action_error: type[Exception],
    error_message: str,
    minimum_solver_tier: int = 0,
) -> PreparedEECommand | None:

    if isinstance(minimum_solver_tier, bool) or minimum_solver_tier not in range(
        _HYBRID_SOLVER_TIER_COUNT
    ):
        raise ValueError("minimum_solver_tier must be an integer in [0, 7]")

    current = np.asarray(arm.get_joint_positions(), dtype=np.float64)
    if current.ndim != 1 or not np.all(np.isfinite(current)):
        raise invalid_action_error(error_message)
    try:
        limits = _joint_limit_specification(arm, current)
    except (TypeError, ValueError) as exc:
        raise invalid_action_error(error_message) from exc

    if minimum_solver_tier <= 0:
        cached_command = _cached_hybrid_joint_command(
            arm,
            target,
            current=current,
            limits=limits,
            config=config,
            diagnostics=diagnostics,
        )
        if cached_command is not None:
            return cached_command
        pseudo_command = _prepare_pseudo_inverse_command(
            arm,
            target,
            current=current,
            limits=limits,
            diagnostics=diagnostics,
            ik_error=ik_error,
            invalid_action_error=invalid_action_error,
            error_message=error_message,
            continuity_config=config,
        )
        if pseudo_command is not None:
            return pseudo_command
    if minimum_solver_tier <= 1:
        trac_command = _prepare_trac_ik_distance_command(
            arm,
            target,
            current=current,
            limits=limits,
            config=config,
            diagnostics=diagnostics,
            external_solver_factory=external_solver_factory,
        )
        if trac_command is not None:
            return trac_command
    if minimum_solver_tier <= 2:
        current_pose = _arm_tip_pose(arm)
        continuation_available = False
        for backoff in config.cartesian_continuation_backoff_factors:
            continuation = _cartesian_continuation_subgoal(
                current_pose,
                target,
                translation_step_m=(
                    backoff * config.cartesian_continuation_translation_step_m
                ),
                rotation_step_rad=(
                    backoff * config.cartesian_continuation_rotation_step_rad
                ),
            )
            if continuation is None:
                continue
            continuation_available = True
            subgoal, fraction = continuation
            _increment_ik_diagnostic(diagnostics, "cartesian_continuation_attempts")
            diagnostics["cartesian_continuation_fraction_min"] = min(
                float(diagnostics.get("cartesian_continuation_fraction_min", 1.0)),
                fraction,
            )
            continuation_command = _prepare_pseudo_inverse_command(
                arm,
                subgoal,
                current=current,
                limits=limits,
                diagnostics=diagnostics,
                ik_error=ik_error,
                invalid_action_error=invalid_action_error,
                error_message=error_message,
                continuity_config=config,
            )
            if continuation_command is None:
                continuation_command = _prepare_trac_ik_distance_command(
                    arm,
                    subgoal,
                    current=current,
                    limits=limits,
                    config=config,
                    diagnostics=diagnostics,
                    external_solver_factory=external_solver_factory,
                )
            if continuation_command is not None:
                _increment_ik_diagnostic(
                    diagnostics, "cartesian_continuation_successes"
                )
                _increment_ik_diagnostic(
                    diagnostics, "selected_via_cartesian_continuation"
                )
                return continuation_command
        if continuation_available:
            _increment_ik_diagnostic(diagnostics, "cartesian_continuation_failures")
    if minimum_solver_tier <= 3:
        return _prepare_sampling_after_trac_command(
            arm,
            target,
            current=current,
            limits=limits,
            diagnostics=diagnostics,
            configuration_error=configuration_error,
            allow_collision_relaxed=config.allow_collision_relaxed_sampling,
            continuity_config=config if minimum_solver_tier > 0 else None,
        )
    return None


def _prepare_collision_aware_path_command(
    arm: Any,
    target: Array,
    *,
    config: GlobalIKControllerConfig,
    diagnostics: dict[str, Any],
    configuration_path_error: type[Exception],
    invalid_action_error: type[Exception],
    path_algorithm: Any,
    error_message: str,
    allow_near_nonlinear: bool = False,
    minimum_path_tier: int = 0,
) -> PreparedEECommand | None:
    if isinstance(minimum_path_tier, bool) or minimum_path_tier not in range(4):
        raise ValueError("minimum_path_tier must be an integer in [0, 3]")
    _set_ik_group_properties(
        arm,
        resolution_method=FROZEN_V4_IK_RESOLUTION_METHOD,
        max_iterations=FROZEN_V4_IK_MAX_ITERATIONS,
        damping=FROZEN_V4_IK_DAMPING,
        invalid_action_error=invalid_action_error,
        error_message=error_message,
    )
    if isinstance(config, HybridIKControllerConfig):
        linear_path = getattr(arm, "get_linear_path", None)
        if callable(linear_path) and minimum_path_tier <= 0:
            _increment_ik_diagnostic(diagnostics, "linear_path_attempts")
            try:
                path = linear_path(
                    target[:3],
                    quaternion=target[3:],
                    steps=config.linear_path_steps,
                    ignore_collisions=False,
                    relative_to=None,
                )
            except configuration_path_error:
                _increment_ik_diagnostic(
                    diagnostics, "linear_path_collision_aware_failures"
                )
            else:
                _increment_ik_diagnostic(
                    diagnostics, "linear_path_collision_aware_successes"
                )
                return PreparedEECommand(
                    arm=arm,
                    target_pose=target,
                    mode="planned_path",
                    path=path,
                )

        translation, _rotation = end_effector_pose_distance(_arm_tip_pose(arm), target)
        nonlinear_allowed = bool(
            allow_near_nonlinear or translation > config.far_translation_threshold_m
        )
        if nonlinear_allowed and minimum_path_tier <= 1:
            _increment_ik_diagnostic(diagnostics, "nonlinear_path_attempts")
            if translation > config.far_translation_threshold_m:
                _increment_ik_diagnostic(diagnostics, "far_target_planner_attempts")
            else:
                _increment_ik_diagnostic(
                    diagnostics, "near_target_nonlinear_planner_attempts"
                )
            try:
                path = arm.get_path(
                    target[:3],
                    quaternion=target[3:],
                    ignore_collisions=False,
                    trials=config.planner_trials,
                    max_configs=config.planner_max_configs,
                    max_time_ms=config.planner_max_time_ms,
                    trials_per_goal=config.planner_trials_per_goal,
                    algorithm=path_algorithm,
                    relative_to=None,
                )
            except configuration_path_error:
                _increment_ik_diagnostic(diagnostics, "nonlinear_path_failures")
                if translation > config.far_translation_threshold_m:
                    _increment_ik_diagnostic(diagnostics, "far_target_planner_failures")
            else:
                _increment_ik_diagnostic(diagnostics, "nonlinear_path_successes")
                if translation > config.far_translation_threshold_m:
                    _increment_ik_diagnostic(
                        diagnostics, "far_target_planner_successes"
                    )
                return PreparedEECommand(
                    arm=arm,
                    target_pose=target,
                    mode="planned_path",
                    path=path,
                )
        elif minimum_path_tier <= 1:
            _increment_ik_diagnostic(diagnostics, "near_target_nonlinear_planner_skips")

        if (
            callable(linear_path)
            and config.allow_collision_relaxed_linear_path
            and minimum_path_tier <= 2
        ):
            _increment_ik_diagnostic(
                diagnostics, "linear_path_collision_relaxed_attempts"
            )
            try:
                path = linear_path(
                    target[:3],
                    quaternion=target[3:],
                    steps=config.linear_path_steps,
                    ignore_collisions=True,
                    relative_to=None,
                )
            except configuration_path_error:
                _increment_ik_diagnostic(
                    diagnostics, "linear_path_collision_relaxed_failures"
                )
            else:
                _increment_ik_diagnostic(
                    diagnostics, "linear_path_collision_relaxed_successes"
                )
                return PreparedEECommand(
                    arm=arm,
                    target_pose=target,
                    mode="planned_path",
                    path=path,
                )

        if (
            nonlinear_allowed
            and config.allow_collision_relaxed_nonlinear_path
            and minimum_path_tier <= 3
        ):
            _increment_ik_diagnostic(
                diagnostics, "nonlinear_path_collision_relaxed_attempts"
            )
            try:
                path = arm.get_path(
                    target[:3],
                    quaternion=target[3:],
                    ignore_collisions=True,
                    trials=config.planner_trials,
                    max_configs=config.planner_max_configs,
                    max_time_ms=config.planner_max_time_ms,
                    trials_per_goal=config.planner_trials_per_goal,
                    algorithm=path_algorithm,
                    relative_to=None,
                )
            except configuration_path_error:
                _increment_ik_diagnostic(
                    diagnostics, "nonlinear_path_collision_relaxed_failures"
                )
            else:
                _increment_ik_diagnostic(
                    diagnostics, "nonlinear_path_collision_relaxed_successes"
                )
                return PreparedEECommand(
                    arm=arm,
                    target_pose=target,
                    mode="planned_path",
                    path=path,
                )
        return None

    if isinstance(config, HybridIKControllerConfig):
        raise AssertionError("unreachable hybrid path branch")

    _increment_ik_diagnostic(diagnostics, "far_target_planner_attempts")
    try:
        path = arm.get_path(
            target[:3],
            quaternion=target[3:],
            ignore_collisions=False,
            trials=config.planner_trials,
            max_configs=config.planner_max_configs,
            max_time_ms=config.planner_max_time_ms,
            trials_per_goal=config.planner_trials_per_goal,
            algorithm=path_algorithm,
            relative_to=None,
        )
    except configuration_path_error as exc:
        _increment_ik_diagnostic(diagnostics, "far_target_planner_failures")
        raise invalid_action_error(error_message) from exc
    _increment_ik_diagnostic(diagnostics, "far_target_planner_successes")
    return PreparedEECommand(
        arm=arm,
        target_pose=target,
        mode="planned_path",
        path=path,
    )


def execute_global_ik_ee_control(
    scene: Any,
    arm_targets: tuple[tuple[Any, Array], ...],
    *,
    config: GlobalIKControllerConfig,
    diagnostics: dict[str, Any],
    external_solver_factory: TracIKDistanceSolverFactory,
    ik_error: type[Exception],
    configuration_error: type[Exception],
    configuration_path_error: type[Exception],
    invalid_action_error: type[Exception],
    path_algorithm: Any,
    error_message: str,
    max_steps: int = 200,
    budget_exhaustion_is_stopped: bool = False,
    use_hybrid_solver_order: bool = False,
    hybrid_minimum_solver_tier: int = 0,
    prepared_commands_out: dict[int, PreparedEECommand] | None = None,
    solver_preparation_exhausted_out: set[int] | None = None,
) -> Literal["reached", "stopped"]:

    if not arm_targets:
        raise ValueError("at least one arm target is required")
    if not callable(external_solver_factory):
        raise TypeError("external_solver_factory must be callable")
    if max_steps < 1:
        raise ValueError("max_steps must be positive")
    if hybrid_minimum_solver_tier != 0 and not use_hybrid_solver_order:
        raise ValueError("hybrid_minimum_solver_tier requires the hybrid solver order")
    if use_hybrid_solver_order and not isinstance(config, HybridIKControllerConfig):
        raise TypeError("the hybrid solver order requires its hybrid config")
    _ensure_global_ik_controller_diagnostics(diagnostics)
    normalized = tuple(
        (arm, np.asarray(target, dtype=np.float64).copy())
        for arm, target in arm_targets
    )
    translations = []
    for arm, target in normalized:
        translation, _rotation = _record_target_pose_diagnostic(
            diagnostics, _arm_tip_pose(arm), target
        )
        translations.append(translation)

    prepared: list[PreparedEECommand] = []
    try:
        for (arm, target), translation in zip(normalized, translations):
            if use_hybrid_solver_order:
                assert isinstance(config, HybridIKControllerConfig)
                command = _prepare_hybrid_command(
                    arm,
                    target,
                    config=config,
                    diagnostics=diagnostics,
                    external_solver_factory=external_solver_factory,
                    ik_error=ik_error,
                    configuration_error=configuration_error,
                    invalid_action_error=invalid_action_error,
                    error_message=error_message,
                    minimum_solver_tier=hybrid_minimum_solver_tier,
                )
            else:
                command = _prepare_pseudo_trac_sampling_command(
                    arm,
                    target,
                    config=config,
                    diagnostics=diagnostics,
                    external_solver_factory=external_solver_factory,
                    ik_error=ik_error,
                    configuration_error=configuration_error,
                    invalid_action_error=invalid_action_error,
                    error_message=error_message,
                )
            if command is None and (
                isinstance(config, HybridIKControllerConfig)
                or translation > config.far_translation_threshold_m
            ):
                _increment_ik_diagnostic(diagnostics, "path_after_all_ik_exhaustion")
                command = _prepare_collision_aware_path_command(
                    arm,
                    target,
                    config=config,
                    diagnostics=diagnostics,
                    configuration_path_error=configuration_path_error,
                    invalid_action_error=invalid_action_error,
                    path_algorithm=path_algorithm,
                    error_message=error_message,
                    allow_near_nonlinear=bool(
                        use_hybrid_solver_order and hybrid_minimum_solver_tier >= 4
                    ),
                    minimum_path_tier=(
                        max(0, hybrid_minimum_solver_tier - 4)
                        if use_hybrid_solver_order
                        else 0
                    ),
                )
            if command is None:
                if solver_preparation_exhausted_out is not None:
                    solver_preparation_exhausted_out.add(id(arm))
                raise invalid_action_error(error_message)
            prepared.append(command)
        _increment_ik_diagnostic(
            diagnostics, "commands_prepared_before_physics", len(prepared)
        )
        if prepared_commands_out is not None:
            prepared_commands_out.clear()
            prepared_commands_out.update(
                (id(command.arm), command) for command in prepared
            )
    finally:
        restore_error: Exception | None = None
        for arm, _target in normalized:
            _increment_ik_diagnostic(diagnostics, "ik_group_baseline_restore_attempts")
            try:
                _set_ik_group_properties(
                    arm,
                    resolution_method=FROZEN_V4_IK_RESOLUTION_METHOD,
                    max_iterations=FROZEN_V4_IK_MAX_ITERATIONS,
                    damping=FROZEN_V4_IK_DAMPING,
                    invalid_action_error=invalid_action_error,
                    error_message=error_message,
                )
            except invalid_action_error as exc:
                _increment_ik_diagnostic(
                    diagnostics, "ik_group_baseline_restore_failures"
                )
                restore_error = exc
        if restore_error is not None:
            raise invalid_action_error(error_message) from restore_error

    counter = {"steps": 0}
    try:
        return _execute_prepared_ee_commands(
            scene,
            tuple(prepared),
            invalid_action_error=invalid_action_error,
            error_message=error_message,
            max_steps=max_steps,
            step_counter=counter,
            budget_exhaustion_is_stopped=budget_exhaustion_is_stopped,
        )
    finally:
        release_error: Exception | None = None
        for command in prepared:
            if command.mode != "planned_path":
                continue
            try:
                if _release_configuration_path_motion_handle(command.path):
                    _increment_ik_diagnostic(
                        diagnostics, "planned_path_motion_handle_releases"
                    )
            except Exception as exc:
                _increment_ik_diagnostic(
                    diagnostics, "planned_path_motion_handle_release_failures"
                )
                release_error = exc
        steps = int(counter["steps"])
        if int(counter.get("budget_exhausted", 0)):
            _increment_ik_diagnostic(
                diagnostics, "controller_raw_physics_budget_exhaustions"
            )
        _increment_ik_diagnostic(diagnostics, "trac_ik_distance_controller_actions")
        diagnostics["trac_ik_distance_controller_raw_physics_steps"] = (
            int(diagnostics.get("trac_ik_distance_controller_raw_physics_steps", 0))
            + steps
        )
        diagnostics["trac_ik_distance_controller_raw_physics_steps_max"] = max(
            int(
                diagnostics.get("trac_ik_distance_controller_raw_physics_steps_max", 0)
            ),
            steps,
        )
        if release_error is not None:
            raise invalid_action_error(error_message) from release_error


def _cartesian_unresolved_targets(
    arm_targets: tuple[tuple[Any, Array], ...],
    *,
    config: HybridIKControllerConfig,
    diagnostics: dict[str, Any],
) -> tuple[tuple[Any, Array], ...]:

    unresolved = []
    for arm, target in arm_targets:
        translation, rotation = end_effector_pose_distance(_arm_tip_pose(arm), target)
        _increment_ik_diagnostic(diagnostics, "cartesian_verification_checks")
        diagnostics["cartesian_post_translation_error_m_max"] = max(
            float(diagnostics.get("cartesian_post_translation_error_m_max", 0.0)),
            translation,
        )
        diagnostics["cartesian_post_rotation_error_rad_max"] = max(
            float(diagnostics.get("cartesian_post_rotation_error_rad_max", 0.0)),
            rotation,
        )
        if (
            translation > config.physical_completion_translation_tolerance_m
            or rotation > config.physical_completion_rotation_tolerance_rad
        ):
            _increment_ik_diagnostic(diagnostics, "cartesian_verification_failures")
            unresolved.append((arm, target))
    return tuple(unresolved)


def _cartesian_residual_score(
    arm: Any,
    target: Array,
    *,
    config: HybridIKControllerConfig,
) -> float:
    translation, rotation = end_effector_pose_distance(_arm_tip_pose(arm), target)
    return math.hypot(
        translation / config.physical_completion_translation_tolerance_m,
        rotation / config.physical_completion_rotation_tolerance_rad,
    )


def _cartesian_targets_within_tolerance(
    arm_targets: tuple[tuple[Any, Array], ...],
    *,
    translation_tolerance_m: float,
    rotation_tolerance_rad: float,
) -> bool:
    return all(
        translation <= translation_tolerance_m and rotation <= rotation_tolerance_rad
        for translation, rotation in (
            end_effector_pose_distance(_arm_tip_pose(arm), target)
            for arm, target in arm_targets
        )
    )


def _cartesian_vector_made_progress(
    before_scores: dict[int, float],
    after_scores: dict[int, float],
) -> tuple[bool, bool]:

    if before_scores.keys() != after_scores.keys():
        raise ValueError("Cartesian progress scores must describe the same arms")
    numerical_tolerance = 1.0e-6
    no_arm_regressed = all(
        after_scores[key] <= before_scores[key] + numerical_tolerance
        for key in before_scores
    )
    improved = [
        key
        for key in before_scores
        if after_scores[key] < before_scores[key] - numerical_tolerance
    ]
    return no_arm_regressed and bool(improved), len(improved) < len(before_scores)


def execute_hybrid_ik_ee_control(
    scene: Any,
    arm_targets: tuple[tuple[Any, Array], ...],
    *,
    config: HybridIKControllerConfig,
    diagnostics: dict[str, Any],
    external_solver_factory: TracIKDistanceSolverFactory,
    ik_error: type[Exception],
    configuration_error: type[Exception],
    configuration_path_error: type[Exception],
    invalid_action_error: type[Exception],
    path_algorithm: Any,
    error_message: str,
    max_steps: int = 200,
    per_arm_status_out: (
        dict[int, Literal["reached", "progressed", "stopped"]] | None
    ) = None,
) -> Literal["reached", "progressed", "stopped"]:

    if not arm_targets:
        raise ValueError("at least one arm target is required")
    normalized = tuple(
        (arm, np.asarray(target, dtype=np.float64).copy())
        for arm, target in arm_targets
    )
    original_scores = {
        id(arm): _cartesian_residual_score(arm, target, config=config)
        for arm, target in normalized
    }

    def finish(
        overall_status: Literal["reached", "progressed", "stopped"],
    ) -> Literal["reached", "progressed", "stopped"]:
        if per_arm_status_out is not None:
            per_arm_status_out.clear()
            for arm, target in normalized:
                translation, rotation = end_effector_pose_distance(
                    _arm_tip_pose(arm), target
                )
                if (
                    translation <= config.control_acceptance_translation_tolerance_m
                    and rotation <= config.control_acceptance_rotation_tolerance_rad
                ):
                    status: Literal["reached", "progressed", "stopped"] = "reached"
                elif (
                    _cartesian_residual_score(arm, target, config=config)
                    < original_scores[id(arm)] - 1.0e-6
                ):
                    status = "progressed"
                else:
                    status = "stopped"
                per_arm_status_out[id(arm)] = status
        return overall_status

    if _cartesian_targets_within_tolerance(
        normalized,
        translation_tolerance_m=config.control_acceptance_translation_tolerance_m,
        rotation_tolerance_rad=config.control_acceptance_rotation_tolerance_rad,
    ):
        for arm, _target in normalized:
            _clear_hybrid_same_target_solver_tier(arm)
        _increment_ik_diagnostic(
            diagnostics,
            "cartesian_pre_execution_control_accepts",
        )
        return finish("reached")

    exhaustion_states = tuple(
        _hybrid_same_target_solver_exhaustion_state(
            arm,
            target,
            config=config,
        )
        for arm, target in normalized
    )
    if all(state == "unchanged" for state in exhaustion_states):
        _increment_ik_diagnostic(
            diagnostics, "same_target_solver_exhaustion_fast_returns"
        )
        return finish("stopped")
    if any(state != "absent" for state in exhaustion_states):

        for arm, _target in normalized:
            _clear_hybrid_same_target_solver_tier(arm)
        _increment_ik_diagnostic(diagnostics, "same_target_solver_exhaustion_reopens")

    def remember_final_exhaustion() -> None:
        for arm, target in normalized:
            _store_hybrid_same_target_solver_exhaustion(arm, target)
        _increment_ik_diagnostic(diagnostics, "same_target_solver_exhaustion_records")

    before_scores = dict(original_scores)
    made_any_progress = False

    minimum_solver_tier = min(
        _hybrid_same_target_solver_tier(
            arm,
            target,
            diagnostics=diagnostics,
        )
        for arm, target in normalized
    )
    raw_steps_at_entry = int(
        diagnostics.get("trac_ik_distance_controller_raw_physics_steps", 0)
    )
    for segment_index in range(config.cartesian_continuation_max_segments):
        raw_steps_before_segment = int(
            diagnostics.get("trac_ik_distance_controller_raw_physics_steps", 0)
        )
        remaining_raw_steps = config.cartesian_continuation_max_raw_physics_steps - (
            raw_steps_before_segment - raw_steps_at_entry
        )
        if remaining_raw_steps <= 0:
            _increment_ik_diagnostic(
                diagnostics,
                "cartesian_policy_action_physics_budget_exhaustions",
            )
            if (
                not made_any_progress
                and minimum_solver_tier == _HYBRID_SOLVER_TIER_COUNT - 1
            ):
                remember_final_exhaustion()
            return finish("progressed" if made_any_progress else "stopped")
        _increment_ik_diagnostic(diagnostics, "cartesian_direct_goal_attempts")
        prepared_commands: dict[int, PreparedEECommand] = {}
        solver_preparation_exhausted: set[int] = set()
        try:
            direct_status = execute_global_ik_ee_control(
                scene,
                normalized,
                config=config,
                diagnostics=diagnostics,
                external_solver_factory=external_solver_factory,
                ik_error=ik_error,
                configuration_error=configuration_error,
                configuration_path_error=configuration_path_error,
                invalid_action_error=invalid_action_error,
                path_algorithm=path_algorithm,
                error_message=error_message,
                max_steps=min(max_steps, remaining_raw_steps),
                budget_exhaustion_is_stopped=True,
                use_hybrid_solver_order=True,
                hybrid_minimum_solver_tier=minimum_solver_tier,
                prepared_commands_out=prepared_commands,
                solver_preparation_exhausted_out=solver_preparation_exhausted,
            )
        except invalid_action_error:
            if solver_preparation_exhausted:
                unresolved_near_target = any(
                    id(arm) in solver_preparation_exhausted
                    and end_effector_pose_distance(_arm_tip_pose(arm), target)[0]
                    <= config.far_translation_threshold_m
                    for arm, target in normalized
                )
                if minimum_solver_tier < 4 and unresolved_near_target:
                    minimum_solver_tier = 4
                    for arm, target in normalized:
                        _store_hybrid_same_target_solver_tier(
                            arm, target, minimum_solver_tier
                        )
                    _increment_ik_diagnostic(
                        diagnostics, "physical_stall_solver_escalations"
                    )
                    diagnostics["physical_stall_solver_tier_max"] = max(
                        int(diagnostics.get("physical_stall_solver_tier_max", 0)),
                        minimum_solver_tier,
                    )
                    continue
                remember_final_exhaustion()
                return finish("progressed" if made_any_progress else "stopped")
            if minimum_solver_tier == 0 and not made_any_progress:
                raise
            _increment_ik_diagnostic(
                diagnostics, "physical_stall_solver_escalation_exhaustions"
            )
            _increment_ik_diagnostic(
                diagnostics,
                "cartesian_multi_pass_solver_exhaustions_after_progress",
            )
            if (
                not made_any_progress
                and minimum_solver_tier == _HYBRID_SOLVER_TIER_COUNT - 1
            ):
                remember_final_exhaustion()
            return finish("progressed" if made_any_progress else "stopped")

        if segment_index > 0:
            _increment_ik_diagnostic(
                diagnostics, "cartesian_multi_pass_followup_passes"
            )
        unresolved_after_direct = _cartesian_unresolved_targets(
            normalized,
            config=config,
            diagnostics=diagnostics,
        )
        if not unresolved_after_direct:
            for arm, _target in normalized:
                _clear_hybrid_same_target_solver_tier(arm)
            for arm, target in normalized:
                command = prepared_commands.get(id(arm))
                if command is not None:
                    _store_hybrid_joint_command(command, target)
                    if (
                        command.mode == "joint_target"
                        and _same_hybrid_cartesian_target(command.target_pose, target)
                    ):
                        _increment_ik_diagnostic(
                            diagnostics, "same_target_joint_cache_stores"
                        )
            _increment_ik_diagnostic(diagnostics, "cartesian_direct_goal_reaches")
            if segment_index > 0:
                _increment_ik_diagnostic(
                    diagnostics, "cartesian_multi_pass_goals_completed"
                )
            return finish("reached")
        if _cartesian_targets_within_tolerance(
            normalized,
            translation_tolerance_m=(config.control_acceptance_translation_tolerance_m),
            rotation_tolerance_rad=config.control_acceptance_rotation_tolerance_rad,
        ):
            for arm, _target in normalized:
                _clear_hybrid_same_target_solver_tier(arm)
            for arm, target in normalized:
                command = prepared_commands.get(id(arm))
                if command is not None:
                    _store_hybrid_joint_command(command, target)
                    if (
                        command.mode == "joint_target"
                        and _same_hybrid_cartesian_target(command.target_pose, target)
                    ):
                        _increment_ik_diagnostic(
                            diagnostics, "same_target_joint_cache_stores"
                        )
            _increment_ik_diagnostic(
                diagnostics, "cartesian_direct_goal_control_accepts"
            )
            if segment_index > 0:
                _increment_ik_diagnostic(
                    diagnostics, "cartesian_multi_pass_goals_completed"
                )
            return finish("reached")

        direct_scores = {
            id(arm): _cartesian_residual_score(arm, target, config=config)
            for arm, target in normalized
        }
        direct_progressed, direct_partial = _cartesian_vector_made_progress(
            before_scores,
            direct_scores,
        )
        raw_steps_used = (
            int(diagnostics.get("trac_ik_distance_controller_raw_physics_steps", 0))
            - raw_steps_at_entry
        )
        raw_physics_budget_exhausted = bool(
            raw_steps_used >= config.cartesian_continuation_max_raw_physics_steps
        )
        if direct_progressed:
            made_any_progress = True
            for arm, target in normalized:
                key = id(arm)
                if direct_scores[key] >= before_scores[key] - 1.0e-6:
                    continue
                command = prepared_commands.get(key)
                if command is None:
                    continue
                _store_hybrid_joint_command(command, target)
                if command.mode == "joint_target" and _same_hybrid_cartesian_target(
                    command.target_pose, target
                ):
                    _increment_ik_diagnostic(
                        diagnostics, "same_target_joint_cache_stores"
                    )
            _increment_ik_diagnostic(
                diagnostics, "cartesian_direct_goal_progress_accepts"
            )
            _increment_ik_diagnostic(
                diagnostics, "cartesian_goal_directed_progress_accepts"
            )
            if direct_partial:
                _increment_ik_diagnostic(
                    diagnostics, "cartesian_partial_arm_progress_accepts"
                )

            if minimum_solver_tier > 0:
                for arm, target in normalized:
                    _store_hybrid_same_target_solver_tier(arm, target, 0)
                _increment_ik_diagnostic(
                    diagnostics, "same_target_solver_tier_resets_after_progress"
                )

            if raw_physics_budget_exhausted:
                _increment_ik_diagnostic(
                    diagnostics,
                    "cartesian_policy_action_physics_budget_exhaustions",
                )
                diagnostics["cartesian_policy_action_raw_physics_steps_max"] = max(
                    int(
                        diagnostics.get(
                            "cartesian_policy_action_raw_physics_steps_max", 0
                        )
                    ),
                    raw_steps_used,
                )
                return finish("progressed")
            if minimum_solver_tier > 0:
                return finish("progressed")
            before_scores = direct_scores
            continue

        _increment_ik_diagnostic(diagnostics, "cartesian_direct_goal_stalls")
        for arm, target in normalized:
            if _invalidate_hybrid_joint_command(arm, target):
                _increment_ik_diagnostic(
                    diagnostics, "same_target_joint_cache_invalidations"
                )
        _increment_ik_diagnostic(
            diagnostics,
            (
                "stopped_with_cartesian_residual"
                if direct_status == "stopped"
                else "reached_joint_target_with_cartesian_residual"
            ),
        )
        if raw_physics_budget_exhausted:
            final_solver_tier_exhausted = bool(
                not direct_progressed
                and minimum_solver_tier == _HYBRID_SOLVER_TIER_COUNT - 1
            )
            if (
                not direct_progressed
                and minimum_solver_tier < _HYBRID_SOLVER_TIER_COUNT - 1
            ):
                minimum_solver_tier += 1
                for arm, target in normalized:
                    _store_hybrid_same_target_solver_tier(
                        arm, target, minimum_solver_tier
                    )
                _increment_ik_diagnostic(
                    diagnostics, "physical_stall_solver_escalations"
                )
                _increment_ik_diagnostic(
                    diagnostics, "same_target_cross_cycle_solver_escalations"
                )
                diagnostics["physical_stall_solver_tier_max"] = max(
                    int(diagnostics.get("physical_stall_solver_tier_max", 0)),
                    minimum_solver_tier,
                )
            _increment_ik_diagnostic(
                diagnostics,
                "cartesian_policy_action_physics_budget_exhaustions",
            )
            diagnostics["cartesian_policy_action_raw_physics_steps_max"] = max(
                int(
                    diagnostics.get("cartesian_policy_action_raw_physics_steps_max", 0)
                ),
                raw_steps_used,
            )
            if not made_any_progress and final_solver_tier_exhausted:
                remember_final_exhaustion()
            return finish("progressed" if made_any_progress else "stopped")
        if made_any_progress:
            return finish("progressed")
        if minimum_solver_tier < _HYBRID_SOLVER_TIER_COUNT - 1:
            minimum_solver_tier += 1
            for arm, target in normalized:
                _store_hybrid_same_target_solver_tier(arm, target, minimum_solver_tier)
            _increment_ik_diagnostic(diagnostics, "physical_stall_solver_escalations")
            diagnostics["physical_stall_solver_tier_max"] = max(
                int(diagnostics.get("physical_stall_solver_tier_max", 0)),
                minimum_solver_tier,
            )
            continue
        _increment_ik_diagnostic(
            diagnostics, "physical_stall_solver_escalation_exhaustions"
        )
        remember_final_exhaustion()
        return finish("progressed" if made_any_progress else "stopped")

    _increment_ik_diagnostic(diagnostics, "cartesian_multi_pass_limit_exhaustions")
    final_scores = {
        id(arm): _cartesian_residual_score(arm, target, config=config)
        for arm, target in normalized
    }
    overall_progressed, _partial = _cartesian_vector_made_progress(
        original_scores,
        final_scores,
    )
    if not overall_progressed and minimum_solver_tier == _HYBRID_SOLVER_TIER_COUNT - 1:
        remember_final_exhaustion()
    return finish("progressed" if overall_progressed else "stopped")


def xyzw_to_wxyz(pose: Array) -> Array:

    return _xyzw_to_wxyz(pose)


def wxyz_to_xyzw(pose: Array) -> Array:

    return _wxyz_to_xyzw(pose)


def unimanual_observation_from_rlbench(observation: Any, task: str | TaskSpec) -> Any:

    from litetwin.policy import DynaMACObservation

    spec = task if isinstance(task, TaskSpec) else get_task_spec(task)
    if spec.bimanual:
        raise ValueError(f"{spec.task_name} is bimanual")
    return DynaMACObservation(
        ee_pose=xyzw_to_wxyz(observation.gripper_pose),
        frames=spec.extract_pose_chunks(observation.task_low_dim_state),
    )


def bimanual_observations_from_rlbench(
    observation: Any,
    task: str | TaskSpec,
) -> tuple[Any, Any]:

    from litetwin.policy import DynaMACObservation

    spec = task if isinstance(task, TaskSpec) else get_task_spec(task)
    if not spec.bimanual:
        raise ValueError(f"{spec.task_name} is unimanual")
    frames = spec.extract_pose_chunks(observation.task_low_dim_state)
    return (
        DynaMACObservation(
            ee_pose=xyzw_to_wxyz(observation.left.gripper_pose),
            frames={name: value.copy() for name, value in frames.items()},
        ),
        DynaMACObservation(
            ee_pose=xyzw_to_wxyz(observation.right.gripper_pose),
            frames={name: value.copy() for name, value in frames.items()},
        ),
    )


def _gripper_to_rlbench(value: Array | float) -> float:
    scalar = float(np.asarray(value, dtype=np.float64).reshape(-1).mean())
    if not np.isfinite(scalar):
        raise ValueError("gripper prediction must be finite")

    return float(scalar > 0.0)


def unimanual_action_to_rlbench(
    action: Any, *, ignore_collisions: bool = False
) -> Array:

    pose = wxyz_to_xyzw(np.asarray(action.pose, dtype=np.float64))
    return np.concatenate(
        (pose, [_gripper_to_rlbench(action.gripper), float(ignore_collisions)])
    )


@dataclass(frozen=True)
class ArmActionOffset:

    arm: Literal["left", "right"]
    translation: tuple[float, float, float]

    def apply(self, left_pose: Array, right_pose: Array) -> tuple[Array, Array]:
        left = np.asarray(left_pose, dtype=np.float64).copy()
        right = np.asarray(right_pose, dtype=np.float64).copy()
        target = left if self.arm == "left" else right
        target[:3] += np.asarray(self.translation, dtype=np.float64)
        return left, right


def bimanual_action_to_rlbench(
    action: Any,
    *,
    left_ignore_collisions: bool = False,
    right_ignore_collisions: bool = False,
    offset: ArmActionOffset | None = None,
) -> Array:

    left_pose = np.asarray(action.left.pose, dtype=np.float64)
    right_pose = np.asarray(action.right.pose, dtype=np.float64)
    if offset is not None:
        left_pose, right_pose = offset.apply(left_pose, right_pose)
    right = np.concatenate(
        (
            wxyz_to_xyzw(right_pose),
            [
                _gripper_to_rlbench(action.right.gripper),
                float(right_ignore_collisions),
            ],
        )
    )
    left = np.concatenate(
        (
            wxyz_to_xyzw(left_pose),
            [
                _gripper_to_rlbench(action.left.gripper),
                float(left_ignore_collisions),
            ],
        )
    )
    result = np.concatenate((right, left))
    if result.shape != (18,):
        raise AssertionError(f"invalid RLBench bimanual action shape: {result.shape}")
    return result


def pose_execution_error(command_wxyz: Array, observed_xyzw: Array) -> dict[str, float]:
    command = np.asarray(command_wxyz, dtype=np.float64)
    observed = xyzw_to_wxyz(np.asarray(observed_xyzw, dtype=np.float64))
    position = float(np.linalg.norm(command[:3] - observed[:3]))
    q_command = command[3:7] / np.linalg.norm(command[3:7])
    q_observed = observed[3:7] / np.linalg.norm(observed[3:7])
    dot = float(np.clip(abs(np.dot(q_command, q_observed)), 0.0, 1.0))
    rotation = float(2.0 * math.acos(dot))
    return {"position_m": position, "rotation_rad": rotation}


__all__ = [
    "DEFAULT_FINAL_SETTLING_PHYSICS_STEPS",
    "DiscreteGripperProtocol",
    "GLOBAL_IK_CONTROLLER_PROFILE",
    "HYBRID_IK_CONTROLLER_PROFILE",
    "GlobalIKControllerConfig",
    "HybridIKControllerConfig",
    "apply_gripper_for_policy_target",
    "bimanual_action_to_rlbench",
    "bimanual_observations_from_rlbench",
    "commit_joint_hold_after_primary_failure",
    "execute_global_ik_ee_control",
    "execute_hybrid_ik_ee_control",
    "initialize_fresh_task_generation",
    "initialize_global_ik_controller_diagnostics",
    "policy_action_execution_status",
    "policy_action_execution_statuses",
    "pose_execution_error",
    "run_final_settling",
    "set_policy_gripper_authorization",
    "unimanual_action_to_rlbench",
    "unimanual_observation_from_rlbench",
    "wxyz_to_xyzw",
    "xyzw_to_wxyz",
]
