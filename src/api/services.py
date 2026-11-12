"""Material ingestion + LLM parsing + RAG Q&A for the student API.

Reuses the project's existing building blocks:
  * LLMClient          -> chat (OpenRouter, OpenAI-compatible)
  * ChromaVectorStore  -> RAG over uploaded materials
  * HashingEmbedder / SentenceTransformerEmbedder -> text vectors
  * ingestion.chunker  -> chunk text with citation metadata
  * ingestion.pdf_parser -> PDF -> text (plus TXT/MD support added here)

Flow for a student upload:
  1. save_upload(): write the file under data/uploads/, extract plain text
     (PDF via PyMuPDF; TXT/MD read as text), and index chunks into ChromaDB.
  2. parse_material(): call the LLM to extract knowledge points + generate
     per-point explain pages and quizzes from the material text.
  3. ask(): retrieve relevant chunks from ChromaDB, then answer with the LLM
     grounded on those excerpts, attaching verified citations.

Missing API key raises MissingKey so endpoints can return a clear 503.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence

from ..ingestion.chunker import Chunk, chunk_pages
from ..ingestion.pdf_parser import PageText, format_source, lecture_name, parse_pdf
from ..retrieval.embeddings import get_embedder
from ..retrieval.vector_store import ChromaVectorStore, RetrievedChunk
from ..utils.config import resolve_path
from ..utils.llm_client import LLMClient, MissingAPIKeyError

_UPLOAD_KINDS = (".pdf", ".txt", ".md", ".markdown")

EXTRACT_SYSTEM = """You are an expert study-material analyzer. Given the full text of a
student-uploaded course material, extract its key knowledge points and build a
structured study plan.

CRITICAL OUTPUT RULES:
- Output ONLY one JSON object as your ENTIRE response. Start directly with "{"
  and end with "}".
- Do NOT write any reasoning, explanation, preamble, "thinking out loud", or
  commentary before or after the JSON. Do NOT wrap it in markdown fences.
- If the text has no extractable knowledge points, still return valid JSON with
  an empty "topics" array and a one-sentence "summary".

Required schema:
{
  "summary": "a 2-3 sentence overview of the material",
  "topics": [
    {
      "name": "knowledge point name",
      "desc": "one-sentence description",
      "explain": [ {"t": "page title", "items": ["bullet 1", "bullet 2"], "tip": "takeaway"} ],
      "quiz": [ {"q": "question stem", "opts": ["A", "B", "C", "D"], "a": 0, "why": "explanation"} ]
    }
  ]
}

Rules:
- Extract 3-6 topics; each topic needs 1-2 explain pages and 1-2 quizzes.
- Each quiz has 2-4 options; "a" is the 0-based index of the correct option.
- KEEP IT CONCISE: short bullet items (<= 12 words each), short quiz options,
  and a one-line "why". Do NOT write long paragraphs - the whole JSON must stay
  well under the token limit or it will be truncated.
- Base EVERYTHING strictly on the material text; do not invent facts.
- Use the same language as the material.
- Output valid JSON: double quotes only, no trailing commas, no comments.
- MOST IMPORTANT: always close every brace and bracket so the JSON is complete.
  It is better to return fewer topics than to return truncated JSON."""

EXTRACT_USER = """Material name: {name}

Material text:
{text}
"""

QA_SYSTEM = """You are a study assistant for a student's own uploaded course materials.
Rules:
- Answer ONLY from the excerpts provided. If the excerpts do not contain the
  answer, say clearly that you could not find it in the materials and suggest
  what to upload, rather than inventing an answer.
- Do NOT write any citation, page number, or "Source:" line; the platform
  appends the verified source after your answer.
- Answer concisely in the same language as the question.
- Plain text only: no markdown fences."""

QA_USER = """Question: {question}

