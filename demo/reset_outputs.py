"""Replace artifacts from a previous demo run."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def _remove_within(root: Path, target: Path) -> None:
    root = root.resolve()
    target = target.resolve()
    target.relative_to(root)
    if target == root:
        raise ValueError("refusing to remove the complete output root")
    if target.exists():
        print(f"Replacing previous demo artifacts: {target}", flush=True)
        shutil.rmtree(target)


def reset(output_root: Path, stage: str, demo: str) -> None:
    output_root = output_root.resolve()
    if output_root.name != "outputs":
        raise ValueError(f"expected an outputs directory, got: {output_root}")

    if stage == "offline":

        targets = (
            output_root / "offline",
            output_root / "models" / "litetwin" / demo,
            output_root / "online",
        )
    elif stage == "online":
        targets = (output_root / "online",)
    else:
        raise ValueError(stage)

    for target in targets:
        _remove_within(output_root, target)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--stage", choices=("offline", "online"), required=True)
    parser.add_argument("--demo", default="open_drawer")
    args = parser.parse_args()
    reset(args.output_root, args.stage, args.demo)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
