"""P4 acceptance tests: OpenRouter free model wiring, key safety, LLM content.

Everything is offline: no API key and no network access. All LLM calls go
through an injected stub client, so the tests verify parsing / grounding /
degradation / trace logging without touching a real provider.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.agent import run as run_mod
from src.agent.evaluator import evaluate_step
from src.agent.llm_content import (
    LLMContentGenerator,
    QuestionParseError,
    parse_question_payload,
)
from src.agent.planner import choose_action
from src.agent.questions import questions_for
from src.retrieval.vector_store import RetrievedChunk
from src.utils import llm_client as lc
from src.utils import secret_guard as sg
from src.utils.config import PROJECT_ROOT, config_for_log, load_config
from src.utils.llm_client import LLMClient, LLMResponse, MissingAPIKeyError

VALID_QUESTION = json.dumps({
    "question": "Which quantity is the posterior in Bayes' theorem?",
    "options": {"A": "P(D)", "B": "P(H|D)", "C": "P(D|H)", "D": "P(H)"},
    "correct_option": "B",
    "explanation": "The posterior is P(H|D).",
}, ensure_ascii=False)


def _hit() -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id="c1",
        text="The posterior combines the prior and the likelihood.",
        lecture="Lecture 05",
        page=14,
        topic="Bayes Theorem",
        source="Source: Lecture 05, Page 14",
        distance=0.1,
    )


class StubLLMClient:
    """Minimal LLMClient replacement; never reads .env or the network."""

    def __init__(
        self,
        replies: list[str] | None = None,
        *,
        configured: bool = True,
        fail: bool = False,
        model: str = "stub-free:free",
    ) -> None:
        self.is_configured = configured
        self.max_tokens = 600
        self.model = model
        self.fail = fail
        self.replies = list(replies or [])
        self.messages: list[list[dict[str, str]]] = []

    def chat(self, messages, *, max_tokens=None, **kwargs) -> LLMResponse:
        self.messages.append(list(messages))
        if self.fail:
            raise RuntimeError("stub network failure")
        system = messages[0]["content"] if messages else ""
        if self.replies:
            text = self.replies.pop(0)
        elif "JSON object" in system:
            text = VALID_QUESTION
        else:
            text = "The posterior combines prior and likelihood.\nSource: FAKE BOOK, Page 999"
        return LLMResponse(
            text=text, model=self.model, latency_ms=12.5,
            prompt_tokens=30, completion_tokens=20, total_tokens=50,
            response_id="stub-001",
        )


def _generator(client, **overrides) -> LLMContentGenerator:
    import copy

    cfg = copy.deepcopy(load_config())
    cfg["agent"].update(overrides)
    return LLMContentGenerator(cfg, client=client)


# --- model & configuration --------------------------------------------------

def test_config_defaults_to_the_configured_model():
    cfg = load_config()
    assert cfg["llm"]["model"]  # a model is configured (paid model; not :free)
    assert "openrouter.ai" in cfg["llm"]["default_base_url"]
    # fallback list may be empty when a paid model is used
    assert isinstance(cfg["llm"]["free_fallback_models"], list)
    assert cfg["agent"]["llm_mode"] == "on"           # platform uses the LLM by default
    assert cfg["evaluation"]["subjective_rubric_enabled"] is False
    assert config_for_log(cfg)["llm_mode"] == "on"     # frozen snapshot matches config


def test_key_setup_hint_is_actionable_and_contains_no_secret():
    hint = lc.key_setup_message()
    assert ".env" in hint and "OPENROUTER_API_KEY" in hint
    assert ".gitignore" in hint and ("GitHub" in hint or "提交" in hint)
    assert "sk-or-your-key-here" not in hint
    client = LLMClient(load_config(), api_key="", client=object())
    with pytest.raises(MissingAPIKeyError, match="OPENROUTER_API_KEY"):
        client.require_key()


# --- secret safety -----------------------------------------------------------

def test_gitignore_blocks_all_local_env_variants():
    if not (PROJECT_ROOT / ".git").exists():
        pytest.skip("not a git checkout")

    def ignored(rel: str) -> bool:
        return subprocess.run(
            ["git", "check-ignore", "-q", rel], cwd=PROJECT_ROOT
        ).returncode == 0

    for rel in (".env", ".env.local", ".env.production", ".env.bak"):
        assert ignored(rel), f"{rel} must never be committable"
    assert not ignored(".env.example")
    assert not ignored("config.yaml")


def test_secret_guard_detects_real_keys_but_allows_placeholders():
    assert sg.scan_text("OPENROUTER_API_KEY=sk-or-your-key-here") == []
    fake = "sk-or-v1-" + "a" * 40
    hits = sg.scan_text(f"OPENROUTER_API_KEY={fake}")
    assert len(hits) == 1
    assert hits[0]["masked"].startswith("sk-or-")
    assert fake not in str(hits)          # never echo the full key
    assert sg.scan_text("note = 'the key stays in the local .env only'") == []


def test_secret_guard_on_repository_is_clean():
    report = sg.scan_repository(PROJECT_ROOT)
    assert report["gitignore_problems"] == []
    assert report["findings"] == []
    assert sg.main([]) == 0


def test_config_and_snapshot_never_contain_a_key():
    text = (PROJECT_ROOT / "config.yaml").read_text(encoding="utf-8")
    assert "sk-" not in text
    assert "api_key:" not in text.replace("api_key_env:", "")
    assert not any("key" in str(v).lower() for v in config_for_log().values())


# --- free model catalog & retired-ID fallback --------------------------------

def test_list_free_models_filters_the_public_catalog():
    payload = {"data": [
        {"id": "google/gemma-4-31b-it:free", "pricing": {"prompt": "0", "completion": "0"},
         "context_length": 262144},
        {"id": "paid/model", "pricing": {"prompt": "0.000001", "completion": "0.000002"}},
        {"id": "openrouter/free", "pricing": {"prompt": "0", "completion": "0"}},
    ]}
    urls: list[str] = []

    def fetch(url: str, timeout: float):
        urls.append(url)
        return payload

    models = lc.list_free_models(base_url="https://openrouter.ai/api/v1", fetcher=fetch)
    assert [m["id"] for m in models] == ["google/gemma-4-31b-it:free", "openrouter/free"]
    assert urls == ["https://openrouter.ai/api/v1/models"]


def test_retired_primary_model_falls_back_and_logs_actual_model():
    cfg = load_config()
    # 注入一个临时兜底列表，验证「主模型失效 -> 回退 -> 记录实际模型」机制
    cfg["llm"]["free_fallback_models"] = ["test/fallback-model"]
    seen: list[str] = []

    class _Completions:
        def create(self, **kwargs):
            seen.append(kwargs["model"])
            if len(seen) == 1:
                raise RuntimeError("404: model not found (ID retired)")
            return SimpleNamespace(
                id="gen-2", model=kwargs["model"],
                choices=[SimpleNamespace(message=SimpleNamespace(content="OK"))],
                usage=SimpleNamespace(prompt_tokens=2, completion_tokens=1, total_tokens=3),
            )

    fake = SimpleNamespace(chat=SimpleNamespace(completions=_Completions()))
    client = LLMClient(cfg, api_key="test-key", client=fake)
    result = client.smoke()
    assert seen == [cfg["llm"]["model"], cfg["llm"]["free_fallback_models"][0]]
    assert result.model == cfg["llm"]["free_fallback_models"][0]
    assert result.text == "OK"


# --- structured question parsing ---------------------------------------------

def test_question_json_with_fences_and_aliases_is_evaluator_compatible():
    fenced = "```json\n" + json.dumps({
        "stem": "Which term normalizes the numerator?",
        "choices": ["P(D)", "P(H)", "P(D|H)", "P(H|D)"],
        "answer": "A) P(D)",
    }) + "\n```"
    mcq = parse_question_payload(fenced, topic="Bayes Theorem", difficulty="mcq", index=0)
    assert mcq.question_id.startswith("llm-")
    assert set(mcq.options) == {"A", "B", "C", "D"} and mcq.correct_option == "A"
    question = mcq.as_dict()
    assert evaluate_step(question, "correct").correct is True
    assert evaluate_step(question, "correct").performance == 1.0
    assert evaluate_step(question, "wrong").correct is False


@pytest.mark.parametrize("payload", [
    "not json at all",
    '{"question": "Q?", "options": {"A": "x", "B": "x"}, "correct_option": "A"}',
    '{"question": "Q?", "options": {"A": "x", "B": "y", "C": "z"}, "correct_option": "Z"}',
    '{"question": "Q?", "options": ["x", "y"], "correct_option": "A"}',
])
def test_bad_question_json_is_rejected_not_scored(payload):
    with pytest.raises(QuestionParseError):
        parse_question_payload(payload, topic="t", difficulty="mcq", index=0)


# --- grounded content & degradation ------------------------------------------

def test_grounded_teach_uses_p1_citation_not_llm_text():
    client = StubLLMClient()
    gen = _generator(client)
    result = gen.teach("Bayes Theorem", [_hit()])
    assert result.source == "llm" and result.grounded is True
    assert result.citation == "Source: Lecture 05, Page 14"
    assert result.lecture == "Lecture 05" and result.page == 14
    assert "FAKE BOOK" not in result.text
    assert result.text.rstrip().endswith("Source: Lecture 05, Page 14")
    assert result.usage["total_tokens"] == 50 and result.usage["model"] == "stub-free:free"


def test_missing_key_degrades_to_template_and_static_bank():
    client = StubLLMClient(configured=False)
    gen = _generator(client)
    with pytest.warns(UserWarning, match="OPENROUTER_API_KEY"):
        teach = gen.teach("Bayes Theorem", [_hit()])
    assert teach.source == "template" and teach.grounded is True
    assert teach.citation == "Source: Lecture 05, Page 14"   # still verified
    # The missing-key warning is emitted once per generator, so use a fresh one.
    gen2 = _generator(client)
    with pytest.warns(UserWarning, match="OPENROUTER_API_KEY"):
        question = gen2.question(
            "Bayes Theorem", [], difficulty="mcq", index=0,
            bank_question=questions_for("Bayes Theorem")[0],
        )
    assert question.source == "bank" and question.question.question_id == "bayes-q1"
    assert client.messages == []                              # no network call


def test_empty_retrieval_degrades_teach_but_questions_still_work():
    client = StubLLMClient(replies=[VALID_QUESTION])
    gen = _generator(client)
    teach = gen.teach("Bayes Theorem", [])
    assert teach.source == "template" and teach.citation is None
    assert teach.grounded is False
    assert "离线占位" in teach.text
    with pytest.warns(UserWarning, match="no lecture excerpt"):
        question = gen.question(
            "Bayes Theorem", [], difficulty="mcq", index=0,
            bank_question=questions_for("Bayes Theorem")[0],
        )
    assert question.source == "llm"
    assert evaluate_step(question.question.as_dict(), "correct").correct is True


def test_unparsable_json_retries_then_falls_back_to_bank():
    client = StubLLMClient(replies=["bad reply one", "bad reply two"])
    gen = _generator(client, max_json_retries=1)
    bank = questions_for("Bayes Theorem")[0]
    with pytest.warns(UserWarning, match="degraded"):
        result = gen.question(
            "Bayes Theorem", [_hit()], difficulty="challenge", index=2, bank_question=bank,
        )
    assert result.source == "fallback" and result.question == bank
    assert result.usage["llm_calls"] == 2 and result.usage["total_tokens"] == 100
    assert evaluate_step(result.question.as_dict(), "wrong").correct is False


def test_llm_exceptions_do_not_break_the_loop():
    client = StubLLMClient(fail=True)
    gen = _generator(client)
    with pytest.warns(UserWarning, match="degrading"):
        teach = gen.teach("Bayes Theorem", [_hit()])
    assert teach.source == "template"
    assert teach.citation == "Source: Lecture 05, Page 14"
    with pytest.warns(UserWarning, match="degraded"):
        result = gen.question(
            "Bayes Theorem", [_hit()], difficulty="mcq", index=0,
            bank_question=questions_for("Bayes Theorem")[0],
        )
    assert result.source == "fallback"


# --- extended planner --------------------------------------------------------

def test_extended_planner_routes_challenge_review_and_done():
    cfg = load_config()

    def decide(mastery, errors=None, last=None, correct=None):
        return choose_action(
            mastery=mastery, recent_errors=errors or [], last_action=last,
            config=cfg, extended=True, last_was_correct=correct,
        )

    assert decide(0.0).action == "Teach"
    assert decide(0.5, last="Teach").action == "MCQ"
    assert decide(0.75).action == "Challenge"
    assert decide(0.5, errors=[{"question_id": "q1"}, {"question_id": "q2"}]).action == "Review"
    # A correct answer stops old errors from triggering endless Review.
    assert decide(0.5, errors=[{"question_id": "q1"}, {"question_id": "q2"}],
                  correct=True).action == "MCQ"
    assert decide(0.5, last="Review").action == "MCQ"
    assert decide(0.9).action == "Done" and decide(0.9).reached_target
    assert decide(0.75) == decide(0.75)


# --- full P4 loop ------------------------------------------------------------

def test_p4_loop_with_stub_llm_logs_usage_and_four_activities(tmp_path):
    client = StubLLMClient()
    hit = _hit()
    result = run_mod.run_cli_session(
        student_id="p4", topic="Bayes Theorem",
        answers="wrong,wrong,correct,correct,correct,correct,correct,correct",
        max_steps=16, db_path=tmp_path / "p4.db", use_rag=True,
        retriever=lambda topic: [hit],
        llm_client=client,   # no llm_mode: the default path must use the LLM
    )

    assert result.llm_mode == "on" and result.llm_calls >= 8
    assert set(result.actions) == {"Teach", "MCQ", "Challenge", "Review"}
    assert result.final_mastery > result.initial_mastery
    assert result.stop_reason == "target_mastery"
    assert len(result.events) == result.steps

    for event in result.events:
        assert event["config_snapshot"]
        assert event["total_tokens"] == 50 and event["prompt_tokens"] == 30
        assert event["model"] == "stub-free:free"     # model that actually answered
        assert event["cost_usd"] == 0.0               # :free model
        assert event["citation"] == "Source: Lecture 05, Page 14"
        assert event["action_reason"]
        assert event["latency_ms"] >= 0
        if event["action"] in ("Teach", "Review"):
            assert event["evaluation_result"] == "skipped"
            assert event["mastery_before"] == event["mastery_after"]
        else:
            assert event["evaluation_result"] in ("correct", "incorrect")
            assert event["question_id"].startswith("llm-")


def test_default_mode_without_key_fails_with_setup_hint(tmp_path, monkeypatch, capsys):
    class _Unconfigured:
        def __init__(self, *args, **kwargs):
            self.is_configured = False

    monkeypatch.setattr(run_mod, "LLMClient", _Unconfigured)
    with pytest.raises(MissingAPIKeyError, match="OPENROUTER_API_KEY"):
        run_mod.run_cli_session(db_path=tmp_path / "on.db", use_rag=False)
    # No flag at all: the CLI default itself must require the key.
    assert run_mod.main([
        "--db", str(tmp_path / "cli.db"), "--no-rag",
    ]) == 2
    assert ".env" in capsys.readouterr().err


def test_default_mode_never_silently_degrades_without_key(tmp_path):
    client = StubLLMClient(configured=False)
    with pytest.raises(MissingAPIKeyError, match="OPENROUTER_API_KEY"):
        run_mod.run_cli_session(
            answers="correct", max_steps=4, db_path=tmp_path / "default.db",
            use_rag=False, llm_client=client,
        )
    assert client.messages == []


def test_offline_flag_is_the_only_no_key_escape_hatch(tmp_path, capsys):
    code = run_mod.main([
        "--offline", "--answers", "correct", "--max-steps", "4",
        "--db", str(tmp_path / "offline.db"), "--no-rag",
    ])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["steps"] == 4 and payload["llm_mode"] == "off"
    assert payload["llm_calls"] == 0 and payload["llm_model"] is None


def test_removed_mode_flags_are_rejected():
    with pytest.raises(SystemExit) as exc:
        run_mod.main(["--llm-mode", "on", "--no-rag"])
    assert exc.value.code == 2


def test_cli_check_reports_configured_model_without_network(capsys):
    assert lc.main(["--check"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["model"]  # a model is configured (paid model; not :free)
    assert payload["api_key"] in ("configured", "missing/placeholder")
    assert payload["effective_config"]["llm_mode"] == "on"
