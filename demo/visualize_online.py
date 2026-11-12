"""Render a data-faithful LiteTwin execution timeline from cycle records."""

from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
from typing import Any

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np


INK = "#17324D"
MUTED = "#5D6A75"
GRID = "#D7E0E7"
MODE_COLORS = {
    "task": "#4477AA",
    "verify_link": "#AA4499",
    "recovery": "#EEBB55",
    "reentry": "#7B2CBF",
}
RELATION_COLORS = {"external": "#B8C2CC", "linked": "#228833", "unknown": "#E7E7E7"}
EVENT_COLORS = {
    "relation_loss": "#A23B4A",
    "recovery": "#9A5A00",
    "reentry": "#6432A8",
}


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(path)
    return value


def _cycles(records_root: Path) -> list[dict[str, Any]]:
    paths = sorted((records_root / "cycles").glob("*.jsonl.gz"))
    if len(paths) != 1:
        raise RuntimeError(f"expected one cycle record, found {len(paths)}")
    with gzip.open(paths[0], "rt", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def _configure() -> None:
    mpl.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans", "sans-serif"],
            "font.size": 8.0,
            "axes.labelsize": 8.5,
            "axes.titlesize": 10.0,
            "xtick.labelsize": 7.5,
            "ytick.labelsize": 7.5,
            "legend.fontsize": 7.5,
            "axes.spines.right": False,
            "axes.spines.top": False,
            "svg.fonttype": "none",
            "pdf.fonttype": 42,
        }
    )


def _runs(values: list[str]) -> list[tuple[int, int, str]]:
    if not values:
        return []
    result = []
    start = 0
    for index in range(1, len(values) + 1):
        if index == len(values) or values[index] != values[start]:
            result.append((start, index, values[start]))
            start = index
    return result


