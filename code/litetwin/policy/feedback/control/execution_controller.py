"""Unified owner of HOLD, REALIGN, and ADVANCE cursor commits."""

from __future__ import annotations

import math
import json
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from ...dynamac import DynaMACAction, DynaMACObservation, pose_log_nearest
from ..inference.belief_updater import LiteTwinBelief
from .boundary_runtime import TransitionRequest
from .execution_cursor import LiteTwinCursor, ExecutionDecision
from .frame_roles import (
    FrameRole,
    FrameRoleConfig,
    FrameRoleRouter,
    FrameRoleSnapshot,
)
from .mismatch import (
    MismatchConfig,
    MismatchTracker,
    MismatchUpdate,
)
from ..inference.progress_filter import ProgressFilterConfig, ProgressStatus
from ..model.state_index import StateId
from ..model.task_model import LiteTwinTaskModel
from .weighted_poe import WeightedPoEExecutor, WeightedPoEResult


@dataclass(frozen=True)
class LiteTwinExecutionConfig:
    frame_roles: FrameRoleConfig = field(default_factory=FrameRoleConfig)
    mismatch: MismatchConfig = field(default_factory=MismatchConfig)
    minimum_action_equivalence_compatibility: float = 0.8

    def __post_init__(self) -> None:
        if not 0.0 < self.minimum_action_equivalence_compatibility <= 1.0:
            raise ValueError(
                "Control-equivalence compatibility threshold must be in (0, 1]"
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> LiteTwinExecutionConfig:
        known = {
            "frame_roles",
            "mismatch",
            "minimum_action_equivalence_compatibility",
        }
        unknown = set(value).difference(known)
        if unknown:
            raise ValueError(
                f"Execution-control configuration contains unknown sections: {sorted(unknown)}"
            )
        raw_roles = value.get("frame_roles", {})
        raw_mismatch = value.get("mismatch", {})
        if not isinstance(raw_roles, Mapping) or not isinstance(raw_mismatch, Mapping):
            raise TypeError(
                "Each execution-control configuration section must be an object"
            )
        return cls(
            frame_roles=FrameRoleConfig(**dict(raw_roles)),
            mismatch=MismatchConfig(**dict(raw_mismatch)),
            minimum_action_equivalence_compatibility=float(
                value.get("minimum_action_equivalence_compatibility", 0.8)
            ),
        )

    @classmethod
    def from_json(cls, path: str | Path) -> LiteTwinExecutionConfig:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(value, Mapping):
            raise TypeError("Execution-control configuration root must be an object")
        return cls.from_mapping(value)


@dataclass(frozen=True)
class ExecutionCycleResult:
    tick: int
    cursor_before: LiteTwinCursor
    cursor_after: LiteTwinCursor
    decision: ExecutionDecision
    reasons: tuple[str, ...]
    roles: FrameRoleSnapshot
    weighted_action: WeightedPoEResult
    mismatch: MismatchUpdate
    progress_anchor_state: StateId
    control_equivalence: ControlEquivalenceAssessment


@dataclass(frozen=True)
class ControlEquivalenceAssessment:

    evaluated: bool
    accepted: bool
    anchor_state: StateId
    equivalent_states: tuple[StateId, ...]
    aggregated_confidence: float
    normalized_class_entropy: float
    class_count: int
    minimum_action_compatibility: float
    reason: str


class LiteTwinExecutionController:

    def __init__(
        self,
        task_model: LiteTwinTaskModel,
        config: LiteTwinExecutionConfig = LiteTwinExecutionConfig(),
        progress_filter_config: ProgressFilterConfig = ProgressFilterConfig(),
    ) -> None:
        self.task_model = task_model
        self.config = config
        self.progress_filter_config = progress_filter_config
        self.role_router = FrameRoleRouter(task_model, config.frame_roles)
        self.weighted_poe = WeightedPoEExecutor(task_model)
        self.mismatch_tracker = MismatchTracker(config.mismatch)
        self.reset()

    def _route_roles(
        self,
        state_id: StateId,
        belief: LiteTwinBelief,
        *,
        mode_by_skill: Mapping[int, int] | None = None,
        captured_virtual_frames: frozenset[str] = frozenset(),
        commit: bool = True,
    ) -> FrameRoleSnapshot:
        return self.role_router.route(
            state_id,
            belief,
            mode_by_skill=mode_by_skill,
            captured_virtual_frames=captured_virtual_frames,
            commit=commit,
        )

    def reset(self, initial_state: StateId | None = None) -> None:
        if initial_state is None:
            initial_state = min(self.task_model.states)
        if initial_state not in self.task_model.states:
            raise KeyError(
                f"LiteTwin executor initial state does not exist: {initial_state}"
            )
        self._cursor = LiteTwinCursor(
            nominal_state=initial_state,
            estimated_state=initial_state,
            reference_state=initial_state,
        )
        self.role_router.reset()
        self.mismatch_tracker.reset()
        self._last_tick: int | None = None
        self._last_boundary_transition_tick: int | None = None

    def commit_reentry(self, state_id: StateId) -> None:

        if state_id not in self.task_model.states:
            raise KeyError(f"LiteTwin executor re-entry state does not exist: {state_id}")
        self._cursor = LiteTwinCursor(
            nominal_state=state_id,
            estimated_state=state_id,
            reference_state=state_id,
        )
        self.role_router.reenter(state_id)
        self.mismatch_tracker.reset()
        self._last_boundary_transition_tick = None

    @staticmethod
    def _action_compatibility(left: DynaMACAction, right: DynaMACAction) -> float:

        residual = pose_log_nearest(left.pose, right.pose)
        covariance = (
            np.asarray(left.covariance, dtype=np.float64)
            + np.asarray(right.covariance, dtype=np.float64)
            + np.eye(6, dtype=np.float64) * 1.0e-12
        )
        try:
            solved = np.linalg.solve(covariance, residual)
        except np.linalg.LinAlgError:
            solved = np.linalg.pinv(covariance) @ residual
        mahalanobis = max(0.0, float(residual @ solved))
        return float(np.exp(max(-750.0, -0.5 * mahalanobis / 6.0)))

    @staticmethod
    def _roles_equivalent(
        left: FrameRoleSnapshot,
        right: FrameRoleSnapshot,
    ) -> bool:

        if set(left.decisions) != set(right.decisions):
            return False
        if (
            left.recovery_intents != right.recovery_intents
            or left.verification_requests != right.verification_requests
            or left.confirmed_link_events != right.confirmed_link_events
            or left.rejected_link_events != right.rejected_link_events
            or left.unresolved_pending_link_events
            != right.unresolved_pending_link_events
        ):
            return False
        for frame in sorted(left.decisions):
            lhs = left.decisions[frame]
            rhs = right.decisions[frame]
            if (
                lhs.role != rhs.role
                or lhs.selected_offline != rhs.selected_offline
                or lhs.expected_relation != rhs.expected_relation
                or lhs.actual_relation != rhs.actual_relation
                or lhs.monitor != rhs.monitor
                or lhs.blocks_advance != rhs.blocks_advance
                or lhs.recovery_intent != rhs.recovery_intent
                or lhs.link_confirmation_pending != rhs.link_confirmation_pending
                or not np.isclose(
                    lhs.execution_weight,
                    rhs.execution_weight,
                    rtol=1.0e-9,
                    atol=1.0e-12,
                )
            ):
                return False
            if (lhs.expected_distribution is None) != (
                rhs.expected_distribution is None
            ):
                return False
            if lhs.expected_distribution is not None:
                assert rhs.expected_distribution is not None
                if not np.allclose(
                    lhs.expected_distribution,
                    rhs.expected_distribution,
                    rtol=1.0e-9,
                    atol=1.0e-12,
                ):
                    return False
        return True

    def _state_controls(
        self,
        state_id: StateId,
        belief: LiteTwinBelief,
        observation: DynaMACObservation,
        mode_by_skill: Mapping[int, int] | None,
    ) -> tuple[FrameRoleSnapshot, WeightedPoEResult]:
        roles = self._route_roles(
            state_id,
            belief,
            mode_by_skill=mode_by_skill,
            commit=False,
        )
        action = self.weighted_poe.query(
            observation,
            state_id,
            roles,
            mode_index=self._mode_index(state_id, mode_by_skill),
        )
        return roles, action

    def _states_control_equivalent(
        self,
        left: StateId,
        right: StateId,
        controls: Mapping[StateId, tuple[FrameRoleSnapshot, WeightedPoEResult]],
    ) -> tuple[bool, float]:
        if left.skill_index != right.skill_index:
            return False, 0.0
        left_roles, left_weighted = controls[left]
        right_roles, right_weighted = controls[right]
        if not self._roles_equivalent(left_roles, right_roles):
            return False, 0.0
        left_action = left_weighted.action
        right_action = right_weighted.action
        if left_action is None or right_action is None:
            return False, 0.0
        if not np.allclose(
            np.atleast_1d(left_action.gripper),
            np.atleast_1d(right_action.gripper),
            rtol=0.0,
            atol=1.0e-8,
        ):
            return False, 0.0
        compatibility = self._action_compatibility(left_action, right_action)
        return (
            compatibility >= self.config.minimum_action_equivalence_compatibility,
            compatibility,
        )

    def _control_equivalence(
        self,
        belief: LiteTwinBelief,
        observation: DynaMACObservation,
        mode_by_skill: Mapping[int, int] | None,
    ) -> ControlEquivalenceAssessment:

        anchor = belief.progress.estimated_state
        current = self._cursor.reference_state
        adjacent_reference_lag = bool(
            current.skill_index == anchor.skill_index
            and current.local_index == anchor.local_index + 1
        )
        eligible_statuses = {
            ProgressStatus.LOW_CONFIDENCE,
            ProgressStatus.BACKWARD_REALIGNMENT,
        }
        if (
            belief.progress.status not in eligible_statuses
            and not adjacent_reference_lag
        ):
            return ControlEquivalenceAssessment(
                evaluated=False,
                accepted=False,
                anchor_state=anchor,
                equivalent_states=(anchor,),
                aggregated_confidence=belief.progress.confidence,
                normalized_class_entropy=0.0,
                class_count=len(belief.progress.posterior),
                minimum_action_compatibility=1.0,
                reason="progress_status_not_control_equivalence_candidate",
            )

        posterior_states = {
            state
            for state, probability in belief.progress.posterior.items()
            if probability > 0.0
        }
        if adjacent_reference_lag:

            posterior_states.add(current)
        states = tuple(sorted(posterior_states))
        if not states or anchor not in states:
            raise ValueError(
                "Control-equivalence evaluation requires a positive-probability MAP progress state"
            )
        controls = {
            state: self._state_controls(
                state,
                belief,
                observation,
                mode_by_skill,
            )
            for state in states
        }

        classes: list[list[StateId]] = []
        pair_compatibilities: dict[frozenset[StateId], float] = {}
        for state in states:
            if not classes:
                classes.append([state])
                continue
            current_class = classes[-1]
            previous = current_class[-1]
            contiguous = bool(
                state.skill_index == previous.skill_index
                and state.local_index == previous.local_index + 1
            )
            equivalent_to_all = contiguous
            for member in current_class if contiguous else ():
                equivalent, compatibility = self._states_control_equivalent(
                    member,
                    state,
                    controls,
                )
                pair_compatibilities[frozenset((member, state))] = compatibility
                if not equivalent:
                    equivalent_to_all = False
            if equivalent_to_all:
                current_class.append(state)
            else:
                classes.append([state])

        anchor_class = next(group for group in classes if anchor in group)
        class_masses = np.asarray(
            [
                sum(belief.progress.posterior.get(state, 0.0) for state in group)
                for group in classes
            ],
            dtype=np.float64,
        )
        class_masses /= float(np.sum(class_masses))
        class_entropy = -float(
            np.sum(
                class_masses
                * np.log(
                    np.maximum(
                        class_masses,
                        self.progress_filter_config.probability_floor,
                    )
                )
            )
        )
        normalized_entropy = (
            0.0 if len(classes) == 1 else class_entropy / math.log(float(len(classes)))
        )
        confidence = float(
            sum(belief.progress.posterior.get(state, 0.0) for state in anchor_class)
        )
        accepted = bool(
            confidence >= self.progress_filter_config.minimum_confidence
            and normalized_entropy
            <= self.progress_filter_config.maximum_normalized_entropy
        )
        if belief.progress.status != ProgressStatus.LOW_CONFIDENCE:

            accepted = bool(
                accepted and adjacent_reference_lag and current in anchor_class
            )
        relevant_compatibilities = [
            pair_compatibilities[frozenset((left, right))]
            for index, left in enumerate(anchor_class)
            for right in anchor_class[index + 1 :]
        ]
        minimum_compatibility = (
            min(relevant_compatibilities) if relevant_compatibilities else 1.0
        )
        if belief.progress.status == ProgressStatus.LOW_CONFIDENCE:
            accepted_reason = "control_equivalent_progress_uncertainty"
            rejected_reason = "control_relevant_progress_uncertainty"
        else:
            accepted_reason = "control_equivalent_reference_lag"
            rejected_reason = "control_relevant_reference_lag"
        return ControlEquivalenceAssessment(
            evaluated=True,
            accepted=accepted,
            anchor_state=anchor,
            equivalent_states=tuple(anchor_class),
            aggregated_confidence=confidence,
            normalized_class_entropy=normalized_entropy,
            class_count=len(classes),
            minimum_action_compatibility=float(minimum_compatibility),
            reason=accepted_reason if accepted else rejected_reason,
        )

    def _assess_continuous_reference(
        self,
        belief: LiteTwinBelief,
        current: StateId,
        successor: StateId,
        current_roles: FrameRoleSnapshot,
        mode_by_skill: Mapping[int, int] | None,
    ) -> tuple[bool, FrameRoleSnapshot | None]:

        estimated = belief.progress.estimated_state
        if not (
            estimated.skill_index == current.skill_index == successor.skill_index
            and current.local_index + 1 == successor.local_index
            and belief.progress.status
            in {
                ProgressStatus.LOW_CONFIDENCE,
                ProgressStatus.BACKWARD_REALIGNMENT,
                ProgressStatus.NO_PLAUSIBLE_STATE,
            }
        ):
            return False, None
        current_score = belief.candidate_scores.get(current)
        robot_reference_supported = bool(
            current_score is not None
            and current_score.robot_evidence_available
            and current_score.robot_peak_normalized_compatibility
            >= self.progress_filter_config.minimum_explanation_score
        )

        moving_execute_reference = any(
            decision.role == FrameRole.EXECUTE
            and decision.selected_offline
            and frame in belief.runtime_features.relation_information_weight
            and not np.isclose(
                belief.runtime_features.relation_information_weight[frame],
                belief.runtime_features.action_excitation
                * belief.runtime_features.paired_tracking_reliability.get(frame, 0.0),
                rtol=1.0e-12,
                atol=1.0e-12,
            )
            and belief.runtime_features.relation_information_weight[frame]
            < belief.runtime_features.action_excitation
            * belief.runtime_features.paired_tracking_reliability.get(frame, 0.0)
            for frame, decision in current_roles.decisions.items()
        )
        command_reference_supported = bool(
            belief.runtime_features.command_tracking_available
            and belief.runtime_features.command_tracking_compatibility
            >= self.config.minimum_action_equivalence_compatibility
            and moving_execute_reference
        )
        if not (robot_reference_supported or command_reference_supported):
            return False, None

        if robot_reference_supported:
            states = tuple(
                state
                for state in belief.progress.prior
                if state.skill_index == current.skill_index
                and state in belief.candidate_scores
                and belief.candidate_scores[state].robot_evidence_available
            )
            if current not in states or len(states) < 2:
                return False, None
            log_values = np.asarray(
                [
                    math.log(
                        max(
                            belief.progress.prior[state],
                            self.progress_filter_config.probability_floor,
                        )
                    )
                    + belief.candidate_scores[state].robot_log_support
                    for state in states
                ],
                dtype=np.float64,
            )
            probabilities = np.exp(log_values - float(np.max(log_values)))
            probabilities /= float(np.sum(probabilities))
            selected_index = int(np.argmax(probabilities))
            confidence = float(probabilities[selected_index])
            entropy = -float(
                np.sum(
                    probabilities
                    * np.log(
                        np.maximum(
                            probabilities,
                            self.progress_filter_config.probability_floor,
                        )
                    )
                )
            )
            normalized_entropy = entropy / math.log(float(len(states)))
            if not (
                states[selected_index] == current
                and confidence >= self.progress_filter_config.minimum_confidence
                and normalized_entropy
                <= self.progress_filter_config.maximum_normalized_entropy
            ):
                return False, None

        selected_mode = self._mode_index(current, mode_by_skill)
        if selected_mode is None:
            selected_mode = int(np.argmax(self.task_model.state(current).mode_priors))
        grippers = tuple(
            self.task_model.state(state).gripper_commands[selected_mode]
            for state in (current, successor)
        )
        if not np.allclose(grippers[0], grippers[1], rtol=0.0, atol=1.0e-8):
            return False, None

        successor_roles = self._route_roles(
            successor,
            belief,
            mode_by_skill=mode_by_skill,
            commit=False,
        )
        snapshots = (current_roles, successor_roles)
        if any(
            snapshot.blocks_advance or snapshot.recovery_intents
            for snapshot in snapshots
        ):
            return False, None
        signatures = tuple(
            {
                frame: decision.expected_relation
                for frame, decision in snapshot.decisions.items()
            }
            for snapshot in snapshots
        )
        if signatures[0] != signatures[1]:
            return False, None
        if current_roles.verification_requests:
            return False, None
        if successor_roles.verification_requests:
            return False, successor_roles
        return True, None

    @property
    def cursor(self) -> LiteTwinCursor:
        return self._cursor

    def query_frozen_reference(
        self,
        belief: LiteTwinBelief,
        observation: DynaMACObservation,
        *,
        mode_by_skill: Mapping[int, int] | None = None,
    ) -> WeightedPoEResult:

        state_id = self._cursor.reference_state
        roles = self._route_roles(
            state_id,
            belief,
            mode_by_skill=mode_by_skill,
            commit=False,
        )
        return self.weighted_poe.query(
            observation,
            state_id,
            roles,
            mode_index=self._mode_index(state_id, mode_by_skill),
        )

    def query_reentry_alignment(
        self,
        state_id: StateId,
        belief: LiteTwinBelief,
        observation: DynaMACObservation,
        *,
        mode_by_skill: Mapping[int, int] | None = None,
    ) -> WeightedPoEResult:

        if state_id not in self.task_model.states:
            raise KeyError(
                f"Recovery re-entry alignment references unknown state {state_id}"
            )

        conditioned_progress = replace(
            belief.progress,
            prior={state_id: 1.0},
            posterior={state_id: 1.0},
            nominal_state=state_id,
            estimated_state=state_id,
        )
        conditioned_belief = replace(belief, progress=conditioned_progress)
        roles = self._route_roles(
            state_id,
            conditioned_belief,
            mode_by_skill=mode_by_skill,
            commit=False,
        )
        if roles.blocks_advance:
            return WeightedPoEResult(
                state_id=state_id,
                stream_weights=roles.execution_weights,
                participating_frames=(),
                action=None,
            )
        return self.weighted_poe.query(
            observation,
            state_id,
            roles,
            mode_index=self._mode_index(state_id, mode_by_skill),
        )

    def validate_boundary_transition(self, request: TransitionRequest) -> None:

        if request.arm_id != self.task_model.arm_id:
            raise ValueError(
                "Boundary-crossing request does not belong to this execution controller"
            )
        if not request.permitted:
            raise ValueError(
                "A boundary-crossing request cannot be committed before it is permitted"
            )
        if request.boundary_id not in self.task_model.boundaries:
            raise KeyError(f"Task model has no boundary {request.boundary_id.token}")
        if self._last_tick != request.tick:
            raise ValueError(
                "A boundary-crossing request must be committed in the control tick that produced it"
            )
        if self._last_boundary_transition_tick == request.tick:
            raise ValueError(
                "Each arm may commit at most one boundary crossing per tick"
            )
        if self._cursor.reference_state != request.source_state:
            raise ValueError(
                "Boundary-request source state disagrees with the execution cursor"
            )
        boundary = self.task_model.boundaries[request.boundary_id]
        expected_target = self.task_model.skill_states[boundary.target_skill][0]
        if request.target_state != expected_target:
            raise ValueError(
                "Boundary target must be the first state of the next skill"
            )

    def commit_boundary_transition(self, request: TransitionRequest) -> None:

        self.validate_boundary_transition(request)
        self._cursor = LiteTwinCursor(
            nominal_state=self._cursor.nominal_state,
            estimated_state=self._cursor.estimated_state,
            reference_state=request.target_state,
        )
        self._cursor.validate(self.task_model)
        self._last_boundary_transition_tick = request.tick

    def _decision(
        self,
        belief: LiteTwinBelief,
        roles: FrameRoleSnapshot,
        control_equivalence: ControlEquivalenceAssessment,
        current_discrete_action_committed: bool,
        action_executed: bool,
        continuous_reference_supported: bool,
    ) -> tuple[ExecutionDecision, StateId, tuple[str, ...]]:
        current = self._cursor.reference_state
        nominal = belief.progress.nominal_state
        estimated = belief.progress.estimated_state
        reasons = []
        if (
            belief.progress.status == ProgressStatus.NO_PLAUSIBLE_STATE
            and not continuous_reference_supported
        ):
            reasons.append("no_plausible_state")
        elif (
            belief.progress.status == ProgressStatus.LOW_CONFIDENCE
            and not control_equivalence.accepted
            and not continuous_reference_supported
        ):
            reasons.append("low_progress_confidence")
        if roles.blocks_advance:
            if roles.recovery_intents:
                reasons.append("reliable_relation_mismatch")
            else:
                reasons.append("critical_relation_unknown")
        if not current_discrete_action_committed:

            reasons.append("current_discrete_action_pending")
        if estimated.skill_index != current.skill_index:
            reasons.append("skill_boundary_requires_guard")
        if reasons:
            return ExecutionDecision.HOLD, current, tuple(dict.fromkeys(reasons))

        direct_successors = self.task_model.state(current).topology.successors
        same_skill_successors = tuple(
            state
            for state in direct_successors
            if state.skill_index == current.skill_index
        )

        if continuous_reference_supported and same_skill_successors:
            return (
                ExecutionDecision.ADVANCE,
                same_skill_successors[0],
                ("continuous_reference_supported",),
            )

        if (
            estimated.skill_index == current.skill_index
            and estimated.local_index < current.local_index
        ):
            if (
                control_equivalence.accepted
                and estimated in control_equivalence.equivalent_states
                and current in control_equivalence.equivalent_states
            ):
                if same_skill_successors:
                    return (
                        ExecutionDecision.ADVANCE,
                        same_skill_successors[0],
                        ("control_equivalent_current_reference_reached",),
                    )
                return (
                    ExecutionDecision.HOLD,
                    current,
                    ("skill_boundary_requires_guard",),
                )
            return (
                ExecutionDecision.HOLD,
                current,
                ("current_target_incomplete",),
            )

        if estimated == nominal and estimated in same_skill_successors:
            return ExecutionDecision.ADVANCE, estimated, ("early_successor_reached",)

        if estimated == current and (self._last_tick is not None or action_executed):
            if same_skill_successors:
                return (
                    ExecutionDecision.ADVANCE,
                    same_skill_successors[0],
                    ("current_reference_reached",),
                )
            return ExecutionDecision.HOLD, current, ("skill_boundary_requires_guard",)

        if estimated != nominal:
            if estimated == current:
                return (
                    ExecutionDecision.HOLD,
                    current,
                    ("no_confirmed_successor",),
                )
            return (
                ExecutionDecision.REALIGN,
                estimated,
                ("trusted_progress_realignment",),
            )

        if estimated != current:

            return (
                ExecutionDecision.REALIGN,
                estimated,
                ("trusted_progress_realignment",),
            )

        return ExecutionDecision.HOLD, current, ("no_confirmed_successor",)

    @staticmethod
    def _mode_index(
        state_id: StateId,
        mode_by_skill: Mapping[int, int] | None,
    ) -> int | None:
        return (
            None if mode_by_skill is None else mode_by_skill.get(state_id.skill_index)
        )

    def update(
        self,
        belief: LiteTwinBelief,
        observation: DynaMACObservation,
        *,
        mode_by_skill: Mapping[int, int] | None = None,
        current_discrete_action_committed: bool = True,
        action_executed: bool = False,
    ) -> ExecutionCycleResult:
        if self._last_tick is not None and belief.tick <= self._last_tick:
            raise ValueError(
                "The LiteTwin execution controller may commit only once per increasing control tick"
            )
        cursor_before = self._cursor
        if belief.progress.nominal_state not in self.task_model.states:
            raise KeyError("nominal_state is not in the task model")
        if belief.progress.estimated_state not in self.task_model.states:
            raise KeyError("estimated_state is not in the task model")

        roles = self._route_roles(
            cursor_before.reference_state,
            belief,
            mode_by_skill=mode_by_skill,
            commit=False,
        )
        mismatch_roles = roles
        control_equivalence = self._control_equivalence(
            belief,
            observation,
            mode_by_skill,
        )
        same_skill_successors = tuple(
            state
            for state in self.task_model.state(
                cursor_before.reference_state
            ).topology.successors
            if state.skill_index == cursor_before.reference_state.skill_index
        )
        continuous_reference_supported = False
        verification_handoff = None
        if len(same_skill_successors) == 1:
            (
                continuous_reference_supported,
                verification_handoff,
            ) = self._assess_continuous_reference(
                belief,
                cursor_before.reference_state,
                same_skill_successors[0],
                roles,
                mode_by_skill,
            )
        decision, proposed_reference, reasons = self._decision(
            belief,
            roles,
            control_equivalence,
            current_discrete_action_committed,
            action_executed,
            continuous_reference_supported,
        )
        if verification_handoff is not None:
            requests = {
                request.event_id.token: request
                for request in (
                    *roles.verification_requests,
                    *verification_handoff.verification_requests,
                )
            }
            roles = replace(
                roles,
                verification_requests=tuple(requests[key] for key in sorted(requests)),
            )
            mismatch_roles = roles
            reasons = tuple(
                dict.fromkeys((*reasons, "successor_relation_verification_required"))
            )
        if control_equivalence.accepted:
            reasons = tuple(dict.fromkeys((control_equivalence.reason, *reasons)))
        if proposed_reference != cursor_before.reference_state:
            proposed_roles = self._route_roles(
                proposed_reference,
                belief,
                mode_by_skill=mode_by_skill,
                commit=False,
            )
            if proposed_roles.blocks_advance:
                decision = ExecutionDecision.HOLD
                proposed_reference = cursor_before.reference_state

                mismatch_roles = proposed_roles
                reasons = tuple(
                    dict.fromkeys((*reasons, "proposed_reference_blocked_by_role"))
                )
            else:
                roles = proposed_roles
                mismatch_roles = roles

        weighted_action = self.weighted_poe.query(
            observation,
            proposed_reference,
            roles,
            mode_index=self._mode_index(proposed_reference, mode_by_skill),
        )
        if (
            not weighted_action.available
            and proposed_reference != cursor_before.reference_state
        ):
            decision = ExecutionDecision.HOLD
            proposed_reference = cursor_before.reference_state
            reasons = tuple(dict.fromkeys((*reasons, "no_positive_execution_stream")))
            roles = self._route_roles(
                proposed_reference,
                belief,
                mode_by_skill=mode_by_skill,
                commit=False,
            )
            weighted_action = self.weighted_poe.query(
                observation,
                proposed_reference,
                roles,
                mode_index=self._mode_index(proposed_reference, mode_by_skill),
            )
        elif not weighted_action.available:
            decision = ExecutionDecision.HOLD
            reasons = tuple(dict.fromkeys((*reasons, "no_positive_execution_stream")))

        cursor_after = LiteTwinCursor(
            nominal_state=belief.progress.nominal_state,
            estimated_state=belief.progress.estimated_state,
            reference_state=proposed_reference,
        )
        cursor_after.validate(self.task_model)
        mismatch = self.mismatch_tracker.update(
            belief,
            cursor_after,
            decision,
            mismatch_roles,
            action_executed=action_executed,
        )

        self.role_router.commit(roles, belief)
        self._cursor = cursor_after
        self._last_tick = belief.tick
        return ExecutionCycleResult(
            tick=belief.tick,
            cursor_before=cursor_before,
            cursor_after=cursor_after,
            decision=decision,
            reasons=reasons,
            roles=roles,
            weighted_action=weighted_action,
            mismatch=mismatch,
            progress_anchor_state=weighted_action.state_id,
            control_equivalence=control_equivalence,
        )

    def query_after_boundary_transition(
        self,
        previous: ExecutionCycleResult,
        belief: LiteTwinBelief,
        observation: DynaMACObservation,
        *,
        mode_by_skill: Mapping[int, int] | None = None,
    ) -> ExecutionCycleResult:

        if previous.tick != belief.tick or self._last_tick != belief.tick:
            raise ValueError(
                "Post-boundary action re-query must use the current tick's belief and execution result"
            )
        if self._last_boundary_transition_tick != belief.tick:
            raise RuntimeError(
                "Only a boundary transaction committed in this tick may re-query the entry action"
            )
        target = self._cursor.reference_state
        source_skill = previous.cursor_after.reference_state.skill_index
        if target.skill_index == source_skill:
            raise RuntimeError(
                "Post-boundary action re-query requires reference_state to cross the skill boundary"
            )
        source_terminal = self.task_model.skill_states[source_skill][-1]
        source_roles = self._route_roles(
            source_terminal,
            belief,
            mode_by_skill=mode_by_skill,
            captured_virtual_frames=frozenset(
                frame
                for frame in observation.frames
                if frame.startswith("virtual_skill_")
            ),
            commit=False,
        )
        source_weighted = self.weighted_poe.query(
            observation,
            source_terminal,
            source_roles,
            mode_index=self._mode_index(source_terminal, mode_by_skill),
        )
        if source_weighted.action is None:
            weighted_action = source_weighted
        else:
            entry_mode = self._mode_index(target, mode_by_skill)
            if entry_mode is None:
                entry_mode = int(
                    self.task_model.base_policy.selected_mode_path[target.skill_index]
                )
            entry_node = self.task_model.state(target)
            if entry_mode < 0 or entry_mode >= len(entry_node.gripper_commands):
                raise IndexError(
                    "Boundary entry mode is outside the StateNode gripper-command range"
                )
            bridge_action = DynaMACAction(
                pose=source_weighted.action.pose.copy(),
                covariance=source_weighted.action.covariance.copy(),
                gripper=entry_node.gripper_commands[entry_mode].copy(),
                diagnostics={
                    **source_weighted.action.diagnostics,
                    "boundary_transition": {
                        "source_terminal_state": (
                            f"k{source_terminal.skill_index}:t{source_terminal.local_index}"
                        ),
                        "committed_entry_state": (
                            f"k{target.skill_index}:t{target.local_index}"
                        ),
                        "continuous_target_source": "source_terminal_state",
                        "gripper_target_source": "committed_entry_state",
                        "virtual_frame_capture": "next_post_action_observation",
                    },
                },
            )
            weighted_action = WeightedPoEResult(
                state_id=source_terminal,
                stream_weights=source_weighted.stream_weights,
                participating_frames=source_weighted.participating_frames,
                action=bridge_action,
            )

        self.role_router.commit_boundary_entry(
            source_roles,
            belief,
            target,
            mode_by_skill=mode_by_skill,
            captured_virtual_frames=frozenset(
                frame
                for frame in observation.frames
                if frame.startswith("virtual_skill_")
            ),
        )
        reasons: tuple[str, ...] = (
            "entry_guard_transaction_committed",
            "dynamac_boundary_terminal_bridge",
        )
        if not source_weighted.available:
            reasons = (*reasons, "no_positive_execution_stream")
        return ExecutionCycleResult(
            tick=belief.tick,
            cursor_before=previous.cursor_before,
            cursor_after=self._cursor,
            decision=ExecutionDecision.ADVANCE,
            reasons=reasons,
            roles=source_roles,
            weighted_action=weighted_action,
            mismatch=previous.mismatch,
            progress_anchor_state=target,
            control_equivalence=previous.control_equivalence,
        )


__all__ = [
    "LiteTwinExecutionConfig",
    "LiteTwinExecutionController",
    "ControlEquivalenceAssessment",
    "ExecutionCycleResult",
]
