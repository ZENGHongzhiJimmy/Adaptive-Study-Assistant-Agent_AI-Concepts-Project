"""Experiment pipeline: content, then decisions, then the equal-budget replay.

``content``             the two content conditions, generated once and frozen
``collect_decisions``   the agent decides each state once; the log is frozen
``run_experiment``      four tutors under an equal activity budget (offline)
``plots``               the three figures, drawn from the result JSON

Each stage only reads the artifact the previous one wrote, so the expensive
stages (model calls) happen once and everything after them is free to re-run.
"""
