"""OpenAI-compatible LLM client for OpenRouter (P0 smoke call + P4 content).

OpenRouter is OpenAI-compatible, so a plain ``openai.OpenAI`` client with a
custom ``base_url`` is enough; switching models only changes ``llm.model`` in
config.yaml (see README 9.3 / 15). P4 defaults to a free ``:free`` model.

Key safety (P4):
  * The real key is read ONLY from the local, git-ignored ``.env``.
  * It is never printed, never written into events/config snapshots, and never
    placed in config.yaml (``python -m src.utils.secret_guard`` enforces this).
  * When it is missing, ``key_setup_message()`` gives explicit setup steps and
    callers fail fast (exit code 2). The platform defaults to using the LLM;
    ``python -m src.agent.run --offline`` is the explicit no-key opt-out.

Usage:
    python -m src.utils.llm_client --check              # offline config/status
    python -m src.utils.llm_client --list-free-models   # live free IDs, no key
    python -m src.utils.llm_client                      # real minimal call
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from dataclasses import asdict, dataclass
from typing import Any, Callable, Sequence

from .config import config_for_log, get_config, get_env

PLACEHOLDER_KEYS = (
    "sk-or-your-key-here", "your-key-here", "changeme", "your-api-key",
)

KEY_SETUP_HINT = """\
[需要在本地 .env 填入 OpenRouter API Key / API key required]
  1) 复制模板（Windows PowerShell: Copy-Item .env.example .env）：
         cp .env.example .env
  2) 在 https://openrouter.ai/keys 创建 key，填入 .env 的 OPENROUTER_API_KEY=
  3) 确认 .env 已被忽略：  python -m src.utils.secret_guard
  4) 重新运行本命令。
  5) 平台默认接入 LLM，不会静默降级；仅测试/无 Key 场景用 --offline 显式离线运行。

  !! 安全提醒：真实 key 只能放在本地 .env，绝不写入 config.yaml / 代码 /
     README / 测试，也绝不提交到 GitHub。.env 已在 .gitignore 中忽略。
  !! If a key is ever committed or pasted anywhere public, revoke it at
     https://openrouter.ai/keys immediately.\
