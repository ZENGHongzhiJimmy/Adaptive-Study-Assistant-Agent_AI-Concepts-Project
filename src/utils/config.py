"""Central configuration loader (P0).

Rules (see README 12.2 "配置集中"):
  * ``config.yaml`` is the single source of truth for all non-sensitive knobs
    (model, paths, retrieval, seed). It is frozen at P6.
  * ``.env`` holds ONLY secrets and service endpoints; it is never committed.
  * ``config_for_log()`` returns the flat snapshot that a run writes into its
    metadata (model / embedding / seed / version), so a result can always be
    traced back to a config.

Precedence: config.yaml defaults -> environment variables (secrets/endpoint only).
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"

REQUIRED_SECTIONS = ("platform", "llm", "paths", "experiment")


class ConfigError(RuntimeError):
    """Raised when config.yaml is missing or structurally invalid."""


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """Read config.yaml and load .env into os.environ (existing env wins).

    A relative ``path`` is resolved against the project root, so the loader
    behaves the same from any working directory.
    """
    cfg_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    if not cfg_path.is_absolute():
        cfg_path = PROJECT_ROOT / cfg_path

    load_dotenv(PROJECT_ROOT / ".env", override=False)

    if not cfg_path.is_file():
        raise ConfigError(f"config.yaml not found: {cfg_path}")
    with cfg_path.open("r", encoding="utf-8") as fh:
        config = yaml.safe_load(fh)
    if not isinstance(config, dict):
        raise ConfigError(f"config.yaml must contain a mapping: {cfg_path}")

    missing = [key for key in REQUIRED_SECTIONS if key not in config]
    if missing:
        raise ConfigError(f"config.yaml missing sections: {', '.join(missing)}")
    return config


@lru_cache(maxsize=1)
def get_config() -> dict[str, Any]:
    """Cached effective config for runtime use."""
    return load_config()


def get_env(name: str, default: str | None = None) -> str | None:
    """Read a secret/endpoint from the environment (loaded from .env)."""
    return os.getenv(name, default)


def resolve_path(value: str | Path) -> Path:
    """Resolve a configured path against the project root if it is relative."""
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def config_for_log(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Flat, secret-free snapshot for run metadata / experiment provenance.

    Only infrastructure knobs live here. The evaluated method's numbers are in
    ``src/agent/policy.py`` and are deliberately not copied into this snapshot.
    """
    cfg = config or get_config()
    return {
        "platform_version": cfg["platform"]["version"],
        "model": cfg["llm"]["model"],
        "embedding_model": cfg.get("embedding", {}).get("model"),
        "embedding_backend": cfg.get("embedding", {}).get("backend"),
        "retrieval_top_k": cfg.get("retrieval", {}).get("top_k"),
        "planner_mode": cfg.get("agent", {}).get("planner", "llm"),
        "subjective_rubric_enabled": cfg.get("evaluation", {}).get(
            "subjective_rubric_enabled", False
        ),
        "seed": cfg["experiment"]["seed"],
    }
