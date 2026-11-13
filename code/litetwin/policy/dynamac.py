"""Self-contained implementation of DynaMAC."""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import re
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

import numpy as np

Array = np.ndarray
IDENTITY_POSE = np.asarray([0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0], dtype=np.float64)
MODEL_SCHEMA_VERSION = 13
TAPAS_REFERENCE_COMMIT = "52e35214b9baa7b190b87196c36b9e98f4006149"
QUATERNION_BATCH_GAUGE_PROTOCOL_ID = (
    "per-timestep-sign-invariant-markley-anchor-temporal-continuity-v2"
)
RIEPY_MANIFOLD_REGULARIZATION = 1.0e-6
SELECTION_SEMANTICS_ID = (
    "eq5_skill_majority_mask_before_eq6_time_state_position3d_unimodal_v1"
)
TIMESTEP_SELECTION_SEMANTICS_ID = (
    "eq5_timestep_availability_before_eq6_and_poe_time_state_position3d_unimodal_v1"
)
MAJORITY_GATED_TIMESTEP_SELECTION_SEMANTICS_ID = (
    "eq5_skill_majority_gate_timestep_availability_before_eq6_and_poe_"
    "time_state_position3d_unimodal_v1"
)
CovarianceEstimationMethod = Literal[
    "diagonal_empirical_ridge",
    "diagonal_empirical_spd_floor",
    "full_empirical_ridge",
]
CandidateKind = Literal["dynamic", "virtual"]
Eq6EmptySelection = Literal["error", "keep_argmax"]
Eq6CovarianceScope = Literal["full_pose", "eq5_weighted_subspace"]


def _selection_semantics_id(config: Any) -> str:

    if config.link_mask_scope == "skill_majority":
        return SELECTION_SEMANTICS_ID
    if config.link_mask_scope == "timestep":
        return TIMESTEP_SELECTION_SEMANTICS_ID
    if config.link_mask_scope == "skill_majority_gate_timestep":
        return MAJORITY_GATED_TIMESTEP_SELECTION_SEMANTICS_ID
    raise ValueError(f"Unknown link_mask_scope: {config.link_mask_scope}")


def _as_float_array(value: Array | Sequence[float]) -> Array:
    return np.asarray(value, dtype=np.float64)


def normalize_quaternion(quaternion: Array) -> Array:

    quaternion = _as_float_array(quaternion)
    norm = np.linalg.norm(quaternion, axis=-1, keepdims=True)
    if np.any(norm < 1.0e-12):
        raise ValueError("quaternion norm must be nonzero")
    return quaternion / norm


def ensure_quaternion_continuity(quaternions: Array) -> Array:

    result = normalize_quaternion(quaternions).copy()
    if result.ndim != 2 or result.shape[1] != 4:
        raise ValueError("quaternion sequence must have shape [T, 4]")
    for index in range(1, len(result)):
        if float(np.dot(result[index - 1], result[index])) < 0.0:
            result[index] *= -1.0
    return result


def _normalize_pose_trajectory(poses: Array) -> Array:
    poses = _as_float_array(poses).copy()
    if poses.ndim != 2 or poses.shape[1] != 7 or len(poses) == 0:
        raise ValueError("pose trajectory must be nonempty with shape [T, 7]")

    poses[:, 3:7] = ensure_quaternion_continuity(poses[:, 3:7])
    return poses


def _prepare_pose_batch(trajectories: Array) -> Array:

    values = _as_float_array(trajectories)
    if (
        values.ndim != 3
        or values.shape[-1] != 7
        or values.shape[0] == 0
        or values.shape[1] == 0
    ):
        raise ValueError("pose batch must be nonempty with shape [N, T, 7]")
    result = np.stack([_normalize_pose_trajectory(item) for item in values])
    previous_anchor: Array | None = None
    for time_index in range(result.shape[1]):
        quaternions = result[:, time_index, 3:7]
        accumulator = np.einsum("ni,nj->ij", quaternions, quaternions)
        _, eigenvectors = np.linalg.eigh(accumulator)
        anchor = eigenvectors[:, -1]

        reference = quaternions[0] if previous_anchor is None else previous_anchor
        if float(np.dot(anchor, reference)) < 0.0:
            anchor *= -1.0
        opposite = np.einsum("ni,i->n", quaternions, anchor) < 0.0
        result[opposite, time_index, 3:7] = -result[opposite, time_index, 3:7]
        previous_anchor = anchor
    return result


def quaternion_conjugate(quaternion: Array) -> Array:
    result = normalize_quaternion(quaternion).copy()
    result[..., 1:] *= -1.0
    return result


def quaternion_multiply(left: Array, right: Array) -> Array:

    left = _as_float_array(left)
    right = _as_float_array(right)
    lw, lx, ly, lz = np.moveaxis(left, -1, 0)
    rw, rx, ry, rz = np.moveaxis(right, -1, 0)
    return np.stack(
        (
            lw * rw - lx * rx - ly * ry - lz * rz,
            lw * rx + lx * rw + ly * rz - lz * ry,
            lw * ry - lx * rz + ly * rw + lz * rx,
            lw * rz + lx * ry - ly * rx + lz * rw,
        ),
        axis=-1,
    )


def quaternion_to_matrix(quaternion: Array) -> Array:
    quaternion = normalize_quaternion(quaternion)
    w, x, y, z = np.moveaxis(quaternion, -1, 0)
    return np.stack(
        (
            1.0 - 2.0 * (y * y + z * z),
            2.0 * (x * y - z * w),
            2.0 * (x * z + y * w),
            2.0 * (x * y + z * w),
            1.0 - 2.0 * (x * x + z * z),
            2.0 * (y * z - x * w),
            2.0 * (x * z - y * w),
            2.0 * (y * z + x * w),
            1.0 - 2.0 * (x * x + y * y),
        ),
        axis=-1,
    ).reshape(quaternion.shape[:-1] + (3, 3))


def rotate_vector(quaternion: Array, vector: Array) -> Array:
    return np.einsum("...ij,...j->...i", quaternion_to_matrix(quaternion), vector)


def quaternion_log(quaternion: Array) -> Array:

    quaternion = normalize_quaternion(quaternion)
    vector = quaternion[..., 1:]
    vector_norm = np.linalg.norm(vector, axis=-1)
    angle = np.arccos(np.clip(quaternion[..., 0], -1.0, 1.0))
    active = np.abs(quaternion[..., 0] - 1.0) > RIEPY_MANIFOLD_REGULARIZATION
    scale = np.divide(
        angle,
        vector_norm,
        out=np.zeros_like(angle),
        where=active & (vector_norm > np.finfo(np.float64).eps),
    )
    return vector * scale[..., None]


def quaternion_exp(rotation_vector: Array) -> Array:

    rotation_vector = _as_float_array(rotation_vector)
    angle = np.linalg.norm(rotation_vector, axis=-1)
    scale = np.divide(
        np.sin(angle),
        angle,
        out=np.ones_like(angle),
        where=angle > 1.0e-12,
    )
    return normalize_quaternion(
        np.concatenate(
            (np.cos(angle)[..., None], rotation_vector * scale[..., None]), axis=-1
        )
    )


def quaternion_mean(quaternions: Array) -> Array:

    quaternions = normalize_quaternion(quaternions)
    accumulator = np.einsum("ni,nj->ij", quaternions, quaternions)
    _, eigenvectors = np.linalg.eigh(accumulator)
    result = eigenvectors[:, -1]
    if result[0] < 0.0:
        result *= -1.0
    return normalize_quaternion(result)


def pose_compose(left: Array, right: Array) -> Array:

    left = _as_float_array(left)
    right = _as_float_array(right)
    position = left[..., :3] + rotate_vector(left[..., 3:7], right[..., :3])
    orientation = normalize_quaternion(
        quaternion_multiply(left[..., 3:7], right[..., 3:7])
    )
    return np.concatenate((position, orientation), axis=-1)


def pose_inverse(pose: Array) -> Array:
    pose = _as_float_array(pose)
    orientation = quaternion_conjugate(pose[..., 3:7])
    position = -rotate_vector(orientation, pose[..., :3])
    return np.concatenate((position, orientation), axis=-1)


def relative_pose(frame_pose: Array, world_pose: Array) -> Array:

    return pose_compose(pose_inverse(frame_pose), world_pose)


def pose_log_world(base: Array, point: Array) -> Array:

    base = _as_float_array(base)
    point = _as_float_array(point)
    rotation_tangent = quaternion_log(
        quaternion_multiply(quaternion_conjugate(base[3:7]), point[3:7])
    )
    return np.concatenate((point[:3] - base[:3], rotation_tangent))


def pose_log_nearest(base: Array, point: Array) -> Array:

    reference = _as_float_array(base)
    aligned = _as_float_array(point).copy()
    if reference.shape != (7,) or aligned.shape != (7,):
        raise ValueError("pose_log_nearest requires two [7] poses")
    if float(np.dot(reference[3:7], aligned[3:7])) < 0.0:
        aligned[3:7] *= -1.0
    return pose_log_world(reference, aligned)


def pose_exp_world(base: Array, tangent: Array) -> Array:

    base = _as_float_array(base)
    tangent = _as_float_array(tangent)
    orientation = quaternion_multiply(base[3:7], quaternion_exp(tangent[3:]))
    return np.concatenate((base[:3] + tangent[:3], normalize_quaternion(orientation)))


def _quaternion_left_matrix(quaternion: Array) -> Array:

    w, x, y, z = normalize_quaternion(quaternion)
    return np.asarray(
        [
            [w, -x, -y, -z],
            [x, w, -z, y],
            [y, z, w, -x],
            [z, -y, x, w],
        ],
        dtype=np.float64,
    )


def quaternion_parallel_transport(source: Array, target: Array) -> Array:

    source = normalize_quaternion(source)
    target = normalize_quaternion(target)
    inner = float(np.clip(np.dot(source, target), -1.0, 1.0))
    distance = math.acos(inner)
    if distance < RIEPY_MANIFOLD_REGULARIZATION:
        return np.eye(3, dtype=np.float64)
    if 1.0 + inner < np.finfo(np.float64).eps:
        raise ValueError(
            "parallel transport between antipodal S3 quaternions is undefined"
        )

    ambient = np.eye(4) - np.outer(source + target, target) / (1.0 + inner)
    source_basis = _quaternion_left_matrix(source)[:, 1:]
    target_basis = _quaternion_left_matrix(target)[:, 1:]
    result = target_basis.T @ ambient @ source_basis

    left, _, right = np.linalg.svd(result)
    result = left @ right

    if np.sign(np.trace(result)) == -1:
        result *= -1.0
    return result


def pose_parallel_transport(source: Array, target: Array) -> Array:

    result = np.eye(6, dtype=np.float64)
    result[3:, 3:] = quaternion_parallel_transport(source[3:7], target[3:7])
    return result


def interpolate_rows(values: Array, length: int) -> Array:
    values = _as_float_array(values)
    if length < 1:
        raise ValueError("resampling length must be positive")
    if len(values) == 1:
        return np.repeat(values, length, axis=0)
    source = np.linspace(0.0, 1.0, len(values))
    target = np.linspace(0.0, 1.0, length)
    flat = values.reshape(len(values), -1)
    result = np.stack(
        [np.interp(target, source, flat[:, index]) for index in range(flat.shape[1])],
        axis=-1,
    )
    return result.reshape((length,) + values.shape[1:])


def tapas_subsample_rows(values: Array, length: int) -> Array:

    values = _as_float_array(values)
    if length < 1 or len(values) < 1:
        raise ValueError("source and target trajectory lengths must be positive")

    if length == 1:
        samples = np.zeros(1, dtype=np.float32)
    else:
        end = np.float32(len(values) - 1)
        step = end / np.float32(length - 1)
        halfway = length // 2
        samples = np.empty(length, dtype=np.float32)
        samples[:halfway] = np.arange(halfway, dtype=np.float32) * step
        samples[halfway:] = (
            end
            - np.arange(
                length - halfway - 1,
                -1,
                -1,
                dtype=np.float32,
            )
            * step
        )
    indices = np.rint(samples).astype(np.int64)
    return values[indices].copy()


def tapas_subsample_poses(poses: Array, length: int) -> Array:
    poses = _normalize_pose_trajectory(poses)
    return tapas_subsample_rows(poses, length)


def _quaternion_slerp(left: Array, right: Array, fraction: float) -> Array:
    left = normalize_quaternion(left)
    right = normalize_quaternion(right)
    dot = float(np.dot(left, right))
    if dot < 0.0:
        right = -right
        dot = -dot
    dot = float(np.clip(dot, -1.0, 1.0))
    if dot > 0.9995:
        return normalize_quaternion(left + fraction * (right - left))
    angle = math.acos(dot)
    denominator = math.sin(angle)
    return normalize_quaternion(
        math.sin((1.0 - fraction) * angle) / denominator * left
        + math.sin(fraction * angle) / denominator * right
    )


def interpolate_poses(poses: Array, length: int) -> Array:
    poses = _normalize_pose_trajectory(poses)
    position = interpolate_rows(poses[:, :3], length)
    source = np.linspace(0.0, 1.0, len(poses))
    target = np.linspace(0.0, 1.0, length)
    quaternion = []
    for value in target:
        right_index = min(
            int(np.searchsorted(source, value, side="right")), len(poses) - 1
        )
        left_index = max(right_index - 1, 0)
        width = source[right_index] - source[left_index]
        fraction = 0.0 if width == 0.0 else float((value - source[left_index]) / width)
        quaternion.append(
            _quaternion_slerp(
                poses[left_index, 3:7],
                poses[right_index, 3:7],
                fraction,
            )
        )
    quaternion = np.stack(quaternion)
    return np.concatenate((position, quaternion), axis=-1)


def _pose_mean(poses: Array, weights: Array | None = None) -> Array:

    poses = _as_float_array(poses)
    if weights is None:
        weights = np.full(len(poses), 1.0 / len(poses), dtype=np.float64)
    else:
        weights = _as_float_array(weights)
        if (
            weights.shape != (len(poses),)
            or np.any(weights < 0.0)
            or np.sum(weights) <= 0.0
        ):
            raise ValueError(
                "Frechet mean weights must be a nonnegative vector with positive sum"
            )
        weights = weights / np.sum(weights)

    quaternions = normalize_quaternion(poses[:, 3:7])
    accumulator = np.einsum("n,ni,nj->ij", weights, quaternions, quaternions)
    _, eigenvectors = np.linalg.eigh(accumulator)
    initial_quaternion = eigenvectors[:, -1]

    reference = quaternions[int(np.argmax(weights))]
    if float(np.dot(initial_quaternion, reference)) < 0.0:
        initial_quaternion *= -1.0
    mean = np.concatenate(
        (np.sum(weights[:, None] * poses[:, :3], axis=0), initial_quaternion)
    )
    for _ in range(64):
        increment = np.sum(weights[:, None] * _pose_residuals(mean, poses), axis=0)
        mean = pose_exp_world(mean, increment)
        if np.linalg.norm(increment) < 1.0e-12:
            break
    return mean


def _pose_residuals(mean: Array, poses: Array) -> Array:
    return np.stack([pose_log_world(mean, pose) for pose in poses])


def _fit_pose_sequence(
    trajectories: Array,
    position_variance_floor: float,
    rotation_variance_floor: float,
    *,
    covariance_estimation_method: CovarianceEstimationMethod,
) -> tuple[Array, Array]:

    trajectories = _as_float_array(trajectories)
    if trajectories.ndim != 3 or trajectories.shape[-1] != 7:
        raise ValueError("trajectory batch must have shape [N, T, 7]")
    trajectories = _prepare_pose_batch(trajectories)
    means = np.zeros((trajectories.shape[1], 7), dtype=np.float64)
    covariance = np.zeros((trajectories.shape[1], 6, 6), dtype=np.float64)
    floor = np.asarray(
        [position_variance_floor] * 3 + [rotation_variance_floor] * 3,
        dtype=np.float64,
    )
    if not np.all(np.isfinite(floor)) or np.any(floor <= 0.0):
        raise ValueError("covariance floors must be finite and positive")
    if covariance_estimation_method not in {
        "diagonal_empirical_ridge",
        "diagonal_empirical_spd_floor",
        "full_empirical_ridge",
    }:
        raise ValueError("unknown covariance_estimation_method")
    for time_index in range(trajectories.shape[1]):
        means[time_index] = _pose_mean(trajectories[:, time_index])
        if (
            time_index
            and float(np.dot(means[time_index - 1, 3:7], means[time_index, 3:7])) < 0.0
        ):
            means[time_index, 3:7] *= -1.0
        residuals = _pose_residuals(means[time_index], trajectories[:, time_index])
        denominator = max(len(residuals) - 1, 1)
        empirical = residuals.T @ residuals / denominator
        if covariance_estimation_method == "diagonal_empirical_ridge":
            covariance[time_index] = np.diag(np.diag(empirical) + floor)
        elif covariance_estimation_method == "diagonal_empirical_spd_floor":
            covariance[time_index] = np.diag(np.maximum(np.diag(empirical), floor))
        else:
            covariance[time_index] = empirical + np.diag(floor)
    return means, covariance


def geometric_mean_standard_deviation(
    covariance: Array,
    *,
    position_weight: float = 1.0,
    rotation_weight: float = 0.0,
) -> Array:

    weighted = _weighted_pose_covariance(
        covariance,
        position_weight=position_weight,
        rotation_weight=rotation_weight,
    )
    dimension = weighted.shape[-1]
    sign, log_determinant = np.linalg.slogdet(weighted)
    if np.any(sign <= 0.0):
        raise ValueError("covariance must be positive definite")
    return np.exp(log_determinant / (2.0 * dimension))


def _weighted_pose_covariance(
    covariance: Array,
    *,
    position_weight: float,
    rotation_weight: float,
) -> Array:

    covariance = _as_float_array(covariance)
    if covariance.shape[-2:] != (6, 6):
        raise ValueError("pose statistics require a 6x6 tangent covariance")
    weights = np.asarray(
        [position_weight] * 3 + [rotation_weight] * 3,
        dtype=np.float64,
    )
    if not np.all(np.isfinite(weights)) or np.any(weights < 0.0):
        raise ValueError(
            "Eq. (5) position and rotation weights must be finite and nonnegative"
        )
    active = weights > 0.0
    if not np.any(active):
        raise ValueError(
            "Eq. (5) requires at least one positive-weight tangent dimension"
        )
    selected = covariance[..., active, :][..., :, active]
    active_weights = weights[active]
    return selected * (active_weights[:, None] * active_weights[None, :])


def task_parameter_scores(
    covariances: dict[str, Array],
    *,
    availability: dict[str, Array],
    candidate_kind: dict[str, CandidateKind],
) -> dict[str, float]:

    return task_parameter_score_details(
        covariances,
        availability=availability,
        candidate_kind=candidate_kind,
    )["scores"]


