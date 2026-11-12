"""Translate the pinned RLBench low-dimensional wire format into core inputs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import numpy as np

from litetwin.policy import DynaMACObservation
from litetwin.policy.feedback import RuntimeObservation
from integrations.rlbench.rlbench_dynamac.core.gripper_timing import (
    native_gripper_to_wire,
)
from integrations.rlbench.rlbench_dynamac.core.task_specs import (
    TaskSpec,
    wxyz_to_xyzw,
    xyzw_to_wxyz,
)


def _signed_gripper(value: Any) -> np.ndarray:
    scalar = float(value)
    if not np.isfinite(scalar):
        raise ValueError("RLBench gripper_open must be a finite scalar")
    return np.asarray([2.0 * float(scalar >= 0.5) - 1.0], dtype=np.float64)


def _arm_payload(payload: Mapping[str, Any], arm: str) -> Mapping[str, Any]:
    value = payload if arm == "single" else payload[arm]
    if not isinstance(value, Mapping):
        raise TypeError(f"RLBench observation for arm {arm} must be an object")
    return value


@dataclass(frozen=True)
class AdaptedObservationBatch:
    dynamac: dict[str, DynaMACObservation]
    runtime: dict[str, RuntimeObservation]


class LiteTwinObservationAdapter:

    def __init__(self, task_spec: TaskSpec) -> None:
        self.task_spec = task_spec
        self.arms = ("left", "right") if task_spec.bimanual else ("single",)

    def build(
        self,
        payload: Mapping[str, Any],
        *,
        tick: int,
        previous_ee_pose: Mapping[str, np.ndarray | None],
        previous_command_pose: Mapping[str, np.ndarray | None],
        previous_command_covariance: Mapping[str, np.ndarray | None] | None = None,
    ) -> AdaptedObservationBatch:
        task_state = payload.get("task_low_dim_state")
        all_poses = self.task_spec.extract_pose_chunks(task_state)
        frames = {name: all_poses[name] for name in self.task_spec.action_frame_names}
        scene_entity_poses = {
            name: all_poses[name] for name in self.task_spec.scene_entity_names
        }
        entity_configurations = self.task_spec.extract_entity_configurations(task_state)
        ee = {
            arm: xyzw_to_wxyz(
                np.asarray(_arm_payload(payload, arm)["gripper_pose"], dtype=np.float64)
            )
            for arm in self.arms
        }
        grippers = {
            arm: _signed_gripper(_arm_payload(payload, arm)["gripper_open"])
            for arm in self.arms
        }

        dynamac: dict[str, DynaMACObservation] = {}
        runtime: dict[str, RuntimeObservation] = {}
        for arm in self.arms:
            arm_frames = {name: pose.copy() for name, pose in frames.items()}
            if self.task_spec.bimanual:
                opposite = "right" if arm == "left" else "left"
                arm_frames[f"{opposite}_ee"] = ee[opposite].copy()
            runtime_frames = {**arm_frames, **scene_entity_poses}
            visible = {name: True for name in runtime_frames}
            reliable = {name: 1.0 for name in runtime_frames}
            current = DynaMACObservation(ee[arm], arm_frames)
            dynamac[arm] = current
            runtime[arm] = RuntimeObservation.from_dynamac(
                current,
                tick=tick,
                gripper_state=grippers[arm],
                previous_command_pose=previous_command_pose.get(arm),
                previous_command_covariance=(
                    None
                    if previous_command_covariance is None
                    else previous_command_covariance.get(arm)
                ),
                previous_ee_pose=previous_ee_pose.get(arm),
                tracking_reliability=reliable,
                frame_visibility=visible,
                additional_frame_poses={
                    **scene_entity_poses,
                    f"{arm}_ee": ee[arm],
                },
                entity_configurations=entity_configurations,
            )
        return AdaptedObservationBatch(dynamac, runtime)


def commands_to_rlbench(
    commands: Mapping[str, Any],
    *,
    bimanual: bool,
) -> np.ndarray:

    def lane(arm: str) -> np.ndarray:
        command = commands[arm]
        return np.concatenate(
            (
                wxyz_to_xyzw(command.pose),
                [native_gripper_to_wire(command.gripper), 0.0],
            )
        )

    if not bimanual:
        result = lane("single")
        if result.shape != (9,):
            raise AssertionError(
                "Closed-loop single-arm RLBench action dimension must be 9"
            )
        return result
    result = np.concatenate((lane("right"), lane("left")))
    if result.shape != (18,):
        raise AssertionError("Closed-loop bimanual RLBench action dimension must be 18")
    return result


__all__ = [
    "AdaptedObservationBatch",
    "LiteTwinObservationAdapter",
    "commands_to_rlbench",
]
