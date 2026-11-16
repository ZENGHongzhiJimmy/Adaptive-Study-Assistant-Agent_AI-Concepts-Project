"""Student-facing HTTP API (FastAPI).

Exposes the existing P1/P4 capabilities (RAG + LLM) as a REST API for the
原生 JS 学生端 frontend:

    GET  /api/health
    POST /api/materials/upload      multipart: 上传课程资料 (PDF/TXT/MD)
    POST /api/materials/parse       LLM 解析资料 -> 抽取知识点 + 生成讲解与 quiz
    POST /api/qa/ask                RAG + LLM 问答，带引用出处
    *    /api/agent/*               自适应 agent（LangGraph + policy.py，见 api/agent.py）

Design:
  * Reuses src.utils.llm_client.LLMClient, src.retrieval.* and
    src.ingestion.* so behaviour matches the CLI path.
  * Citations are attached from retrieval metadata (never written by the LLM).
  * Missing API key -> the affected endpoint returns a clear 503 message
    instead of silently degrading; the health endpoint reports key status.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from ..utils.config import get_config
from .agent import close_agent, get_agent, router as agent_router
from .services import (
    MaterialService,
    key_status,
)

_material_service: MaterialService | None = None


def get_material_service() -> MaterialService:
    global _material_service
    if _material_service is None:
        _material_service = MaterialService(get_config())
    return _material_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Warm up the service (loads config; lazy LLM client on first use).
    get_material_service()
    yield
    svc = _material_service
    if svc is not None:
        svc.close()
    close_agent()


app = FastAPI(
    title="Adaptive Study Assistant — Student API",
    version="0.1.0",
    lifespan=lifespan,
)

# 学生端为纯静态页，允许跨域（本地开发 http://localhost:8080 等）。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 自适应 agent 的 HTTP 入口（/api/agent/*）。前端不再自己决定下一步，
# 规划与状态更新都由这里的 LangGraph agent 产出。
app.include_router(agent_router)


class ParseRequest(BaseModel):
    material_id: str


class AskRequest(BaseModel):
    question: str
    # 可选的「当前问答资料」：带上后 RAG 检索会限定在该资料范围内，
    # 不带时保持全局检索（兼容旧调用与 CLI 路径）。
    material_id: str | None = None
    material_name: str | None = None
    # 「知识点讲解」页随堂提问：当前知识点名 + 正在阅读的讲解页内容。
    # 两者都来自该资料，因此可作为 grounded 上下文直接交给模型。
    topic: str | None = None
    focus: str | None = None


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "adaptive-study-assistant-student-api",
        "version": "0.1.0",
        "llm": key_status(get_config()),
        "materials_count": len(get_material_service().list_materials()),
    }


@app.post("/api/materials/upload")
async def upload_material(file: UploadFile = File(...)) -> dict[str, Any]:
    svc = get_material_service()
    try:
        rec = await svc.save_upload(file)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return rec


@app.post("/api/materials/parse")
def parse_material(req: ParseRequest) -> dict[str, Any]:
    svc = get_material_service()
    rec = svc.get_material(req.material_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="资料不存在或已被移除")
    try:
        result = svc.parse_material(req.material_id)
    except svc.MissingKey as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except svc.MaterialRemoved as exc:
        # 解析期间资料被删除：结果已丢弃，前端无需提示任何「解析成功」。
        raise HTTPException(status_code=410, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"解析失败：{type(exc).__name__}: {exc}")
    return result


@app.get("/api/materials/{material_id}")
def material_status(material_id: str) -> dict[str, Any]:
    """Front-end recovery probe after a page refresh.

    Returns whether the server already finished parsing this material and, if
    so, the cached result. A 404 means the backend lost its in-memory record
    (e.g. it was restarted) and the file has to be uploaded again.
    """
    info = get_material_service().material_status(material_id)
    if info is None:
        raise HTTPException(status_code=404, detail="资料不存在或已被移除")
    return info


@app.delete("/api/materials/{material_id}")
def delete_material(
    material_id: str, name: str | None = Query(default=None)
) -> dict[str, Any]:
    """Idempotent cleanup of an uploaded material.

    ``name`` is the original filename: it lets the service fall back to the
    lecture topic when the in-memory record was lost to a backend restart.
    Missing records are NOT a 404 -- the file and index are still cleaned.
    """
    result = get_material_service().delete_material(
        material_id, material_name=name
    )
    # 资料删掉后，它的掌握度/错题本/轮次也必须清掉，否则重新上传同名资料
    # 会立刻"继承"上次的进度，看起来像没删干净。
    get_agent().reset(material_id)
    return result


@app.post("/api/materials/reset")
def reset_materials() -> dict[str, Any]:
    """清除全部数据（含 ChromaDB 索引与上传文件），供前端"重置数据"调用。"""
    result = get_material_service().reset()
    # 学习状态现在由 agent 存在 SQLite 里，重置必须一起清，否则重传同名
    # 资料会带着旧掌握度回来。
    get_agent().reset()
    return result


@app.post("/api/qa/ask")
def ask(req: AskRequest) -> dict[str, Any]:
    question = (req.question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="问题不能为空")
    svc = get_material_service()
    try:
        return svc.ask(
            question,
            material_id=req.material_id,
            material_name=req.material_name,
            topic=req.topic,
            focus=req.focus,
        )
    except svc.MissingKey as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"问答失败：{type(exc).__name__}: {exc}")
