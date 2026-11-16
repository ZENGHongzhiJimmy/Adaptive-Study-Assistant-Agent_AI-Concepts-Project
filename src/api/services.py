"""Material ingestion + LLM parsing + RAG Q&A for the student API.

Reuses the project's existing building blocks:
  * LLMClient          -> chat (OpenRouter, OpenAI-compatible)
  * ChromaVectorStore  -> RAG over uploaded materials
  * HashingEmbedder  -> deterministic text vectors
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
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Sequence

from ..ingestion.chunker import chunk_pages
from ..ingestion.pdf_parser import PageText, lecture_name, parse_pdf
from ..retrieval.embeddings import get_embedder
from ..retrieval.vector_store import ChromaVectorStore, RetrievedChunk
from ..utils.config import resolve_path
from ..utils.llm_client import LLMClient
from ..utils.quiz_order import reorder_quiz

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
- Extract ALL important knowledge points in the provided text. Do NOT fix the
  count to a small range: if the text has 10 key points, return 10. The number
  of topics must scale with the amount of content.
- Each topic needs 1-2 explain pages and 1-2 quizzes.
- Each quiz has 2-4 options; "a" is the 0-based index of the correct option.
- VARY the correct option's position: spread correct answers across 0/1/2/3
  instead of always writing it first. Do NOT default to a=0.
- KEEP IT CONCISE: short bullet items (<= 12 words each), short quiz options,
  and a one-line "why". Do NOT write long paragraphs.
- Base EVERYTHING strictly on the material text; do not invent facts.
- Use the same language as the material.
- Output valid JSON: double quotes only, no trailing commas, no comments.
- MOST IMPORTANT: always close every brace and bracket so the JSON is complete.
  If you are about to run out of room, close the current topic cleanly instead
  of leaving it truncated — a complete JSON is more important than one extra topic."""

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

# 「知识点讲解」页里的随堂提问：学生正在看某一页讲解，问的通常是这一页
# 没讲透的地方。除了检索片段，还会把当前讲解页内容一起交给模型，避免
# 明明页面上就有的内容被答成「资料里没找到」。
TUTOR_SYSTEM = """You are an AI teaching assistant. A student is studying ONE knowledge
point of their own uploaded course material and has paused to ask a question
about the slide they are reading.

Rules:
- Answer the question directly first (2-5 sentences), then add at most three
  short supporting points, each starting with "- ", only if they help.
- Ground every claim in the material given below: the current lecture slide and
  the retrieved excerpts both come from the student's uploaded material. If
  neither covers the question, say briefly what is missing instead of inventing
  facts.
- Teaching tone: define the term, then give one concrete example or contrast.
- Do NOT write any citation, page number, or "Source:" line; the platform
  appends the verified sources after your answer.
- Answer in the same language as the student's question.
- Plain text only: no markdown fences, no headings."""

TUTOR_USER = """Knowledge point: {topic}
Current lecture slide the student is reading:
{lecture}

Student's question: {question}

Related excerpts retrieved from the material:
{context}
"""


# 回答的 token 预算：推理型免费模型会把思考 token 也算在内，给 800 容易
# 在正文中途被截断，因此放宽到 1500（答案本身仍受 prompt 的篇幅约束）。
_ANSWER_MAX_TOKENS = 1500

_SCRATCHPAD_MARKERS = (
    "the student is asking",
    "the user is asking",
    "we need to answer",
    "i need to answer",
    "let me look at",
    "let me draft",
    "let me write",
    "draft answer:",
    "the retrieved excerpts",
)


def _clean_answer(text: str) -> str:
    """Strip transport/markdown artefacts a free model sometimes prefixes."""
    out = (text or "").strip()
    out = re.sub(r"^[/\\]n\s*", "", out)          # 个别模型返回 "/n" 前缀
    out = re.sub(r"^```[a-zA-Z]*\s*", "", out)     # 误加的代码围栏
    out = re.sub(r"\s*```$", "", out)
    return out.strip()


