"""Separate nominal, inferred, and commanded task progress in LiteTwin."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..model.state_index import StateId
from ..model.task_model import LiteTwinTaskModel


class ExecutionDecision(str, Enum):
    HOLD = "hold"
    REALIGN = "realign"
    ADVANCE = "advance"


@dataclass(frozen=True)
class LiteTwinCursor:
    nominal_state: StateId
    estimated_state: StateId
    reference_state: StateId

    def validate(self, task_model: LiteTwinTaskModel) -> None:
        unknown = {
            self.nominal_state,
            self.estimated_state,
            self.reference_state,
        }.difference(task_model.states)
        if unknown:
            raise KeyError(f"LiteTwin cursor contains unknown states: {sorted(unknown)}")


__all__ = ["LiteTwinCursor", "ExecutionDecision"]
