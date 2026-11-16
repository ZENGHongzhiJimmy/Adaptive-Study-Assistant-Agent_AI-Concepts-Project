# Figure captions

Three figures, all drawn from `experiments/results/e1_main.json` by
`python -m experiments.plots`. Nothing here is generated from a live model call.

**fig1_learning_gain.png** — Learning gain on the held-out probes, equal budget
(32 activities). Bars are means over 5 learner profiles x 4 seeds (n = 20 per
method); whiskers are the 95% CI of the mean. The agent beats both content
baselines and ties the fixed teaching loop.

**fig2_gain_by_profile.png** — The same metric split by learner archetype. The
ordering is the expected one: retrieval helps everywhere, the designed loop adds
a constant step, and the agent's advantage concentrates on `medium`, where it
can choose per topic between explanation and practice.

**fig3_paired_delta.png** — Paired difference against the agent (same profile,
same seed, so the learner and the dice are identical); horizontal bars are 95%
bootstrap intervals over the 20 pairs. The interval for the fixed-strategy tutor
crosses zero; the intervals for both content baselines do not.
