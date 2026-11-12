"""Task-frame schemas for compact RLBench observations."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np

from integrations.rlbench.rlbench_dynamac.core.paths import INTEGRATION_ROOT

Array = np.ndarray
RLBenchPoseConvention = "world [x, y, z, qx, qy, qz, qw]"
CorePoseConvention = "world [x, y, z, qw, qx, qy, qz]"
RLBENCH_REFERENCE_COMMIT = "a51b4e609dc5c3e1a8c06046bd87a9da24723da4"
CANDIDATE_FRAME_POLICY = "ALL_GET_LOW_DIM_STATE_FRAMES_IN_SOURCE_ORDER"
TRAINING_MANIFEST_SCHEMA_STATIC_V1 = "dynamac-direct-static-training-v1"
TASK_CONFIG_PATH = INTEGRATION_ROOT / "configs" / "open_drawer" / "task.json"

SegmentationCoordination = Literal["single", "independent", "shared_union"]
RECOVERABLE_RELATION_ROLES = frozenset(
    {
        "operated_object",
        "candidate_object",
        "articulated_object",
        "operated_tool",
        "variation_target",
        "carried_object",
        "cooperatively_operated_object",
        "cooperatively_operated_tool",
    }
)


def _pose_array(value: Any, *, convention: str) -> Array:

    pose = np.asarray(value, dtype=np.float64)
    if pose.ndim < 1 or pose.shape[-1] != 7:
        raise ValueError(f"{convention} pose must end in 7 values, got {pose.shape}")
    if not np.all(np.isfinite(pose)):
        raise ValueError(f"{convention} pose contains non-finite values")
    quaternion_norm = np.linalg.norm(pose[..., 3:7], axis=-1)
    if np.any(quaternion_norm <= 0.0):
        raise ValueError(f"{convention} pose contains a zero quaternion")
    return pose


def xyzw_to_wxyz(pose: Any) -> Array:

    value = _pose_array(pose, convention="RLBench xyzw")
    return np.concatenate((value[..., :3], value[..., 6:7], value[..., 3:6]), axis=-1)


def wxyz_to_xyzw(pose: Any) -> Array:

    value = _pose_array(pose, convention="core wxyz")
    return np.concatenate((value[..., :3], value[..., 4:7], value[..., 3:4]), axis=-1)


rlbench_pose_xyzw_to_core_wxyz = xyzw_to_wxyz
core_pose_wxyz_to_rlbench_xyzw = wxyz_to_xyzw


def unwrap_task_low_dim_state(value: Any) -> Array:

    if isinstance(value, tuple):
        if len(value) != 1:
            raise ValueError(
                "RLBench task_low_dim_state tuple must contain exactly one value"
            )
        value = value[0]
        if isinstance(value, tuple):
            raise ValueError("nested task_low_dim_state tuples are not supported")
    state = np.asarray(value, dtype=np.float64)
    if state.ndim == 2 and state.shape[0] == 1:

        state = state[0]
    if state.ndim != 1 or state.size == 0:
        raise ValueError(
            f"task_low_dim_state must be a non-empty flat vector, got {state.shape}"
        )
    if not np.all(np.isfinite(state)):
        raise ValueError("task_low_dim_state contains non-finite values")
    return state.copy()


@dataclass(frozen=True)
class TaskPoseChunk:

    name: str
    role: str
    index: int
    action_reference: bool = True

    @property
    def start(self) -> int:
        return 7 * self.index

    @property
    def stop(self) -> int:
        return self.start + 7

    @property
    def source_slice(self) -> tuple[int, int]:
        return self.start, self.stop


@dataclass(frozen=True)
class TaskConfigurationChunk:

    entity: str
    name: str
    size: int


@dataclass(frozen=True)
class TaskSpec:

    task_name: str
    module: str
    class_name: str
    bimanual: bool
    pose_chunks: tuple[TaskPoseChunk, ...]
    configuration_chunks: tuple[TaskConfigurationChunk, ...]
    structural_bindings: dict[str, str]
    source_expression: str
    segmentation_coordination: SegmentationCoordination = "single"

    @property
    def frame_names(self) -> tuple[str, ...]:
        return tuple(chunk.name for chunk in self.pose_chunks)

    @property
    def action_frame_names(self) -> tuple[str, ...]:
        return tuple(chunk.name for chunk in self.pose_chunks if chunk.action_reference)

    @property
    def scene_entity_names(self) -> tuple[str, ...]:
        return tuple(
            chunk.name for chunk in self.pose_chunks if not chunk.action_reference
        )

    @property
    def expected_low_dim_size(self) -> int:
        return 7 * len(self.pose_chunks) + sum(
            chunk.size for chunk in self.configuration_chunks
        )

    @property
    def configuration_schema(self) -> dict[str, dict[str, int]]:
        schema: dict[str, dict[str, int]] = {}
        for chunk in self.configuration_chunks:
            schema.setdefault(chunk.entity, {})[chunk.name] = chunk.size
        return schema

    @property
    def arm_count(self) -> int:
        return 2 if self.bimanual else 1

    @property
    def recoverable_relation_frames(self) -> tuple[str, ...]:

        return tuple(
            chunk.name
            for chunk in self.pose_chunks
            if chunk.role in RECOVERABLE_RELATION_ROLES
        )

    def extract_pose_chunks(
        self,
        task_low_dim_state: Any,
        *,
        convention: str = "core_wxyz",
    ) -> dict[str, Array]:

        state = unwrap_task_low_dim_state(task_low_dim_state)
        if state.size != self.expected_low_dim_size:
            raise ValueError(
                f"{self.task_name} task_low_dim_state has {state.size} values; "
                f"expected {self.expected_low_dim_size} from {self.source_expression}"
            )
        if convention not in {"rlbench_xyzw", "core_wxyz"}:
            raise ValueError("convention must be 'rlbench_xyzw' or 'core_wxyz'")
        chunks = {
            chunk.name: state[chunk.start : chunk.stop].copy()
            for chunk in self.pose_chunks
        }
        if convention == "core_wxyz":
            chunks = {name: xyzw_to_wxyz(pose) for name, pose in chunks.items()}
        return chunks

    def extract_entity_configurations(
        self,
        task_low_dim_state: Any,
    ) -> dict[str, dict[str, Array]]:

        state = unwrap_task_low_dim_state(task_low_dim_state)
        if state.size != self.expected_low_dim_size:
            raise ValueError(
                f"{self.task_name} task_low_dim_state has {state.size} values; "
                f"expected {self.expected_low_dim_size} from {self.source_expression}"
            )
        cursor = 7 * len(self.pose_chunks)
        result: dict[str, dict[str, Array]] = {}
        for chunk in self.configuration_chunks:
            result.setdefault(chunk.entity, {})[chunk.name] = state[
                cursor : cursor + chunk.size
            ].copy()
            cursor += chunk.size
        return result


def task_spec_identity(spec: TaskSpec) -> dict[str, Any]:

    return {
        "schema": "rlbench-task-spec-identity-v1",
        "task_name": spec.task_name,
        "module": spec.module,
        "class_name": spec.class_name,
        "source_expression": spec.source_expression,
        "frame_names": list(spec.frame_names),
        "action_frame_names": list(spec.action_frame_names),
        "scene_entity_names": list(spec.scene_entity_names),
        "configuration_schema": spec.configuration_schema,
        "structural_bindings": dict(spec.structural_bindings),
        "expected_low_dim_size": spec.expected_low_dim_size,
    }


def _parse_task_spec(task_name: str, raw: Mapping[str, Any]) -> TaskSpec:
    required = {
        "module",
        "class_name",
        "bimanual",
        "pose_chunks",
        "source_expression",
        "segmentation_coordination",
    }
    missing = required.difference(raw)
    if missing:
        raise ValueError(f"{task_name} task spec is missing {sorted(missing)}")
    raw_chunks = raw["pose_chunks"]
    if not isinstance(raw_chunks, list) or not raw_chunks:
        raise ValueError(f"{task_name}.pose_chunks must be a non-empty list")
    chunks: list[TaskPoseChunk] = []
    for index, item in enumerate(raw_chunks):
        if not isinstance(item, Mapping):
            raise ValueError(f"{task_name}.pose_chunks[{index}] must be an object")
        name = item.get("name")
        role = item.get("role")
        action_reference = item.get("action_reference", True)
        if (
            not isinstance(name, str)
            or not name
            or not isinstance(role, str)
            or not role
        ):
            raise ValueError(f"{task_name}.pose_chunks[{index}] needs name and role")
        if not isinstance(action_reference, bool):
            raise ValueError(
                f"{task_name}.pose_chunks[{index}].action_reference must be boolean"
            )
        chunks.append(
            TaskPoseChunk(
                name=name,
                role=role,
                index=index,
                action_reference=action_reference,
            )
        )
    if len({chunk.name for chunk in chunks}) != len(chunks):
        raise ValueError(f"{task_name} has duplicate task-frame names")
    raw_configurations = raw.get("configuration_chunks", [])
    if not isinstance(raw_configurations, list):
        raise ValueError(f"{task_name}.configuration_chunks must be a list")
    configurations: list[TaskConfigurationChunk] = []
    for index, item in enumerate(raw_configurations):
        if not isinstance(item, Mapping):
            raise ValueError(
                f"{task_name}.configuration_chunks[{index}] must be an object"
            )
        entity = item.get("entity")
        name = item.get("name")
        size = item.get("size")
        if (
            not isinstance(entity, str)
            or not entity
            or entity not in {chunk.name for chunk in chunks}
            or not isinstance(name, str)
            or not name
            or isinstance(size, bool)
            or not isinstance(size, int)
            or size < 1
        ):
            raise ValueError(f"{task_name}.configuration_chunks[{index}] is invalid")
        configurations.append(
            TaskConfigurationChunk(entity=entity, name=name, size=size)
        )
    configuration_keys = [(chunk.entity, chunk.name) for chunk in configurations]
    if len(configuration_keys) != len(set(configuration_keys)):
        raise ValueError(f"{task_name} has duplicate entity-configuration fields")
    raw_bindings = raw.get("structural_bindings", {})
    if not isinstance(raw_bindings, Mapping):
        raise ValueError(f"{task_name}.structural_bindings must be an object")
    frame_names = {chunk.name for chunk in chunks}
    structural_bindings: dict[str, str] = {}
    for child, parent in raw_bindings.items():
        if (
            not isinstance(child, str)
            or not isinstance(parent, str)
            or child not in frame_names
            or parent not in frame_names
            or child == parent
        ):
            raise ValueError(
                f"{task_name}.structural_bindings contains an invalid edge"
            )
        structural_bindings[child] = parent
    bimanual = bool(raw["bimanual"])
    coordination = raw["segmentation_coordination"]
    allowed_coordination = {"independent", "shared_union"} if bimanual else {"single"}
    if coordination not in allowed_coordination:
        raise ValueError(
            f"{task_name}.segmentation_coordination must be one of "
            f"{sorted(allowed_coordination)}"
        )
    return TaskSpec(
        task_name=task_name,
        module=str(raw["module"]),
        class_name=str(raw["class_name"]),
        bimanual=bimanual,
        pose_chunks=tuple(chunks),
        configuration_chunks=tuple(configurations),
        structural_bindings=structural_bindings,
        source_expression=str(raw["source_expression"]),
        segmentation_coordination=coordination,
    )


def load_task_specs(path: str | Path = TASK_CONFIG_PATH) -> dict[str, TaskSpec]:

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema") != "rlbench-dynamac-task-specs-v3":
        raise ValueError("unsupported RLBench DynaMAC task-spec schema")
    source = payload.get("source", {})
    if source.get("commit") != RLBENCH_REFERENCE_COMMIT:
        raise ValueError("task specs are not bound to the pinned RLBench commit")
    raw_tasks = payload.get("tasks")
    if not isinstance(raw_tasks, Mapping) or not raw_tasks:
        raise ValueError("task registry must contain a non-empty tasks object")
    return {
        str(name): _parse_task_spec(str(name), raw)
        for name, raw in raw_tasks.items()
        if isinstance(raw, Mapping)
    }


def _snake_case(value: str) -> str:
    normalized = value.replace("-", "_").replace(" ", "_")
    normalized = re.sub(r"(?<!^)(?=[A-Z])", "_", normalized).lower()
    return re.sub(r"_+", "_", normalized).strip("_")


TASK_SPECS = load_task_specs()
_TASK_ALIASES = {
    _snake_case(alias): name
    for name, spec in TASK_SPECS.items()
    for alias in (name, spec.class_name)
}


def get_task_spec(task_name: str) -> TaskSpec:

    if not isinstance(task_name, str) or not task_name.strip():
        raise ValueError("task_name must be a non-empty string")
    normalized = _snake_case(task_name)
    canonical = _TASK_ALIASES.get(normalized, normalized)
    try:
        return TASK_SPECS[canonical]
    except KeyError as exc:
        raise KeyError(
            f"unsupported RLBench DynaMAC task {task_name!r}; available tasks: {sorted(TASK_SPECS)}"
        ) from exc


def task_pose_chunks(
    task_name: str | TaskSpec,
    task_low_dim_state: Any,
    *,
    convention: str = "core_wxyz",
) -> dict[str, Array]:

    spec = task_name if isinstance(task_name, TaskSpec) else get_task_spec(task_name)
    return spec.extract_pose_chunks(task_low_dim_state, convention=convention)


split_task_pose_chunks = task_pose_chunks


__all__ = [
    "CANDIDATE_FRAME_POLICY",
    "CorePoseConvention",
    "RLBENCH_REFERENCE_COMMIT",
    "RECOVERABLE_RELATION_ROLES",
    "RLBenchPoseConvention",
    "SegmentationCoordination",
    "TASK_CONFIG_PATH",
    "TRAINING_MANIFEST_SCHEMA_STATIC_V1",
    "TASK_SPECS",
    "TaskConfigurationChunk",
    "TaskPoseChunk",
    "TaskSpec",
    "core_pose_wxyz_to_rlbench_xyzw",
    "get_task_spec",
    "load_task_specs",
    "rlbench_pose_xyzw_to_core_wxyz",
    "split_task_pose_chunks",
    "task_spec_identity",
    "task_pose_chunks",
    "unwrap_task_low_dim_state",
    "wxyz_to_xyzw",
    "xyzw_to_wxyz",
]
