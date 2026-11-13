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
      rolls: {},                    // 各资料的滚动学习轮次：{ [materialKey]: { round, visited[] } }
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
    /* 滚动学习的轮次状态：按资料分别记录，旧数据补一个空表 */
    if (!state.rolls || typeof state.rolls !== 'object' || Array.isArray(state.rolls)) {
      state.rolls = {};
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
     MASTERED：达到 80% 且没有未纠正的错题，才算「学会了」。到这一步就不再推荐活动。
     EXPLAIN_CAP：只看讲解最多到 55%。讲解没有任何检核，不能单独把掌握度刷满。 */
  const MASTERED = 80;
  const EXPLAIN_CAP = 55;

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

  function isMastered(t) {
    return !!t && t.mastery >= MASTERED && mistakesOf(t).length === 0 && !(t.errors > 0);
  }

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

  /* 规划一条真正有终点的路径：
       连续失败 → 重学讲解（explain）
       错题 ≥ 2 道 → 错题复习（review）
       没看过讲解 → 学讲解（explain，一次）
       有题可做 → 练习（practice，按正确率结算）
       已掌握 / 没有可学内容 → 不再推荐（activity 为 null）
     不再用「mastery < 45 / < 80」这种只看分数的阈值来决定活动类型。 */
  function plan(topicId) {
    const t = topic(topicId);
    if (!t) return null;

    const wrong = mistakesOf(t).length;
    const pages = explainPages(t);
    const bank = quizBank(t);

    if (isMastered(t)) {
      return {
        topic: t, activity: null, mastered: true,
        rule: `≥ ${MASTERED}% 且无错题`,
        reason: `掌握度 ${t.mastery}%，错题已全部纠正，这个知识点已经完成。`,
      };
    }

    /* 连续两次练习正确率不足一半：说明讲解没吃透，先重学再看错题，
       否则会在「练习→错题复习→再练习」之间原地打转。 */
    if (t.needHelp && pages.length) {
      return {
        topic: t,
        activity: DATA.activities.explain,
        rule: `连续 ${t.failStreak} 次练习未达标`,
        reason: `最近 ${t.failStreak} 次练习正确率都不足一半，先回到讲解把概念重新过一遍，再回来做题。`,
      };
    }

    if (wrong >= 2) {
      return {
        topic: t,
        activity: DATA.activities.review,
        rule: `错题本还有 ${wrong} 道题`,
        reason: `练习中有 <b>${wrong} 道题</b>还没答对，先用错题复习纠正概念，再回到练习。`,
      };
    }

    if (!t.explainDone && pages.length) {
      return {
        topic: t,
        activity: DATA.activities.explain,
        rule: '尚未学习讲解',
        reason: `还没看过这个知识点的讲解（${pages.length} 页），先建立概念再练习。`,
      };
    }

    if (bank.length) {
      return {
        topic: t,
        activity: DATA.activities.practice,
        rule: `掌握度 ${t.mastery}% < ${MASTERED}%`,
        reason: `掌握度 ${t.mastery}%，用练习检验是否真的掌握：答对越多提升越快，答错会回落。`,
      };
    }

    return {
      topic: t, activity: null, blocked: true,
      rule: '没有可评估的内容',
      reason: pages.length
        ? `这份资料只为它生成了讲解、没有测验题，读完讲解后无法继续评估掌握度。`
        : `解析结果里既没有讲解也没有测验题，这个知识点暂时无法学习。`,
    };
  }

  /* 只在「已完成 Pre-test 且尚未掌握」的知识点里选：先补最薄弱的
     （mastery 升序），同分时错题多的优先。不能让 95% 但有 1 道旧错题
     的知识点压过 30% 的新知识点。
     连续失败 3 次的知识点会被排到后面（暂时换一个知识点），
     避免整条路径卡在同一个学不会的知识点上。 */
  function nextTopic(list) {
    const arr = pathTopics(list).filter(t => t && t.pretested && !isMastered(t));
    const key = t => t.mastery + ((t.failStreak || 0) >= 3 ? 100 : 0);
    const sorted = [...arr].sort((a, b) => {
      const d = key(a) - key(b);
      if (d !== 0) return d;
      return mistakesOf(b).length - mistakesOf(a).length;
    });
    return sorted[0] || null;
  }

  /* 传入资料 rec/id 时，只在该资料范围内推荐下一步 */
  function nextPlan(arg) {
    const t = nextTopic(arg);
    return t ? plan(t.id) : null;
  }

  /* =========================================================
     滚动学习（Rolling Plan）—— 一份资料的默认路径
     ---------------------------------------------------------
     不再「一个知识点学到掌握再换下一个」，而是按轮次把整份资料的
     知识点一起推进：
       第 r 轮 = 所有「已评估且未掌握」的知识点，每个各安排 1 个活动
       一轮里某个知识点只出现一次（做完就记入 visited）
       本轮全部走过 → 轮次 +1，按最新掌握度重新排下一轮
     活动类型仍由 plan(topicId) 决定：连续失败→重学讲解、错题≥2→复习、
     没学讲解→讲解、其余→练习。

     这是「默认」路径：界面只暴露 rollPlan().next 一个动作，
     不列本轮队列、不让学生在多个知识点之间挑先后（那属于学生自主决策，
     由下面的单知识点路径承担）。
     单知识点路径保留：知识点详情页 / 知识点卡片里的「开始学习」
     仍然直接调用 plan() + start()，随时可以只学一个知识点。
     ========================================================= */

  const STAGE_ORDER = { help: 0, review: 1, explain: 2, practice: 3 };

  /* 这个知识点此刻处在哪一步（用于排序与展示） */
  function stageOf(t) {
    if (!t) return 'none';
    if (!t.pretested) return 'pretest';
    if (isMastered(t)) return 'done';
    const p = plan(t.id);
    if (!p) return 'none';
    if (p.blocked) return 'blocked';
    if (!p.activity) return 'done';
    if (p.activity.id === 'explain' && t.needHelp) return 'help';
    return p.activity.id;
  }

  function rollKey(ref) {
    const rec = findMaterial(ref);
    if (rec) return String(rec.materialId || rec.id);
    return typeof ref === 'string' ? ref : 'all';
  }

  /* 取（并按需初始化）某份资料的轮次状态 */
  function rollOf(ref) {
    if (!state.rolls || typeof state.rolls !== 'object') state.rolls = {};
    const key = rollKey(ref);
    let r = state.rolls[key];
    if (!r || typeof r !== 'object') {
      r = state.rolls[key] = { round: 1, visited: [], startedAt: Date.now() };
    }
    if (!Number.isFinite(r.round) || r.round < 1) r.round = 1;
    if (!Array.isArray(r.visited)) r.visited = [];
    return r;
  }

  /* 只返回「能被推荐」的知识点（已评估、未掌握、有下一步活动） */
  function rollCandidates(ts) {
    return ts.filter(t => t.pretested && !isMastered(t));
  }

  /* 一份资料的滚动计划：
       { round, total, mastered, remaining, pendingPretest,
         steps: [{ topic, activity, rule, reason, stage, nextRound, order }],
         next, blocked, done }
     steps 已按「本轮还没轮到的 → 阶段 → 掌握度」排好序。 */
  function rollPlan(arg, opts) {
    const ts = pathTopics(arg);
    const rs = rollOf(arg);
    const visited = rs.visited || [];

    const pendingPretest = ts.filter(t => !t.pretested).length;
    const cands = rollCandidates(ts);

    const steps = [];
    cands.forEach(t => {
      const p = plan(t.id);
      if (!p || !p.activity) return;
      const stage = stageOf(t);
      steps.push({
        topic: t,
        activity: p.activity,
        rule: p.rule,
        reason: p.reason,
        stage: stage,
        nextRound: visited.indexOf(t.id) !== -1,
        order: [
          STAGE_ORDER[stage] == null ? 9 : STAGE_ORDER[stage],
          t.mastery,
          -mistakesOf(t).length,
          ts.indexOf(t),          // 最终按资料里的原始顺序，读起来符合解析顺序
        ],
      });
    });

    steps.sort((a, b) => {
      /* 本轮还没轮到的优先；同组内先处理最紧急（重学 > 错题 > 讲解 > 练习） */
      if (a.nextRound !== b.nextRound) return a.nextRound ? 1 : -1;
      for (let i = 0; i < a.order.length; i++) {
        if (a.order[i] !== b.order[i]) return a.order[i] - b.order[i];
      }
      return 0;
    });

    const limit = opts && opts.limit;
    const listed = limit ? steps.slice(0, limit) : steps;

    return {
      round: rs.round,
      total: ts.length,
      mastered: ts.filter(t => isMastered(t)).length,
      remaining: cands.length,
      pendingPretest,
      visited: visited.filter(id => cands.some(t => t.id === id)).length,
      steps: listed,
      more: Math.max(0, steps.length - listed.length),
      next: steps[0] || null,
      lastRoundDone: steps.length > 0 && steps.every(s => s.nextRound),
      blocked: ts.filter(t => stageOf(t) === 'blocked'),
      done: ts.length > 0 && pendingPretest === 0 && cands.length === 0,
    };
  }

  /* 一次活动完成后推进轮次：本轮每个知识点只排一次，全部走过就进入下一轮 */
  function noteRoll(t) {
    const rec = materialOfTopic(t);
    if (!rec) return;
    const rs = rollOf(rec);
    if (rs.visited.indexOf(t.id) === -1) rs.visited.push(t.id);
    const cands = rollCandidates(topicsOf(rec));
    if (!cands.length) {
      rs.visited = [];              // 全部掌握：轮次停在当前，不再累加
      return;
    }
    const allVisited = cands.every(x => rs.visited.indexOf(x.id) !== -1);
    if (allVisited) {
      rs.round = (rs.round || 1) + 1;
      rs.visited = [];
    }
  }

  /* ---------------- 活动完成 → 状态更新 ----------------
     分数跟着「表现」走，不跟着「次数」走：
       explain  首次完成 +10（最高到 55%）；重学 +2；再往后 0
       practice delta = round((正确率 × 100 − 当前掌握度) × 0.6)，限制在 [−10, +25]
                —— 全对会快速上升，半对停在 50% 附近，全错会掉分
       review   只清错题、不涨分（翻看错题不等于学会，由练习判定）
     没有内容可学的活动一律 0 分，不再出现「点一下完成就加分」。 */
  function applyResult(topicId, actId, outcome) {
    const t = topic(topicId);
    if (!t) return null;

    const before = t.mastery;
    let delta = 0;
    let note = '';

    switch (actId) {
      case 'explain': {
        const pages = explainPages(t);
        if (!pages.length) {
          delta = 0;
          note = '该知识点没有讲解内容，未计分';
          break;
        }
        /* 讲解只给一次学分：首次 +10，重学 +2，再往后 0 分。
           否则「反复点完成讲解」就能把掌握度一点点堆上去。 */
        const runs = Number.isFinite(t.explainRuns) ? t.explainRuns : (t.explainDone ? 1 : 0);
        delta = runs === 0 ? 10 : (runs === 1 ? 2 : 0);
        note = runs === 0 ? '完成首次讲解学习'
          : (runs === 1 ? '重看讲解，只做少量巩固' : '再次重学讲解，不再计分');
        t.explainRuns = runs + 1;
        t.explainDone = true;
        t.needHelp = false;          // 已经重学过，把下一次机会还给练习
        break;
      }
      case 'practice': {
        const answers = Array.isArray(outcome.answers) ? outcome.answers : null;
        const total = Number.isFinite(outcome.total) ? outcome.total
          : (answers ? answers.length : quizBank(t).length);
        const correct = Number.isFinite(outcome.correct) ? outcome.correct
          : (outcome.quizCorrect === true ? total : 0);

        if (!total) {
          delta = 0;
          note = '没有可作答的题目，未计分';
          break;
        }

        const score = correct / total;
        /* 掌握度向「本次正确率」靠近：答对越多涨得越快，正确率低于当前
           掌握度时反而回落（例如一直答错不会涨到 100%）。 */
        const raw = Math.round((score * 100 - before) * 0.6);
        delta = Math.max(-10, Math.min(25, raw));

        if (score >= 0.6) {
          t.failStreak = 0;
          t.needHelp = false;
        } else if (score < 0.5) {
          t.failStreak = (t.failStreak || 0) + 1;
          if (t.failStreak >= 2) t.needHelp = true;
        }

        /* 错题本：答错的进、答对的出。这样「复习」纠正的正是练错的题，
           而不是讲解页里的 tip。 */
        if (answers) {
          const list = quizBank(t);
          const bucket = mistakesOf(t).slice();
          answers.forEach(a => {
            const q = list[a && a.i];
            if (!q || !q.q) return;
            const key = String(q.q).slice(0, 160);
            const at = bucket.findIndex(m => m.q === key);
            if (a.ok && at !== -1) bucket.splice(at, 1);
            if (!a.ok && at === -1) {
              bucket.push({ q: key, opts: q.opts || [], a: q.a, why: q.why || '' });
            }
          });
          t.mistakes = bucket;
        } else if (score < 0.8) {
          /* 旧调用方没传逐题结果时，至少保证有错题信号可复习 */
          const bucket = mistakesOf(t).slice();
          quizBank(t).forEach(q => {
            const key = String(q.q).slice(0, 160);
            if (!bucket.some(m => m.q === key)) {
              bucket.push({ q: key, opts: q.opts || [], a: q.a, why: q.why || '' });
            }
          });
          t.mistakes = bucket;
        } else {
          t.mistakes = [];
        }

        note = `答对 ${correct} / ${total} 题`;
        break;
      }
      case 'review': {
        const cards = mistakesOf(t);
        if (!cards.length) {
          delta = 0;
          note = '没有待纠正的错题，未计分';
          break;
        }
        /* 复习不计掌握度：翻看错题不等于学会，是否真的掌握由随后的练习判定。
           它只负责把错题清掉，让路径回到练习。 */
        delta = 0;
        t.mistakes = [];
        note = `已重新讲解 ${cards.length} 道错题，回到练习验证`;
        break;
      }
      default:
        delta = 0;
        note = '未知活动，未计分';
    }

    /* 讲解没有检核，不能把掌握度推高过 55%；但也不能把已掌握的知识点往下拉 */
    const capped = actId === 'explain'
      ? Math.max(before, Math.min(EXPLAIN_CAP, before + delta))
      : before + delta;
    t.mastery = Math.max(0, Math.min(100, capped));

    const after = t.mastery;
    const realDelta = after - before;      // 以真实变化为准：100% 时不再显示 +7

    t.errors = mistakesOf(t).length;
    t.attempts += 1;
    t.lastActivity = DATA.activities[actId] ? DATA.activities[actId].name : actId;

    state.completed += 1;
    state.lastActivity = { topicId, actId, delta: realDelta, ts: Date.now() };

    state.trace.unshift({
      ts: Date.now(),
      topicId, topicName: t.name,
      actId, actName: DATA.activities[actId] ? DATA.activities[actId].name : actId,
      delta: realDelta, before, after,
      ok: realDelta >= 0,
    });
    if (state.trace.length > 30) state.trace.pop();

    /* 滚动学习：本次活动的知识点记入本轮；本轮走完自动进入下一轮 */
    noteRoll(t);

    const re = plan(topicId);
    save();
    return { topic: t, before, after, delta: realDelta, note, re };
  }

  /* ---------------- Pre-test（按资料独立评估） ----------------
     answers: [{ topicId, correct: true | false | null }]
     correct === null 表示该知识点没有评估题、学生跳过了：只标记「已评估」，
     **不改掌握度**——否则「没考过」会被当成「答错」打成 20%，而跳过的题
     不标记则会让 pretestDone 永远为 false，整条学习路径卡在第一步。
     Pre-test 只确定起点，不算「错题」：答错记的是 20% 的起点分，
     不应该让学生一进来就被要求去复习错题本（那时还没有错题卡）。 */
  function applyPreTest(answers) {
    (answers || []).forEach(a => {
      const t = topic(a.topicId);
      if (!t) return;
      if (a.correct === null || typeof a.correct === 'undefined') {
        t.pretested = true;          // 保留解析初值（30%），只是不再挡路
        return;
      }
      t.mastery = a.correct ? 45 : 20;
      t.attempts += 1;
      t.errors = 0;
      t.mistakes = [];
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
    rollPlan, rollOf, stageOf,
    isMastered, quizBank, explainPages, reviewCards, mistakesOf,
    MASTERED, EXPLAIN_CAP,
    setStage, stage, setRole, role,
    published, pathTopics, pathGroups, pendingTopics,
    dismissAppend,
    uploads, addUpload, markUpload, removeUpload, ingestParsed,
    // 资料维度
    materialKey, findMaterial, activeMaterial, selectMaterial,
    topicsOf, materialStats, materialOfTopic,
  };
})();
