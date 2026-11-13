/* ============================================================
   engine.js — 学生端知识模型 + 自适应规划 Agent（前端状态）
   本版本为「学生端」专用：
   - 学生自主上传课程资料，后端（LLM + RAG）解析并生成 quiz
   - 学习主页按「上传的课程资料文件」分类，每份资料是一个独立学习模块
   - 选中资料后，才展示该资料的自主学习路径（Pre-test → 推荐活动 → 进度）
   - 课程资料 · AI 问答：上传资料 + AI 解析问答（带引用出处）
   - 学习进度：各知识点掌握度与学习轨迹，可切换资料维度

   知识点来源：学生上传的资料经后端解析后生成的知识点
   （后端 /api/materials/parse 返回 topics，其中包含讲解与 quiz）。
   每个知识点通过 _materialId 绑定所属资料，学习状态按资料彼此隔离。
   自适应规划沿用 Plan → Act → Observe → Update → Re-plan 闭环。
   ============================================================ */

const Engine = (() => {

  /* ---------------- 持久化 ---------------- */
  const KEY = 'asa_student_state_v1';

  let state = blankState();

  function blankState() {
    return {
      role: 'student',
      studentName: 'student1',
      hasPreTest: false,           // 兼容字段：所有资料是否都已完成起点评估
      topics: [],                   // 全部知识点（跨资料），各自带 _materialId
      trace: [],
      loopStage: 'plan',
      lastActivity: null,
      completed: 0,
      streak: 5,
      uploads: [],                  // 学生自主上传的资料（含后端解析结果）
      activeMaterialId: null,       // 当前选中的资料（uploads[].id）
    };
  }

  function init() {
    let saved = null;
    try { saved = localStorage.getItem(KEY); } catch (e) { saved = null; }
    if (saved) {
      try {
        const parsed = JSON.parse(saved);
        Object.keys(state).forEach(k => { delete state[k]; });
        Object.keys(parsed).forEach(k => { state[k] = parsed[k]; });
        if (!Array.isArray(state.uploads)) state.uploads = [];
        if (!Array.isArray(state.topics)) state.topics = [];
        if (!Array.isArray(state.trace)) state.trace = [];
        if (typeof state.activeMaterialId === 'undefined') state.activeMaterialId = null;
        migrateLinks();
        recoverInterrupted();
        return;
      } catch (e) { /* 忽略损坏数据 */ }
    }
    reset();
  }

  /* 兼容旧数据：早期版本的知识点可能只有 src（文件名）而没有 _materialId，
     这里按文件名回填关联，保证历史进度仍归属到对应资料模块。 */
  function migrateLinks() {
    state.topics.forEach(t => {
      if (t._materialId) return;
      const rec = state.uploads.find(u => u.name && u.name === t.src);
      if (rec) t._materialId = rec.materialId || rec.id;
    });
    if (state.activeMaterialId && !findMaterial(state.activeMaterialId)) {
      state.activeMaterialId = null;
    }
    /* 旧版本把 LLM 生成的「材料解读/摘要」写进了资料记录，界面已不再展示，
       统一清掉，保证所有资料（PDF / TXT / MD）的记录结构与显示完全一致。 */
    state.uploads.forEach(u => { if (u && 'summary' in u) delete u.summary; });
    save();
  }

  /* 刷新页面会中断正在进行的上传/解析请求，localStorage 里可能残留
     uploading / uploaded / parsing。这些都不是失败：统一归一为 interrupted，
     由视图层在页面加载后自动续传，避免刷新后卡住或误显示「解析失败」。 */
  function recoverInterrupted() {
    state.uploads.forEach(u => {
      if (u && (u.status === 'uploading' || u.status === 'uploaded' ||
                u.status === 'parsing' || u.status === 'interrupted')) {
        u.status = 'interrupted';
        delete u.error;
      }
    });
    save();
  }

  function reset() {
    const s = blankState();
    Object.keys(s).forEach(k => { state[k] = s[k]; });
    save();
  }

  function save() {
    try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (e) {}
  }

  /* =========================================================
     资料文件（学习模块）
     rec = { id, name, kind, size, pages, status, materialId,
             topicCount, ... }
     ========================================================= */

  function uploads() { return state.uploads; }

  function materialKey(rec) { return rec ? (rec.materialId || rec.id) : null; }

  function findMaterial(ref) {
    if (!ref) return null;
    if (typeof ref === 'object') return ref;
    return state.uploads.find(u => u.id === ref || u.materialId === ref) || null;
  }

  function activeMaterial() { return findMaterial(state.activeMaterialId); }

  function selectMaterial(ref) {
    const rec = findMaterial(ref);
    state.activeMaterialId = rec ? rec.id : null;
    save();
    return rec;
  }

  /* 知识点归属于哪个资料（返回 upload rec，用于详情页/活动页返回模块） */
  function materialOfTopic(t) {
    if (!t) return null;
    if (t._materialId) return findMaterial(t._materialId);
    return state.uploads.find(u => u.name && u.name === t.src) || null;
  }

  /* 按资料取知识点：优先 _materialId；旧数据按文件名兜底 */
  function topicsOf(ref) {
    const rec = findMaterial(ref);
    if (!rec) return [];
    const keys = [rec.materialId, rec.id].filter(Boolean);
    return state.topics.filter(t => {
      if (t._materialId) return keys.indexOf(t._materialId) !== -1;
      return !!rec.name && (t.src === rec.name || t.src === materialKey(rec));
    });
  }

  /* 一份资料的概览统计（供主页卡片与资料模块复用） */
  function materialStats(ref) {
    const rec = findMaterial(ref);
    const ts = rec ? topicsOf(rec) : [];
    const avg = ts.length ? Math.round(ts.reduce((s, t) => s + t.mastery, 0) / ts.length) : 0;
    const quizCount = ts.reduce((n, t) => n + (((t.content || {}).quiz) || []).length, 0);
    const explainCount = ts.reduce((n, t) => n + (((t.content || {}).explain) || []).length, 0);
    const pending = ts.filter(t => !t.pretested).length;
    const ids = ts.map(t => t.id);
    const trace = state.trace.filter(x => ids.indexOf(x.topicId) !== -1);
    return {
      rec, topics: ts, count: ts.length, avg,
      quizCount, explainCount, pending,
      pretestDone: ts.length > 0 && pending === 0,
      parsed: !!rec && (rec.status === 'parsed' || rec.status === 'indexed'),
      trace,
    };
  }

  /* =========================================================
     知识点形态（由后端解析资料生成）
     { id, name, icon, desc, mastery, attempts, errors, src,
       _materialId,
       content: { explain:[{t,items,tip}], quiz:[{q,opts,a,why}], pretest:{...} } }
     ========================================================= */

  function topics() { return state.topics; }
  function topic(id) { return state.topics.find(t => t.id === id); }

  function overallMastery(list) {
    const arr = Array.isArray(list) ? list : state.topics;
    if (!arr.length) return 0;
    return Math.round(arr.reduce((s, t) => s + t.mastery, 0) / arr.length);
  }

  /* 不传 = 全部知识点；传资料 rec/id 或知识点数组 = 该范围 */
  function pathTopics(arg) {
    if (Array.isArray(arg)) return arg;
    if (arg) return topicsOf(arg);
    return state.topics;
  }

  function topicContent(topicId) {
    const t = topic(topicId);
    if (!t) return { explain: [], quiz: [], pretest: null };
    return t.content || { explain: [], quiz: [], pretest: null };
  }

  /* =========================================================
     自适应规划（学生端 3 种活动）
     ========================================================= */

  function plan(topicId) {
    const t = topic(topicId);
    if (!t) return null;

    let actId, reason, rule;

    if (t.errors >= 2 && t.mastery < 75) {
      actId = 'review';
      rule = `errors >= 2 且 mastery < 75`;
      reason = `该知识点已累计 <b>${t.errors} 次错误</b>，且掌握度仅 ${t.mastery}%，先通过复习纠正错误概念再继续。`;
    } else if (t.mastery < 45) {
      actId = 'explain';
      rule = `mastery < 45`;
      reason = `掌握度 ${t.mastery}% 属于薄弱区，先学习资料生成的知识点讲解。`;
    } else if (t.mastery < 80) {
      actId = 'practice';
      rule = `45 <= mastery < 80`;
      reason = `掌握度 ${t.mastery}% 已有基础但仍不稳定，通过练习巩固应用。`;
    } else {
      actId = 'review';
      rule = `mastery >= 80`;
      reason = `掌握度 ${t.mastery}% 已达熟练，安排间隔复习以维持长期记忆。`;
    }

    return { topic: t, activity: DATA.activities[actId], reason, rule };
  }

  function nextTopic(list) {
    const arr = pathTopics(list);
    const sorted = [...arr].sort((a, b) => {
      if (b.errors !== a.errors) return b.errors - a.errors;
      return a.mastery - b.mastery;
    });
    return sorted[0];
  }

  /* 传入资料 rec/id 时，只在该资料范围内推荐下一步 */
  function nextPlan(arg) {
    const t = nextTopic(arg);
    return t ? plan(t.id) : null;
  }

  /* ---------------- 活动完成 → 状态更新 ---------------- */
  function applyResult(topicId, actId, outcome) {
    const t = topic(topicId);
    if (!t) return null;

    const before = t.mastery;
    let delta = 0;

    switch (actId) {
      case 'explain':
        delta = 9;
        break;
      case 'practice': {
        const quizOk = outcome.quizCorrect !== false;
        delta = quizOk ? 12 : 4;
        if (!quizOk) t.errors += 1;
        break;
      }
      case 'review':
        delta = 7;
        t.errors = 0;
        break;
      default:
        delta = 5;
    }

    t.mastery = Math.max(0, Math.min(100, t.mastery + delta));
    t.attempts += 1;
    t.lastActivity = DATA.activities[actId].name;

    state.completed += 1;
    state.lastActivity = { topicId, actId, delta, ts: Date.now() };

    state.trace.unshift({
      ts: Date.now(),
      topicId, topicName: t.name,
      actId, actName: DATA.activities[actId].name,
      delta, before, after: t.mastery,
      ok: outcome.correct !== false,
    });
    if (state.trace.length > 30) state.trace.pop();

    const after = t.mastery;
    const re = plan(topicId);
    save();
    return { topic: t, before, after, delta, re };
  }

  /* ---------------- Pre-test（按资料独立评估） ---------------- */
  function applyPreTest(answers) {
    (answers || []).forEach(a => {
      const t = topic(a.topicId);
      if (!t) return;
      t.mastery = a.correct ? 45 : 20;
      t.attempts += 1;
      t.errors = a.correct ? 0 : 1;
      t.pretested = true;
    });
    // 兼容字段：仅当全部资料的知识点都评估完才为 true
    state.hasPreTest = state.topics.length > 0 && state.topics.every(t => t.pretested);
    state.loopStage = 'plan';
    save();
  }

  function pretestDone(ref) {
    const ts = topicsOf(ref);
    return ts.length > 0 && ts.every(t => t.pretested);
  }

  /* 不传 = 全部待评估知识点；传资料 = 仅该资料 */
  function pendingTopics(ref) {
    const ts = ref ? topicsOf(ref) : state.topics;
    return ts.filter(t => !t.pretested);
  }

  /* ---------------- 循环阶段 / 角色 ---------------- */
  function setStage(s) { state.loopStage = s; save(); }
  function stage() { return state.loopStage; }
  function setRole(r) { state.role = r; save(); }
  function role() { return state.role; }

  /* =========================================================
     学生自主上传资料（经后端解析生成知识点与 quiz）
     ========================================================= */

  /* 把后端解析结果合并进本地知识点模型：
     - 后端返回 { topics: [{ name, desc, explain, quiz, ... }], summary }
     - 每个知识点带 id、初始 mastery 30，pretested=false，并绑定资料 id */
  function ingestParsed(parsed) {
    const tlist = (parsed && parsed.topics) || [];
    const materialId = parsed && (parsed.material_id || parsed.materialId);
    /* 续传时后端可能早已解析成功、只是响应因刷新丢失。按「资料 + 知识点名」
       去重，避免重复插入，同时保留学生已有的学习进度（mastery/attempts）。 */
    const existing = new Set(
      state.topics
        .filter(t => t && materialId && t._materialId === materialId)
        .map(t => String(t.name || '').trim().toLowerCase())
    );
    const added = [];
    tlist.forEach((p, i) => {
      const name = p.name || ('知识点 ' + (i + 1));
      const key = String(name).trim().toLowerCase();
      if (existing.has(key)) return;
      existing.add(key);
      const tid = 't_' + Date.now().toString(36) + '_' + i;
      const quiz = normalizeQuiz(p.quiz || p.questions || []);
      const topicObj = {
        id: tid,
        name: name,
        icon: p.icon || '📘',
        desc: p.desc || '',
        mastery: 30,
        attempts: 0,
        errors: 0,
        pretested: false,
        src: p.src || (parsed.source || '我的上传资料'),
        _materialId: materialId || null,
        content: {
          explain: normalizeExplain(p.explain || p.points || []),
          quiz: quiz,
          pretest: quiz[0] || null,
        },
      };
      state.topics.push(topicObj);
      added.push(topicObj);
    });
    if (added.length) {
      state.hasPreTest = false;
      state.lastAppend = {
        ts: Date.now(),
        count: added.length,
        courseTitle: parsed.source || '我的上传资料',
        materialId: materialId || null,
        names: added.slice(0, 5).map(t => t.icon + ' ' + t.name),
      };
    }
    save();
    return added;
  }

  function normalizeQuiz(list) {
    return (list || []).map((q, i) => {
      if (typeof q === 'string') {
        return { q, opts: [], a: -1, why: '' };
      }
      return {
        q: q.q || q.question || q.prompt || ('题目 ' + (i + 1)),
        opts: q.opts || q.options || q.choices || [],
        a: typeof q.a === 'string' ? 'ABCD'.indexOf(q.a.toUpperCase()) : (typeof q.a === 'number' ? q.a : (q.correct_option !== undefined ? 'ABCD'.indexOf(String(q.correct_option).toUpperCase()) : -1)),
        why: q.why || q.explanation || '',
      };
    });
  }

  function normalizeExplain(list) {
    return (list || []).map(s => {
      if (typeof s === 'string') return { t: s, items: [], tip: '' };
      return {
        t: s.t || s.title || s.topic || '',
        items: s.items || s.points || s.content || [],
        tip: s.tip || s.summary || s.takeaway || '',
      };
    });
  }

  function addUpload(file) {
    const rec = {
      id: 'up_' + Date.now() + '_' + Math.floor(Math.random() * 1000),
      name: file.name,
      kind: (file.name.split('.').pop() || 'FILE').toUpperCase().slice(0, 5),
      size: fmtSize(file.size),
      from: '我的上传',
      status: 'uploading',   // uploading → uploaded → parsing → parsed | error
      ts: Date.now(),
    };
    state.uploads.unshift(rec);
    save();
    return rec;
  }

  function markUpload(id, patch) {
    const r = state.uploads.find(x => x.id === id);
    if (r) Object.assign(r, patch);
    save();
    return r;
  }

  function removeUpload(id) {
    // 找到该资料对应的知识点来源，一并清除，避免残留
    const rec = state.uploads.find(x => x.id === id);
    if (rec) {
      const keys = [rec.materialId, rec.id].filter(Boolean);
      state.topics = state.topics.filter(t =>
        !t._materialId || keys.indexOf(t._materialId) === -1);
      if (state.activeMaterialId === rec.id) state.activeMaterialId = null;
    }
    state.uploads = state.uploads.filter(x => x.id !== id);
    save();
  }

  function dismissAppend() { state.lastAppend = null; save(); }

  function fmtSize(b) {
    if (!b && b !== 0) return '—';
    if (b < 1024) return b + ' B';
    if (b < 1024 * 1024) return (b / 1024).toFixed(0) + ' KB';
    return (b / 1024 / 1024).toFixed(1) + ' MB';
  }

  /* =========================================================
     路径信息（学生端语义：按资料出处分组）
     ========================================================= */
  function published() {
    return {
      count: state.topics.length,
      courses: [],
      latest: null,
      note: '',
    };
  }

  /* 接受资料 rec/id 或知识点数组；不传 = 全部资料 */
  function pathGroups(arg) {
    const list = Array.isArray(arg) ? arg : (arg ? topicsOf(arg) : state.topics);
    const groups = {};
    list.forEach(t => {
      const key = t.src || '我的上传资料';
      if (!groups[key]) groups[key] = [];
      groups[key].push(t);
    });
    return Object.keys(groups).map(key => ({ course: { title: key }, topics: groups[key] }));
  }

  init();

  return {
    state, init, reset, save,
    topics, topic, overallMastery, topicContent,
    plan, nextTopic, nextPlan, applyResult, applyPreTest, pretestDone,
    setStage, stage, setRole, role,
    published, pathTopics, pathGroups, pendingTopics,
    dismissAppend,
    uploads, addUpload, markUpload, removeUpload, ingestParsed,
    // 资料维度
    materialKey, findMaterial, activeMaterial, selectMaterial,
    topicsOf, materialStats, materialOfTopic,
  };
})();
