"""Guard rails for "one method, one set of numbers".

Why this file exists
--------------------
The project used to ship two planners (JavaScript + Python), two mastery
formulas (0-100 with ``0.6 * gap`` and 0-1 with ``0.7 * old + 0.3 * p``) and
two threshold sets (80/55 vs 0.4/0.7/0.85).  Both survived because nothing in
the test suite noticed the duplication, so the product and the experiment could
drift apart silently.

There is now exactly one method: ``src/agent/policy.py``.  The old formula and
the old thresholds still exist, but only as the *baseline* arm (``graph.py`` ->
``planner.py`` / ``mastery.py``).  These tests assert the boundary holds:

* constants live in one place and have one scale (0-100);
* the product path never imports the baseline modules;
* the frontend carries no threshold of its own - it reads ``snap.mastered_at``;
* the verdict ("mastered") is computed server-side and travels with the plan,
  so the badge the student sees cannot disagree with the number the report
  quotes.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.agent import policy
from src.agent.learner_store import LearnerStore
from src.agent.serve import AgentService

ROOT = Path(__file__).resolve().parents[1]

# Everything that serves the shipped product.  None of these may touch the
# baseline formula or the baseline threshold planner.
PRODUCT_FILES = [
    ROOT / "src/api/app.py",
    ROOT / "src/api/agent.py",
    ROOT / "src/api/services.py",
    ROOT / "src/agent/serve.py",
    ROOT / "src/agent/policy.py",
    ROOT / "src/agent/learner_store.py",
]

BASELINE_MODULES = ("planner", "mastery", "student_model")


def _imported_names(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module:
                names.append(module)
            names.extend(f"{module}.{alias.name}" for alias in node.names)
    return names


# ---------------------------------------------------------------------------
# 1. one definition, one scale
# ---------------------------------------------------------------------------

def test_method_constants_are_declared_once() -> None:
    """The evaluated numbers live in policy.py, on the 0-100 scale."""
    assert policy.POLICY_ID == "adaptive-v1"
    assert policy.MASTERED == 80
    assert policy.EXPLAIN_CAP == 55
    assert policy.PRACTICE_GAIN == pytest.approx(0.6)
    assert (policy.DELTA_MIN, policy.DELTA_MAX) == (-10, 25)


def test_baseline_modules_are_not_on_the_product_path() -> None:
    """A regression here means the old formula sneaked back into the product."""
    offenders: list[str] = []
    for path in PRODUCT_FILES:
        for name in _imported_names(path):
            if any(name == mod or name.endswith("." + mod) for mod in BASELINE_MODULES):
                offenders.append(f"{path.name} -> {name}")
    assert offenders == [], "product code must not import baseline modules: " + ", ".join(offenders)


def test_config_does_not_duplicate_method_constants() -> None:
    """config.yaml keeps the baseline block, but the method's numbers are code."""
    text = (ROOT / "config.yaml").read_text(encoding="utf-8")
    assert "BASELINE configuration - NOT the method" in text
    # The 0-100 method values must not appear as YAML keys/values.
    assert "mastered_at:" not in text
    assert "explain_cap:" not in text
    assert "practice_gain:" not in text


# ---------------------------------------------------------------------------
# 2. the shipped UI has no threshold of its own
# ---------------------------------------------------------------------------

def test_frontend_reads_thresholds_from_the_agent() -> None:
    engine = (ROOT / "frontend/js/engine.js").read_text(encoding="utf-8")
    assert "snap.mastered_at" in engine
    assert "DEFAULT_MASTERED" not in engine
    assert "const MASTERED" not in engine
    # The display copy must come from the snapshot, not a literal.
    assert "masteryGoal" in engine


def test_views_do_not_reference_removed_constants() -> None:
    views = (ROOT / "frontend/js/views-student.js").read_text(encoding="utf-8")
    assert "Engine.MASTERED" not in views
    assert "Engine.EXPLAIN_CAP" not in views
    assert "Engine.masteryGoal(" in views


# ---------------------------------------------------------------------------
# 3. the verdict travels with the plan
# ---------------------------------------------------------------------------

@pytest.fixture()
def service(tmp_path: Path):
    svc = AgentService(store=LearnerStore(tmp_path / "p9.db"))
    try:
        yield svc
    finally:
        svc.close()


def _topic(topic_id: str, **overrides) -> dict:
    payload = {
        "topic_id": topic_id,
        "name": topic_id,
        "mastery": 30,
        "pretested": True,
        "explain_pages": 2,
        "quiz_count": 2,
        "order": 0,
    }
    payload.update(overrides)
    return payload


def test_plan_envelope_carries_the_method_thresholds(service: AgentService) -> None:
    plan = service.plan("m_p9", [_topic("a")])
    assert plan["policy"] == policy.POLICY_ID
    assert plan["mastered_at"] == policy.MASTERED
    assert plan["explain_cap"] == policy.EXPLAIN_CAP


def test_mastered_verdict_is_computed_once_on_the_server(service: AgentService) -> None:
    """Same mastery, different error book -> different verdict."""
    plan = service.plan(
        "m_p9_flags",
        [
            _topic("clean", mastery=85, order=0),
            _topic(
                "with_wrong",
                mastery=85,
                order=1,
                explain_done=True,
                mistakes=[{"q": "still wrong"}, {"q": "wrong too"}],
            ),
        ],
    )
    by_topic = plan["by_topic"]
    assert by_topic["clean"]["mastered"] is True
    assert by_topic["with_wrong"]["mastered"] is False
    # 85 >= 80 but the error book is not empty -> still scheduled for review.
    assert by_topic["with_wrong"]["activity"] == policy.REVIEW

    # The frontend's isMastered() reads exactly this flag; no local re-derivation.
    step_ids = [s["topic_id"] for s in plan["steps"]]
    assert "with_wrong" in step_ids
    assert "clean" not in step_ids
