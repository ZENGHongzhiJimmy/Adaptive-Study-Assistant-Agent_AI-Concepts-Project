"""Prompt templates for grounded Teach / Review and structured MCQ generation.

Two rules protect the experiment (README 12.2):

1. Citations are NEVER produced by the LLM. The platform attaches
   ``Source: Lecture 05, Page 14`` from P1 retrieval metadata after the call,
   so a hallucinated reference can never enter the trace.
2. The LLM only *generates* objective MCQs. Scoring is still done in code by
   ``evaluator.py``; no LLM rubric, no subjective scoring at P4.
"""

from __future__ import annotations

TEACH_SYSTEM = """You are a concise university-level tutor for an AI concepts course.
Rules:
- Use ONLY the lecture excerpts provided in the user message. Do not invent facts,
  formulas, examples, or references that are not in those excerpts.
- Do NOT write any citation, page number, lecture name, or "Source:" line.
  The platform appends the verified source after your answer.
- Write 2-4 short paragraphs or bullets, in the same language as the topic text.
- End with one concrete takeaway the student should remember.
- Plain text only: no markdown fences, no JSON, no preamble about being an AI."""

TEACH_USER = """Topic: {topic}
Activity: {style}
Student's recent weak points: {weak_points}

Lecture excerpts (verbatim, with verified source labels):
{context}

Explain the concept so this student can answer a multiple-choice question about it."""

REVIEW_STYLE_NOTE = (
    "This is a REVIEW after repeated wrong answers: start by naming the "
    "misconception to correct, then restate the key idea from the excerpts."
)

QUESTION_SYSTEM = """You write ONE objective multiple-choice question for an AI concepts course.
Respond with EXACTLY ONE JSON object and nothing else - no markdown fences, no prose.

Required schema:
{
  "question": "string, the stem",
  "options": {"A": "string", "B": "string", "C": "string", "D": "string"},
  "correct_option": "one of A/B/C/D",
  "explanation": "one short sentence why that option is right"
}

Rules:
- Exactly 4 non-empty, mutually distinct options with keys A, B, C, D.
- Exactly one correct option; distractors must be plausible but clearly wrong.
- Use ONLY facts present in the lecture excerpts when excerpts are provided.
- The stem and options must be answerable from the excerpts / topic alone.
- Output valid JSON: double quotes only, no trailing commas, no comments."""

QUESTION_USER = """Topic: {topic}
Difficulty: {difficulty} (challenge = one inference step beyond recall; mcq = direct recall)
Question number: {index}

Lecture excerpts (verbatim, with verified source labels):
{context}

Write one JSON object for this topic and difficulty."""

QUESTION_RETRY = """Your previous reply was not valid for the required schema.
Parse error: {error}
Previous reply (truncated): {previous}
Reply again with EXACTLY ONE JSON object: {{"question": ..., "options": {{"A": ..., "B": ..., "C": ..., "D": ...}}, "correct_option": "A|B|C|D", "explanation": ...}}.
No markdown fences, no prose outside the JSON object."""

NO_EXCERPTS = "(no lecture excerpt retrieved; use only the topic name, do not cite any source)"


def format_context(hits: list[object], limit: int = 600) -> str:
    """Render retrieved chunks as verbatim excerpts with verified source labels."""
    if not hits:
        return NO_EXCERPTS
    blocks: list[str] = []
    for number, hit in enumerate(hits, start=1):
        source = getattr(hit, "source", None) or f"lecture page {getattr(hit, 'page', '?')}"
        text = str(getattr(hit, "text", "")).strip()[:limit]
        blocks.append(f"[{number}] {source}\n{text}")
    return "\n\n".join(blocks)


def format_weak_points(recent_errors: list[object] | None) -> str:
    if not recent_errors:
        return "none recorded"
    ids = [
        str(item.get("question_id")) if isinstance(item, dict) else str(item)
        for item in recent_errors
    ]
    return ", ".join(ids)
