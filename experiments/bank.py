"""Loader + validator for the frozen experiment bank.

The bank is a plain JSON file (``experiments/data/bank.json``) so the
experiment needs no LLM, no network and no database. Everything downstream —
simulator, runner — reads it through this module.

Invariants enforced by :func:`validate`:

* every question id is unique and every answer index points inside ``opts``;
* ``practice`` and ``probe`` are disjoint in both ids *and* stems, so the
  post-test never reuses an item the learner has just practised;
* every item carries a difficulty in ``[0, 1]``;
* each topic has at least one practice item (the policy needs ``quiz_count > 0``
  to schedule practice) and at least one probe item (the pre-test needs a seed).

Run ``python -m experiments.bank --check`` to print every item with its answer
marked, for human verification of the LLM-authored keys.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

DATA_DIR = Path(__file__).resolve().parent / "data"
BANK_PATH = DATA_DIR / "bank.json"

LETTERS = "ABCD"


@dataclass(frozen=True)
class Item:
    item_id: str
    prompt: str
    options: tuple[str, ...]
    answer: int
    why: str = ""
    difficulty: float = 0.5

    @property
    def correct_option(self) -> str:
        return self.options[self.answer]

    def as_quiz(self) -> dict[str, Any]:
        """Shape expected by ``policy.apply_result(..., quiz=...)``."""
        return {
            "q": self.prompt,
            "opts": list(self.options),
            "a": self.answer,
            "why": self.why,
        }


@dataclass(frozen=True)
class Topic:
    topic_id: str
    name: str
    difficulty: float
    explain_pages: int
    practice: tuple[Item, ...] = field(default_factory=tuple)
    probe: tuple[Item, ...] = field(default_factory=tuple)

    @property
    def quiz_count(self) -> int:
        return len(self.practice)


@dataclass(frozen=True)
class Bank:
    topics: tuple[Topic, ...]
    source: dict[str, Any] = field(default_factory=dict)

    def topic(self, topic_id: str) -> Topic:
        for topic in self.topics:
            if topic.topic_id == topic_id:
                return topic
        raise KeyError(topic_id)

    @property
    def item_count(self) -> int:
        return sum(len(t.practice) + len(t.probe) for t in self.topics)


def _item(raw: dict[str, Any], *, where: str) -> Item:
    try:
        item_id = str(raw["id"])
        prompt = str(raw["q"]).strip()
        options = tuple(str(o) for o in raw["opts"])
        answer = int(raw["a"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{where}: malformed item ({exc})") from exc
    if not prompt:
        raise ValueError(f"{where}/{item_id}: empty question stem")
    if len(options) < 2:
        raise ValueError(f"{where}/{item_id}: needs at least two options")
    if not 0 <= answer < len(options):
        raise ValueError(f"{where}/{item_id}: answer index {answer} outside {len(options)} options")
    difficulty = float(raw.get("difficulty", 0.5))
    if not 0.0 <= difficulty <= 1.0:
        raise ValueError(f"{where}/{item_id}: difficulty {difficulty} outside [0, 1]")
    return Item(
        item_id=item_id,
        prompt=prompt,
        options=options,
        answer=answer,
        why=str(raw.get("why") or ""),
        difficulty=difficulty,
    )


def parse_bank(payload: dict[str, Any]) -> Bank:
    raw_topics = payload.get("topics")
    if not isinstance(raw_topics, list) or not raw_topics:
        raise ValueError("bank has no topics")
    topics: list[Topic] = []
    for raw in raw_topics:
        if not isinstance(raw, dict):
            raise ValueError("topic entry is not an object")
        topic_id = str(raw.get("topic_id") or "").strip()
        if not topic_id:
            raise ValueError("topic without topic_id")
        where = topic_id
        practice = tuple(_item(x, where=f"{where}/practice") for x in (raw.get("practice") or []))
        probe = tuple(_item(x, where=f"{where}/probe") for x in (raw.get("probe") or []))
        if not practice:
            raise ValueError(f"{where}: no practice items (policy needs quiz_count > 0)")
        if not probe:
            raise ValueError(f"{where}: no probe items (pre-test needs a seed item)")
        topics.append(
            Topic(
                topic_id=topic_id,
                name=str(raw.get("name") or topic_id),
                difficulty=float(raw.get("difficulty", 0.5)),
                explain_pages=int(raw.get("explain_pages", 1) or 1),
                practice=practice,
                probe=probe,
            )
        )
    return Bank(topics=tuple(topics), source=dict(payload.get("source") or {}))


def validate(bank: Bank) -> None:
    seen_ids: set[str] = set()
    for topic in bank.topics:
        stems = set()
        for item in (*topic.practice, *topic.probe):
            if item.item_id in seen_ids:
                raise ValueError(f"duplicate item id: {item.item_id}")
            seen_ids.add(item.item_id)
            if item.prompt.lower() in stems:
                raise ValueError(f"{topic.topic_id}: duplicate stem {item.prompt!r}")
            stems.add(item.prompt.lower())
    for topic in bank.topics:
        practice_ids = {i.item_id for i in topic.practice}
        probe_ids = {i.item_id for i in topic.probe}
        if practice_ids & probe_ids:
            raise ValueError(f"{topic.topic_id}: practice and probe share ids")
        practice_stems = {i.prompt.lower() for i in topic.practice}
        probe_stems = {i.prompt.lower() for i in topic.probe}
        if practice_stems & probe_stems:
            raise ValueError(f"{topic.topic_id}: practice and probe share a stem")


def load_bank(path: str | Path | None = None) -> Bank:
    target = Path(path) if path else BANK_PATH
    if not target.exists():
        raise FileNotFoundError(
            f"{target} not found - run `python -m experiments.build_bank --pdf ...` first"
        )
    payload = json.loads(target.read_text(encoding="utf-8"))
    bank = parse_bank(payload)
    validate(bank)
    return bank


def describe(bank: Bank) -> str:
    lines = [
        f"topics={len(bank.topics)} items={bank.item_count} "
        f"practice={sum(len(t.practice) for t in bank.topics)} "
        f"probe={sum(len(t.probe) for t in bank.topics)}"
    ]
    for topic in bank.topics:
        lines.append(
            f"  {topic.topic_id:<32} d={topic.difficulty:.2f} "
            f"practice={len(topic.practice)} probe={len(topic.probe)}  {topic.name}"
        )
    return "\n".join(lines)


def check_sheet(bank: Bank) -> str:
    """Human-readable answer key: every item with its correct option marked."""
    out: list[str] = []
    for topic in bank.topics:
        out.append(f"\n## {topic.name}  ({topic.topic_id}, difficulty {topic.difficulty:.2f})")
        for label, items in (("practice", topic.practice), ("probe", topic.probe)):
            out.append(f"  [{label}]")
            for item in items:
                out.append(f"    {item.item_id}  (d={item.difficulty:.2f})")
                out.append(f"      Q: {item.prompt}")
                for index, option in enumerate(item.options):
                    mark = "*" if index == item.answer else " "
                    out.append(f"      {mark}{LETTERS[index]}) {option}")
    return "\n".join(out)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="inspect the frozen experiment bank")
    parser.add_argument("--bank", default=None)
    parser.add_argument("--check", action="store_true", help="print the answer key for review")
    args = parser.parse_args(argv)

    bank = load_bank(args.bank)
    print(describe(bank))
    if args.check:
        print(check_sheet(bank))
    return 0


if __name__ == "__main__":
    sys.exit(main())
