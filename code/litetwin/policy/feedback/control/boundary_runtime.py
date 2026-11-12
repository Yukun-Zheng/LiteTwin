"""Runtime records and calibrated parameters for guarded skill boundaries."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from ..model.boundary_model import BoundaryId
from .frame_roles import RelationVerificationRequest
from ..model.relation_events import RelationEventId
from ..model.state_index import StateId


class ConditionKind(str, Enum):
    LOCAL_PROGRESS = "local_progress"
    GUARD_RELATION = "guard_relation"
    GUARD_SCENE = "guard_scene"


@dataclass(frozen=True, order=True)
class ConditionId:
    kind: ConditionKind
    arm_id: str
    token: str

    def __post_init__(self) -> None:
        if not self.arm_id or not self.token:
            raise ValueError(
                "Boundary condition identifiers require a nonempty arm and token"
            )


@dataclass(frozen=True)
class ConditionResult:
    condition_id: ConditionId
    compatibility: float
    reliability: float
    threshold: float | None
    observed: bool
    stable: bool
    raw_satisfied: bool
    consecutive_cycles: int
    required_cycles: int
    satisfied: bool
    reason: str

    def __post_init__(self) -> None:
        for name in ("compatibility", "reliability"):
            value = float(getattr(self, name))
            if not np.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be in [0, 1]")
        if self.threshold is not None and not 0.0 <= self.threshold <= 1.0:
            raise ValueError("Condition threshold must be in [0, 1]")
        if self.consecutive_cycles < 0 or self.required_cycles < 1:
            raise ValueError("Invalid consecutive-cycle count")
        if self.satisfied != (
            self.raw_satisfied and self.consecutive_cycles >= self.required_cycles
        ):
            raise ValueError(
                "Stable condition state is inconsistent with the consecutive-cycle count"
            )


@dataclass(frozen=True)
class LocalCompletionResult:
    boundary_id: BoundaryId
    end_probability: float
    score: float
    threshold: float
    raw_satisfied: bool
    consecutive_cycles: int
    required_cycles: int
    done: bool
    evidence_available: bool

    def __post_init__(self) -> None:
        for name in (
            "end_probability",
            "score",
            "threshold",
        ):
            value = float(getattr(self, name))
            if not np.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be in [0, 1]")
        if self.consecutive_cycles < 0 or self.required_cycles < 1:
            raise ValueError("Invalid local-completion consecutive-cycle count")
        if self.done != (
            self.raw_satisfied and self.consecutive_cycles >= self.required_cycles
        ):
            raise ValueError(
                "Local-completion state is inconsistent with the consecutive-cycle count"
            )


@dataclass(frozen=True)
class TransitionPreparation:

    boundary_id: BoundaryId
    event_ids: tuple[RelationEventId, ...]
    gripper_command: np.ndarray

    def __post_init__(self) -> None:
        command = np.asarray(self.gripper_command, dtype=np.float64)
        if command.ndim != 1 or not len(command) or not np.all(np.isfinite(command)):
            raise ValueError(
                "Boundary-transition preparation gripper command must be a finite one-dimensional vector"
            )
        if not np.all(command <= 0.5):
            raise ValueError(
                "Boundary-transition preparation permits only relation-forming close commands"
            )
        if not self.event_ids:
            raise ValueError(
                "Boundary-transition preparation must be supported by an offline LINK event"
            )
        if any(
            event.arm_id != self.boundary_id.arm_id
            or event.transition not in {"link", "link_pending"}
            for event in self.event_ids
        ):
            raise ValueError(
                "Boundary-transition preparation event conflicts with the boundary arm or LINK semantics"
            )
        object.__setattr__(self, "gripper_command", command.copy())


@dataclass(frozen=True)
class TransitionRequest:
    tick: int
    arm_id: str
    boundary_id: BoundaryId
    permitted: bool
    source_state: StateId
    target_state: StateId
    local_done: bool
    condition_results: dict[ConditionId, ConditionResult]
    verification_requests: tuple[RelationVerificationRequest, ...] = ()
    transaction_group: str | None = None
    preparation: TransitionPreparation | None = None

    def __post_init__(self) -> None:
        if self.tick < 0 or not self.arm_id:
            raise ValueError("Transition request has an invalid tick or arm_id")
        if self.arm_id != self.boundary_id.arm_id:
            raise ValueError("Transition-request arm does not match the boundary")
        if self.source_state.skill_index != self.boundary_id.source_skill:
            raise ValueError(
                "Transition source state is not in the boundary source skill"
            )
        if self.target_state.skill_index != self.boundary_id.target_skill:
            raise ValueError(
                "Transition target state is not in the boundary target skill"
            )
        if self.permitted and not self.local_done:
            raise ValueError(
                "A boundary permit cannot be generated before local skill completion"
            )
        if self.permitted and any(
            not result.satisfied
            for condition_id, result in self.condition_results.items()
            if condition_id.kind
            in {ConditionKind.GUARD_RELATION, ConditionKind.GUARD_SCENE}
        ):
            raise ValueError(
                "A boundary permit cannot be generated before all required guard conditions are met"
            )
        if self.preparation is not None:
            if self.preparation.boundary_id != self.boundary_id:
                raise ValueError(
                    "Transition preparation action does not match the transition-request boundary"
                )
            if self.permitted:
                raise ValueError(
                    "An uncommitted preparation action cannot be generated after the boundary is permitted"
                )


@dataclass(frozen=True)
class BoundaryCalibration:
    local_score_threshold: float
    confirmation_cycles: int
    relation_thresholds: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 0.0 <= self.local_score_threshold <= 1.0:
            raise ValueError("Local-completion threshold must be in [0, 1]")
        if self.confirmation_cycles < 1:
            raise ValueError(
                "The consecutive-confirmation cycle count must be a positive integer"
            )
        if any(not 0.5 < value <= 1.0 for value in self.relation_thresholds.values()):
            raise ValueError("Relation-guard threshold must be in (0.5, 1]")


@dataclass(frozen=True)
class BoundaryRuntimeConfig:
    calibrations: dict[str, BoundaryCalibration]
    default_relation_probability: float = 0.70
    minimum_tracking_reliability: float = 0.25
    minimum_scene_reliability: float = 0.25
    minimum_information_weight: float = 0.10

    def __post_init__(self) -> None:
        if not 0.5 < self.default_relation_probability <= 1.0:
            raise ValueError("Default relation threshold must be in (0.5, 1]")
        for value in (
            self.minimum_tracking_reliability,
            self.minimum_scene_reliability,
            self.minimum_information_weight,
        ):
            if not 0.0 <= value <= 1.0:
                raise ValueError(
                    "Boundary-guard reliability parameters must be in [0, 1]"
                )

    def calibration_for(self, boundary_id: BoundaryId) -> BoundaryCalibration:
        try:
            return self.calibrations[boundary_id.token]
        except KeyError as exc:
            raise KeyError(
                f"Boundary {boundary_id.token} has not been calibrated on nominal data"
            ) from exc

    def relation_threshold(self, boundary_id: BoundaryId, condition: str) -> float:
        calibration = self.calibration_for(boundary_id)
        return float(
            calibration.relation_thresholds.get(
                condition, self.default_relation_probability
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "default_relation_probability": self.default_relation_probability,
            "minimum_tracking_reliability": self.minimum_tracking_reliability,
            "minimum_scene_reliability": self.minimum_scene_reliability,
            "minimum_information_weight": self.minimum_information_weight,
            "calibrations": {
                token: {
                    "local_score_threshold": value.local_score_threshold,
                    "confirmation_cycles": value.confirmation_cycles,
                    "relation_thresholds": dict(value.relation_thresholds),
                }
                for token, value in sorted(self.calibrations.items())
            },
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> BoundaryRuntimeConfig:
        known = {
            "default_relation_probability",
            "minimum_tracking_reliability",
            "minimum_scene_reliability",
            "minimum_information_weight",
            "calibrations",
        }
        unknown = set(value).difference(known)
        if unknown:
            raise ValueError(
                f"Boundary-guard configuration contains unknown fields: {sorted(unknown)}"
            )
        raw_calibrations = value.get("calibrations", {})
        if not isinstance(raw_calibrations, Mapping):
            raise TypeError("Boundary-guard calibrations must be an object")
        calibrations = {}
        for token, raw in raw_calibrations.items():
            if not isinstance(token, str) or not isinstance(raw, Mapping):
                raise TypeError("Invalid boundary-guard calibration entry")
            calibrations[token] = BoundaryCalibration(
                local_score_threshold=float(raw["local_score_threshold"]),
                confirmation_cycles=int(raw["confirmation_cycles"]),
                relation_thresholds={
                    str(name): float(threshold)
                    for name, threshold in dict(
                        raw.get("relation_thresholds", {})
                    ).items()
                },
            )
        return cls(
            calibrations=calibrations,
            default_relation_probability=float(
                value.get("default_relation_probability", 0.70)
            ),
            minimum_tracking_reliability=float(
                value.get("minimum_tracking_reliability", 0.25)
            ),
            minimum_scene_reliability=float(
                value.get("minimum_scene_reliability", 0.25)
            ),
            minimum_information_weight=float(
                value.get("minimum_information_weight", 0.10)
            ),
        )

    @classmethod
    def from_json(cls, path: str | Path) -> BoundaryRuntimeConfig:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(value, Mapping):
            raise TypeError("Boundary-guard configuration root must be an object")
        return cls.from_mapping(value)


__all__ = [
    "BoundaryCalibration",
    "BoundaryRuntimeConfig",
    "ConditionId",
    "ConditionKind",
    "ConditionResult",
    "LocalCompletionResult",
    "TransitionPreparation",
    "TransitionRequest",
]