"""


def key_setup_message() -> str:
    """Single source of truth for the missing-key prompt (CLI + agent loop)."""
    return KEY_SETUP_HINT


class MissingAPIKeyError(RuntimeError):
    """Raised when the LLM key is absent or still the .env.example placeholder."""


@dataclass
class LLMResponse:
    """Normalized result of one chat completion (safe to log/serialize)."""

    text: str
    model: str
    latency_ms: float
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cost_usd: float | None = None  # :free models are 0.0; P6 fills paid pricing
    response_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def list_free_models(
    *,
    base_url: str | None = None,
    timeout_s: float = 15.0,
    fetcher: Callable[[str, float], Any] | None = None,
) -> list[dict[str, Any]]:
    """Query OpenRouter's public /models catalog for zero-cost text models.

    No API key is needed. Free IDs rotate often, so this is the recommended way
    to refresh ``config.yaml -> llm.model``. ``fetcher`` is injectable for tests.
    """
    url = (base_url or get_env("OPENROUTER_BASE_URL")
           or get_config()["llm"].get("default_base_url", "https://openrouter.ai/api/v1"))
    url = url.rstrip("/") + "/models"

    if fetcher is None:
        def fetch(target: str, timeout: float) -> Any:
            request = urllib.request.Request(
                target, headers={"User-Agent": "adaptive-study-assistant/0.1"}
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response)

        fetcher = fetch

    payload = fetcher(url, timeout_s)
    models = payload.get("data", []) if isinstance(payload, dict) else []
    result: list[dict[str, Any]] = []
    for model in models:
        model_id = str(model.get("id", ""))
        pricing = model.get("pricing") or {}
        try:
            free_by_price = (
                float(pricing.get("prompt", "1") or 1) == 0.0
                and float(pricing.get("completion", "1") or 1) == 0.0
            )
        except (TypeError, ValueError):
            free_by_price = False
        if model_id.endswith(":free") or (free_by_price and "free" in model_id):
            result.append({"id": model_id, "context_length": model.get("context_length")})
    return sorted(result, key=lambda item: item["id"])


def _model_unavailable(exc: Exception) -> bool:
    """True only for 'this model does not exist' errors, not for auth/quota."""
    if type(exc).__name__ in {"NotFoundError", "ModelNotFoundError"}:
        return True
    text = str(exc).lower()
    markers = (
        "404", "not found", "no such model", "is not a valid model",
        "model is not available", "model_not_found", "unknown model",
        "no endpoints found",
    )
    return any(marker in text for marker in markers)


class LLMClient:
    """Thin wrapper over the OpenAI-compatible chat completions endpoint."""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        client: Any = None,
    ) -> None:
        cfg = config or get_config()
        llm = cfg["llm"]
        self.config = cfg
        self.model = llm["model"]
        self.fallback_models = [
            str(m) for m in llm.get("free_fallback_models", []) if str(m) != self.model
        ]
        self.temperature = float(llm.get("temperature", 0.0))
        self.max_tokens = int(llm.get("max_tokens", 256))
        self.timeout_s = float(llm.get("timeout_s", 30))
        self.max_retries = int(llm.get("max_retries", 2))

        key_env = llm.get("api_key_env", "OPENROUTER_API_KEY")
        url_env = llm.get("base_url_env", "OPENROUTER_BASE_URL")
        self.api_key = api_key if api_key is not None else get_env(key_env)
        self.base_url = base_url if base_url is not None else (
            get_env(url_env) or llm.get("default_base_url", "https://openrouter.ai/api/v1")
        )
        # ``client`` is injectable so tests can verify the call path offline
        # with a stub, without an API key or network access.
        self._injected_client = client

    @property
    def is_configured(self) -> bool:
        if not self.api_key:
            return False
        lowered = self.api_key.strip().lower()
        return bool(lowered) and not any(p in lowered for p in PLACEHOLDER_KEYS)

    def _client(self) -> Any:
        if self._injected_client is not None:
            return self._injected_client
        # Lazy import: importing config utilities does not require the SDK.
        from openai import OpenAI

        return OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout_s,
            max_retries=self.max_retries,
        )

    def require_key(self) -> None:
        """Fail with actionable .env instructions (never echo the key)."""
        if not self.is_configured:
            raise MissingAPIKeyError(
                "OPENROUTER_API_KEY is missing or still a placeholder.\n"
                + key_setup_message()
            )

    def chat(
        self,
        messages: Sequence[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        model: str | None = None,
    ) -> LLMResponse:
        """Send one chat request and return text + latency + token usage.

        If the configured free model has been retired, retry the same request on
        ``llm.free_fallback_models`` (free IDs rotate). ``LLMResponse.model``
        always reports the model that actually answered, so P6 logs are honest.
        """
        self.require_key()
        if not messages:
            raise ValueError("messages must not be empty")

        request: dict[str, Any] = {
            "model": model or self.model,
            "messages": list(messages),
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": self.max_tokens if max_tokens is None else max_tokens,
        }
        candidates = [request["model"], *self.fallback_models]
        started = time.perf_counter()

        for index, candidate in enumerate(candidates):
            request["model"] = candidate
            try:
                response = self._client().chat.completions.create(**request)
                break
            except Exception as exc:
                if index + 1 < len(candidates) and _model_unavailable(exc):
                    continue
                raise

        latency_ms = (time.perf_counter() - started) * 1000.0
        usage = getattr(response, "usage", None)
        return LLMResponse(
            text=(response.choices[0].message.content or "").strip(),
            model=getattr(response, "model", None) or request["model"],
            latency_ms=round(latency_ms, 2),
            prompt_tokens=getattr(usage, "prompt_tokens", None),
            completion_tokens=getattr(usage, "completion_tokens", None),
            total_tokens=getattr(usage, "total_tokens", None),
            response_id=getattr(response, "id", None),
        )

    def smoke(self, prompt: str = "Reply with exactly: P0 OK") -> LLMResponse:
        """Single-message minimal call used by the P0 acceptance check."""
        return self.chat([{"role": "user", "content": prompt}])


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OpenRouter LLM smoke / setup check")
    parser.add_argument(
        "--check",
        action="store_true",
        help="offline only: print effective config and key status, no network call",
    )
    parser.add_argument(
        "--list-free-models",
        action="store_true",
        help="list current zero-cost OpenRouter models from the public catalog (no key)",
    )
    parser.add_argument("--prompt", default="Reply with exactly: P0 OK")
    args = parser.parse_args(argv)

    cfg = get_config()
    client = LLMClient(cfg)

    if args.list_free_models:
        try:
            models = list_free_models(base_url=client.base_url)
        except Exception as exc:
            print(f"[models] FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(
            {"configured_model": client.model, "free_model_count": len(models),
             "free_models": models},
            ensure_ascii=False, indent=2,
        ))
        return 0

    if args.check:
        print(json.dumps(
            {
                "effective_config": config_for_log(cfg),
                "base_url": client.base_url,
                "api_key": "configured" if client.is_configured else "missing/placeholder",
                "model": client.model,
                "free_fallback_models": client.fallback_models,
                "key_setup_hint": None if client.is_configured else key_setup_message(),
            },
            ensure_ascii=False,
            indent=2,
        ))
        return 0

    if not client.is_configured:
        print(
            "[P0/P4 smoke] skipped: OPENROUTER_API_KEY is missing or still a placeholder.",
            file=sys.stderr,
        )
        print(key_setup_message(), file=sys.stderr)
        print(
            "  Offline checks: python -m src.utils.llm_client --check "
            "| --list-free-models",
            file=sys.stderr,
        )
        return 2

    try:
        result = client.smoke(args.prompt)
    except Exception as exc:  # network / auth / model errors must be explicit
        print(f"[P0/P4 smoke] FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
