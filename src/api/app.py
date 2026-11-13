"""Student-facing HTTP API (FastAPI).

Exposes the existing P1/P4 capabilities (RAG + LLM) as a REST API for the
原生 JS 学生端 frontend:

    GET  /api/health
    POST /api/materials/upload      multipart: 上传课程资料 (PDF/TXT/MD)
    POST /api/materials/parse       LLM 解析资料 -> 抽取知识点 + 生成讲解与 quiz
    POST /api/quiz/generate         基于已上传资料生成 quiz（可选独立入口）
    POST /api/qa/ask                RAG + LLM 问答，带引用出处
    GET  /api/progress              学习进度（SQLite 可选；前端本地状态兜底）

Design:
  * Reuses src.utils.llm_client.LLMClient, src.retrieval.* and
    src.ingestion.* so behaviour matches the CLI path.
  * Citations are attached from retrieval metadata (never written by the LLM).
  * Missing API key -> the affected endpoint returns a clear 503 message
    instead of silently degrading; the health endpoint reports key status.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from ..utils.config import get_config, resolve_path
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


class ParseRequest(BaseModel):
    material_id: str


class QuizRequest(BaseModel):
    material_id: str | None = None
    topic: str | None = None
    count: int = Field(default=5, ge=1, le=10)


class AskRequest(BaseModel):
    question: str
    # 可选的「当前问答资料」：带上后 RAG 检索会限定在该资料范围内，
    # 不带时保持全局检索（兼容旧调用与 CLI 路径）。
    material_id: str | None = None
    material_name: str | None = None


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
def delete_material(material_id: str) -> dict[str, Any]:
    svc = get_material_service()
    try:
        return svc.delete_material(material_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/api/materials/reset")
def reset_materials() -> dict[str, Any]:
    """清除全部演示数据（含 ChromaDB 索引与上传文件），供前端"重置演示数据"调用。"""
    return get_material_service().reset()


@app.post("/api/quiz/generate")
def generate_quiz(req: QuizRequest) -> dict[str, Any]:
    svc = get_material_service()
    try:
        result = svc.generate_quiz(req.material_id, req.topic, req.count)
    except svc.MissingKey as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
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
        )
    except svc.MissingKey as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"问答失败：{type(exc).__name__}: {exc}")


@app.get("/api/progress")
def progress() -> dict[str, Any]:
    svc = get_material_service()
    return {
        "topics": svc.list_topics(),
        "overall_mastery": svc.overall_mastery(),
    }
