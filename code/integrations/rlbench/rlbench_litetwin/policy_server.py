"""Serve LiteTwin policies over a JSON-lines interface."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from litetwin.policy import BimanualDynaMAC, DynaMAC
from litetwin.policy.feedback import BoundaryRuntimeConfig, LiteTwinMultiStreamPolicy
from integrations.rlbench.rlbench_dynamac.core.task_specs import (
    TaskSpec,
    load_task_specs,
)
from integrations.rlbench.rlbench_litetwin.observation_adapter import (
    LiteTwinObservationAdapter,
    commands_to_rlbench,
)
from integrations.rlbench.rlbench_litetwin.protocol import litetwin_gripper_timing_metadata


POLICY_CLOCK_SEMANTICS_ID = "policy-tick-transactional-feedback-v1"


class LiteTwinPolicyServer:

    def __init__(
        self,
        task: str,
        models_dir: Path,
        base_models_dir: Path,
        *,
        diagnostics_dir: Path | None = None,
        task_spec: TaskSpec,
        boundary_config: Path | None = None,
    ) -> None:
        self.task = task
        self.task_spec = task_spec
        self.bimanual = task_spec.bimanual
        task_dir = Path(base_models_dir) / task
        if self.bimanual:
            base_policies = {
                "left": DynaMAC.load(task_dir / "left.npz"),
                "right": DynaMAC.load(task_dir / "right.npz"),
            }
            base_identity = BimanualDynaMAC(
                left=base_policies["left"],
                right=base_policies["right"],
            ).summary()
        else:
            base_policies = {"single": DynaMAC.load(task_dir / "model.npz")}
            base_identity = base_policies["single"].summary()
        runtime_boundary = (
            None
            if boundary_config is None
            else BoundaryRuntimeConfig.from_json(boundary_config)
        )
        self.policy = LiteTwinMultiStreamPolicy.load(
            Path(models_dir) / task,
            base_policies=base_policies,
            boundary_config=runtime_boundary,
        )
        self.adapter = LiteTwinObservationAdapter(task_spec)
        self.arms = self.policy.arms
        self.model_identity = {
            "policy_type": self.policy.name,
            "motion_policy": base_identity,
            "task_state_model": self.policy.summary(),
        }
        self.diagnostics_dir = diagnostics_dir
        self._episode_index = -1
        self._tick = 0
        self._previous_ee: dict[str, np.ndarray | None] = {
            arm: None for arm in self.arms
        }
        self._previous_command: dict[str, np.ndarray | None] = {
            arm: None for arm in self.arms
        }
        self._previous_command_covariance: dict[str, np.ndarray | None] = {
            arm: None for arm in self.arms
        }
        self._pending: dict[str, Any] | None = None
        self._next_transaction_id = 1

    def _adapt(self, payload: Mapping[str, Any]):
        return self.adapter.build(
            payload,
            tick=self._tick,
            previous_ee_pose=self._previous_ee,
            previous_command_pose=self._previous_command,
            previous_command_covariance=self._previous_command_covariance,
        )

    def _save_diagnostics(self) -> None:
        if self.diagnostics_dir is None or self._episode_index < 0:
            return
        path = (
            self.diagnostics_dir
            / self.task
            / f"episode_{self._episode_index:04d}.jsonl"
        )
        self.policy.diagnostics.save(path)

    def _reset(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if self._pending is not None:
            self.policy.abort()
            self._pending = None
        self._save_diagnostics()
        self._episode_index += 1
        self._tick = 0
        self._previous_ee = {arm: None for arm in self.arms}
        self._previous_command = {arm: None for arm in self.arms}
        self._previous_command_covariance = {arm: None for arm in self.arms}
        batch = self._adapt(payload)
        self.policy.reset(batch.dynamac, mode_strategy="map")
        return {"ok": True, "complete": self.policy.complete}

    def _act(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if self._pending is not None:
            raise RuntimeError("Previous closed-loop action has not been resolved")
        batch = self._adapt(payload)
        cycle = self.policy.act(batch.dynamac, batch.runtime)
        transaction_id = self._next_transaction_id
        self._next_transaction_id += 1
        self._pending = {
            "transaction_id": transaction_id,
            "pre_action_ee": {
                arm: batch.runtime[arm].ee_pose.copy() for arm in self.arms
            },
            "commands": {arm: cycle.commands[arm].pose.copy() for arm in self.arms},
            "command_covariances": {
                arm: cycle.commands[arm].covariance.copy() for arm in self.arms
            },
            "gripper_authorization": {
                arm: cycle.commands[arm].gripper_authorized for arm in self.arms
            },
        }
        action = commands_to_rlbench(cycle.commands, bimanual=self.bimanual)
        diagnostics = (
            cycle.diagnostics if isinstance(cycle.diagnostics, Mapping) else {}
        )
        arm_diagnostics = diagnostics.get("arms", {})
        states = {}
        for arm in self.arms:
            controller = self.policy.execution_controllers[arm]
            reference = controller.cursor.reference_state
            execution = arm_diagnostics.get(arm, {}).get("execution") or {}
            states[arm] = {
                "skill_index": reference.skill_index,
                "time_index": reference.local_index,
                "mode": self.policy._mode_by_arm_skill[arm][reference.skill_index],
                "active_frames": list(execution.get("participating_streams", ())),
                "poe_weights": dict(execution.get("poe_weights", {})),
                "execution_mode": cycle.arms[arm].mode_after.value,
                "reentry_committed": bool(
                    isinstance(arm_diagnostics.get(arm, {}).get("recovery"), Mapping)
                    and arm_diagnostics[arm]["recovery"].get("reentry") is not None
                ),
            }
        policy_state = states if self.bimanual else states["single"]
        return {
            "ok": True,
            "complete": self.policy.complete,
            "policy_failed": cycle.lifecycle.value == "failed",
            "failure_reasons": {
                arm: result.failure_reason
                for arm, result in cycle.arms.items()
                if result.failure_reason is not None
            },
            "action": action.tolist(),
            "transaction_id": transaction_id,
            "policy_state": policy_state,
            "gripper_authorization": {
                arm: cycle.commands[arm].gripper_authorized for arm in self.arms
            },
        }

    def _resolve(self, request: Mapping[str, Any], *, commit: bool) -> dict[str, Any]:
        if self._pending is None:
            raise RuntimeError("No closed-loop action transaction is pending")
        transaction_id = request.get("transaction_id")
        if transaction_id != self._pending["transaction_id"]:
            raise RuntimeError(
                "Closed-loop action transaction identifier does not match"
            )
        if not commit:
            self.policy.abort()
            action_status = "aborted"
        else:
            action_status = request.get("primary_action_status")
            if action_status not in {"reached", "progressed", "stopped"}:
                raise ValueError("Unsupported primary action status")
            primary_action_applied = request.get("primary_action_applied", True)
            action_statuses = request.get("primary_action_statuses") or {
                arm: action_status for arm in self.arms
            }
            if set(action_statuses) != set(self.arms):
                raise ValueError("Primary action statuses must cover every arm")
            self.policy.commit(
                task_command_applied={
                    arm: bool(primary_action_applied) for arm in self.arms
                },
                absolute_target_completed={
                    arm: bool(primary_action_applied and status == "reached")
                    for arm, status in action_statuses.items()
                },
            )
            self._previous_ee = self._pending["pre_action_ee"]
            self._previous_command = (
                self._pending["commands"]
                if primary_action_applied
                else {
                    arm: self._pending["pre_action_ee"][arm].copy() for arm in self.arms
                }
            )
            self._previous_command_covariance = (
                self._pending["command_covariances"]
                if primary_action_applied
                else {arm: None for arm in self.arms}
            )
            self._tick += 1
        self._pending = None
        return {
            "ok": True,
            "transaction_id": transaction_id,
            "committed": commit,
            "complete": self.policy.complete,
            "primary_action_status": action_status,
        }

    def handle(self, request: Mapping[str, Any]) -> dict[str, Any]:
        command = request.get("command")
        if command == "ping":
            return {
                "ok": True,
                "ready": True,
                "task": self.task,
                "bimanual": self.bimanual,
                "policy_steps": max(
                    len(model.states) for model in self.policy.task_models.values()
                ),
                "model_identity": self.model_identity,
                "policy_clock_semantics_id": POLICY_CLOCK_SEMANTICS_ID,
                "gripper_timing": litetwin_gripper_timing_metadata(),
                "policy_type": self.policy.name,
            }
        if command == "close":
            if self._pending is not None:
                self.policy.abort()
                self._pending = None
            self._save_diagnostics()
            return {"ok": True, "closed": True}
        if command == "commit":
            return self._resolve(request, commit=True)
        if command == "abort":
            return self._resolve(request, commit=False)
        if command not in {"reset", "act"}:
            raise ValueError(
                "Command must be ping, reset, act, commit, abort, or close"
            )
        payload = request.get("observation")
        if not isinstance(payload, Mapping):
            raise TypeError("Reset and act commands require an observation")
        return self._reset(payload) if command == "reset" else self._act(payload)


def serve(
    task: str,
    models_dir: Path,
    base_models_dir: Path,
    *,
    diagnostics_dir: Path | None = None,
    task_spec: TaskSpec,
    boundary_config: Path | None = None,
) -> int:
    server = LiteTwinPolicyServer(
        task,
        models_dir,
        base_models_dir,
        diagnostics_dir=diagnostics_dir,
        task_spec=task_spec,
        boundary_config=boundary_config,
    )
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            response = server.handle(json.loads(line))
        except Exception as exc:
            response = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        print(json.dumps(response, separators=(",", ":")), flush=True)
        if response.get("closed"):
            return 0
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("serve",))
    parser.add_argument("--task", required=True)
    parser.add_argument("--models-dir", type=Path, required=True)
    parser.add_argument("--base-models-dir", type=Path, required=True)
    parser.add_argument("--diagnostics-dir", type=Path)
    parser.add_argument("--task-specs", type=Path, required=True)
    parser.add_argument("--boundary-config", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    task_spec = load_task_specs(args.task_specs)[args.task]
    return serve(
        args.task,
        args.models_dir,
        args.base_models_dir,
        diagnostics_dir=args.diagnostics_dir,
        task_spec=task_spec,
        boundary_config=args.boundary_config,
    )


if __name__ == "__main__":
    raise SystemExit(main())
