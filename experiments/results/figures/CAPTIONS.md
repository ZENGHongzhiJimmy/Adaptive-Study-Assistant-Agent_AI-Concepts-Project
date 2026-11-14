# E1 figure captions

Source data: `experiments/results/e1_main.json` (4 arms × 5 profiles × 8 seeds = 160 sessions,
budget 32 activities, LLM off).
Render with `python -m experiments.plots` (matplotlib, offline). All panels are 300 dpi PNG.

---

**Fig. 1 — `fig1_learning_gain.png`**
Learning gain (post-test minus pre-test probe score) per arm under an equal 32-activity
budget. Bars show means over 40 sessions per arm; whiskers are the 95% confidence
interval of the mean (n = 40). Adaptive reaches 0.216 versus 0.203 (fixed schedule),
0.172 (always-practice) and 0.143 (random).

**Fig. 2 — `fig2_learning_curve.png`**
Mean probe score as a function of activities completed, measured at checkpoints
0/8/16/24/32; shaded whiskers are ±1 SE. All arms start from the same pre-test
(0.501) because the same pre-test seeds every arm. Sessions that stop early (all
topics mastered) carry their final score forward to the remaining checkpoints, so
the arm that finishes most often is not penalised by missing readings. Adaptive keeps
the highest trajectory from checkpoint 16 onwards and ends at 0.717.

**Fig. 3 — `fig3_gain_by_profile.png`**
Learning gain by learner profile. The adaptive advantage is concentrated in the
weak and heterogeneous learners (`struggling` 0.197 vs 0.088 for always-practice;
`weak` 0.303 vs 0.232), while all arms converge for `strong` learners (0.079 vs
0.081). This is the expertise-reversal effect the proposal predicted: novices need
explanations, advanced learners mostly need practice.

**Fig. 4 — `fig4_paired_delta.png`**
Paired difference in learning gain between the adaptive policy and each ablation,
matched on learner profile and random seed (40 pairs per comparison). Dots are mean
differences, bars are 95% bootstrap CIs (2000 resamples); the parenthesised fraction
is wins/pairs. All three intervals exclude zero: +0.013 [+0.009, +0.018] vs fixed,
+0.073 [+0.061, +0.084] vs random, +0.043 [+0.030, +0.057] vs always-practice.

**Fig. 5 — `fig5_activity_mix.png`**
Mean number of explain / practice / review activities per session. The adaptive arm
spends its budget differently from every baseline (8.1 explain, 17.2 practice, 5.0
review) rather than following a fixed ratio, which is the mechanism behind the gain
difference. The fixed arm never reviews; always-practice never explains.

**Fig. 6 — `fig6_calibration.png`**
Agent mastery estimate versus the learner's true topic mastery, one point per topic
per session, against the identity line. MAE is 0.097 for adaptive, 0.093 fixed,
0.150 random, 0.073 always-practice. Random's higher error shows that an
uninformative activity schedule also corrupts the estimate: the agent sees fewer
diagnostic answers, so its belief drifts further from reality.