Relevant excerpts from the student's materials (verbatim, with source labels):
{context}
"""


def key_status(config: dict[str, Any]) -> dict[str, Any]:
    client = LLMClient(config)
    return {
        "configured": client.is_configured,
        "model": client.model,
    }


def _extract_json(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```\s*$", "", text)

    # 有些模型（尤其免费小模型）会在真正 JSON 前后输出"思考过程"文字。
    # 策略：尝试从"第一个 {" 到"最后一个 }"整段解析；若失败，再逐个
    # 尝试每个平衡的 {...} 子串，取第一个能解析成 dict 的。
    candidates: list[str] = []
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        candidates.append(text[start : end + 1])

    # 逐段尝试：从左到右扫描所有 '{'，用括号配对找出每个完整 JSON 对象
    depth = 0
    obj_start = -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                obj_start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and obj_start >= 0:
                    candidates.append(text[obj_start : i + 1])
                    obj_start = -1

    if not candidates:
        raise ValueError("LLM 未返回 JSON 对象")

    for cand in candidates:
        try:
            payload = json.loads(cand)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload

    # 兜底：模型可能因 token 限制把 JSON 截断了（末尾的 quiz/explain 项不完整）。
    # 尝试逐步截短尾部、补齐括号，抢救出尽可能完整且合法的 JSON 子集。
    salvaged = _salvage_truncated_json(text)
    if salvaged is not None:
        return salvaged

    # 全部候选都无法解析为 dict：报最后一个候选的错误，便于诊断
    raise ValueError("LLM 返回非法 JSON: 未能解析出有效的 JSON 对象（模型可能输出了思考过程而非结果）")


def _salvage_truncated_json(text: str) -> dict[str, Any] | None:
    """Attempt to recover a valid object from a truncated JSON string.

    The model may hit its output-token cap mid-stream, leaving the JSON
    unclosed (or closed with an incomplete trailing item). We walk backward
    from the end, dropping the incomplete tail, then close every open
    brace/bracket/quote and try to parse. Returns a dict on success, else None.
    """
    start = text.find("{")
    if start < 0:
        return None
    body = text[start:]

    # 从末尾往前，逐字符截短，尝试补全后解析。截短步长先用 1（最细粒度），
    # 但为性能对超长文本用更大步长。
    step = 1 if len(body) <= 20000 else 4
    for cut in range(len(body), start - 1, -step):
        chunk = body[:cut]
        repaired = _close_open_structures(chunk)
        if repaired is None:
            continue
        try:
            obj = json.loads(repaired)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("topics"), list):
            return obj
    return None


def _close_open_structures(s: str) -> str | None:
    """Close unclosed brackets and a trailing open string to make JSON parseable.

    Returns the repaired string, or None if the string has no JSON object start.
    """
    out = s.strip()
    if not out.startswith("{"):
        return None

    # 去掉末尾未闭合的字符串：从末尾找未配对的引号
    # 先去掉末尾残缺的字符串内容（最后一个未闭合引号之后的部分）
    in_str = False
    last_str_quote = -1
    for i, ch in enumerate(out):
        if ch == '"' and (i == 0 or out[i - 1] != "\\"):
            in_str = not in_str
            if not in_str:
                last_str_quote = i
    if in_str:
        # 字符串未闭合：截断到最后一个已闭合引号位置（若存在）
        if last_str_quote >= 0:
            out = out[: last_str_quote + 1]
        else:
            return None

    # 去掉末尾可能的逗号（截断常停在字段后）
    out = out.rstrip()
    # 补全括号：逐字符扫描，遇到未闭合的 '}' 和 ']' 依次补上
    stack: list[str] = []
    i = 0
    n = len(out)
    while i < n:
        ch = out[i]
        if ch == '"':
            # 跳过字符串
            i += 1
            while i < n:
                if out[i] == "\\":
                    i += 2
                    continue
                if out[i] == '"':
                    i += 1
                    break
                i += 1
            continue
        if ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if stack:
                stack.pop()
        i += 1

    # 补全：栈里剩余的开括号，按后进先出补对应闭括号
    closing = ""
    for open_ch in reversed(stack):
        closing += "}" if open_ch == "{" else "]"
    return out + closing


def _read_pdf_text(path: Path) -> tuple[str, int]:
    pages = parse_pdf(path)
    text = "\n\n".join(p.text for p in pages)
    return text, len(pages)


def _read_plain_text(path: Path) -> tuple[str, int]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    # 粗略按段落数估算"页"，仅用于展示
    pages = max(1, len([l for l in raw.splitlines() if l.strip()]) // 40)
    return raw, pages


class MaterialService:
    """Stateful service: holds an in-memory material index + ChromaDB store."""

    class MissingKey(RuntimeError):
        """LLM key absent/placeholder; endpoints map this to HTTP 503."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.upload_dir = resolve_path(
            config.get("paths", {}).get("uploads_dir", "./data/uploads")
        )
        self.upload_dir.mkdir(parents=True, exist_ok=True)

        self.embedder = get_embedder(config=config)
        persist = resolve_path(config["paths"]["chroma_persist_dir"])
        collection = config.get("retrieval", {}).get(
            "upload_collection", "student_uploads"
        )
        self.store = ChromaVectorStore(persist, collection_name=collection)
        self.retrieval_cfg = config.get("retrieval", {})

        self._materials: dict[str, dict[str, Any]] = {}
        self._topics: dict[str, list[dict[str, Any]]] = {}
        self._llm: LLMClient | None = None

    @property
    def llm(self) -> LLMClient:
        if self._llm is None:
            self._llm = LLMClient(self.config)
        return self._llm

    def _require_key(self) -> None:
        if not self.llm.is_configured:
            raise self.MissingKey(
                "未配置 LLM API Key。请在项目根目录 .env 填入 OPENROUTER_API_KEY "
                "后重启后端服务。"
            )

    # ---- 上传 / 索引 ----
    async def save_upload(self, file: Any) -> dict[str, Any]:
        name = getattr(file, "filename", None) or "upload"
        suffix = Path(name).suffix.lower()
        if suffix not in _UPLOAD_KINDS:
            raise ValueError(f"不支持的文件类型 {suffix}，请上传 PDF / TXT / MD")

        data = await file.read()
        if not data:
            raise ValueError("文件为空")
        if len(data) > 20 * 1024 * 1024:
            raise ValueError("文件过大（上限 20MB）")

        mid = "m_" + re.sub(r"[^a-zA-Z0-9]", "_", str(abs(hash(name + str(len(data))))))
        path = self.upload_dir / f"{mid}{suffix}"
        path.write_bytes(data)

        if suffix == ".pdf":
            text, pages = _read_pdf_text(path)
        else:
            text, pages = _read_plain_text(path)

        lecture = lecture_name(name)
        page_texts = [
            PageText(
                lecture=lecture, page=1, topic=lecture, text=text,
                source_path=str(path),
            )
        ]
        chunks = chunk_pages(
            page_texts,
            chunk_size=int(self.retrieval_cfg.get("chunk_size", 800)),
            chunk_overlap=int(self.retrieval_cfg.get("chunk_overlap", 100)),
        )
        chunk_ids: list[str] = []
        if chunks:
            vectors = self.embedder.embed([c.text for c in chunks])
            self.store.upsert_chunks(chunks, vectors)
            chunk_ids = [c.chunk_id for c in chunks]

        rec = {
            "id": mid,
            "name": name,
            "kind": suffix.lstrip(".").upper(),
            "size": len(data),
            "pages": pages,
            "chars": len(text),
            "chunks": len(chunks),
            "chunk_ids": chunk_ids,
            "status": "uploaded",  # 已上传并索引，但尚未经 LLM 解析
        }
        self._materials[mid] = rec
        return rec

    def get_material(self, material_id: str) -> dict[str, Any] | None:
        return self._materials.get(material_id)

    def list_materials(self) -> list[dict[str, Any]]:
        return list(self._materials.values())

    def delete_material(self, material_id: str) -> dict[str, Any]:
        """Remove an uploaded material everywhere it lives.

        Deletes the on-disk file, the ChromaDB chunks (so future RAG Q&A no
        longer cites it), the parsed topics, and the in-memory record. This is
        the single cleanup path that guarantees no stale citations remain.
        """
        rec = self._materials.get(material_id)
        if rec is None:
            raise ValueError("资料不存在或已被移除")

        # 1) remove indexed chunks from ChromaDB
        chunk_ids = rec.get("chunk_ids") or []
        if chunk_ids:
            try:
                self.store.delete_by_ids(chunk_ids)
            except Exception:
                pass

        # 2) remove the on-disk file
        suffix = Path(rec["name"]).suffix.lower()
        path = self.upload_dir / f"{material_id}{suffix}"
        try:
            if path.is_file():
                path.unlink()
        except Exception:
            pass

        # 3) drop parsed topics and the record
        self._topics.pop(material_id, None)
        self._materials.pop(material_id, None)
        return {"deleted": material_id, "chunks_removed": len(chunk_ids)}

    # ---- 解析：LLM 抽取知识点 + 生成讲解与 quiz ----
    def _material_text(self, material_id: str) -> str:
        rec = self.get_material(material_id)
        if rec is None:
            raise ValueError("资料不存在")
        suffix = Path(rec["name"]).suffix.lower()
        path = self.upload_dir / f"{material_id}{suffix}"
        if suffix == ".pdf":
            text, _ = _read_pdf_text(path)
        else:
            text, _ = _read_plain_text(path)
        return text

    def parse_material(self, material_id: str) -> dict[str, Any]:
        self._require_key()
        rec = self.get_material(material_id)
        if rec is None:
            raise ValueError("资料不存在")

        text = self._material_text(material_id)
        # 控制 prompt 长度：超出部分截断
        max_chars = 16000
        excerpt = text[:max_chars]

        messages = [
            {"role": "system", "content": EXTRACT_SYSTEM},
            {"role": "user", "content": EXTRACT_USER.format(
                name=rec["name"], text=excerpt,
            )},
        ]
        # 知识点 + 讲解 + quiz 的完整 JSON 较长（英文资料尤其），
        # 4000 token 会被截断导致 JSON 不完整；提高到 8000。
        response = self.llm.chat(messages, max_tokens=8000)

        # 仅在「空响应/限流」这类瞬时错误时重试一次；JSON 截断/格式错误
        # 重试通常无益（模型行为一致），直接报错，避免浪费调用额度。
        payload = None
        last_err = ""
        for _ in range(2):
            if not (response.text or "").strip():
                last_err = "LLM 返回空响应（可能被限流，请稍后重试）"
                response = self.llm.chat(messages, max_tokens=8000)
                continue
            try:
                payload = _extract_json(response.text)
                break
            except ValueError as exc:
                last_err = str(exc)
                break  # 不再重试：非瞬时错误，重试无益
        if payload is None:
            raise ValueError(f"知识点解析失败：{last_err}")

        topics = payload.get("topics") or []
        if not isinstance(topics, list) or not topics:
            raise ValueError("LLM 未返回有效知识点")

        normalized: list[dict[str, Any]] = []
        for i, t in enumerate(topics[:8]):
            if not isinstance(t, dict):
                continue
            quiz = self._normalize_quiz(t.get("quiz") or [])
            explain = self._normalize_explain(t.get("explain") or [])
            normalized.append({
                "name": str(t.get("name") or f"知识点 {i + 1}"),
                "desc": str(t.get("desc") or ""),
                "icon": self._icon_for(i),
                "explain": explain,
                "quiz": quiz,
                "src": rec["name"],
            })

        self._topics[material_id] = normalized
        rec["status"] = "parsed"
        rec["topic_count"] = len(normalized)

        return {
            "material_id": material_id,
            "source": rec["name"],
            "summary": payload.get("summary", ""),
            "pages": rec.get("pages"),
            "topics": normalized,
        }

    @staticmethod
    def _icon_for(i: int) -> str:
        icons = ["📘", "📗", "📙", "📕", "📌", "🧩", "💡", "🎯"]
        return icons[i % len(icons)]

    @staticmethod
    def _normalize_quiz(list_: list[Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for q in list_ or []:
            if not isinstance(q, dict):
                continue
            opts = q.get("opts") or q.get("options") or q.get("choices") or []
            a = q.get("a")
            if isinstance(a, str):
                a = "ABCD".index(a.upper()) if a.upper() in "ABCD" else -1
            a = a if isinstance(a, int) and 0 <= a < len(opts) else -1
            if a < 0 or not opts:
                continue
            out.append({
                "q": str(q.get("q") or q.get("question") or ""),
                "opts": [str(o) for o in opts],
                "a": a,
                "why": str(q.get("why") or q.get("explanation") or ""),
            })
        return out

    @staticmethod
    def _normalize_explain(list_: list[Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for s in list_ or []:
            if isinstance(s, str):
                out.append({"t": s, "items": [], "tip": ""})
            elif isinstance(s, dict):
                out.append({
                    "t": str(s.get("t") or s.get("title") or ""),
                    "items": [str(x) for x in (s.get("items") or [])],
                    "tip": str(s.get("tip") or s.get("summary") or ""),
                })
        return out

    # ---- 独立 quiz 生成 ----
    def generate_quiz(
        self,
        material_id: str | None,
        topic: str | None,
        count: int,
    ) -> dict[str, Any]:
        self._require_key()
        if material_id:
            rec = self.get_material(material_id)
            if rec is None:
                raise ValueError("资料不存在")
            text = self._material_text(material_id)
        else:
            # 取最近上传的一份资料
            if not self._materials:
                raise ValueError("尚未上传任何资料")
            material_id = list(self._materials.keys())[-1]
            text = self._material_text(material_id)

        # 简化：直接走 parse 的抽取逻辑，但只返回 quiz
        payload = self.parse_material(material_id)
        all_quiz: list[dict[str, Any]] = []
        for t in payload.get("topics", []):
            for q in t.get("quiz", []):
                q = dict(q)
                q["topic"] = t.get("name")
                all_quiz.append(q)
        return {"material_id": material_id, "count": len(all_quiz), "quiz": all_quiz[:count]}

    # ---- RAG 问答 ----
    def ask(self, question: str) -> dict[str, Any]:
        self._require_key()
        hits = self._retrieve(question)
        if not hits:
            return {
                "answer": "我在你上传的资料中没有找到直接依据。请换用资料中的原词再问，或上传包含该内容的资料后重试。我不会凭空编造答案。",
                "sources": [],
                "grounded": False,
            }

        context = self._format_context(hits)
        messages = [
            {"role": "system", "content": QA_SYSTEM},
            {"role": "user", "content": QA_USER.format(question=question, context=context)},
        ]
        response = self.llm.chat(messages, max_tokens=800)
        sources = list(dict.fromkeys(h.source for h in hits if h.source))
        return {
            "answer": response.text.strip(),
            "sources": sources,
            "grounded": True,
            "model": response.model,
        }

    def _retrieve(self, question: str) -> list[RetrievedChunk]:
        if self.store.count() == 0:
            return []
        vector = self.embedder.embed([question])[0]
        return self.store.search_by_vector(
            vector, top_k=int(self.retrieval_cfg.get("top_k", 3))
        )

    @staticmethod
    def _format_context(hits: Sequence[RetrievedChunk], limit: int = 700) -> str:
        blocks = []
        for i, h in enumerate(hits, 1):
            blocks.append(f"[{i}] {h.source}\n{h.text[:limit]}")
        return "\n\n".join(blocks)

    # ---- progress ----
    def list_topics(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for mid, topics in self._topics.items():
            for t in topics:
                result.append({**t, "material_id": mid})
        return result

    def overall_mastery(self) -> float:
        # 后端不保存学生作答状态（由前端本地维护）；这里返回 0 占位。
        return 0.0

    def close(self) -> None:
        try:
            self.store.close()
        except Exception:
            pass
