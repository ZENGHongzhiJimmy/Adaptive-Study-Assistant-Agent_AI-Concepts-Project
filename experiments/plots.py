"""Render the experiment figures from the result JSON.

Read-only with respect to the experiment: it never re-simulates and never calls
a model, it only draws what is already in the committed artifact
(``e1_main.json``), so every figure is traceable to a file under version
control.  Changing a metric or a colour costs nothing.

Only the three reported metrics get a figure - keeping the plot set as small as
the metric set is deliberate:

``fig1_learning_gain.png``    mean learning gain per method, 95% CI of the mean
``fig2_gain_by_profile.png``  the same metric split by learner profile
``fig3_paired_delta.png``     paired gain difference vs. the agent, bootstrap CI

Usage
-----
    python -m experiments.plots
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

RESULTS_DIR = Path(__file__).resolve().parent / "results"
DEFAULT_IN = RESULTS_DIR / "e1_main.json"
DEFAULT_OUTDIR = RESULTS_DIR / "figures"

METHOD_ORDER = ("llm_only", "rag", "fixed", "adaptive")
LABEL = {
    "llm_only": "LLM-only tutor",
    "rag": "RAG tutor",
    "fixed": "Fixed-strategy tutor",
    "adaptive": "Adaptive agent (ours)",
}
SHORT = {"llm_only": "LLM-only", "rag": "RAG", "fixed": "Fixed", "adaptive": "Agent"}
COLOR = {
    "llm_only": "#c47f2e",
    "rag": "#3f8f6f",
    "fixed": "#6b7280",
    "adaptive": "#2f6fdb",
}
PROFILE_LABEL = {
    "struggling": "struggling",
    "weak": "weak",
    "medium": "medium",
    "strong": "strong",
    "mixed": "mixed",
}
DPI = 300
FIGSIZE = (6.4, 3.8)


def _sem(xs: Sequence[float]) -> float:
    if len(xs) < 2:
        return 0.0
    return statistics.pstdev(xs) / (len(xs) ** 0.5)


def _style() -> None:
    plt.rcParams.update({
        "figure.dpi": 120,
        "savefig.dpi": DPI,
        "font.size": 9,
        "axes.titlesize": 10,
        "axes.labelsize": 9,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "axes.axisbelow": True,
        "grid.alpha": 0.25,
        "grid.linewidth": 0.6,
        "legend.frameon": False,
        "legend.fontsize": 8,
        "xtick.labelsize": 8.5,
        "ytick.labelsize": 8.5,
    })


def _save(fig, outdir: Path, name: str) -> Path:
    path = outdir / name
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return path


def _methods(payload: dict[str, Any]) -> list[str]:
    present = {r["method"] for r in payload["episodes"]}
    return [m for m in METHOD_ORDER if m in present]


def fig1_learning_gain(payload, outdir: Path) -> Path:
    rows = payload["episodes"]
    methods = _methods(payload)
    gains = [[r["learning_gain"] for r in rows if r["method"] == m] for m in methods]
    means = [statistics.fmean(g) for g in gains]
    errs = [1.96 * _sem(g) for g in gains]

    fig, ax = plt.subplots(figsize=FIGSIZE)
    positions = np.arange(len(methods))
    bars = ax.bar(
        positions, means, yerr=errs, capsize=3,
        color=[COLOR[m] for m in methods], width=0.58,
        error_kw={"elinewidth": 0.9, "ecolor": "#374151"},
    )
    for bar, value in zip(bars, means):
        ax.annotate(
            f"{value:+.3f}", (bar.get_x() + bar.get_width() / 2, value),
            textcoords="offset points", xytext=(0, 6),
            ha="center", fontsize=8.5,
        )
    ax.set_xticks(positions)
    ax.set_xticklabels([SHORT[m] for m in methods])
    ax.set_ylabel("learning gain (post - pre)")
    ax.set_title("Learning gain on held-out probes, equal budget")
    return _save(fig, outdir, "fig1_learning_gain.png")


def fig2_gain_by_profile(payload, outdir: Path) -> Path:
    rows = payload["by_profile"]
    methods = _methods(payload)
    profiles = [p for p in PROFILE_LABEL if any(r["profile"] == p for r in rows)]

    fig, ax = plt.subplots(figsize=(6.8, 3.8))
    width = 0.8 / max(1, len(methods))
    positions = np.arange(len(profiles))
    for offset, method in enumerate(methods):
        values = []
        for profile in profiles:
            row = next(
                (r for r in rows if r["method"] == method and r["profile"] == profile),
                None,
            )
            values.append(row["learning_gain"] if row else 0.0)
        ax.bar(
            positions + (offset - (len(methods) - 1) / 2) * width,
            values, width=width * 0.92,
            color=COLOR[method], label=SHORT[method],
        )
    ax.set_xticks(positions)
    ax.set_xticklabels([PROFILE_LABEL[p] for p in profiles])
    ax.set_ylabel("learning gain (post - pre)")
    ax.set_title("Where the agent helps: learning gain by learner profile")
    ax.legend(ncol=len(methods), loc="upper left")
    return _save(fig, outdir, "fig2_gain_by_profile.png")


def fig3_paired_delta(payload, outdir: Path) -> Path:
    pairs = payload.get("paired_gain") or []
    if not pairs:
        raise ValueError("payload has no paired_gain - rerun run_experiment first")

    fig, ax = plt.subplots(figsize=(6.2, 2.6))
    ys = list(range(len(pairs)))[::-1]
    for y, row in zip(ys, pairs):
        lo, hi = row["ci_low"], row["ci_high"]
        color = COLOR.get(row["method"], "#374151")
        ax.plot([lo, hi], [y, y], color=color, linewidth=2.2, solid_capstyle="round")
        ax.plot([row["delta"]], [y], "o", color=color, markersize=6)
        ax.annotate(
            f"{row['delta']:+.3f}  [{lo:+.3f}, {hi:+.3f}]  {row['wins']}/{row['pairs']} wins",
            (hi, y), textcoords="offset points", xytext=(6, -2.5),
            fontsize=8, va="center",
        )
    ax.axvline(0, color="#9ca3af", linewidth=1, linestyle="--")
    ax.set_yticks(ys)
    ax.set_yticklabels([LABEL.get(r["method"], r["method"]) for r in pairs])
    ax.set_xlabel("paired difference in learning gain (agent - baseline)")
    ax.set_title("Paired comparison, same learner and same seed")
    ax.grid(axis="y", visible=False)
    ax.margins(x=0.35)
    return _save(fig, outdir, "fig3_paired_delta.png")


FIGURES = (fig1_learning_gain, fig2_gain_by_profile, fig3_paired_delta)


def build(in_path: Path, outdir: Path) -> list[Path]:
    payload = json.loads(in_path.read_text(encoding="utf-8"))
    outdir.mkdir(parents=True, exist_ok=True)
    _style()
    return [figure(payload, outdir) for figure in FIGURES]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="render the experiment figures")
    parser.add_argument("--in", dest="in_path", default=str(DEFAULT_IN))
    parser.add_argument("--outdir", default=str(DEFAULT_OUTDIR))
    args = parser.parse_args(argv)

    for path in build(Path(args.in_path), Path(args.outdir)):
        print(f"-> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
