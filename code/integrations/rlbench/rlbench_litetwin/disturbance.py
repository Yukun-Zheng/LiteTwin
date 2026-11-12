"""Apply and audit a physical relation-loss disturbance in RLBench."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np


@dataclass(frozen=True)
class RelationLossSpec:

    earliest_step: int
    minimum_stable_cycles: int = 3
    translation: tuple[float, float, float] = (0.04, 0.0, 0.0)

    def __post_init__(self):
        if self.earliest_step < 0 or self.minimum_stable_cycles < 1:
            raise ValueError("Relation-loss timing values are invalid")
        value = np.asarray(self.translation, dtype=np.float64)
        if value.shape != (3,) or not np.all(np.isfinite(value)):
            raise ValueError(
                "Relation-loss translation must contain three finite values"
            )


class RelationLossEnvironment:

    def __init__(self, environment: Any, spec: RelationLossSpec):
        self._environment = environment
        self.spec = spec
        self.events: list[dict[str, Any]] = []
        self._step = 0
        self._triggered = False
        self._stable_signature = None
        self._stable_cycles = 0
        self._source = None
        self._targets: dict[str, Any] = {}
        self._target_positions: dict[str, np.ndarray] = {}
        self._effect_observed = None
        self._effect_step = None
        self._relation_restored = False
        self._restoration_step = None
        self._restored_contact_cycles = 0

    def __getattr__(self, name: str):
        return getattr(self._environment, name)

    def _gripper(self):
        return self._environment._scene.robot.gripper

    @staticmethod
    def _names(objects):
        return tuple(sorted(str(obj.get_name()) for obj in objects))

    def _detected_targets(self):
        task = self._environment._scene.task
        gripper = self._gripper()
        sensor = getattr(gripper, "_proximity_sensor", None)
        detects = getattr(sensor, "is_detected", None)
        if not callable(detects):
            raise RuntimeError("The gripper proximity sensor is unavailable")
        candidates = list(task.get_graspable_objects())
        base = task.get_base()
        for obj in base.get_objects_in_tree(exclude_base=False):
            dynamic = getattr(obj, "is_dynamic", None)
            respondable = getattr(obj, "is_respondable", None)
            if (
                callable(dynamic)
                and callable(respondable)
                and bool(dynamic())
                and bool(respondable())
            ):
                candidates.append(obj)
        unique = []
        seen = set()
        for obj in candidates:
            if id(obj) not in seen and bool(detects(obj)):
                seen.add(id(obj))
                unique.append(obj)
        return tuple(unique)

    def _gripper_open(self):
        amount = np.asarray(self._gripper().get_open_amount(), dtype=np.float64)
        return bool(np.all(amount > 0.9))

    def _interaction(self, action):
        grasped = tuple(self._gripper().get_grasped_objects())
        if grasped:
            return "attachment", grasped
        if float(action[7]) <= 0.5:
            return "maintained_contact", self._detected_targets()
        return "maintained_contact", ()

    def _update_stability(self, action):
        source, targets = self._interaction(action)
        names = self._names(targets)
        signature = (source, names) if names else None
        if signature is None:
            self._stable_signature = None
            self._stable_cycles = 0
        elif signature == self._stable_signature:
            self._stable_cycles += 1
        else:
            self._stable_signature = signature
            self._stable_cycles = 1
        return source, targets

    def _trigger(self, source, targets):
        self._source = source
        self._targets = {str(obj.get_name()): obj for obj in targets}
        self._target_positions = {
            name: np.asarray(obj.get_position(), dtype=np.float64).copy()
            for name, obj in self._targets.items()
            if callable(getattr(obj, "get_position", None))
        }
        self._gripper().release()
        displaced = source == "attachment"
        if displaced:
            delta = np.asarray(self.spec.translation, dtype=np.float64)
            for obj in targets:
                obj.set_position(
                    np.asarray(obj.get_position(), dtype=np.float64) + delta
                )
        self._triggered = True
        self.events.append(
            {
                "kind": "relation_loss",
                "policy_step": self._step,
                "interaction_source": source,
                "released_objects": sorted(self._targets),
                "displaced": displaced,
                "translation": list(self.spec.translation) if displaced else None,
            }
        )

    def _authorize_release(self):
        action_mode = getattr(self._environment, "_action_mode", None)
        setter = getattr(action_mode, "set_policy_gripper_authorization", None)
        if callable(setter):
            setter({"single": True})

    def _audit(self):
        if not self._triggered:
            return
        grasped = set(self._names(self._gripper().get_grasped_objects()))
        detected = set(self._names(self._detected_targets()))
        targets = set(self._targets)
        if self._effect_observed is None:
            detached = not targets.intersection(grasped)
            if self._source == "attachment":
                expected = float(np.linalg.norm(self.spec.translation))
                displaced = all(
                    float(
                        np.linalg.norm(
                            np.asarray(
                                self._targets[name].get_position(), dtype=np.float64
                            )
                            - self._target_positions[name]
                        )
                    )
                    >= max(0.0, expected - 0.01)
                    for name in targets
                )
            else:
                displaced = self._gripper_open()
            self._effect_observed = bool(detached and displaced)
            self._effect_step = self._step
            return
        if self._source == "attachment":
            restored = bool(targets.intersection(grasped))
        else:
            contact = not self._gripper_open() and targets.issubset(detected)
            self._restored_contact_cycles = (
                self._restored_contact_cycles + 1 if contact else 0
            )
            restored = self._restored_contact_cycles >= self.spec.minimum_stable_cycles
        if self._effect_observed and not self._relation_restored and restored:
            self._relation_restored = True
            self._restoration_step = self._step
            self.events.append(
                {
                    "kind": "relation_restored",
                    "policy_step": self._step,
                    "interaction_source": self._source,
                }
            )

    def step(self, action):
        command = np.asarray(action, dtype=np.float64)
        if command.shape != (9,):
            raise ValueError("The Open Drawer demo expects a 9D action")
        source, targets = self._update_stability(command)
        triggered_now = (
            not self._triggered
            and self._step >= self.spec.earliest_step
            and self._stable_cycles >= self.spec.minimum_stable_cycles
        )
        applied = command
        if triggered_now:
            self._trigger(source, targets)
            applied = command.copy()
            applied[7] = 1.0
            self._authorize_release()
        result = self._environment.step(applied)
        self._audit()
        self._step += 1
        return result

    def record_committed_fallback(self):
        self._step += 1

    def protocol_metadata(self):
        return {
            "schema": "litetwin.relation-loss.v1",
            "spec": asdict(self.spec),
            "triggered": self._triggered,
            "events": list(self.events),
            "policy_steps_observed": self._step,
            "physical_effect_observed": self._effect_observed,
            "effect_policy_step": self._effect_step,
            "relation_restored": self._relation_restored,
            "relation_restoration_policy_step": self._restoration_step,
            "target_objects": sorted(self._targets),
        }


__all__ = ["RelationLossEnvironment", "RelationLossSpec"]
