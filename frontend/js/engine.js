/* ============================================================
   engine.js — 学生端知识模型 + 自适应规划 Agent（前端状态）
   本版本为「学生端」专用：
   - 学生自主上传课程资料，后端（LLM + RAG）解析并生成 quiz
   - 学习主页提示自主学习路径（依据上传资料解析出的知识点）
   - 课程资料 · AI 问答：上传资料 + AI 解析问答（带引用出处）
   - 学习进度：各知识点掌握度与学习轨迹

   知识点来源：学生上传的资料经后端解析后生成的知识点
   （后端 /api/materials/parse 返回 topics，其中包含讲解与 quiz）。
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
      hasPreTest: false,
      topics: [],                   // 当前生效的知识点（学生知识模型）
      trace: [],
      loopStage: 'plan',
      lastActivity: null,
      completed: 0,
      streak: 5,
      uploads: [],                  // 学生自主上传的资料（含后端解析结果）
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
        return;
      } catch (e) { /* 忽略损坏数据 */ }
    }
    reset();
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
     知识点形态（由后端解析资料生成）
     { id, name, icon, desc, mastery, attempts, errors, src,
       content: { explain:[{t,items,tip}], quiz:[{q,opts,a,why}], pretest:{...} } }
     ========================================================= */

  function topics() { return state.topics; }
  function topic(id) { return state.topics.find(t => t.id === id); }

  function overallMastery() {
    if (!state.topics.length) return 0;
    return Math.round(state.topics.reduce((s, t) => s + t.mastery, 0) / state.topics.length);
  }

  function pathTopics() { return state.topics; }

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

  function nextTopic() {
    const sorted = [...state.topics].sort((a, b) => {
      if (b.errors !== a.errors) return b.errors - a.errors;
      return a.mastery - b.mastery;
    });
    return sorted[0];
  }

  function nextPlan() {
    const t = nextTopic();
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

  /* ---------------- Pre-test ---------------- */
  function applyPreTest(answers) {
    (answers || []).forEach(a => {
      const t = topic(a.topicId);
      if (!t) return;
      t.mastery = a.correct ? 45 : 20;
      t.attempts += 1;
      t.errors = a.correct ? 0 : 1;
      t.pretested = true;
    });
    state.hasPreTest = state.topics.length > 0 && state.topics.every(t => t.pretested);
    state.loopStage = 'plan';
    save();
  }

  function pendingTopics() { return state.topics.filter(t => !t.pretested); }

  /* ---------------- 循环阶段 / 角色 ---------------- */
  function setStage(s) { state.loopStage = s; save(); }
  function stage() { return state.loopStage; }
  function setRole(r) { state.role = r; save(); }
  function role() { return state.role; }

  /* =========================================================
     学生自主上传资料（经后端解析生成知识点与 quiz）
     ========================================================= */
  function uploads() { return state.uploads; }

  /* 把后端解析结果合并进本地知识点模型：
     - 后端返回 { topics: [{ name, desc, explain, quiz, ... }] }
     - 每个知识点带 id、初始 mastery 30，pretested=false */
  function ingestParsed(parsed) {
    const tlist = (parsed && parsed.topics) || [];
    const materialId = parsed && (parsed.material_id || parsed.materialId);
    const added = [];
    tlist.forEach((p, i) => {
      const tid = 't_' + Date.now().toString(36) + '_' + i;
      const quiz = normalizeQuiz(p.quiz || p.questions || []);
      const topicObj = {
        id: tid,
        name: p.name || ('知识点 ' + (i + 1)),
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
    if (rec && rec.materialId) {
      // 后端解析结果的 topic.src 是「我的上传资料」或文件名，
      // 这里按 materialId 关联移除（见 ingestParsed 里对 src 的标记）。
      state.topics = state.topics.filter(t => t._materialId !== rec.materialId);
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
     已发布/学习路径信息（学生端语义简化为「我的知识点」）
     ========================================================= */
  function published() {
    return {
      count: state.topics.length,
      courses: [],
      latest: null,
      note: '上传课程资料后，AI 会解析出知识点并加入你的学习路径；已有知识点与进度不会被覆盖。',
    };
  }

  function pathGroups() {
    // 学生端按「资料出处」分组
    const groups = {};
    state.topics.forEach(t => {
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
    plan, nextTopic, nextPlan, applyResult, applyPreTest,
    setStage, stage, setRole, role,
    published, pathTopics, pathGroups, pendingTopics,
    dismissAppend,
    uploads, addUpload, markUpload, removeUpload, ingestParsed,
  };
})();
