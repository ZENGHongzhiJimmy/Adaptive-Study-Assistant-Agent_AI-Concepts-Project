"""LLM transport acceptance tests: model wiring, key safety, provider fallback.

Everything is offline: no API key and no network access. The client is driven
through injected stubs, so the tests verify configuration, secret handling and
the fallback path without touching a real provider.

The content-generation tests that used to live here were removed together with
the legacy CLI loop (``src/agent/llm_content.py``); the product's decision path
is covered by ``test_p11_llm_decision.py`` and ``test_p8_agent_api.py``.
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from src.utils import llm_client as lc
from src.utils import secret_guard as sg
from src.utils.config import PROJECT_ROOT, config_for_log, load_config
from src.utils.llm_client import LLMClient, MissingAPIKeyError


# --- model & configuration --------------------------------------------------

def test_config_defaults_to_the_configured_model():
    cfg = load_config()
    assert cfg["llm"]["model"]  # a model is configured (paid model; not :free)
    assert "openrouter.ai" in cfg["llm"]["default_base_url"]
    # fallback list may be empty when a paid model is used
    assert isinstance(cfg["llm"]["fallback_models"], list)
    assert cfg["agent"]["planner"] == "llm"            # the model makes the decision
    assert cfg["evaluation"]["subjective_rubric_enabled"] is False
    assert config_for_log(cfg)["planner_mode"] == "llm"  # snapshot matches config


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
    cfg["llm"]["fallback_models"] = ["test/fallback-model"]
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
    assert seen == [cfg["llm"]["model"], cfg["llm"]["fallback_models"][0]]
    assert result.model == cfg["llm"]["fallback_models"][0]
    assert result.text == "OK"