def render(records_root: Path, model_summary: Path, output_stem: Path) -> None:
    _configure()
    rows = _cycles(records_root)
    if not rows:
        raise RuntimeError("online cycle record is empty")
    model = _load(model_summary)
    counts = {
        int(key): int(value)
        for key, value in model["extracted_task_structure"][
            "skill_state_counts"
        ].items()
    }
    offsets: dict[int, int] = {}
    cursor = 0
    for skill in sorted(counts):
        offsets[skill] = cursor
        cursor += counts[skill]

    cycles = np.asarray([int(row["cycle"]) for row in rows], dtype=int)
    progress = []
    modes: list[str] = []
    relations: list[str] = []
    fault_cycle = None
    reentry_cycles = []
    for row in rows:
        state = row.get("feature", {}).get("policy_state", {})
        reference = state.get("reference_state_if_available", {})
        skill = reference.get("skill")
        local = reference.get("progress")
        progress.append(
            np.nan
            if skill is None or local is None
            else offsets.get(int(skill), 0) + int(local)
        )
        arm = (
            row.get("execution", {})
            .get("policy_audit", {})
            .get("arms", {})
            .get("single", {})
        )
        modes.append(str(arm.get("mode_after", "task")).lower())
        if arm.get("reentry_committed"):
            reentry_cycles.append(int(row["cycle"]))
        relation = row.get("audit", {}).get("physical_relation", {}).get("single")
        relations.append(str(relation or "unknown").lower())
        injector = row.get("execution", {}).get("injector", {})
        if fault_cycle is None and injector.get("effect_policy_step") is not None:
            fault_cycle = int(injector["effect_policy_step"])

    mode_runs = _runs(modes)
    relation_runs = _runs(relations)
    recovery_starts = [start for start, _, value in mode_runs if value == "recovery"]

    fig = plt.figure(figsize=(7.15, 4.65), constrained_layout=False)
    grid = fig.add_gridspec(
        3,
        1,
        height_ratios=(3.0, 0.52, 0.52),
        left=0.15,
        right=0.97,
        top=0.77,
        bottom=0.16,
        hspace=0.25,
    )
    ax = fig.add_subplot(grid[0])
    ax_mode = fig.add_subplot(grid[1], sharex=ax)
    ax_relation = fig.add_subplot(grid[2], sharex=ax)

    fig.suptitle(
        "Online task-state feedback during one perturbed rollout",
        x=0.15,
        y=0.955,
        ha="left",
        fontsize=13,
        fontweight="bold",
        color=INK,
    )
    fig.text(
        0.15,
        0.895,
        "Task-state reference, controller mode, and audited physical relation.",
        ha="left",
        fontsize=9.2,
        color="#425466",
    )

    ax.step(cycles, progress, where="post", color="#2F6FA3", linewidth=1.8)
    for start, stop, value in mode_runs:
        if value == "recovery":
            ax.axvspan(start, stop, color=MODE_COLORS[value], alpha=0.16, linewidth=0)
    for skill, start in offsets.items():
        ax.axhline(start, color=GRID, linewidth=0.7, zorder=0)
    ax.set_yticks([offsets[skill] for skill in sorted(offsets)])
    ax.set_yticklabels([f"Skill {skill}" for skill in sorted(offsets)])
    ax.set_ylabel("Task-state index")
    ax.set_ylim(-2, max(value for value in progress if np.isfinite(value)) + 6)
    ax.set_xlim(0, int(cycles[-1]) + 1)
    ax.yaxis.grid(False)
    ax.xaxis.grid(True, color=GRID, linewidth=0.5)
    ax.tick_params(axis="x", labelbottom=False)
    ax.spines["left"].set_color(GRID)
    ax.spines["bottom"].set_color(GRID)
    ax.text(-0.11, 1.06, "a", transform=ax.transAxes, fontsize=10, fontweight="bold")
    event_specs = []
    if fault_cycle is not None:
        event_specs.append(
            (
                fault_cycle,
                "relation loss",
                EVENT_COLORS["relation_loss"],
                "center",
                0.0,
                1.025,
            )
        )
    event_specs.extend(
        (
            cycle,
            "recovery",
            EVENT_COLORS["recovery"],
            "center",
            0.0,
            1.025,
        )
        for cycle in recovery_starts
    )
    event_specs.extend(
        (
            cycle,
            "legal re-entry",
            EVENT_COLORS["reentry"],
            "center",
            0.0,
            1.025,
        )
        for cycle in reentry_cycles
    )
    for cycle, label, color, anchor, offset, label_y in event_specs:
        ax.axvline(cycle, ymax=0.98, color=color, linestyle=(0, (3, 3)), linewidth=1.0)
        if label in {"relation loss", "legal re-entry"}:
            ax.annotate(
                label,
                xy=(cycle, label_y),
                xycoords=("data", "axes fraction"),
                xytext=(-10, 0),
                textcoords="offset points",
                color=color,
                fontsize=8.2,
                fontweight="bold",
                ha="right",
                va="bottom",
            )
            ax.text(
                cycle,
                label_y,
                str(cycle),
                transform=ax.get_xaxis_transform(),
                color=color,
                fontsize=8.2,
                fontweight="bold",
                ha="center",
                va="bottom",
            )
        else:
            ax.text(
                cycle + offset,
                label_y,
                f"{label}\n{cycle}",
                transform=ax.get_xaxis_transform(),
                color=color,
                fontsize=8.2,
                fontweight="bold",
                ha=anchor,
                va="bottom",
            )

    for start, stop, value in mode_runs:
        color = MODE_COLORS.get(value, "#999999")
        ax_mode.broken_barh(
            [(start, stop - start)], (0, 1), facecolors=color, edgecolors="none"
        )
        if stop - start >= 10:
            ax_mode.text(
                (start + stop) / 2,
                0.5,
                value.replace("_", " ").title(),
                ha="center",
                va="center",
                fontsize=7.5,
                fontweight="medium",
                color="white" if value == "task" else INK,
            )
    for cycle in reentry_cycles:
        ax_mode.plot(
            cycle,
            0.5,
            marker="D",
            markersize=5,
            color=MODE_COLORS["reentry"],
            markeredgecolor="white",
            markeredgewidth=0.6,
        )
    ax_mode.set_yticks([0.5])
    ax_mode.set_yticklabels(["Controller mode"])
    ax_mode.tick_params(axis="x", labelbottom=False)
    ax_mode.tick_params(axis="y", length=0)
    ax_mode.set_ylim(0, 1)
    for spine in ax_mode.spines.values():
        spine.set_visible(False)
    ax_mode.text(
        -0.11, 0.90, "b", transform=ax_mode.transAxes, fontsize=10, fontweight="bold"
    )

    for start, stop, value in relation_runs:
        color = RELATION_COLORS.get(value, RELATION_COLORS["unknown"])
        ax_relation.broken_barh(
            [(start, stop - start)], (0, 1), facecolors=color, edgecolors="none"
        )
        if stop - start >= 14:
            ax_relation.text(
                (start + stop) / 2,
                0.5,
                value.title(),
                ha="center",
                va="center",
                fontsize=7.5,
                fontweight="medium",
                color="white" if value == "linked" else INK,
            )
    ax_relation.set_yticks([0.5])
    ax_relation.set_yticklabels(["Physical relation"])
    ax_relation.set_xlabel("Control cycle")
    ax_relation.tick_params(axis="y", length=0)
    ax_relation.set_ylim(0, 1)
    for spine in ax_relation.spines.values():
        spine.set_visible(False)
    ax_relation.text(
        -0.11,
        0.90,
        "c",
        transform=ax_relation.transAxes,
        fontsize=10,
        fontweight="bold",
    )

    episode_paths = list((records_root / "episodes").glob("*.json"))
    if len(episode_paths) != 1:
        raise ValueError("Expected a single terminal episode record")
    episode = _load(episode_paths[0])
    if episode["cycle_records"] != len(rows):
        raise ValueError("Terminal episode and cycle record lengths differ")
    outcome = "success" if episode["success"] else "not completed"
    fig.text(
        0.15,
        0.045,
        f"Outcome: {outcome}. Flat reference segments denote held task progress.",
        ha="left",
        fontsize=8.0,
        color="#425466",
    )

    output_stem.parent.mkdir(parents=True, exist_ok=True)
    fig.canvas.draw()
    fig.savefig(f"{output_stem}.svg", bbox_inches="tight")
    fig.savefig(f"{output_stem}.png", dpi=320, bbox_inches="tight")
    plt.close(fig)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--model-summary", type=Path, required=True)
    parser.add_argument("--output-stem", type=Path, required=True)
    args = parser.parse_args(argv)
    render(args.records, args.model_summary, args.output_stem)
    print(f"{args.output_stem}.svg", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
