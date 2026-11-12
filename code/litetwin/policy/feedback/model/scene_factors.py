"""Sparse entity-configuration distributions for progress and boundary models."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

import numpy as np

from ...dynamac import _fit_pose_sequence, pose_log_nearest

Array = np.ndarray
FactorKind = Literal["node", "edge"]
FactorSpace = Literal["se3", "euclidean"]


@dataclass(frozen=True, order=True)
class FactorId:

    kind: FactorKind
    source: str
    target: str | None = None
    feature: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in {"node", "edge"}:
            raise ValueError("Scene-factor kind must be node or edge")
        if not isinstance(self.source, str) or not self.source:
            raise ValueError("Scene-factor source must be a nonempty string")
        if self.kind == "node":
            if self.target is not None:
                raise ValueError("A node factor cannot contain a target")
            if not isinstance(self.feature, str) or not self.feature:
                raise ValueError(
                    "A node factor must identify an entity configuration field"
                )
        elif (
            not isinstance(self.target, str)
            or not self.target
            or self.target == self.source
            or self.feature is not None
        ):
            raise ValueError(
                "An edge factor must connect distinct entities and cannot contain a feature"
            )

    @property
    def token(self) -> str:
        return (
            f"node:{self.source}#{self.feature}"
            if self.kind == "node"
            else f"edge:{self.source}->{self.target}"
        )

    @property
    def entities(self) -> tuple[str, ...]:
        return (self.source,) if self.target is None else (self.source, self.target)

    @classmethod
    def from_token(cls, value: str) -> FactorId:
        if value.startswith("node:") and "#" in value:
            source, feature = value.removeprefix("node:").rsplit("#", 1)
            return cls("node", source, feature=feature)
        if value.startswith("edge:") and "->" in value:
            source, target = value.removeprefix("edge:").split("->", 1)
            return cls("edge", source, target)
        raise ValueError(f"Unknown scene-factor identifier: {value}")


@dataclass(frozen=True)
class FactorDistribution:

    mean: Array
    covariance: Array
    sample_count: int
    space: FactorSpace = "se3"
    observability: float = 1.0
    stable_fraction: float = 1.0
    loo_gain: float = 0.0
    loo_accuracy: float = 0.0
    neighborhood_radius: int = 0

    def __post_init__(self) -> None:
        mean = np.asarray(self.mean, dtype=np.float64)
        covariance = np.asarray(self.covariance, dtype=np.float64)
        if self.space == "se3":
            if mean.shape != (7,) or covariance.shape != (6, 6):
                raise ValueError(
                    "SE(3) scene factors require a [7] mean and [6, 6] covariance"
                )
        elif self.space == "euclidean":
            if (
                mean.ndim != 1
                or len(mean) == 0
                or covariance.shape != (len(mean), len(mean))
            ):
                raise ValueError(
                    "Euclidean scene factors require a [D] mean and [D, D] covariance"
                )
        else:
            raise ValueError("Scene-factor space must be se3 or euclidean")
        if not np.all(np.isfinite(mean)) or not np.all(np.isfinite(covariance)):
            raise ValueError("Scene-factor distribution contains nonfinite values")
        if self.sample_count < 1:
            raise ValueError("Scene-factor distribution requires at least one sample")
        if not 0.0 <= self.observability <= 1.0:
            raise ValueError("Scene-factor observability must be in [0, 1]")
        if not 0.0 <= self.stable_fraction <= 1.0:
            raise ValueError("Scene-factor stable support must be in [0, 1]")
        if not 0.0 <= self.loo_accuracy <= 1.0:
            raise ValueError(
                "Scene-factor leave-one-out positive-gain rate must be in [0, 1]"
            )
        if self.neighborhood_radius not in {0, 1, 2}:
            raise ValueError("Scene-factor neighborhood radius must be 0, 1, or 2")
        object.__setattr__(self, "mean", mean.copy())
        object.__setattr__(self, "covariance", covariance.copy())

    def log_likelihood(self, value: Array) -> float:
        current = np.asarray(value, dtype=np.float64)
        residual = (
            pose_log_nearest(self.mean, current)
            if self.space == "se3"
            else current - self.mean
        )
        if residual.shape != (self.covariance.shape[0],):
            raise ValueError(
                "Scene-factor observation dimension does not match the distribution"
            )
        sign, logdet = np.linalg.slogdet(self.covariance)
        if sign <= 0.0:
            raise RuntimeError("Scene-factor covariance is not positive definite")
        try:
            precision_residual = np.linalg.solve(self.covariance, residual)
        except np.linalg.LinAlgError:
            precision_residual = np.linalg.solve(
                self.covariance + np.eye(len(residual)) * 1.0e-8,
                residual,
            )
        dimension = len(residual)
        return float(
            -0.5
            * (
                residual @ precision_residual
                + logdet
                + dimension * math.log(2.0 * math.pi)
            )
        )

    def mahalanobis_squared(
        self,
        value: Array,
        observation_covariance: Array | None = None,
    ) -> float:

        current = np.asarray(value, dtype=np.float64)
        residual = (
            pose_log_nearest(self.mean, current)
            if self.space == "se3"
            else current - self.mean
        )
        covariance = self.covariance.copy()
        if observation_covariance is not None:
            observed = np.asarray(observation_covariance, dtype=np.float64)
            if observed.shape != covariance.shape:
                raise ValueError(
                    "Scene-factor observation covariance dimension does not match"
                )
            covariance += observed
        covariance += np.eye(len(residual), dtype=np.float64) * 1.0e-12
        try:
            solved = np.linalg.solve(covariance, residual)
        except np.linalg.LinAlgError:
            solved = np.linalg.pinv(covariance) @ residual
        return max(0.0, float(residual @ solved))

    def compatibility(
        self,
        value: Array,
        observation_covariance: Array | None = None,
    ) -> float:

        return float(
            math.exp(
                -0.5
                * min(
                    self.mahalanobis_squared(value, observation_covariance),
                    1500.0,
                )
            )
        )


def fit_factor_distribution(
    samples: Array,
    *,
    position_variance_floor: float,
    rotation_variance_floor: float,
    covariance_estimation_method: str,
    space: FactorSpace = "se3",
    observability: float = 1.0,
    stable_fraction: float = 1.0,
    loo_gain: float = 0.0,
    loo_accuracy: float = 0.0,
    neighborhood_radius: int = 0,
) -> FactorDistribution:

    values = np.asarray(samples, dtype=np.float64)
    if values.ndim != 2 or len(values) < 1:
        raise ValueError("Scene-factor samples must be a nonempty [N, D] array")
    if space == "se3":
        if values.shape[1] != 7:
            raise ValueError("SE(3) scene-factor samples must have shape [N, 7]")
        mean, covariance = _fit_pose_sequence(
            values[:, None, :],
            position_variance_floor,
            rotation_variance_floor,
            covariance_estimation_method=covariance_estimation_method,
        )
        fitted_mean = mean[0]
        fitted_covariance = covariance[0]
    elif space == "euclidean":
        if values.shape[1] == 0 or not np.all(np.isfinite(values)):
            raise ValueError(
                "Euclidean scene-factor samples must be a finite [N, D] array"
            )
        fitted_mean = np.mean(values, axis=0)
        residual = values - fitted_mean
        denominator = max(1, len(values) - 1)
        fitted_covariance = residual.T @ residual / denominator
        fitted_covariance += np.eye(values.shape[1]) * position_variance_floor
    else:
        raise ValueError("Scene-factor space must be se3 or euclidean")
    return FactorDistribution(
        mean=fitted_mean,
        covariance=fitted_covariance,
        sample_count=len(values),
        space=space,
        observability=float(observability),
        stable_fraction=float(stable_fraction),
        loo_gain=float(loo_gain),
        loo_accuracy=float(loo_accuracy),
        neighborhood_radius=int(neighborhood_radius),
    )


__all__ = [
    "FactorDistribution",
    "FactorId",
    "FactorKind",
    "FactorSpace",
    "fit_factor_distribution",
]
