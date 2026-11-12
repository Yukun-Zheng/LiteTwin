"""Action-conditioned nominal progress prior for LiteTwin inference."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import numpy as np

from ..model.boundary_model import BoundaryId
from ..model.state_index import StateId
from ..model.task_model import LiteTwinTaskModel


def _normalize(values: Mapping[StateId, float]) -> dict[StateId, float]:
    if any(not np.isfinite(value) or value < 0.0 for value in values.values()):
        raise ValueError("Progress probabilities must be finite and nonnegative")
    total = float(sum(values.values()))
    if not np.isfinite(total) or total <= 0.0:
        raise ValueError("Progress probabilities must have a positive finite sum")
    return {state: float(value / total) for state, value in values.items()}


@dataclass(frozen=True)
class ProgressPriorConfig:
    incomplete_weight: float = 0.20
    normal_completion_weight: float = 0.65
    early_completion_weight: float = 0.15
    local_backward_radius: int = 1
    local_forward_radius: int = 2

    def __post_init__(self) -> None:
        weights = np.asarray(
            [
                self.incomplete_weight,
                self.normal_completion_weight,
                self.early_completion_weight,
            ],
            dtype=np.float64,
        )
        if np.any(weights < 0.0) or not np.isclose(np.sum(weights), 1.0):
            raise ValueError(
                "Nominal progress-transition weights must be nonnegative and normalized"
            )
        if self.local_backward_radius < 0 or self.local_forward_radius < 0:
            raise ValueError("Local candidate radius must be nonnegative")


@dataclass(frozen=True)
class ProgressPrior:
    probabilities: dict[StateId, float]
    nominal_state: StateId
    candidates: tuple[StateId, ...]


class ProgressPriorBuilder:
    def __init__(
        self,
        task_model: LiteTwinTaskModel,
        config: ProgressPriorConfig = ProgressPriorConfig(),
    ) -> None:
        self.task_model = task_model
        self.config = config
        self._states = tuple(sorted(task_model.states))
        self._global_index = {state: index for index, state in enumerate(self._states)}

    def _boundary_allowed(
        self,
        source: StateId,
        target: StateId,
        permitted_boundaries: frozenset[BoundaryId],
    ) -> bool:
        if source.skill_index == target.skill_index:
            return True
        boundary_id = BoundaryId(
            self.task_model.arm_id, source.skill_index, target.skill_index
        )
        return boundary_id in permitted_boundaries

    def _advance(
        self,
        state: StateId,
        steps: int,
        permitted_boundaries: frozenset[BoundaryId],
    ) -> StateId:
        current = state
        for _ in range(steps):
            successors = self.task_model.states[current].topology.successors
            if not successors:
                break
            successor = successors[0]
            if not self._boundary_allowed(current, successor, permitted_boundaries):
                break
            crossed = successor.skill_index != current.skill_index
            current = successor

            if crossed:
                break
        return current

    def build(
        self,
        previous_posterior: Mapping[StateId, float],
        *,
        executed_reference_state: StateId | None = None,
        action_executed: bool = True,
        permitted_boundaries: frozenset[BoundaryId] = frozenset(),
    ) -> ProgressPrior:
        unknown = set(previous_posterior).difference(self.task_model.states)
        if unknown:
            raise KeyError(
                f"Progress posterior contains unknown states: {sorted(unknown)}"
            )
        posterior = _normalize(previous_posterior)
        if (
            executed_reference_state is not None
            and executed_reference_state not in self.task_model.states
        ):
            raise KeyError(
                f"Executed action reference state does not exist: {executed_reference_state}"
            )

        raw: dict[StateId, float] = {}
        if not action_executed:
            if executed_reference_state is not None:
                raise ValueError(
                    "An action reference state cannot be provided when no action was executed"
                )

            raw.update(posterior)
        elif executed_reference_state is None:

            transitions = (
                (0, self.config.incomplete_weight),
                (1, self.config.normal_completion_weight),
                (2, self.config.early_completion_weight),
            )
            for source, probability in posterior.items():
                for steps, weight in transitions:
                    target = self._advance(source, steps, permitted_boundaries)
                    raw[target] = raw.get(target, 0.0) + probability * weight
        else:

            for source, probability in posterior.items():
                raw[source] = raw.get(source, 0.0) + (
                    probability * self.config.incomplete_weight
                )
            for steps, weight in (
                (0, self.config.normal_completion_weight),
                (1, self.config.early_completion_weight),
            ):
                target = self._advance(
                    executed_reference_state,
                    steps,
                    permitted_boundaries,
                )
                raw[target] = raw.get(target, 0.0) + weight

        raw = _normalize(raw)
        nominal = max(raw, key=lambda state: (raw[state], -self._global_index[state]))
        center = self._global_index[nominal]
        lower = max(0, center - self.config.local_backward_radius)
        upper = min(len(self._states), center + self.config.local_forward_radius + 1)
        window = set(self._states[lower:upper])
        restricted = {
            state: probability for state, probability in raw.items() if state in window
        }
        restricted = _normalize(restricted)
        candidates = tuple(sorted(restricted, key=self._global_index.__getitem__))
        return ProgressPrior(restricted, nominal, candidates)


__all__ = [
    "ProgressPrior",
    "ProgressPriorBuilder",
    "ProgressPriorConfig",
]
