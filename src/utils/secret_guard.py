"""Pre-commit secret guard (P4).

The OpenRouter key must live ONLY in the local, git-ignored ``.env``. This
module is a deliberately simple second line of defence:

  * verify ``.env`` is ignored and ``.env.example`` is still tracked;
  * scan tracked + untracked-but-not-ignored text files for real-looking keys
    (and for ``OPENROUTER_API_KEY=<value>`` assignments);
  * never print a full key, only a masked prefix.

Run before every commit::

    python -m src.utils.secret_guard

It is also exercised by ``tests/test_p4_llm.py`` so the rule cannot silently
rot. No network access and no API key are required.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterator, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Placeholders are documentation values, not credentials.
PLACEHOLDER_VALUES = frozenset({
    "sk-or-your-key-here", "your-key-here", "changeme", "your-api-key",
    "your_api_key_here", "<your-key>", "xxx", "none", "null",
})
PLACEHOLDER_MARKERS = ("your-key", "your_key", "your-api", "changeme", "<", "example")

KEY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("OpenRouter key", re.compile(r"sk-or-v1-[A-Za-z0-9]{20,}")),
    ("OpenAI-style key", re.compile(r"\bsk-[A-Za-z0-9_-]{32,}")),
)
ENV_ASSIGNMENT = re.compile(
    r"^\s*(?:OPENROUTER_API_KEY|OPENAI_API_KEY|API_KEY)\s*=\s*[\"']?([^\"'#\s]+)[\"']?",
    re.MULTILINE | re.IGNORECASE,
)

TEXT_SUFFIXES = frozenset({
    ".py", ".md", ".txt", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".json", ".sql", ".sh", ".ps1", ".bat", ".ipynb", ".rst", ".csv",
    ".env.example",
})
SKIP_DIRS = frozenset({
    ".git", ".venv", "venv", "env", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", "node_modules", "build", "dist",
    "data/chroma_db",
})
MAX_FILE_BYTES = 1_000_000


def mask(value: str) -> str:
    """Never echo a credential in full, not even in an error message."""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:6]}...{value[-2:]}(len={len(value)})"


def _is_placeholder(value: str) -> bool:
    lowered = value.strip().strip("\"'").lower()
    if not lowered or lowered in PLACEHOLDER_VALUES:
        return True
    return any(marker in lowered for marker in PLACEHOLDER_MARKERS)


def scan_text(text: str) -> list[dict[str, str]]:
    """Return masked findings for one file's content (pure, no I/O)."""
    findings: dict[str, dict[str, str]] = {}
    for kind, pattern in KEY_PATTERNS:
        for match in pattern.finditer(text):
            value = match.group(0)
            if _is_placeholder(value):
                continue
            findings[value] = {
                "kind": kind,
                "masked": mask(value),
                "line": str(text.count("\n", 0, match.start()) + 1),
            }
    for match in ENV_ASSIGNMENT.finditer(text):
        value = match.group(1)
        if _is_placeholder(value):
            continue
        # Short values are test stubs / variable names, not live credentials.
        if len(value) < 20 and not value.startswith("sk-"):
            continue
        if value.startswith(("${", "$(", "os.getenv", "getenv(")):
            continue
        findings.setdefault(value, {
            "kind": "API key assignment",
            "masked": mask(value),
            "line": str(text.count("\n", 0, match.start()) + 1),
        })
    return list(findings.values())


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=False
    )


def _is_text_file(path: Path) -> bool:
    return path.suffix.lower() in TEXT_SUFFIXES or path.name.startswith(".env")


def candidate_files(root: Path = PROJECT_ROOT) -> Iterator[Path]:
    """Tracked + untracked-not-ignored files (exactly what a commit would add)."""
    if (root / ".git").exists():
        seen: set[str] = set()
        for args in (("ls-files",), ("ls-files", "--others", "--exclude-standard")):
            result = _git(root, *args)
            if result.returncode == 0:
                for rel in result.stdout.splitlines():
                    rel = rel.strip()
                    if rel and rel not in seen:
                        seen.add(rel)
                        yield root / rel
        return
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if any(part in SKIP_DIRS or rel.startswith(part + "/") for part in SKIP_DIRS):
            continue
        yield path


def check_gitignore(root: Path = PROJECT_ROOT) -> list[str]:
    """Return problems with the local secret-file rules."""
    problems: list[str] = []
    if not (root / ".git").exists():
        return problems
    if _git(root, "check-ignore", "-q", ".env").returncode != 0:
        problems.append(".env is NOT ignored by git - add it to .gitignore")
    if _git(root, "check-ignore", "-q", ".env.example").returncode == 0:
        problems.append(".env.example must stay tracked (remove the ignore rule)")
    return problems


def scan_repository(root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Scan candidate files; return gitignore problems + per-file findings."""
    problems = check_gitignore(root)
    findings: list[dict[str, str]] = []
    for path in candidate_files(root):
        rel = path.relative_to(root).as_posix()
        if not path.is_file() or not _is_text_file(path):
            continue
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for hit in scan_text(text):
            findings.append({"file": rel, **hit})
    return {"gitignore_problems": problems, "findings": findings}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check that no OpenRouter key can be committed (offline)."
    )
    parser.add_argument("--root", default=str(PROJECT_ROOT))
    args = parser.parse_args(argv)

    report = scan_repository(Path(args.root).resolve())
    for problem in report["gitignore_problems"]:
        print(f"[secret-guard] RULE ERROR: {problem}", file=sys.stderr)
    for hit in report["findings"]:
        print(
            f"[secret-guard] POSSIBLE KEY: {hit['file']}:{hit['line']} "
            f"({hit['kind']}) {hit['masked']}",
            file=sys.stderr,
        )

    if report["gitignore_problems"] or report["findings"]:
        print(
            "[secret-guard] FAILED. Put the real key ONLY in the local .env "
            "(git-ignored), revoke any leaked key at https://openrouter.ai/keys, "
            "then rerun this check.",
            file=sys.stderr,
        )
        return 1
    print("[secret-guard] OK: .env is ignored and no credential-like value is committable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
