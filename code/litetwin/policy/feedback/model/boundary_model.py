"""Offline skill-boundary models shared by entry guards and recovery reentry."""

from __future__ import annotations

from dataclasses import dataclass, field

from .scene_factors import FactorDistribution, FactorId
from .state_index import StateId


@dataclass(frozen=True, order=True)
class BoundaryId:
    arm_id: str
    source_skill: int
    target_skill: int

    def __post_init__(self) -> None:
        if not self.arm_id:
            raise ValueError("BoundaryId requires a nonempty arm_id")
        if self.source_skill < 0 or self.target_skill != self.source_skill + 1:
            raise ValueError("BoundaryId must connect adjacent skills")

    @property
    def token(self) -> str:
        return f"{self.arm_id}:{self.source_skill}->{self.target_skill}"


@dataclass(frozen=True)
class LocalCompletionModel:
    terminal_states: tuple[StateId, ...]
    own_relation_conditions: dict[str, RelationGuardDistribution] = field(
        default_factory=dict
    )


@dataclass(frozen=True)
class RelationGuardDistribution:
    external: float
    linked: float
    required_state: str

    def __post_init__(self) -> None:
        if self.required_state not in {"external", "linked"}:
            raise ValueError("Boundary relation target must be external or linked")
        if self.external < 0.0 or self.linked < 0.0:
            raise ValueError(
                "Boundary relation-support probabilities cannot be negative"
            )
        if abs(self.external + self.linked - 1.0) > 1.0e-9:
            raise ValueError(
                "Boundary relation-support probabilities must be normalized"
            )


@dataclass(frozen=True)
class ReliabilityStatistics:
    observed_fraction: float
    stable_fraction: float


@dataclass(frozen=True)
class BoundaryModel:
    boundary_id: BoundaryId
    source_skill: int
    target_skill: int
    terminal_window: tuple[StateId, ...]
    local_completion_model: LocalCompletionModel
    relation_conditions: dict[str, RelationGuardDistribution]
    scene_conditions: dict[FactorId, FactorDistribution]
    scene_condition_thresholds: dict[FactorId, float]
    condition_reliability: dict[str, ReliabilityStatistics]
    affected_arms: tuple[str, ...]
    transaction_group: str | None = None

    def __post_init__(self) -> None:
        if self.source_skill != self.boundary_id.source_skill:
            raise ValueError("Boundary source_skill does not match BoundaryId")
        if self.target_skill != self.boundary_id.target_skill:
            raise ValueError("Boundary target_skill does not match BoundaryId")
        if not self.terminal_window:
            raise ValueError("Boundary terminal window cannot be empty")
        if not self.affected_arms:
            raise ValueError("A boundary must affect at least one arm")
        if set(self.scene_condition_thresholds) != set(self.scene_conditions):
            raise ValueError(
                "Boundary scene conditions and compatibility thresholds must correspond one-to-one"
            )
        overlap = set(self.local_completion_model.own_relation_conditions).intersection(
            self.relation_conditions
        )
        if overlap:
            raise ValueError(
                f"Local-completion relations cannot be duplicated as boundary relation conditions: {sorted(overlap)}"
            )
        expected_reliability = (
            set(self.local_completion_model.own_relation_conditions)
            .union(self.relation_conditions)
            .union(factor_id.token for factor_id in self.scene_conditions)
        )
        if set(self.condition_reliability) != expected_reliability:
            raise ValueError(
                "Each stored boundary condition must have exactly one reliability statistic"
            )
        if any(
            not 0.0 <= value <= 1.0
            for value in self.scene_condition_thresholds.values()
        ):
            raise ValueError("Boundary scene-compatibility threshold must be in [0, 1]")


__all__ = [
    "BoundaryId",
    "BoundaryModel",
    "LocalCompletionModel",
    "RelationGuardDistribution",
    "ReliabilityStatistics",
]
