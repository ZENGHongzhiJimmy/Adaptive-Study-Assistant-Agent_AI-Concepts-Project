"""Prompts for the LLM decision module (the planner that picks the next activity).

Why this file is named ``decision.py`` and not ``planner.py``: ``tests/test_p9``
greps every import on the product path for a module called ``planner`` (the
legacy 0-1 threshold planner used by the CLI).  A ``prompts/planner.py`` would
trip that guard even though it has nothing to do with the baseline, so the
prompt lives under a neutral name.

Three strings:

* ``DECISION_SYSTEM``  - role, hard rules, output schema.
* ``DECISION_USER``    - the学情 report; placeholders only, no literal braces.
* ``DECISION_RETRY``   - fed back when the reply was not a usable decision.

The system prompt deliberately does **not** say which activity is better.  The
whole point of this module is that the trade-off ("rescue the weakest topic" vs
"push the overall progress") is the model's judgement, not a rule tree's.
"""

from __future__ import annotations

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


__all__ = ["DECISION_SYSTEM", "DECISION_USER", "DECISION_RETRY"]
