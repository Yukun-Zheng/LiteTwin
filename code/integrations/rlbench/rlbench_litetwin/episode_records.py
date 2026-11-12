"""Write cycle-level observations and terminal episode summaries."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path
from typing import Mapping


EPISODE_SCHEMA = "litetwin.demo-episode.v1"


def atomic_json(path: Path, value: Mapping) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class EpisodeWriter:

    def __init__(self, output_root: Path, episode_id: str):
        safe = episode_id.replace("/", "__")
        self.cycles_path = Path(output_root) / "cycles" / f"{safe}.jsonl.gz"
        self.episode_path = Path(output_root) / "episodes" / f"{safe}.json"
        self.temporary_cycles = self.cycles_path.with_name(
            self.cycles_path.name + ".tmp"
        )
        self.temporary_cycles.parent.mkdir(parents=True, exist_ok=True)
        self._stream = gzip.open(self.temporary_cycles, "wt", encoding="utf-8")
        self._count = 0

    def write_cycle(
        self, cycle: int, feature: Mapping, audit: Mapping, execution: Mapping
    ):
        self._stream.write(
            json.dumps(
                {
                    "cycle": int(cycle),
                    "feature": dict(feature),
                    "audit": dict(audit),
                    "execution": dict(execution),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n"
        )
        self._count += 1

    def finalize(self, summary: Mapping):
        self._stream.close()
        os.replace(self.temporary_cycles, self.cycles_path)
        payload = dict(summary)
        payload["schema"] = EPISODE_SCHEMA
        payload["cycle_records"] = self._count
        payload["cycle_file"] = os.path.relpath(
            self.cycles_path.resolve(), self.episode_path.parent.resolve()
        )
        payload["cycle_file_sha256"] = _sha256(self.cycles_path)
        atomic_json(self.episode_path, payload)
        return self.episode_path

    def abort(self):
        if not self._stream.closed:
            self._stream.close()


__all__ = ["EPISODE_SCHEMA", "EpisodeWriter", "atomic_json"]
