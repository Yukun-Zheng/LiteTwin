"""Selection and instantiation of event-level UNLINK metadata."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ...dynamac import pose_compose
from .relation_events import UnlinkEventMetadata
from .state_index import StateId
from .task_model import LiteTwinTaskModel

Array = np.ndarray


@dataclass(frozen=True)
class InstantiatedUnlinkTarget:
    metadata: UnlinkEventMetadata
    pose: Array
    used_learned_target: bool

    def __post_init__(self) -> None:
        pose = np.asarray(self.pose, dtype=np.float64)
        if pose.shape != (7,) or not np.all(np.isfinite(pose)):
            raise ValueError("UNLINK separation target must be a finite [7] pose")
        object.__setattr__(self, "pose", pose.copy())


class UnlinkMetadataRepository:

    def __init__(self, task_model: LiteTwinTaskModel) -> None:
        self.task_model = task_model
        self._states = tuple(sorted(task_model.states))
        self._global_index = {state: index for index, state in enumerate(self._states)}

    def resolve(
        self,
        frame_id: str,
        state_id: StateId,
        mode: int,
    ) -> UnlinkEventMetadata:
        if state_id not in self._global_index:
            raise KeyError(f"UNLINK query state does not exist: {state_id}")
        current = self._global_index[state_id]
        candidates = [
            event
            for event_id, event in self.task_model.unlink_events.items()
            if event_id.arm_id == self.task_model.arm_id
            and event_id.frame_id == frame_id
            and event_id.mode == mode
        ]
        if not candidates:
            raise KeyError(
                f"Relation {self.task_model.arm_id}/{frame_id} in mode {mode} "
                "has no UNLINK metadata"
            )

        preceding = [
            event
            for event in candidates
            if self._global_index[event.release_state] <= current
        ]
        if preceding:
            return max(
                preceding,
                key=lambda event: self._global_index[event.release_state],
            )
        return min(
            candidates,
            key=lambda event: abs(self._global_index[event.release_state] - current),
        )

    @staticmethod
    def instantiate(
        metadata: UnlinkEventMetadata,
        frame_pose: Array,
        current_ee_pose: Array,
        *,
        fallback_distance: float,
    ) -> InstantiatedUnlinkTarget:
        frame = np.asarray(frame_pose, dtype=np.float64)
        current = np.asarray(current_ee_pose, dtype=np.float64)
        if frame.shape != (7,) or current.shape != (7,):
            raise ValueError(
                "UNLINK instantiation requires [7] frame and end-effector poses"
            )
        if fallback_distance <= 0.0 or not np.isfinite(fallback_distance):
            raise ValueError(
                "UNLINK generic separation distance must be finite and positive"
            )
        if metadata.local_detachment_target is not None:
            return InstantiatedUnlinkTarget(
                metadata=metadata,
                pose=pose_compose(frame, metadata.local_detachment_target),
                used_learned_target=True,
            )

        direction = current[:3] - frame[:3]
        norm = float(np.linalg.norm(direction))
        if norm <= 1.0e-8:
            direction = np.asarray([0.0, 0.0, 1.0], dtype=np.float64)
        else:
            direction /= norm
        target = current.copy()
        target[:3] += fallback_distance * direction
        return InstantiatedUnlinkTarget(
            metadata=metadata,
            pose=target,
            used_learned_target=False,
        )


__all__ = ["InstantiatedUnlinkTarget", "UnlinkMetadataRepository"]
