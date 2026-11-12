"""Dynamic roles for selected experts and event-confirmed relations."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Mapping

import numpy as np

from ..inference.belief_updater import LiteTwinBelief
from ..model.relation_events import RelationEventId, RelationStateKey
from ..inference.relation_filter import RelationDecision, RelationEstimate
from ..model.state_index import StateId
from ..model.task_model import LiteTwinTaskModel

Array = np.ndarray


class FrameRole(str, Enum):
    EXECUTE = "execute"
    MONITOR = "monitor"
    RECOVER = "recover"
    DEFER = "defer"


@dataclass(frozen=True)
class RelationRecoveryIntent:
    arm_id: str
    frame_id: str
    expected_relation: RelationDecision
    actual_relation: RelationDecision
    origin_event_id: RelationEventId | None = None

    def __post_init__(self) -> None:
        if not self.arm_id or not self.frame_id:
            raise ValueError(
                "Relation-recovery intent requires a nonempty arm and reference frame"
            )
        if RelationDecision.UNKNOWN in {
            self.expected_relation,
            self.actual_relation,
        }:
            raise ValueError(
                "A reliable relation-recovery intent cannot use Unknown as an endpoint"
            )
        if self.expected_relation == self.actual_relation:
            raise ValueError("Relation-recovery intent must contain an actual mismatch")
        if self.origin_event_id is not None and (
            self.expected_relation != RelationDecision.LINKED
            or self.actual_relation != RelationDecision.EXTERNAL
            or self.origin_event_id.arm_id != self.arm_id
            or self.origin_event_id.frame_id != self.frame_id
            or self.origin_event_id.transition not in {"link", "link_pending"}
        ):
            raise ValueError(
                "Relation-recovery intent event source conflicts with the LINK mismatch"
            )


@dataclass(frozen=True)
class RelationVerificationRequest:
    arm_id: str
    frame_id: str
    relation: str
    event_id: RelationEventId
    context_state: StateId
    committed_occurrence: bool = False

    def __post_init__(self) -> None:
        if not self.arm_id or not self.frame_id:
            raise ValueError(
                "Active relation-verification request requires a nonempty arm and reference frame"
            )
        if self.relation != "linked":
            raise ValueError(
                "The stream-role router generates active verification requests only for linked relations"
            )
        if (
            self.event_id.transition not in {"link", "link_pending"}
            or self.event_id.arm_id != self.arm_id
            or self.event_id.frame_id != self.frame_id
        ):
            raise ValueError(
                "Active relation-verification request conflicts with the LINK event"
            )


@dataclass(frozen=True)
class FrameRoleDecision:
    frame_id: str
    role: FrameRole
    selected_offline: bool
    expected_distribution: Array | None
    expected_relation: RelationDecision | None
    actual_relation: RelationDecision | None
    relation_compatibility: float
    execution_weight: float
    monitor: bool
    blocks_advance: bool
    recovery_intent: RelationRecoveryIntent | None = None
    link_confirmation_pending: bool = False

    def __post_init__(self) -> None:
        if not self.frame_id:
            raise ValueError("Stream roles require a nonempty reference frame")
        if self.expected_distribution is not None:
            distribution = np.asarray(self.expected_distribution, dtype=np.float64)
            if (
                distribution.shape != (2,)
                or np.any(distribution < 0.0)
                or not np.isclose(np.sum(distribution), 1.0)
            ):
                raise ValueError(
                    "Expected relation must be a normalized external/linked vector"
                )
            object.__setattr__(self, "expected_distribution", distribution.copy())
        if not 0.0 <= self.relation_compatibility <= 1.0:
            raise ValueError("Relation compatibility must be in [0, 1]")
        if not np.isfinite(self.execution_weight) or self.execution_weight < 0.0:
            raise ValueError(
                "Execution precision weight must be finite and nonnegative"
            )
        if self.role == FrameRole.MONITOR and not self.monitor:
            raise ValueError("MONITOR role must enable the relation-monitoring flag")
        if self.role in {FrameRole.MONITOR, FrameRole.RECOVER} and (
            self.execution_weight != 0.0
        ):
            raise ValueError(
                "MONITOR and RECOVER roles cannot participate in nominal PoE"
            )
        if self.link_confirmation_pending and (
            self.role != FrameRole.DEFER
            or self.execution_weight != 0.0
            or self.blocks_advance
            or self.recovery_intent is not None
        ):
            raise ValueError(
                "A LINK confirmation window must be a zero-weight, nonblocking DEFER role"
            )


@dataclass(frozen=True)
class FrameRoleSnapshot:
    state_id: StateId
    decisions: dict[str, FrameRoleDecision]
    recovery_intents: tuple[RelationRecoveryIntent, ...] = ()
    verification_requests: tuple[RelationVerificationRequest, ...] = ()
    confirmed_link_events: tuple[RelationEventId, ...] = ()
    rejected_link_events: tuple[RelationEventId, ...] = ()
    unresolved_link_events: tuple[RelationEventId, ...] = ()
    unresolved_pending_link_events: tuple[RelationEventId, ...] = ()

    @property
    def execution_weights(self) -> dict[str, float]:
        return {
            frame: decision.execution_weight
            for frame, decision in self.decisions.items()
            if decision.selected_offline
        }

    @property
    def blocks_advance(self) -> bool:
        return any(decision.blocks_advance for decision in self.decisions.values())


@dataclass(frozen=True)
class FrameRoleConfig:
    expected_relation_probability: float = 0.70
    minimum_tracking_reliability: float = 0.25
    minimum_information_weight: float = 0.10

    def __post_init__(self) -> None:
        if not 0.5 < self.expected_relation_probability <= 1.0:
            raise ValueError("Expected-relation decision threshold must be in (0.5, 1]")
        for value in (
            self.minimum_tracking_reliability,
            self.minimum_information_weight,
        ):
            if not 0.0 <= value <= 1.0:
                raise ValueError("Stream-role reliability thresholds must be in [0, 1]")


class FrameRoleRouter:

    def __init__(
        self,
        task_model: LiteTwinTaskModel,
        config: FrameRoleConfig = FrameRoleConfig(),
    ) -> None:
        self.task_model = task_model
        self.config = config
        self._states = tuple(sorted(task_model.states))
        self._global_index = {state: index for index, state in enumerate(self._states)}

        self._indirect_link_events = self._derive_indirect_link_events()

        self._last_trusted_weights: dict[str, float] = {}

        self._confirmed_link_events: set[RelationEventId] = set()

        self._rejected_link_events: set[RelationEventId] = set()

        self._unresolved_link_events: set[RelationEventId] = set()

        self._unresolved_pending_link_events: set[RelationEventId] = set()
        self._last_committed_state: StateId | None = None

    def _link_events_overlap(
        self,
        left: RelationEventId,
        right: RelationEventId,
    ) -> bool:

        if (
            left == right
            or left.transition != "link"
            or right.transition != "link"
            or left.skill_index != right.skill_index
            or left.mode != right.mode
        ):
            return False
        lhs = self.task_model.link_anchors[left]
        rhs = self.task_model.link_anchors[right]
        tolerance = int(
            self.task_model.builder_config.get("relation_event_alignment_tolerance", 0)
        )
        timing_close = bool(
            len(lhs.event_local_indices) == len(rhs.event_local_indices)
            and all(
                abs(int(a) - int(b)) <= tolerance
                for a, b in zip(
                    lhs.event_local_indices,
                    rhs.event_local_indices,
                    strict=True,
                )
            )
        )
        return timing_close and not set(lhs.linked_entry_states).isdisjoint(
            rhs.linked_entry_states
        )

    def _derive_indirect_link_events(self) -> frozenset[RelationEventId]:

        remaining = set(self.task_model.link_anchors)
        groups: list[set[RelationEventId]] = []
        while remaining:
            seed = remaining.pop()
            component = {seed}
            frontier = [seed]
            while frontier:
                current = frontier.pop()
                neighbors = {
                    candidate
                    for candidate in tuple(remaining)
                    if self._link_events_overlap(current, candidate)
                }
                remaining.difference_update(neighbors)
                component.update(neighbors)
                frontier.extend(neighbors)
            if len(component) > 1:
                groups.append(component)

        indirect: set[RelationEventId] = set()
        for group in groups:
            earliest = min(
                min(self.task_model.link_anchors[event].linked_entry_states)
                for event in group
            )
            pre_event_states = tuple(
                state for state in self._states if state < earliest
            )
            actionable = set()
            for event in group:
                for state in pre_event_states:
                    node = self.task_model.state(state)

                    if event.frame_id in node.action_relevant_frames:
                        actionable.add(event)
                        break
            if len(actionable) == 1:
                indirect.update(group.difference(actionable))
        return frozenset(indirect)

    def reset(self) -> None:
        self._last_trusted_weights.clear()
        self._confirmed_link_events.clear()
        self._rejected_link_events.clear()
        self._unresolved_link_events.clear()
        self._unresolved_pending_link_events.clear()
        self._last_committed_state = None

    def active_direct_link_event(
        self,
        frame_id: str,
        state_id: StateId,
        *,
        mode_by_skill: Mapping[int, int],
    ) -> RelationEventId | None:

        if state_id not in self.task_model.states:
            raise KeyError(f"Stream-role query references unknown state {state_id}")
        candidates: list[tuple[StateId, RelationEventId]] = []
        for event, anchor in self.task_model.link_anchors.items():
            if event.frame_id != frame_id:
                continue
            if mode_by_skill.get(event.skill_index) != event.mode:
                continue
            first_linked = min(anchor.linked_entry_states)
            if first_linked > state_id:
                continue
            invalidated = any(
                unlink.frame_id == frame_id
                and mode_by_skill.get(unlink.skill_index) == unlink.mode
                and first_linked < metadata.release_state <= state_id
                for unlink, metadata in self.task_model.unlink_events.items()
            )
            if not invalidated:
                candidates.append((first_linked, event))
        if not candidates:
            return None
        latest = max(state for state, _event in candidates)
        active = [event for state, event in candidates if state == latest]
        if len(active) != 1 or active[0] in self._indirect_link_events:
            return None
        return active[0]

    def selected_mode_unlink_event_in_skill(
        self,
        frame_id: str,
        skill_index: int,
        *,
        mode_by_skill: Mapping[int, int],
    ) -> RelationEventId | None:

        selected_mode = mode_by_skill.get(skill_index)
        if selected_mode is None:
            return None
        candidates = sorted(
            event
            for event in self.task_model.unlink_events
            if event.frame_id == frame_id
            and event.skill_index == skill_index
            and event.mode == selected_mode
        )
        return None if not candidates else candidates[0]

    def reenter(self, state_id: StateId) -> None:

        if state_id not in self.task_model.states:
            raise KeyError(f"Stream-role re-entry references unknown state {state_id}")
        self._last_committed_state = state_id

    def commit(
        self,
        snapshot: FrameRoleSnapshot,
        belief: LiteTwinBelief | None = None,
        *,
        causal_state: StateId | None = None,
    ) -> None:

        self._confirmed_link_events.update(snapshot.confirmed_link_events)
        self._rejected_link_events.update(snapshot.rejected_link_events)
        self._unresolved_link_events = set(snapshot.unresolved_link_events)
        self._unresolved_pending_link_events = set(
            snapshot.unresolved_pending_link_events
        )
        if causal_state is not None and causal_state not in self.task_model.states:
            raise KeyError(
                f"Stream-role commit references unknown causal state {causal_state}"
            )
        self._last_committed_state = causal_state or snapshot.state_id

        if belief is not None:
            features = belief.runtime_features
            for frame, estimate in belief.relation_estimates.items():
                if frame not in self.task_model.relation_frames:
                    continue
                reliability = features.tracking_reliability.get(frame, 0.0)
                visible = features.frame_visibility.get(frame, False)
                if (
                    not visible
                    or reliability < self.config.minimum_tracking_reliability
                ):
                    continue
                if estimate.decision_state == RelationDecision.EXTERNAL:

                    self._last_trusted_weights[frame] = reliability
                elif estimate.decision_state == RelationDecision.LINKED:
                    self._last_trusted_weights[frame] = 0.0
        for frame, decision in snapshot.decisions.items():
            if decision.role != FrameRole.DEFER:
                self._last_trusted_weights[frame] = decision.execution_weight

    def commit_boundary_entry(
        self,
        source_snapshot: FrameRoleSnapshot,
        belief: LiteTwinBelief,
        entry_state: StateId,
        *,
        mode_by_skill: Mapping[int, int] | None = None,
        captured_virtual_frames: frozenset[str] = frozenset(),
    ) -> None:

        entry_snapshot = self.route(
            entry_state,
            belief,
            mode_by_skill=mode_by_skill,
            captured_virtual_frames=captured_virtual_frames,
            commit=False,
        )
        confirmed = set(source_snapshot.confirmed_link_events)
        confirmed.update(entry_snapshot.confirmed_link_events)
        rejected = set(source_snapshot.rejected_link_events)
        rejected.update(entry_snapshot.rejected_link_events)
        unresolved = set(source_snapshot.unresolved_link_events)
        unresolved.update(entry_snapshot.unresolved_link_events)
        unresolved.difference_update(confirmed)
        unresolved.difference_update(rejected)
        lifecycle_snapshot = replace(
            source_snapshot,
            confirmed_link_events=tuple(sorted(confirmed)),
            rejected_link_events=tuple(sorted(rejected)),
            unresolved_link_events=tuple(sorted(unresolved)),
            unresolved_pending_link_events=tuple(
                sorted(self._unresolved_pending_link_events)
            ),
        )
        self.commit(
            lifecycle_snapshot,
            belief,
            causal_state=entry_state,
        )
        entry_mode = self._mode_for_state(entry_state, mode_by_skill)
        source_mode = self._mode_for_state(source_snapshot.state_id, mode_by_skill)
        if entry_mode is None:
            entry_mode = int(
                self.task_model.base_policy.selected_mode_path[entry_state.skill_index]
            )
        if source_mode is None:
            source_mode = int(
                self.task_model.base_policy.selected_mode_path[
                    source_snapshot.state_id.skill_index
                ]
            )
        source_commands = self.task_model.state(
            source_snapshot.state_id
        ).gripper_commands
        entry_commands = self.task_model.state(entry_state).gripper_commands
        if 0 <= source_mode < len(source_commands) and 0 <= entry_mode < len(
            entry_commands
        ):
            source_gripper = np.asarray(source_commands[source_mode], dtype=np.float64)
            entry_gripper = np.asarray(entry_commands[entry_mode], dtype=np.float64)
            closes_relation = bool(
                source_gripper.shape == entry_gripper.shape
                and np.all(source_gripper > 0.5)
                and np.all(entry_gripper <= 0.5)
            )
            if closes_relation:
                self._unresolved_pending_link_events.update(
                    event_id
                    for event_id, candidate in self.task_model.link_pending_events.items()
                    if event_id.mode == entry_mode
                    and candidate.candidate_state == entry_state
                )

    @staticmethod
    def _mode_for_state(
        state_id: StateId,
        mode_by_skill: Mapping[int, int] | None,
    ) -> int | None:
        return (
            None if mode_by_skill is None else mode_by_skill.get(state_id.skill_index)
        )

    def _candidate_frames(
        self,
        state_id: StateId,
        mode_by_skill: Mapping[int, int] | None,
    ) -> tuple[str, ...]:
        node = self.task_model.state(state_id)
        mode = self._mode_for_state(state_id, mode_by_skill)
        if mode is None:
            return tuple(
                dict.fromkeys((*node.selected_frames, *node.action_relevant_frames))
            )
        if mode < 0 or mode >= len(node.mode_selected_frames):
            raise IndexError("mode_index is outside the stream-role model's mode range")
        return tuple(
            dict.fromkeys(
                (
                    *node.mode_selected_frames[mode],
                    *node.mode_action_relevant_frames[mode],
                )
            )
        )

    def _action_frames(
        self,
        state_id: StateId,
        mode_by_skill: Mapping[int, int] | None,
    ) -> tuple[str, ...]:
        node = self.task_model.state(state_id)
        mode = self._mode_for_state(state_id, mode_by_skill)
        candidates = set(self._candidate_frames(state_id, mode_by_skill))
        if mode is None:
            return tuple(
                frame for frame in node.action_relevant_frames if frame in candidates
            )
        if mode < 0 or mode >= len(node.mode_action_relevant_frames):
            raise IndexError(
                "mode_index is outside the action-relevant stream model's mode range"
            )
        return tuple(
            frame
            for frame in node.mode_action_relevant_frames[mode]
            if frame in candidates
        )

    def _expected_distribution(
        self,
        frame: str,
        posterior: Mapping[StateId, float],
        mode_by_skill: Mapping[int, int] | None,
    ) -> Array:
        result = np.zeros(2, dtype=np.float64)
        total = 0.0
        for state_id, probability in posterior.items():
            if probability <= 0.0:
                continue
            node = self.task_model.state(state_id)
            priors = node.demo_relation_priors.get(frame)
            if priors is None:
                prior = np.asarray([0.5, 0.5], dtype=np.float64)
            else:
                mode = self._mode_for_state(state_id, mode_by_skill)
                if mode is None:
                    prior = np.sum(node.mode_priors[:, None] * priors, axis=0)
                else:
                    if mode < 0 or mode >= len(priors):
                        raise IndexError(
                            "mode_index is outside the expected-relation model's mode range"
                        )
                    prior = priors[mode]
            result += float(probability) * prior
            total += float(probability)
        if total <= 0.0:
            raise ValueError("Progress posterior must have positive probability mass")
        result /= total
        result /= np.sum(result)
        return result

    def _expected_decision(self, distribution: Array) -> RelationDecision:

        if float(np.max(distribution)) < self.config.expected_relation_probability:
            return RelationDecision.UNKNOWN
        return (
            RelationDecision.LINKED
            if distribution[1] > distribution[0]
            else RelationDecision.EXTERNAL
        )

    def _confirmed_monitor_frames(
        self,
        posterior: Mapping[StateId, float],
        mode_by_skill: Mapping[int, int] | None,
    ) -> tuple[str, ...]:

        frames = set()
        for state_id, probability in posterior.items():
            if probability <= 0.0:
                continue
            node = self.task_model.state(state_id)
            mode = self._mode_for_state(state_id, mode_by_skill)
            modes = range(len(node.mode_priors)) if mode is None else (mode,)
            for candidate_mode in modes:
                for frame in self.task_model.relation_frames:
                    key = RelationStateKey(
                        self.task_model.arm_id,
                        frame,
                        state_id,
                        candidate_mode,
                    )
                    if key in self.task_model.link_origins:
                        frames.add(frame)
        return tuple(sorted(frames))

    def _link_origins_for_state(
        self,
        frame: str,
        state_id: StateId,
        mode_by_skill: Mapping[int, int] | None,
    ) -> tuple[RelationEventId, ...]:
        node = self.task_model.state(state_id)
        mode = self._mode_for_state(state_id, mode_by_skill)
        modes = range(len(node.mode_priors)) if mode is None else (mode,)
        return tuple(
            sorted(
                {
                    event_id
                    for candidate_mode in modes
                    for event_id in (
                        self.task_model.link_origins.get(
                            RelationStateKey(
                                self.task_model.arm_id,
                                frame,
                                state_id,
                                candidate_mode,
                            )
                        ),
                    )
                    if event_id is not None
                }
            )
        )

    def _link_confirmation_events(
        self,
        frame: str,
        state_id: StateId,
        mode_by_skill: Mapping[int, int] | None,
    ) -> tuple[RelationEventId, ...]:

        events = []
        for event_id, anchor in self.task_model.link_anchors.items():
            if event_id.frame_id != frame or state_id not in anchor.linked_entry_states:
                continue
            if mode_by_skill is not None:
                selected_mode = mode_by_skill.get(event_id.skill_index)
                if selected_mode is not None and selected_mode != event_id.mode:
                    continue
            if event_id in self._confirmed_link_events:
                continue
            if event_id in self._rejected_link_events:
                continue
            events.append(event_id)
        return tuple(events)

    def _topology_reachable(self, source: StateId, target: StateId) -> bool:

        if source == target:
            return True
        frontier = [source]
        visited = {source}
        target_index = self._global_index[target]
        while frontier:
            current = frontier.pop()
            for successor in self.task_model.state(current).topology.successors:
                if successor in visited:
                    continue

                if self._global_index[successor] > target_index:
                    continue
                if successor == target:
                    return True
                visited.add(successor)
                frontier.append(successor)
        return False

    def _causally_enters_link_interval(
        self,
        event_id: RelationEventId,
        state_id: StateId,
    ) -> bool:

        previous = self._last_committed_state
        if previous is None:
            return False
        anchor = self.task_model.link_anchors[event_id]
        if not anchor.linked_entry_states or state_id not in anchor.linked_entry_states:
            return False
        first = anchor.linked_entry_states[0]
        if self._global_index[previous] >= self._global_index[first]:
            return False
        return self._topology_reachable(previous, first) and self._topology_reachable(
            first, state_id
        )

    def _link_confirmation_pending(
        self,
        frame: str,
        state_id: StateId,
        estimate: RelationEstimate | None,
        belief: LiteTwinBelief,
        mode_by_skill: Mapping[int, int] | None,
    ) -> tuple[RelationEventId, ...]:

        if estimate is None:
            return ()
        events = set(
            self._link_confirmation_events(
                frame,
                state_id,
                mode_by_skill,
            )
        )

        if (
            estimate.informative
            and estimate.informative_evidence_direction == RelationDecision.LINKED
        ):
            for event_id in self._unresolved_link_events:
                if event_id.frame_id != frame:
                    continue
                anchor = self.task_model.link_anchors.get(event_id)
                if anchor is None or not anchor.linked_entry_states:
                    continue
                terminal = anchor.linked_entry_states[-1]
                if self._last_committed_state != terminal:
                    continue
                if state_id not in self.task_model.state(terminal).topology.successors:
                    continue
                if mode_by_skill is not None:
                    selected_mode = mode_by_skill.get(event_id.skill_index)
                    if selected_mode is not None and selected_mode != event_id.mode:
                        continue
                if event_id not in self._confirmed_link_events and event_id not in (
                    self._rejected_link_events
                ):
                    events.add(event_id)
        events = tuple(sorted(events))
        if not events:
            return ()
        causally_entered = set(self._unresolved_link_events)
        causally_entered.update(
            event_id
            for event_id in events
            if self._causally_enters_link_interval(event_id, state_id)
        )
        events = tuple(event for event in events if event in causally_entered)

        events = tuple(
            event
            for event in events
            if not (
                self._last_committed_state == state_id
                and belief.progress.estimated_state == state_id
                and self.task_model.link_anchors[event].linked_entry_states[-1]
                == state_id
                and not (
                    estimate.informative
                    and estimate.informative_evidence_direction
                    == RelationDecision.LINKED
                )
            )
        )
        if not events:
            return ()
        features = belief.runtime_features
        if (
            not features.frame_pair_available.get(frame, False)
            or features.paired_tracking_reliability.get(frame, 0.0)
            < self.config.minimum_tracking_reliability
        ):
            return ()
        if estimate.decision_state == RelationDecision.LINKED:
            return ()
        return events

    def _planned_unlink_is_direct_successor(
        self,
        frame: str,
        state_id: StateId,
        mode_by_skill: Mapping[int, int] | None,
    ) -> bool:

        successors = set(self.task_model.state(state_id).topology.successors)
        mode = self._mode_for_state(state_id, mode_by_skill)
        modes = (
            range(len(self.task_model.state(state_id).mode_priors))
            if mode is None
            else (mode,)
        )
        for candidate_mode in modes:
            if not any(
                event_id.arm_id == self.task_model.arm_id
                and event_id.frame_id == frame
                and event_id.mode == candidate_mode
                and metadata.release_state in successors
                for event_id, metadata in self.task_model.unlink_events.items()
            ):
                return False
        return True

    def _is_unlink_confirmation_state(
        self,
        frame: str,
        state_id: StateId,
        mode_by_skill: Mapping[int, int] | None,
    ) -> bool:

        mode = self._mode_for_state(state_id, mode_by_skill)
        modes = (
            range(len(self.task_model.state(state_id).mode_priors))
            if mode is None
            else (mode,)
        )
        for candidate_mode in modes:
            matching = tuple(
                metadata
                for event_id, metadata in self.task_model.unlink_events.items()
                if event_id.arm_id == self.task_model.arm_id
                and event_id.frame_id == frame
                and event_id.mode == candidate_mode
            )
            if not any(
                metadata.release_state == state_id
                or state_id in metadata.legal_reentry_states
                for metadata in matching
            ):
                return False
        return True

    def _planned_link_is_direct_successor(
        self,
        frame: str,
        state_id: StateId,
        mode_by_skill: Mapping[int, int] | None,
    ) -> bool:

        successors = set(self.task_model.state(state_id).topology.successors)
        mode = self._mode_for_state(state_id, mode_by_skill)
        modes = (
            range(len(self.task_model.state(state_id).mode_priors))
            if mode is None
            else (mode,)
        )
        for candidate_mode in modes:
            if not any(
                event_id.arm_id == self.task_model.arm_id
                and event_id.frame_id == frame
                and event_id.mode == candidate_mode
                and bool(anchor.linked_entry_states)
                and anchor.linked_entry_states[0] in successors
                for event_id, anchor in self.task_model.link_anchors.items()
            ):
                return False
        return True

    def _pending_relation_needed(
        self,
        event_id: RelationEventId,
        mode_by_skill: Mapping[int, int] | None,
    ) -> bool:
        candidate = self.task_model.link_pending_events[event_id]
        start = self._global_index[candidate.candidate_state]
        for state_id in self._states[start + 1 :]:
            node = self.task_model.state(state_id)
            mode = self._mode_for_state(state_id, mode_by_skill)
            if mode is None:
                selected = candidate.frame_id in node.selected_frames
                priors = node.demo_relation_priors.get(candidate.frame_id)
                linked = (
                    0.0
                    if priors is None
                    else float(np.sum(node.mode_priors * priors[:, 1]))
                )
            else:
                if mode < 0 or mode >= len(node.mode_selected_frames):
                    raise IndexError(
                        "mode_index is outside the pending successor state's mode range"
                    )
                selected = candidate.frame_id in node.mode_selected_frames[mode]
                priors = node.demo_relation_priors.get(candidate.frame_id)
                linked = 0.0 if priors is None else float(priors[mode, 1])
            if selected and linked >= self.config.expected_relation_probability:
                return True

        boundary = next(
            (
                model
                for boundary_id, model in sorted(self.task_model.boundaries.items())
                if boundary_id.source_skill == candidate.candidate_state.skill_index
            ),
            None,
        )
        if boundary is None:
            return False
        key = f"{self.task_model.arm_id}/{candidate.frame_id}"
        conditions = {
            **boundary.local_completion_model.own_relation_conditions,
            **boundary.relation_conditions,
        }
        condition = conditions.get(key)
        return condition is not None and condition.required_state == "linked"

    def _committed_pending_resolution_due(
        self,
        event_id: RelationEventId,
        context_state: StateId,
    ) -> bool:

        candidate = self.task_model.link_pending_events[event_id]
        release = candidate.release_state
        if release is None:
            return False
        if context_state == release:
            return True
        return release in self.task_model.state(context_state).topology.successors

    def _verification_requests(
        self,
        state_id: StateId,
        estimated_state: StateId,
        belief: LiteTwinBelief,
        mode_by_skill: Mapping[int, int] | None,
        *,
        naturally_confirming_events: frozenset[RelationEventId] = frozenset(),
        confirmed_events: frozenset[RelationEventId] = frozenset(),
        rejected_events: frozenset[RelationEventId] = frozenset(),
    ) -> tuple[RelationVerificationRequest, ...]:

        requests: dict[str, RelationVerificationRequest] = {}
        context_state = max(
            (state_id, estimated_state),
            key=self._global_index.__getitem__,
        )
        for event_id, candidate in sorted(self.task_model.link_pending_events.items()):
            active_candidate = self.task_model.active_link_pending_candidate(
                candidate.frame_id,
                context_state,
                mode_by_skill,
            )
            if active_candidate is None or active_candidate.event_id != event_id:
                continue
            mode = self._mode_for_state(candidate.candidate_state, mode_by_skill)
            if mode is not None and mode != event_id.mode:
                continue
            frame = candidate.frame_id
            estimate = belief.relation_estimates.get(frame)
            features = belief.runtime_features
            committed_occurrence = event_id in self._unresolved_pending_link_events
            committed_resolution_due = bool(
                committed_occurrence
                and self._committed_pending_resolution_due(event_id, context_state)
            )
            if (
                estimate is None
                or estimate.decision_state == RelationDecision.LINKED
                or not features.frame_pair_available.get(frame, False)
                or features.paired_tracking_reliability.get(frame, 0.0)
                < self.config.minimum_tracking_reliability
                or (
                    not committed_resolution_due
                    and (
                        not self._pending_relation_needed(event_id, mode_by_skill)
                        or estimate.information_weight
                        >= self.config.minimum_information_weight
                    )
                )
            ):
                continue
            request = RelationVerificationRequest(
                arm_id=self.task_model.arm_id,
                frame_id=frame,
                relation="linked",
                event_id=event_id,
                context_state=candidate.candidate_state,
                committed_occurrence=committed_resolution_due,
            )
            requests[request.event_id.token] = request

        for event_id in sorted(self._unresolved_link_events):
            if (
                event_id in naturally_confirming_events
                or event_id in confirmed_events
                or event_id in rejected_events
                or event_id in self._indirect_link_events
            ):
                continue
            anchor = self.task_model.link_anchors.get(event_id)
            if anchor is None or not anchor.linked_entry_states:
                continue
            frame = anchor.frame_id
            if event_id not in self._link_origins_for_state(
                frame,
                context_state,
                mode_by_skill,
            ):
                continue
            mode = (
                None
                if mode_by_skill is None
                else mode_by_skill.get(event_id.skill_index)
            )
            if mode is not None and mode != event_id.mode:
                continue
            estimate = belief.relation_estimates.get(frame)
            features = belief.runtime_features
            if (
                estimate is None
                or estimate.decision_state == RelationDecision.LINKED
                or not features.frame_pair_available.get(frame, False)
                or features.paired_tracking_reliability.get(frame, 0.0)
                < self.config.minimum_tracking_reliability
                or estimate.information_weight >= self.config.minimum_information_weight
            ):
                continue
            request = RelationVerificationRequest(
                arm_id=self.task_model.arm_id,
                frame_id=frame,
                relation="linked",
                event_id=event_id,
                context_state=anchor.linked_entry_states[-1],
            )
            requests[request.event_id.token] = request
        return tuple(requests[key] for key in sorted(requests))

    def route(
        self,
        state_id: StateId,
        belief: LiteTwinBelief,
        *,
        mode_by_skill: Mapping[int, int] | None = None,
        captured_virtual_frames: frozenset[str] = frozenset(),
        commit: bool = True,
    ) -> FrameRoleSnapshot:
        if state_id not in self.task_model.states:
            raise KeyError(f"Stream role references unknown state {state_id}")
        candidate_frames = self._candidate_frames(state_id, mode_by_skill)
        action_frames = self._action_frames(state_id, mode_by_skill)
        selected = frozenset(action_frames)
        monitor_frames = set(
            self._confirmed_monitor_frames(
                belief.progress.posterior,
                mode_by_skill,
            )
        )

        monitor_frames.update(
            frame
            for frame in self.task_model.relation_frames
            if self._link_confirmation_events(
                frame,
                state_id,
                mode_by_skill,
            )
        )
        confirmed_monitors = tuple(sorted(monitor_frames.difference(candidate_frames)))
        decisions: dict[str, FrameRoleDecision] = {}
        recovery_intents = []
        confirmed_link_events: set[RelationEventId] = set()
        rejected_link_events: set[RelationEventId] = set()
        unresolved_link_events = set(self._unresolved_link_events)
        unresolved_pending_link_events = set(self._unresolved_pending_link_events)
        naturally_confirming_events: set[RelationEventId] = set()
        features = belief.runtime_features
        context_state = max(
            (state_id, belief.progress.estimated_state),
            key=self._global_index.__getitem__,
        )
        for event_id in tuple(unresolved_pending_link_events):
            candidate = self.task_model.link_pending_events.get(event_id)
            estimate = belief.relation_estimates.get(event_id.frame_id)
            active = (
                None
                if candidate is None
                else self.task_model.active_link_pending_candidate(
                    candidate.frame_id,
                    context_state,
                    mode_by_skill,
                )
            )
            if (
                candidate is None
                or active is None
                or active.event_id != event_id
                or (
                    estimate is not None
                    and estimate.decision_state == RelationDecision.LINKED
                )
            ):
                unresolved_pending_link_events.discard(event_id)
                continue

            if (
                estimate is not None
                and estimate.decision_state == RelationDecision.EXTERNAL
                and estimate.informative
                and estimate.information_weight
                >= self.config.minimum_information_weight
                and estimate.informative_evidence_direction == RelationDecision.EXTERNAL
            ):
                recovery_intents.append(
                    RelationRecoveryIntent(
                        arm_id=self.task_model.arm_id,
                        frame_id=event_id.frame_id,
                        expected_relation=RelationDecision.LINKED,
                        actual_relation=RelationDecision.EXTERNAL,
                        origin_event_id=event_id,
                    )
                )

        for frame in (*candidate_frames, *confirmed_monitors):
            selected_offline = frame in selected
            reliability = (
                features.tracking_reliability.get(frame, 0.0)
                if features.frame_visibility.get(frame, False)
                else 0.0
            )
            if frame.startswith("virtual_skill_"):

                weight = (
                    1.0
                    if frame in captured_virtual_frames
                    else (
                        reliability
                        if features.frame_visibility.get(frame, False)
                        else 0.0
                    )
                )
                decisions[frame] = FrameRoleDecision(
                    frame_id=frame,
                    role=(FrameRole.EXECUTE if selected_offline else FrameRole.DEFER),
                    selected_offline=selected_offline,
                    expected_distribution=None,
                    expected_relation=None,
                    actual_relation=None,
                    relation_compatibility=1.0,
                    execution_weight=weight if selected_offline else 0.0,
                    monitor=False,
                    blocks_advance=False,
                )
                continue

            distribution = self._expected_distribution(
                frame, belief.progress.posterior, mode_by_skill
            )
            estimate = belief.relation_estimates.get(frame)
            actual = (
                RelationDecision.UNKNOWN
                if estimate is None
                else estimate.decision_state
            )
            expected = self._expected_decision(distribution)
            current_link_events = self._link_origins_for_state(
                frame,
                state_id,
                mode_by_skill,
            )
            if actual == RelationDecision.LINKED:
                confirmed_link_events.update(current_link_events)
            confirmation_events = self._link_confirmation_pending(
                frame,
                state_id,
                estimate,
                belief,
                mode_by_skill,
            )
            unresolved_link_events.update(confirmation_events)
            naturally_confirming_events.update(confirmation_events)
            if actual == RelationDecision.LINKED:
                confirmed_link_events.update(confirmation_events)
            if (
                not confirmation_events
                and expected == RelationDecision.LINKED
                and actual == RelationDecision.EXTERNAL
                and estimate is not None
                and estimate.informative
                and estimate.information_weight
                >= self.config.minimum_information_weight
                and estimate.observation_likelihood[0]
                > estimate.observation_likelihood[1]
            ):
                rejected_link_events.update(current_link_events)
            planned_link = self._planned_link_is_direct_successor(
                frame,
                state_id,
                mode_by_skill,
            )
            planned_unlink = self._planned_unlink_is_direct_successor(
                frame,
                state_id,
                mode_by_skill,
            )
            unlink_confirmation = self._is_unlink_confirmation_state(
                frame,
                state_id,
                mode_by_skill,
            )
            unresolved_low_information_link = bool(
                expected == RelationDecision.LINKED
                and actual
                in {
                    RelationDecision.UNKNOWN,
                    RelationDecision.EXTERNAL,
                }
                and estimate is not None
                and estimate.information_weight < self.config.minimum_information_weight
                and any(
                    event_id in self._unresolved_link_events
                    for event_id in current_link_events
                )
                and features.frame_pair_available.get(frame, False)
                and features.paired_tracking_reliability.get(frame, 0.0)
                >= self.config.minimum_tracking_reliability
            )
            compatibility = (
                0.5
                if estimate is None
                else float(np.dot(estimate.posterior, distribution))
            )
            if confirmation_events:

                role = FrameRole.DEFER
                weight = 0.0
                monitor = True
                blocks = False
                intent = None
            elif (
                planned_link
                and expected == RelationDecision.LINKED
                and actual in {RelationDecision.UNKNOWN, RelationDecision.EXTERNAL}
            ):

                role = FrameRole.DEFER
                weight = 0.0
                monitor = True
                blocks = False
                intent = None
            elif (
                planned_unlink
                and expected == RelationDecision.LINKED
                and actual in {RelationDecision.UNKNOWN, RelationDecision.EXTERNAL}
            ):

                role = FrameRole.DEFER
                weight = 0.0
                monitor = False
                blocks = False
                intent = None
            elif (
                unlink_confirmation
                and expected == RelationDecision.EXTERNAL
                and actual in {RelationDecision.UNKNOWN, RelationDecision.LINKED}
            ):

                role = FrameRole.EXECUTE if selected_offline else FrameRole.DEFER

                weight = reliability if selected_offline else 0.0
                monitor = True
                blocks = False
                intent = None
            elif unresolved_low_information_link:

                role = FrameRole.DEFER
                weight = 0.0
                monitor = True
                blocks = False
                intent = None
            elif actual == RelationDecision.UNKNOWN:
                role = FrameRole.DEFER
                safe_weight = (
                    0.0 if estimate is None else reliability * estimate.external
                )
                trusted = frame in self._last_trusted_weights
                weight = (
                    self._last_trusted_weights.get(frame, safe_weight)
                    if selected_offline
                    else 0.0
                )
                monitor = False

                safe_external_bootstrap = bool(
                    selected_offline
                    and not trusted
                    and expected == RelationDecision.EXTERNAL
                    and estimate is not None
                    and estimate.external > estimate.linked
                    and reliability >= self.config.minimum_tracking_reliability
                    and weight > 0.0
                )

                non_driving_external = bool(
                    not selected_offline and expected == RelationDecision.EXTERNAL
                )
                blocks = not (safe_external_bootstrap or non_driving_external)
                intent = None
            elif expected == RelationDecision.UNKNOWN:

                role = FrameRole.DEFER
                if actual == RelationDecision.LINKED:
                    weight = 0.0
                    monitor = True
                else:
                    assert estimate is not None
                    safe_weight = reliability * estimate.external
                    weight = self._last_trusted_weights.get(frame, safe_weight)
                    monitor = False
                blocks = False
                intent = None
            elif expected == actual == RelationDecision.EXTERNAL:
                role = FrameRole.EXECUTE if selected_offline else FrameRole.DEFER

                weight = reliability if selected_offline else 0.0
                monitor = False
                blocks = False
                intent = None
            elif expected == actual == RelationDecision.LINKED:
                role = FrameRole.MONITOR
                weight = 0.0
                monitor = True
                blocks = False
                intent = None
            elif (
                expected == RelationDecision.LINKED
                and actual == RelationDecision.EXTERNAL
                and current_link_events
                and all(
                    event_id in self._indirect_link_events
                    for event_id in current_link_events
                )
            ):

                role = FrameRole.DEFER
                weight = 0.0
                monitor = True
                blocks = False
                intent = None
            else:
                role = FrameRole.RECOVER
                weight = 0.0
                monitor = False
                blocks = True
                assert expected != RelationDecision.UNKNOWN
                assert actual != RelationDecision.UNKNOWN
                intent = RelationRecoveryIntent(
                    arm_id=self.task_model.arm_id,
                    frame_id=frame,
                    expected_relation=expected,
                    actual_relation=actual,
                )
                recovery_intents.append(intent)
            decisions[frame] = FrameRoleDecision(
                frame_id=frame,
                role=role,
                selected_offline=selected_offline,
                expected_distribution=distribution,
                expected_relation=expected,
                actual_relation=actual,
                relation_compatibility=compatibility,
                execution_weight=weight,
                monitor=monitor,
                blocks_advance=blocks,
                recovery_intent=intent,
                link_confirmation_pending=bool(confirmation_events),
            )

        requests = self._verification_requests(
            state_id,
            belief.progress.estimated_state,
            belief,
            mode_by_skill,
            naturally_confirming_events=frozenset(naturally_confirming_events),
            confirmed_events=frozenset(confirmed_link_events),
            rejected_events=frozenset(rejected_link_events),
        )

        unique_recovery_intents: dict[tuple[str, str], RelationRecoveryIntent] = {}
        for intent in recovery_intents:
            key = (intent.arm_id, intent.frame_id)
            previous = unique_recovery_intents.get(key)
            if previous is None or (
                previous.origin_event_id is None and intent.origin_event_id is not None
            ):
                unique_recovery_intents[key] = intent
        snapshot = FrameRoleSnapshot(
            state_id=state_id,
            decisions=decisions,
            recovery_intents=tuple(
                unique_recovery_intents[key] for key in sorted(unique_recovery_intents)
            ),
            verification_requests=requests,
            confirmed_link_events=tuple(sorted(confirmed_link_events)),
            rejected_link_events=tuple(sorted(rejected_link_events)),
            unresolved_link_events=tuple(
                sorted(
                    unresolved_link_events.difference(
                        confirmed_link_events, rejected_link_events
                    )
                )
            ),
            unresolved_pending_link_events=tuple(
                sorted(unresolved_pending_link_events)
            ),
        )
        if commit:
            self.commit(snapshot, belief)
        return snapshot


__all__ = [
    "FrameRole",
    "FrameRoleConfig",
    "FrameRoleDecision",
    "FrameRoleRouter",
    "FrameRoleSnapshot",
    "RelationRecoveryIntent",
    "RelationVerificationRequest",
]
