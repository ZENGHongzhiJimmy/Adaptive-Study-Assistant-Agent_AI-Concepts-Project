"""T6 acceptance tests: the correct option is spread across A/B/C/D.

LLMs write the right answer first, so every generated quiz would read
"A, A, A". These tests pin the fix: the sheet is rebalanced, the correct answer
still points at the same *text* after reordering, and 2/3-option questions stay
valid. Scoring itself is untouched.
"""

from __future__ import annotations

import random
from collections import Counter

from src.api.services import MaterialService
from src.utils.quiz_order import move_correct_to, reorder_quiz, seed_for, target_slots


def _always_first_letter(n: int, width: int = 4) -> list[dict[str, object]]:
    return [
        {
            "q": f"Q{i}?",
            "opts": ["right"] + [f"distractor-{i}-{j}" for j in range(1, width)],
            "a": 0,
            "why": "",
        }
        for i in range(n)
    ]


def test_sheet_is_balanced_across_four_letters():
    items = reorder_quiz(_always_first_letter(8), seed_parts=("Topic",))
    counts = Counter(item["a"] for item in items)
    assert counts == {0: 2, 1: 2, 2: 2, 3: 2}


def test_reorder_moves_options_not_the_answer():
    for width in (2, 3, 4):
        for item in reorder_quiz(_always_first_letter(6, width), seed_parts=("T",)):
            opts = item["opts"]
            assert isinstance(opts, list) and 0 <= item["a"] < len(opts)
            assert opts[item["a"]] == "right"
            assert len(set(opts)) == width


def test_reorder_is_deterministic_per_seed_and_varies_across_topics():
    a = reorder_quiz(_always_first_letter(8, 4), seed_parts=("Topic A",))
    b = reorder_quiz(_always_first_letter(8, 4), seed_parts=("Topic A",))
    c = reorder_quiz(_always_first_letter(8, 4), seed_parts=("Topic B",))
    assert [i["a"] for i in a] == [i["a"] for i in b]
    assert [i["opts"] for i in a] == [i["opts"] for i in b]
    assert [i["a"] for i in a] != [i["a"] for i in c]      # 不同知识点顺序不同


def test_single_item_sheet_is_left_alone_but_targets_still_balanced():
    only = _always_first_letter(1)
    reorder_quiz(only, seed_parts=("T",))
    assert only[0]["a"] == 0 and only[0]["opts"][0] == "right"
    assert sorted(target_slots(8, rng=random.Random(seed_for("x")))) == [0, 0, 1, 1, 2, 2, 3, 3]


def test_move_correct_to_clamps_target_for_short_option_lists():
    opts, idx = move_correct_to(["right", "b"], 0, target=3)
    assert idx == 1 and opts[idx] == "right" and len(opts) == 2


def test_parse_material_quizzes_are_rebalanced_and_still_score():
    raw = _always_first_letter(4)
    quiz = MaterialService._normalize_quiz(raw, topic="需求工程")
    assert sorted(q["a"] for q in quiz) == [0, 1, 2, 3]   # 四个字母各用一次
    assert all(q["opts"][q["a"]] == "right" for q in quiz)
    # 前端按下标判分：新下标指向的仍然是原来的正确文本
    assert {q["opts"][q["a"]] for q in quiz} == {"right"}
