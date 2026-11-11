"""P0 acceptance tests: config, secrets, skeleton, dependencies, LLM stub call.

All tests are offline - no API key and no network access required.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.utils.config import PROJECT_ROOT, config_for_log, load_config, resolve_path
from src.utils import llm_client as lc

EXPECTED_PACKAGES = {
    "langgraph", "langchain-openrouter", "openai", "pymupdf",
    "sentence-transformers", "chromadb", "streamlit", "pandas",
    "scikit-learn", "matplotlib", "python-dotenv", "PyYAML", "pytest",
}

SKELETON_DIRS = (
    "src/utils", "src/ingestion", "src/retrieval", "src/student_model",
    "src/agent/prompts", "src/ui", "experiments/baselines",
    "experiments/results", "tests", "report",
    "data/course_materials", "data/chroma_db",
)


# --- config.yaml ------------------------------------------------------------

def test_config_loads_required_sections():
    cfg = load_config()
    for section in ("platform", "llm", "paths", "mastery", "planner", "experiment"):
        assert section in cfg
    assert 0.0 <= float(cfg["mastery"]["alpha"]) <= 1.0
    t = cfg["planner"]["thresholds"]
    assert t["teach_below"] < t["practice_below"] < t["challenge_below"]
    assert cfg["planner"]["max_steps"] > 0  # back-edge must terminate
    assert cfg["evaluation"]["subjective_rubric_enabled"] is False  # P3 MCQ-only
    assert isinstance(cfg["experiment"]["seed"], int)


def test_config_for_log_is_secret_free_and_stable():
    snapshot = config_for_log(load_config())
    assert set(snapshot) == {
        "platform_version", "model", "embedding_model", "embedding_backend",
        "retrieval_top_k", "mastery_alpha", "planner_mode",
        "planner_thresholds", "target_mastery", "max_steps",
        "subjective_rubric_enabled", "seed",
    }
    assert not any("key" in str(v).lower() for v in snapshot.values())
    json.dumps(snapshot)  # must be serializable into events


def test_config_yaml_contains_no_secret_value():
    text = (PROJECT_ROOT / "config.yaml").read_text(encoding="utf-8")
    assert "sk-" not in text
    assert "api_key:" not in text.replace("api_key_env:", "")


def test_resolve_path_is_anchored_to_project_root():
    assert resolve_path("./data/student_state.db") == PROJECT_ROOT / "data/student_state.db"


# --- secrets ----------------------------------------------------------------

def test_env_example_uses_placeholder_only():
    text = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
    values = dict(
        line.split("=", 1)
        for line in text.splitlines()
        if line and not line.startswith("#") and "=" in line
    )
    assert values["OPENROUTER_API_KEY"].strip() == "sk-or-your-key-here"
    assert values["OPENROUTER_BASE_URL"].strip().startswith("https://")
    assert "MASTERY_ALPHA" not in values  # non-sensitive knobs live in config.yaml


def test_gitignore_protects_secrets_and_data_but_keeps_template():
    if not (PROJECT_ROOT / ".git").exists():
        pytest.skip("not a git checkout")

    def ignored(rel: str) -> bool:
        return subprocess.run(
            ["git", "check-ignore", "-q", rel], cwd=PROJECT_ROOT
        ).returncode == 0

    assert ignored(".env")
    assert ignored("data/course_materials/lecture05.pdf")
    assert ignored("data/chroma_db/index.bin")
    assert ignored("data/student_state.db")
    assert not ignored(".env.example")
    assert not ignored("data/course_materials/.gitkeep")


# --- dependency set & skeleton ----------------------------------------------

def test_requirements_declares_full_p0_set_with_upper_bounds():
    lines = [
        line.strip() for line in (PROJECT_ROOT / "requirements.txt").read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    names = {re.match(r"^([A-Za-z0-9_.-]+)", line).group(1).lower() for line in lines}
    assert {p.lower() for p in EXPECTED_PACKAGES} <= names
    assert all("<" in line for line in lines)  # no unbounded major upgrades


def test_directory_skeleton_exists():
    for rel in SKELETON_DIRS:
        assert (PROJECT_ROOT / rel).is_dir(), f"missing directory: {rel}"
    for rel in (
        "src/agent/prompts/.gitkeep", "experiments/results/.gitkeep",
        "report/.gitkeep", "data/course_materials/.gitkeep",
        "data/chroma_db/.gitkeep",
    ):
        assert (PROJECT_ROOT / rel).is_file(), f"missing placeholder: {rel}"


# --- LLM client (offline stub) ----------------------------------------------

class _FakeCompletions:
    def __init__(self, recorder: dict):
        self.recorder = recorder

    def create(self, **kwargs):
        self.recorder.update(kwargs)
        return SimpleNamespace(
            id="gen-fake-001",
            model=kwargs["model"],
            choices=[SimpleNamespace(message=SimpleNamespace(content="  P0 OK  "))],
            usage=SimpleNamespace(prompt_tokens=7, completion_tokens=3, total_tokens=10),
        )


class _FakeClient:
    def __init__(self):
        self.recorder: dict = {}
        self.chat = SimpleNamespace(completions=_FakeCompletions(self.recorder))


def test_llm_client_smoke_call_parses_response_and_usage():
    cfg = load_config()
    fake = _FakeClient()
    client = lc.LLMClient(
        cfg, api_key="test-key", base_url="https://example.invalid/v1", client=fake
    )
    result = client.smoke("Reply with exactly: P0 OK")

    assert result.text == "P0 OK"
    assert result.model == cfg["llm"]["model"]
    assert result.total_tokens == 10
    assert result.prompt_tokens == 7
    assert result.latency_ms >= 0
    assert result.cost_usd is None  # filled at P6
    assert fake.recorder["model"] == cfg["llm"]["model"]
    assert fake.recorder["messages"] == [
        {"role": "user", "content": "Reply with exactly: P0 OK"}
    ]
    assert result.as_dict()["response_id"] == "gen-fake-001"


@pytest.mark.parametrize("key", [None, "", "sk-or-your-key-here"])
def test_missing_or_placeholder_key_fails_loudly(key):
    client = lc.LLMClient(load_config(), api_key="" if key is None else key, client=_FakeClient())
    assert client.is_configured is False
    with pytest.raises(lc.MissingAPIKeyError, match="OPENROUTER_API_KEY"):
        client.smoke()


def test_cli_check_is_offline_and_prints_effective_config(capsys):
    assert lc.main(["--check"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert "platform_version" in payload["effective_config"]
    assert payload["effective_config"]["seed"] == load_config()["experiment"]["seed"]
    assert "base_url" in payload


def test_cli_without_key_skips_without_network(monkeypatch, capsys):
    class _Unconfigured(lc.LLMClient):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.api_key = ""

    monkeypatch.setattr(lc, "LLMClient", _Unconfigured)
    assert lc.main([]) == 2
    assert "skipped" in capsys.readouterr().err