def task_parameter_score_details(
    covariances: dict[str, Array],
    *,
    availability: dict[str, Array],
    candidate_kind: dict[str, CandidateKind],
) -> dict[str, Any]:

    if not covariances:
        raise ValueError("At least one candidate task parameter is required")
    if availability is None:
        raise ValueError("Eq. (5) availability must be provided explicitly")
    if candidate_kind is None:
        raise ValueError("candidate_kind must be provided explicitly")
    names = list(covariances)
    values = np.stack([_as_float_array(covariances[name]) for name in names])
    if (
        values.ndim != 4
        or values.shape[-1] == 0
        or values.shape[-2] != values.shape[-1]
    ):
        raise ValueError("Candidate covariances must have square shape [F, T, D, D]")
    if values.shape[1] == 0:
        raise ValueError("The candidate covariance time axis cannot be empty")
    if not np.all(np.isfinite(values)):
        raise ValueError("Candidate covariances must contain only finite values")
    signs, log_determinants = np.linalg.slogdet(values)
    if np.any(signs <= 0.0) or not np.all(np.isfinite(log_determinants)):
        raise ValueError("Covariances must be positive definite")

    duration = values.shape[1]
    if set(candidate_kind) != set(names):
        raise ValueError(
            "candidate_kind and candidate covariances must have identical keys"
        )
    invalid_kinds = {
        name: kind
        for name, kind in candidate_kind.items()
        if kind not in {"dynamic", "virtual"}
    }
    if invalid_kinds:
        raise ValueError(
            f"candidate_kind accepts only dynamic or virtual; received {invalid_kinds}"
        )
    candidate_kinds: dict[str, CandidateKind] = {
        name: candidate_kind[name] for name in names
    }

    if set(availability) != set(names):
        raise ValueError(
            "Eq. (5) availability and candidate covariances must have identical keys"
        )
    availability_rows = []
    for name in names:
        row = np.asarray(availability[name])
        if row.dtype.kind != "b" or row.shape != (duration,):
            raise ValueError(
                "Eq. (5) availability must be a Boolean [T] mask matching the time axis"
            )
        availability_rows.append(row)
    availability_values = np.stack(availability_rows)

    invalid_virtual_frames = [
        name
        for index, name in enumerate(names)
        if candidate_kinds[name] == "virtual" and not np.all(availability_values[index])
    ]
    if invalid_virtual_frames:
        raise ValueError(
            "virtual task-parameter frames must be available at every time step: "
            f"{invalid_virtual_frames}"
        )

    available_candidate_count = np.sum(availability_values, axis=0, dtype=np.int64)
    if np.any(available_candidate_count == 0):
        missing_times = [
            int(index) for index in np.flatnonzero(available_candidate_count == 0)
        ]
        raise RuntimeError(
            f"Eq. (6) time steps {missing_times} have no candidates available under "
            "Eq. (5); an empty-denominator fallback is undefined"
        )

    log_precision = -log_determinants
    available_log_precision = np.where(availability_values, log_precision, -np.inf)
    maximum = np.max(available_log_precision, axis=0, keepdims=True)
    shifted = np.full_like(log_precision, -np.inf)
    np.subtract(
        log_precision,
        maximum,
        out=shifted,
        where=availability_values,
    )
    relative = np.zeros_like(log_precision)
    np.exp(shifted, out=relative, where=availability_values)
    stabilized_denominator = np.sum(relative, axis=0, keepdims=True)
    log_precision_denominator = maximum[0] + np.log(stabilized_denominator[0])
    relative /= stabilized_denominator
    normalization_residual = np.sum(relative, axis=0) - 1.0
    normalization_valid_mask = (
        np.isfinite(log_precision_denominator)
        & np.isfinite(normalization_residual)
        & (np.abs(normalization_residual) <= 1.0e-12)
    )
    if not np.all(normalization_valid_mask):
        invalid_times = [
            int(index) for index in np.flatnonzero(~normalization_valid_mask)
        ]
        raise RuntimeError(
            f"Eq. (6) normalization is invalid at timesteps {invalid_times}"
        )
    ever_available = np.any(availability_values, axis=1)
    scores = {
        name: (
            float(np.max(relative[index, availability_values[index]]))
            if ever_available[index]
            else 0.0
        )
        for index, name in enumerate(names)
    }
    return {
        "semantics_id": SELECTION_SEMANTICS_ID,
        "precision_dimension": int(values.shape[-1]),
        "frame_names": tuple(names),
        "candidate_kind": candidate_kinds,
        "candidate_kind_source": "explicit",
        "logdet_covariance": log_determinants.copy(),
        "logdet_precision": log_precision.copy(),
        "availability": {
            name: availability_values[index].copy() for index, name in enumerate(names)
        },
        "availability_source": "explicit_eq5",
        "available_candidate_count": available_candidate_count.copy(),
        "log_precision_denominator": log_precision_denominator.copy(),
        "relative_precision": relative.copy(),
        "normalization_residual": normalization_residual.copy(),
        "normalization_valid_mask": normalization_valid_mask.copy(),
        "scores": scores,
        "normalization_scope": "eq5_available_candidate_frames_per_timestep",
        "eq5_filters_eq6_candidates": True,
        "eq5_filters_eq6_denominator": True,
        "unavailable_relative_precision_is_zero": True,
        "argmax_time_restricted_to_available": True,
        "virtual_frames_always_available": True,
        "rejection_reason": {
            name: (None if ever_available[index] else "eq5_never_available")
            for index, name in enumerate(names)
        },
        "argmax_time": {
            name: (
                int(
                    np.flatnonzero(availability_values[index])[
                        np.argmax(relative[index, availability_values[index]])
                    ]
                )
                if ever_available[index]
                else None
            )
            for index, name in enumerate(names)
        },
    }


def static_task_parameter_score_details(
    covariances: dict[str, Array],
) -> dict[str, Any]:

    if not covariances:
        raise ValueError("At least one candidate task parameter is required")
    durations = {
        name: _as_float_array(covariance).shape[0]
        for name, covariance in covariances.items()
    }
    if len(set(durations.values())) != 1:
        raise ValueError("Static candidate covariances must share the same time length")
    duration = next(iter(durations.values()))
    details = task_parameter_score_details(
        covariances,
        availability={name: np.ones(duration, dtype=bool) for name in covariances},
        candidate_kind={name: "dynamic" for name in covariances},
    )
    details["availability_source"] = "implicit_all_candidates_static_default"
    details["candidate_kind_source"] = "implicit_all_dynamic_static_default"
    details["static_all_candidates_default"] = True
    return details


def static_task_parameter_scores(covariances: dict[str, Array]) -> dict[str, float]:

    return static_task_parameter_score_details(covariances)["scores"]


def _eq6_skill_selection(
    covariances: dict[str, Array],
    tau_omega: float,
    *,
    availability: dict[str, Array],
    candidate_kind: dict[str, CandidateKind],
    empty_selection: Eq6EmptySelection = "error",
    semantics_id: str = SELECTION_SEMANTICS_ID,
) -> tuple[tuple[str, ...], dict[str, bool], dict[str, Any]]:

    if not 0.0 <= tau_omega < 1.0:
        raise ValueError("tau_omega must be in [0, 1)")
    if empty_selection not in {"error", "keep_argmax"}:
        raise ValueError("Unknown Eq. (6) empty-selection policy")
    details = task_parameter_score_details(
        covariances,
        availability=availability,
        candidate_kind=candidate_kind,
    )
    details["semantics_id"] = semantics_id
    scores = details["scores"]
    selected_by_threshold = {
        name: bool(scores[name] > tau_omega) for name in details["frame_names"]
    }
    selected_by_eq6 = selected_by_threshold.copy()
    fallback_selected_frames: tuple[str, ...] = ()
    if not any(selected_by_threshold.values()) and empty_selection == "keep_argmax":
        eligible = tuple(
            name
            for name in details["frame_names"]
            if details["rejection_reason"][name] != "eq5_never_available"
        )
        if not eligible:
            raise RuntimeError("Eq. (6) has no candidate frame that was ever available")
        maximum_score = max(scores[name] for name in eligible)
        fallback_selected_frames = tuple(
            name
            for name in eligible
            if math.isclose(
                scores[name],
                maximum_score,
                rel_tol=1.0e-12,
                abs_tol=1.0e-15,
            )
        )
        for name in fallback_selected_frames:
            selected_by_eq6[name] = True
    selected_frames = tuple(
        name for name in details["frame_names"] if selected_by_eq6[name]
    )
    details["tau_omega"] = float(tau_omega)
    details["selected_by_threshold"] = selected_by_threshold.copy()
    details["selected_by_eq6"] = selected_by_eq6.copy()
    details["empty_selection_policy"] = empty_selection
    details["empty_selection_policy_source_status"] = (
        "REFERENCE_EQ6_STRICT_THRESHOLD_FAIL_CLOSED"
        if empty_selection == "error"
        else "LOCAL_INFERENCE"
    )
    details["empty_selection_fallback_applied"] = bool(fallback_selected_frames)
    details["fallback_selected_frames"] = fallback_selected_frames
    details["fallback_reason"] = (
        "no_frame_score_strictly_above_tau_omega_keep_argmax"
        if fallback_selected_frames
        else None
    )
    details["rejection_reason"] = {
        name: (
            details["rejection_reason"][name]
            if details["rejection_reason"][name] is not None
            else None if selected_by_eq6[name] else "eq6_score_not_above_tau_omega"
        )
        for name in details["frame_names"]
    }
    return selected_frames, selected_by_eq6, details


def _compose_framewise_poe_participation(
    eq5_availability: dict[str, Array],
    eq6_selected: dict[str, Array],
) -> dict[str, Array]:

    if not eq5_availability or set(eq5_availability) != set(eq6_selected):
        raise ValueError(
            "Eq. (5) availability and Eq. (6) selection must have identical nonempty keys"
        )
    result: dict[str, Array] = {}
    common_shape: tuple[int, int] | None = None
    for name in eq5_availability:
        available = np.asarray(eq5_availability[name], dtype=bool)
        selected = np.asarray(eq6_selected[name], dtype=bool)
        if available.ndim != 2:
            raise ValueError("Eq. (5) availability must have shape [mode, T]")
        if selected.shape != (available.shape[0],):
            raise ValueError("Eq. (6) selection must have shape [mode]")
        if common_shape is None:
            common_shape = available.shape
        elif available.shape != common_shape:
            raise ValueError("All candidate frames must share the same [mode, T] shape")
        result[name] = available & selected[:, None]
    return result


@dataclass(frozen=True)
class GaussianMarginal:

    frame: str
    mean: Array
    covariance: Array


def transform_marginal(
    frame_name: str,
    frame_pose: Array,
    local_mean: Array,
    local_covariance: Array,
    *,
    diagonalize: bool = False,
) -> GaussianMarginal:

    frame_pose = _as_float_array(frame_pose)
    mean = pose_compose(frame_pose, local_mean)
    rotation = quaternion_to_matrix(frame_pose[3:7])
    tangent_action = np.eye(6, dtype=np.float64)
    tangent_action[:3, :3] = rotation

    transport = pose_parallel_transport(frame_pose, mean)
    transform = transport @ tangent_action
    covariance = transform @ local_covariance @ transform.T
    if diagonalize:
        covariance = np.diag(np.diag(covariance))
    return GaussianMarginal(frame_name, mean, covariance)


def product_of_experts(
    marginals: Sequence[GaussianMarginal],
    maximum_iterations: int = 50,
    tolerance: float = 1.0e-5,
    precision_weights: Sequence[float] | None = None,
) -> tuple[Array, Array, dict[str, float]]:

    if not marginals:
        raise ValueError("PoE requires at least one marginal")
    if precision_weights is not None:
        weights_array = np.asarray(precision_weights, dtype=np.float64)
        if weights_array.shape != (len(marginals),):
            raise ValueError("PoE precision weights must match the number of marginals")
        if np.any(~np.isfinite(weights_array)) or np.any(weights_array < 0.0):
            raise ValueError("PoE precision weights must be finite and nonnegative")
        retained = [
            (marginal, float(weight))
            for marginal, weight in zip(marginals, weights_array, strict=True)
            if weight > 0.0
        ]
        if not retained:
            raise ValueError(
                "PoE requires at least one marginal with positive precision weight"
            )

        if any(weight != 1.0 for _, weight in retained):
            marginals = tuple(
                GaussianMarginal(
                    marginal.frame,
                    marginal.mean,
                    marginal.covariance / weight,
                )
                for marginal, weight in retained
            )
        else:
            marginals = tuple(marginal for marginal, _ in retained)
    reference_quaternion = marginals[0].mean[3:7]
    aligned_marginals = []
    for marginal in marginals:
        mean = marginal.mean.copy()
        if float(np.dot(reference_quaternion, mean[3:7])) < 0.0:

            mean[3:7] *= -1.0
        aligned_marginals.append(
            GaussianMarginal(marginal.frame, mean, marginal.covariance)
        )
    marginals = tuple(aligned_marginals)

    def robust_inverse(matrix: Array) -> Array:
        try:
            return np.linalg.inv(matrix)
        except np.linalg.LinAlgError:
            return np.linalg.inv(matrix + np.eye(len(matrix)) * 1.0e-8)

    def multiply_pair(
        left: GaussianMarginal, right: GaussianMarginal
    ) -> GaussianMarginal:
        left_precision = robust_inverse(left.covariance)
        right_precision = robust_inverse(right.covariance)
        mean = left.mean.copy()
        covariance = left.covariance.copy()

        for _ in range(maximum_iterations + 1):
            left_transport = pose_parallel_transport(left.mean, mean)
            right_transport = pose_parallel_transport(right.mean, mean)
            transported_left = left_transport @ left_precision @ left_transport.T
            transported_right = right_transport @ right_precision @ right_transport.T
            covariance = robust_inverse(transported_left + transported_right)
            information = transported_left @ pose_log_world(
                mean, left.mean
            ) + transported_right @ pose_log_world(mean, right.mean)
            increment = covariance @ information
            mean = pose_exp_world(mean, increment)
            if float(increment @ increment) <= tolerance:
                break

        return GaussianMarginal(f"{left.frame}*{right.frame}", mean, covariance)

    joint = marginals[0]
    for marginal in marginals[1:]:
        joint = multiply_pair(joint, marginal)

    log_determinant_scores = np.asarray(
        [-np.linalg.slogdet(item.covariance)[1] for item in marginals], dtype=np.float64
    )
    shifted = log_determinant_scores - np.max(log_determinant_scores)
    determinant_scores = np.exp(shifted)
    weights = determinant_scores / np.sum(determinant_scores)
    return (
        joint.mean.copy(),
        joint.covariance.copy(),
        {
            item.frame: float(weight)
            for item, weight in zip(marginals, weights, strict=True)
        },
    )


@dataclass(frozen=True)
class DynaMACDemonstration:

    ee_pose: Array
    action_pose: Array
    gripper: Array
    frames: dict[str, Array]
    skill: Array
    name: str = "demonstration"
    entity_configurations: dict[str, dict[str, Array]] = field(default_factory=dict)
    scene_entity_poses: dict[str, Array] = field(default_factory=dict)
    structural_bindings: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        ee_pose = _normalize_pose_trajectory(self.ee_pose)
        action_pose = _normalize_pose_trajectory(self.action_pose)
        gripper = _as_float_array(self.gripper)
        raw_skill = _as_float_array(self.skill)
        if raw_skill.ndim != 1 or not np.all(np.isfinite(raw_skill)):
            raise ValueError(
                f"{self.name} skill labels must be a finite one-dimensional integer array"
            )
        if np.any(raw_skill != np.rint(raw_skill)):
            raise ValueError(
                f"{self.name} skill labels cannot contain fractional values"
            )
        int64 = np.iinfo(np.int64)
        if np.any(raw_skill < int64.min) or np.any(raw_skill > int64.max):
            raise ValueError(f"{self.name} skill labels exceed the int64 range")
        skill = raw_skill.astype(np.int64)
        if any(not isinstance(name, str) or not name for name in self.frames):
            raise ValueError(
                f"{self.name} task-parameter names must be nonempty strings"
            )
        frames = {
            name: _normalize_pose_trajectory(value)
            for name, value in self.frames.items()
        }
        scene_entity_poses = {
            name: _normalize_pose_trajectory(value)
            for name, value in self.scene_entity_poses.items()
        }
        steps = len(ee_pose)
        if ee_pose.shape != (steps, 7) or action_pose.shape != (steps, 7):
            raise ValueError(
                f"{self.name} end-effector and action poses must have shape [T, 7]"
            )
        if gripper.ndim == 1:
            gripper = gripper[:, None]
        if (
            gripper.ndim != 2
            or gripper.shape[1] == 0
            or gripper.shape[0] != steps
            or skill.shape != (steps,)
        ):
            raise ValueError(f"{self.name} arrays have inconsistent lengths")
        if not frames or any(value.shape != (steps, 7) for value in frames.values()):
            raise ValueError(
                f"{self.name} task parameters must be a nonempty mapping of [T, 7] poses"
            )
        if any(value.shape != (steps, 7) for value in scene_entity_poses.values()):
            raise ValueError(f"{self.name} scene-entity poses must have shape [T, 7]")
        overlap = set(frames).intersection(scene_entity_poses)
        if overlap:
            raise ValueError(
                f"Scene-entity poses cannot duplicate task parameters: {sorted(overlap)}"
            )
        if any(name.startswith("virtual_skill_") for name in frames):
            raise ValueError(
                "Physical task-parameter names cannot use the reserved virtual_skill_ prefix"
            )
        entity_configurations: dict[str, dict[str, Array]] = {}
        known_entities = set(frames).union(scene_entity_poses)
        for entity, raw_fields in self.entity_configurations.items():
            if entity not in known_entities:
                raise ValueError(
                    f"Entity configuration references unknown entity {entity}"
                )
            if not isinstance(raw_fields, dict) or not raw_fields:
                raise ValueError(
                    f"Entity {entity} must define at least one internal configuration field"
                )
            fields: dict[str, Array] = {}
            for field_name, raw_values in raw_fields.items():
                if not isinstance(field_name, str) or not field_name:
                    raise ValueError(
                        "Entity configuration field names must be nonempty strings"
                    )
                values = _as_float_array(raw_values)
                if values.ndim == 1:
                    values = values[:, None]
                if (
                    values.ndim != 2
                    or values.shape[0] != steps
                    or values.shape[1] == 0
                    or not np.all(np.isfinite(values))
                ):
                    raise ValueError(
                        f"Configuration field {field_name} for entity {entity} must be a finite [T, D] array"
                    )
                fields[field_name] = values.copy()
            entity_configurations[entity] = fields
        structural_bindings = dict(self.structural_bindings)
        for child, parent in structural_bindings.items():
            if (
                not isinstance(child, str)
                or not child
                or not isinstance(parent, str)
                or not parent
                or child == parent
            ):
                raise ValueError(
                    "A direct structural binding must connect two distinct nonempty entities"
                )
            if child not in known_entities or parent not in known_entities:
                raise ValueError(
                    f"Direct structural binding references an unknown entity: {child}->{parent}"
                )
        sequence = _compressed_skill_sequence(skill)
        if len(sequence) != len(set(sequence)):
            raise ValueError(
                f"A skill in {self.name} cannot be split across disjoint intervals"
            )
        for poses in [ee_pose, action_pose, *frames.values()]:
            if not np.all(np.isfinite(poses)):
                raise ValueError(f"{self.name} contains nonfinite poses")
        if not np.all(np.isfinite(gripper)):
            raise ValueError(f"{self.name} contains nonfinite gripper actions")
        object.__setattr__(self, "ee_pose", ee_pose)
        object.__setattr__(self, "action_pose", action_pose)
        object.__setattr__(self, "gripper", gripper)
        object.__setattr__(self, "skill", skill)
        object.__setattr__(self, "frames", frames)
        object.__setattr__(self, "entity_configurations", entity_configurations)
        object.__setattr__(self, "scene_entity_poses", scene_entity_poses)
        object.__setattr__(self, "structural_bindings", structural_bindings)


