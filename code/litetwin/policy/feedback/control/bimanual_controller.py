"""Shared-snapshot boundary evaluation for one or multiple robot arms."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from ..inference.belief_updater import BeliefUpdater, LiteTwinBelief
from .boundary_runtime import (
    BoundaryRuntimeConfig,
    ConditionKind,
    LocalCompletionResult,
    TransitionPreparation,
    TransitionRequest,
)
from .entry_guard import EntryGuard
from .execution_controller import LiteTwinExecutionController
from ..model.state_index import StateId
from ..model.task_model import LiteTwinTaskModel
from .transition_transaction import (
    TransitionCommitResult,
    TransitionTransactionCoordinator,
)


@dataclass(frozen=True)
class BoundaryCycleResult:
    tick: int
    requests: dict[str, TransitionRequest]
    local_completion: dict[str, LocalCompletionResult]
    transaction: TransitionCommitResult | None
    preparations: dict[str, TransitionPreparation] = field(default_factory=dict)


class MultiArmBoundaryController:

    def __init__(
        self,
        task_models: Mapping[str, LiteTwinTaskModel],
        execution_controllers: Mapping[str, LiteTwinExecutionController],
        config: BoundaryRuntimeConfig,
        *,
        belief_updaters: Mapping[str, BeliefUpdater],
        robot_runtime_variance: float = 0.0,
    ) -> None:
        if not task_models:
            raise ValueError(
                "The multi-arm boundary controller requires at least one arm"
            )
        self.task_models = dict(task_models)
        self.execution_controllers = dict(execution_controllers)
        self.belief_updaters = dict(belief_updaters)
        self.config = config
        self.guards = {
            arm: EntryGuard(
                self.task_models,
                arm,
                config,
                robot_runtime_variance=robot_runtime_variance,
            )
            for arm in self.task_models
        }
        self.transactions = TransitionTransactionCoordinator(
            self.task_models,
            self.execution_controllers,
            self.belief_updaters,
        )
        self._preparations: dict[str, TransitionPreparation] = {}

    def reset(self) -> None:
        for guard in self.guards.values():
            guard.reset()
        self.transactions = TransitionTransactionCoordinator(
            self.task_models,
            self.execution_controllers,
            self.belief_updaters,
        )
        self._preparations.clear()

    @property
    def preparations(self) -> dict[str, TransitionPreparation]:
        return dict(self._preparations)

    def _required_link_relations(
        self,
        requests: Mapping[str, TransitionRequest],
    ) -> frozenset[tuple[str, str]]:

        required: set[tuple[str, str]] = set()
        for request in requests.values():
            boundary = self.task_models[request.arm_id].boundaries[request.boundary_id]
            local = getattr(boundary, "local_completion_model", None)
            relation_conditions = {
                **({} if local is None else local.own_relation_conditions),
                **boundary.relation_conditions,
            }
            for condition_id in request.condition_results:
                if condition_id.kind is not ConditionKind.GUARD_RELATION:
                    continue
                condition = relation_conditions[condition_id.token]
                if condition.required_state != "linked":
                    continue
                relation_arm, frame = condition_id.token.split("/", 1)
                if relation_arm != condition_id.arm_id:
                    raise ValueError(
                        "Boundary relation-condition identifier does not match the relation-key arm"
                    )
                required.add((relation_arm, frame))
        return frozenset(required)

    @staticmethod
    def _preparation_serves_required_link(
        preparation: TransitionPreparation,
        required: frozenset[tuple[str, str]],
    ) -> bool:
        return any(
            (event.arm_id, event.frame_id) in required
            for event in preparation.event_ids
        )

    def _reconcile_preparations(
        self,
        requests: Mapping[str, TransitionRequest],
        source_by_arm: Mapping[str, StateId],
    ) -> None:
        required_links = self._required_link_relations(requests)
        for arm, request in requests.items():
            boundary = self.task_models[arm].boundaries[request.boundary_id]
            source_state = source_by_arm[arm]
            active = self._preparations.get(arm)
            if active is not None and (
                active.boundary_id != request.boundary_id
                or source_state not in boundary.terminal_window
                or not self._preparation_serves_required_link(active, required_links)
            ):
                self._preparations.pop(arm, None)
                active = None
            if (
                active is None
                and request.preparation is not None
                and self._preparation_serves_required_link(
                    request.preparation, required_links
                )
            ):
                self._preparations[arm] = request.preparation

    def evaluate(
        self,
        beliefs: Mapping[str, LiteTwinBelief],
        *,
        arms: frozenset[str] | None = None,
        source_states: Mapping[str, StateId] | None = None,
        mode_by_arm_skill: Mapping[str, Mapping[int, int]] | None = None,
    ) -> BoundaryCycleResult:
        if set(beliefs) != set(self.task_models):
            raise ValueError(
                "The multi-arm boundary controller requires pre-action beliefs for all arms"
            )
        ticks = {belief.tick for belief in beliefs.values()}
        if len(ticks) != 1:
            raise ValueError(
                "Multi-arm boundary evaluation must share one pre-action tick"
            )
        tick = ticks.pop()
        selected_arms = set(self.task_models) if arms is None else set(arms)
        unknown_arms = selected_arms.difference(self.task_models)
        if unknown_arms:
            raise KeyError(
                f"Boundary evaluation contains unknown arms: {sorted(unknown_arms)}"
            )
        requests = {}
        local_results = {}
        source_by_arm: dict[str, StateId] = {}
        for arm, model in self.task_models.items():
            if arm not in selected_arms:
                continue
            source_state = (
                self.execution_controllers[arm].cursor.reference_state
                if source_states is None or arm not in source_states
                else source_states[arm]
            )
            boundary = next(
                (
                    candidate
                    for boundary_id, candidate in sorted(model.boundaries.items())
                    if boundary_id.source_skill == source_state.skill_index
                ),
                None,
            )
            if boundary is None:
                continue
            source_by_arm[arm] = source_state
            request, local = self.guards[arm].evaluate(
                boundary.boundary_id,
                beliefs,
                source_state,
                mode_by_arm_skill=mode_by_arm_skill,
            )
            requests[arm] = request
            local_results[arm] = local

        self._reconcile_preparations(requests, source_by_arm)

        for arm in selected_arms.difference(requests):
            self._preparations.pop(arm, None)

        return BoundaryCycleResult(
            tick=tick,
            requests=requests,
            local_completion=local_results,
            transaction=None,
            preparations=self.preparations,
        )

    def commit_requests(
        self,
        evaluation: BoundaryCycleResult,
        *,
        requests: tuple[TransitionRequest, ...] | None = None,
        externally_committed_arms: frozenset[str] = frozenset(),
    ) -> BoundaryCycleResult:
        selected = tuple(evaluation.requests.values()) if requests is None else requests
        transaction = (
            None
            if not selected
            else self.transactions.commit(
                selected,
                externally_committed_arms=externally_committed_arms,
            )
        )
        if transaction is not None:
            for request in transaction.committed:
                self._preparations.pop(request.arm_id, None)
        return BoundaryCycleResult(
            tick=evaluation.tick,
            requests=evaluation.requests,
            local_completion=evaluation.local_completion,
            transaction=transaction,
            preparations=self.preparations,
        )

    def update(
        self,
        beliefs: Mapping[str, LiteTwinBelief],
        *,
        arms: frozenset[str] | None = None,
        mode_by_arm_skill: Mapping[str, Mapping[int, int]] | None = None,
    ) -> BoundaryCycleResult:
        evaluation = self.evaluate(
            beliefs,
            arms=arms,
            mode_by_arm_skill=mode_by_arm_skill,
        )
        return self.commit_requests(evaluation)


BimanualBoundaryController = MultiArmBoundaryController


__all__ = [
    "BimanualBoundaryController",
    "BoundaryCycleResult",
    "MultiArmBoundaryController",
]