def _looks_like_scratchpad(text: str) -> bool:
    """True when a model leaked its chain-of-thought into the answer body.

    Reasoning models normally return the trace in ``reasoning``; when the
    output is truncated some providers append it to ``content`` instead. A
    student must never see that, so the caller retries once.
    """
    head = (text or "")[:400].lower()
    return any(marker in head for marker in _SCRATCHPAD_MARKERS)


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

    class MaterialRemoved(RuntimeError):
        """资料在解析过程中被删除；解析结果必须整体丢弃，不再写回任何一层。

        FastAPI 的同步端点在线程池里跑，前端删资料时 LLM 调用不会中断。
        没有这个信号，解析完成后会把知识点、解析缓存、状态重新写回一个已经
        删除的资料，表现为「资料已移除，界面却又提示解析出 N 个知识点」，
        并且内存里留下无主数据。端点把它映射成 HTTP 410。
        """

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
        # Cache of completed parse results. A page refresh aborts the browser
        # request but not the server-side parse thread, so re-requesting the
        # same material can return the cached result instead of a second LLM
        # call. Front-end recovery reads this via material_status().
        self._parse_results: dict[str, dict[str, Any]] = {}
        # Tombstones: materials deleted while their parse was still running.
        # A late parse result must be dropped instead of resurrecting the data.
        self._removed: set[str] = set()
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

        # 每次上传都是一个新资料：id 不能由「文件名 + 大小」推出，否则重名文件
        # 会落回同一个 id，命中上一次的解析缓存而看着像「上传成功但不解析」。
        # 现在重名/重复上传都会成为独立模块，各自走一次真实的解析。
        mid = "m_" + uuid.uuid4().hex[:12]
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
            # Tag every chunk with its material id so deletion can find them
            # directly in ChromaDB, even after the in-memory table is gone.
            self.store.upsert_chunks(chunks, vectors, extra_metadata={"material_id": mid})
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

    def reset(self) -> dict[str, Any]:
        """彻底清空全部相关数据，回到初始状态，供平台重新开始使用。

        清空范围（"重置数据" = 全部清空、从头开始）：
          1) 学生上传资料对应的 ChromaDB 集合（student_uploads）；
          2) data/uploads/ 目录下所有上传文件；
          3) data/course_materials/ 目录下所有课程材料文件；
          4) 整个 ChromaDB 持久化目录（含 course_chunks 课程索引），
             确保向量库物理数据也一并删除；
          5) 内存中的资料与知识点记录。

        实现要点：Windows 上 ChromaDB 会缓存 SQLite 句柄，直接删目录会
        失败或留下脏状态，因此必须先 close() 释放句柄，再删除目录并重建
        一个全新的空 store（重建后 student_uploads 集合仍可正常写入）。
        """
        # 1) 清空学生上传集合（尽力而为，后续会整体删除目录）
        chunk_removed = 0
        try:
            chunk_removed = self.store.clear()
        except Exception:
            pass

        # 2) 删除 uploads 目录下所有上传文件（跳过 .gitkeep 占位文件）
        files_removed = 0
        try:
            for path in self.upload_dir.iterdir():
                try:
                    if path.is_file() and path.name != ".gitkeep":
                        path.unlink()
                        files_removed += 1
                except Exception:
                    pass
        except Exception:
            pass

        # 3) 删除 course_materials 目录下所有课程材料文件（跳过 .gitkeep）
        course_files_removed = 0
        course_dir = resolve_path(
            self.config.get("paths", {}).get("course_materials", "./data/course_materials")
        )
        try:
            if course_dir.exists():
                for path in course_dir.iterdir():
                    try:
                        if path.is_file() and path.name != ".gitkeep":
                            path.unlink()
                            course_files_removed += 1
                    except Exception:
                        pass
        except Exception:
            pass

        # 4) 关闭句柄 -> 删除整个 chroma_db 持久化目录 -> 重建空 store
        db_removed = False
        persist_dir = resolve_path(self.config["paths"]["chroma_persist_dir"])
        try:
            self.store.close()
        except Exception:
            pass
        # 重试几次，应对 Windows 句柄释放的短暂延迟
        for _ in range(5):
            try:
                if persist_dir.exists():
                    shutil.rmtree(persist_dir)
                db_removed = True
                break
            except OSError:
                time.sleep(0.1)
        # 重建全新的空 store（student_uploads 集合按需重建），并恢复 .gitkeep 占位
        collection = self.config.get("retrieval", {}).get(
            "upload_collection", "student_uploads"
        )
        self.store = ChromaVectorStore(persist_dir, collection_name=collection)
        try:
            (persist_dir / ".gitkeep").touch(exist_ok=True)
        except Exception:
            pass

        # 5) 清空内存状态
        material_ids = list(self._materials.keys())
        self._topics.clear()
        self._parse_results.clear()
        self._materials.clear()
        # 全部数据清空后，仍需保留墓碑：此刻在跑的解析线程同样不能把结果写回。
        self._removed.update(material_ids)
        return {
            "reset": True,
            "materials_removed": len(material_ids),
            "chunks_removed": chunk_removed,
            "files_removed": files_removed,
            "course_files_removed": course_files_removed,
            "chroma_db_removed": db_removed,
        }

    def delete_material(
        self, material_id: str, material_name: str | None = None
    ) -> dict[str, Any]:
        """Remove an uploaded material everywhere it lives (idempotent).

        ``_materials`` is only an in-memory cache: restarting the backend wipes
        it while the uploaded file and the ChromaDB chunks survive. Cleanup
        therefore must NOT depend on that cache -- previously a missing record
        raised 404, the front-end swallowed the error, and the user saw the
        material disappear while the file (and its citations) stayed behind.

        Layers, most precise first:
          1. chunks tagged with ``material_id`` (written at upload time),
          2. chunk ids tracked in memory (legacy behaviour),
          3. chunks whose ``topic`` matches the original filename (legacy
             index built before the material_id tag existed).
        The on-disk file is removed by id prefix, so no record/suffix is needed.
        """
        rec = self._materials.get(material_id)
        name = material_name or (rec.get("name") if rec else None)

        # 0) 打上删除标记：若此时还有解析线程在跑，它完成后会看到这个标记并
        #    丢弃结果，不会把知识点/缓存写回一个已删除的资料。
        self._removed.add(material_id)

        # 1) ChromaDB chunks
        removed = self.store.delete_where({"material_id": material_id})
        if removed == 0 and rec and rec.get("chunk_ids"):
            try:
                removed = self.store.delete_by_ids(rec["chunk_ids"])
            except Exception:
                removed = 0
        if removed == 0 and name:
            removed = self.store.delete_where({"topic": lecture_name(name)})

        # 2) on-disk file(s): glob by id instead of relying on rec["name"]
        files_removed = 0
        for path in self.upload_dir.glob(f"{material_id}.*"):
            if path.is_file():
                try:
                    path.unlink()
                    files_removed += 1
                except OSError:
                    pass

        # 3) in-memory state, parsed topics and cached parse result
        self._topics.pop(material_id, None)
        self._parse_results.pop(material_id, None)
        self._materials.pop(material_id, None)
        return {
            "deleted": material_id,
            "chunks_removed": removed,
            "files_removed": files_removed,
        }

    def material_status(self, material_id: str) -> dict[str, Any] | None:
        """Parse state used by the front-end to recover after a page refresh.

        ``parsed`` reflects the server's own truth (not the browser's), and
        ``result`` carries the cached parse output so the UI can finish without
        re-running the LLM. Returns ``None`` when the record is gone.
        """
        rec = self._materials.get(material_id)
        if rec is None:
            return None
        return {
            "id": material_id,
            "name": rec["name"],
            "status": rec.get("status", "uploaded"),
            "parsed": rec.get("status") == "parsed",
            "result": self._parse_results.get(material_id),
        }

    # ---- 解析：LLM 抽取知识点 + 生成讲解与 quiz ----
    def _assert_alive(self, material_id: str) -> None:
        """解析流程的取消检查点：资料已删除就抛 MaterialRemoved。

        检查两件事：删除墓碑（DELETE 已经进来过）和内存记录是否还在。
        任意一条成立都意味着这次解析结果属于「不存在」的资料，必须整体丢弃。
        """
        if material_id in self._removed or material_id not in self._materials:
            raise self.MaterialRemoved("资料已被移除，解析已取消")

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
        rec = self.get_material(material_id)
        if rec is None:
            raise ValueError("资料不存在")
        self._assert_alive(material_id)
        # Refreshing the page aborts the browser request, but FastAPI's sync
        # endpoint keeps running in its thread pool. Serve the cached result
        # on retry so recovery is instant and does not pay for another LLM call.
        cached = self._parse_results.get(material_id)
        if cached is not None:
            return cached
        self._require_key()

        text = self._material_text(material_id)

        # 完整优先、不受限：使用付费模型，将整份资料一次性交给 LLM 提取
        # 全部知识点，不做分片、不截断、不限制知识点数量。max_tokens 给足，
        # 保证长资料的完整 JSON 也能一次生成。
        messages = [
            {"role": "system", "content": EXTRACT_SYSTEM},
            {"role": "user", "content": EXTRACT_USER.format(
                name=rec["name"], text=text,
            )},
        ]
        response = self.llm.chat(messages, max_tokens=16000)

        # 仅在「空响应/限流」这类瞬时错误时重试一次；JSON 截断/格式错误
        # 重试通常无益（模型行为一致），直接报错，避免浪费调用额度。
        payload = None
        last_err = ""
        for _ in range(2):
            if not (response.text or "").strip():
                last_err = "LLM 返回空响应（可能被限流，请稍后重试）"
                response = self.llm.chat(messages, max_tokens=16000)
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

        # LLM 调用期间资料可能已被前端删除（DELETE 无法中断这次调用）。
        # 在写回任何数据之前必须再确认一次：资料还在，才允许落库。
        self._assert_alive(material_id)

        # 合并去重：按知识点名称归一化去重，保留首次出现顺序
        seen: set[str] = set()
        normalized: list[dict[str, Any]] = []
        for t in topics:
            if not isinstance(t, dict):
                continue
            name = str(t.get("name") or "").strip()
            key = name.lower()
            if not name or key in seen:
                continue
            seen.add(key)
            quiz = self._normalize_quiz(t.get("quiz") or [], topic=name)
            explain = self._normalize_explain(t.get("explain") or [])
            normalized.append({
                "name": name,
                "desc": str(t.get("desc") or ""),
                "icon": self._icon_for(len(normalized)),
                "explain": explain,
                "quiz": quiz,
                "src": rec["name"],
            })

        self._topics[material_id] = normalized
        rec["status"] = "parsed"
        rec["topic_count"] = len(normalized)

        result = {
            "material_id": material_id,
            "source": rec["name"],
            "summary": payload.get("summary", ""),
            "pages": rec.get("pages"),
            "topics": normalized,
        }
        self._parse_results[material_id] = result
        return result

    @staticmethod
    def _icon_for(i: int) -> str:
        """知识点图标统一为单一语义名，由前端以单色线性 SVG 渲染。

        历史版本按序号轮换彩色 emoji，列表里会出现书本/图钉/灯泡等多种
        高饱和形状，视觉噪声较大。这里保留字段与接口形状不变，只固定取值。
        """
        return "bookOpen"

    @staticmethod
    def _normalize_quiz(list_: list[Any], topic: str = "") -> list[dict[str, Any]]:
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
        # 模型几乎总把正确项写在第一格（答案清一色 A）。这里在解析后重排选项，
        # 让正确答案均匀落在 A/B/C/D，索引 "a" 同步改写；判分逻辑不变。
        return reorder_quiz(out, seed_parts=(topic,))

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

    # ---- RAG 问答 ----
    def ask(
        self,
        question: str,
        material_id: str | None = None,
        material_name: str | None = None,
        topic: str | None = None,
        focus: str | None = None,
    ) -> dict[str, Any]:
        """RAG + LLM answer.

        ``topic`` / ``focus`` are optional and only used by the in-lecture
        question box: ``topic`` is the knowledge point being studied and
        ``focus`` is the text of the slide on screen. Passing them makes the
        model answer in a tutoring tone grounded on that slide, which keeps a
        follow-up question from being answered with "not found in materials"
        when the slide itself already says it.
        """
        self._require_key()
        topic = (topic or "").strip() or None
        focus = (focus or "").strip() or None
        # 知识点名称同时用作检索提示词：hashed 向量是词级匹配，带上知识点
        # 词汇能显著提高命中该知识点原文的概率。
        hits = self._retrieve(
            question,
            material_id=material_id,
            material_name=material_name,
            topic_hint=topic,
        )
        if not hits and not focus:
            return {
                "answer": "我在你上传的资料中没有找到直接依据。请换用资料中的原词再问，或上传包含该内容的资料后重试。我不会凭空编造答案。",
                "sources": [],
                "grounded": False,
                "mode": "material",
            }

        context = self._format_context(hits) if hits else "(none retrieved)"
        if topic or focus:
            messages = [
                {"role": "system", "content": TUTOR_SYSTEM},
                {"role": "user", "content": TUTOR_USER.format(
                    topic=topic or "当前知识点",
                    lecture=focus or "(none)",
                    question=question,
                    context=context,
                )},
            ]
            mode = "tutor"
        else:
            messages = [
                {"role": "system", "content": QA_SYSTEM},
                {"role": "user", "content": QA_USER.format(question=question, context=context)},
            ]
            mode = "material"

        response = self.llm.chat(messages, max_tokens=_ANSWER_MAX_TOKENS)
        answer = _clean_answer(response.text)
        if not answer or _looks_like_scratchpad(answer):
            # 免费模型偶发把「思考过程」写进正文并因截断而只剩草稿。
            # 强化一次输出约束后重试一次；仍不合格就如实告知，不展示草稿。
            answer = _clean_answer(self.llm.chat(
                [*messages, {
                    "role": "system",
                    "content": "Output ONLY the final answer for the student. "
                               "No analysis, no reasoning, no scratchpad.",
                }],
                max_tokens=_ANSWER_MAX_TOKENS,
            ).text)
            if not answer or _looks_like_scratchpad(answer):
                return {
                    "answer": "模型这次没有返回可用的回答，请再提问一次（可换个说法）。",
                    "sources": [],
                    "grounded": False,
                    "mode": "tutor" if (topic or focus) else "material",
                }
        sources = list(dict.fromkeys(h.source for h in hits if h.source))
        return {
            "answer": answer,
            "sources": sources,
            # 讲解页内容本身也来自该资料，因此有 focus 也算有依据。
            "grounded": bool(hits or focus),
            "retrieved": len(hits),
            "mode": mode,
            "model": response.model,
        }

    def _topic_for_material(
        self, material_id: str | None, material_name: str | None
    ) -> str | None:
        """把「当前问答资料」映射到索引里的 topic 标签。

        ``save_upload()`` 索引时把每个 chunk 的 ``topic`` 设为
        ``lecture_name(原始文件名)``。这里复用同一规则即可定位该资料的
        chunk，因此旧索引无需重建；后端重启后内存记录丢失时，前端传来的
        原始文件名同样能完成映射。
        """
        name = material_name
        if not name and material_id:
            rec = self._materials.get(material_id)
            if rec:
                name = rec.get("name")
        if not name:
            return None
        return lecture_name(name)

    def _retrieve(
        self,
        question: str,
        material_id: str | None = None,
        material_name: str | None = None,
        topic_hint: str | None = None,
    ) -> list[RetrievedChunk]:
        if self.store.count() == 0:
            return []
        # topic_hint（当前知识点名）只影响检索召回，不改变答案的提问内容。
        query = f"{topic_hint} {question}" if topic_hint else question
        vector = self.embedder.embed([query])[0]
        top_k = int(self.retrieval_cfg.get("top_k", 3))
        topic = self._topic_for_material(material_id, material_name)
        hits = self.store.search_by_vector(vector, top_k=top_k, topic=topic)
        if not hits and topic:
            # 安全兜底：若旧索引无法按该标签命中（例如文件名与索引标签不一致），
            # 退回全局检索，避免误报「未找到依据」。
            hits = self.store.search_by_vector(vector, top_k=top_k)
        return hits

    @staticmethod
    def _format_context(hits: Sequence[RetrievedChunk], limit: int = 700) -> str:
        blocks = []
        for i, h in enumerate(hits, 1):
            blocks.append(f"[{i}] {h.source}\n{h.text[:limit]}")
        return "\n\n".join(blocks)

    def close(self) -> None:
        try:
            self.store.close()
        except Exception:
            pass
