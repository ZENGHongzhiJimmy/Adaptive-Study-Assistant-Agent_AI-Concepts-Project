"""删除「解析中」的资料：解析结果必须整体丢弃，不留残留。

场景：学生在资料还处于 parsing 时点「移除」。DELETE 会立刻清掉磁盘文件与
向量索引，但那一轮 LLM 解析跑在服务端线程里，无法被中断。修复前它跑完会把
知识点、解析缓存写回一个已经删除的资料，前端收到响应后又在右下角弹出
「已从「X」解析出 N 个知识点」。

这里用桩件（无网络、无 ChromaDB）断言三件事：
1. 解析中途被删除 → 抛 ``MaterialRemoved``，什么都不写回；
2. 已经删除的资料再发起解析 → 同样被拒；
3. 同一份文件重新上传 → 清掉删除标记，解析恢复正常。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.api.services import MaterialService

TOPICS_JSON = {
    "summary": "一份测试资料",
    "topics": [
        {
            "name": "梯度下降",
            "desc": "按梯度反方向更新参数",
            "explain": [{"t": "直觉", "items": ["沿最陡方向走"], "tip": "步长别太大"}],
            "quiz": [{"q": "梯度下降往哪走？", "opts": ["梯度反方向", "梯度正方向"], "a": 0, "why": "最小化损失"}],
        }
    ],
}


class _Store:
    """ChromaDB 桩：记录删除请求，不做真实向量操作。"""

    def __init__(self) -> None:
        self.delete_calls: list[dict] = []

    def upsert_chunks(self, chunks, vectors, extra_metadata=None) -> None:  # noqa: ANN001
        return None

    def delete_where(self, where: dict) -> int:  # noqa: ANN001
        self.delete_calls.append(where)
        return 0

    def delete_by_ids(self, ids) -> int:  # noqa: ANN001
        return 0


class _Embedder:
    def embed(self, texts):  # noqa: ANN001
        return [[0.0, 0.0] for _ in texts]


class _LLM:
    """LLM 桩：在「思考」期间触发 on_call()，用来模拟解析途中被删除。"""

    is_configured = True
    model = "stub"

    def __init__(self, on_call=None) -> None:  # noqa: ANN001
        self.on_call = on_call

    def chat(self, messages, *, max_tokens=None):  # noqa: ANN001
        if self.on_call:
            self.on_call()
        return SimpleNamespace(text=json.dumps(TOPICS_JSON), model=self.model)


class _Upload:
    def __init__(self, filename: str, data: bytes) -> None:
        self.filename = filename
        self._data = data

    async def read(self) -> bytes:
        return self._data


def _service(tmp_path: Path, on_llm=None) -> MaterialService:  # noqa: ANN001
    svc = MaterialService.__new__(MaterialService)
    svc.config = {}
    svc.upload_dir = tmp_path
    svc.embedder = _Embedder()
    svc.store = _Store()
    svc.retrieval_cfg = {"chunk_size": 200, "chunk_overlap": 20}
    svc._materials = {}
    svc._topics = {}
    svc._parse_results = {}
    svc._removed = set()
    svc._llm = _LLM(on_llm)
    svc._tmp_path = tmp_path
    return svc


def _upload_one(svc: MaterialService) -> str:
    rec = asyncio.run(svc.save_upload(_Upload("lecture.txt", b"gradient descent notes " * 20)))
    return rec["id"]


def test_delete_during_parse_discards_everything(tmp_path: Path) -> None:
    holder: dict[str, object] = {}

    def _delete_midway() -> None:
        # 相当于学生在「解析中」点了移除：DELETE 已经清理完，解析线程还在跑
        svc.delete_material(str(holder["mid"]), material_name="lecture.txt")

    svc = _service(tmp_path, on_llm=_delete_midway)
    mid = _upload_one(svc)
    holder["mid"] = mid

    with pytest.raises(MaterialService.MaterialRemoved):
        svc.parse_material(mid)

    assert svc._parse_results == {}, "已删除的资料不能留下解析缓存"
    assert svc._topics == {}, "已删除的资料不能留下知识点"
    assert mid in svc._removed
    assert svc._materials == {}
    # 上传时写入的向量索引已被 DELETE 按 material_id 清理
    assert {"material_id": mid} in svc.store.delete_calls


def test_parse_after_delete_is_rejected(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    mid = _upload_one(svc)
    svc.delete_material(mid, material_name="lecture.txt")

    with pytest.raises(ValueError):
        svc.parse_material(mid)


def test_reupload_same_file_clears_tombstone(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    mid = _upload_one(svc)
    svc.delete_material(mid, material_name="lecture.txt")
    assert mid in svc._removed

    again = _upload_one(svc)
    assert again == mid, "同名同大小文件应复用同一个 material id"
    assert mid not in svc._removed, "重新上传必须清掉删除标记，否则无法解析"

    out = svc.parse_material(mid)
    assert out["topics"][0]["name"] == "梯度下降"
    assert svc._parse_results[mid]["source"] == "lecture.txt"
