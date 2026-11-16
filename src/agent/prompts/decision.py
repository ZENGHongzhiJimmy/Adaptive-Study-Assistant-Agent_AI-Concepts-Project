"""Prompts for the LLM decision module (the planner that picks the next activity).

Why this file is named ``decision.py`` and not ``planner.py``: ``tests/test_p9``
greps every import on the product path for a module called ``planner`` (the
legacy 0-1 threshold planner used by the CLI).  A ``prompts/planner.py`` would
trip that guard even though it has nothing to do with the baseline, so the
prompt lives under a neutral name.

Three strings:

* ``DECISION_SYSTEM``  - role, hard rules, output schema.
* ``DECISION_MECHANICS`` - the goal + the *facts* of the machinery (what each
  activity is worth, what "learned" requires).  Rendered into ``DECISION_USER``.
* ``DECISION_USER``    - the学情 report; placeholders only, no literal braces.
* ``DECISION_RETRY``   - fed back when the reply was not a usable decision.

The system prompt deliberately does **not** say which activity is better.  The
whole point of this module is that the trade-off ("rescue the weakest topic" vs
"push the overall progress") is the model's judgement, not a rule tree's.

``DECISION_MECHANICS`` is held to the same standard: every line is a number or
a prerequisite taken from ``policy.py``, never a preference.  It may say
"review is the only way to empty the error book"; it may not say "review next".

Why v2 (2026-11-16)
-------------------
The v1 mechanics text said "first read pays most, re-reads pay little" and
"two practices under 50% means the concept is missing, go back to explain".
Measured on the v1 decision bank: the agent re-read explanations 6-7 times per
session and picked review 5 times in 607 decisions (0.8%).  The cause was a
gap in the facts, not a broken model - v1 never said that
(a) mastery >= MASTERED is only *half* the pass condition (the error book must
also be empty), and (b) review is the only way to empty it.  Without those two
lines, re-reading looked like the only way to raise a number that had stopped
moving.  v2 states both and drops the "go back to explain" nudge, which was a
preference smuggled in as advice - and a self-reinforcing one, because a topic
capped at EXPLAIN_CAP returns to practice, fails, and gets sent back again.

``DECISION_VERSION`` / ``prompt_fingerprint()`` exist so a cached decision can
never be replayed against a prompt it was not made from (see
``experiments/decisions.py::state_key``).
"""

from __future__ import annotations

import hashlib

#: Bumped whenever the wording of any prompt below changes.  Recorded in
#: ``experiments/data/decisions.json`` so a result carries the prompt it came
#: from.
DECISION_VERSION = "decision-v2"

DECISION_SYSTEM = """\
你是「自适应学习助手」的决策模块。系统会给你当前学情和一份【合法候选动作清单】，\
你的唯一任务是从中选出下一步最该做的那一个，并说明理由。

硬性规则：
1. 只能选候选清单里已经出现的组合，返回时原样使用它的 topic_id 与 activity。
   不要发明知识点 id，不要发明新的活动类型，不要一次返回多个动作。
2. 只输出一个 JSON 对象，不要代码块围栏，不要在 JSON 前后写任何解释性文字。
3. reason 会原样显示给学生看，必须是**一句中文**，并且要引用具体数字
   （掌握度、错题数、连续低分次数、题数）。不要写「继续努力」「加强练习」这类空话。
4. 不要连续两轮选择完全相同的动作，除非上一轮失败且候选里确实没有更好的选择。

输出格式：
{"topic_id": "<候选里的 topic_id>", "activity": "explain|practice|review", \
"reason": "<一句话，中文，含具体数字>", "confidence": 0.0}

如何权衡由你自己判断：可以先补最容易掉下去的那个知识点，也可以先推进整体进度，\
但要在 reason 里体现你做这个判断的依据。
"""


# Formatted with policy.MASTERED / policy.EXPLAIN_CAP by
# ``planner_llm.build_user_message``.  Facts only - see the module docstring.
DECISION_MECHANICS = """\
学习目标：让学习者真正掌握尽可能多的知识点。
达标判定：掌握度 >= {mastered}% **且**错题本为空。两个条件同时成立才算达标；\
错题本非空的知识点，掌握度再高也仍然算「待学」。

系统机制（固定规则，不是建议；选活动前请先理解）：
- 讲解：第一次完成讲解掌握度 +10；重看一次只 +2；之后不再加分。\
讲解最多把掌握度推到 {explain_cap}%。
- 练习：本轮的题答对则从错题本移除、答错则加入错题本。掌握度按\
「本轮正确率减去当前掌握度」的 0.6 倍调整，所以正确率低于当前掌握度时，练习会让掌握度下降。
- 复习：逐题纠正错题，本身不加掌握度，只清空错题本；没有错题时复习不产生任何变化。
- 清空错题本只有复习这一条途径。
- 连续两次练习正确率低于 50% 时，该知识点会被标记为「需要帮助」。

请判断每个知识点现在缺的是「概念」「验证」还是「纠错」，据此选活动；\
并把有限的学习机会给最需要它的知识点。
"""


# Placeholders only.  Braces coming from JSON always live in DECISION_SYSTEM,
# which is never passed through ``str.format``.
DECISION_USER = """\
{goal}

{progress}

{topics_block}

{candidates_block}

{recent_block}
"""


DECISION_RETRY = """\
上一次的输出无法使用：{error}

上一次的输出（前 300 字）：
{previous}

请重新输出一个合法的 JSON 对象。仍然只能从【合法候选动作清单】里选，\
topic_id 与 activity 必须与清单中的某一行完全一致。
"""


def prompt_fingerprint() -> str:
    """Short stable hash of everything that shapes the decision prompt.

    Used as part of the decision-log cache key, so editing a prompt cannot
    silently reuse decisions that were made from the old text.  Deliberately
    includes only the templates, not the学情 payload: the payload is hashed
    separately by ``state_key``.
    """
    parts = (
        DECISION_VERSION,
        DECISION_SYSTEM,
        DECISION_MECHANICS,
        DECISION_USER,
        DECISION_RETRY,
    )
    return hashlib.sha1("\x00".join(parts).encode()).hexdigest()[:12]


__all__ = [
    "DECISION_MECHANICS",
    "DECISION_RETRY",
    "DECISION_SYSTEM",
    "DECISION_USER",
    "DECISION_VERSION",
    "prompt_fingerprint",
]
