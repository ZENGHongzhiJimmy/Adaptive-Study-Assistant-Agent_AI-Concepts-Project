# Figure captions

Sources: `experiments/results/e1_main.json` (4 methods × 5 learner profiles × 8 seeds = 160
sessions, 32-activity budget) and `experiments/data/materials.json` (the frozen LLM-only and
RAG explanations plus their scores). Render with `python -m experiments.plots`; all panels are
300 dpi PNG. Figs. 1-4 and 6 come from the equal-budget comparison; Fig. 5 comes from the
content-preparation stage.

---

**Fig. 1 — `fig1_learning_gain.png`**
Learning gain (post-test minus pre-test probe score) for the four tutors under an equal
32-activity budget. Bars are means over 40 sessions; whiskers are the 95% confidence interval
of the mean. LLM-only 0.123, RAG 0.143, fixed-strategy 0.163, adaptive 0.171. Each step adds one
component: retrieval, then a teaching loop, then learner-state driven scheduling.

**Fig. 2 — `fig2_learning_curve.png`**
Mean probe score as a function of activities completed, measured at checkpoints 0/8/16/24/32;
whiskers are ±1 SE. All methods start from the same pre-test (0.501) because the same pre-test
seeds every condition, and the ordering is already established by checkpoint 16. Sessions that
finish early carry their final score forward to the remaining checkpoints, so finishing early is
never penalised.

**Fig. 3 — `fig3_gain_by_profile.png`**
Learning gain by learner profile. The distance between methods is largest exactly where the
proposal predicted: `struggling` learners gain 0.063 from LLM-only and 0.122 from the adaptive
tutor, while `strong` learners are essentially indifferent (0.068–0.076 across all four
methods). This is the expertise-reversal effect: novices depend on whether the explanation
covers the tested content, advanced learners mostly need practice.

**Fig. 4 — `fig4_paired_delta.png`**
Paired difference in learning gain between the adaptive tutor and each baseline, matched on
learner profile and random seed (40 pairs per comparison). Dots are mean differences, bars are
95% bootstrap CIs (2000 resamples); the fraction is wins/pairs. All three intervals exclude
zero: +0.049 [+0.040, +0.057] vs LLM-only, +0.029 [+0.023, +0.035] vs RAG, +0.008
[+0.003, +0.013] vs fixed-strategy. The last one is the smallest and wins only 26/40 pairs —
reported as it is, not rounded up into a stronger claim.

**Fig. 5 — `fig5_content_quality.png`**
What retrieval buys, and what it costs. Left: per-topic coverage of the post-test content by
the explanation (mean 0.274 LLM-only vs 0.439 RAG). Middle: TF-IDF closeness to the lecture
wording (0.141 vs 0.289). Right: tokens per topic (568 vs 1040) and mean latency (3.2 s vs
3.0 s). The two metrics disagree on `what is AI` (equal coverage, double grounding), which is
kept visible: coverage is what the simulator consumes, grounding explains why RAG nevertheless
covers more topics elsewhere.

**Fig. 6 — `fig6_activity_mix.png`**
Mean number of explain / practice / review activities per session. LLM-only and RAG tutors spend
15.7 / 15.7 / 0 (one explanation, one quiz per topic, repeated); the fixed-strategy tutor runs
10.7 / 20.4 / 0 (explain → practice → practice); the adaptive tutor runs 8.1 / 17.0 / 5.6 and is
the only method that reviews errors. The differences in gain therefore come from what the
methods actually did, not from one method simply doing more.