@dataclass(frozen=True)
class DynaMACObservation:
    ee_pose: Array
    frames: dict[str, Array]

    def __post_init__(self) -> None:
        ee_pose = _as_float_array(self.ee_pose)
        frames = {name: _as_float_array(value) for name, value in self.frames.items()}
        if ee_pose.shape != (7,) or any(
            value.shape != (7,) for value in frames.values()
        ):
            raise ValueError("Observation poses must have shape [7]")
        if not np.all(np.isfinite(ee_pose)) or any(
            not np.all(np.isfinite(value)) for value in frames.values()
        ):
            raise ValueError("Observation contains nonfinite poses")
        ee_pose = ee_pose.copy()
        ee_pose[3:7] = normalize_quaternion(ee_pose[3:7])
        frames = {name: value.copy() for name, value in frames.items()}
        for value in frames.values():
            value[3:7] = normalize_quaternion(value[3:7])
        object.__setattr__(self, "ee_pose", ee_pose)
        object.__setattr__(self, "frames", frames)


@dataclass(frozen=True)
class DynaMACAction:
    pose: Array
    covariance: Array
    gripper: Array
    diagnostics: dict[str, Any]


@dataclass(frozen=True)
class DynaMACGripperLookahead:

    gripper: Array
    crosses_skill_boundary: bool
    repeats_terminal: bool
    next_skill_index: int
    next_skill_label: int
    next_time_index: int
    next_mode: int

    def __post_init__(self) -> None:
        gripper = _as_float_array(self.gripper)
        if gripper.ndim != 1 or len(gripper) == 0:
            raise ValueError("gripper lookahead command must be a non-empty vector")
        if not np.all(np.isfinite(gripper)):
            raise ValueError("gripper lookahead command must be finite")
        object.__setattr__(self, "gripper", gripper.copy())
        for name in (
            "crosses_skill_boundary",
            "repeats_terminal",
        ):
            if not isinstance(getattr(self, name), bool):
                raise TypeError(f"{name} must be a bool")
        for name in (
            "next_skill_index",
            "next_skill_label",
            "next_time_index",
            "next_mode",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
                raise TypeError(f"{name} must be an integer")
            if name != "next_skill_label" and value < 0:
                raise ValueError(f"{name} must be non-negative")
            object.__setattr__(self, name, int(value))


@dataclass(frozen=True)
class DynaMACConfig:

    tau_m: float = 0.005
    tau_omega: float = 0.5

    eq6_empty_selection: Eq6EmptySelection = "error"

    eq6_covariance_scope: Eq6CovarianceScope = "full_pose"

    policy_model: Literal["time_state", "action_pose"] = "time_state"

    kinematic_analysis_enabled: bool = True

    eq5_position_weight: float = 1.0
    eq5_rotation_weight: float = 0.0

    link_mask_scope: Literal[
        "skill_majority",
        "timestep",
        "skill_majority_gate_timestep",
    ] = "skill_majority"
    link_filter: Literal["none", "temporal_variance"] = "none"
    temporal_variance_window: int = 5
    temporal_variance_threshold: float = 1.0e-4
    position_variance_floor: float = 1.0e-6
    rotation_variance_floor: float = 1.0e-6

    covariance_estimation_method: CovarianceEstimationMethod = (
        "diagonal_empirical_ridge"
    )
    diagonalize_transformed_covariance: bool = True
    resampling_method: Literal["tapas_subsample", "interpolate"] = "tapas_subsample"

    modal_partition_method: Literal[
        "none",
        "riemannian_kmeans_bic",
        "riemannian_gmm_bic",
        "dbscan",
    ] = "none"
    preliminary_analysis: Literal[
        "pooled",
        "precluster_all_real_frame_product_mode_conditioned",
    ] = "pooled"
    maximum_modes: int = 8
    minimum_mode_size: int = 2
    clustering_length: int = 20

    gripper_clustering_scale: float = 1.0

    clustering_variance_floor: float = 1.0e-6
    clustering_restarts: int = 64
    dbscan_epsilon: float = 0.1
    dbscan_min_samples: int = 2
    gmm_maximum_iterations: int = 100
    default_mode_strategy: Literal["map", "sample"] = "sample"
    random_seed: int = 2608

    @property
    def relation_position_weight(self) -> float:

        return self.eq5_position_weight

    @property
    def relation_rotation_weight(self) -> float:

        return self.eq5_rotation_weight

    def __post_init__(self) -> None:
        floating_names = (
            "tau_m",
            "tau_omega",
            "eq5_position_weight",
            "eq5_rotation_weight",
            "temporal_variance_threshold",
            "position_variance_floor",
            "rotation_variance_floor",
            "gripper_clustering_scale",
            "clustering_variance_floor",
            "dbscan_epsilon",
        )
        floating = tuple(getattr(self, name) for name in floating_names)
        if any(
            isinstance(value, (bool, np.bool_))
            or not isinstance(value, (int, float, np.integer, np.floating))
            for value in floating
        ):
            raise ValueError(
                "DynaMAC floating-point configuration values must be real numbers"
            )
        if not all(math.isfinite(float(value)) for value in floating):
            raise ValueError(
                "DynaMAC floating-point configuration values must be finite"
            )
        integer_names = (
            "temporal_variance_window",
            "maximum_modes",
            "minimum_mode_size",
            "clustering_length",
            "clustering_restarts",
            "dbscan_min_samples",
            "gmm_maximum_iterations",
            "random_seed",
        )
        integer = tuple(getattr(self, name) for name in integer_names)
        if any(
            isinstance(value, (bool, np.bool_))
            or not isinstance(value, (int, np.integer))
            for value in integer
        ):
            raise ValueError(
                "DynaMAC integer configuration values cannot be Boolean or fractional"
            )
        if self.random_seed < 0:
            raise ValueError("random_seed must be a nonnegative integer")
        if not isinstance(self.diagonalize_transformed_covariance, (bool, np.bool_)):
            raise ValueError(
                "The transformed-covariance diagonalization flag must be Boolean"
            )
        if not isinstance(self.kinematic_analysis_enabled, (bool, np.bool_)):
            raise ValueError("The kinematic-link analysis flag must be Boolean")
        for name, value in zip(floating_names, floating, strict=True):
            object.__setattr__(self, name, float(value))
        for name, value in zip(integer_names, integer, strict=True):
            object.__setattr__(self, name, int(value))
        object.__setattr__(
            self,
            "diagonalize_transformed_covariance",
            bool(self.diagonalize_transformed_covariance),
        )
        object.__setattr__(
            self,
            "kinematic_analysis_enabled",
            bool(self.kinematic_analysis_enabled),
        )
        if not 0.0 < self.tau_m or not 0.0 <= self.tau_omega < 1.0:
            raise ValueError("Invalid tau_m or tau_omega")
        if self.policy_model not in {"time_state", "action_pose"}:
            raise ValueError("Unknown policy_model")
        if self.eq6_empty_selection not in {"error", "keep_argmax"}:
            raise ValueError("Unknown eq6_empty_selection")
        if self.eq6_covariance_scope not in {
            "full_pose",
            "eq5_weighted_subspace",
        }:
            raise ValueError("Unknown eq6_covariance_scope")
        if (
            self.eq5_position_weight < 0.0
            or self.eq5_rotation_weight < 0.0
            or self.eq5_position_weight + self.eq5_rotation_weight <= 0.0
        ):
            raise ValueError(
                "Eq. (5) position and rotation weights must be nonnegative, with at least one positive"
            )
        if self.link_mask_scope not in {
            "skill_majority",
            "timestep",
            "skill_majority_gate_timestep",
        }:
            raise ValueError("Unknown link_mask_scope")
        if self.link_filter not in {"none", "temporal_variance"}:
            raise ValueError("Unknown link_filter")
        if (
            self.link_mask_scope == "skill_majority_gate_timestep"
            and self.link_filter != "none"
        ):
            raise ValueError(
                "skill_majority_gate_timestep must gate the raw Eq. (5) mask directly"
            )
        if self.maximum_modes < 1:
            raise ValueError("The number of modes must be positive")
        if self.temporal_variance_window < 1 or self.temporal_variance_threshold < 0.0:
            raise ValueError("Invalid temporal-variance parameters")
        if self.position_variance_floor <= 0.0 or self.rotation_variance_floor <= 0.0:
            raise ValueError("The covariance floor must be positive")
        if self.covariance_estimation_method not in {
            "diagonal_empirical_ridge",
            "diagonal_empirical_spd_floor",
            "full_empirical_ridge",
        }:
            raise ValueError("Unknown covariance_estimation_method")
        if self.minimum_mode_size < 1 or self.clustering_length < 2:
            raise ValueError("Invalid minimum mode sample count or clustering length")
        if self.gripper_clustering_scale <= 0.0:
            raise ValueError("The gripper clustering scale must be positive")
        if self.clustering_variance_floor <= 0.0 or self.clustering_restarts < 1:
            raise ValueError(
                "Clustering regularization and restart count must be positive"
            )
        if self.dbscan_epsilon <= 0.0 or self.dbscan_min_samples < 1:
            raise ValueError("Invalid DBSCAN parameters")
        if self.gmm_maximum_iterations < 1:
            raise ValueError("The GMM iteration count must be positive")
        if self.resampling_method not in {"tapas_subsample", "interpolate"}:
            raise ValueError("Unknown resampling_method")
        if self.modal_partition_method not in {
            "none",
            "riemannian_kmeans_bic",
            "riemannian_gmm_bic",
            "dbscan",
        }:
            raise ValueError("Unknown modal_partition_method")
        if self.preliminary_analysis not in {
            "pooled",
            "precluster_all_real_frame_product_mode_conditioned",
        }:
            raise ValueError("Unknown preliminary_analysis")
        if self.default_mode_strategy not in {"map", "sample"}:
            raise ValueError("Unknown default_mode_strategy")


@dataclass
class StreamModel:
    frame: str
    mean: Array
    covariance: Array

    active: Array | None = None
    availability: Array | None = None
    selected_by_eq6: Array | None = None

    def __post_init__(self) -> None:
        self.mean = _as_float_array(self.mean)
        self.covariance = _as_float_array(self.covariance)
        if self.mean.ndim != 3 or self.mean.shape[-1] != 7:
            raise ValueError("Stream means must have shape [M, T, 7]")
        if self.covariance.shape != self.mean.shape[:2] + (6, 6):
            raise ValueError("Stream covariances must have shape [M, T, 6, 6]")
        if self.active is None:
            self.active = np.ones(self.mean.shape[1], dtype=bool)
        else:
            self.active = np.asarray(self.active, dtype=bool)
        valid_shapes = {
            (self.mean.shape[1],),
            (self.mean.shape[0], self.mean.shape[1]),
        }
        if self.active.shape not in valid_shapes:
            raise ValueError("The stream active mask must have shape [T] or [M, T]")
        if self.availability is None:

            self.availability = self.active.copy()
        else:
            self.availability = np.asarray(self.availability, dtype=bool)
        if self.availability.shape not in valid_shapes:
            raise ValueError("Stream availability must have shape [T] or [M, T]")
        if self.selected_by_eq6 is None:
            self.selected_by_eq6 = np.ones(self.mean.shape[0], dtype=bool)
        else:
            self.selected_by_eq6 = np.asarray(self.selected_by_eq6, dtype=bool)
        if self.selected_by_eq6.shape != (self.mean.shape[0],):
            raise ValueError("Stream selected_by_eq6 must have shape [M]")

        active_by_mode = (
            np.repeat(self.active[None, :], self.mean.shape[0], axis=0)
            if self.active.ndim == 1
            else self.active
        )
        availability_by_mode = (
            np.repeat(self.availability[None, :], self.mean.shape[0], axis=0)
            if self.availability.ndim == 1
            else self.availability
        )
        expected_active = availability_by_mode & self.selected_by_eq6[:, None]
        if not np.array_equal(active_by_mode, expected_active):
            raise ValueError(
                "Stream active must equal availability AND selected_by_eq6 exactly"
            )

    def is_active(self, mode: int, time_index: int) -> bool:
        if self.active.ndim == 1:
            return bool(self.active[time_index])
        return bool(self.active[mode, time_index])

    def is_available(self, mode: int, time_index: int) -> bool:
        if self.availability.ndim == 1:
            return bool(self.availability[time_index])
        return bool(self.availability[mode, time_index])

    def is_selected(self, mode: int) -> bool:
        return bool(self.selected_by_eq6[mode])


@dataclass
class SkillModel:
    label: int
    duration: int
    selected_frames: tuple[str, ...]
    mode_priors: Array
    streams: dict[str, StreamModel]
    gripper: Array
    transition_from_previous: Array | None = None
    mode_demonstration_indices: tuple[tuple[int, ...], ...] = ()
    link_diagnostics: dict[str, dict[str, Any]] = field(default_factory=dict)
    selection_scores: dict[str, float] = field(default_factory=dict)


def _compressed_skill_sequence(skill: Array) -> list[int]:
    if len(skill) == 0:
        return []
    result = [int(skill[0])]
    for value in skill[1:]:
        value = int(value)
        if value != result[-1]:
            result.append(value)
    return result


def _maximum_true_run(mask: Array) -> int:
    maximum = current = 0
    for value in np.asarray(mask, dtype=bool):
        current = current + 1 if value else 0
        maximum = max(maximum, current)
    return maximum


def _temporal_variance_curve(
    local_mean: Array,
    window: int,
) -> tuple[Array, Array]:

    local_mean = _normalize_pose_trajectory(local_mean)
    left_width = (window - 1) // 2
    right_width = window // 2
    curve = np.full(len(local_mean), np.nan, dtype=np.float64)
    valid = np.zeros(len(local_mean), dtype=bool)
    for index in range(len(local_mean)):
        start = max(0, index - left_width)
        stop = min(len(local_mean), index + right_width + 1)
        poses = local_mean[start:stop]
        if len(poses) == 0:
            continue
        centre = _pose_mean(poses)
        residuals = _pose_residuals(centre, poses)
        curve[index] = float(np.mean(np.sum(np.square(residuals), axis=1)))
        valid[index] = True
    return curve, valid


def _temporal_variance_filter(
    local_mean: Array,
    raw_mask: Array,
    window: int,
    threshold: float,
) -> Array:

    raw_mask = np.asarray(raw_mask, dtype=bool)
    curve, valid = _temporal_variance_curve(local_mean, window)
    if raw_mask.shape != curve.shape:
        raise ValueError(
            "Temporal-variance mean trajectories and link masks must have equal length"
        )

    stable = valid & (curve <= threshold)
    return raw_mask & stable


def _filter_link_mask(
    scale: Array,
    config: DynaMACConfig,
    local_mean: Array | None = None,
) -> tuple[Array, Array]:

    raw = np.asarray(scale < config.tau_m, dtype=bool)
    if config.link_filter == "none":
        pointwise = raw.copy()
    elif config.link_filter == "temporal_variance":
        if local_mean is None:
            raise ValueError(
                "The temporal_variance link filter requires a local relative-pose mean trajectory"
            )
        pointwise = _temporal_variance_filter(
            local_mean,
            raw,
            config.temporal_variance_window,
            config.temporal_variance_threshold,
        )
    else:
        raise ValueError(f"Unknown link_filter: {config.link_filter}")
    if config.link_mask_scope == "timestep":
        return raw, pointwise
    if config.link_mask_scope == "skill_majority":
        skill_linked = bool(float(np.mean(pointwise)) > 0.5)
        return raw, np.full(pointwise.shape, skill_linked, dtype=bool)
    if config.link_mask_scope == "skill_majority_gate_timestep":
        gate_enabled = _strict_majority_gate(raw)
        return raw, raw.copy() if gate_enabled else np.zeros(raw.shape, dtype=bool)
    raise ValueError(f"Unknown link_mask_scope: {config.link_mask_scope}")


def _strict_majority_gate(raw_linked: Array) -> bool:

    values = np.asarray(raw_linked, dtype=bool)
    if values.ndim != 1 or len(values) == 0:
        raise ValueError(
            "The raw Eq. (5) linked mask must be a nonempty Boolean [T] sequence"
        )
    return bool(float(np.mean(values)) > 0.5)


def _majority_gate_audit(raw_linked: Array, config: DynaMACConfig) -> bool | None:

    if (
        not config.kinematic_analysis_enabled
        or config.link_mask_scope != "skill_majority_gate_timestep"
    ):
        return None
    return _strict_majority_gate(raw_linked)


def _kinematic_link_masks(
    scale: Array,
    config: DynaMACConfig,
    local_mean: Array | None = None,
) -> tuple[Array, Array]:

    values = _as_float_array(scale)
    if not config.kinematic_analysis_enabled:
        disabled = np.zeros(values.shape, dtype=bool)
        return disabled.copy(), disabled
    return _filter_link_mask(values, config, local_mean=local_mean)


def _validate_demonstrations(
    demonstrations: Sequence[DynaMACDemonstration],
) -> tuple[tuple[str, ...], tuple[int, ...]]:
    if not demonstrations:
        raise ValueError("At least one demonstration is required")

    frame_names = tuple(demonstrations[0].frames)
    skill_sequence = tuple(_compressed_skill_sequence(demonstrations[0].skill))
    if not skill_sequence:
        raise ValueError("Demonstrations contain no skills")
    for demonstration in demonstrations[1:]:
        if set(demonstration.frames) != set(frame_names):
            raise ValueError("All demonstrations must contain the same task parameters")
        if tuple(_compressed_skill_sequence(demonstration.skill)) != skill_sequence:
            raise ValueError("All demonstrations must have the same skill sequence")
        if demonstration.gripper.shape[1] != demonstrations[0].gripper.shape[1]:
            raise ValueError("All demonstrations must have the same gripper dimension")
    return frame_names, skill_sequence


def _skill_slice(demonstration: DynaMACDemonstration, label: int) -> Array:
    indices = np.flatnonzero(demonstration.skill == label)
    if not len(indices) or np.any(np.diff(indices) != 1):
        raise ValueError(f"{demonstration.name} is missing contiguous skill {label}")
    return indices


def _resample_rows(values: Array, duration: int, method: str) -> Array:
    if method == "tapas_subsample":
        return tapas_subsample_rows(values, duration)
    if method == "interpolate":
        return interpolate_rows(values, duration)
    raise ValueError(f"Unknown resampling method: {method}")


def _resample_poses(poses: Array, duration: int, method: str) -> Array:
    if method == "tapas_subsample":
        return tapas_subsample_poses(poses, duration)
    if method == "interpolate":
        return interpolate_poses(poses, duration)
    raise ValueError(f"Unknown resampling method: {method}")


def _resampled_skill_data(
    demonstrations: Sequence[DynaMACDemonstration],
    label: int,
    duration: int,
    virtual_starts: dict[int, list[Array]],
    resampling_method: str = "tapas_subsample",
) -> tuple[Array, Array, dict[str, Array], dict[str, Array]]:
    ee_trajectories = []
    action_trajectories = []
    grippers = []
    real_frames: dict[str, list[Array]] = {
        name: [] for name in demonstrations[0].frames
    }
    virtual_frames: dict[str, list[Array]] = {
        f"virtual_skill_{virtual_label}": [] for virtual_label in virtual_starts
    }
    for demo_index, demonstration in enumerate(demonstrations):
        indices = _skill_slice(demonstration, label)
        ee_trajectories.append(
            _resample_poses(demonstration.ee_pose[indices], duration, resampling_method)
        )
        action_trajectories.append(
            _resample_poses(
                demonstration.action_pose[indices], duration, resampling_method
            )
        )
        grippers.append(
            _resample_rows(demonstration.gripper[indices], duration, resampling_method)
        )
        for name, poses in demonstration.frames.items():
            real_frames[name].append(
                _resample_poses(poses[indices], duration, resampling_method)
            )
        for virtual_label, starts in virtual_starts.items():
            pose = starts[demo_index]
            virtual_frames[f"virtual_skill_{virtual_label}"].append(
                np.repeat(pose[None], duration, axis=0)
            )
    frames = {
        name: np.stack(values)
        for name, values in {**real_frames, **virtual_frames}.items()
    }
    return (
        np.stack(ee_trajectories),
        np.stack(action_trajectories),
        frames,
        {"gripper": np.stack(grippers)},
    )


def _local_trajectories(frame_trajectories: Array, poses: Array) -> Array:
    return np.stack(
        [
            relative_pose(frame_demo, pose_demo)
            for frame_demo, pose_demo in zip(frame_trajectories, poses, strict=True)
        ]
    )


def _modal_euclidean_matrix(values: Array | None, samples: int) -> Array | None:

    if values is None:
        return None
    matrix = _as_float_array(values)
    if matrix.ndim < 2 or len(matrix) != samples:
        raise ValueError(
            "Global Euclidean modal data must start with a demonstration axis"
        )
    matrix = matrix.reshape(samples, -1)
    if matrix.shape[1] == 0 or not np.all(np.isfinite(matrix)):
        raise ValueError("Global Euclidean modal data must be nonempty and finite")
    return matrix


def _trajectory_distances(
    trajectories: Array,
    centres: Array,
    euclidean_trajectories: Array | None = None,
    euclidean_centres: Array | None = None,
) -> Array:

    euclidean_trajectories = _modal_euclidean_matrix(
        euclidean_trajectories, len(trajectories)
    )
    euclidean_centres = _modal_euclidean_matrix(euclidean_centres, len(centres))
    if (euclidean_trajectories is None) != (euclidean_centres is None):
        raise ValueError("Euclidean trajectories and centers must be provided together")
    if (
        euclidean_trajectories is not None
        and euclidean_trajectories.shape[1] != euclidean_centres.shape[1]
    ):
        raise ValueError(
            "Euclidean trajectories and centers have inconsistent dimensions"
        )

    distances = np.empty((len(trajectories), len(centres)), dtype=np.float64)
    for trajectory_index, trajectory in enumerate(trajectories):
        for centre_index, centre in enumerate(centres):
            residuals = np.stack(
                [
                    pose_log_world(centre_pose, trajectory_pose)
                    for centre_pose, trajectory_pose in zip(
                        centre, trajectory, strict=True
                    )
                ]
            )
            squared = float(np.sum(np.square(residuals)))
            if euclidean_trajectories is not None:
                squared += float(
                    np.sum(
                        np.square(
                            euclidean_trajectories[trajectory_index]
                            - euclidean_centres[centre_index]
                        )
                    )
                )
            distances[trajectory_index, centre_index] = squared
    return distances


def _riemannian_kmeans_from_centres(
    trajectories: Array,
    centre_indices: Sequence[int],
    euclidean_trajectories: Array | None = None,
) -> tuple[Array, Array, float]:

    centre_indices = tuple(int(index) for index in centre_indices)
    clusters = len(centre_indices)
    centres = trajectories[list(centre_indices)].copy()
    euclidean_trajectories = _modal_euclidean_matrix(
        euclidean_trajectories, len(trajectories)
    )
    euclidean_centres = (
        None
        if euclidean_trajectories is None
        else euclidean_trajectories[list(centre_indices)].copy()
    )
    labels = np.zeros(len(trajectories), dtype=np.int64)
    for iteration in range(100):
        distances = _trajectory_distances(
            trajectories,
            centres,
            euclidean_trajectories,
            euclidean_centres,
        )
        updated = np.argmin(distances, axis=1)
        if np.array_equal(updated, labels) and iteration:
            break
        labels = updated
        if any(np.sum(labels == index) == 0 for index in range(clusters)):
            return labels, centres, float("inf")
        centres = np.stack(
            [
                np.stack(
                    [
                        _pose_mean(trajectories[labels == index, time_index])
                        for time_index in range(trajectories.shape[1])
                    ]
                )
                for index in range(clusters)
            ]
        )
        if euclidean_trajectories is not None:
            euclidean_centres = np.stack(
                [
                    np.mean(euclidean_trajectories[labels == index], axis=0)
                    for index in range(clusters)
                ]
            )
    distances = _trajectory_distances(
        trajectories,
        centres,
        euclidean_trajectories,
        euclidean_centres,
    )
    residual = float(np.sum(distances[np.arange(len(trajectories)), labels]))
    return labels, centres, residual


def _riemannian_kmeans_candidates(
    trajectories: Array,
    clusters: int,
    config: DynaMACConfig,
    euclidean_trajectories: Array | None = None,
) -> list[tuple[Array, Array, float]]:

    samples = len(trajectories)
    all_combinations = itertools.combinations(range(samples), clusters)
    initializations = list(
        itertools.islice(all_combinations, config.clustering_restarts)
    )
    candidates: list[tuple[Array, Array, float]] = []
    seen: set[tuple[int, ...]] = set()
    for centre_indices in initializations:
        labels, centres, residual = _riemannian_kmeans_from_centres(
            trajectories,
            centre_indices,
            euclidean_trajectories,
        )
        if not np.isfinite(residual):
            continue

        unique = sorted(
            np.unique(labels),
            key=lambda value: int(np.flatnonzero(labels == value)[0]),
        )
        mapping = {int(old): new for new, old in enumerate(unique)}
        canonical = np.asarray(
            [mapping[int(value)] for value in labels], dtype=np.int64
        )
        key = tuple(int(value) for value in canonical)
        if key in seen:
            continue
        seen.add(key)
        canonical_centres = np.stack([centres[int(old)] for old in unique])
        candidates.append((canonical, canonical_centres, residual))
    return candidates


def _deterministic_riemannian_kmeans(
    trajectories: Array,
    clusters: int,
    config: DynaMACConfig | None = None,
    euclidean_trajectories: Array | None = None,
) -> tuple[Array, Array, float]:

    active_config = DynaMACConfig() if config is None else config
    candidates = _riemannian_kmeans_candidates(
        trajectories,
        clusters,
        active_config,
        euclidean_trajectories,
    )
    if not candidates:
        return (
            np.zeros(len(trajectories), dtype=np.int64),
            trajectories[:clusters].copy(),
            float("inf"),
        )
    return min(candidates, key=lambda item: item[2])


def _logsumexp(values: Array, axis: int) -> Array:
    maximum = np.max(values, axis=axis, keepdims=True)
    result = maximum + np.log(
        np.sum(np.exp(values - maximum), axis=axis, keepdims=True)
    )
    return np.squeeze(result, axis=axis)


def _mixture_statistics(
    trajectories: Array,
    responsibilities: Array,
    config: DynaMACConfig,
    euclidean_trajectories: Array | None = None,
) -> tuple[Array | tuple[Array, Array], Array, Array]:

    components = responsibilities.shape[1]
    effective = np.sum(responsibilities, axis=0)
    priors = effective / np.sum(effective)
    euclidean_trajectories = _modal_euclidean_matrix(
        euclidean_trajectories, len(trajectories)
    )
    centres = []
    euclidean_centres = []
    variances = []
    euclidean_dimension = (
        0 if euclidean_trajectories is None else euclidean_trajectories.shape[1]
    )
    floor = np.full(
        trajectories.shape[1] * 6 + euclidean_dimension,
        config.clustering_variance_floor,
        dtype=np.float64,
    )
    for component in range(components):
        weights = responsibilities[:, component]
        centre = np.stack(
            [
                _pose_mean(trajectories[:, time_index], weights)
                for time_index in range(trajectories.shape[1])
            ]
        )
        residuals = np.stack(
            [
                np.concatenate(
                    [
                        pose_log_world(centre_pose, sample_pose)
                        for centre_pose, sample_pose in zip(
                            centre, trajectory, strict=True
                        )
                    ]
                )
                for trajectory in trajectories
            ]
        )
        if euclidean_trajectories is not None:
            euclidean_centre = (
                np.sum(weights[:, None] * euclidean_trajectories, axis=0)
                / effective[component]
            )
            residuals = np.concatenate(
                (residuals, euclidean_trajectories - euclidean_centre), axis=1
            )
            euclidean_centres.append(euclidean_centre)
        variance = (
            np.sum(weights[:, None] * np.square(residuals), axis=0)
            / effective[component]
        )
        centres.append(centre)
        variances.append(variance + floor)
    pose_centres = np.stack(centres)
    combined_centres: Array | tuple[Array, Array] = pose_centres
    if euclidean_trajectories is not None:
        combined_centres = (pose_centres, np.stack(euclidean_centres))
    return combined_centres, np.stack(variances), priors


def _mixture_log_joint(
    trajectories: Array,
    centres: Array | tuple[Array, Array],
    variances: Array,
    priors: Array,
    euclidean_trajectories: Array | None = None,
) -> Array:
    euclidean_trajectories = _modal_euclidean_matrix(
        euclidean_trajectories, len(trajectories)
    )
    if isinstance(centres, tuple):
        pose_centres, euclidean_centres = centres
    else:
        pose_centres, euclidean_centres = centres, None
    if (euclidean_trajectories is None) != (euclidean_centres is None):
        raise ValueError(
            "Mixture-model Euclidean trajectories and centers must be provided together"
        )
    samples = len(trajectories)
    result = np.empty((samples, len(pose_centres)), dtype=np.float64)
    for component, (centre, variance, prior) in enumerate(
        zip(pose_centres, variances, priors, strict=True)
    ):
        residuals = np.stack(
            [
                np.concatenate(
                    [
                        pose_log_world(centre_pose, sample_pose)
                        for centre_pose, sample_pose in zip(
                            centre, trajectory, strict=True
                        )
                    ]
                )
                for trajectory in trajectories
            ]
        )
        if euclidean_trajectories is not None:
            residuals = np.concatenate(
                (
                    residuals,
                    euclidean_trajectories - euclidean_centres[component],
                ),
                axis=1,
            )
        result[:, component] = np.log(max(float(prior), 1.0e-300)) - 0.5 * np.sum(
            np.log(2.0 * math.pi * variance) + np.square(residuals) / variance,
            axis=1,
        )
    return result


def _mixture_bic(
    log_likelihood: float, clusters: int, dimension: int, samples: int
) -> float:

    parameters = clusters * (2 * dimension) + clusters - 1
    return -2.0 * log_likelihood + parameters * math.log(max(samples, 2))


def _riemannian_kmeans_bic(
    trajectories: Array,
    config: DynaMACConfig,
    euclidean_trajectories: Array | None = None,
) -> Array:
    samples = len(trajectories)
    euclidean_trajectories = _modal_euclidean_matrix(euclidean_trajectories, samples)
    dimension = trajectories.shape[1] * 6 + (
        0 if euclidean_trajectories is None else euclidean_trajectories.shape[1]
    )
    maximum = max(1, min(config.maximum_modes, samples // config.minimum_mode_size))
    best_bic = float("inf")
    best_labels = np.zeros(samples, dtype=np.int64)
    for clusters in range(1, maximum + 1):
        for labels, _, residual in _riemannian_kmeans_candidates(
            trajectories,
            clusters,
            config,
            euclidean_trajectories,
        ):
            counts = np.asarray([np.sum(labels == index) for index in range(clusters)])
            if np.any(counts < config.minimum_mode_size) or not np.isfinite(residual):
                continue
            responsibilities = np.eye(clusters, dtype=np.float64)[labels]
            centres, variances, priors = _mixture_statistics(
                trajectories,
                responsibilities,
                config,
                euclidean_trajectories,
            )
            log_likelihood = float(
                np.sum(
                    _logsumexp(
                        _mixture_log_joint(
                            trajectories,
                            centres,
                            variances,
                            priors,
                            euclidean_trajectories,
                        ),
                        1,
                    )
                )
            )
            bic = _mixture_bic(log_likelihood, clusters, dimension, samples)
            if bic < best_bic:
                best_bic = bic
                best_labels = labels.copy()
    return best_labels


def _riemannian_gmm_bic(
    trajectories: Array,
    config: DynaMACConfig,
    euclidean_trajectories: Array | None = None,
) -> Array:

    samples = len(trajectories)
    euclidean_trajectories = _modal_euclidean_matrix(euclidean_trajectories, samples)
    dimension = trajectories.shape[1] * 6 + (
        0 if euclidean_trajectories is None else euclidean_trajectories.shape[1]
    )
    maximum = max(1, min(config.maximum_modes, samples // config.minimum_mode_size))
    best_bic = float("inf")
    best_labels = np.zeros(samples, dtype=np.int64)
    for clusters in range(1, maximum + 1):
        for labels, _, residual in _riemannian_kmeans_candidates(
            trajectories,
            clusters,
            config,
            euclidean_trajectories,
        ):
            counts = np.asarray([np.sum(labels == index) for index in range(clusters)])
            if np.any(counts < config.minimum_mode_size) or not np.isfinite(residual):
                continue
            responsibilities = np.eye(clusters, dtype=np.float64)[labels]
            previous_likelihood = -np.inf
            likelihood = -np.inf
            valid = True
            for _ in range(config.gmm_maximum_iterations):
                effective = np.sum(responsibilities, axis=0)
                if np.any(effective < config.minimum_mode_size):
                    valid = False
                    break
                centres, variances, priors = _mixture_statistics(
                    trajectories,
                    responsibilities,
                    config,
                    euclidean_trajectories,
                )
                log_joint = _mixture_log_joint(
                    trajectories,
                    centres,
                    variances,
                    priors,
                    euclidean_trajectories,
                )
                normalizer = _logsumexp(log_joint, 1)
                likelihood = float(np.sum(normalizer))
                responsibilities = np.exp(log_joint - normalizer[:, None])
                if np.isfinite(previous_likelihood) and abs(
                    likelihood - previous_likelihood
                ) <= 1.0e-8 * (1.0 + abs(likelihood)):
                    break
                previous_likelihood = likelihood
            if not valid or not np.isfinite(likelihood):
                continue
            labels = np.argmax(responsibilities, axis=1).astype(np.int64)
            counts = np.asarray([np.sum(labels == index) for index in range(clusters)])
            if np.any(counts < config.minimum_mode_size):
                continue
            bic = _mixture_bic(likelihood, clusters, dimension, samples)
            if bic < best_bic:
                best_bic = bic
                best_labels = labels.copy()
    return best_labels


def _riemannian_dbscan(
    trajectories: Array,
    config: DynaMACConfig,
    euclidean_trajectories: Array | None = None,
) -> Array:

    distances = np.sqrt(
        np.maximum(
            _trajectory_distances(
                trajectories,
                trajectories,
                euclidean_trajectories,
                euclidean_trajectories,
            ),
            0.0,
        )
    )
    labels, neighbours = _dbscan_raw_labels_from_distances(
        distances,
        config.dbscan_epsilon,
        config.dbscan_min_samples,
    )
    cluster = int(np.max(labels)) + 1 if np.any(labels >= 0) else 0

    noise = np.flatnonzero(labels < 0)
    if len(noise) and cluster == 0:
        labels[:] = 0
    elif len(noise):
        assigned = np.flatnonzero(labels >= 0)
        for index in noise:
            nearest = assigned[int(np.argmin(distances[index, assigned]))]
            labels[index] = labels[nearest]
    return labels


def _dbscan_raw_labels_from_distances(
    distances: Array,
    epsilon: float,
    min_samples: int,
) -> tuple[Array, list[Array]]:

    distances = _as_float_array(distances)
    if (
        distances.ndim != 2
        or distances.shape[0] != distances.shape[1]
        or not np.all(np.isfinite(distances))
    ):
        raise ValueError("DBSCAN distances must form a finite square matrix")
    neighbours = [
        np.flatnonzero(distances[index] <= epsilon) for index in range(len(distances))
    ]
    labels = np.full(len(distances), -1, dtype=np.int64)
    visited = np.zeros(len(distances), dtype=bool)
    cluster = 0
    for seed in range(len(distances)):
        if visited[seed]:
            continue
        visited[seed] = True
        if len(neighbours[seed]) < min_samples:
            continue
        labels[seed] = cluster
        queue = list(int(value) for value in neighbours[seed] if int(value) != seed)
        queued = set(queue)
        while queue:
            point = queue.pop(0)
            if not visited[point]:
                visited[point] = True
                if len(neighbours[point]) >= min_samples:
                    for neighbour in neighbours[point]:
                        neighbour = int(neighbour)
                        if neighbour not in queued:
                            queue.append(neighbour)
                            queued.add(neighbour)
            if labels[point] < 0:
                labels[point] = cluster
        cluster += 1
    return labels, neighbours


def _partition_modes(
    local_trajectories: Array,
    config: DynaMACConfig,
    global_euclidean_trajectories: Array | None = None,
) -> Array:

    trajectories = _prepare_pose_batch(
        np.stack(
            [
                _resample_poses(
                    trajectory, config.clustering_length, config.resampling_method
                )
                for trajectory in local_trajectories
            ]
        )
    )
    euclidean = None
    if global_euclidean_trajectories is not None:
        values = _as_float_array(global_euclidean_trajectories)
        if values.ndim == 2:
            values = values[..., None]
        if values.ndim != 3 or len(values) != len(local_trajectories):
            raise ValueError("Global Euclidean trajectories must have shape [N, T, G]")
        euclidean = np.stack(
            [
                _resample_rows(
                    trajectory, config.clustering_length, config.resampling_method
                )
                for trajectory in values
            ]
        ).reshape(len(values), -1)
    return _partition_resampled_modes(trajectories, config, euclidean)


def _partition_resampled_modes(
    trajectories: Array,
    config: DynaMACConfig,
    global_euclidean: Array | None = None,
) -> Array:

    trajectories = _as_float_array(trajectories)
    if trajectories.ndim != 3 or trajectories.shape[-1] != 7:
        raise ValueError("Clustering trajectories must have shape [N, T, 7]")
    global_euclidean = _modal_euclidean_matrix(global_euclidean, len(trajectories))
    if config.modal_partition_method == "none":
        best_labels = np.zeros(len(trajectories), dtype=np.int64)
    elif config.modal_partition_method == "riemannian_kmeans_bic":
        best_labels = _riemannian_kmeans_bic(trajectories, config, global_euclidean)
    elif config.modal_partition_method == "riemannian_gmm_bic":
        best_labels = _riemannian_gmm_bic(trajectories, config, global_euclidean)
    elif config.modal_partition_method == "dbscan":
        best_labels = _riemannian_dbscan(trajectories, config, global_euclidean)
    else:
        raise ValueError(
            f"Unknown modal partitioning method: {config.modal_partition_method}"
        )

    unique = sorted(
        np.unique(best_labels),
        key=lambda value: int(np.flatnonzero(best_labels == value)[0]),
    )
    mapping = {old: new for new, old in enumerate(unique)}
    return np.asarray([mapping[int(value)] for value in best_labels], dtype=np.int64)


def _gripper_modal_factor(gripper: Array, scale: float) -> Array:

    return float(scale) * _as_float_array(gripper)


def _partition_product_modes(
    local_streams: dict[str, Array],
    config: DynaMACConfig,
    global_streams: dict[str, Array] | None = None,
) -> Array:

    products, global_euclidean = _resampled_product_modal_data(
        local_streams,
        config,
        global_streams,
    )
    return _partition_resampled_modes(products, config, global_euclidean)


def _resampled_product_modal_data(
    local_streams: dict[str, Array],
    config: DynaMACConfig,
    global_streams: dict[str, Array] | None = None,
) -> tuple[Array, Array | None]:

    if not local_streams:
        raise ValueError(
            "Per-frame product clustering requires at least one local stream"
        )
    sample_count: int | None = None
    products = []
    for name, stream in local_streams.items():
        values = _as_float_array(stream)
        if values.ndim != 3 or values.shape[-1] != 7 or len(values) == 0:
            raise ValueError(f"Local stream {name} must have nonempty shape [N, T, 7]")
        if sample_count is None:
            sample_count = len(values)
        elif len(values) != sample_count:
            raise ValueError(
                "All local streams in a per-frame product must correspond one-to-one across demonstrations"
            )
        products.append(
            _prepare_pose_batch(
                np.stack(
                    [
                        _resample_poses(
                            trajectory,
                            config.clustering_length,
                            config.resampling_method,
                        )
                        for trajectory in values
                    ]
                )
            )
        )
    euclidean_products = []
    for name, stream in (global_streams or {}).items():
        values = _as_float_array(stream)
        if values.ndim == 2:
            values = values[..., None]
        if values.ndim != 3 or len(values) == 0:
            raise ValueError(f"Global stream {name} must have nonempty shape [N, T, G]")
        if sample_count is not None and len(values) != sample_count:
            raise ValueError(
                "Global streams must correspond one-to-one with local-stream demonstrations"
            )
        resampled = np.stack(
            [
                _resample_rows(
                    trajectory,
                    config.clustering_length,
                    config.resampling_method,
                )
                for trajectory in values
            ]
        )
        euclidean_products.append(resampled.reshape(len(values), -1))
    global_euclidean = (
        None if not euclidean_products else np.concatenate(euclidean_products, axis=1)
    )
    return np.concatenate(products, axis=1), global_euclidean


def _modal_partition_audit(
    trajectories: Array,
    global_euclidean: Array | None,
    labels: Array,
    config: DynaMACConfig,
) -> dict[str, Any]:

    trajectories = _as_float_array(trajectories)
    labels = np.asarray(labels, dtype=np.int64)
    squared_distances = _trajectory_distances(
        trajectories,
        trajectories,
        global_euclidean,
        global_euclidean,
    )
    distances = np.sqrt(np.maximum(squared_distances, 0.0))
    audit: dict[str, Any] = {
        "method": config.modal_partition_method,
        "partition_performed": config.modal_partition_method != "none",
        "workflow": "unimodal DiGaP; no modal partition for these tasks",
        "automatic_fallback": False,
        "resampled_pose_product": trajectories.copy(),
        "global_euclidean_product": (
            None
            if global_euclidean is None
            else _as_float_array(global_euclidean).copy()
        ),
        "geodesic_distance_matrix": distances,
        "final_labels": labels.copy(),
        "mode_sizes": np.asarray(
            [np.sum(labels == mode) for mode in range(int(np.max(labels)) + 1)],
            dtype=np.int64,
        ),
    }
    if config.modal_partition_method == "none":
        audit.update(
            {
                "unimodal": True,
                "partition_reason": "disabled_by_unimodal_task_default",
            }
        )
    elif config.modal_partition_method == "dbscan":
        raw_labels, neighbours = _dbscan_raw_labels_from_distances(
            distances,
            config.dbscan_epsilon,
            config.dbscan_min_samples,
        )
        raw_clusters = int(np.max(raw_labels)) + 1 if np.any(raw_labels >= 0) else 0
        audit.update(
            {
                "dbscan_epsilon": config.dbscan_epsilon,
                "dbscan_min_samples": config.dbscan_min_samples,
                "dbscan_neighbour_counts": np.asarray(
                    [len(item) for item in neighbours], dtype=np.int64
                ),
                "dbscan_core_mask": np.asarray(
                    [len(item) >= config.dbscan_min_samples for item in neighbours],
                    dtype=bool,
                ),
                "dbscan_raw_labels": raw_labels,
                "dbscan_noise_indices": np.flatnonzero(raw_labels < 0),
                "dbscan_raw_cluster_count": raw_clusters,
                "dbscan_completion": (
                    "pooled_all_samples_INFERRED_IMPLEMENTATION"
                    if raw_clusters == 0 and np.any(raw_labels < 0)
                    else (
                        "nearest_detected_cluster_INFERRED_IMPLEMENTATION"
                        if np.any(raw_labels < 0)
                        else "none"
                    )
                ),
            }
        )
    else:
        audit.update(
            {
                "maximum_modes": config.maximum_modes,
                "minimum_mode_size": config.minimum_mode_size,
                "clustering_variance_floor": config.clustering_variance_floor,
                "clustering_restarts": config.clustering_restarts,
                "selection_criterion": "BIC",
            }
        )
    return audit


def _transition_probabilities(previous: Array, current: Array) -> Array:

    previous = np.asarray(previous, dtype=np.int64)
    current = np.asarray(current, dtype=np.int64)
    if previous.shape != current.shape or previous.ndim != 1:
        raise ValueError(
            "Adjacent-skill mode labels must be one-dimensional and correspond one-to-one by demonstration"
        )
    result = np.zeros((int(np.max(previous)) + 1, int(np.max(current)) + 1))
    for source in range(result.shape[0]):
        source_mask = previous == source
        denominator = int(np.sum(source_mask))
        if denominator == 0:
            raise ValueError("The preceding skill contains an empty mode")
        for target in range(result.shape[1]):
            result[source, target] = (
                np.sum(source_mask & (current == target)) / denominator
            )
    return result


class DynaMAC:

    name = "dynamac"

    def __init__(self, config: DynaMACConfig = DynaMACConfig()) -> None:
        self.config = config
        self.frame_names: tuple[str, ...] = ()
        self.skill_sequence: tuple[int, ...] = ()
        self.skills: list[SkillModel] = []
        self._skill_index = 0
        self._time_index = 0
        self._virtual_frames: dict[str, Array] = {}
        self._pending_virtual_capture = False
        self._mode_strategy: Literal["map", "sample"] = config.default_mode_strategy
        self._mode_path: tuple[int, ...] = ()
        self._mode_evidence: tuple[Array, ...] = ()
        self._active_mode = 0
        self._complete = False
        self._episode_initialized = False
        self._rng = np.random.default_rng(config.random_seed)
        self._training_audit: dict[str, Any] = {}

    @property
    def fitted(self) -> bool:
        return bool(self.skills)

    @property
    def complete(self) -> bool:
        return self._complete

    @property
    def selected_mode_path(self) -> tuple[int, ...]:

        if not self._episode_initialized:
            raise RuntimeError(
                "DynaMAC has not been reset; the episode mode path is unavailable"
            )
        return tuple(self._mode_path)

    @property
    def selection_semantics_id(self) -> str:

        return _selection_semantics_id(self.config)

    @property
    def training_audit(self) -> dict[str, Any]:

        return deepcopy(self._training_audit)

    @property
    def current_skill(self) -> SkillModel:
        if not self.fitted:
            raise RuntimeError("DynaMAC has not been fitted")
        return self.skills[self._skill_index]

    def _capture_runtime_state(self) -> dict[str, Any]:

        return {
            "skill_index": self._skill_index,
            "time_index": self._time_index,
            "virtual_frames": {
                name: value.copy() for name, value in self._virtual_frames.items()
            },
            "pending_virtual_capture": self._pending_virtual_capture,
            "mode_strategy": self._mode_strategy,
            "mode_path": self._mode_path,
            "mode_evidence": tuple(value.copy() for value in self._mode_evidence),
            "active_mode": self._active_mode,
            "complete": self._complete,
            "episode_initialized": self._episode_initialized,
            "rng_state": deepcopy(self._rng.bit_generator.state),
        }

    def _restore_runtime_state(self, state: dict[str, Any]) -> None:
        self._skill_index = state["skill_index"]
        self._time_index = state["time_index"]
        self._virtual_frames = {
            name: value.copy() for name, value in state["virtual_frames"].items()
        }
        self._pending_virtual_capture = state["pending_virtual_capture"]
        self._mode_strategy = state["mode_strategy"]
        self._mode_path = state["mode_path"]
        self._mode_evidence = tuple(value.copy() for value in state["mode_evidence"])
        self._active_mode = state["active_mode"]
        self._complete = state["complete"]
        self._episode_initialized = state["episode_initialized"]
        self._rng.bit_generator.state = deepcopy(state["rng_state"])

    def _invalidate_episode_after_fit(self) -> None:

        self._skill_index = 0
        self._time_index = 0
        self._virtual_frames = {}
        self._pending_virtual_capture = False
        self._mode_strategy = self.config.default_mode_strategy
        self._mode_path = ()
        self._mode_evidence = ()
        self._active_mode = 0
        self._complete = False
        self._episode_initialized = False
        self._rng = np.random.default_rng(self.config.random_seed)

    def fit(self, demonstrations: Sequence[DynaMACDemonstration]) -> DynaMAC:

        previous_model = (self.frame_names, self.skill_sequence, self.skills)
        previous_runtime = self._capture_runtime_state()
        previous_audit = deepcopy(self._training_audit)
        try:
            return self._fit_in_place(demonstrations)
        except Exception:
            self.frame_names, self.skill_sequence, self.skills = previous_model
            self._restore_runtime_state(previous_runtime)
            self._training_audit = previous_audit
            raise

    def _fit_in_place(
        self,
        demonstrations: Sequence[DynaMACDemonstration],
    ) -> DynaMAC:

        frame_names, skill_sequence = _validate_demonstrations(demonstrations)
        self._training_audit = {
            "audit_schema_version": 1,
            "model_schema_version": MODEL_SCHEMA_VERSION,
            "semantics_id": self.selection_semantics_id,
            "scope": "DynaMAC Algorithm 1 + MiDiGaP + TAPAS skill inputs",
            "policy_feedback": False,
            "frame_names": frame_names,
            "skill_sequence": skill_sequence,
            "demonstration_names": tuple(item.name for item in demonstrations),
            "config": asdict(self.config),
            "kinematic_analysis": {
                "enabled": self.config.kinematic_analysis_enabled,
                "equation": "Eq. (5)",
                "disabled_behavior": (
                    None
                    if self.config.kinematic_analysis_enabled
                    else "explicit_bypass_all_dynamic_candidates_available"
                ),
                "tau_m_retained": self.config.tau_m,
            },
            "skills": [],
        }
        fitted_skills: list[SkillModel] = []
        virtual_starts: dict[int, list[Array]] = {}
        previous_mode_labels: Array | None = None
        for label in skill_sequence:

            virtual_starts[label] = [
                demonstration.ee_pose[_skill_slice(demonstration, label)[0]].copy()
                for demonstration in demonstrations
            ]
            lengths = [
                len(_skill_slice(demonstration, label))
                for demonstration in demonstrations
            ]

            duration = max(int(float(np.mean(lengths))), 1)
            ee, actions, frames, extra = _resampled_skill_data(
                demonstrations,
                label,
                duration,
                virtual_starts,
                self.config.resampling_method,
            )

            policy_pose = ee if self.config.policy_model == "time_state" else actions
            local_policy = {
                name: _local_trajectories(frame_values, policy_pose)
                for name, frame_values in frames.items()
            }
            candidate_frames = [
                *frame_names,
                *(f"virtual_skill_{virtual_label}" for virtual_label in virtual_starts),
            ]
            candidate_kind: dict[str, CandidateKind] = {
                name: "dynamic" for name in frame_names
            }
            candidate_kind.update(
                {
                    f"virtual_skill_{virtual_label}": "virtual"
                    for virtual_label in virtual_starts
                }
            )
            if len(candidate_kind) != len(candidate_frames):
                raise ValueError(
                    "Physical task-parameter names cannot conflict with DynaMAC virtual-frame names"
                )
            skill_audit: dict[str, Any] = {
                "semantics_id": self.selection_semantics_id,
                "skill_label": int(label),
                "duration": int(duration),
                "source_skill_indices": tuple(
                    _skill_slice(demonstration, label).copy()
                    for demonstration in demonstrations
                ),
                "virtual_frame_history": tuple(
                    f"virtual_skill_{virtual_label}" for virtual_label in virtual_starts
                ),
                "virtual_frame_start_poses": {
                    f"virtual_skill_{virtual_label}": np.stack(starts)
                    for virtual_label, starts in virtual_starts.items()
                },
                "candidate_frames": tuple(candidate_frames),
                "candidate_kind": candidate_kind.copy(),
                "resampled_ee": ee.copy(),
                "resampled_action": actions.copy(),
                "policy_model": self.config.policy_model,
                "kinematic_analysis_enabled": self.config.kinematic_analysis_enabled,
                "resampled_policy_pose": policy_pose.copy(),
                "resampled_gripper": extra["gripper"].copy(),
                "resampled_frame_pose": {
                    name: values.copy() for name, values in frames.items()
                },
                "local_policy": {
                    name: values.copy() for name, values in local_policy.items()
                },
                "kinematic_links": {},
            }
            link_diagnostics: dict[str, dict[str, Any]] = {}
            if self.config.preliminary_analysis == "pooled":

                pooled_availability = {
                    name: np.ones(duration, dtype=bool) for name in candidate_frames
                }
                for name in frame_names:
                    link_mean, covariance = _fit_pose_sequence(
                        local_policy[name],
                        self.config.position_variance_floor,
                        self.config.rotation_variance_floor,
                        covariance_estimation_method=self.config.covariance_estimation_method,
                    )
                    scale = geometric_mean_standard_deviation(
                        covariance,
                        position_weight=self.config.eq5_position_weight,
                        rotation_weight=self.config.eq5_rotation_weight,
                    )
                    raw_mask, linked_mask = _kinematic_link_masks(
                        scale,
                        self.config,
                        local_mean=link_mean,
                    )
                    temporal_curve, temporal_valid = _temporal_variance_curve(
                        link_mean,
                        self.config.temporal_variance_window,
                    )
                    pooled_availability[name] = ~linked_mask
                    link_diagnostics[name] = {
                        "linked": bool(np.any(linked_mask)),
                        "fully_linked": bool(np.all(linked_mask)),
                        "raw_linked_fraction": float(np.mean(raw_mask)),
                        "linked_fraction": float(np.mean(linked_mask)),
                        "raw_maximum_link_run": _maximum_true_run(raw_mask),
                        "maximum_link_run": _maximum_true_run(linked_mask),
                        "minimum_m": float(np.min(scale)),
                        "median_m": float(np.median(scale)),
                        "gmsd": scale.tolist(),
                        "raw_link_mask": raw_mask.tolist(),
                        "filtered_link_mask": linked_mask.tolist(),
                        "filter": self.config.link_filter,
                        "mask_scope": self.config.link_mask_scope,
                        **(
                            {
                                "majority_gate_enabled": _majority_gate_audit(
                                    raw_mask, self.config
                                ),
                                "majority_gate_rule": ("strict_mean_raw_linked_gt_0.5"),
                            }
                            if self.config.link_mask_scope
                            == "skill_majority_gate_timestep"
                            else {}
                        ),
                        "kinematic_analysis_enabled": (
                            self.config.kinematic_analysis_enabled
                        ),
                        "analysis_performed": self.config.kinematic_analysis_enabled,
                        "disabled_behavior": (
                            None
                            if self.config.kinematic_analysis_enabled
                            else "explicit_bypass_all_dynamic_candidates_available"
                        ),
                        "skill_linked": (
                            bool(linked_mask[0])
                            if self.config.link_mask_scope == "skill_majority"
                            else None
                        ),
                        "scope": "pooled_preliminary_digap",
                        "per_mode": [],
                    }
                    sign_covariance, logdet_covariance = np.linalg.slogdet(covariance)
                    if np.any(sign_covariance <= 0.0):
                        raise RuntimeError(
                            "Kinematic-link analysis produced a non-positive-definite covariance"
                        )
                    skill_audit["kinematic_links"][name] = {
                        "preliminary_mean": link_mean.copy(),
                        "preliminary_covariance": covariance.copy(),
                        "preliminary_precision": np.linalg.inv(covariance),
                        "per_dimension_standard_deviation": np.sqrt(
                            np.diagonal(covariance, axis1=-2, axis2=-1)
                        ),
                        "gmsd": scale.copy(),
                        "logdet_covariance": logdet_covariance.copy(),
                        "logdet_precision": (-logdet_covariance).copy(),
                        "raw_link_mask": raw_mask.copy(),
                        "filtered_link_mask": linked_mask.copy(),
                        "temporal_variance": temporal_curve,
                        "temporal_variance_valid": temporal_valid,
                        "tau_m": self.config.tau_m,
                        "position_weight": self.config.eq5_position_weight,
                        "rotation_weight": self.config.eq5_rotation_weight,
                        "dimension": int(
                            3 * (self.config.eq5_position_weight > 0.0)
                            + 3 * (self.config.eq5_rotation_weight > 0.0)
                        ),
                        "filter": self.config.link_filter,
                        "mask_scope": self.config.link_mask_scope,
                        **(
                            {
                                "majority_gate_enabled": _majority_gate_audit(
                                    raw_mask, self.config
                                ),
                                "majority_gate_rule": ("strict_mean_raw_linked_gt_0.5"),
                            }
                            if self.config.link_mask_scope
                            == "skill_majority_gate_timestep"
                            else {}
                        ),
                        "kinematic_analysis_enabled": (
                            self.config.kinematic_analysis_enabled
                        ),
                        "analysis_performed": self.config.kinematic_analysis_enabled,
                        "disabled_behavior": (
                            None
                            if self.config.kinematic_analysis_enabled
                            else "explicit_bypass_all_dynamic_candidates_available"
                        ),
                        "skill_linked": (
                            bool(linked_mask[0])
                            if self.config.link_mask_scope == "skill_majority"
                            else None
                        ),
                        "filter_status": (
                            "METHOD_ABLATION_EQ5_DISABLED"
                            if not self.config.kinematic_analysis_enabled
                            else (
                                "REFERENCE_EQ5_EXACT"
                                if self.config.link_filter == "none"
                                else "ALLOWED_NUMERICS_INFERRED"
                            )
                        ),
                    }
                policy_covariance: dict[str, Array] = {}
                for name in candidate_frames:
                    _, covariance = _fit_pose_sequence(
                        local_policy[name],
                        self.config.position_variance_floor,
                        self.config.rotation_variance_floor,
                        covariance_estimation_method=self.config.covariance_estimation_method,
                    )
                    policy_covariance[name] = covariance
                selection_covariance = (
                    policy_covariance
                    if self.config.eq6_covariance_scope == "full_pose"
                    else {
                        name: _weighted_pose_covariance(
                            covariance,
                            position_weight=self.config.eq5_position_weight,
                            rotation_weight=self.config.eq5_rotation_weight,
                        )
                        for name, covariance in policy_covariance.items()
                    }
                )
                selected, pooled_eq6_selected, selection_details = _eq6_skill_selection(
                    selection_covariance,
                    self.config.tau_omega,
                    availability=pooled_availability,
                    candidate_kind=candidate_kind,
                    empty_selection=self.config.eq6_empty_selection,
                    semantics_id=self.selection_semantics_id,
                )
                scores = selection_details["scores"]
                skill_audit["task_parameter_selection"] = {
                    **selection_details,
                    "candidate_covariance": {
                        name: covariance.copy()
                        for name, covariance in policy_covariance.items()
                    },
                    "selection_covariance": {
                        name: covariance.copy()
                        for name, covariance in selection_covariance.items()
                    },
                    "eq6_covariance_scope": self.config.eq6_covariance_scope,
                    "eq6_covariance_scope_source_status": (
                        "CONFIGURED_EQ5_WEIGHTED_SUBSPACE"
                        if self.config.eq6_covariance_scope == "eq5_weighted_subspace"
                        else "REFERENCE_EQ6_FULL_POSE"
                    ),
                    "eq5_availability": {
                        name: mask.copy() for name, mask in pooled_availability.items()
                    },
                    "tau_omega": self.config.tau_omega,
                    "kinematic_analysis_enabled": (
                        self.config.kinematic_analysis_enabled
                    ),
                    "eq5_bypass": (
                        None
                        if self.config.kinematic_analysis_enabled
                        else "all_dynamic_candidates_available"
                    ),
                }
                if not selected:
                    score_summary = ", ".join(
                        f"{name}={scores[name]:.6g}" for name in candidate_frames
                    )
                    raise RuntimeError(
                        f"skill {label} has no task parameter above "
                        f"tau_omega={self.config.tau_omega}; fix demonstration coverage "
                        "or the configured threshold rather than applying a silent "
                        f"fallback; scores: {score_summary}"
                    )

                modal_local_streams = {name: local_policy[name] for name in selected}
                modal_global_streams = {
                    "gripper": _gripper_modal_factor(
                        extra["gripper"], self.config.gripper_clustering_scale
                    )
                }
                modal_product, modal_euclidean = _resampled_product_modal_data(
                    modal_local_streams,
                    self.config,
                    modal_global_streams,
                )
                mode_labels = _partition_resampled_modes(
                    modal_product,
                    self.config,
                    modal_euclidean,
                )
                skill_audit["modal_partition"] = _modal_partition_audit(
                    modal_product,
                    modal_euclidean,
                    mode_labels,
                    self.config,
                )
                modes = int(np.max(mode_labels)) + 1
                eq5_availability = {
                    name: np.repeat(pooled_availability[name][None, :], modes, axis=0)
                    for name in candidate_frames
                }
                eq6_selected = {
                    name: np.full(modes, pooled_eq6_selected[name], dtype=bool)
                    for name in candidate_frames
                }
            else:

                modal_product, modal_euclidean = _resampled_product_modal_data(
                    {name: local_policy[name] for name in frame_names},
                    self.config,
                    {
                        "gripper": _gripper_modal_factor(
                            extra["gripper"], self.config.gripper_clustering_scale
                        )
                    },
                )
                mode_labels = _partition_resampled_modes(
                    modal_product,
                    self.config,
                    modal_euclidean,
                )
                skill_audit["modal_partition"] = _modal_partition_audit(
                    modal_product,
                    modal_euclidean,
                    mode_labels,
                    self.config,
                )
                modes = int(np.max(mode_labels)) + 1

                policy_covariance: dict[str, Array] = {}
                for name in candidate_frames:
                    per_mode_covariance = []
                    for mode in range(modes):
                        _, covariance = _fit_pose_sequence(
                            local_policy[name][mode_labels == mode],
                            self.config.position_variance_floor,
                            self.config.rotation_variance_floor,
                            covariance_estimation_method=self.config.covariance_estimation_method,
                        )
                        per_mode_covariance.append(covariance)
                    policy_covariance[name] = np.stack(per_mode_covariance)
                selection_covariance = (
                    policy_covariance
                    if self.config.eq6_covariance_scope == "full_pose"
                    else {
                        name: _weighted_pose_covariance(
                            covariance,
                            position_weight=self.config.eq5_position_weight,
                            rotation_weight=self.config.eq5_rotation_weight,
                        )
                        for name, covariance in policy_covariance.items()
                    }
                )

                eq5_availability = {
                    name: np.ones((modes, duration), dtype=bool)
                    for name in candidate_frames
                }
                for name in frame_names:
                    mode_scales = []
                    mode_raw_masks = []
                    mode_linked_masks = []
                    per_mode_diagnostics = []
                    for mode in range(modes):
                        link_mean, covariance = _fit_pose_sequence(
                            local_policy[name][mode_labels == mode],
                            self.config.position_variance_floor,
                            self.config.rotation_variance_floor,
                            covariance_estimation_method=self.config.covariance_estimation_method,
                        )
                        scale = geometric_mean_standard_deviation(
                            covariance,
                            position_weight=self.config.eq5_position_weight,
                            rotation_weight=self.config.eq5_rotation_weight,
                        )
                        raw_mask, linked_mask = _kinematic_link_masks(
                            scale,
                            self.config,
                            local_mean=link_mean,
                        )
                        temporal_curve, temporal_valid = _temporal_variance_curve(
                            link_mean,
                            self.config.temporal_variance_window,
                        )
                        eq5_availability[name][mode] = ~linked_mask
                        mode_scales.append(scale)
                        mode_raw_masks.append(raw_mask)
                        mode_linked_masks.append(linked_mask)
                        per_mode_diagnostics.append(
                            {
                                "mode": mode,
                                "demonstration_indices": [
                                    int(index)
                                    for index in np.flatnonzero(mode_labels == mode)
                                ],
                                "linked": bool(np.any(linked_mask)),
                                "fully_linked": bool(np.all(linked_mask)),
                                "raw_linked_fraction": float(np.mean(raw_mask)),
                                "linked_fraction": float(np.mean(linked_mask)),
                                "raw_maximum_link_run": _maximum_true_run(raw_mask),
                                "maximum_link_run": _maximum_true_run(linked_mask),
                                "minimum_m": float(np.min(scale)),
                                "median_m": float(np.median(scale)),
                                "gmsd": scale.tolist(),
                                "raw_link_mask": raw_mask.tolist(),
                                "filtered_link_mask": linked_mask.tolist(),
                                "mask_scope": self.config.link_mask_scope,
                                **(
                                    {
                                        "majority_gate_enabled": _majority_gate_audit(
                                            raw_mask, self.config
                                        ),
                                        "majority_gate_rule": (
                                            "strict_mean_raw_linked_gt_0.5"
                                        ),
                                    }
                                    if self.config.link_mask_scope
                                    == "skill_majority_gate_timestep"
                                    else {}
                                ),
                                "kinematic_analysis_enabled": (
                                    self.config.kinematic_analysis_enabled
                                ),
                                "analysis_performed": (
                                    self.config.kinematic_analysis_enabled
                                ),
                                "disabled_behavior": (
                                    None
                                    if self.config.kinematic_analysis_enabled
                                    else "explicit_bypass_all_dynamic_candidates_available"
                                ),
                                "skill_linked": (
                                    bool(linked_mask[0])
                                    if self.config.link_mask_scope == "skill_majority"
                                    else None
                                ),
                            }
                        )
                    scales = np.stack(mode_scales)
                    raw_masks = np.stack(mode_raw_masks)
                    linked_masks = np.stack(mode_linked_masks)
                    link_diagnostics[name] = {
                        "linked": bool(np.any(linked_masks)),
                        "fully_linked": bool(np.all(linked_masks)),
                        "raw_linked_fraction": float(np.mean(raw_masks)),
                        "linked_fraction": float(np.mean(linked_masks)),
                        "raw_maximum_link_run": max(
                            _maximum_true_run(mask) for mask in raw_masks
                        ),
                        "maximum_link_run": max(
                            _maximum_true_run(mask) for mask in linked_masks
                        ),
                        "minimum_m": float(np.min(scales)),
                        "median_m": float(np.median(scales)),
                        "gmsd": (scales[0] if modes == 1 else scales).tolist(),
                        "raw_link_mask": (
                            raw_masks[0] if modes == 1 else raw_masks
                        ).tolist(),
                        "filtered_link_mask": (
                            linked_masks[0] if modes == 1 else linked_masks
                        ).tolist(),
                        "filter": self.config.link_filter,
                        "mask_scope": self.config.link_mask_scope,
                        **(
                            {
                                "majority_gate_enabled": (
                                    [
                                        _majority_gate_audit(mask, self.config)
                                        for mask in mode_raw_masks
                                    ]
                                    if modes > 1
                                    else _majority_gate_audit(
                                        mode_raw_masks[0], self.config
                                    )
                                ),
                                "majority_gate_rule": ("strict_mean_raw_linked_gt_0.5"),
                            }
                            if self.config.link_mask_scope
                            == "skill_majority_gate_timestep"
                            else {}
                        ),
                        "kinematic_analysis_enabled": (
                            self.config.kinematic_analysis_enabled
                        ),
                        "analysis_performed": self.config.kinematic_analysis_enabled,
                        "disabled_behavior": (
                            None
                            if self.config.kinematic_analysis_enabled
                            else "explicit_bypass_all_dynamic_candidates_available"
                        ),
                        "scope": (
                            "precluster_all_real_frame_product_mode_conditioned_"
                            "preliminary_digap_INFERRED_IMPLEMENTATION"
                        ),
                        "per_mode": per_mode_diagnostics,
                    }
                    skill_audit["kinematic_links"][name] = {
                        "scope": "mode_conditioned_INFERRED_IMPLEMENTATION",
                        "per_mode": deepcopy(per_mode_diagnostics),
                    }

                per_mode_selection = [
                    _eq6_skill_selection(
                        {
                            name: selection_covariance[name][mode]
                            for name in candidate_frames
                        },
                        self.config.tau_omega,
                        availability={
                            name: eq5_availability[name][mode]
                            for name in candidate_frames
                        },
                        candidate_kind=candidate_kind,
                        empty_selection=self.config.eq6_empty_selection,
                        semantics_id=self.selection_semantics_id,
                    )
                    for mode in range(modes)
                ]
                per_mode_scores = [item[2]["scores"] for item in per_mode_selection]
                scores = {
                    name: max(mode_scores[name] for mode_scores in per_mode_scores)
                    for name in candidate_frames
                }
                skill_audit["task_parameter_selection"] = {
                    "scope": "mode_conditioned_INFERRED_IMPLEMENTATION",
                    "per_mode_scores": deepcopy(per_mode_scores),
                    "per_mode_details": [
                        deepcopy(item[2]) for item in per_mode_selection
                    ],
                    "scores": deepcopy(scores),
                    "candidate_covariance": {
                        name: covariance.copy()
                        for name, covariance in policy_covariance.items()
                    },
                    "selection_covariance": {
                        name: covariance.copy()
                        for name, covariance in selection_covariance.items()
                    },
                    "eq6_covariance_scope": self.config.eq6_covariance_scope,
                    "eq6_covariance_scope_source_status": (
                        "CONFIGURED_EQ5_WEIGHTED_SUBSPACE"
                        if self.config.eq6_covariance_scope == "eq5_weighted_subspace"
                        else "REFERENCE_EQ6_FULL_POSE"
                    ),
                    "eq5_availability": {
                        name: mask.copy() for name, mask in eq5_availability.items()
                    },
                    "normalization_scope": "eq5_available_candidate_frames_per_timestep",
                    "eq5_filters_eq6_candidates": (
                        self.config.kinematic_analysis_enabled
                    ),
                    "eq5_filters_eq6_denominator": (
                        self.config.kinematic_analysis_enabled
                    ),
                    "tau_omega": self.config.tau_omega,
                    "kinematic_analysis_enabled": (
                        self.config.kinematic_analysis_enabled
                    ),
                    "eq5_bypass": (
                        None
                        if self.config.kinematic_analysis_enabled
                        else "all_dynamic_candidates_available"
                    ),
                }

                eq6_selected = {
                    name: np.asarray(
                        [item[1][name] for item in per_mode_selection],
                        dtype=bool,
                    )
                    for name in candidate_frames
                }
                selected = tuple(
                    name for name in candidate_frames if np.any(eq6_selected[name])
                )
            if not selected:
                score_summary = ", ".join(
                    f"{name}={scores[name]:.6g}" for name in candidate_frames
                )
                raise RuntimeError(
                    f"skill {label} has no task parameter above "
                    f"tau_omega={self.config.tau_omega}; fix demonstration coverage "
                    "or the configured threshold rather than applying a silent "
                    f"fallback; scores: {score_summary}"
                )

            framewise_participation = _compose_framewise_poe_participation(
                eq5_availability,
                eq6_selected,
            )
            selection_audit = skill_audit["task_parameter_selection"]
            selection_audit.update(
                {
                    "kinematic_link_granularity": (
                        "disabled_all_dynamic_candidates_available"
                        if not self.config.kinematic_analysis_enabled
                        else (
                            "per_skill_strict_majority_eq5"
                            if self.config.link_mask_scope == "skill_majority"
                            else (
                                "skill_majority_gate_then_raw_per_timestep_eq5"
                                if self.config.link_mask_scope
                                == "skill_majority_gate_timestep"
                                else "per_timestep_within_skill_eq5"
                            )
                        )
                    ),
                    "task_parameter_selection_granularity": "per_skill_max_over_time_eq6",
                    "eq5_filters_eq6_candidates": (
                        self.config.kinematic_analysis_enabled
                    ),
                    "eq5_filters_eq6_denominator": (
                        self.config.kinematic_analysis_enabled
                    ),
                    "eq6_normalization_scope": (
                        "eq5_available_candidate_frames_per_timestep"
                    ),
                    "selected_by_eq6": {
                        name: mask.copy() for name, mask in eq6_selected.items()
                    },
                    "poe_participation_mask": {
                        name: mask.copy()
                        for name, mask in framewise_participation.items()
                    },
                }
            )
            selected_coverage = np.zeros((modes, duration), dtype=bool)
            for name in selected:
                selected_coverage |= framewise_participation[name]
            if not np.all(selected_coverage):
                missing_mode_times = [
                    [int(mode), int(time_index)]
                    for mode, time_index in np.argwhere(~selected_coverage)
                ]
                raise RuntimeError(
                    f"skill {label} selected task parameters are all linked at "
                    f"[mode, time] {missing_mode_times}; DynaMAC does not "
                    "define a no-expert fallback"
                )

            streams = {}
            gripper_models = []
            for mode in range(modes):
                members = mode_labels == mode
                gripper_models.append(np.mean(extra["gripper"][members], axis=0))
            for name in selected:
                means = []
                covariances = []
                for mode in range(modes):
                    members = mode_labels == mode
                    mean, covariance = _fit_pose_sequence(
                        local_policy[name][members],
                        self.config.position_variance_floor,
                        self.config.rotation_variance_floor,
                        covariance_estimation_method=self.config.covariance_estimation_method,
                    )
                    means.append(mean)
                    covariances.append(covariance)
                streams[name] = StreamModel(
                    name,
                    np.stack(means),
                    np.stack(covariances),
                    (
                        framewise_participation[name][0].copy()
                        if modes == 1
                        else framewise_participation[name].copy()
                    ),
                    (
                        eq5_availability[name][0].copy()
                        if modes == 1
                        else eq5_availability[name].copy()
                    ),
                    eq6_selected[name].copy(),
                )

            priors = np.asarray(
                [np.mean(mode_labels == mode) for mode in range(modes)],
                dtype=np.float64,
            )
            mode_demonstration_indices = tuple(
                tuple(int(index) for index in np.flatnonzero(mode_labels == mode))
                for mode in range(modes)
            )
            transition = (
                None
                if previous_mode_labels is None
                else _transition_probabilities(previous_mode_labels, mode_labels)
            )
            fitted_skills.append(
                SkillModel(
                    label=label,
                    duration=duration,
                    selected_frames=selected,
                    mode_priors=priors,
                    streams=streams,
                    gripper=np.stack(gripper_models),
                    transition_from_previous=transition,
                    mode_demonstration_indices=mode_demonstration_indices,
                    link_diagnostics=link_diagnostics,
                    selection_scores=scores,
                )
            )
            skill_audit.update(
                {
                    "selected_frames": tuple(selected),
                    "mode_labels": mode_labels.copy(),
                    "mode_priors": priors.copy(),
                    "mode_demonstration_indices": mode_demonstration_indices,
                    "transition_from_previous": (
                        None if transition is None else transition.copy()
                    ),
                    "final_streams": {
                        name: {
                            "mean": stream.mean.copy(),
                            "covariance": stream.covariance.copy(),
                            "precision": np.linalg.inv(stream.covariance),
                            "active": stream.active.copy(),
                            "availability": stream.availability.copy(),
                            "selected_by_eq6": stream.selected_by_eq6.copy(),
                            "semantics_id": self.selection_semantics_id,
                        }
                        for name, stream in streams.items()
                    },
                    "gripper_model": np.stack(gripper_models),
                }
            )
            self._training_audit["skills"].append(skill_audit)
            previous_mode_labels = mode_labels
        self.frame_names = frame_names
        self.skill_sequence = skill_sequence
        self.skills = fitted_skills
        self._invalidate_episode_after_fit()
        return self

    def _select_mode_path(
        self,
        strategy: Literal["map", "sample"],
        mode_evidence: Sequence[Array] | None = None,
    ) -> tuple[int, ...]:

        if not self.skills:
            raise RuntimeError("DynaMAC has no fitted skills")
        if mode_evidence is None:
            evidence = tuple(np.ones_like(skill.mode_priors) for skill in self.skills)
        else:
            if len(mode_evidence) != len(self.skills):
                raise ValueError("Mode evidence must match the skill-sequence length")
            validated = []
            for skill, values in zip(self.skills, mode_evidence, strict=True):
                current = _as_float_array(values)
                if (
                    current.shape != skill.mode_priors.shape
                    or np.any(~np.isfinite(current))
                    or np.any(current < 0.0)
                ):
                    raise ValueError(
                        "Mode evidence for each skill must be a finite nonnegative [M] vector"
                    )
                maximum = float(np.max(current))
                if maximum <= 0.0:
                    raise RuntimeError("Mode evidence excludes every mode for a skill")

                validated.append(current / maximum)
            evidence = tuple(validated)

        priors = self.skills[0].mode_priors * evidence[0]
        prior_total = float(np.sum(priors))
        if prior_total <= 0.0:
            raise RuntimeError("Mode evidence excludes every MiDiGaP path")
        priors = priors / prior_total
        transitions: list[Array] = []
        for index, skill in enumerate(self.skills[1:], start=1):
            original = skill.transition_from_previous
            if original is None:
                raise RuntimeError(
                    "A MiDiGaP skill is missing its mode-transition matrix"
                )
            weighted = original * evidence[index][None, :]
            row_sum = np.sum(weighted, axis=1, keepdims=True)
            updated = np.divide(
                weighted,
                row_sum,
                out=np.zeros_like(weighted),
                where=row_sum > 0.0,
            )
            transitions.append(updated)

        if strategy == "sample":

            backward: list[Array] = [np.empty(0)] * len(self.skills)
            backward[-1] = np.ones_like(self.skills[-1].mode_priors)
            for index in range(len(self.skills) - 2, -1, -1):
                message = transitions[index] @ backward[index + 1]
                maximum = float(np.max(message))
                backward[index] = message / maximum if maximum > 0.0 else message
            probabilities = priors * backward[0]
            total = float(np.sum(probabilities))
            if total <= 0.0:
                raise RuntimeError("Mode evidence excludes every MiDiGaP path")
            path = [int(self._rng.choice(len(probabilities), p=probabilities / total))]
            for index, transition in enumerate(transitions, start=1):
                probabilities = transition[path[-1]] * backward[index]
                total = float(np.sum(probabilities))
                if total <= 0.0:
                    raise RuntimeError(
                        "Mode evidence excludes every continuation of the selected prefix"
                    )
                path.append(
                    int(self._rng.choice(len(probabilities), p=probabilities / total))
                )
            return tuple(path)
        if strategy != "map":
            raise ValueError(f"Unknown mode-selection strategy: {strategy}")

        scores = np.full(priors.shape, -np.inf, dtype=np.float64)
        np.log(priors, out=scores, where=priors > 0.0)
        backpointers: list[Array] = []
        for updated_transition in transitions:
            transition = np.full(updated_transition.shape, -np.inf, dtype=np.float64)
            np.log(
                updated_transition,
                out=transition,
                where=updated_transition > 0.0,
            )
            candidates = scores[:, None] + transition
            backpointers.append(np.argmax(candidates, axis=0))
            scores = np.max(candidates, axis=0)
        if not np.any(np.isfinite(scores)):
            raise RuntimeError("Mode evidence excludes every MiDiGaP path")
        path = [int(np.argmax(scores))]
        for backpointer in reversed(backpointers):
            path.append(int(backpointer[path[-1]]))
        return tuple(reversed(path))

    def reset(
        self,
        observation: DynaMACObservation,
        mode_strategy: Literal["map", "sample"] | None = None,
        mode_evidence: Sequence[Array] | None = None,
    ) -> None:

        state = self._capture_runtime_state()
        try:
            self._reset_impl(observation, mode_strategy, mode_evidence)
        except Exception:
            self._restore_runtime_state(state)
            raise

    def restart_current_skill_reference(self) -> tuple[int, int]:

        if not self._episode_initialized:
            raise RuntimeError(
                "DynaMAC has not been reset; the skill reference cannot be restarted"
            )
        self._time_index = 0
        self._complete = False

        self._active_mode = self._mode_path[self._skill_index]
        return self._skill_index, self._time_index

    def _reset_impl(
        self,
        observation: DynaMACObservation,
        mode_strategy: Literal["map", "sample"] | None,
        mode_evidence: Sequence[Array] | None,
    ) -> None:
        if not self.fitted:
            raise RuntimeError("DynaMAC has not been fitted")
        selected_strategy = (
            self.config.default_mode_strategy
            if mode_strategy is None
            else mode_strategy
        )
        mode_path = self._select_mode_path(selected_strategy, mode_evidence)
        evidence = (
            tuple(np.ones_like(skill.mode_priors) for skill in self.skills)
            if mode_evidence is None
            else tuple(_as_float_array(value).copy() for value in mode_evidence)
        )
        first_skill = self.skills[0]
        missing = {
            name
            for name in first_skill.selected_frames
            if not name.startswith("virtual_skill_")
            and first_skill.streams[name].is_active(mode_path[0], 0)
            and name not in observation.frames
        }
        if missing:
            raise ValueError(
                f"Initial observation is missing selected task parameters: {sorted(missing)}"
            )
        self._skill_index = 0
        self._time_index = 0
        self._complete = False
        self._virtual_frames = {
            f"virtual_skill_{self.current_skill.label}": observation.ee_pose.copy()
        }
        self._pending_virtual_capture = False
        self._mode_strategy = selected_strategy
        self._mode_path = mode_path
        self._mode_evidence = evidence
        self._active_mode = self._mode_path[0]
        self._episode_initialized = True

    def _frame_pose(self, name: str, observation: DynaMACObservation) -> Array:
        if name.startswith("virtual_skill_"):
            if name in self._virtual_frames:
                return self._virtual_frames[name]

            if name in observation.frames:
                return observation.frames[name]
            raise RuntimeError(
                f"Virtual frame {name} has not been captured at a skill boundary or provided by the observation"
            )
        if name not in observation.frames:
            raise ValueError(f"Observation is missing selected task parameter {name}")
        return observation.frames[name]

    @staticmethod
    def _query_state_components(
        state_id: Any,
        mode_index: int | None,
    ) -> tuple[int, int, int | None]:

        if all(hasattr(state_id, name) for name in ("skill_index", "local_index")):
            values = (state_id.skill_index, state_id.local_index)
        else:
            try:
                raw_values = tuple(state_id)
            except TypeError as exc:
                raise TypeError(
                    "state_id must be a StateId or a two-element tuple"
                ) from exc
            if len(raw_values) != 2:
                raise ValueError(
                    "state_id must contain only skill_index and local_index"
                )
            values = raw_values
        if len(values) != 2:
            raise ValueError("state_id must contain skill_index and local_index")
        if any(
            isinstance(value, (bool, np.bool_))
            or not isinstance(value, (int, np.integer))
            for value in values
        ):
            raise TypeError("Both state_id components must be integers")
        mode = mode_index
        if mode is not None and (
            isinstance(mode, (bool, np.bool_))
            or not isinstance(mode, (int, np.integer))
        ):
            raise TypeError("mode_index must be an integer")
        return int(values[0]), int(values[1]), None if mode is None else int(mode)

    def query_state(
        self,
        observation: DynaMACObservation,
        state_id: Any,
        stream_weights: dict[str, float] | None = None,
        *,
        mode_index: int | None = None,
    ) -> DynaMACAction:

        if not self.fitted:
            raise RuntimeError("DynaMAC has not been fitted")
        if not self._episode_initialized:
            raise RuntimeError(
                "DynaMAC has not been reset; state queries are unavailable"
            )
        skill_index, index, mode = self._query_state_components(state_id, mode_index)
        if skill_index < 0 or skill_index >= len(self.skills):
            raise IndexError("state_id skill_index is out of range")
        skill = self.skills[skill_index]
        if mode is None:
            mode = self._mode_path[skill_index]
        if index < 0 or index >= skill.duration:
            raise IndexError("state_id local_index is out of range")
        if mode < 0 or mode >= len(skill.mode_priors):
            raise IndexError("state_id mode is out of range")
        if stream_weights is not None:
            unknown = set(stream_weights).difference(skill.selected_frames)
            if unknown:
                raise ValueError(
                    f"Stream weights include frames not modeled by the current skill: {sorted(unknown)}"
                )
            normalized_weights: dict[str, float] = {}
            for name, value in stream_weights.items():
                if (
                    isinstance(value, (bool, np.bool_))
                    or not isinstance(value, (int, float, np.integer, np.floating))
                    or not math.isfinite(float(value))
                    or float(value) < 0.0
                ):
                    raise ValueError(
                        "Stream precision weights must be finite nonnegative real numbers"
                    )
                normalized_weights[name] = float(value)
        else:
            normalized_weights = {}

        marginals: list[GaussianMarginal] = []
        precision_weights: list[float] = []
        inactive_frames: list[str] = []
        effective_weights: dict[str, float] = {}
        mask_indices: dict[str, int] = {}
        for name in skill.selected_frames:
            stream = skill.streams[name]
            mask_index = index
            if self.config.link_mask_scope == "skill_majority":
                active_mask = (
                    stream.active if stream.active.ndim == 1 else stream.active[mode]
                )
                availability_mask = (
                    stream.availability
                    if stream.availability.ndim == 1
                    else stream.availability[mode]
                )
                if not (
                    np.all(active_mask == active_mask[0])
                    and np.all(availability_mask == availability_mask[0])
                ):
                    raise RuntimeError(
                        "Schema 13 skill masks must be constant within a skill"
                    )
                mask_index = 0
            mask_indices[name] = mask_index
            selected = stream.is_selected(mode)
            weight = (
                float(stream.is_active(mode, mask_index))
                if stream_weights is None
                else normalized_weights.get(name, 0.0)
            )
            if not selected:
                weight = 0.0
            effective_weights[name] = weight
            if weight <= 0.0:
                inactive_frames.append(name)
                continue
            marginals.append(
                transform_marginal(
                    name,
                    self._frame_pose(name, observation),
                    stream.mean[mode, index],
                    stream.covariance[mode, index],
                    diagonalize=self.config.diagonalize_transformed_covariance,
                )
            )
            precision_weights.append(weight)
        if not marginals:
            raise RuntimeError(
                f"Skill {skill.label} has no positive-weight execution stream at state ({index}, {mode})"
            )
        pose, covariance, weights = product_of_experts(
            marginals,
            precision_weights=precision_weights,
        )
        gripper = skill.gripper[mode, index].copy()
        diagnostics = {
            "method": self.name,
            "selection_semantics_id": self.selection_semantics_id,
            "skill_index": skill_index,
            "skill_label": skill.label,
            "time_index": index,
            "duration": skill.duration,
            "mode": mode,
            "mode_prior": float(skill.mode_priors[mode]),
            "mode_evidence": float(self._mode_evidence[skill_index][mode]),
            "modal_path": list(self._mode_path),
            "path_probability_factor": (
                float(skill.mode_priors[mode])
                if skill_index == 0
                else float(
                    skill.transition_from_previous[
                        self._mode_path[skill_index - 1], mode
                    ]
                )
            ),
            "selected_frames": list(skill.selected_frames),
            "active_frames": [item.frame for item in marginals],
            "inactive_linked_frames": inactive_frames,
            "frame_status": {
                name: {
                    "selected_by_eq6_for_skill": skill.streams[name].is_selected(mode),
                    "exogenous_for_skill_by_eq5": (
                        skill.streams[name].is_available(mode, mask_indices[name])
                        if self.config.link_mask_scope == "skill_majority"
                        else None
                    ),
                    "exogenous_at_t_by_eq5": skill.streams[name].is_available(
                        mode, mask_indices[name]
                    ),
                    "participates_in_poe_at_t": effective_weights[name] > 0.0,
                }
                for name in skill.selected_frames
            },
            "marginal_means": {item.frame: item.mean.tolist() for item in marginals},
            "marginal_covariances": {
                item.frame: item.covariance.tolist() for item in marginals
            },
            "frame_poses": {
                item.frame: self._frame_pose(item.frame, observation).tolist()
                for item in marginals
            },
            "captured_virtual_frames": {
                name: pose.tolist() for name, pose in self._virtual_frames.items()
            },
            "poe_weights": weights,
            "joint_covariance": covariance.tolist(),
            "selection_mode": (
                "eq6_with_kinematic_analysis_disabled"
                if not self.config.kinematic_analysis_enabled
                else (
                    "eq6_per_skill_with_eq5_skill_mask"
                    if self.config.link_mask_scope == "skill_majority"
                    else (
                        "eq6_per_skill_with_majority_gated_eq5_framewise_participation"
                        if self.config.link_mask_scope == "skill_majority_gate_timestep"
                        else "eq6_per_skill_with_eq5_framewise_participation"
                    )
                )
            ),
            "kinematic_link_granularity": (
                "disabled_all_dynamic_candidates_available"
                if not self.config.kinematic_analysis_enabled
                else (
                    "offline_per_skill_strict_majority"
                    if self.config.link_mask_scope == "skill_majority"
                    else (
                        "offline_skill_majority_gate_then_raw_per_timestep"
                        if self.config.link_mask_scope == "skill_majority_gate_timestep"
                        else "offline_per_timestep_within_skill"
                    )
                )
            ),
            "kinematic_analysis_enabled": self.config.kinematic_analysis_enabled,
            "task_parameter_selection_granularity": "offline_per_skill_max_over_time",
            "online_link_detection": False,
            "query_advances_clock": False,
        }
        if stream_weights is not None:
            diagnostics.update(
                {
                    "requested_stream_weights": normalized_weights,
                    "effective_stream_weights": effective_weights,
                }
            )
        return DynaMACAction(
            pose=pose,
            covariance=covariance,
            gripper=gripper,
            diagnostics=diagnostics,
        )

    def preview_next_gripper(self) -> DynaMACGripperLookahead:

        if not self.fitted:
            raise RuntimeError("DynaMAC has not been fitted")
        if not self._episode_initialized:
            raise RuntimeError(
                "DynaMAC has not been reset; gripper preview is unavailable"
            )
        if len(self._mode_path) != len(self.skills):
            raise RuntimeError("DynaMAC episode mode path is incomplete")

        source_skill_index = self._skill_index
        source_skill = self.skills[source_skill_index]
        source_time_index = min(self._time_index, source_skill.duration - 1)
        crosses_boundary = False
        repeats_terminal = False
        if self._complete:
            next_skill_index = len(self.skills) - 1
            next_time_index = self.skills[next_skill_index].duration - 1
            repeats_terminal = True
        elif source_time_index + 1 < source_skill.duration:
            next_skill_index = source_skill_index
            next_time_index = source_time_index + 1
        elif source_skill_index + 1 < len(self.skills):
            next_skill_index = source_skill_index + 1
            next_time_index = 0
            crosses_boundary = True
        else:
            next_skill_index = source_skill_index
            next_time_index = source_skill.duration - 1
            repeats_terminal = True

        next_skill = self.skills[next_skill_index]
        next_mode = self._mode_path[next_skill_index]
        if next_mode < 0 or next_mode >= next_skill.gripper.shape[0]:
            raise RuntimeError("DynaMAC episode mode path references a missing mode")
        if (
            next_skill.duration < 1
            or next_skill.gripper.shape[1] != next_skill.duration
        ):
            raise RuntimeError("DynaMAC skill has an invalid gripper duration")
        return DynaMACGripperLookahead(
            gripper=next_skill.gripper[next_mode, next_time_index].copy(),
            crosses_skill_boundary=crosses_boundary,
            repeats_terminal=repeats_terminal,
            next_skill_index=next_skill_index,
            next_skill_label=next_skill.label,
            next_time_index=next_time_index,
            next_mode=next_mode,
        )

    def act(self, observation: DynaMACObservation) -> DynaMACAction:

        state = self._capture_runtime_state()
        try:
            return self._act_impl(observation)
        except Exception:
            self._restore_runtime_state(state)
            raise

    def _act_impl(self, observation: DynaMACObservation) -> DynaMACAction:

        if not self.fitted:
            raise RuntimeError("DynaMAC has not been fitted")
        if not self._episode_initialized:
            raise RuntimeError("DynaMAC has not been reset; act cannot execute")
        if self._complete:
            raise RuntimeError("DynaMAC has completed execution")
        if self._pending_virtual_capture:
            virtual_name = f"virtual_skill_{self.current_skill.label}"
            self._virtual_frames[virtual_name] = observation.ee_pose.copy()
            self._pending_virtual_capture = False
        skill = self.current_skill
        index = min(self._time_index, skill.duration - 1)
        action = self.query_state(
            observation,
            (self._skill_index, index),
            mode_index=self._active_mode,
        )
        self._time_index += 1
        if self._time_index >= skill.duration:
            if self._skill_index == len(self.skills) - 1:
                self._complete = True
            else:
                self._skill_index += 1
                self._time_index = 0

                self._pending_virtual_capture = True
                self._active_mode = self._mode_path[self._skill_index]
        return action

    def summary(self) -> dict[str, Any]:
        if not self.fitted:
            raise RuntimeError("DynaMAC has not been fitted")
        demonstration_count = self._validate_and_get_demonstration_count()
        return {
            "implementation": (
                "DynaMAC Algorithm 1 + task-parameterized MiDiGaP"
                if self.name == "dynamac"
                else "static-frame task-parameterized MiDiGaP baseline"
            ),
            "policy_type": self.name,
            "model_schema_version": MODEL_SCHEMA_VERSION,
            "selection_semantics_id": self.selection_semantics_id,
            "demonstration_count": demonstration_count,
            "tapas_reference_commit": TAPAS_REFERENCE_COMMIT,
            "config": asdict(self.config),
            "frame_names": list(self.frame_names),
            "skill_sequence": list(self.skill_sequence),
            "skills": [
                {
                    "label": skill.label,
                    "duration": skill.duration,
                    "modes": len(skill.mode_priors),
                    "mode_priors": skill.mode_priors.tolist(),
                    "mode_demonstration_indices": [
                        list(indices) for indices in skill.mode_demonstration_indices
                    ],
                    "transition_from_previous": (
                        None
                        if skill.transition_from_previous is None
                        else skill.transition_from_previous.tolist()
                    ),
                    "selected_frames": list(skill.selected_frames),
                    "active_fraction": {
                        name: float(np.mean(skill.streams[name].active))
                        for name in skill.selected_frames
                    },
                    "availability_fraction": {
                        name: float(np.mean(skill.streams[name].availability))
                        for name in skill.selected_frames
                    },
                    "selected_by_eq6": {
                        name: skill.streams[name].selected_by_eq6.tolist()
                        for name in skill.selected_frames
                    },
                    "link_diagnostics": skill.link_diagnostics,
                    "selection_scores": skill.selection_scores,
                }
                for skill in self.skills
            ],
        }

    def _validate_and_get_demonstration_count(self) -> int:

        demonstration_count: int | None = None
        for skill in self.skills:
            membership = skill.mode_demonstration_indices
            if (
                not membership
                or len(membership) != len(skill.mode_priors)
                or any(not members for members in membership)
            ):
                raise ValueError(
                    f"Mode demonstration memberships for skill {skill.label} must be nonempty and correspond one-to-one with modes"
                )
            flattened = [index for members in membership for index in members]
            if any(index < 0 for index in flattened) or len(set(flattened)) != len(
                flattened
            ):
                raise ValueError(
                    f"Mode demonstration memberships for skill {skill.label} contain negative or duplicate indices"
                )
            if sorted(flattened) != list(range(len(flattened))):
                raise ValueError(
                    f"Mode demonstration memberships for skill {skill.label} must cover contiguous indices completely"
                )
            if demonstration_count is None:
                demonstration_count = len(flattened)
            elif len(flattened) != demonstration_count:
                raise ValueError(
                    "Mode demonstration memberships for all skills must cover the same demonstrations"
                )
            expected_priors = np.asarray(
                [len(members) / len(flattened) for members in membership],
                dtype=np.float64,
            )
            if not np.all(np.isfinite(skill.mode_priors)) or not np.allclose(
                skill.mode_priors, expected_priors
            ):
                raise ValueError(
                    f"Mode priors for skill {skill.label} disagree with demonstration membership proportions"
                )
        if demonstration_count is None:
            raise ValueError("DynaMAC checkpoint contains no skills")
        return demonstration_count

    def identity_digest(self) -> str:
        payload = json.dumps(self.summary(), sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(payload.encode("utf-8"))
        for skill in self.skills:
            digest.update(skill.mode_priors.tobytes())
            digest.update(skill.gripper.tobytes())
            if skill.transition_from_previous is not None:
                digest.update(skill.transition_from_previous.tobytes())
            for name in skill.selected_frames:
                digest.update(skill.streams[name].mean.tobytes())
                digest.update(skill.streams[name].covariance.tobytes())
                digest.update(skill.streams[name].active.tobytes())
                digest.update(skill.streams[name].availability.tobytes())
                digest.update(skill.streams[name].selected_by_eq6.tobytes())
        return digest.hexdigest()

    @staticmethod
    def _array_key(skill_index: int, frame: str, field_name: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9_]", "_", frame)
        suffix = hashlib.sha256(frame.encode("utf-8")).hexdigest()[:8]
        return f"skill_{skill_index}__{safe}_{suffix}__{field_name}"

    def save(self, path: str | Path) -> None:

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        metadata = self.summary()
        metadata["identity_digest"] = self.identity_digest()
        arrays: dict[str, Array] = {}
        for index, skill in enumerate(self.skills):
            arrays[f"skill_{index}__mode_priors"] = skill.mode_priors
            arrays[f"skill_{index}__gripper"] = skill.gripper
            if skill.transition_from_previous is not None:
                arrays[f"skill_{index}__transition"] = skill.transition_from_previous
            for name, stream in skill.streams.items():
                arrays[self._array_key(index, name, "mean")] = stream.mean
                arrays[self._array_key(index, name, "covariance")] = stream.covariance
                arrays[self._array_key(index, name, "active")] = stream.active
                arrays[self._array_key(index, name, "availability")] = (
                    stream.availability
                )
                arrays[self._array_key(index, name, "selected_by_eq6")] = (
                    stream.selected_by_eq6
                )

        with path.open("wb") as checkpoint:
            np.savez_compressed(
                checkpoint,
                metadata_json=np.asarray(
                    json.dumps(metadata, ensure_ascii=False, sort_keys=True)
                ),
                **arrays,
            )

    @classmethod
    def load(cls, path: str | Path) -> DynaMAC:
        path = Path(path)
        with np.load(path, allow_pickle=False) as archive:
            metadata = json.loads(str(archive["metadata_json"].item()))
            schema = metadata.get("model_schema_version")
            if schema == 2:
                raise ValueError(
                    "DynaMAC schema 2 uses legacy full-angle/world-tangent mathematics and does not "
                    "store per-timestep active masks or virtual-frame history. Lossless migration is "
                    "not possible; refit from demonstrations with the current implementation."
                )
            if schema == 3:
                raise ValueError(
                    "DynaMAC schema 3 does not store auditable mode demonstration memberships; "
                    f"refit from demonstrations as schema {MODEL_SCHEMA_VERSION} with the current implementation."
                )
            if schema in {4, 5}:
                raise ValueError(
                    f"DynaMAC schema {schema} predates the current preliminary-analysis/product-"
                    "clustering configuration semantics, so its content digest cannot be verified; "
                    f"refit from demonstrations as schema {MODEL_SCHEMA_VERSION}."
                )
            if schema == 6:
                raise ValueError(
                    "DynaMAC schema 6 excludes the global gripper action from the modal "
                    "product and can correlate both arms through the same seed; refit the "
                    f"demonstrations with schema {MODEL_SCHEMA_VERSION}"
                )
            if schema == 7:
                raise ValueError(
                    "DynaMAC schema 7 does not store the dimensional scale between the "
                    "gripper Euclidean factor and pose factors; refit the demonstrations "
                    f"with schema {MODEL_SCHEMA_VERSION}"
                )
            if schema == 9:
                raise ValueError(
                    "DynaMAC schema 9 silently mixes diagonal empirical covariance with "
                    "additive ridge regularization; refit the demonstrations with schema "
                    f"{MODEL_SCHEMA_VERSION}"
                )
            if schema == 10:
                raise ValueError(
                    "DynaMAC schema 10 predates the current Eq. (5)/Eq. (6) candidate "
                    "filtering and empty-denominator rules; refit the demonstrations with "
                    f"schema {MODEL_SCHEMA_VERSION}"
                )
            if schema == 11:
                raise ValueError(
                    "DynaMAC schema 11 applies Eq. (6) normalization to candidates "
                    "unavailable under Eq. (5) and applies availability only in the final "
                    "PoE; refit "
                    f"the demonstrations with schema {MODEL_SCHEMA_VERSION}"
                )
            if schema == 12:
                raise ValueError(
                    "DynaMAC schema 12 uses per-timestep link masks, full-pose Eq. (5), "
                    "a next-action policy stream, and default modal partitioning; refit "
                    "the demonstrations with the current schema"
                )
            if schema != MODEL_SCHEMA_VERSION:
                raise ValueError("unsupported DynaMAC checkpoint schema")
            expected_policy_type = cls.name
            if metadata.get("policy_type") != expected_policy_type:
                raise ValueError(
                    f"checkpoint policy type {metadata.get('policy_type')!r} cannot be "
                    f"loaded by {expected_policy_type!r}"
                )
            config = DynaMACConfig(**metadata["config"])
            if metadata.get("selection_semantics_id") != _selection_semantics_id(
                config
            ):
                raise ValueError(
                    "DynaMAC checkpoint task-parameter selection semantics_id does not match"
                )
            policy = cls(config)
            policy.frame_names = tuple(metadata["frame_names"])
            policy.skill_sequence = tuple(
                int(value) for value in metadata["skill_sequence"]
            )
            for index, skill_meta in enumerate(metadata["skills"]):
                if "mode_demonstration_indices" not in skill_meta:
                    raise ValueError(
                        "DynaMAC checkpoint is missing mode demonstration memberships"
                    )
                selected = tuple(skill_meta["selected_frames"])
                streams = {}
                for name in selected:
                    streams[name] = StreamModel(
                        name,
                        archive[policy._array_key(index, name, "mean")].copy(),
                        archive[policy._array_key(index, name, "covariance")].copy(),
                        archive[policy._array_key(index, name, "active")].copy(),
                        archive[policy._array_key(index, name, "availability")].copy(),
                        archive[
                            policy._array_key(index, name, "selected_by_eq6")
                        ].copy(),
                    )
                policy.skills.append(
                    SkillModel(
                        label=int(skill_meta["label"]),
                        duration=int(skill_meta["duration"]),
                        selected_frames=selected,
                        mode_priors=archive[f"skill_{index}__mode_priors"].copy(),
                        streams=streams,
                        gripper=archive[f"skill_{index}__gripper"].copy(),
                        transition_from_previous=(
                            None
                            if index == 0
                            else archive[f"skill_{index}__transition"].copy()
                        ),
                        mode_demonstration_indices=tuple(
                            tuple(int(value) for value in indices)
                            for indices in skill_meta["mode_demonstration_indices"]
                        ),
                        link_diagnostics=skill_meta["link_diagnostics"],
                        selection_scores={
                            name: float(value)
                            for name, value in skill_meta["selection_scores"].items()
                        },
                    )
                )
        if policy._validate_and_get_demonstration_count() != metadata.get(
            "demonstration_count"
        ):
            raise ValueError(
                "DynaMAC checkpoint demonstration count disagrees with mode memberships"
            )
        stored_digest = metadata.get("identity_digest", metadata.get("fingerprint"))
        if policy.identity_digest() != stored_digest:
            raise ValueError("DynaMAC checkpoint content digest does not match")
        return policy


@dataclass(frozen=True)
class BimanualDynaMACAction:
    left: DynaMACAction
    right: DynaMACAction


@dataclass(frozen=True)
class BimanualDynaMACGripperLookahead:

    left: DynaMACGripperLookahead
    right: DynaMACGripperLookahead


def synchronized_bimanual_demonstrations(
    left_demonstrations: Sequence[DynaMACDemonstration],
    right_demonstrations: Sequence[DynaMACDemonstration],
) -> tuple[list[DynaMACDemonstration], list[DynaMACDemonstration]]:

    if len(left_demonstrations) != len(right_demonstrations):
        raise ValueError("Left- and right-arm demonstration counts must match")
    if not left_demonstrations:
        raise ValueError("Bimanual DynaMAC requires at least one demonstration pair")
    paired_left = []
    paired_right = []
    for left_demo, right_demo in zip(
        left_demonstrations, right_demonstrations, strict=True
    ):
        if len(left_demo.ee_pose) != len(right_demo.ee_pose):
            raise ValueError(
                "Paired bimanual demonstrations must be aligned timestep by timestep"
            )
        left_frames = {
            name: value
            for name, value in left_demo.frames.items()
            if name != "right_ee"
        }
        left_frames["right_ee"] = right_demo.ee_pose
        right_frames = {
            name: value
            for name, value in right_demo.frames.items()
            if name != "left_ee"
        }
        right_frames["left_ee"] = left_demo.ee_pose
        paired_left.append(
            DynaMACDemonstration(
                ee_pose=left_demo.ee_pose,
                action_pose=left_demo.action_pose,
                gripper=left_demo.gripper,
                frames=left_frames,
                skill=left_demo.skill,
                name=left_demo.name,
                entity_configurations=left_demo.entity_configurations,
                scene_entity_poses=left_demo.scene_entity_poses,
                structural_bindings=left_demo.structural_bindings,
            )
        )
        paired_right.append(
            DynaMACDemonstration(
                ee_pose=right_demo.ee_pose,
                action_pose=right_demo.action_pose,
                gripper=right_demo.gripper,
                frames=right_frames,
                skill=right_demo.skill,
                name=right_demo.name,
                entity_configurations=right_demo.entity_configurations,
                scene_entity_poses=right_demo.scene_entity_poses,
                structural_bindings=right_demo.structural_bindings,
            )
        )
    return paired_left, paired_right


class BimanualDynaMAC:

    def __init__(
        self,
        left: DynaMAC | None = None,
        right: DynaMAC | None = None,
        config: DynaMACConfig = DynaMACConfig(),
    ) -> None:
        if left is not None and left is right:
            raise ValueError("the two arms must use two independent DynaMAC instances")

        child_sequences = np.random.SeedSequence(config.random_seed).spawn(2)
        child_seeds = tuple(
            int(sequence.generate_state(1, dtype=np.uint64)[0])
            for sequence in child_sequences
        )
        self.left = (
            left
            if left is not None
            else DynaMAC(replace(config, random_seed=child_seeds[0]))
        )
        self.right = (
            right
            if right is not None
            else DynaMAC(replace(config, random_seed=child_seeds[1]))
        )
        if self.left.config.random_seed == self.right.config.random_seed:
            raise ValueError(
                "independent arm policies cannot use the same random stream seed"
            )
        self._last_left_action: DynaMACAction | None = None
        self._last_right_action: DynaMACAction | None = None

    def fit(
        self,
        left_demonstrations: Sequence[DynaMACDemonstration],
        right_demonstrations: Sequence[DynaMACDemonstration],
    ) -> BimanualDynaMAC:
        paired_left, paired_right = synchronized_bimanual_demonstrations(
            left_demonstrations,
            right_demonstrations,
        )
        left_model = (self.left.frame_names, self.left.skill_sequence, self.left.skills)
        right_model = (
            self.right.frame_names,
            self.right.skill_sequence,
            self.right.skills,
        )
        left_runtime = self.left._capture_runtime_state()
        right_runtime = self.right._capture_runtime_state()
        try:
            self.left.fit(paired_left)
            self.right.fit(paired_right)
        except Exception:
            self.left.frame_names, self.left.skill_sequence, self.left.skills = (
                left_model
            )
            self.right.frame_names, self.right.skill_sequence, self.right.skills = (
                right_model
            )
            self.left._restore_runtime_state(left_runtime)
            self.right._restore_runtime_state(right_runtime)
            raise
        self._last_left_action = None
        self._last_right_action = None
        return self

    @property
    def complete(self) -> bool:
        return self.left.complete and self.right.complete

    def reset(
        self,
        left_observation: DynaMACObservation,
        right_observation: DynaMACObservation,
        mode_strategy: Literal["map", "sample"] | None = None,
        mode_evidence: Sequence[Array] | None = None,
        *,
        left_mode_evidence: Sequence[Array] | None = None,
        right_mode_evidence: Sequence[Array] | None = None,
    ) -> None:

        if mode_evidence is not None and (
            left_mode_evidence is not None or right_mode_evidence is not None
        ):
            raise ValueError(
                "Shared and per-arm mode evidence cannot be provided simultaneously"
            )
        if mode_evidence is not None:
            left_mode_evidence = mode_evidence
            right_mode_evidence = mode_evidence
        left_snapshot, right_snapshot = self._synchronous_observations(
            left_observation, right_observation
        )
        left_state = self.left._capture_runtime_state()
        right_state = self.right._capture_runtime_state()
        try:
            self.left.reset(left_snapshot, mode_strategy, left_mode_evidence)
            self.right.reset(right_snapshot, mode_strategy, right_mode_evidence)
        except Exception:
            self.left._restore_runtime_state(left_state)
            self.right._restore_runtime_state(right_state)
            raise
        self._last_left_action = None
        self._last_right_action = None

    def restart_current_skill_reference(self) -> dict[str, tuple[int, int]]:

        result = {
            "left": self.left.restart_current_skill_reference(),
            "right": self.right.restart_current_skill_reference(),
        }
        self._last_left_action = None
        self._last_right_action = None
        return result

    @staticmethod
    def _synchronous_observations(
        left_observation: DynaMACObservation,
        right_observation: DynaMACObservation,
    ) -> tuple[DynaMACObservation, DynaMACObservation]:

        left_frames = {**left_observation.frames, "right_ee": right_observation.ee_pose}
        right_frames = {**right_observation.frames, "left_ee": left_observation.ee_pose}
        return (
            DynaMACObservation(left_observation.ee_pose, left_frames),
            DynaMACObservation(right_observation.ee_pose, right_frames),
        )

    def preview_next_gripper(self) -> BimanualDynaMACGripperLookahead:

        return BimanualDynaMACGripperLookahead(
            left=self.left.preview_next_gripper(),
            right=self.right.preview_next_gripper(),
        )

    def act(
        self,
        left_observation: DynaMACObservation,
        right_observation: DynaMACObservation,
    ) -> BimanualDynaMACAction:
        if self.complete:
            raise RuntimeError("bimanual DynaMAC has completed")
        left_snapshot, right_snapshot = self._synchronous_observations(
            left_observation, right_observation
        )

        left_state = self.left._capture_runtime_state()
        right_state = self.right._capture_runtime_state()
        try:
            left_action = (
                self._completed_hold_action(self._last_left_action, "left")
                if self.left.complete
                else self.left.act(left_snapshot)
            )
            right_action = (
                self._completed_hold_action(self._last_right_action, "right")
                if self.right.complete
                else self.right.act(right_snapshot)
            )
        except Exception:
            self.left._restore_runtime_state(left_state)
            self.right._restore_runtime_state(right_state)
            raise
        self._last_left_action = left_action
        self._last_right_action = right_action
        return BimanualDynaMACAction(left=left_action, right=right_action)

    @staticmethod
    def _completed_hold_action(
        previous: DynaMACAction | None,
        arm: str,
    ) -> DynaMACAction:

        if previous is None:
            raise RuntimeError(
                f"{arm} DynaMAC has completed without a final action to hold"
            )
        diagnostics = deepcopy(previous.diagnostics)
        diagnostics["complete_hold"] = True
        return DynaMACAction(
            pose=previous.pose.copy(),
            covariance=previous.covariance.copy(),
            gripper=previous.gripper.copy(),
            diagnostics=diagnostics,
        )


DynaMACPolicy = DynaMAC


__all__ = [
    "BimanualDynaMAC",
    "BimanualDynaMACAction",
    "BimanualDynaMACGripperLookahead",
    "DynaMAC",
    "DynaMACAction",
    "DynaMACConfig",
    "DynaMACDemonstration",
    "DynaMACGripperLookahead",
    "DynaMACObservation",
    "DynaMACPolicy",
    "GaussianMarginal",
    "geometric_mean_standard_deviation",
    "interpolate_poses",
    "normalize_quaternion",
    "pose_compose",
    "pose_exp_world",
    "pose_inverse",
    "pose_log_world",
    "pose_log_nearest",
    "product_of_experts",
    "relative_pose",
    "static_task_parameter_score_details",
    "static_task_parameter_scores",
    "synchronized_bimanual_demonstrations",
    "task_parameter_score_details",
    "task_parameter_scores",
    "transform_marginal",
]
