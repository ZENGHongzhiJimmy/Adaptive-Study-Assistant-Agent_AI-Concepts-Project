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
      // Agent 快照：{ policy, plans: { [materialKey]: planPayload }, error }
      // 规划结果的唯一来源是后端 LangGraph agent；这里只是渲染用的缓存。
      agent: { policy: null, plans: {}, error: null },
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
    /* 旧版本把彩色 emoji 存进了知识点 icon；界面已统一改用单色线性 SVG，
       这里把历史数据归一成语义图标名，避免陈旧 emoji 再被任何视图渲染出来。 */
    state.topics.forEach(t => {
      if (t && t.icon && !/^[a-zA-Z]+$/.test(String(t.icon))) t.icon = 'bookOpen';
    });
    /* 新规则用 explainDone / mistakes 判断进度。旧数据补齐字段；
       旧的 errors 是「答错次数」但没有对应错题卡，留着会让复习页无卡可翻，
       因此没有错题卡时把 errors 归零（掌握度本身仍保留）。 */
    state.topics.forEach(t => {
      if (!Array.isArray(t.mistakes)) t.mistakes = [];
      if (typeof t.explainDone !== 'boolean') t.explainDone = false;
      if (typeof t.failStreak !== 'number') t.failStreak = 0;
      if (typeof t.explainRuns !== 'number') t.explainRuns = t.explainDone ? 1 : 0;
      if (typeof t.needHelp !== 'boolean') t.needHelp = false;
      if (!t.mistakes.length) t.errors = 0;
    });
    if (state.activeMaterialId && !findMaterial(state.activeMaterialId)) {
      state.activeMaterialId = null;
    }
    /* Agent 快照：旧版本把轮次状态存在 state.rolls 里（本地规则时代），
       现在轮次由 agent 服务端保存，本地只缓存它返回的计划快照。
       旧字段直接清掉，避免两套轮次状态并存。 */
    if (state.rolls) delete state.rolls;
    if (!state.agent || typeof state.agent !== 'object' || Array.isArray(state.agent)) {
      state.agent = { policy: null, plans: {}, error: null };
    }
    if (!state.agent.plans || typeof state.agent.plans !== 'object') state.agent.plans = {};
    if (typeof state.agent.error === 'undefined') state.agent.error = null;
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
    const quizCount = ts.reduce((n, t) => n + quizBank(t).length, 0);
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

  /* ---------------- 掌握判定 ----------------
     判定只有一处：policy.py::is_mastered（≥ MASTERED 且错题本为空）。
     前端不再自带阈值，直接读 agent 快照里的 by_topic[topic_id].mastered。
     没有快照时返回 false —— 「不知道」不能当作「已掌握」，界面会显示
     Agent 未连接，也不会因此少排活动。
     阈值本身（80 / 55）随快照下发（mastered_at / explain_cap），
     文案统一走 masteryGoal()，前端一个数字都不写。 */
  function masteredThreshold(ref) {
    const snap = agentSnapshot(ref);
    const value = snap ? Number(snap.mastered_at) : NaN;
    return Number.isFinite(value) && value > 0 ? value : null;
  }

  /* 文案用的掌握线：有快照就显示服务端的数字，没有就用文字，不猜数值 */
  function masteryGoal(ref) {
    const value = masteredThreshold(ref);
    return value === null ? '掌握线' : `${value}%`;
  }

  function isMastered(t) {
    if (!t) return false;
    const snap = agentSnapshot(materialOfTopic(t));
    const d = snap && snap.by_topic ? snap.by_topic[t.id] : null;
    return !!(d && d.mastered);
  }

  /* 可作答的题目：必须有题干、至少 2 个选项、且正确答案下标合法。
     解析结果偶尔会缺 a（-1）或选项不足，这类题「怎么选都判错」，
     必须在进入学习前剔除，否则学生会一直被认为答错。 */
  function quizBank(t) {
    const list = (t && t.content && t.content.quiz) || [];
    return list.filter(q => q && q.q && Array.isArray(q.opts) && q.opts.length >= 2 &&
      Number.isInteger(q.a) && q.a >= 0 && q.a < q.opts.length);
  }

  function explainPages(t) { return (t && t.content && t.content.explain) || []; }
  function mistakesOf(t) { return (t && Array.isArray(t.mistakes)) ? t.mistakes : []; }

  /* 复习卡片来自「错题本」，而不是讲解页的 tip */
  function reviewCards(topicId) {
    const t = topic(topicId);
    return mistakesOf(t).map(m => ({
      q: m.q || '',
      a: (m.opts && m.opts[m.a]) || '',
      why: m.why || '',
    }));
  }

  /* =========================================================
     自适应规划（学生端 3 种活动）
     ========================================================= */

  /* =========================================================
     自适应规划 —— 决策来自后端 LangGraph agent，前端只负责渲染
     ---------------------------------------------------------
     这里不再有任何规划规则：plan() / rollPlan() 只读 agent 返回的快照
     （state.agent.plans[materialKey]）。规则的唯一实现在
     src/agent/policy.py，由 /api/agent/* 暴露，实验脚本用同一份函数。
     快照缺失时返回 null / pendingAgent，界面显示「正在询问 Agent」，
     绝不会退回到另一套本地规则——那正是这个重构要消除的东西。
     ========================================================= */

  /* agent 请求可能在途：同一个资料只允许一次并发请求 */
  const inflight = new Map();

  function agentKeyOf(ref) {
    const rec = findMaterial(ref);
    return rec ? String(rec.materialId || rec.id) : null;
  }

  /* 把本地知识点打包成 policy.TopicState 需要的形状 */
  function agentTopics(ref) {
    return pathTopics(ref).map((t, i) => ({
      topic_id: t.id,
      name: t.name || '',
      mastery: Number(t.mastery) || 0,
      attempts: Number(t.attempts) || 0,
      errors: Number(t.errors) || 0,
      mistakes: mistakesOf(t),
      explain_done: !!t.explainDone,
      explain_runs: Number.isFinite(t.explainRuns) ? t.explainRuns : 0,
      fail_streak: Number(t.failStreak) || 0,
      need_help: !!t.needHelp,
      pretested: !!t.pretested,
      explain_pages: explainPages(t).length,
      quiz_count: quizBank(t).length,
      order: i,
    }));
  }

  function agentSnapshot(ref) {
    const key = agentKeyOf(ref);
    if (!key || !state.agent || !state.agent.plans) return null;
    return state.agent.plans[key] || null;
  }

  /* 把 agent 返回的权威状态合并回本地知识点（mastery / 错题本 / 计数） */
  function applySnapshot(ref, payload) {
    if (!payload) return null;
    (payload.topics || []).forEach(x => {
      const t = topic(x.topic_id);
      if (!t) return;
      t.mastery = x.mastery;
      t.attempts = x.attempts;
      t.errors = x.errors;
      t.mistakes = x.mistakes || [];
      t.explainDone = !!x.explain_done;
      t.explainRuns = x.explain_runs || 0;
      t.failStreak = x.fail_streak || 0;
      t.needHelp = !!x.need_help;
      t.pretested = !!x.pretested;
    });
    const key = agentKeyOf(ref);
    if (!state.agent) state.agent = { policy: null, plans: {}, error: null };
    if (!state.agent.plans) state.agent.plans = {};
    if (payload.policy) state.agent.policy = payload.policy;
    state.agent.error = null;
    if (key) state.agent.plans[key] = payload;
    state.hasPreTest = state.topics.length > 0 && state.topics.every(t => t.pretested);
    save();
    return payload;
  }

  function agentError(message) {
    if (!state.agent) state.agent = { policy: null, plans: {}, error: null };
    state.agent.error = message || 'Agent 未连接';
  }

  /* 问 agent「下一步做什么」。同一资料并发请求会复用同一个 promise。 */
  async function syncAgent(ref) {
    const rec = findMaterial(ref);
    const key = agentKeyOf(rec);
    if (!key) return null;
    if (inflight.has(key)) return inflight.get(key);
    if (!topicsOf(rec).length) return null;

    const run = (async () => {
      const r = await API.agentPlan(key, agentTopics(rec));
      if (!r || !r.ok) {
        agentError(r && r.error);
        save();
        return null;
      }
      return applySnapshot(rec, r.data);
    })().finally(() => inflight.delete(key));

    inflight.set(key, run);
    return run;
  }

  /* 单个知识点的推荐活动（知识点列表 / 详情页的「推荐」徽标） */
  function plan(topicId) {
    const t = topic(topicId);
    if (!t) return null;
    const rec = materialOfTopic(t);
    const snap = agentSnapshot(rec);
    const d = snap && snap.by_topic ? snap.by_topic[topicId] : null;
    if (!d) return null;
    return {
      topic: t,
      activity: d.activity ? (DATA.activities[d.activity] || null) : null,
      rule: d.rule,
      reason: d.reason,
      mastered: !!d.mastered,
      blocked: !!d.blocked,
    };
  }

  /* =========================================================
     滚动学习（Rolling Plan）—— agent 排好的本轮路径
     ---------------------------------------------------------
     排序与轮次推进都由 policy.plan_material() / advance_round() 在服务端
     完成（一轮里每个未掌握知识点各排一次，本轮走完再进入下一轮）。
     前端只把快照翻译成界面需要的形状：topic / activity 对象、blocked 列表。
     ========================================================= */

  function stageOf(t) {
    if (!t) return 'none';
    if (!t.pretested) return 'pretest';
    const rec = materialOfTopic(t);
    const snap = agentSnapshot(rec);
    const d = snap && snap.by_topic ? snap.by_topic[t.id] : null;
    if (!d) return 'none';
    /* 服务端改为下发「事实状态」（pretest / ready / done / blocked）：
       「下一步做什么」由模型决定，代码不再为每个知识点猜一个活动。
       ready 表示可以排，但没被模型选中，因此不显示活动徽标。 */
    if (d.status) return d.status;
    if (d.mastered || (!d.activity && !d.blocked)) return 'done';
    if (d.blocked) return 'blocked';
    if (d.activity === 'explain' && t.needHelp) return 'help';
    return d.activity;
  }

  /* 一份资料的滚动计划：把 agent 快照翻译成界面需要的形状。
     agent 还没返回时返回 pendingAgent，界面会提示「正在询问 Agent」，
     而不是本地猜一个下一步。 */
  function rollPlan(arg, opts) {
    const ts = pathTopics(arg);
    const snap = agentSnapshot(arg);
    if (!snap) {
      return {
        round: 1,
        total: ts.length,
        mastered: ts.filter(t => isMastered(t)).length,
        remaining: 0,
        pendingPretest: ts.filter(t => !t.pretested).length,
        visited: 0, steps: [], more: 0, next: null,
        lastRoundDone: false, blocked: [], done: false,
        pendingAgent: true,
        policy: null,
        error: (state.agent && state.agent.error) || null,
      };
    }

    /* 服务端给出了「无决策」标记：模型没有选出下一步，且不会退回规则。
       不能本地猜一个，也不能显示空队列；把理由带上去让界面说清楚。 */
    if (snap.agent_unavailable) {
      return {
        round: snap.round || 1,
        total: snap.total == null ? ts.length : snap.total,
        mastered: snap.mastered || 0,
        remaining: snap.remaining || 0,
        pendingPretest: snap.pending_pretest || 0,
        visited: snap.visited || 0, steps: [], more: 0, next: null,
        lastRoundDone: false, blocked: [], done: false,
        pendingAgent: true,
        agentReason: snap.unavailable_reason || 'unavailable',
        /* 每次尝试的模型与失败原因（服务端只给 model + reason，不含提示词），
           用来把「限流」和「回复不是 JSON」分开说，而不是一句笼统的重试。 */
        agentAttempts: (snap.decision && snap.decision.attempts) || [],
        policy: snap.policy || null,
        error: null,
      };
    }

    const byId = id => ts.find(t => t.id === id) || null;
    const steps = (snap.steps || []).map(s => {
      const t = byId(s.topic_id);
      if (!t) return null;
      return {
        topic: t,
        activity: DATA.activities[s.activity] || null,
        rule: s.rule,
        reason: s.reason,
        stage: s.stage,
        escalated: !!s.escalated,
        nextRound: !!s.next_round,
      };
    }).filter(Boolean);

    const limit = opts && opts.limit;
    const listed = limit ? steps.slice(0, limit) : steps;

    return {
      round: snap.round || 1,
      total: snap.total == null ? ts.length : snap.total,
      mastered: snap.mastered || 0,
      remaining: snap.remaining || 0,
      pendingPretest: snap.pending_pretest || 0,
      visited: snap.visited || 0,
      steps: listed,
      more: Math.max(0, steps.length - listed.length),
      next: steps[0] || null,
      lastRoundDone: !!snap.last_round_done,
      blocked: (snap.blocked || []).map(byId).filter(Boolean),
      done: !!snap.done,
      pendingAgent: false,
      policy: snap.policy || (state.agent && state.agent.policy) || null,
      error: null,
    };
  }

  /* ---------------- 活动完成 → 交给 Agent 更新状态 ----------------
     掌握度公式与错题本的维护现在唯一存在于 src/agent/policy.py::apply_result；
     前端只把逐题结果打包发给 /api/agent/observe，再把权威状态合并回来。
     返回结构与旧版保持一致：{ topic, before, after, delta, note, re }，
     这样结果页（Re-plan 卡片）不需要改渲染逻辑。 */
  async function applyResult(topicId, actId, outcome) {
    const t = topic(topicId);
    if (!t) return null;
    const out = outcome || {};
    const rec = materialOfTopic(t);
    const key = agentKeyOf(rec);
    const answers = Array.isArray(out.answers) ? out.answers : null;
    const total = Number.isFinite(out.total) ? out.total
      : (answers ? answers.length : quizBank(t).length);
    const correct = Number.isFinite(out.correct) ? out.correct
      : (out.quizCorrect === true ? total : 0);

    if (!key) {
      agentError('该知识点没有关联资料，Agent 无法记录');
      return { topic: t, before: t.mastery, after: t.mastery, delta: 0, note: '未关联资料，本次未计分', re: plan(topicId), failed: true };
    }

    const r = await API.agentObserve(key, {
      topic_id: topicId,
      activity: actId,
      outcome: {
        total,
        correct,
        answers: answers || undefined,
      },
      // 错题本需要题目原文与选项：只有练习需要
      quiz: actId === 'practice' ? quizBank(t) : [],
      topics: agentTopics(rec),
    });

    if (!r || !r.ok) {
      agentError(r && r.error);
      save();
      return {
        topic: t, before: t.mastery, after: t.mastery, delta: 0,
        note: 'Agent 未连接，本次未计入掌握度', re: plan(topicId), failed: true,
      };
    }

    applySnapshot(rec, r.data);
    const res = (r.data && r.data.result) || {};
    const before = Number.isFinite(res.before) ? res.before : t.mastery;
    const after = Number.isFinite(res.after) ? res.after : t.mastery;
    const delta = Number.isFinite(res.delta) ? res.delta : (after - before);

    state.completed += 1;
    state.lastActivity = { topicId, actId, delta: delta, ts: Date.now() };
    state.trace.unshift({
      ts: Date.now(),
      topicId, topicName: t.name,
      actId, actName: DATA.activities[actId] ? DATA.activities[actId].name : actId,
      delta: delta, before: before, after: after,
      ok: delta >= 0,
    });
    if (state.trace.length > 30) state.trace.pop();
    save();

    return {
      topic: t,
      before: before,
      after: after,
      delta: delta,
      note: res.note || '',
      re: plan(topicId),
    };
  }

  /* ---------------- Pre-test（按资料独立评估） ----------------
     answers: [{ topicId, correct: true | false | null }]
     correct === null 表示该知识点没有评估题、学生跳过了：只标记「已评估」，
     **不改掌握度**——否则「没考过」会被当成「答错」打成 20%，而跳过的题
     不标记则会让 pretestDone 永远为 false，整条学习路径卡在第一步。
     Pre-test 只确定起点，不算「错题」：答错记的是 20% 的起点分，
     不应该让学生一进来就被要求去复习错题本（那时还没有错题卡）。 */
  function applyPreTest(answers) {
    /* Pre-test 的起点分（45 / 20）与「跳过不改分」也由 Agent 决定，
       这里按资料分组发过去，再把权威状态合并回来。按资料分组是因为
       一次 Pre-test 只覆盖一份资料，而 agent 的接口是按资料划分的。 */
    const groups = new Map();
    (answers || []).forEach(a => {
      const t = topic(a.topicId);
      if (!t) return;
      const rec = materialOfTopic(t);
      const key = agentKeyOf(rec);
      if (!key) return;
      if (!groups.has(key)) groups.set(key, { rec: rec, answers: [] });
      groups.get(key).answers.push({
        topic_id: a.topicId,
        correct: (a.correct === null || typeof a.correct === 'undefined') ? null : !!a.correct,
      });
    });

    if (!groups.size) { save(); return Promise.resolve(null); }

    return Promise.all([...groups.entries()].map(async ([key, group]) => {
      const r = await API.agentPreTest(key, {
        answers: group.answers,
        topics: agentTopics(group.rec),
      });
      if (!r || !r.ok) { agentError(r && r.error); return null; }
      return applySnapshot(group.rec, r.data);
    })).then(results => {
      state.loopStage = 'plan';
      save();
      return results;
    });
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
        icon: 'bookOpen',   // 统一语义图标名：所有知识点共用一种图标，不再按序号轮换彩色 emoji
        desc: p.desc || '',
        mastery: 30,
        attempts: 0,
        errors: 0,
        pretested: false,
        explainDone: false,
        explainRuns: 0,
        mistakes: [],
        failStreak: 0,
        needHelp: false,
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
        names: added.slice(0, 5).map(t => t.name),
      };
    }
    save();
    return added;
  }

  /* 归一化题库：丢弃「无法作答」的题（没有题干 / 选项不足 2 个 / 正确答案下标非法）。
     以前这类题会原样进入界面，学生怎么选都判错，掌握度也被判成「一直答错」。 */
  function normalizeQuiz(list) {
    return (list || []).map(q => {
      if (!q || typeof q === 'string') return null;
      const raw = q.opts || q.options || q.choices || [];
      const opts = Array.isArray(raw)
        ? raw.slice()
        : 'ABCD'.split('').map(k => raw[k]).filter(v => v !== undefined && v !== null);
      let a = -1;
      if (typeof q.a === 'number') a = q.a;
      else if (typeof q.a === 'string') {
        const s = q.a.trim().toUpperCase();
        a = /^\d+$/.test(s) ? Number(s) : 'ABCD'.indexOf(s);
      } else if (q.correct_option !== undefined && q.correct_option !== null) {
        const s = String(q.correct_option).trim().toUpperCase();
        a = /^\d+$/.test(s) ? Number(s) : 'ABCD'.indexOf(s);
      }
      return {
        q: q.q || q.question || q.prompt || '',
        opts: opts.map(o => String(o)),
        a,
        why: q.why || q.explanation || '',
      };
    }).filter(q => q && q.q && q.opts.length >= 2 && q.a >= 0 && q.a < q.opts.length);
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
    // 删除资料必须连带清掉它的所有派生数据：知识点、学习轨迹，以及
    // 「已解析出 N 个知识点」这类提示，避免界面里留下无主内容。
    const rec = state.uploads.find(x => x.id === id);
    if (rec) {
      const keys = [rec.materialId, rec.id].filter(Boolean);
      const gone = new Set(
        state.topics
          .filter(t => t._materialId && keys.indexOf(t._materialId) !== -1)
          .map(t => t.id)
      );
      state.topics = state.topics.filter(t => !gone.has(t.id));
      state.trace = state.trace.filter(x => !gone.has(x.topicId));
      if (state.lastAppend && keys.indexOf(state.lastAppend.materialId) !== -1) {
        state.lastAppend = null;
      }
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
    plan, applyResult, applyPreTest, pretestDone,
    rollPlan, stageOf,
    // Agent：唯一决策来源（/api/agent/*），前端不再自带规则
    syncAgent, agentSnapshot, agentError, agentTopics,
    isMastered, masteredThreshold, masteryGoal, quizBank, explainPages, reviewCards, mistakesOf,
    setStage, stage, setRole, role,
    published, pathTopics, pathGroups, pendingTopics,
    dismissAppend,
    uploads, addUpload, markUpload, removeUpload, ingestParsed,
    // 资料维度
    materialKey, findMaterial, activeMaterial, selectMaterial,
    topicsOf, materialStats, materialOfTopic,
  };
})();
