"""Lossless-enough JSON diagnostics for every LiteTwin control tick."""

from __future__ import annotations

import json
from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

import numpy as np


def json_ready(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {
            field.name: json_ready(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, Mapping):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [json_ready(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


class DiagnosticRecorder:

    def __init__(self) -> None:
        self._records: list[dict[str, Any]] = []

    @property
    def records(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._records)

    def reset(self) -> None:
        self._records.clear()

    def truncate(self, length: int) -> None:
        if length < 0 or length > len(self._records):
            raise ValueError("Invalid diagnostic truncation length")
        del self._records[length:]

    def append(self, record: Mapping[str, Any]) -> dict[str, Any]:
        ready = json_ready(record)
        if not isinstance(ready, dict):
            raise TypeError("Per-cycle diagnostics must be objects")
        self._records.append(ready)
        return ready

    def annotate_last(self, key: str, value: Any) -> None:

        if not self._records:
            raise RuntimeError(
                "No per-cycle diagnostic is available for appending a commit result"
            )
        if not isinstance(key, str) or not key:
            raise ValueError("Diagnostic extension keys must be nonempty strings")
        if key in self._records[-1]:
            raise ValueError(f"Diagnostic extension key already exists: {key}")
        self._records[-1][key] = json_ready(value)

    def save(self, path: str | Path) -> None:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            "".join(
                json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
                for record in self._records
            ),
            encoding="utf-8",
        )


def state_token(value: Any) -> str | None:
    if value is None:
        return None
    return f"k{value.skill_index}:t{value.local_index}"


__all__ = ["DiagnosticRecorder", "json_ready", "state_token"]
