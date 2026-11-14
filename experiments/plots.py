"""Render the experiment figures from the result JSON.

Read-only with respect to the experiment: it never re-simulates, it only draws
what is already in the committed artifacts (``e1_main.json`` and
``materials.json``), so every figure is traceable to a file under version
control.

Usage
-----
    python -m experiments.plots

Outputs (``experiments/results/figures``)
-----------------------------------------
fig1_learning_gain.png     mean learning gain per method, 95% CI of the mean
fig2_learning_curve.png    probe score vs. activities spent, mean +/- 1 SE
fig3_gain_by_profile.png   learning gain per method, split by learner profile
fig4_paired_delta.png      paired gain difference vs. adaptive, 95% bootstrap CI
fig5_content_quality.png   what retrieval buys: probe coverage and LLM cost
fig6_activity_mix.png      activity mix per method (explain / practice / review)
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
MATERIALS = Path(__file__).resolve().parent / "data" / "materials.json"
DEFAULT_OUTDIR = RESULTS_DIR / "figures"

METHOD_ORDER = ("llm_only", "rag", "fixed", "adaptive")
LABEL = {
    "llm_only": "LLM-only tutor",
    "rag": "RAG tutor",
    "fixed": "Fixed-strategy tutor",
    "adaptive": "Adaptive (ours)",
}
SHORT = {"llm_only": "LLM-only", "rag": "RAG", "fixed": "Fixed", "adaptive": "Adaptive"}
COLOR = {
    "llm_only": "#c47f2e",
    "rag": "#3f8f6f",
    "fixed": "#6b7280",
    "adaptive": "#2f6fdb",
}
COND_COLOR = {"llm_only": "#c47f2e", "rag": "#3f8f6f"}
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


def _methods(payload: dict[str, Any]) -> list[str]:
    present = {r["method"] for r in payload["episodes"]}
    return [m for m in METHOD_ORDER if m in present]


# --------------------------------------------------------------------------
# figures
# --------------------------------------------------------------------------

def fig1_learning_gain(payload, outdir: Path) -> Path:
    rows = payload["episodes"]
    methods = _methods(payload)
    gains = [[r["learning_gain"] for r in rows if r["method"] == m] for m in methods]

    fig, ax = plt.subplots(figsize=FIGSIZE)
    x = np.arange(len(methods))
    vals = [_mean(g) for g in gains]
    errs = [_ci95(g) for g in gains]
    bars = ax.bar(x, vals, yerr=errs, capsize=4, width=0.6,
                  color=[COLOR[m] for m in methods],
                  error_kw={"elinewidth": 0.9, "ecolor": "#333333"})
    for bar, v, e in zip(bars, vals, errs):
        ax.text(bar.get_x() + bar.get_width() / 2, v + e + 0.004, f"{v:.3f}",
                ha="center", va="bottom", fontsize=9)
    n = len(rows) // max(1, len(methods))
    ax.set_xticks(x)
    ax.set_xticklabels([LABEL[m] for m in methods])
    ax.set_ylabel("Learning gain (post - pre)")
    ax.set_title(f"Equal-budget learning gain ({n} sessions per method)")
    ax.set_ylim(0, max(v + e for v, e in zip(vals, errs)) * 1.25)
    return _save(fig, outdir, "fig1_learning_gain.png")


def fig2_learning_curve(payload, outdir: Path) -> Path:
    rows = payload["episodes"]
    checkpoints = payload["protocol"]["checkpoints"]
    methods = _methods(payload)

    fig, ax = plt.subplots(figsize=FIGSIZE)
    xs = [0] + list(checkpoints)
    for method in methods:
        arm = [r for r in rows if r["method"] == method]
        means = [_mean([r["pre"] for r in arm])]
        errs = [0.0]
        for cp in checkpoints:
            vals = [r["curve"][str(cp)] for r in arm if str(cp) in r["curve"]]
            means.append(_mean(vals))
            errs.append(_sem(vals))
        ax.errorbar(xs, means, yerr=errs, marker="o", ms=4, lw=1.8, capsize=3,
                    color=COLOR[method], label=LABEL[method], elinewidth=0.8,
                    alpha=0.95 if method == "adaptive" else 0.85)
        ax.annotate(f"{means[-1]:.3f}", (xs[-1], means[-1]), textcoords="offset points",
                    xytext=(6, 0), fontsize=8, color=COLOR[method], va="center")
    ax.set_xlabel("Activities completed")
    ax.set_ylabel("Probe score (0-1)")
    ax.set_title("Learning curves under an equal 32-activity budget")
    ax.set_xticks(xs)
    ax.set_xlim(-1, xs[-1] + 4)
    ax.legend(loc="lower right")
    return _save(fig, outdir, "fig2_learning_curve.png")


def fig3_gain_by_profile(payload, outdir: Path) -> Path:
    rows = payload["by_profile"]
    profiles: list[str] = []
    for r in rows:
        if r["profile"] not in profiles:
            profiles.append(r["profile"])
    methods = _methods(payload)

    fig, ax = plt.subplots(figsize=(7.0, 4.0))
    width = 0.8 / len(methods)
    x = np.arange(len(profiles))
    for i, method in enumerate(methods):
        vals = [
            next((r["learning_gain"] for r in rows
                  if r["method"] == method and r["profile"] == p), 0.0)
            for p in profiles
        ]
        ax.bar(x + i * width - 0.4 + width / 2, vals, width,
               label=LABEL[method], color=COLOR[method])
    ax.set_xticks(x)
    ax.set_xticklabels([p.capitalize() for p in profiles])
    ax.set_ylabel("Learning gain (post - pre)")
    ax.set_title("Gain by learner profile: the advantage is not uniform")
    ax.legend(ncol=4, loc="upper center", bbox_to_anchor=(0.5, -0.10))
    ax.grid(axis="x", visible=False)
    return _save(fig, outdir, "fig3_gain_by_profile.png")


def fig4_paired_delta(payload, outdir: Path) -> Path:
    pairs = [p for p in payload["paired"] if p.get("pairs", 0) > 1]
    fig, ax = plt.subplots(figsize=(6.4, 2.4 + 0.5 * len(pairs)))
    ys = np.arange(len(pairs))[::-1]
    for y, p in zip(ys, pairs):
        lo, hi, d = p["ci_low"], p["ci_high"], p["delta_gain"]
        ax.plot([lo, hi], [y, y], color="#2f6fdb", lw=2.4, solid_capstyle="round", alpha=0.85)
        ax.plot([d], [y], "o", color="#1b3f7a", ms=6.5, zorder=3)
        ax.text(hi + 0.003, y, f"{d:+.3f}  [{lo:+.3f}, {hi:+.3f}]  ({p['wins']}/{p['pairs']})",
                va="center", fontsize=8.5)
    ax.axvline(0, color="#888888", ls="--", lw=1)
    ax.set_yticks(ys)
    ax.set_yticklabels([LABEL.get(p["method"], p["method"]) for p in pairs])
    ax.set_xlabel("Paired difference in learning gain vs. adaptive (95% bootstrap CI)")
    ax.set_title("Adaptive vs each baseline, same learner and seed")
    ax.set_ylim(-0.6, len(pairs) - 0.4)
    ax.set_xlim(-0.005, max(p["ci_high"] for p in pairs) * 1.6)
    ax.grid(axis="y", visible=False)
    return _save(fig, outdir, "fig4_paired_delta.png")


def fig5_content_quality(payload, materials, outdir: Path) -> Path:
    """Two content metrics plus the cost of getting them.

    Coverage is what the simulator consumes; grounding explains *why* the RAG
    text covers more - it stays close to the lecture wording. Both are shown
    because they disagree on some topics (e.g. "what is ai" has equal coverage
    but twice the grounding), and hiding that would oversell the result.
    """
    per_topic = materials["scores"]["per_topic"]
    conditions = ("llm_only", "rag")
    names = [e["topic_id"].split("_", 1)[1].replace("_", " ") for e in per_topic]

    fig, axes = plt.subplots(
        1, 3, figsize=(12.6, 3.7),
        gridspec_kw={"width_ratios": [1.55, 1.55, 1.0]},
    )
    x = np.arange(len(per_topic))
    width = 0.38
    for ax, metric, title in (
        (axes[0], "coverage", "Coverage of what the post-test asks"),
        (axes[1], "grounding", "Closeness to the lecture wording"),
    ):
        for i, condition in enumerate(conditions):
            vals = [e[condition][metric] for e in per_topic]
            ax.bar(x + i * width - width / 2, vals, width,
                   label=("LLM-only" if condition == "llm_only" else "RAG"),
                   color=COND_COLOR[condition])
            ax.axhline(statistics.fmean(vals), color=COND_COLOR[condition],
                       ls="--", lw=0.9, alpha=0.7)
        ax.set_title(title)
        ax.set_xticks(x)
        ax.set_xticklabels(names, rotation=40, ha="right")
        # Headroom so the legend never sits on the tallest bar.
        peak = max(e[c][metric] for e in per_topic for c in conditions)
        ax.set_ylim(0, peak * 1.35)
        ax.legend(ncol=2, loc="upper left")
    axes[0].set_ylabel("Score (0-1)")
    axes[1].set_ylabel("")

    ax = axes[2]
    costs = payload["content_cost"]
    labels = ["LLM-only", "RAG"]
    keys = ["llm_only", "rag"]
    tokens = [costs[k]["tokens_per_topic"] for k in keys]
    lat = [costs[k]["mean_latency_ms"] / 1000.0 for k in keys]
    xx = np.arange(2)
    ax.bar(xx - 0.19, tokens, 0.36, color=[COND_COLOR[k] for k in keys], label="tokens / topic")
    ax.bar(xx + 0.19, [v * 250 for v in lat], 0.36,
           color=[COND_COLOR[k] for k in keys], alpha=0.45, label="latency x250 (s)")
    for i, (t, l) in enumerate(zip(tokens, lat)):
        ax.text(i - 0.19, t + 25, f"{t:.0f}", ha="center", fontsize=8)
        ax.text(i + 0.19, l * 250 + 25, f"{l:.1f}s", ha="center", fontsize=8)
    ax.set_xticks(xx)
    ax.set_xticklabels(labels)
    ax.set_ylabel("Cost per material")
    ax.set_title("Retrieval is not free")
    ax.legend(loc="upper left")
    ax.grid(axis="x", visible=False)
    return _save(fig, outdir, "fig5_content_quality.png")


def fig6_activity_mix(payload, outdir: Path) -> Path:
    totals = payload["totals"]
    methods = [t["method"] for t in totals]
    kinds = [(P.EXPLAIN, "Explain", "#2f6fdb"),
             (P.PRACTICE, "Practice", "#3f8f6f"),
             (P.REVIEW, "Review", "#b08968")]

    fig, ax = plt.subplots(figsize=FIGSIZE)
    x = np.arange(len(methods))
    bottom = np.zeros(len(methods))
    for key, label, color in kinds:
        vals = np.array([t["mix"][key] for t in totals])
        ax.bar(x, vals, 0.6, bottom=bottom, label=label, color=color,
               edgecolor="white", linewidth=0.6)
        for xi, (b, v) in enumerate(zip(bottom, vals)):
            if v > 1.5:
                ax.text(xi, b + v / 2, f"{v:.1f}", ha="center", va="center",
                        fontsize=8, color="white")
        bottom += vals
    ax.set_xticks(x)
    ax.set_xticklabels([SHORT.get(m, m) for m in methods])
    ax.set_ylabel("Activities per session (mean)")
    ax.set_title("What each tutor spends the budget on")
    ax.legend(ncol=3, loc="upper center", bbox_to_anchor=(0.5, -0.12))
    ax.grid(axis="x", visible=False)
    return _save(fig, outdir, "fig6_activity_mix.png")


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def build(in_path: Path, materials_path: Path, outdir: Path) -> list[Path]:
    payload = json.loads(in_path.read_text(encoding="utf-8"))
    materials = json.loads(materials_path.read_text(encoding="utf-8"))
    outdir.mkdir(parents=True, exist_ok=True)
    _style()
    return [
        fig1_learning_gain(payload, outdir),
        fig2_learning_curve(payload, outdir),
        fig3_gain_by_profile(payload, outdir),
        fig4_paired_delta(payload, outdir),
        fig5_content_quality(payload, materials, outdir),
        fig6_activity_mix(payload, outdir),
    ]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render experiment figures")
    parser.add_argument("--in", dest="in_path", default=str(DEFAULT_IN))
    parser.add_argument("--materials", default=str(MATERIALS))
    parser.add_argument("--outdir", default=str(DEFAULT_OUTDIR))
    args = parser.parse_args(argv)

    in_path = Path(args.in_path)
    if not in_path.exists():
        print(f"missing result file: {in_path}\nrun `python -m experiments.run_experiment` first")
        return 2
    materials = Path(args.materials)
    if not materials.exists():
        print(f"missing content file: {materials}\nrun `python -m experiments.content` first")
        return 2

    for path in build(in_path, materials, Path(args.outdir)):
        print(f"-> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
