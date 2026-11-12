"""Episode-scoped LINK anchor resolution and world-frame instantiation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np

from ...dynamac import transform_marginal
from .relation_events import (
    LinkPendingCandidate,
    LinkRecoveryAnchor,
    RelationEventId,
    RelationStateKey,
)
from .state_index import StateId
from .task_model import LiteTwinTaskModel

Array = np.ndarray
AnchorSource = Literal["offline_link", "pending_recovery", "verified_pending"]


@dataclass(frozen=True)
class RuntimeLinkAnchor:

    origin_event_id: RelationEventId
    arm_id: str
    frame_id: str
    context_state: StateId
    local_means: Array
    local_covariances: Array
    gripper_commands: Array
    legal_reentry_states: tuple[StateId, ...]
    source: AnchorSource

    def __post_init__(self) -> None:
        means = np.asarray(self.local_means, dtype=np.float64)
        covariances = np.asarray(self.local_covariances, dtype=np.float64)
        gripper = np.asarray(self.gripper_commands, dtype=np.float64)
        if means.ndim != 2 or means.shape[1] != 7 or not len(means):
            raise ValueError(
                "Runtime LINK anchor must contain a nonempty [H, 7] local trajectory"
            )
        if covariances.shape != (len(means), 6, 6):
            raise ValueError("Runtime LINK anchor covariance must have shape [H, 6, 6]")
        if gripper.ndim != 2 or gripper.shape[0] != len(means):
            raise ValueError(
                "Runtime LINK anchor gripper commands must have shape [H, G]"
            )
        if not self.legal_reentry_states:
            raise ValueError(
                "Runtime LINK anchor must provide at least one legal re-entry state"
            )
        if self.source not in {
            "offline_link",
            "pending_recovery",
            "verified_pending",
        }:
            raise ValueError("Unknown LINK anchor source")
        object.__setattr__(self, "local_means", means.copy())
        object.__setattr__(self, "local_covariances", covariances.copy())
        object.__setattr__(self, "gripper_commands", gripper.copy())

    @classmethod
    def from_offline(
        cls,
        anchor: LinkRecoveryAnchor,
        *,
        legal_reentry_states: tuple[StateId, ...] | None = None,
    ) -> RuntimeLinkAnchor:
        if not anchor.linked_entry_states:
            raise ValueError("Formal LINK anchor has no legal linked state")
        legal = (
            anchor.linked_entry_states
            if legal_reentry_states is None
            else tuple(dict.fromkeys(legal_reentry_states))
        )
        if not legal:
            raise ValueError(
                "Formal LINK anchor has no re-entry state with sustained relation support"
            )
        return cls(
            origin_event_id=anchor.event_id,
            arm_id=anchor.arm_id,
            frame_id=anchor.frame_id,
            context_state=anchor.context_state,
            local_means=anchor.local_means,
            local_covariances=anchor.local_covariances,
            gripper_commands=anchor.gripper_commands,
            legal_reentry_states=legal,
            source="offline_link",
        )

    @classmethod
    def from_pending(
        cls,
        candidate: LinkPendingCandidate,
        *,
        verified: bool,
    ) -> RuntimeLinkAnchor:
        return cls(
            origin_event_id=candidate.event_id,
            arm_id=candidate.arm_id,
            frame_id=candidate.frame_id,
            context_state=candidate.context_state,
            local_means=candidate.local_means,
            local_covariances=candidate.local_covariances,
            gripper_commands=candidate.gripper_commands,
            legal_reentry_states=(candidate.candidate_state,),
            source="verified_pending" if verified else "pending_recovery",
        )


@dataclass(frozen=True)
class InstantiatedLinkWaypoint:
    sequence_index: int
    pose: Array
    covariance: Array
    gripper_command: Array

    def __post_init__(self) -> None:
        pose = np.asarray(self.pose, dtype=np.float64)
        covariance = np.asarray(self.covariance, dtype=np.float64)
        gripper = np.asarray(self.gripper_command, dtype=np.float64)
        if self.sequence_index < 0:
            raise ValueError("LINK recovery waypoint index must be nonnegative")
        if pose.shape != (7,) or covariance.shape != (6, 6):
            raise ValueError(
                "LINK recovery waypoints require a [7] pose and [6, 6] covariance"
            )
        if gripper.ndim != 1 or not len(gripper):
            raise ValueError(
                "LINK recovery waypoint gripper command must be a nonempty one-dimensional array"
            )
        if not np.all(np.isfinite(pose)) or not np.all(np.isfinite(covariance)):
            raise ValueError("LINK recovery waypoint contains nonfinite values")
        object.__setattr__(self, "pose", pose.copy())
        object.__setattr__(self, "covariance", covariance.copy())
        object.__setattr__(self, "gripper_command", gripper.copy())


class EpisodeLinkAnchorRegistry:

    def __init__(self, task_model: LiteTwinTaskModel) -> None:
        self.task_model = task_model
        self.reset()

    def reset(self) -> None:
        self._active_pending: dict[str, RuntimeLinkAnchor] = {}

    def _offline_anchor(self, event_id: RelationEventId) -> RuntimeLinkAnchor:

        anchor = self.task_model.link_anchors[event_id]
        persistent_states = tuple(
            sorted(
                key.state_id
                for key, origin in self.task_model.link_origins.items()
                if origin == event_id
            )
        )
        return RuntimeLinkAnchor.from_offline(
            anchor,
            legal_reentry_states=(persistent_states or anchor.linked_entry_states),
        )

    @property
    def active_pending(self) -> dict[str, RuntimeLinkAnchor]:
        return dict(self._active_pending)

    def activate_pending(self, event_id: RelationEventId) -> RuntimeLinkAnchor:
        try:
            candidate = self.task_model.link_pending_events[event_id]
        except KeyError as exc:
            raise KeyError(
                f"LINK_PENDING candidate does not exist: {event_id.token}"
            ) from exc
        anchor = RuntimeLinkAnchor.from_pending(candidate, verified=True)
        self._active_pending[candidate.frame_id] = anchor
        return anchor

    def resolve_event_for_recovery(
        self,
        event_id: RelationEventId,
    ) -> RuntimeLinkAnchor:

        if event_id.arm_id != self.task_model.arm_id:
            raise ValueError("LINK recovery event does not belong to the current arm")
        if event_id.transition == "link":
            try:
                return self._offline_anchor(event_id)
            except KeyError as exc:
                raise KeyError(
                    f"Formal LINK event does not exist: {event_id.token}"
                ) from exc
        if event_id.transition == "link_pending":
            try:
                return RuntimeLinkAnchor.from_pending(
                    self.task_model.link_pending_events[event_id],
                    verified=False,
                )
            except KeyError as exc:
                raise KeyError(
                    f"LINK_PENDING event does not exist: {event_id.token}"
                ) from exc
        raise ValueError("LINK recovery event must be link or link_pending")

    def _pending_recovery_candidate(
        self,
        frame_id: str,
        state_id: StateId,
        mode: int,
    ) -> LinkPendingCandidate | None:

        latest_unlink = max(
            (
                metadata.release_state
                for event_id, metadata in self.task_model.unlink_events.items()
                if event_id.arm_id == self.task_model.arm_id
                and event_id.frame_id == frame_id
                and event_id.mode == mode
                and metadata.release_state <= state_id
            ),
            default=None,
        )
        candidates = [
            candidate
            for event_id, candidate in self.task_model.link_pending_events.items()
            if event_id.arm_id == self.task_model.arm_id
            and event_id.frame_id == frame_id
            and event_id.mode == mode
            and candidate.candidate_state <= state_id
            and (latest_unlink is None or candidate.candidate_state > latest_unlink)
        ]
        if not candidates:
            return None
        latest_state = max(candidate.candidate_state for candidate in candidates)
        latest = [
            candidate
            for candidate in candidates
            if candidate.candidate_state == latest_state
        ]
        if len(latest) != 1:
            raise RuntimeError(
                "The same arm-frame-mode state matches multiple LINK_PENDING occurrences"
            )
        return latest[0]

    def has_pending_recovery_candidate(
        self,
        frame_id: str,
        state_id: StateId,
        mode: int,
    ) -> bool:
        return self._pending_recovery_candidate(frame_id, state_id, mode) is not None

    def resolve_for_recovery(
        self,
        frame_id: str,
        state_id: StateId,
        mode: int,
    ) -> RuntimeLinkAnchor:

        try:
            return self.resolve(frame_id, state_id, mode)
        except KeyError as original_error:
            candidate = self._pending_recovery_candidate(frame_id, state_id, mode)
            if candidate is None:
                raise original_error
            return RuntimeLinkAnchor.from_pending(candidate, verified=False)

    def release(self, frame_id: str) -> None:

        self._active_pending.pop(frame_id, None)

    def resolve(
        self,
        frame_id: str,
        state_id: StateId,
        mode: int,
    ) -> RuntimeLinkAnchor:
        key = RelationStateKey(self.task_model.arm_id, frame_id, state_id, mode)
        relation_event = self.task_model.link_origins.get(key)
        if relation_event is not None:
            return self._offline_anchor(relation_event)

        runtime = self._active_pending.get(frame_id)
        if runtime is not None:
            return runtime

        candidates = [
            anchor
            for event_id, anchor in self.task_model.link_anchors.items()
            if event_id.arm_id == self.task_model.arm_id
            and event_id.frame_id == frame_id
            and event_id.mode == mode
            and state_id in anchor.linked_entry_states
        ]
        if len(candidates) == 1:
            return self._offline_anchor(candidates[0].event_id)
        if not candidates:
            raise KeyError(
                f"Relation {self.task_model.arm_id}/{frame_id} at state {state_id} "
                "has no event-level LINK origin"
            )
        raise RuntimeError(
            "The same relation state matches multiple LINK event anchors"
        )

    @staticmethod
    def instantiate(
        anchor: RuntimeLinkAnchor,
        frame_pose: Array,
        covariance_inflation: float,
    ) -> tuple[InstantiatedLinkWaypoint, ...]:
        if covariance_inflation < 0.0 or not np.isfinite(covariance_inflation):
            raise ValueError(
                "Recovery covariance relaxation must be finite and nonnegative"
            )
        result = []
        for index, (mean, covariance, gripper) in enumerate(
            zip(
                anchor.local_means,
                anchor.local_covariances,
                anchor.gripper_commands,
                strict=True,
            )
        ):
            widened = covariance + np.eye(6, dtype=np.float64) * covariance_inflation
            marginal = transform_marginal(
                anchor.frame_id,
                frame_pose,
                mean,
                widened,
            )
            result.append(
                InstantiatedLinkWaypoint(
                    sequence_index=index,
                    pose=marginal.mean,
                    covariance=marginal.covariance,
                    gripper_command=gripper,
                )
            )
        return tuple(result)


__all__ = [
    "EpisodeLinkAnchorRegistry",
    "InstantiatedLinkWaypoint",
    "RuntimeLinkAnchor",
]
