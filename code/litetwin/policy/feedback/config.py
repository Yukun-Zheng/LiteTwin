"""Compose the inference, execution, boundary, and recovery settings for LiteTwin."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .inference.belief_updater import BeliefUpdaterConfig
from .control.boundary_runtime import BoundaryRuntimeConfig
from .control.execution_controller import LiteTwinExecutionConfig
from .recovery.manager import LiteTwinRecoveryConfig


@dataclass(frozen=True)
class LiteTwinPolicyConfig:

    belief: BeliefUpdaterConfig
    execution: LiteTwinExecutionConfig
    boundary: BoundaryRuntimeConfig
    recovery: LiteTwinRecoveryConfig

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> LiteTwinPolicyConfig:
        known = {"belief", "execution", "boundary", "recovery"}
        unknown = set(value).difference(known)
        if unknown or set(value) != known:
            raise ValueError(
                f"LiteTwin policy configuration sections are incomplete or unknown: {sorted(unknown)}"
            )
        sections = {name: value[name] for name in known}
        if any(not isinstance(section, Mapping) for section in sections.values()):
            raise TypeError("Each LiteTwin policy configuration section must be an object")
        return cls(
            belief=BeliefUpdaterConfig.from_mapping(sections["belief"]),
            execution=LiteTwinExecutionConfig.from_mapping(sections["execution"]),
            boundary=BoundaryRuntimeConfig.from_mapping(sections["boundary"]),
            recovery=LiteTwinRecoveryConfig.from_mapping(sections["recovery"]),
        )

    @classmethod
    def from_files(
        cls,
        *,
        belief: str | Path,
        execution: str | Path,
        boundary: str | Path,
        recovery: str | Path,
    ) -> LiteTwinPolicyConfig:
        return cls(
            belief=BeliefUpdaterConfig.from_json(belief),
            execution=LiteTwinExecutionConfig.from_json(execution),
            boundary=BoundaryRuntimeConfig.from_json(boundary),
            recovery=LiteTwinRecoveryConfig.from_json(recovery),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "belief": self.belief.to_dict(),
            "execution": self.execution.to_dict(),
            "boundary": self.boundary.to_dict(),
            "recovery": self.recovery.to_dict(),
        }


__all__ = ["LiteTwinPolicyConfig"]
