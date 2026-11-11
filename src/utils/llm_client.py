"""Minimal OpenAI-compatible LLM client (P0 smoke call).

OpenRouter is OpenAI-compatible, so a plain ``openai.OpenAI`` client with a
custom ``base_url`` is enough; switching models only changes ``llm.model`` in
config.yaml (see README 6 / 15).

Design notes for later phases:
  * ``LLMResponse`` already carries latency + token counts, so P2's events table
    and P6's frozen runs can log without re-instrumenting the client.
  * ``cost_usd`` is intentionally ``None`` at P0: OpenRouter pricing is filled
    in at P6 (from the /generation endpoint or a fixed price table).
  * No network call happens at import time; ``--check`` works fully offline.

Usage:
    python -m src.utils.llm_client --check      # offline config check
    python -m src.utils.llm_client              # real minimal call (needs .env key)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from typing import Any, Sequence

from .config import config_for_log, get_config, get_env

PLACEHOLDER_KEYS = ("sk-or-your-key-here", "your-key-here", "changeme")


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
    cost_usd: float | None = None  # P6: fill from OpenRouter pricing
    response_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


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
        # ``client`` is injectable so the P0 test suite can verify the call
        # path offline with a stub, without an API key or network access.
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

    def chat(
        self,
        messages: Sequence[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Send one chat request and return text + latency + token usage."""
        if not self.is_configured:
            raise MissingAPIKeyError(
                "LLM API key missing or still a placeholder. Copy .env.example to "
                ".env and set OPENROUTER_API_KEY (never commit .env)."
            )
        if not messages:
            raise ValueError("messages must not be empty")

        request: dict[str, Any] = {
            "model": self.model,
            "messages": list(messages),
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": self.max_tokens if max_tokens is None else max_tokens,
        }

        started = time.perf_counter()
        response = self._client().chat.completions.create(**request)
        latency_ms = (time.perf_counter() - started) * 1000.0

        usage = getattr(response, "usage", None)
        return LLMResponse(
            text=(response.choices[0].message.content or "").strip(),
            model=getattr(response, "model", None) or self.model,
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
    parser = argparse.ArgumentParser(description="P0 LLM smoke test")
    parser.add_argument(
        "--check",
        action="store_true",
        help="offline only: print effective config and key status, no network call",
    )
    parser.add_argument("--prompt", default="Reply with exactly: P0 OK")
    args = parser.parse_args(argv)

    cfg = get_config()
    client = LLMClient(cfg)

    if args.check:
        print(json.dumps(
            {
                "effective_config": config_for_log(cfg),
                "base_url": client.base_url,
                "api_key": "configured" if client.is_configured else "missing/placeholder",
            },
            ensure_ascii=False,
            indent=2,
        ))
        return 0

    if not client.is_configured:
        print(
            "[P0 smoke] skipped: OPENROUTER_API_KEY is missing or still a placeholder.\n"
            "  cp .env.example .env  ->  fill in the real key  ->  rerun this command.\n"
            "  Offline check is available: python -m src.utils.llm_client --check",
            file=sys.stderr,
        )
        return 2

    try:
        result = client.smoke(args.prompt)
    except Exception as exc:  # network / auth / model errors must be explicit
        print(f"[P0 smoke] FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
