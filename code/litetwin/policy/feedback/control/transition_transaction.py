"""Batch validation and atomic multi-arm skill-boundary commits."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Mapping, Sequence

from ..model.boundary_model import BoundaryId
from .boundary_runtime import TransitionRequest
from .execution_controller import LiteTwinExecutionController
from ..model.task_model import LiteTwinTaskModel

if TYPE_CHECKING:
    from ..inference.belief_updater import BeliefUpdater


@dataclass(frozen=True)
class TransitionCommitResult:
    tick: int
    requests: tuple[TransitionRequest, ...]
    committed: tuple[TransitionRequest, ...]
    held_transaction_groups: tuple[str, ...]

    @property
    def permitted_boundaries(self) -> dict[str, frozenset[BoundaryId]]:
        result: dict[str, set[BoundaryId]] = {}
        for request in self.committed:
            result.setdefault(request.arm_id, set()).add(request.boundary_id)
        return {arm: frozenset(values) for arm, values in result.items()}


class TransitionTransactionCoordinator:

    def __init__(
        self,
        task_models: Mapping[str, LiteTwinTaskModel],
        controllers: Mapping[str, LiteTwinExecutionController],
        belief_updaters: Mapping[str, BeliefUpdater],
    ) -> None:
        if not set(task_models) == set(controllers) == set(belief_updaters):
            raise ValueError(
                "Transaction coordinator models, executors, and belief updaters must cover the same arms"
            )
        if any(model.arm_id != arm for arm, model in task_models.items()):
            raise ValueError(
                "Transaction coordinator model arm_id values do not match mapping keys"
            )
        self.task_models = dict(task_models)
        self.controllers = dict(controllers)
        self.belief_updaters = dict(belief_updaters)
        groups: dict[str, set[BoundaryId]] = {}
        for model in self.task_models.values():
            for boundary_id, boundary in model.boundaries.items():
                if boundary.transaction_group is not None:
                    groups.setdefault(boundary.transaction_group, set()).add(
                        boundary_id
                    )
        self.transaction_groups = {
            group: frozenset(boundaries) for group, boundaries in groups.items()
        }
        self._last_tick: int | None = None

    def _resolve(
        self,
        requests: Sequence[TransitionRequest],
    ) -> tuple[int, list[TransitionRequest], list[str]]:
        if not requests:
            raise ValueError(
                "A transaction commit requires at least one transition request"
            )
        ticks = {request.tick for request in requests}
        if len(ticks) != 1:
            raise ValueError(
                "All transition requests must originate from the same pre-action tick"
            )
        tick = ticks.pop()
        if len({request.arm_id for request in requests}) != len(requests):
            raise ValueError(
                "An arm cannot generate multiple boundary-crossing requests in one tick"
            )
        unknown_arms = {request.arm_id for request in requests}.difference(
            self.controllers
        )
        if unknown_arms:
            raise KeyError(
                f"Transaction requests contain unknown arms: {sorted(unknown_arms)}"
            )

        by_group: dict[str, list[TransitionRequest]] = {}
        independent = []
        for request in requests:
            if request.transaction_group is None:
                independent.append(request)
            else:
                by_group.setdefault(request.transaction_group, []).append(request)

        candidates = [request for request in independent if request.permitted]
        held_groups = []
        for group, members in sorted(by_group.items()):
            expected = self.transaction_groups.get(group)
            if expected is None:
                raise KeyError(
                    f"Transition request references unknown transaction group {group}"
                )
            received = {request.boundary_id for request in members}
            if received == expected and all(request.permitted for request in members):
                candidates.extend(members)
            else:
                held_groups.append(group)

        return tick, candidates, held_groups

    def commit(
        self,
        requests: Sequence[TransitionRequest],
        *,
        externally_committed_arms: frozenset[str] = frozenset(),
    ) -> TransitionCommitResult:

        tick, candidates, held_groups = self._resolve(requests)
        if self._last_tick is not None and tick <= self._last_tick:
            raise ValueError(
                "The transaction coordinator may commit only once per increasing control tick"
            )
        unknown_external = externally_committed_arms.difference(
            request.arm_id for request in candidates
        )
        if unknown_external:
            raise ValueError(
                "Externally committed arm is not part of the permitted transaction"
            )

        for request in candidates:
            if request.arm_id not in externally_committed_arms:
                self.controllers[request.arm_id].validate_boundary_transition(request)
                self.belief_updaters[request.arm_id].validate_boundary_transition(
                    request.boundary_id,
                    request.target_state,
                )
        for request in candidates:
            if request.arm_id not in externally_committed_arms:
                self.controllers[request.arm_id].commit_boundary_transition(request)
                self.belief_updaters[request.arm_id].commit_boundary_transition(
                    request.boundary_id,
                    request.target_state,
                )
        self._last_tick = tick
        return TransitionCommitResult(
            tick=tick,
            requests=tuple(requests),
            committed=tuple(candidates),
            held_transaction_groups=tuple(held_groups),
        )

    def preview(
        self,
        requests: Sequence[TransitionRequest],
    ) -> TransitionCommitResult:

        tick, candidates, held_groups = self._resolve(requests)
        return TransitionCommitResult(
            tick=tick,
            requests=tuple(requests),
            committed=tuple(candidates),
            held_transaction_groups=tuple(held_groups),
        )


__all__ = ["TransitionCommitResult", "TransitionTransactionCoordinator"]
