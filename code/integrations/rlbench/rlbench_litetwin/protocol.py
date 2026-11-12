"""Shared LiteTwin execution timing metadata."""

from __future__ import annotations

from typing import Any


LITETWIN_GRIPPER_TIMING_PROTOCOL_ID = "litetwin-state-or-link-edge-gripper-v2"
LITETWIN_GRIPPER_TIMING_RULE = "state_command_or_learned_link_transition_preparation"


def litetwin_gripper_timing_metadata() -> dict[str, Any]:
    return {
        "protocol_id": LITETWIN_GRIPPER_TIMING_PROTOCOL_ID,
        "rule": LITETWIN_GRIPPER_TIMING_RULE,
        "policy_pose_tick": "t",
        "default_gripper_tick": "t",
        "boundary_transition_gripper_tick": "commit_or_link_prepare",
        "preparation_scope": ("learned_link_or_link_pending_open_to_closed_edge_only"),
        "preparation_progress_semantics": "no_stateid_or_localdone_commit",
        "release_semantics": "post_boundary_commit_only",
        "authorization": (
            "aligned_task_state_or_committed_boundary_or_link_edge_preparation"
        ),
        "executor_status_role": "physical_diagnostic",
    }


__all__ = [
    "LITETWIN_GRIPPER_TIMING_PROTOCOL_ID",
    "LITETWIN_GRIPPER_TIMING_RULE",
    "litetwin_gripper_timing_metadata",
]
