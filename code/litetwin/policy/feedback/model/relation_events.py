"""Event-level LINK anchors and UNLINK reentry metadata."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .state_index import StateId

Array = np.ndarray


@dataclass(frozen=True, order=True)
class RelationEventId:
    arm_id: str
    frame_id: str
    skill_index: int
    mode: int
    occurrence: int
    transition: str

    def __post_init__(self) -> None:
        if not self.arm_id or not self.frame_id:
            raise ValueError(
                "Relation events require nonempty arm and frame identifiers"
            )
        if self.skill_index < 0 or self.mode < 0 or self.occurrence < 0:
            raise ValueError("Relation event index must be a nonnegative integer")
        if self.transition not in {"link", "link_pending", "unlink"}:
            raise ValueError(
                "Relation event transition must be link, link_pending, or unlink"
            )

    @property
    def token(self) -> str:
        return (
            f"{self.arm_id}:{self.frame_id}:k{self.skill_index}:m{self.mode}:"
            f"{self.transition}:{self.occurrence}"
        )


@dataclass(frozen=True, order=True)
class RelationStateKey:

    arm_id: str
    frame_id: str
    state_id: StateId
    mode: int

    def __post_init__(self) -> None:
        if not self.arm_id or not self.frame_id:
            raise ValueError(
                "Relation-state keys require nonempty arm and frame identifiers"
            )
        if self.mode < 0:
            raise ValueError("Relation-state key mode must be a nonnegative integer")

    @property
    def token(self) -> str:
        return (
            f"{self.arm_id}:{self.frame_id}:k{self.state_id.skill_index}:"
            f"t{self.state_id.local_index}:m{self.mode}"
        )


@dataclass(frozen=True)
class LinkRecoveryAnchor:
    event_id: RelationEventId
    arm_id: str
    frame_id: str
    context_state: StateId
    local_means: Array
    local_covariances: Array
    gripper_commands: Array

    linked_entry_states: tuple[StateId, ...]
    support_fraction: float = 1.0

    demonstration_indices: tuple[int, ...] = ()
    event_local_indices: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        means = np.asarray(self.local_means, dtype=np.float64)
        covariance = np.asarray(self.local_covariances, dtype=np.float64)
        gripper = np.asarray(self.gripper_commands, dtype=np.float64)
        if means.ndim != 2 or means.shape[1] != 7:
            raise ValueError("LINK anchor means must have shape [H, 7]")
        if covariance.shape != (len(means), 6, 6):
            raise ValueError("LINK anchor covariances must have shape [H, 6, 6]")
        if gripper.ndim != 2 or gripper.shape[0] != len(means):
            raise ValueError("LINK anchor gripper commands must have shape [H, G]")
        if not 0.0 <= self.support_fraction <= 1.0:
            raise ValueError("LINK event demonstration support must be in [0, 1]")
        if len(self.demonstration_indices) != len(self.event_local_indices):
            raise ValueError(
                "LINK event demonstration indices and aligned positions must correspond one-to-one"
            )
        object.__setattr__(self, "local_means", means.copy())
        object.__setattr__(self, "local_covariances", covariance.copy())
        object.__setattr__(self, "gripper_commands", gripper.copy())


@dataclass(frozen=True)
class LinkPendingCandidate:

    event_id: RelationEventId
    arm_id: str
    frame_id: str
    candidate_state: StateId
    context_state: StateId
    local_means: Array
    local_covariances: Array
    gripper_commands: Array

    release_state: StateId | None = None
    support_fraction: float = 1.0
    demonstration_indices: tuple[int, ...] = ()
    event_local_indices: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if self.event_id.transition != "link_pending":
            raise ValueError(
                "LINK_PENDING candidate must use a link_pending event identifier"
            )
        if (
            self.event_id.arm_id != self.arm_id
            or self.event_id.frame_id != self.frame_id
            or self.event_id.skill_index != self.candidate_state.skill_index
        ):
            raise ValueError(
                "LINK_PENDING candidate has inconsistent arm, frame, or skill"
            )
        if not 0.0 <= self.support_fraction <= 1.0:
            raise ValueError(
                "LINK_PENDING candidate demonstration support must be in [0, 1]"
            )
        if len(self.demonstration_indices) != len(self.event_local_indices):
            raise ValueError(
                "LINK_PENDING demonstration indices and aligned positions must correspond one-to-one"
            )
        if (
            self.release_state is not None
            and self.release_state <= self.candidate_state
        ):
            raise ValueError(
                "LINK_PENDING release state must follow the candidate gripper-close state"
            )
        means = np.asarray(self.local_means, dtype=np.float64)
        covariance = np.asarray(self.local_covariances, dtype=np.float64)
        gripper = np.asarray(self.gripper_commands, dtype=np.float64)
        if means.ndim != 2 or means.shape[1] != 7 or not len(means):
            raise ValueError(
                "LINK_PENDING local trajectory means must have nonempty shape [H, 7]"
            )
        if covariance.shape != (len(means), 6, 6):
            raise ValueError(
                "LINK_PENDING local trajectory covariances must have shape [H, 6, 6]"
            )
        if gripper.ndim != 2 or gripper.shape[0] != len(means):
            raise ValueError("LINK_PENDING gripper commands must have shape [H, G]")
        object.__setattr__(self, "local_means", means.copy())
        object.__setattr__(self, "local_covariances", covariance.copy())
        object.__setattr__(self, "gripper_commands", gripper.copy())


@dataclass(frozen=True)
class UnlinkEventMetadata:
    event_id: RelationEventId
    arm_id: str
    frame_id: str
    release_state: StateId
    legal_reentry_states: tuple[StateId, ...]
    local_detachment_target: Array | None = None
    support_fraction: float = 1.0

    demonstration_indices: tuple[int, ...] = ()
    event_local_indices: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if not 0.0 <= self.support_fraction <= 1.0:
            raise ValueError("UNLINK event demonstration support must be in [0, 1]")
        if len(self.demonstration_indices) != len(self.event_local_indices):
            raise ValueError(
                "UNLINK event demonstration indices and aligned positions must correspond one-to-one"
            )
        if self.local_detachment_target is not None:
            target = np.asarray(self.local_detachment_target, dtype=np.float64)
            if target.shape != (7,) or not np.all(np.isfinite(target)):
                raise ValueError(
                    "UNLINK local separation target must be a finite [7] pose"
                )
            object.__setattr__(self, "local_detachment_target", target.copy())


__all__ = [
    "LinkPendingCandidate",
    "LinkRecoveryAnchor",
    "RelationEventId",
    "RelationStateKey",
    "UnlinkEventMetadata",
]
