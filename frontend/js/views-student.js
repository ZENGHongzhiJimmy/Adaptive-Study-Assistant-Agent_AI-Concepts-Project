/* ============================================================
   views-student.js — 学生端视图
   页面结构：
   1) 学习主页 —— 平台功能 + 自主学习规划 + 按「课程资料文件」分类的模块入口
   2) 资料模块（选中某份资料后）—— 该资料的自主学习路径（Pre-test → 推荐活动 → 进度）
      + 课件解析后的知识点（可展开查看讲解与测验）
   3) 课程资料 · 问答 —— 自主上传资料，AI 解析生成 quiz + 问答
   4) 学习进度 —— 各知识点掌握度与学习轨迹，可按资料切换
   学生可做的活动：知识点讲解 / 练习测验 / 知识复习

   设计约定：知识点不全局堆叠。所有知识点、Pre-test、推荐活动、学习记录
   都归属于某一份上传的课程资料，只有进入该资料模块后才展示。
   ============================================================ */

const Student = (() => {

  const I = UI.icon;

  /* ================= 活动运行状态 ================= */
  let A = null;   // 当前活动状态
  let M = null;   // 问答状态
  let P = null;   // Pre-test 状态

  /* 返回到知识点所属的资料模块（找不到时回学习主页） */
  function backHash(topicId) {
    const rec = Engine.materialOfTopic(Engine.topic(topicId));
    return rec ? '#/student/material/' + rec.id : '#/student/dashboard';
  }

  function startAct(type, topicId) {
    A = { type, topicId, result: null };

    if (type === 'explain') {
      A.slides = Engine.topicContent(topicId).explain || [];
      A.i = 0; A.asked = null;
    } else if (type === 'practice') {
      A.qs = Engine.topicContent(topicId).quiz || [];
      A.i = 0; A.sel = null; A.correct = 0;
    } else if (type === 'review') {
      A.cards = (Engine.topicContent(topicId).explain || []).map(s => ({ q: s.t, a: s.tip }));
      A.i = 0; A.flip = false;
    }

    Engine.setStage('act');
    App.go(`#/student/activity/${type}/${topicId}`);
  }

  function finish(outcome) {
    if (!A) return;
    const r = Engine.applyResult(A.topicId, A.type, outcome || {});
    A.result = r;
    Engine.setStage('replan');
    App.render();
  }

  /* ================= 通用片段 ================= */

  function fileIcon(kind) {
    return kind === 'PDF' ? '📕' : kind === 'PPT' ? '📊'
         : kind === 'MD' ? '📝' : kind === 'TXT' ? '📄' : '📄';
  }

  function statusBadge(m) {
    switch (m.status) {
      case 'uploading': return `<span class="badge badge-warning">${I('refresh',12)} 上传中…</span>`;
      case 'uploaded':  return `<span class="badge badge-muted">${I('file',12)} 已上传（未解析）</span>`;
      case 'parsing':   return `<span class="badge badge-warning">${I('refresh',12)} AI 解析中…</span>`;
      case 'error':     return `<span class="badge badge-danger" title="${escapeHtml(m.error || '')}">${I('bug',12)} 解析失败</span>`;
      case 'parsed':
      case 'indexed':   return `<span class="badge badge-success">${I('check',12)} 已解析</span>`;
      default:          return `<span class="badge badge-muted">${escapeHtml(m.status || '未处理')}</span>`;
    }
  }

  function stepsBar(items, cur) {
    return `<div class="steps">${items.map((s, i) => `
      <div class="step ${i < cur ? 'done' : (i === cur ? 'active' : '')}">
        <span class="step-n">${i + 1}</span>
        <div>
          <div class="step-t">${s[0]}</div>
          <div class="step-d">${s[1]}</div>
        </div>
      </div>`).join('<span class="step-arrow">›</span>')}</div>`;
  }

  /* 主页区块：标题条与内容合成一个整体卡片，避免标题孤立地贴在左上角 */
  function homeSection(icon, title, badge, hint, body) {
    return `
      <section class="card home-sec">
        <div class="card-head home-sec-head">
          <span class="home-sec-ico">${I(icon, 15)}</span>
          <span class="h-section">${title}</span>
          <span class="home-sec-rule"></span>
          ${hint ? `<span class="home-sec-hint">${hint}</span>` : ''}
          ${badge ? `<span class="badge badge-muted">${badge}</span>` : ''}
        </div>
        <div class="card-body home-sec-body">${body}</div>
      </section>`;
  }

  /* ================= 学习主页 =================
     只保留：平台功能、自主学习规划、按资料文件分类的模块入口。
     不再把全部知识点、推荐活动、学习记录堆在主页。 */
  function dashboard() {
    const mine = Engine.uploads();
    const parsed = mine.filter(m => m.status === 'parsed' || m.status === 'indexed' || Engine.materialStats(m).count > 0);
    const active = Engine.activeMaterial();
    const activeStats = active ? Engine.materialStats(active) : null;

    /* ① 自主学习规划（总览，不在此处开始 Pre-test） */
    const planSteps = [
      ['选择课程资料', '进入对应学习模块'],
      ['完成 Pre-test', '建立该资料的起点'],
      ['完成推荐活动', '讲解 / 练习 / 复习'],
      ['查看学习进度', '掌握度持续更新'],
    ];
    const cur = !active ? 0
      : !activeStats.pretestDone ? 1
      : (activeStats.trace.length ? 3 : 2);

    const activeHint = active
      ? `<div class="plan-current">
           <div>
             <div class="text-xs" style="color:var(--primary-600);font-weight:700">${I('check', 12)} 已选中 · 当前资料</div>
             <div style="font-weight:700;font-size:13.5px">${fileIcon(active.kind)} ${escapeHtml(active.name)}</div>
             <div class="text-xs" style="color:var(--text-3)">
               ${activeStats.count} 个知识点 · ${activeStats.pretestDone ? 'Pre-test 已完成' : `还有 ${activeStats.pending} 个知识点待 Pre-test`}
             </div>
           </div>
           <span class="spacer"></span>
           <button class="btn btn-primary btn-sm" onclick="Student.openMaterial('${active.id}')">
             进入该资料模块 ${I('arrowRight',14)}
           </button>
         </div>`
      : `<div class="plan-current">
           <div>
             <div style="font-weight:700;font-size:13.5px">先选择一份课程资料</div>
             <div class="text-xs" style="color:var(--text-3)">进入对应模块后，才会出现 Pre-test 与后续学习步骤。</div>
           </div>
         </div>`;

    const planCard = homeSection(
      'brain', '自主学习规划', `${planSteps.length} 步闭环`,
      'Plan → Act → Observe → Update → Re-plan',
      `${stepsBar(planSteps, cur)}${activeHint}`
    );

    /* ② 平台功能（展示时排在自主学习规划之前） */
    const FEATURES = [
      ['upload',    '上传与 AI 解析', 'PDF / TXT / MD 上传后，LLM 自动抽取知识点、讲解与测验题'],
      ['brain',     '自适应学习路径', '按掌握度与错误次数推荐讲解 / 练习 / 复习，一次只推一件事'],
      ['chat',      '课程资料 AI 问答', 'RAG 只依据你上传的资料作答，并给出可核对的引用出处'],
      ['chart',     '学习进度追踪', '掌握度、错误与学习轨迹按资料模块独立记录，随时回看'],
    ];
    const featureCards = FEATURES.map(f => `
      <div class="card overview-item">
        <div class="overview-ico">${I(f[0], 18)}</div>
        <div class="overview-t">${f[1]}</div>
        <div class="overview-d">${f[2]}</div>
      </div>`).join('');
    const overview = homeSection('layers', '平台功能', `${FEATURES.length} 项能力`, '',
      `<div class="grid g-4 overview-grid">${featureCards}</div>`);

    /* ③ 按课程资料文件分类的模块 */
    const cards = mine.map(m => materialCard(m)).join('');
    const addCard = `
      <button class="mat-card mat-add" onclick="App.go('#/student/materials')">
        <div class="mat-add-ico">${I('plus', 22)}</div>
        <div class="mat-add-t">上传/管理资料</div>
        <div class="mat-add-d">支持 PDF / TXT / MD<br>AI 解析后自动生成独立学习模块</div>
      </button>`;

    const append = Engine.state.lastAppend;
    const appendRec = append && append.materialId ? Engine.findMaterial(append.materialId) : null;
    /* 新解析消息：整条可点，点一次即跳转到对应模块并标记为已读（返回主页不再出现） */
    const appendBanner = append && append.count ? `
      <div class="notice notice-msg" role="button" tabindex="0"
           title="${appendRec ? '点击查看该资料的学习模块' : '点击关闭'}"
           onclick="${appendRec ? `Student.openMaterial('${appendRec.id}')` : 'Student.dismissAppend()'}"
           onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();this.click()}">
        <span class="notice-msg-ico">${I('sparkles', 16)}</span>
        <div style="flex:1">
          <div class="notice-t">已从「${escapeHtml(append.courseTitle)}」解析出 ${append.count} 个知识点</div>
          <div class="notice-d">${appendRec
            ? '已归入这份资料的学习模块，原有进度不变 · 点击此消息查看解析出的知识点与学习步骤'
            : '已归入这份资料的学习模块，原有进度不变 · 点击关闭'}</div>
        </div>
        <span class="notice-msg-arrow">${I('arrowRight', 16)}</span>
      </div>` : '';

    const emptyHint = !mine.length ? `
      <div class="notice">
        <span class="notice-ico">📚</span>
        <div style="flex:1">
          <div class="notice-t">开始你的自主学习</div>
          <div class="notice-d">上传第一份课程资料，AI 会解析出知识点与测验，并为它建立独立的学习模块。</div>
        </div>
        <button class="btn btn-primary" onclick="App.go('#/student/materials')">上传资料 ${I('arrowRight',15)}</button>
      </div>` : '';

    const materialSection = homeSection(
      'book', '我的课程资料',
      `${mine.length} 份资料 · ${parsed.length} 份已解析`,
      '点击卡片选中 · 点「进入学习模块」开始',
      `<div class="mat-grid">${cards}${addCard}</div>`
    );

    return `
      ${overview}
      ${planCard}
      ${emptyHint}
      ${appendBanner}
      ${materialSection}`;
  }

  /* 单个资料模块卡片（学习主页） */
  function materialCard(m) {
    const s = Engine.materialStats(m);
    const active = Engine.activeMaterial();
    const isActive = !!(active && active.id === m.id);
    // 只要已解析出知识点就能进入模块（兼容旧版本留在本地的资料状态）
    const ready = s.count > 0;

    const stats = ready ? `
      <div class="mat-stats">
        <div><b>${s.count}</b><span>知识点</span></div>
        <div><b>${s.explainCount}</b><span>讲解页</span></div>
        <div><b>${s.quizCount}</b><span>测验题</span></div>
      </div>
      <div class="mat-mastery">
        <div class="row mb-8">
          <span class="text-xs" style="color:var(--text-3)">平均掌握度</span>
          <span class="spacer"></span>
          <b style="font-size:12.5px">${s.avg}%</b>
        </div>
        ${UI.bar(s.avg)}
      </div>
      <div class="row row-wrap mt-8" style="gap:6px">
        ${s.pretestDone
          ? `<span class="badge badge-success">${I('check',12)} Pre-test 已完成</span>`
          : `<span class="badge badge-warning">待 Pre-test · ${s.pending} 个知识点</span>`}
      </div>` : `
      <div class="mat-placeholder">
        ${m.status === 'error'
          ? `解析失败：${escapeHtml(m.error || '未知错误')}。可在「课程资料 · 问答」移除后重新上传。`
          : m.status === 'parsed'
            ? '解析完成，但尚未生成知识点。可移除后重新上传。'
            : '资料尚未完成 AI 解析；解析完成后会自动生成知识点、讲解与测验。'}
      </div>`;

    return `
      <div class="mat-card ${isActive ? 'is-active' : ''}" data-mid="${m.id}"
           title="点击选中这份资料"
           onclick="Student.chooseMaterial('${m.id}')">
        <div class="mat-top">
          <div class="mat-ico">${fileIcon(m.kind)}</div>
          <div style="min-width:0;flex:1">
            <div class="mat-name" title="${escapeHtml(m.name)}">${escapeHtml(m.name)}</div>
            <div class="mat-meta">${m.kind} · ${m.size}${m.pages ? ' · ' + m.pages + ' 页' : ''}</div>
          </div>
          ${statusBadge(m)}
        </div>
        ${stats}
        <div class="mat-foot">
          ${isActive ? `<span class="badge badge-primary">${I('check',12)} 已选中</span>` : '<span></span>'}
          <button class="btn ${ready ? 'btn-primary' : 'btn-ghost'} btn-sm"
                  onclick="event.stopPropagation();Student.openMaterial('${m.id}')">
            ${ready ? '进入学习模块' : '查看资料'} ${I('arrowRight',13)}
          </button>
        </div>
      </div>`;
  }

  /* ================= 学习主页：点击卡片只选中，不跳转 ================= */
  function chooseMaterial(id) {
    const rec = Engine.findMaterial(id);
    if (!rec) { UI.toast('该资料不存在或已被移除', 'warn'); return; }
    if (Engine.activeMaterial() && Engine.activeMaterial().id === rec.id) return;
    // 保留滚动位置：整页重绘后停在原来的位置，不会跳回顶部
    const y = (typeof window !== 'undefined' && window.scrollY) || 0;
    Engine.selectMaterial(rec.id);
    App.render();
    if (typeof window !== 'undefined' && window.scrollTo) window.scrollTo(0, y);
  }

  /* ================= 资料模块（点击「进入学习模块」后） ================= */
  function openMaterial(id) {
    const rec = Engine.findMaterial(id);
    if (!rec) { UI.toast('该资料不存在或已被移除', 'warn'); App.go('#/student/dashboard'); return; }
    // 进入该资料模块即视为已读：新解析消息提示消失，返回主页也不会再出现
    const ap = Engine.state.lastAppend;
    if (ap && ap.materialId && (ap.materialId === rec.materialId || ap.materialId === rec.id)) {
      Engine.dismissAppend();
    }
    Engine.selectMaterial(rec.id);
    App.go('#/student/material/' + rec.id);
  }

  function materialView(id) {
    const rec = Engine.findMaterial(id);
    if (!rec) {
      return UI.empty('该资料不存在或已被移除，请回到学习主页重新选择', '📭') +
        `<div class="row mt-16"><span class="spacer"></span>
          <button class="btn btn-primary" onclick="App.go('#/student/dashboard')">回到学习主页</button></div>`;
    }
    if (!Engine.activeMaterial() || Engine.activeMaterial().id !== rec.id) Engine.selectMaterial(rec.id);
    const s = Engine.materialStats(rec);
    const ready = s.count > 0;   // 兼容旧版本资料状态：有知识点即可进入模块

    /* 头部 */
    const head = `
      <div class="runner-head">
        <button class="btn btn-ghost btn-sm" onclick="App.go('#/student/dashboard')">${I('arrowLeft',15)} 学习主页</button>
        <div style="min-width:0">
          <div class="runner-title">${fileIcon(rec.kind)} ${escapeHtml(rec.name)}</div>
          <div class="runner-topic">${rec.kind} · ${rec.size}${rec.pages ? ' · ' + rec.pages + ' 页' : ''} · ${rec.from || '我的上传'}</div>
        </div>
        <span class="spacer"></span>
        ${statusBadge(rec)}
        ${UI.ring(s.avg, 56)}
      </div>`;

    if (!ready) {
      return head + `
        <div class="card card-pad">
          ${UI.empty(rec.status === 'error'
            ? '该资料解析失败：' + escapeHtml(rec.error || '未知错误')
            : '该资料还没有解析出知识点', rec.status === 'error' ? '⚠️' : '📄')}
          <div class="text-sm mt-8" style="color:var(--text-2);text-align:center">
            请到「课程资料 · AI 问答」完成上传与解析，或移除后重新上传。
          </div>
          <div class="row mt-16" style="justify-content:center">
            <button class="btn btn-primary" onclick="App.go('#/student/materials')">${I('upload',14)} 前往课程资料</button>
          </div>
        </div>`;
    }

    /* ① 课件解析概览 */
    const parsedCard = `
      <div class="card mb-16">
        <div class="card-head">
          <span class="h-section">课件解析概览</span>
          <span class="spacer"></span>
          <span class="text-xs" style="color:var(--text-3)">由 AI 解析这份资料自动生成</span>
        </div>
        <div class="card-body">
          <div class="grid g-4">
            ${miniStat('知识点', s.count, 'book')}
            ${miniStat('讲解页', s.explainCount, 'layers')}
            ${miniStat('测验题', s.quizCount, 'target')}
            ${miniStat('平均掌握度', s.avg + '%', 'chart')}
          </div>
        </div>
      </div>`;

    /* ② 该资料的自主学习路径（选择资料后才显示 Pre-test 等步骤） */
    const pathSteps = [
      ['完成 Pre-test', '建立该资料的初始掌握度'],
      ['完成推荐活动', '讲解 / 练习 / 复习'],
      ['查看学习进度', '掌握度持续更新'],
    ];
    const cur = !s.pretestDone ? 0 : (s.trace.length ? 2 : 1);
    const pretestNotice = !s.pretestDone ? `
      <div class="notice">
        <span class="notice-ico">📝</span>
        <div style="flex:1">
          <div class="notice-t">先完成这份资料的 Pre-test</div>
          <div class="notice-d">共 ${s.pending} 道题（对应尚未评估的知识点），约 1–2 分钟；结果只影响本资料模块。</div>
        </div>
        <button class="btn btn-primary" onclick="Student.pretest('${rec.id}')">开始 Pre-test ${I('arrowRight',15)}</button>
      </div>` : `
      <div class="notice notice-ok">
        <span class="notice-ico">✅</span>
        <div style="flex:1">
          <div class="notice-t">Pre-test 已完成</div>
          <div class="notice-d">Agent 已为这 ${s.count} 个知识点建立起点，下面推荐下一步学习活动。</div>
        </div>
      </div>`;

    const plan = s.pretestDone ? Engine.nextPlan(rec) : null;
    const focus = plan ? `
      <div class="focus-card mt-16">
        <div class="focus-top">
          <div class="focus-ico">${plan.activity.icon}</div>
          <div class="focus-main">
            <div class="focus-label">ADAPTIVE PLANNING AGENT · 推荐下一步</div>
            <div class="focus-title">${plan.activity.name}</div>
            <div class="focus-meta">${plan.topic.icon} ${escapeHtml(plan.topic.name)} · 当前掌握度 ${plan.topic.mastery}%</div>
          </div>
          ${UI.ring(plan.topic.mastery, 68)}
        </div>
        <div class="focus-body">
          <div class="reason-line">${plan.reason}　规则 <span class="reason-rule">${plan.rule}</span></div>
          <div class="row mt-16">
            <button class="btn btn-primary" onclick="Student.start('${plan.activity.id}','${plan.topic.id}')">${I('play',15)} 开始学习</button>
            <button class="btn btn-ghost" onclick="Student.openTopic('${plan.topic.id}')">选择其他活动</button>
            <span class="spacer"></span>
            <button class="btn btn-ghost btn-sm" onclick="App.go('#/student/progress/${rec.id}')">查看本资料进度</button>
          </div>
        </div>
      </div>` : '';

    const pathCard = `
      <div class="card mb-16">
        <div class="card-head">
          <span class="h-section">自主学习路径 · ${escapeHtml(rec.name)}</span>
          <span class="spacer"></span>
          <span class="badge badge-primary">${s.count} 个知识点</span>
        </div>
        <div class="card-body">
          ${stepsBar(pathSteps, cur)}
          ${pretestNotice}
          ${focus}
        </div>
      </div>`;

    /* ③ 课件解析后的知识点（可展开查看讲解与测验） */
    const kpList = s.topics.map((t, i) => knowledgeItem(t, i)).join('');
    const kpCard = `
      <div class="card mb-16">
        <div class="card-head">
          <span class="h-section">课件解析后的知识点</span>
          <span class="badge badge-info">${s.count} 个</span>
          <span class="spacer"></span>
          <span class="text-xs" style="color:var(--text-3)">点击展开查看讲解要点与生成测验</span>
        </div>
        <div class="card-body kp-list">${kpList}</div>
      </div>`;

    /* ④ 该资料的学习记录 */
    const recent = s.trace.slice(0, 5).map(x => `
      <div class="topic-row">
        <div class="topic-emoji">${(Engine.topic(x.topicId) || {}).icon || '📘'}</div>
        <div class="topic-main">
          <div class="topic-name">${escapeHtml(x.topicName)} · ${x.actName}</div>
          <div class="topic-meta">掌握度 ${x.before}% → ${x.after}%</div>
        </div>
        <span class="badge badge-success">+${x.delta}%</span>
      </div>`).join('') || UI.empty('这份资料还没有学习记录，完成一个活动试试', '🌱');

    const recordCard = `
      <div class="card">
        <div class="card-head">
          <span class="h-section">本资料的学习记录</span>
          <span class="spacer"></span>
          <button class="btn btn-ghost btn-sm" onclick="App.go('#/student/progress/${rec.id}')">查看完整进度</button>
        </div>
        ${recent}
      </div>`;

    return head + parsedCard + pathCard + kpCard + recordCard;
  }

  function miniStat(label, val, ic) {
    return `
      <div class="mini-stat">
        <div class="mini-stat-ico">${I(ic, 16)}</div>
        <div class="mini-stat-v">${val}</div>
        <div class="mini-stat-l">${label}</div>
      </div>`;
  }

  /* 单个知识点：折叠面板展示解析内容 */
  function knowledgeItem(t, i) {
    const c = t.content || { explain: [], quiz: [], pretest: null };
    const recAct = Engine.plan(t.id);
    const actId = recAct ? recAct.activity.id : 'explain';
    const explain = (c.explain || []).map(s => `
      <div class="kp-slide">
        <div class="kp-slide-t">${escapeHtml(s.t || '讲解要点')}</div>
        ${(s.items || []).length
          ? `<ul class="kp-points">${(s.items || []).map(x => `<li>${escapeHtml(x)}</li>`).join('')}</ul>`
          : ''}
        ${s.tip ? `<div class="kp-tip"><b>要点：</b>${escapeHtml(s.tip)}</div>` : ''}
      </div>`).join('') || `<div class="text-xs" style="color:var(--text-3)">暂无讲解内容</div>`;

    const quiz = (c.quiz || []).map((q, qi) => `
      <div class="kp-quiz">
        <div class="kp-quiz-q">${qi + 1}. ${escapeHtml(q.q)}</div>
        ${(q.opts || []).map((o, oi) => `
          <div class="kp-quiz-opt ${oi === q.a ? 'is-answer' : ''}">
            <span class="kp-quiz-key">${'ABCD'[oi]}</span>${escapeHtml(o)}
            ${oi === q.a ? '<span class="kp-answer-tag">正确答案</span>' : ''}
          </div>`).join('')}
        ${q.why ? `<div class="kp-quiz-why">解析：${escapeHtml(q.why)}</div>` : ''}
      </div>`).join('') || `<div class="text-xs" style="color:var(--text-3)">暂无测验题</div>`;

    const lv = UI.level(t.mastery);
    return `
      <details class="kp-item">
        <summary class="kp-head">
          <span class="kp-idx">${i + 1}</span>
          <span class="kp-ico">${t.icon || '📘'}</span>
          <div class="kp-main">
            <div class="kp-name">${escapeHtml(t.name)}
              ${t.pretested ? '' : '<span class="badge badge-warning">待 Pre-test</span>'}
            </div>
            <div class="kp-desc">${escapeHtml(t.desc || '')}</div>
            <div class="kp-meta">讲解 ${(c.explain || []).length} 页 · 测验 ${(c.quiz || []).length} 题 · ${lv.label}</div>
          </div>
          <div class="kp-bar-wrap">${UI.bar(t.mastery)}<div class="kp-pct">${t.mastery}%</div></div>
          <span class="kp-toggle">查看解析 ▾</span>
        </summary>
        <div class="kp-body">
          <div class="kp-cols">
            <div class="kp-block">
              <div class="kp-sub">${I('book', 13)} 讲解要点</div>
              ${explain}
            </div>
            <div class="kp-block">
              <div class="kp-sub">${I('target', 13)} 生成测验</div>
              ${quiz}
            </div>
          </div>
          <div class="row mt-16">
            <span class="text-xs" style="color:var(--text-3)">来源：${escapeHtml(t.src || '我的上传资料')}</span>
            <span class="spacer"></span>
            <button class="btn btn-soft btn-sm" onclick="Student.openTopic('${t.id}')">查看知识点详情</button>
            <button class="btn btn-primary btn-sm" onclick="Student.start('${actId}','${t.id}')">
              ${I('play',13)} 开始学习
            </button>
          </div>
        </div>
      </details>`;
  }

  /* ================= 知识点详情（三种活动可选） ================= */
  function openTopic(topicId) {
    if (!Engine.topic(topicId)) { UI.toast('该知识点不存在', 'warn'); return; }
    A = null;
    App.go('#/student/topic/' + topicId);
  }

  function topicDetail(topicId) {
    const t = Engine.topic(topicId);
    if (!t) return UI.empty('该知识点不存在或已被移除', '📭') +
      `<div class="row mt-16"><span class="spacer"></span><button class="btn btn-primary" onclick="App.go('#/student/dashboard')">回到学习主页</button></div>`;
    const p = Engine.plan(topicId);
    const rec = Engine.materialOfTopic(t);
    const acts = Object.values(DATA.activities).map(a => `
      <button class="act-card ${a.id === p.activity.id ? 'recommended' : ''}" onclick="Student.start('${a.id}','${t.id}')">
        <div class="row" style="gap:8px">
          <span class="act-ico">${a.icon}</span>
          <span class="act-name">${a.name}</span>
          ${a.id === p.activity.id ? '<span class="badge badge-primary" style="margin-left:auto">推荐</span>' : ''}
        </div>
        <div class="act-desc">${a.desc}</div>
        <div class="act-ability">训练：${a.ability}</div>
      </button>`).join('');

    return `
      <div class="runner-head">
        <button class="btn btn-ghost btn-sm" onclick="App.go('${backHash(t.id)}')">${I('arrowLeft',15)} 返回资料模块</button>
        <div>
          <div class="runner-title">${t.icon} ${escapeHtml(t.name)}</div>
          <div class="runner-topic">${escapeHtml(t.desc || '')}</div>
        </div>
        <span class="spacer"></span>
        ${UI.ring(t.mastery, 56)}
      </div>

      ${rec ? `<div class="notice notice-soft">
        <span class="notice-ico">${fileIcon(rec.kind)}</span>
        <div style="flex:1"><div class="notice-d">所属课程资料：<b>${escapeHtml(rec.name)}</b>（学习状态只在本资料模块内记录）</div></div>
        <button class="btn btn-ghost btn-sm" onclick="Student.openMaterial('${rec.id}')">回到模块</button>
      </div>` : ''}

      <div class="notice">
        <span class="notice-ico">${I('brain',17)}</span>
        <div style="flex:1">
          <div class="notice-t">Agent 推荐：${p.activity.icon} ${p.activity.name}</div>
          <div class="notice-d">${p.reason}</div>
        </div>
      </div>

      <div class="section-head">
        <span class="h-section">选择学习活动（${Object.keys(DATA.activities).length} 种）</span>
        <span class="spacer"></span>
        <span class="text-xs" style="color:var(--text-3)">知识点来自你上传的资料</span>
      </div>
      <div class="act-grid">${acts}</div>`;
  }

  /* ================= Pre-test（按资料） ================= */
  function pretest(recId) {
    const rec = Engine.findMaterial(recId) || Engine.activeMaterial();
    if (!rec) {
      UI.toast('请先在学习主页选择一份课程资料', 'warn');
      App.go('#/student/dashboard');
      return;
    }
    const pending = Engine.pendingTopics(rec);
    if (!pending.length) { UI.toast('这份资料的 Pre-test 已完成', 'ok'); App.go('#/student/material/' + rec.id); return; }
    Engine.selectMaterial(rec.id);
    P = { i: 0, answers: [], sel: null, fb: null, recId: rec.id };
    App.go('#/student/pretest/' + rec.id);
  }

  function pretestView(recId) {
    const rec = Engine.findMaterial(recId || (P && P.recId)) || Engine.activeMaterial();
    if (!rec) {
      return UI.empty('请先在学习主页选择一份课程资料', '📚') +
        `<div class="row mt-16"><span class="spacer"></span>
          <button class="btn btn-primary" onclick="App.go('#/student/dashboard')">回到学习主页</button></div>`;
    }
    if (!P || P.recId !== rec.id) P = { i: 0, answers: [], sel: null, fb: null, recId: rec.id };

    const TS = Engine.pendingTopics(rec);
    const back = '#/student/material/' + rec.id;

    if (!TS.length) {
      return `
        <div class="runner">
          <div class="runner-head">
            <button class="btn btn-ghost btn-sm" onclick="App.go('${back}')">${I('arrowLeft',15)} 返回资料模块</button>
            <div><div class="runner-title">Pre-test · ${escapeHtml(rec.name)}</div></div>
          </div>
          ${UI.empty('这份资料的所有知识点都已完成起点评估', '✅')}
          <div class="row mt-16"><span class="spacer"></span>
            <button class="btn btn-primary" onclick="App.go('${back}')">查看我的学习路径</button></div>
        </div>`;
    }

    if (P.i >= TS.length) {
      const correct = P.answers.filter(a => a.correct).length;
      return `
        <div class="runner">
          <div class="card card-pad" style="text-align:center">
            <div style="font-size:40px">🎉</div>
            <div class="h-section mt-8" style="font-size:18px">Pre-test 完成</div>
            <div class="text-sm mt-8" style="color:var(--text-2)">
              「${escapeHtml(rec.name)}」答对 ${correct} / ${TS.length} 题。Agent 已为这 ${TS.length} 个知识点建立初始掌握度。
            </div>
            <div class="row mt-16" style="justify-content:center">
              <button class="btn btn-primary" onclick="Student.commitPretest()">查看我的学习路径</button>
            </div>
          </div>
        </div>`;
    }

    const t = TS[P.i];
    const q = Engine.topicContent(t.id).pretest || Engine.topicContent(t.id).quiz[0];
    if (!q || !q.q) return UI.empty('该知识点暂无评估题目，可先完成讲解', '🚧');
    const opts = (q.opts || []).map((o, i) => `
      <button class="opt ${P.sel === i ? 'selected' : ''} ${P.sel !== null ? (i === q.a ? 'correct' : (P.sel === i ? 'wrong' : '')) : ''}"
              ${P.sel !== null ? 'disabled' : `onclick="Student.pickPretest(${i})"`}>
        <span class="opt-key">${'ABCD'[i]}</span><span class="opt-label">${escapeHtml(o)}</span>
      </button>`).join('');

    const fb = P.sel !== null ? `
      <div class="feedback ${P.sel === q.a ? 'ok' : 'bad'}">
        <div class="feedback-title">${P.sel === q.a ? I('check',15) + ' 回答正确' : I('bug',15) + ' 回答错误'}</div>
        ${escapeHtml(q.why || '')}
      </div>
      <div class="row mt-16">
        <button class="btn btn-primary" onclick="Student.nextPretest()">${P.i === TS.length - 1 ? '查看结果' : '下一题'} ${I('arrowRight',15)}</button>
      </div>` : '';

    return `
      <div class="runner">
        <div class="runner-head">
          <button class="btn btn-ghost btn-sm" onclick="App.go('${back}')">${I('arrowLeft',15)} 返回资料模块</button>
          <div>
            <div class="runner-title">Pre-test · 起点评估</div>
            <div class="runner-topic">第 ${P.i + 1} / ${TS.length} 题 · ${t.icon} ${escapeHtml(t.name)}</div>
          </div>
          <span class="spacer"></span>
          <span class="badge badge-muted">${escapeHtml(rec.name)} · 本次评估 ${TS.length} 个知识点</span>
        </div>
        <div class="card card-pad">
          <div class="bar mb-16"><div class="bar-fill" style="width:${(P.i / TS.length) * 100}%"></div></div>
          <div class="q-text">${escapeHtml(q.q)}</div>
          ${opts}
          ${fb}
        </div>
      </div>`;
  }

  function pickPretest(i) { P.sel = i; App.render(); }

  function nextPretest() {
    const rec = Engine.findMaterial(P && P.recId);
    if (!rec) { App.go('#/student/dashboard'); return; }
    const TS = Engine.pendingTopics(rec);
    const t = TS[P.i];
    if (!t) return;
    const q = Engine.topicContent(t.id).pretest || Engine.topicContent(t.id).quiz[0];
    if (!q || !q.q) { P.i++; P.sel = null; App.render(); return; }
    P.answers.push({ topicId: t.id, correct: P.sel === q.a });
    P.i++; P.sel = null;
    App.render();
  }

  function commitPretest() {
    const rec = Engine.findMaterial(P && P.recId);
    Engine.applyPreTest(P.answers);
    P = null;
    UI.toast('已建立该资料的初始掌握度，开始学习', 'ok');
    App.go(rec ? '#/student/material/' + rec.id : '#/student/dashboard');
  }

  /* ================= 活动运行器 ================= */
  function activity(type, topicId) {
    if (!Engine.topic(topicId)) {
      A = null;
      return UI.empty('该知识点不存在或已被移除', '📭');
    }
    if (!A || A.type !== type || A.topicId !== topicId) {
      A = { type, topicId, result: null };
      if (type === 'explain') {
        A.slides = Engine.topicContent(topicId).explain || []; A.i = 0; A.asked = null;
      } else if (type === 'practice') {
        A.qs = Engine.topicContent(topicId).quiz || [];
        A.i = 0; A.sel = null; A.correct = 0;
      } else if (type === 'review') {
        A.cards = (Engine.topicContent(topicId).explain || []).map(s => ({ q: s.t, a: s.tip }));
        A.i = 0; A.flip = false;
      }
    }

    const t = Engine.topic(topicId);
    const act = DATA.activities[type];
    if (!act) { A = null; return UI.empty('该活动已下线', '🚧'); }
    const back = backHash(topicId);

    // ---- 已完成 → 结果面板（含 Re-plan） ----
    if (A.result) {
      const r = A.result;
      return `
        <div class="runner">
          <div class="card card-pad" style="text-align:center">
            <div style="font-size:38px">${r.delta > 0 ? '📈' : '📉'}</div>
            <div class="h-section mt-8" style="font-size:18px">活动完成 · 掌握度已更新</div>
            <div class="row mt-16" style="justify-content:center;gap:24px">
              <div><div class="text-xs" style="color:var(--text-3)">之前</div><div style="font-size:24px;font-weight:800">${r.before}%</div></div>
              <div style="font-size:20px;color:var(--text-3)">→</div>
              <div><div class="text-xs" style="color:var(--text-3)">现在</div><div style="font-size:24px;font-weight:800;color:var(--success)">${r.after}%</div></div>
              <div><div class="text-xs" style="color:var(--text-3)">提升</div><div style="font-size:24px;font-weight:800;color:var(--primary)">+${r.delta}%</div></div>
            </div>
            ${UI.bar(r.after)}
            <div class="mt-16"></div>
          </div>

          <div class="card card-pad mt-16" style="background:var(--primary-soft);border-color:#c7d2fe">
            <div class="row" style="gap:10px">
              ${I('refresh', 18)}
              <div style="font-weight:700">Re-plan · Agent 的下一步决策</div>
            </div>
            <div class="text-sm mt-8" style="color:var(--text-2)">
              该知识点现为 <b>${r.after}%</b>，规则命中
              <span class="reason-rule">${r.re.rule}</span>，下一步推荐：
              <b>${r.re.activity.icon} ${r.re.activity.name}</b>
            </div>
            <div class="row mt-16">
              <button class="btn btn-primary" onclick="Student.start('${r.re.activity.id}','${r.topic.id}')">
                继续：${r.re.activity.name} ${I('arrowRight',15)}
              </button>
              <button class="btn btn-ghost" onclick="App.go('${back}')">返回资料模块</button>
            </div>
          </div>
        </div>`;
    }

    // ---- 活动主体 ----
    let body = '';
    if (type === 'explain')       body = vExplain();
    else if (type === 'practice') body = vPracticeQuiz();
    else if (type === 'review')   body = vReview();

    return `
      <div class="runner">
        <div class="runner-head">
          <button class="btn btn-ghost btn-sm" onclick="Student.quit()">${I('arrowLeft',15)} 退出</button>
          <div>
            <div class="runner-title">${act.icon} ${act.name}</div>
            <div class="runner-topic">${t.icon} ${escapeHtml(t.name)} · 训练：${act.ability}</div>
          </div>
          <span class="spacer"></span>
        </div>
        ${body}
      </div>`;
  }

  function quit() {
    const id = A && A.topicId;
    A = null;
    App.go(id ? backHash(id) : '#/student/dashboard');
  }

  /* ---- 知识点讲解 ---- */
  function vExplain() {
    const s = A.slides[A.i];
    if (!s) {
      return `
        <div class="card card-pad">
          ${UI.empty('该知识点的讲解内容尚未生成', '📄')}
          <div class="row mt-16">
            <span class="spacer"></span>
            <button class="btn btn-primary" onclick="Student.finish({})">完成讲解 ${I('check',15)}</button>
          </div>
        </div>`;
    }
    const items = (s.items || []).map(x => `<div class="slide-item"><span class="slide-bullet"></span><div>${escapeHtml(x)}</div></div>`).join('');
    const last = A.i === A.slides.length - 1;

    const askAns = A.asked ? `
      <div class="ask-answer gen-anim">
        <b>AI 助教：</b>关于「${escapeHtml(A.asked)}」——${escapeHtml(s.tip)} 该内容来自你上传的课程资料；如需沿其他方向展开，可到「课程资料 · AI 问答」继续追问。
      </div>` : '';

    return `
      <div class="slides">
        <div class="slide">
          <div class="slide-num">SLIDE ${A.i + 1} / ${A.slides.length}</div>
          <div class="slide-title">${escapeHtml(s.t)}</div>
          <div class="slide-list">${items}</div>
          <div class="slide-tip"><b>要点：</b>${escapeHtml(s.tip)}</div>
        </div>

        <div class="ask-box mt-16">
          <input id="ask-input" placeholder="随时暂停并提问，例如：这个概念如何应用？"
                 onkeydown="if(event.key==='Enter')Student.ask()">
          <button class="btn btn-soft" onclick="Student.ask()">${I('sparkles',15)} 提问</button>
        </div>
        ${askAns}

        <div class="row mt-16">
          <button class="btn btn-ghost" ${A.i === 0 ? 'disabled' : ''} onclick="Student.slide(-1)">上一页</button>
          <span class="spacer"></span>
          ${last
            ? `<button class="btn btn-primary" onclick="Student.finish({})">完成讲解 ${I('check',15)}</button>`
            : `<button class="btn btn-primary" onclick="Student.slide(1)">下一页 ${I('arrowRight',15)}</button>`}
        </div>
      </div>`;
  }
  function slide(d) {
    A.i = Math.max(0, Math.min(A.slides.length - 1, A.i + d));
    A.asked = null;
    App.render();
  }
  function ask() {
    const el = document.getElementById('ask-input');
    if (!el || !el.value.trim()) return;
    A.asked = el.value.trim();
    App.render();
  }

  /* ---- 练习测验 ---- */
  function vPracticeQuiz() {
    const q = A.qs[A.i];
    if (!q || !q.q) {
      return `
        <div class="card card-pad">
          ${UI.empty('该知识点暂无练习题', '📝')}
          <div class="row mt-16">
            <span class="spacer"></span>
            <button class="btn btn-primary" onclick="Student.finishPractice()">完成练习 ${I('check',15)}</button>
          </div>
        </div>`;
    }
    const opts = (q.opts || []).map((o, i) => {
      let cls = '';
      if (A.sel !== null) {
        if (i === q.a) cls = 'correct';
        else if (A.sel === i) cls = 'wrong';
      }
      return `<button class="opt ${cls}" ${A.sel !== null ? 'disabled' : `onclick="Student.pick(${i})"`}>
                <span class="opt-key">${'ABCD'[i]}</span><span class="opt-label">${escapeHtml(o)}</span>
              </button>`;
    }).join('');

    const fb = A.sel !== null ? `
      <div class="feedback ${A.sel === q.a ? 'ok' : 'bad'}">
        <div class="feedback-title">${A.sel === q.a ? I('check',15) + ' 正确' : I('bug',15) + ' 不正确'}</div>
        ${escapeHtml(q.why || '')}
      </div>
      <div class="row mt-16">
        ${A.i === A.qs.length - 1
          ? `<button class="btn btn-primary" onclick="Student.finishPractice()">完成练习 ${I('check',15)}</button>`
          : `<button class="btn btn-primary" onclick="Student.nextQ()">下一题 ${I('arrowRight',15)}</button>`}
      </div>` : '';

    return `
      <div class="card card-pad">
        <div class="bar mb-16"><div class="bar-fill" style="width:${(A.i / A.qs.length) * 100}%"></div></div>
        <div class="row mb-8"><span class="badge badge-primary">第 ${A.i + 1} / ${A.qs.length} 题</span>
          <span class="text-xs" style="color:var(--text-3)">题目来自该资料的 AI 解析结果</span></div>
        <div class="q-text">${escapeHtml(q.q)}</div>
        ${opts}
        ${fb}
      </div>`;
  }
  function pick(i) {
    if (A.sel !== null) return;
    A.sel = i;
    if (i === A.qs[A.i].a) A.correct++;
    App.render();
  }
  function nextQ() {
    if (A.i < A.qs.length - 1) { A.i++; A.sel = null; App.render(); }
  }
  function finishPractice() {
    const quizOk = A.correct >= Math.ceil(A.qs.length / 2);
    finish({ quizCorrect: quizOk });
  }

  /* ---- 知识复习（闪卡） ---- */
  function vReview() {
    const c = A.cards[A.i];
    if (!c) {
      return `
        <div class="card card-pad">
          ${UI.empty('暂无可复习的内容', '🔁')}
          <div class="row mt-16">
            <span class="spacer"></span>
            <button class="btn btn-primary" onclick="Student.finish({})">完成复习 ${I('check',15)}</button>
          </div>
        </div>`;
    }
    const last = A.i === A.cards.length - 1;
    return `
      <div class="flash" onclick="Student.flip()">
        <div class="flash-q">${escapeHtml(c.q)}</div>
        ${A.flip ? `<div class="flash-a gen-anim">${escapeHtml(c.a)}</div>`
                 : `<div class="text-sm mt-16" style="color:var(--text-3)">点击卡片查看答案</div>`}
      </div>
      <div class="row mt-16">
        <button class="btn btn-ghost" ${A.i === 0 ? 'disabled' : ''} onclick="Student.card(-1)">上一张</button>
        <div class="spacer"></div>
        <span class="text-sm" style="color:var(--text-3)">${A.i + 1} / ${A.cards.length}</span>
        ${last ? `<button class="btn btn-primary" onclick="Student.finish({})">完成复习 ${I('check',15)}</button>`
               : `<button class="btn btn-primary" onclick="Student.card(1)">下一张 ${I('arrowRight',15)}</button>`}
      </div>`;
  }
  function flip() { A.flip = !A.flip; App.render(); }
  function card(d) { A.i = Math.max(0, Math.min(A.cards.length - 1, A.i + d)); A.flip = false; App.render(); }

  /* ================= 课程资料上传 + AI 问答助手 ================= */

  /* AI 问答历史记录持久化（localStorage），刷新页面后仍保留 */
  const CHAT_KEY = 'asa_student_chat_v1';

  function loadChat() {
    try {
      const raw = localStorage.getItem(CHAT_KEY);
      if (raw) {
        const parsed = JSON.parse(raw);
        if (parsed && Array.isArray(parsed.msgs)) {
          M = { msgs: parsed.msgs };
          return;
        }
      }
    } catch (e) { /* 忽略损坏数据 */ }
    M = { msgs: [] };
  }

  function saveChat() {
    if (!M) return;
    try { localStorage.setItem(CHAT_KEY, JSON.stringify(M)); } catch (e) {}
  }

  function clearChat() {
    try { localStorage.removeItem(CHAT_KEY); } catch (e) {}
    M = { msgs: [] };
  }

  function materials() {
    if (!M) loadChat();
    App.go('#/student/materials');
  }

  function materialsView() {
    if (!M) loadChat();

    const mine = Engine.uploads();
    const indexed = mine.filter(m => m.status === 'parsed' || m.status === 'indexed').length;

    /* 资料列表 */
    const rowOf = (m, removable) => `
      <div class="topic-row">
        <div class="topic-emoji">${fileIcon(m.kind)}</div>
        <div class="topic-main">
          <div class="topic-name">${escapeHtml(m.name)}</div>
          <div class="topic-meta">${m.kind} · ${m.size}${m.pages ? ' · ' + m.pages + ' 页' : ''} · ${m.from || '我的上传'}</div>
        </div>
        ${statusBadge(m)}
        ${Engine.materialStats(m).count > 0
          ? `<button class="btn btn-soft btn-sm" onclick="Student.openMaterial('${m.id}')">${I('arrowRight',13)} 进入模块</button>`
          : ''}
        ${removable ? `<button class="btn btn-ghost btn-sm" onclick="Student.rmUpload('${m.id}')">移除</button>` : ''}
      </div>`;

    /* 解析失败的资料，额外展示错误原因与重试 */
    const failedRows = mine.filter(m => m.status === 'error').map(m => `
      <div class="card-body" style="border-top:1px solid var(--border);background:var(--danger-soft, #fef2f2)">
        <div class="text-xs" style="color:var(--danger)">${I('bug',12)} <b>${escapeHtml(m.name)}</b> 解析失败：${escapeHtml(m.error || '未知错误')}</div>
      </div>`).join('');

    const fileRows = mine.length
      ? mine.map(m => rowOf(m, true)).join('')
      : `<div class="empty" style="padding:22px">还没有上传资料 · 上传后 AI 会解析并生成知识点与测验</div>`;

    /* 问答消息 */
    const msgs = M.msgs.map(m => `
      <div class="msg ${m.who === 'ai' ? 'ai' : 'me'}">
        ${m.who === 'ai' ? `<div class="msg-who">${I('sparkles',12)} AI 问答助手</div>` : ''}
        ${m.pending
          ? `<span class="msg-pending">正在思考<span class="dots"></span></span>`
          : m.text.replace(/\n/g, '<br>')}
        ${m.sources && m.sources.length ? `
          <div class="qa-src">
            <span class="text-xs" style="color:var(--text-3)">引用出处：</span>
            ${m.sources.map(s => `<span class="chip">${escapeHtml(s)}</span>`).join('')}
          </div>` : ''}
      </div>`).join('')
      || `<div class="msg ai">
            <div class="msg-who">${I('sparkles',12)} AI 问答助手</div>
            ${DATA.assistant.welcome}
            <div class="qa-src"><span class="chip">${I('database',12)} 已索引 ${indexed} 份资料</span></div>
          </div>`;

    return `
      <div class="grid g-main" style="align-items:start">
        <div class="card">
          <div class="card-head">
            <span class="h-section">AI 问答助手</span>
            <span class="badge badge-info">${indexed} 份资料可检索</span>
            <span class="spacer"></span>
            <span class="text-xs" style="color:var(--text-3)">只依据你的资料作答，并给出引用</span>
          </div>
          <div class="chat-wrap" style="height:460px">
            <div class="chat-scroll" id="qa-scroll">${msgs}</div>
            <div class="chat-input">
              <input id="qa-in" placeholder="就课程资料提问，例如：这份资料的核心概念是什么？"
                     onkeydown="if(event.key==='Enter')Student.askAI()">
              <button class="btn btn-primary" onclick="Student.askAI()">${I('arrowRight',15)}</button>
            </div>
          </div>
          <div class="card-body" style="border-top:1px solid var(--border)">
            <div class="text-xs mb-8" style="color:var(--text-3)">试试这些问题：</div>
            <div class="row row-wrap">
              ${DATA.assistant.examples.map(q =>
                `<button class="chip" onclick="Student.askExample('${q}')">${q}</button>`).join('')}
            </div>
          </div>
        </div>

        <div class="card">
          <div class="card-head">
            <span class="h-section">我的课程资料</span>
            <span class="spacer"></span>
            <span class="badge badge-muted">${mine.length} 份我的上传</span>
          </div>

          <div class="card-body">
            <div class="dropzone"
                 onclick="document.getElementById('file-input').click()"
                 ondragover="event.preventDefault();this.classList.add('over')"
                 ondragleave="this.classList.remove('over')"
                 ondrop="Student.drop(event)">
              ${I('upload', 22)}
              <div style="font-weight:700;font-size:13.5px;margin-top:6px">上传课程资料</div>
              <div class="text-xs" style="color:var(--text-3)">点击选择，或把文件拖到这里 · 支持 PDF / TXT / MD</div>
            </div>
            <input type="file" id="file-input" multiple hidden onchange="Student.onFiles(this)">
          </div>

          ${fileRows}
          ${failedRows}

          <div class="card-body" style="border-top:1px solid var(--border)">
            <div class="text-xs" style="color:var(--text-3)">
              ${I('shield',12)} 每份资料解析后会生成独立的学习模块，可回到「学习主页」按资料进入；AI 回答均为生成内容，请对照引用出处自行判断。
            </div>
          </div>
        </div>
      </div>`;
  }

  function onFiles(input) {
    const files = Array.from(input.files || []);
    input.value = '';
    if (!files.length) return;
    files.slice(0, 5).forEach(f => uploadAndParse(f));
  }

  function drop(e) {
    e.preventDefault();
    const dt = e.dataTransfer;
    if (!dt || !dt.files) return;
    Array.from(dt.files).slice(0, 5).forEach(f => uploadAndParse(f));
  }

  /* 上传资料 → 后端解析 → 生成知识点与 quiz */
  async function uploadAndParse(file) {
    const rec = Engine.addUpload(file);
    App.render();

    // 1) 上传
    const up = await API.uploadMaterial(file);
    if (!up.ok) {
      Engine.markUpload(rec.id, { status: 'error', error: up.error });
      UI.toast('上传失败：' + up.error, 'err');
      App.render();
      return;
    }
    const materialId = up.data.id || up.data.material_id || rec.id;
    Engine.markUpload(rec.id, { status: 'uploaded', materialId });
    App.render();

    // 2) 解析（LLM 抽取知识点 + 生成 quiz）
    Engine.markUpload(rec.id, { status: 'parsing' });
    App.render();
    const parsed = await API.parseMaterial(materialId);
    if (!parsed.ok) {
      Engine.markUpload(rec.id, { status: 'error', error: parsed.error });
      UI.toast('解析失败：' + parsed.error, 'err');
      App.render();
      return;
    }
    const data = parsed.data || {};
    const added = Engine.ingestParsed({ ...data, source: file.name, material_id: materialId });
    Engine.markUpload(rec.id, {
      status: 'parsed',
      topicCount: added.length,
      pages: data.pages,
    });
    UI.toast(`已从「${file.name}」解析出 ${added.length} 个知识点`, 'ok');
    App.render();
  }

  async function rmUpload(id) {
    const rec = Engine.uploads().find(x => x.id === id);
    // 先调后端删除，清理 ChromaDB 向量与磁盘文件，避免残留引用
    if (rec && rec.materialId) {
      try {
        await API.deleteMaterial(rec.materialId);
      } catch (e) { /* 后端删除失败也继续删本地，避免卡住 */ }
    }
    Engine.removeUpload(id);
    UI.toast('已移除该资料（含索引与知识点）', 'warn');
    App.render();
  }

  function askAI() {
    const el = document.getElementById('qa-in');
    if (!el || !el.value.trim()) return;
    const q = el.value.trim();
    el.value = '';
    if (!M) loadChat();
    M.msgs.push({ who: 'me', text: q });
    saveChat();
    App.render();
    doAsk(q);
  }

  function askExample(q) {
    if (!M) loadChat();
    M.msgs.push({ who: 'me', text: q });
    saveChat();
    App.render();
    doAsk(q);
  }

  async function doAsk(q) {
    // 优先走真实后端 RAG + LLM；后端不可用时给出明确提示
    if (!M) loadChat();
    // 先插入「正在思考」占位，避免用户误以为卡死
    const pending = { who: 'ai', text: '', pending: true, sources: [] };
    M.msgs.push(pending);
    App.render();
    const sc = document.getElementById('qa-scroll');
    if (sc) sc.scrollTop = sc.scrollHeight;

    const r = await API.ask(q);
    // 用真实结果替换占位消息
    if (r.ok && r.data) {
      pending.pending = false;
      pending.text = r.data.answer || '';
      pending.sources = r.data.sources || [];
    } else {
      pending.pending = false;
      pending.text = (r.error ? '（' + r.error + '）\n\n' : '')
        + '暂时无法连接 AI 问答后端。请确认已启动后端服务（FastAPI，默认 http://localhost:8000），并已配置 LLM API Key。';
      pending.sources = [];
    }
    saveChat();
    App.render();
    const sc2 = document.getElementById('qa-scroll');
    if (sc2) sc2.scrollTop = sc2.scrollHeight;
  }

  /* ================= 学习进度（按资料切换） ================= */
  function progress(filter) {
    const mine = Engine.uploads();
    let rec = null;
    let showAll = true;
    if (filter && filter !== 'all') {
      rec = Engine.findMaterial(filter);
      if (rec) showAll = false;
    } else if (!filter) {
      const act = Engine.activeMaterial();
      if (act) { rec = act; showAll = false; }
    }

    const ts = rec ? Engine.topicsOf(rec) : Engine.pathTopics();
    const ids = ts.map(t => t.id);
    const trace = Engine.state.trace.filter(x => ids.indexOf(x.topicId) !== -1);
    const traceRows = trace.map(x => `
      <div class="topic-row">
        <div class="topic-emoji">${(Engine.topic(x.topicId) || {}).icon || '📘'}</div>
        <div class="topic-main">
          <div class="topic-name">${escapeHtml(x.topicName)} · ${x.actName}</div>
          <div class="topic-meta">${x.before}% → ${x.after}%</div>
        </div>
        <span class="badge badge-success">+${x.delta}%</span>
      </div>`).join('') || UI.empty('该范围内暂无学习记录', '📊');

    const filterBar = `
      <div class="row row-wrap mb-16" style="gap:8px">
        <span class="text-xs" style="color:var(--text-3);margin-right:4px">资料范围：</span>
        <button class="chip ${showAll ? 'chip-active' : ''}" onclick="App.go('#/student/progress/all')">全部资料</button>
        ${mine.map(m => `
          <button class="chip ${!showAll && rec && rec.id === m.id ? 'chip-active' : ''}"
                  onclick="App.go('#/student/progress/${m.id}')">${fileIcon(m.kind)} ${escapeHtml(m.name)}</button>`).join('')}
      </div>`;

    return `
      <div class="section-head">
        <span class="h-section">学习进度与掌握度</span>
        <span class="spacer"></span>
        <span class="badge badge-primary">${showAll ? '全部资料' : escapeHtml(rec.name)}</span>
      </div>

      ${filterBar}

      <div class="grid g-4 mb-16">
        ${kpi('整体掌握度', Engine.overallMastery(ts) + '%', 'target', '', showAll ? '全部资料平均' : '本资料平均')}
        ${kpi('知识点数', ts.length, 'book', '', showAll ? '跨全部资料' : '本资料解析结果')}
        ${kpi('薄弱知识点', ts.filter(t => t.mastery < 45).length, 'bug', 'amber', '需优先关注')}
        ${kpi('已掌握', ts.filter(t => t.mastery >= 75).length, 'check', 'green', `共 ${ts.length} 个知识点`)}
      </div>

      <div class="grid g-main">
        <div class="card">
          <div class="card-head">
            <span class="h-section">各知识点掌握度</span>
            <span class="spacer"></span>
            <span class="badge badge-primary">${ts.length} 个知识点</span>
          </div>
          <div class="card-body">
            ${Engine.pathGroups(ts).map(g => `
              <div class="text-xs mb-8" style="color:var(--text-3);font-weight:600">${escapeHtml(g.course.title)}</div>
              ${g.topics.map(t => `
              <div class="mb-16">
                <div class="row mb-8">
                  <span style="font-weight:600;font-size:13.5px">${t.icon} ${escapeHtml(t.name)}</span>
                  <span class="spacer"></span>
                  <span style="font-weight:800">${t.mastery}%</span>
                  <span class="badge ${UI.level(t.mastery).badge}">${UI.level(t.mastery).label}</span>
                </div>
                ${UI.bar(t.mastery)}
                <div class="text-xs mt-8" style="color:var(--text-3)">练习 ${t.attempts} 次 · 错误 ${t.errors} 次${t.lastActivity ? ' · 最近：' + escapeHtml(t.lastActivity) : ''}</div>
              </div>`).join('')}
              <div class="divider"></div>`).join('')}
            ${ts.length ? '' : UI.empty('该范围还没有知识点，先上传并解析课程资料', '📚')}
          </div>
        </div>

        <div class="card">
          <div class="card-head"><span class="h-section">学习轨迹</span></div>
          ${traceRows}
        </div>
      </div>`;
  }

  function kpi(label, val, ic, tone, delta) {
    return `
      <div class="card stat">
        <div class="row">
          <div class="stat-icon ${tone}">${I(ic, 18)}</div>
          <span class="spacer"></span>
        </div>
        <div class="stat-label" style="margin-top:12px">${label}</div>
        <div class="stat-value">${val}</div>
        <div class="stat-delta up">${delta}</div>
      </div>`;
  }

  async function reset() {
    // 先清后端（ChromaDB 索引 + 上传文件），否则 AI 问答仍会命中旧资料并给出过时引用
    let backendOk = false;
    try {
      const r = await API.resetMaterials();
      backendOk = !!(r && r.ok);
    } catch (e) {
      backendOk = false;
    }
    Engine.reset();
    clearChat();
    A = null; P = null;
    UI.toast(backendOk ? '演示数据已重置（含后端索引）' : '演示数据已重置（后端未连接，索引可能未清除）', backendOk ? 'ok' : 'warn');
    App.go('#/student/dashboard');
  }

  function dismissAppend() {
    Engine.dismissAppend();
    App.render();
  }

  /* ================= 工具 ================= */
  function escapeHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  function start(type, topicId) {
    const rec = Engine.materialOfTopic(Engine.topic(topicId));
    if (rec && !Engine.pretestDone(rec)) {
      UI.toast('建议先完成该资料的 Pre-test 以获得准确起点', 'warn');
    }
    startAct(type, topicId);
  }

  return {
    dashboard, materialView, chooseMaterial, openMaterial,
    topicDetail, openTopic, activity, start, quit,
    pretest, pretestView, pickPretest, nextPretest, commitPretest,
    slide, ask, pick, nextQ, finishPractice, flip, card, finish,
    materials, materialsView, onFiles, drop, rmUpload, askAI, askExample,
    progress, reset, dismissAppend,
  };
})();
