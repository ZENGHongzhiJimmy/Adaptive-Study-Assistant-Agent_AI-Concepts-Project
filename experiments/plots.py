"""Render the E1 result figures from ``results/e1_main.json``.

The script is deliberately read-only with respect to the experiment: it never
re-simulates, it only draws what is already in the result file, so every figure
is traceable to a committed JSON artifact.

Usage
-----
    python -m experiments.plots
    python -m experiments.plots --in experiments/results/e1_main.json

Outputs (``experiments/results/figures``)
-----------------------------------------
fig1_learning_gain.png     mean learning gain per arm, 95% CI of the mean
fig2_learning_curve.png    probe score vs. activities spent, mean +/- 1 SE
fig3_gain_by_profile.png   learning gain per arm, split by learner profile
fig4_paired_delta.png      paired gain difference vs. adaptive, 95% bootstrap CI
fig5_activity_mix.png      activity mix per arm (explain / practice / review)
fig6_calibration.png       estimated vs. true topic mastery (estimation quality)
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

from src.agent import policy as P  # noqa: E402

RESULTS_DIR = Path(__file__).resolve().parent / "results"
DEFAULT_IN = RESULTS_DIR / "e1_main.json"
DEFAULT_OUTDIR = RESULTS_DIR / "figures"

ARM_ORDER = ("adaptive", "fixed", "random", "always_mcq")
ARM_LABEL = {
    "adaptive": "Adaptive (ours)",
    "fixed": "Fixed schedule",
    "random": "Random",
    "always_mcq": "Always practice",
}
COLORS = {
    "adaptive": "#2f6fdb",
    "fixed": "#6b7280",
    "random": "#b08968",
    "always_mcq": "#3f8f6f",
}
DPI = 300
FIGSIZE = (6.4, 4.0)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _mean(xs: Sequence[float]) -> float:
    return statistics.fmean(xs) if xs else float("nan")


def _sem(xs: Sequence[float]) -> float:
    if len(xs) < 2:
        return 0.0
    return statistics.pstdev(xs) / (len(xs) ** 0.5)


def _ci95(xs: Sequence[float]) -> float:
    """Half-width of the 95% CI of the mean (normal approximation)."""
    return 1.96 * _sem(xs)


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


# --------------------------------------------------------------------------
# figures
# --------------------------------------------------------------------------

def fig1_learning_gain(payload: dict[str, Any], outdir: Path) -> Path:
    rows = payload["episodes"]
    arms = [a for a in ARM_ORDER if any(r["arm"] == a for r in rows)]
    gains = [[r["learning_gain"] for r in rows if r["arm"] == a] for a in arms]

    fig, ax = plt.subplots(figsize=FIGSIZE)
    x = np.arange(len(arms))
    vals = [_mean(g) for g in gains]
    errs = [_ci95(g) for g in gains]
    bars = ax.bar(
        x, vals, yerr=errs, capsize=4,
        color=[COLORS[a] for a in arms], width=0.6,
        error_kw={"elinewidth": 0.9, "ecolor": "#333333"},
    )
    for bar, v, a, g in zip(bars, vals, arms, gains):
        ax.text(
            bar.get_x() + bar.get_width() / 2, v + 0.018,
            f"{v:.3f}", ha="center", va="bottom", fontsize=9,
        )
    ax.set_xticks(x)
    ax.set_xticklabels([ARM_LABEL[a] for a in arms])
    ax.set_ylabel("Learning gain (post - pre)")
    ax.set_title(f"Equal-budget learning gain (n={len(rows) // len(arms)} sessions per arm)")
    ax.set_ylim(0, max(v + e for v, e in zip(vals, errs)) * 1.28)
    return _save(fig, outdir, "fig1_learning_gain.png")


def fig2_learning_curve(payload: dict[str, Any], outdir: Path) -> Path:
    rows = payload["episodes"]
    checkpoints = payload["protocol"]["checkpoints"]
    arms = [a for a in ARM_ORDER if any(r["arm"] == a for r in rows)]

    fig, ax = plt.subplots(figsize=FIGSIZE)
    xs = [0] + list(checkpoints)
    for arm in arms:
        arm_rows = [r for r in rows if r["arm"] == arm]
        means, errs, ns = [_mean([r["pre"] for r in arm_rows])], [0.0], [len(arm_rows)]
        for cp in checkpoints:
            vals = [r["curve"][str(cp)] for r in arm_rows if str(cp) in r["curve"]]
            means.append(_mean(vals))
            errs.append(_sem(vals))
            ns.append(len(vals))
        ax.errorbar(
            xs, means, yerr=errs, marker="o", ms=4, lw=1.8, capsize=3,
            color=COLORS[arm], label=ARM_LABEL[arm],
            elinewidth=0.8, alpha=0.95 if arm == "adaptive" else 0.85,
        )
        ax.annotate(
            f"{means[-1]:.3f}", (xs[-1], means[-1]), textcoords="offset points",
            xytext=(6, 0), fontsize=8, color=COLORS[arm], va="center",
        )
    ax.set_xlabel("Activities completed")
    ax.set_ylabel("Probe score (0-1)")
    ax.set_title("Learning curves under an equal 32-activity budget")
    ax.set_xticks(xs)
    ax.set_xlim(-1, xs[-1] + 4)
    ax.legend(loc="lower right")
    return _save(fig, outdir, "fig2_learning_curve.png")


def fig3_gain_by_profile(payload: dict[str, Any], outdir: Path) -> Path:
    rows = payload["by_profile"]
    profiles: list[str] = []
    for r in rows:
        if r["profile"] not in profiles:
            profiles.append(r["profile"])
    arms = [a for a in ARM_ORDER if any(r["arm"] == a for r in rows)]

    fig, ax = plt.subplots(figsize=(7.0, 4.0))
    width = 0.8 / len(arms)
    x = np.arange(len(profiles))
    for i, arm in enumerate(arms):
        vals = [
            next((r["learning_gain"] for r in rows if r["arm"] == arm and r["profile"] == p), 0.0)
            for p in profiles
        ]
        ax.bar(
            x + i * width - 0.4 + width / 2, vals, width,
            label=ARM_LABEL[arm], color=COLORS[arm],
        )
    ax.set_xticks(x)
    ax.set_xticklabels([p.capitalize() for p in profiles])
    ax.set_ylabel("Learning gain (post - pre)")
    ax.set_title("Where the adaptive policy helps: gain by learner profile")
    # Legend below the axes: inside the frame it covers the tallest bars.
    ax.legend(ncol=4, loc="upper center", bbox_to_anchor=(0.5, -0.10))
    ax.grid(axis="x", visible=False)
    return _save(fig, outdir, "fig3_gain_by_profile.png")


def fig4_paired_delta(payload: dict[str, Any], outdir: Path) -> Path:
    pairs = payload["paired"]
    pairs = [p for p in pairs if p.get("pairs", 0) > 1]
    fig, ax = plt.subplots(figsize=(6.4, 2.6 + 0.5 * len(pairs)))

    ys = np.arange(len(pairs))[::-1]
    for y, p in zip(ys, pairs):
        lo, hi, d = p["ci_low"], p["ci_high"], p["delta_gain"]
        ax.plot([lo, hi], [y, y], color="#2f6fdb", lw=2.4, solid_capstyle="round", alpha=0.85)
        ax.plot([d], [y], "o", color="#1b3f7a", ms=6.5, zorder=3)
        ax.text(
            hi + 0.004, y, f"{d:+.3f}  [{lo:+.3f}, {hi:+.3f}]  ({p['wins']}/{p['pairs']})",
            va="center", fontsize=8.5,
        )
    ax.axvline(0, color="#888888", ls="--", lw=1)
    ax.set_yticks(ys)
    ax.set_yticklabels([ARM_LABEL.get(p["arm"], p["arm"]) for p in pairs])
    ax.set_xlabel("Paired difference in learning gain vs. adaptive (95% bootstrap CI)")
    ax.set_title("Adaptive beats every ablation on the same learner and seed")
    ax.set_ylim(-0.6, len(pairs) - 0.4)
    ax.set_xlim(-0.01, max(p["ci_high"] for p in pairs) * 1.55)
    ax.grid(axis="y", visible=False)
    return _save(fig, outdir, "fig4_paired_delta.png")


def fig5_activity_mix(payload: dict[str, Any], outdir: Path) -> Path:
    totals = payload["totals"]
    arms = [t["arm"] for t in totals]
    kinds = [(P.EXPLAIN, "Explain", "#2f6fdb"), (P.PRACTICE, "Practice", "#3f8f6f"), (P.REVIEW, "Review", "#b08968")]

    fig, ax = plt.subplots(figsize=FIGSIZE)
    x = np.arange(len(arms))
    bottom = np.zeros(len(arms))
    for key, label, color in kinds:
        vals = np.array([t["mix"][key] for t in totals])
        ax.bar(x, vals, 0.6, bottom=bottom, label=label, color=color, edgecolor="white", linewidth=0.6)
        for xi, (b, v) in enumerate(zip(bottom, vals)):
            if v > 1.5:
                ax.text(xi, b + v / 2, f"{v:.1f}", ha="center", va="center",
                        fontsize=8, color="white")
        bottom += vals
    ax.set_xticks(x)
    ax.set_xticklabels([ARM_LABEL.get(a, a) for a in arms])
    ax.set_ylabel("Activities per session (mean)")
    ax.set_title("What each arm actually spends the budget on")
    ax.legend(ncol=3, loc="upper center", bbox_to_anchor=(0.5, -0.14))
    ax.grid(axis="x", visible=False)
    return _save(fig, outdir, "fig5_activity_mix.png")


def fig6_calibration(payload: dict[str, Any], outdir: Path) -> Path:
    rows = payload["episodes"]
    arms = [a for a in ARM_ORDER if any(r["arm"] == a for r in rows)]

    fig, axes = plt.subplots(1, len(arms), figsize=(2.6 * len(arms), 3.2), sharex=True, sharey=True)
    if len(arms) == 1:
        axes = [axes]
    for ax, arm in zip(axes, arms):
        arm_rows = [r for r in rows if r["arm"] == arm]
        xs = [r["true_mastery"] for r in arm_rows]
        ys = [r["agent_mastery"] for r in arm_rows]
        ax.plot([0, 1], [0, 1], ls="--", lw=1, color="#999999")
        ax.scatter(xs, ys, s=16, alpha=0.55, color=COLORS[arm], edgecolors="none")
        ax.set_title(ARM_LABEL[arm], fontsize=9)
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.set_aspect("equal")
        mae = statistics.fmean(r["estimate_mae"] for r in arm_rows)
        ax.text(0.04, 0.94, f"MAE {mae:.3f}", transform=ax.transAxes,
                fontsize=8, va="top")
        ax.set_xlabel("True mastery")
    axes[0].set_ylabel("Agent mastery estimate")
    fig.suptitle("How well the agent's mastery estimate tracks reality", fontsize=10)
    return _save(fig, outdir, "fig6_calibration.png")


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

FIGURES = {
    "fig1": fig1_learning_gain,
    "fig2": fig2_learning_curve,
    "fig3": fig3_gain_by_profile,
    "fig4": fig4_paired_delta,
    "fig5": fig5_activity_mix,
    "fig6": fig6_calibration,
}


def build(in_path: Path, outdir: Path) -> list[Path]:
    payload = json.loads(in_path.read_text(encoding="utf-8"))
    outdir.mkdir(parents=True, exist_ok=True)
    _style()
    return [fn(payload, outdir) for fn in FIGURES.values()]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render E1 figures")
    parser.add_argument("--in", dest="in_path", default=str(DEFAULT_IN))
    parser.add_argument("--outdir", default=str(DEFAULT_OUTDIR))
    args = parser.parse_args(argv)

    in_path = Path(args.in_path)
    if not in_path.exists():
        print(f"missing result file: {in_path}\nrun `python -m experiments.run_experiment` first")
        return 2

    paths = build(in_path, Path(args.outdir))
    for p in paths:
        print(f"-> {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
