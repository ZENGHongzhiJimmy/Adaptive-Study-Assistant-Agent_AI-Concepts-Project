# Figure captions

Three figures, all drawn from `experiments/results/e1_main.json` by
`python -m experiments.plots`. Nothing here is generated from a live model call.

**fig1_learning_gain.png** — Learning gain on the held-out probes, equal budget
(32 activities). Bars are means over 5 learner profiles x 8 seeds (n = 40 per
method); whiskers are the 95% CI of the mean. The agent beats all three
baselines, including the fixed teaching loop (+0.018, 95% CI [+0.013, +0.022]).

**fig2_gain_by_profile.png** — The same metric split by learner archetype.
Retrieval helps everywhere and the designed loop adds a roughly constant step;
the agent's advantage over the loop is +0.024 (`struggling`), +0.029 (`weak`),
+0.014 (`medium`) and +0.021 (`mixed`), and zero on `strong`, which reaches the
mastery ceiling in every session.

**fig3_paired_delta.png** — Paired difference against the agent (same profile,
same seed, so the learner and the dice are identical); horizontal bars are 95%
bootstrap intervals over the 40 pairs. All three intervals now exclude zero; in
the v1 prompt version the fixed-strategy interval still crossed it.
