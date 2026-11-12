"""Relation recovery goals and gripper-resource ordering."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Sequence

from ..control.frame_roles import RelationRecoveryIntent
from ..model.link_anchors import EpisodeLinkAnchorRegistry, RuntimeLinkAnchor
from ..model.relation_events import UnlinkEventMetadata
from ..inference.relation_filter import RelationDecision
from ..model.state_index import StateId
from ..model.unlink_metadata import UnlinkMetadataRepository


class RelationGoalKind(str, Enum):
    LINK = "link"
    UNLINK = "unlink"


@dataclass(frozen=True)
class RelationGoal:
    arm_id: str
    frame_id: str
    kind: RelationGoalKind
    expected_relation: RelationDecision
    actual_relation: RelationDecision
    source_state: StateId
    mode: int
    link_anchor: RuntimeLinkAnchor | None = None
    unlink_metadata: UnlinkEventMetadata | None = None

    def __post_init__(self) -> None:
        if not self.arm_id or not self.frame_id or self.mode < 0:
            raise ValueError(
                "Relation-recovery target has an invalid arm, frame, or mode"
            )
        if RelationDecision.UNKNOWN in {
            self.expected_relation,
            self.actual_relation,
        }:
            raise ValueError(
                "Relation-recovery target must originate from a reliable binary mismatch"
            )
        expected_kind = (
            RelationGoalKind.LINK
            if self.expected_relation == RelationDecision.LINKED
            else RelationGoalKind.UNLINK
        )
        if self.kind != expected_kind:
            raise ValueError(
                "Relation-recovery target direction conflicts with the expected relation"
            )
        if self.kind == RelationGoalKind.LINK:
            if self.actual_relation != RelationDecision.EXTERNAL:
                raise ValueError(
                    "A LINK target requires the current relation to be external"
                )
            if self.link_anchor is None or self.unlink_metadata is not None:
                raise ValueError("A LINK target must carry exactly one LINK anchor")
        else:
            if self.actual_relation != RelationDecision.LINKED:
                raise ValueError(
                    "An UNLINK target requires the current relation to be linked"
                )
            if self.unlink_metadata is None or self.link_anchor is not None:
                raise ValueError(
                    "An UNLINK target must carry exactly one UNLINK metadata object"
                )

    @property
    def legal_reentry_states(self) -> tuple[StateId, ...]:
        if self.kind == RelationGoalKind.LINK:
            assert self.link_anchor is not None
            return self.link_anchor.legal_reentry_states
        assert self.unlink_metadata is not None
        return self.unlink_metadata.legal_reentry_states


class RelationGoalPlanner:

    def __init__(
        self,
        anchor_registry: EpisodeLinkAnchorRegistry,
        unlink_repository: UnlinkMetadataRepository,
    ) -> None:
        if anchor_registry.task_model is not unlink_repository.task_model:
            raise ValueError(
                "Relation-goal planner LINK and UNLINK stores must belong to the same task model"
            )
        self.anchor_registry = anchor_registry
        self.unlink_repository = unlink_repository
        self.task_model = anchor_registry.task_model

    def plan(
        self,
        intents: Sequence[RelationRecoveryIntent],
        *,
        source_state: StateId,
        mode: int,
    ) -> tuple[RelationGoal, ...]:
        by_frame: dict[str, RelationRecoveryIntent] = {}
        for intent in intents:
            if intent.arm_id != self.task_model.arm_id:
                raise ValueError("Recovery intent does not belong to the current arm")
            previous = by_frame.get(intent.frame_id)
            if previous is not None and previous != intent:
                raise ValueError(
                    "The same frame received conflicting relation-recovery intents"
                )
            by_frame[intent.frame_id] = intent

        goals = []
        for frame, intent in sorted(by_frame.items()):
            if intent.expected_relation == RelationDecision.LINKED:
                goals.append(
                    RelationGoal(
                        arm_id=intent.arm_id,
                        frame_id=frame,
                        kind=RelationGoalKind.LINK,
                        expected_relation=intent.expected_relation,
                        actual_relation=intent.actual_relation,
                        source_state=source_state,
                        mode=mode,
                        link_anchor=(
                            self.anchor_registry.resolve_for_recovery(
                                frame, source_state, mode
                            )
                            if intent.origin_event_id is None
                            else self.anchor_registry.resolve_event_for_recovery(
                                intent.origin_event_id
                            )
                        ),
                    )
                )
            else:
                goals.append(
                    RelationGoal(
                        arm_id=intent.arm_id,
                        frame_id=frame,
                        kind=RelationGoalKind.UNLINK,
                        expected_relation=intent.expected_relation,
                        actual_relation=intent.actual_relation,
                        source_state=source_state,
                        mode=mode,
                        unlink_metadata=self.unlink_repository.resolve(
                            frame, source_state, mode
                        ),
                    )
                )

        return tuple(
            sorted(
                goals,
                key=lambda goal: (
                    0 if goal.kind == RelationGoalKind.UNLINK else 1,
                    goal.frame_id,
                ),
            )
        )

    def plan_available(
        self,
        intents: Sequence[RelationRecoveryIntent],
        *,
        source_state: StateId,
        mode: int,
    ) -> tuple[tuple[RelationGoal, ...], tuple[RelationRecoveryIntent, ...]]:

        by_frame: dict[str, RelationRecoveryIntent] = {}
        for intent in intents:
            if intent.arm_id != self.task_model.arm_id:
                raise ValueError("Recovery intent does not belong to the current arm")
            previous = by_frame.get(intent.frame_id)
            if previous is not None and previous != intent:
                raise ValueError(
                    "The same frame received conflicting relation-recovery intents"
                )
            by_frame[intent.frame_id] = intent

        goals: list[RelationGoal] = []
        unavailable: list[RelationRecoveryIntent] = []
        for intent in (by_frame[frame] for frame in sorted(by_frame)):
            try:
                goals.extend(
                    self.plan(
                        (intent,),
                        source_state=source_state,
                        mode=mode,
                    )
                )
            except KeyError:
                unavailable.append(intent)
        return (
            tuple(
                sorted(
                    goals,
                    key=lambda goal: (
                        0 if goal.kind == RelationGoalKind.UNLINK else 1,
                        goal.frame_id,
                    ),
                )
            ),
            tuple(unavailable),
        )


__all__ = ["RelationGoal", "RelationGoalKind", "RelationGoalPlanner"]
