"""Render measured demonstration segmentation and extracted LiteTwin structure."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Patch


COLORS = ("#4477AA", "#66CCAA", "#EEBB55")
INK = "#17324D"
MUTED = "#5D6A75"
GRID = "#D7E0E7"


def _load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(path)
    return value


def _configure() -> None:
    mpl.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans", "sans-serif"],
            "font.size": 8.0,
            "axes.labelsize": 8.5,
            "axes.titlesize": 10.0,
            "xtick.labelsize": 7.5,
            "ytick.labelsize": 8.0,
            "legend.fontsize": 7.5,
            "axes.spines.right": False,
            "axes.spines.top": False,
            "svg.fonttype": "none",
            "pdf.fonttype": 42,
        }
    )


def render(summary_path: Path, output_stem: Path) -> None:
    _configure()
    summary = _load(summary_path)
    segmentation = summary["demonstration_segmentation"]
    demonstrations = segmentation["demonstrations"]
    structure = summary["extracted_task_structure"]

    fig = plt.figure(figsize=(7.15, 4.75), constrained_layout=False)
    grid = fig.add_gridspec(
        2,
        1,
        height_ratios=(2.25, 1.0),
        left=0.13,
        right=0.97,
        top=0.79,
        bottom=0.10,
        hspace=0.78,
    )
    ax = fig.add_subplot(grid[0])
    cards = fig.add_subplot(grid[1])

    fig.suptitle(
        "Offline construction from five successful demonstrations",
        x=0.13,
        y=0.955,
        ha="left",
        fontsize=13,
        fontweight="bold",
        color=INK,
    )
    fig.text(
        0.13,
        0.895,
        "Each color marks one skill in the demonstrated sequence.",
        ha="left",
        fontsize=8.5,
        color=MUTED,
    )

    for row, demo in enumerate(demonstrations):
        y = len(demonstrations) - 1 - row
        for skill, (start, stop) in enumerate(demo["skill_bounds"]):
            ax.broken_barh(
                [(start, stop - start)],
                (y - 0.31, 0.62),
                facecolors=COLORS[skill],
                edgecolors="white",
                linewidth=0.9,
            )
        ax.text(
            113,
            y,
            f'{demo["boundaries"][0]:>2}  /  {demo["boundaries"][1]:>2}  /  {demo["observation_count"]:>3}',
            va="center",
            fontsize=7,
            color=MUTED,
        )

    ax.set_yticks(range(len(demonstrations)))
    ax.set_yticklabels([f"Demo {i}" for i in range(5, 0, -1)])
    ax.set_xlim(0, 145)
    ax.set_xticks(range(0, 101, 20))
    ax.set_ylim(-0.65, len(demonstrations) - 0.35)
    ax.set_xlabel("Observation index")
    ax.set_ylabel("Successful trajectory")
    ax.xaxis.grid(True, color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    ax.spines["left"].set_color(GRID)
    ax.spines["bottom"].set_color(GRID)
    ax.legend(
        handles=[
            Patch(facecolor=color, label=f"Skill {index}")
            for index, color in enumerate(COLORS)
        ],
        loc="upper center",
        bbox_to_anchor=(0.5, -0.23),
        ncol=3,
        handlelength=1.2,
        columnspacing=1.6,
    )
    ax.text(
        -0.10,
        1.08,
        "a",
        transform=ax.transAxes,
        fontsize=10,
        fontweight="bold",
        va="top",
    )
    ax.set_title("Measured skill segmentation", loc="left", color=INK, pad=9)
    ax.text(
        113,
        len(demonstrations) - 0.24,
        "b1  /  b2  /   n",
        fontsize=7,
        fontweight="bold",
        color=INK,
        va="bottom",
    )

    cards.set_axis_off()
    cards.text(
        -0.10,
        1.10,
        "b",
        transform=cards.transAxes,
        fontsize=10,
        fontweight="bold",
        va="top",
    )
    cards.set_title("Extracted LiteTwin model", loc="left", color=INK, pad=8)
    values = (
        ("Task states", structure["state_count"]),
        ("Skill boundaries", structure["boundary_count"]),
        ("Recovery anchors", structure["link_anchor_count"]),
        ("Scene factors", structure["scene_factor_count"]),
    )
    card_width = 0.205
    for index, (label, value) in enumerate(values):
        x = 0.005 + index * 0.247
        cards.add_patch(
            FancyBboxPatch(
                (x, 0.23),
                card_width,
                0.50,
                boxstyle="round,pad=0.012,rounding_size=0.025",
                transform=cards.transAxes,
                facecolor="#F3F7FA",
                edgecolor="none",
                linewidth=0,
            )
        )
        cards.text(
            x + card_width / 2,
            0.54,
            str(value),
            transform=cards.transAxes,
            ha="center",
            va="center",
            fontsize=15,
            fontweight="bold",
            color="#21867A",
        )
        cards.text(
            x + card_width / 2,
            0.33,
            label,
            transform=cards.transAxes,
            ha="center",
            va="center",
            fontsize=7.5,
            color=MUTED,
        )
    cards.text(
        0.005,
        0.02,
        "Relation frames: " + ", ".join(structure["relation_frames"]),
        transform=cards.transAxes,
        fontsize=7.5,
        color=MUTED,
    )

    output_stem.parent.mkdir(parents=True, exist_ok=True)
    fig.canvas.draw()
    fig.savefig(f"{output_stem}.svg", bbox_inches="tight")
    fig.savefig(f"{output_stem}.png", dpi=320, bbox_inches="tight")
    plt.close(fig)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--output-stem", type=Path, required=True)
    args = parser.parse_args(argv)
    render(args.summary, args.output_stem)
    print(f"{args.output_stem}.svg", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
