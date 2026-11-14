/* ============================================================
   api.js — 后端 HTTP API 封装（学生端）
   后端由 FastAPI 提供，默认地址 http://localhost:8000
   （可用 window.ASA_API_BASE 或 localStorage.asa_api_base 覆盖）

   接口：
   - GET  /api/health                    健康检查
   - POST /api/materials/upload          上传资料（multipart）
   - POST /api/materials/parse           解析资料 → 知识点 + quiz
   - POST /api/quiz/generate             基于资料生成 quiz
   - POST /api/qa/ask                    RAG + LLM 问答（带引用）
   - GET  /api/progress                  学习进度（可选，前端本地状态兜底）
   ============================================================ */

const API = (() => {

  const DEFAULT_BASE = 'http://localhost:8000';

  function base() {
    return (window.ASA_API_BASE)
      || localStorage.getItem('asa_api_base')
      || DEFAULT_BASE;
  }

  async function request(path, options = {}) {
    const url = base().replace(/\/$/, '') + path;
    const opts = Object.assign({ headers: {} }, options);
    // 只有带 body 且未显式设置 Content-Type 时才设 JSON
    if (opts.body && !(opts.body instanceof FormData) && !opts.headers['Content-Type']) {
      opts.headers['Content-Type'] = 'application/json';
    }
    let resp;
    try {
      resp = await fetch(url, opts);
    } catch (e) {
      // Network-layer failure: backend down, or the request was aborted by a
      // page refresh. NOT a parse failure — callers keep the material recoverable.
      return { ok: false, network: true, error: '无法连接后端服务（' + base() + '），请确认已启动后端。' };
    }
    let data = null;
    try { data = await resp.json(); } catch (e) { data = null; }
    if (!resp.ok) {
      const msg = (data && data.detail) || ('请求失败（HTTP ' + resp.status + '）');
      return { ok: false, error: (typeof msg === 'string' ? msg : JSON.stringify(msg)), status: resp.status };
    }
    return { ok: true, data };
  }

  /* 只有网络层失败（含刷新导致的请求中断）才是「可恢复」；带 HTTP status 的
     都是后端明确返回的错误，仍应按真实失败处理。 */
  function isNetworkFailure(r) { return !!r && !r.ok && !!r.network; }

  async function health() {
    return request('/api/health');
  }

  /* 刷新后查询后端资料真实状态：是否已解析、以及缓存的解析结果 */
  async function getMaterial(materialId) {
    return request('/api/materials/' + encodeURIComponent(materialId));
  }

  /* 上传单个资料文件，返回 { id, name, kind, size, status } */
  async function uploadMaterial(file) {
    const fd = new FormData();
    fd.append('file', file);
    return request('/api/materials/upload', { method: 'POST', body: fd });
  }

  /* 解析资料：后端对已上传的资料做 LLM 解析，抽取知识点并生成 quiz */
  async function parseMaterial(materialId) {
    return request('/api/materials/parse', {
      method: 'POST',
      body: JSON.stringify({ material_id: materialId }),
    });
  }

  /* 删除资料：同时清理后端索引（ChromaDB 向量），避免残留引用 */
  /* 删除资料：带上原始文件名，让后端在内存记录丢失（重启过）时也能按
     文件名推导 lecture/topic，兜底清掉磁盘文件与向量 chunk。 */
  async function deleteMaterial(materialId, name) {
    const q = name ? '?name=' + encodeURIComponent(name) : '';
    return request('/api/materials/' + encodeURIComponent(materialId) + q, {
      method: 'DELETE',
    });
  }

  /* 清除全部数据：后端会一并清理 ChromaDB 索引与上传文件 */
  async function resetMaterials() {
    return request('/api/materials/reset', { method: 'POST' });
  }

  /* 基于资料生成 quiz（可作为独立入口） */
  async function generateQuiz(payload) {
    return request('/api/quiz/generate', {
      method: 'POST',
      body: JSON.stringify(payload || {}),
    });
  }

  /* RAG + LLM 问答，返回 { answer, sources, grounded, mode }
     可选传入当前选中的课程资料，后端会把检索限定在该资料范围内。
     extra 支持 { topic, focus }：知识点讲解页随堂提问时，把当前知识点与
     正在阅读的讲解页内容一并送给后端，答案会按「助教讲解」的方式组织。 */
  async function ask(question, materialId, materialName, extra) {
    const body = { question };
    if (materialId) body.material_id = materialId;
    if (materialName) body.material_name = materialName;
    if (extra && extra.topic) body.topic = extra.topic;
    if (extra && extra.focus) body.focus = extra.focus;
    return request('/api/qa/ask', {
      method: 'POST',
      body: JSON.stringify(body),
    });
  }

  /* ---------- 自适应 Agent（LangGraph + policy.py） ----------
     前端不再自己算「下一步」，规划与状态更新全部由后端 agent 决定。
     返回结构与 engine.js 的快照翻译一一对应。 */

  async function agentPolicy() {
    return request('/api/agent/policy');
  }

  async function agentPlan(materialId, topics) {
    return request('/api/agent/plan', {
      method: 'POST',
      body: JSON.stringify({ material_id: materialId, topics: topics || [] }),
    });
  }

  async function agentObserve(materialId, payload) {
    return request('/api/agent/observe', {
      method: 'POST',
      body: JSON.stringify(Object.assign({ material_id: materialId }, payload || {})),
    });
  }

  async function agentPreTest(materialId, payload) {
    return request('/api/agent/pre-test', {
      method: 'POST',
      body: JSON.stringify(Object.assign({ material_id: materialId }, payload || {})),
    });
  }

  async function agentState(materialId) {
    return request('/api/agent/state/' + encodeURIComponent(materialId));
  }

  return {
    base, health, isNetworkFailure, getMaterial, uploadMaterial, parseMaterial,
    deleteMaterial, resetMaterials, generateQuiz, ask,
    agentPolicy, agentPlan, agentObserve, agentPreTest, agentState,
  };
})();
