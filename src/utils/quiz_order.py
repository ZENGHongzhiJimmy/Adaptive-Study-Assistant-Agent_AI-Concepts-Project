"""Re-balance which slot holds the correct option in a generated MCQ.

Why this exists
---------------
LLMs are strongly biased towards writing the right answer first, so a generated
quiz sheet reads "A, A, B, A" and a student can score well by always picking A.
We therefore re-order the options *after* generation and move the correct one to
a slot that is spread evenly across A/B/C/D.

Determinism
-----------
Every shuffle is seeded from the question itself (topic + index + stem), so:

* the same question always renders with the same option order - a student who
  retries sees the same sheet, and P2 event logs stay comparable;
* different questions land on different letters, which is what removes the bias.

Nothing here touches scoring: callers must write the new index/letter back, and
``evaluator.py`` keeps scoring exactly as before.
"""

from __future__ import annotations

import hashlib
import random
from typing import Sequence

LETTERS = ("A", "B", "C", "D")


def seed_for(*parts: object) -> int:
    """Stable integer seed derived from the parts that identify a question."""
    raw = "\x00".join(str(part) for part in parts)
    return int.from_bytes(hashlib.sha1(raw.encode("utf-8")).digest()[:8], "big")


def target_slots(
    count: int,
    width: int = len(LETTERS),
    rng: random.Random | None = None,
) -> list[int]:
    """``count`` target slots where every block of ``width`` uses each slot once.

    Shuffling the bag (instead of drawing independently) guarantees an even
    spread instead of a merely *probable* one: 8 questions always get two A's,
    two B's, two C's and two D's - just not in a predictable order.
    """
    picker = rng or random.Random()
    slots: list[int] = []
    bag: list[int] = []
    while len(slots) < max(0, count):
        if not bag:
            bag = list(range(max(2, width)))
            picker.shuffle(bag)
        slots.append(bag.pop())
    return slots


def move_correct_to(
    options: Sequence[str],
    correct: int,
    target: int,
    rng: random.Random | None = None,
) -> tuple[list[str], int]:
    """Return ``(options, correct)`` with the correct option sitting at ``target``.

    Distractors keep a shuffled relative order, and ``target`` is clamped to the
    real option count so 2- or 3-option questions stay valid.
    """
    items = list(options)
    if len(items) < 2 or not 0 <= correct < len(items):
        return items, correct

    target = min(max(0, target), len(items) - 1)
    if target == correct:
        return items, correct

    picker = rng or random.Random()
    distractors = [value for index, value in enumerate(items) if index != correct]
    picker.shuffle(distractors)
    reordered = list(distractors)
    reordered.insert(target, items[correct])
    return reordered, target


def reorder_quiz(
    items: list[dict[str, object]],
    *,
    options_key: str = "opts",
    answer_key: str = "a",
    seed_parts: Sequence[object] = (),
) -> list[dict[str, object]]:
    """Rebalance a whole sheet in place and return it.

    Used by the material-parsing pipeline, where quizzes arrive as
    ``{"q": ..., "opts": [...], "a": <0-based index>}``.
    """
    if len(items) < 2:
        return items
    limit = max(
        (len(item.get(options_key) or []) for item in items),  # type: ignore[arg-type]
        default=len(LETTERS),
    )
    rng = random.Random(seed_for("quiz", *seed_parts))
    slots = target_slots(len(items), width=min(len(LETTERS), max(2, limit)), rng=rng)
    for item, slot in zip(items, slots):
        opts = list(item.get(options_key) or [])  # type: ignore[call-overload]
        answer = item.get(answer_key)
        if not isinstance(answer, int) or not opts:
            continue
        item[options_key], item[answer_key] = move_correct_to(opts, answer, slot, rng)
    return items
