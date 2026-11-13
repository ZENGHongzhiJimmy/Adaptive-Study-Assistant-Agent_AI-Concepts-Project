"""P5-b in-lecture Q&A tests (offline).

The「知识点讲解」question box and the「课程资料 · AI 问答」panel share one
endpoint (``POST /api/qa/ask``). These tests pin the behaviour that matters for
the student UI:

* a question asked on a slide is answered by the LLM in ``tutor`` mode, with
  the current knowledge point + slide text sent as grounded context;
* a slide question is still answerable when retrieval returns nothing;
* a question with neither retrieval nor slide context degrades to the explicit
  "no evidence" message instead of a fabricated answer;
* a model that leaks its scratchpad into the answer is retried once and never
  shown to the student.

Everything runs offline: the LLM, the embedder and the vector store are stubs.
"""

from __future__ import annotations

from types import SimpleNamespace

from src.api.services import MaterialService, _clean_answer, _looks_like_scratchpad
from src.retrieval.vector_store import RetrievedChunk


class StubStore:
    def __init__(self, hits: list[RetrievedChunk] | None = None) -> None:
        self.hits = list(hits or [])
        self.searches: list[dict] = []

    def count(self) -> int:
        return len(self.hits) or 1

    def search_by_vector(self, vector, top_k=3, topic=None):
        self.searches.append({"top_k": top_k, "topic": topic})
        return self.hits[:top_k]


class StubEmbedder:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def embed(self, texts):
        self.queries.extend(texts)
        return [[0.0, 1.0] for _ in texts]


class StubLLM:
    def __init__(self, replies: list[str]) -> None:
        self.is_configured = True
        self.model = "stub:free"
        self.replies = list(replies)
        self.calls: list[list[dict[str, str]]] = []

    def chat(self, messages, *, max_tokens=None):
        self.calls.append(list(messages))
        text = self.replies.pop(0) if self.replies else "好的。"
        return SimpleNamespace(text=text, model=self.model)


def _service(replies: list[str], hits: list[RetrievedChunk] | None = None) -> MaterialService:
    """Build a service without ChromaDB / network: only what ``ask`` touches."""
    svc = MaterialService.__new__(MaterialService)
    svc.config = {}
    svc._materials = {}
    svc._llm = StubLLM(replies)
    svc.store = StubStore(hits)
    svc.embedder = StubEmbedder()
    svc.retrieval_cfg = {"top_k": 3}
    return svc


def _hit() -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id="c1",
        text="向量检索把文本映射为稠密向量，再比较距离。",
        lecture="Lecture 02",
        page=1,
        topic="Lecture 02",
        source="Source: Lecture 02, Page 1",
        distance=0.2,
    )


def test_slide_question_uses_tutor_prompt_with_slide_context() -> None:
    svc = _service(["one-hot 与 embedding 的区别在于是否表达语义距离。"])

    out = svc.ask(
        "为什么检索要用 embedding？",
        topic="向量表示",
        focus="标题：文本向量化\nembedding 让语义相近的文本距离更近",
    )

    assert out["mode"] == "tutor"
    assert out["grounded"] is True
    prompt = svc._llm.calls[0][-1]["content"]
    assert "向量表示" in prompt
    assert "embedding 让语义相近的文本距离更近" in prompt
    assert "为什么检索要用 embedding？" in prompt
    # 知识点名同时作为检索提示，提升召回
    assert any("向量表示" in q for q in svc.embedder.queries)


def test_slide_question_answers_without_retrieval() -> None:
    svc = _service(["RAG 先召回再生成，既省 token 又降低幻觉。"], hits=[])

    out = svc.ask(
        "为什么要先粗排？",
        topic="检索增强生成",
        focus="标题：两段式检索",
    )

    assert out["mode"] == "tutor"
    assert out["grounded"] is True      # 讲解页本身来自资料
    assert out["retrieved"] == 0
    assert out["answer"].startswith("RAG")


def test_no_retrieval_and_no_slide_degrades_to_no_evidence() -> None:
    svc = _service(["不应被调用"], hits=[])

    out = svc.ask("这份资料讲了什么？")

    assert out["mode"] == "material"
    assert out["grounded"] is False
    assert out["sources"] == []
    assert "没有找到直接依据" in out["answer"]
    assert svc._llm.calls == []


def test_material_mode_still_cites_sources() -> None:
    svc = _service(["向量检索先算向量再比距离。"], hits=[_hit()])

    out = svc.ask("检索是怎么做的？")

    assert out["mode"] == "material"
    assert out["sources"] == ["Source: Lecture 02, Page 1"]
    assert out["grounded"] is True


def test_leaked_scratchpad_is_retried_and_never_shown() -> None:
    leaked = "The student is asking about embeddings. Let me look at the slide..."
    svc = _service([leaked, "embedding 用稠密向量表达语义相似度。"])

    out = svc.ask("embedding 是什么？", topic="向量表示", focus="标题：文本向量化")

    assert out["answer"] == "embedding 用稠密向量表达语义相似度。"
    assert len(svc._llm.calls) == 2       # 重试一次
    assert "No analysis" in svc._llm.calls[1][-1]["content"]


def test_persistent_scratchpad_returns_retry_message() -> None:
    leaked = "We need to answer in Chinese. Let me draft: ..."
    svc = _service([leaked, leaked])

    out = svc.ask("这个概念怎么用？", topic="应用", focus="标题：案例")

    assert "请再提问一次" in out["answer"]
    assert out["grounded"] is False


def test_answer_cleanup_strips_transport_artefacts() -> None:
    assert _clean_answer("/n 答案正文") == "答案正文"
    assert _clean_answer("```text\n答案正文\n```") == "答案正文"
    assert _looks_like_scratchpad("The student is asking about X") is True
    assert _looks_like_scratchpad("one-hot 维度等于词表大小") is False
