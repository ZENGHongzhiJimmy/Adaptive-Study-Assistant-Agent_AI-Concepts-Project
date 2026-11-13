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
      A.i = 0; A.ask = null;
    } else if (type === 'practice') {
      A.qs = Engine.quizBank(Engine.topic(topicId));
      A.i = 0; A.sel = null; A.correct = 0;
      A.answers = [];                 // 逐题结果，用于「答错的进错题本」
    } else if (type === 'review') {
      A.cards = Engine.reviewCards(topicId);
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

  /* 统一图标语言：所有资料类型共用一个单色线性「文件」图标，
     类型信息交给旁边的 PDF / PPT / MD / TXT 文字，避免彩色 emoji 抢焦点。
     kind 保留为兼容参数（历史调用会传入文件类型）。 */
  function fileIcon(kind) {
    return I('file', 18);
  }

  /* 知识点行：统一用一个「书」线性图标，不再按序号轮换彩色 emoji */
  function kpIcon() { return I('bookOpen', 16); }

  function statusBadge(m) {
    switch (m.status) {
      case 'uploading': return `<span class="badge badge-warning">${I('refresh',12)} 上传中…</span>`;
      case 'uploaded':  return `<span class="badge badge-muted">${I('file',12)} 已上传（未解析）</span>`;
      case 'parsing':   return `<span class="badge badge-warning">${I('refresh',12)} AI 解析中…</span>`;
      case 'interrupted': return `<span class="badge badge-warning" title="${escapeHtml(m.error || '')}">${I('refresh',12)} ${m.materialId ? '解析中断 · 正在恢复' : '上传中断 · 请重新上传'}</span>`;
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
      ['选择课程资料', '选中后在规划里开始学习'],
      ['完成 Pre-test', '建立该资料的起点'],
      ['滚动学习', '按轮次推进全部知识点'],
      ['查看学习进度', '掌握度持续更新'],
    ];
    const activeAllMastered = !!(activeStats && activeStats.count > 0 &&
      activeStats.topics.every(t => Engine.isMastered(t)));
    const cur = !active ? 0
      : !activeStats.pretestDone ? 1
      : (activeAllMastered ? 3 : 2);
    const activeRoll = active && activeStats.count && activeStats.pretestDone
      ? Engine.rollPlan(active) : null;

    const activeHint = active
      ? `<div class="plan-current">
           <div>
             <div class="text-xs" style="color:var(--primary-600);font-weight:700">${I('check', 12)} 已选中 · 当前资料</div>
             <div style="font-weight:700;font-size:13.5px">${I('file', 14)} ${escapeHtml(active.name)}</div>
             <div class="text-xs" style="color:var(--text-3)">
               ${activeStats.count} 个知识点 · ${activeStats.pretestDone
                 ? (activeRoll ? `第 ${activeRoll.round} 轮 · 已掌握 ${activeRoll.mastered}/${activeRoll.total}` : 'Pre-test 已完成')
                 : `还有 ${activeStats.pending} 个知识点待 Pre-test`}
             </div>
           </div>
           <span class="spacer"></span>
           <button class="btn btn-primary btn-sm" onclick="Student.openMaterial('${active.id}')">
             开始学习 ${I('arrowRight',14)}
           </button>
         </div>`
      : `<div class="plan-current">
           <div>
             <div style="font-weight:700;font-size:13.5px">先选择一份课程资料</div>
             <div class="text-xs" style="color:var(--text-3)">选中资料后点「开始学习」，才会出现 Pre-test 与后续学习步骤。</div>
           </div>
         </div>`;

    const planCard = homeSection(
      'focus', '自主学习规划', `${planSteps.length} 步闭环`,
      'Plan → Act → Observe → Update → Re-plan',
      `${stepsBar(planSteps, cur)}${activeHint}`
    );

    /* ② 平台功能（展示时排在自主学习规划之前） */
    const FEATURES = [
      ['upload',    '上传与 AI 解析', 'PDF / TXT / MD 上传后，LLM 自动抽取知识点、讲解与测验题'],
      ['sparkles',  '自适应学习路径', '按掌握度与错误次数推荐讲解 / 练习 / 复习，一次只推一件事'],
      ['chat',      '课程资料 AI 问答', 'RAG 只依据你上传的资料作答，并给出可核对的引用出处'],
      ['chart',     '学习进度追踪', '掌握度、错误与学习轨迹按资料模块独立记录，随时回看'],
    ];
    const featureCards = FEATURES.map(f => `
      <div class="card overview-item">
        <div class="overview-ico">${I(f[0], 18)}</div>
        <div class="overview-t">${f[1]}</div>
        <div class="overview-d">${f[2]}</div>
      </div>`).join('');
    const overview = homeSection('stack', '平台功能', `${FEATURES.length} 项能力`, '',
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
        <span class="notice-ico">${I('bookOpen', 17)}</span>
        <div style="flex:1">
          <div class="notice-t">开始你的自主学习</div>
          <div class="notice-d">上传第一份课程资料，AI 会解析出知识点与测验，并为它建立独立的学习模块。</div>
        </div>
        <button class="btn btn-primary" onclick="App.go('#/student/materials')">上传资料 ${I('arrowRight',15)}</button>
      </div>` : '';

    const materialSection = homeSection(
      'bookOpen', '我的课程资料',
      `${mine.length} 份资料 · ${parsed.length} 份已解析`,
      '点击卡片选中 · 选中后在「自主学习规划」里点「开始学习」',
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
      return UI.empty('该资料不存在或已被移除，请回到学习主页重新选择', 'inbox') +
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
          <div class="runner-title runner-title-ico">${I('file', 17)} ${escapeHtml(rec.name)}</div>
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
            : '该资料还没有解析出知识点', rec.status === 'error' ? 'bug' : 'file')}
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
            ${miniStat('知识点', s.count, 'bookOpen')}
            ${miniStat('讲解页', s.explainCount, 'stack')}
            ${miniStat('测验题', s.quizCount, 'focus')}
            ${miniStat('平均掌握度', s.avg + '%', 'chart')}
          </div>
        </div>
      </div>`;

    /* ② 该资料的自主学习路径（选择资料后才显示 Pre-test 等步骤）
       默认是「滚动学习」：按轮次把这份资料的所有知识点一起推进，
       而不是把一个知识点学到 80% 再换下一个。 */
    const pathSteps = [
      ['完成 Pre-test', '建立该资料的初始掌握度'],
      ['滚动学习', '按轮次推进全部知识点'],
      ['查看学习进度', '掌握度持续更新'],
    ];
    const allMastered = s.count > 0 && s.topics.every(t => Engine.isMastered(t));
    const cur = !s.pretestDone ? 0 : (allMastered ? 2 : 1);
    const pretestNotice = !s.pretestDone ? `
      <div class="notice">
        <span class="notice-ico">${I('clipboard', 17)}</span>
        <div style="flex:1">
          <div class="notice-t">先完成这份资料的 Pre-test</div>
          <div class="notice-d">共 ${s.pending} 道题（对应尚未评估的知识点），约 1–2 分钟；结果只影响本资料模块。</div>
        </div>
        <button class="btn btn-primary" onclick="Student.pretest('${rec.id}')">开始 Pre-test ${I('arrowRight',15)}</button>
      </div>` : `
      <div class="notice notice-ok">
        <span class="notice-ico">${I('check', 17)}</span>
        <div style="flex:1">
          <div class="notice-t">Pre-test 已完成 ${allMastered ? '· 全部知识点已掌握' : ''}</div>
          <div class="notice-d">${allMastered
            ? `这 ${s.count} 个知识点都达到了 ${Engine.MASTERED}% 且错题已纠正，本资料的学习路径已完成。`
            : `${s.count} 个知识点的起点已建立。下面由 Agent 按掌握度自动排定下一步，跟着走即可。`}</div>
        </div>
      </div>`;

    const roll = s.pretestDone && !allMastered ? Engine.rollPlan(rec) : null;
    const next = roll && roll.next;
    const focus = next ? `
      <div class="focus-card mt-16">
        <div class="focus-top">
          <div class="focus-ico">${I(next.activity.icon, 23)}</div>
          <div class="focus-main">
            <div class="focus-label">ADAPTIVE PLANNING AGENT · 第 ${roll.round} 轮 · 下一步</div>
            <div class="focus-title">${next.activity.name}</div>
            <div class="focus-meta">${escapeHtml(next.topic.name)} · 当前掌握度 ${next.topic.mastery}% · 本资料已掌握 ${roll.mastered}/${roll.total}</div>
          </div>
          ${UI.ring(next.topic.mastery, 68)}
        </div>
        <div class="focus-body">
          <div class="reason-line">${next.reason}　规则 <span class="reason-rule">${next.rule}</span></div>
          <div class="row mt-16">
            <button class="btn btn-primary" onclick="Student.start('${next.activity.id}','${next.topic.id}')">${I('play',15)} 开始学习</button>
            <span class="spacer"></span>
            <button class="btn btn-ghost btn-sm" onclick="App.go('#/student/progress/${rec.id}')">查看本资料进度</button>
          </div>
        </div>
      </div>` : (allMastered ? `
      <div class="focus-card mt-16">
        <div class="focus-top">
          <div class="focus-ico">${I('check', 23)}</div>
          <div class="focus-main">
            <div class="focus-label">ADAPTIVE PLANNING AGENT · 路径已完成</div>
            <div class="focus-title">本资料的知识点已全部掌握</div>
            <div class="focus-meta">${s.count} 个知识点 · 平均掌握度 ${s.avg}%</div>
          </div>
          ${UI.ring(s.avg, 68)}
        </div>
        <div class="focus-body">
          <div class="reason-line">所有知识点都达到 ${Engine.MASTERED}% 且没有待纠正的错题，Agent 不再安排新的活动。</div>
          <div class="row mt-16">
            <button class="btn btn-primary" onclick="App.go('#/student/progress/${rec.id}')">查看学习进度</button>
            <button class="btn btn-ghost" onclick="Student.openTopic('${s.topics[0] ? s.topics[0].id : ''}')">回看知识点</button>
          </div>
        </div>
      </div>` : (roll && roll.blocked.length ? `
      <div class="notice mt-16">
        <span class="notice-ico">${I('bug', 17)}</span>
        <div style="flex:1">
          <div class="notice-t">还有知识点暂时无法继续</div>
          <div class="notice-d">${escapeHtml(roll.blocked[0].name)}：解析结果里缺少可评估的内容，无法安排活动。</div>
        </div>
        <button class="btn btn-ghost btn-sm" onclick="Student.openTopic('${roll.blocked[0].id}')">查看</button>
      </div>` : ''));

    /* 不展示本轮队列：路径由 Agent 默认排定，学生只看「下一步」一个动作，
       不需要从多个知识点里挑谁先谁后。 */
    const pathCard = `
      <div class="card mb-16">
        <div class="card-head">
          <span class="h-section">自主学习路径 · ${escapeHtml(rec.name)}</span>
          <span class="spacer"></span>
          ${roll ? `<span class="badge badge-muted">第 ${roll.round} 轮</span>` : ''}
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
          <span class="text-xs" style="color:var(--text-3)">自主路径之外的单独学习入口 · 点击展开讲解与测验</span>
        </div>
        <div class="card-body kp-list">${kpList}</div>
      </div>`;

    /* ④ 该资料的学习记录 */
    const recent = s.trace.slice(0, 5).map(x => `
      <div class="topic-row">
        <div class="topic-emoji">${kpIcon()}</div>
        <div class="topic-main">
          <div class="topic-name">${escapeHtml(x.topicName)} · ${x.actName}</div>
          <div class="topic-meta">掌握度 ${x.before}% → ${x.after}%</div>
        </div>
        <span class="badge ${x.delta > 0 ? 'badge-success' : (x.delta < 0 ? 'badge-danger' : 'badge-muted')}">${x.delta > 0 ? '+' : ''}${x.delta}%</span>
      </div>`).join('') || UI.empty('这份资料还没有学习记录，完成一个活动试试', 'sparkles');

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
    const plan = Engine.plan(t.id);
    const actId = plan && plan.activity ? plan.activity.id : null;
    const mastered = Engine.isMastered(t);
    const explain = (c.explain || []).map(s => `
      <div class="kp-slide">
        <div class="kp-slide-t">${escapeHtml(s.t || '讲解要点')}</div>
        ${(s.items || []).length
          ? `<ul class="kp-points">${(s.items || []).map(x => `<li>${escapeHtml(x)}</li>`).join('')}</ul>`
          : ''}
        ${s.tip ? `<div class="kp-tip"><b>要点：</b>${escapeHtml(s.tip)}</div>` : ''}
      </div>`).join('') || `<div class="text-xs" style="color:var(--text-3)">暂无讲解内容</div>`;

    /* 题目默认不标答案：正确项与「解析」都先收起，避免一眼看到答案失去练习意义。
       点击「查看答案」后由 revealAnswer() 就地展开（只改 DOM，不重渲染，折叠面板不会合上）。
       只渲染「可作答」的题：缺答案或选项不足的题在 quizBank 里已被剔除。 */
    const bank = Engine.quizBank(t);
    const quiz = bank.map((q, qi) => `
      <div class="kp-quiz" data-a="${Number(q.a) || 0}">
        <div class="kp-quiz-q">${qi + 1}. ${escapeHtml(q.q)}</div>
        ${(q.opts || []).map((o, oi) => `
          <div class="kp-quiz-opt ${oi === q.a ? 'is-answer' : ''}">
            <span class="kp-quiz-key">${'ABCD'[oi]}</span>${escapeHtml(o)}
            ${oi === q.a ? '<span class="kp-answer-tag" hidden>正确答案</span>' : ''}
          </div>`).join('')}
        <div class="kp-quiz-foot">
          <button class="kp-reveal" type="button" onclick="Student.revealAnswer(this)">
            ${I('eye', 13)}<span>查看答案</span>
          </button>
        </div>
        ${q.why ? `<div class="kp-quiz-why" hidden>解析：${escapeHtml(q.why)}</div>` : ''}
      </div>`).join('') || `<div class="text-xs" style="color:var(--text-3)">暂无测验题</div>`;

    const lv = UI.level(t.mastery);
    return `
      <details class="kp-item">
        <summary class="kp-head">
          <span class="kp-idx">${i + 1}</span>
          <div class="kp-main">
            <div class="kp-name">${escapeHtml(t.name)}
              ${mastered ? '<span class="badge badge-success">已掌握</span>' : (t.pretested ? '' : '<span class="badge badge-warning">待 Pre-test</span>')}
            </div>
            <div class="kp-desc">${escapeHtml(t.desc || '')}</div>
            <div class="kp-meta">讲解 ${(c.explain || []).length} 页 · 测验 ${bank.length} 题 · ${lv.label}</div>
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
            ${actId ? `<button class="btn btn-primary btn-sm" onclick="Student.start('${actId}','${t.id}')">
              ${I('play',13)} ${mastered ? '再复习一次' : '开始学习'}
            </button>` : `<span class="badge badge-muted">暂无可学习活动</span>`}
          </div>
        </div>
      </details>`;
  }

  /* 展开/收起某道生成测验的答案：只改 DOM，不重渲染（折叠面板不会被合上） */
  function revealAnswer(btn) {
    const box = btn.closest('.kp-quiz');
    if (!box) return;
    const open = box.classList.toggle('is-revealed');
    const tag = box.querySelector('.kp-quiz-opt.is-answer .kp-answer-tag');
    if (tag) tag.hidden = !open;
    const why = box.querySelector('.kp-quiz-why');
    if (why) why.hidden = !open;
    const label = btn.querySelector('span');
    if (label) label.textContent = open ? '收起答案' : '查看答案';
    btn.setAttribute('aria-expanded', open ? 'true' : 'false');
  }

  /* ================= 知识点详情（三种活动可选） ================= */
  function openTopic(topicId) {
    if (!Engine.topic(topicId)) { UI.toast('该知识点不存在', 'warn'); return; }
    A = null;
    App.go('#/student/topic/' + topicId);
  }

  function topicDetail(topicId) {
    const t = Engine.topic(topicId);
    if (!t) return UI.empty('该知识点不存在或已被移除', 'inbox') +
      `<div class="row mt-16"><span class="spacer"></span><button class="btn btn-primary" onclick="App.go('#/student/dashboard')">回到学习主页</button></div>`;
    const p = Engine.plan(topicId);
    const recAct = p && p.activity ? p.activity.id : null;
    const rec = Engine.materialOfTopic(t);
    const acts = Object.values(DATA.activities).map(a => `
      <button class="act-card ${a.id === recAct ? 'recommended' : ''}" onclick="Student.start('${a.id}','${t.id}')">
        <div class="row" style="gap:8px">
          <span class="act-ico">${I(a.icon, 19)}</span>
          <span class="act-name">${a.name}</span>
          ${a.id === recAct ? '<span class="badge badge-primary" style="margin-left:auto">推荐</span>' : ''}
        </div>
        <div class="act-desc">${a.desc}</div>
        <div class="act-ability">训练：${a.ability}</div>
      </button>`).join('');

    return `
      <div class="runner-head">
        <button class="btn btn-ghost btn-sm" onclick="App.go('${backHash(t.id)}')">${I('arrowLeft',15)} 返回资料模块</button>
        <div>
          <div class="runner-title runner-title-ico">${I('bookOpen', 17)} ${escapeHtml(t.name)}</div>
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

      ${p && p.activity ? `
      <div class="notice">
        <span class="notice-ico">${I('sparkles',17)}</span>
        <div style="flex:1">
          <div class="notice-t">Agent 推荐：${p.activity.name}</div>
          <div class="notice-d">${p.reason}</div>
        </div>
      </div>` : `
      <div class="notice ${p && p.mastered ? 'notice-ok' : ''}">
        <span class="notice-ico">${I(p && p.mastered ? 'check' : 'bug', 17)}</span>
        <div style="flex:1">
          <div class="notice-t">${p && p.mastered ? '这个知识点已掌握' : '暂无可推荐的学习活动'}</div>
          <div class="notice-d">${p ? p.reason : ''}</div>
        </div>
      </div>`}

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
      return UI.empty('请先在学习主页选择一份课程资料', 'bookOpen') +
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
            <div><div class="runner-title runner-title-ico">${I('clipboard', 17)} Pre-test · ${escapeHtml(rec.name)}</div></div>
          </div>
          ${UI.empty('这份资料的所有知识点都已完成起点评估', 'check')}
          <div class="row mt-16"><span class="spacer"></span>
            <button class="btn btn-primary" onclick="App.go('${back}')">查看我的学习路径</button></div>
        </div>`;
    }

    if (P.i >= TS.length) {
      const correct = P.answers.filter(a => a.correct === true).length;
      const skipped = P.answers.filter(a => a.correct === null).length;
      const skipNote = skipped ? `（其中 ${skipped} 个知识点暂无评估题，已跳过）` : '';
      return `
        <div class="runner">
          <div class="card card-pad" style="text-align:center">
            <div class="finish-ico">${I('sparkles', 26)}</div>
            <div class="h-section mt-8" style="font-size:18px">Pre-test 完成</div>
            <div class="text-sm mt-8" style="color:var(--text-2)">
              「${escapeHtml(rec.name)}」答对 ${correct} / ${TS.length} 题${skipNote}。Agent 已为这 ${TS.length} 个知识点建立初始掌握度。
            </div>
            <div class="row mt-16" style="justify-content:center">
              <button class="btn btn-primary" onclick="Student.commitPretest()">开始学习 ${I('arrowRight',15)}</button>
            </div>
          </div>
        </div>`;
    }

    const t = TS[P.i];
    const q = Engine.topicContent(t.id).pretest || Engine.quizBank(t)[0];
    /* 没有评估题时必须给出出口：原来的写法直接 return UI.empty(...)，
       页面上连返回按钮都没有，学生卡死在这里，且该知识点的 pretested
       永远为 false → 这份资料的 pretestDone 永远不成立 → 整条学习路径不出现。 */
    if (!q || !q.q) {
      return `
        <div class="runner">
          <div class="runner-head">
            <button class="btn btn-ghost btn-sm" onclick="App.go('${back}')">${I('arrowLeft',15)} 返回资料模块</button>
            <div>
              <div class="runner-title runner-title-ico">${I('clipboard', 17)} Pre-test · 起点评估</div>
              <div class="runner-topic">第 ${P.i + 1} / ${TS.length} 题 · ${escapeHtml(t.name)}</div>
            </div>
          </div>
          <div class="card card-pad">
            ${UI.empty('该知识点还没有评估题', 'layers')}
            <div class="text-sm mt-8" style="color:var(--text-2);text-align:center">
              解析结果里没有为它生成测验题，可以跳过；它会按初始掌握度进入学习路径，稍后用讲解与练习掌握。
            </div>
            <div class="row mt-16" style="justify-content:center">
              <button class="btn btn-primary" onclick="Student.skipPretest()">跳过此题 ${I('arrowRight',15)}</button>
              <button class="btn btn-ghost" onclick="App.go('${back}')">返回资料模块</button>
            </div>
          </div>
        </div>`;
    }
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
            <div class="runner-topic">第 ${P.i + 1} / ${TS.length} 题 · ${escapeHtml(t.name)}</div>
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
    const q = Engine.topicContent(t.id).pretest || Engine.quizBank(t)[0];
    if (!q || !q.q) {
      /* 没有评估题：记一条「跳过」，否则该知识点仍是待评估，Pre-test 永远做不完 */
      P.answers.push({ topicId: t.id, correct: null });
      P.i++; P.sel = null; App.render(); return;
    }
    P.answers.push({ topicId: t.id, correct: P.sel === q.a });
    P.i++; P.sel = null;
    App.render();
  }

  /* 跳过「没有评估题」的知识点：标记已评估但不改掌握度（见 Engine.applyPreTest） */
  function skipPretest() {
    const rec = Engine.findMaterial(P && P.recId);
    const TS = rec ? Engine.pendingTopics(rec) : [];
    const t = TS[P.i];
    if (!t) return;
    P.answers.push({ topicId: t.id, correct: null });
    P.i++; P.sel = null;
    App.render();
  }

  function commitPretest() {
    const rec = Engine.findMaterial(P && P.recId);
    Engine.applyPreTest(P.answers);
    P = null;
    UI.toast('已建立该资料的初始掌握度，开始学习', 'ok');
    /* 默认就是自主规划学习：Pre-test 一结束，直接进入 Agent 排好的第一步，
       不再让学返回模块页自己挑知识点。没有可排的活动时才退回模块页。 */
    const roll = rec ? Engine.rollPlan(rec) : null;
    const first = roll && roll.next;
    if (first) { startAct(first.activity.id, first.topic.id); return; }
    App.go(rec ? '#/student/material/' + rec.id : '#/student/dashboard');
  }

  /* ================= 活动运行器 ================= */
  function activity(type, topicId) {
    if (!Engine.topic(topicId)) {
      A = null;
      return UI.empty('该知识点不存在或已被移除', 'inbox');
    }
    if (!A || A.type !== type || A.topicId !== topicId) {
      A = { type, topicId, result: null };
      if (type === 'explain') {
        A.slides = Engine.topicContent(topicId).explain || []; A.i = 0; A.ask = null;
      } else if (type === 'practice') {
        A.qs = Engine.quizBank(Engine.topic(topicId));
        A.i = 0; A.sel = null; A.correct = 0;
        A.answers = [];
      } else if (type === 'review') {
        A.cards = Engine.reviewCards(topicId);
        A.i = 0; A.flip = false;
      }
    }

    const t = Engine.topic(topicId);
    const act = DATA.activities[type];
    if (!act) { A = null; return UI.empty('该活动已下线', 'layers'); }
    const back = backHash(topicId);

    // ---- 已完成 → 结果面板（含 Re-plan） ----
    if (A.result) {
      const r = A.result;
      const up = r.delta > 0;
      const flat = r.delta === 0;
      const deltaTxt = flat ? '±0' : (up ? '+' + r.delta : String(r.delta));
      const deltaCol = flat ? 'var(--text-3)' : (up ? 'var(--success)' : 'var(--danger)');
      const headTxt = flat ? '活动完成 · 本次未计分'
        : up ? '活动完成 · 掌握度已更新' : '活动完成 · 掌握度回落';

      /* 完成后由 Agent 直接给出下一步：可能已经换到另一个知识点，
         这正是滚动学习与「一个知识点学到掌握」的区别。学生不需要选。 */
      const mRec = Engine.materialOfTopic(r.topic);
      const rollNow = mRec ? Engine.rollPlan(mRec) : null;
      const step = rollNow && rollNow.next ? rollNow.next : null;
      const replan = step ? `
          <div class="card card-pad mt-16" style="background:var(--primary-soft);border-color:#c7d2fe">
            <div class="row" style="gap:10px">
              ${I('refresh', 18)}
              <div style="font-weight:700">Re-plan · Agent 已排好下一步</div>
            </div>
            <div class="text-sm mt-8" style="color:var(--text-2)">
              本资料还有 <b>${rollNow.remaining}</b> 个知识点未掌握，Agent 按掌握度继续滚动到
              <b>${step.activity.name} · ${escapeHtml(step.topic.name)}</b>
              （当前 ${step.topic.mastery}%），规则命中
              <span class="reason-rule">${step.rule}</span>。
            </div>
            <div class="row mt-16">
              <button class="btn btn-primary" onclick="Student.start('${step.activity.id}','${step.topic.id}')">
                继续：${step.activity.name} ${I('arrowRight',15)}
              </button>
              <button class="btn btn-ghost" onclick="App.go('${back}')">返回资料模块</button>
            </div>
          </div>` : `
          <div class="card card-pad mt-16" style="background:var(--success-soft);border-color:#bbf7d0">
            <div class="row" style="gap:10px">
              ${I('check', 18)}
              <div style="font-weight:700">${rollNow && rollNow.done
                ? '这份资料的知识点已全部掌握'
                : (r.re && r.re.mastered ? '这个知识点已掌握' : '这个知识点暂无后续活动')}</div>
            </div>
            <div class="text-sm mt-8" style="color:var(--text-2)">${rollNow && rollNow.done
              ? `所有知识点都达到 ${Engine.MASTERED}% 且错题已纠正，Agent 不再安排新的活动。`
              : (r.re ? r.re.reason : '')}</div>
            <div class="row mt-16">
              <button class="btn btn-primary" onclick="App.go('${back}')">
                回到资料模块 · 查看整体路径 ${I('arrowRight',15)}
              </button>
              <button class="btn btn-ghost" onclick="Student.openTopic('${r.topic.id}')">查看知识点情况</button>
            </div>
          </div>`;

      return `
        <div class="runner">
          <div class="card card-pad" style="text-align:center">
            <div class="finish-ico">${I('chart', 26)}</div>
            <div class="h-section mt-8" style="font-size:18px">${headTxt}</div>
            <div class="row mt-16" style="justify-content:center;gap:24px">
              <div><div class="text-xs" style="color:var(--text-3)">之前</div><div style="font-size:24px;font-weight:800">${r.before}%</div></div>
              <div style="font-size:20px;color:var(--text-3)">→</div>
              <div><div class="text-xs" style="color:var(--text-3)">现在</div><div style="font-size:24px;font-weight:800;color:${deltaCol}">${r.after}%</div></div>
              <div><div class="text-xs" style="color:var(--text-3)">本次变化</div><div style="font-size:24px;font-weight:800;color:${deltaCol}">${deltaTxt}%</div></div>
            </div>
            ${UI.bar(r.after)}
            ${r.note ? `<div class="text-sm mt-8" style="color:var(--text-2)">${escapeHtml(r.note)}</div>` : ''}
            <div class="mt-16"></div>
          </div>
          ${replan}
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
            <div class="runner-title runner-title-ico">${I(act.icon, 17)} ${act.name}</div>
            <div class="runner-topic">${escapeHtml(t.name)} · 训练：${act.ability}</div>
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
      /* 没有讲解内容时不给「完成讲解」按钮：没有内容可学，点完成也不该加分 */
      const hasQuiz = (Engine.topicContent(A.topicId).quiz || []).length > 0;
      return `
        <div class="card card-pad">
          ${UI.empty('该知识点的讲解内容尚未生成', 'file')}
          <div class="text-sm mt-8" style="color:var(--text-2);text-align:center">
            这份资料的解析结果里没有为它生成讲解页，无法学习讲解。
          </div>
          <div class="row mt-16" style="justify-content:center">
            ${hasQuiz ? `
              <button class="btn btn-primary" onclick="Student.start('practice','${A.topicId}')">${I('focus',14)} 直接做练习</button>
              <button class="btn btn-ghost" onclick="Student.quit()">返回知识点</button>` : `
              <button class="btn btn-primary" onclick="Student.quit()">返回知识点</button>`}
          </div>
        </div>`;
    }
    const items = (s.items || []).map(x => `<div class="slide-item"><span class="slide-bullet"></span><div>${escapeHtml(x)}</div></div>`).join('');
    const last = A.i === A.slides.length - 1;

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
        ${renderAsk()}

        <div class="row mt-16">
          <button class="btn btn-ghost" ${A.i === 0 ? 'disabled' : ''} onclick="Student.slide(-1)">上一页</button>
          <span class="spacer"></span>
          ${last
            ? `<button class="btn btn-primary" onclick="Student.finish({})">完成讲解 ${I('check',15)}</button>`
            : `<button class="btn btn-primary" onclick="Student.slide(1)">下一页 ${I('arrowRight',15)}</button>`}
        </div>
      </div>`;
  }

  /* 随堂提问的渲染：加载中 / 真实 LLM 回答 / 失败重试 三态 */
  function renderAsk() {
    const a = A && A.ask;
    if (!a) return '';
    const head = `
      <div class="ask-answer-head">
        ${I('sparkles',13)}<b>AI 助教</b>
        <span class="ask-q">关于「${escapeHtml(a.q)}」</span>
      </div>`;

    if (a.state === 'loading') {
      return `
        <div class="ask-answer gen-anim">
          ${head}
          <span class="msg-pending">正在结合本页讲解与课程资料作答<span class="dots"></span></span>
        </div>`;
    }

    if (a.state === 'error') {
      return `
        <div class="ask-answer is-error gen-anim">
          ${head}
          <div class="ask-text">${escapeHtml(a.error || '暂时无法连接 AI 助教后端。')}</div>
          <div class="ask-note">请确认后端已启动（默认 http://localhost:8000），并在项目根目录 .env 中配置 OPENROUTER_API_KEY 后重启后端。</div>
          <div class="ask-actions">
            <button class="btn btn-ghost btn-sm" onclick="Student.retryAsk()">${I('refresh',13)} 重试</button>
          </div>
        </div>`;
    }

    const src = (a.sources && a.sources.length) ? `
      <div class="qa-src">
        <span class="text-xs" style="color:var(--text-3)">引用出处：</span>
        ${a.sources.map(x => `<span class="chip">${escapeHtml(x)}</span>`).join('')}
      </div>` : '';
    const note = a.grounded === false
      ? `<div class="ask-note">资料中未检索到额外依据，以上回答仅基于当前讲解页内容。</div>` : '';
    return `
      <div class="ask-answer gen-anim">
        ${head}
        <div class="ask-text">${fmtAnswer(a.answer)}</div>
        ${note}
        ${src}
      </div>`;
  }

  /* 轻量排版：转义后把段落/项目符号转成 HTML，不引入 markdown 渲染器 */
  function fmtAnswer(raw) {
    const safe = escapeHtml(raw).replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>');
    return safe.split(/\n{2,}/).map(block => {
      const lines = block.split('\n').map(l => l.trim()).filter(Boolean);
      if (!lines.length) return '';
      const bullets = lines.filter(l => /^[-*•]\s+/.test(l));
      if (bullets.length === lines.length) {
        return `<ul class="ask-list">${lines
          .map(l => `<li>${l.replace(/^[-*•]\s+/, '')}</li>`).join('')}</ul>`;
      }
      return `<p>${lines.join('<br>')}</p>`;
    }).join('');
  }

  function slide(d) {
    A.i = Math.max(0, Math.min(A.slides.length - 1, A.i + d));
    A.ask = null;
    App.render();
  }

  /* 提问：先渲染「思考中」，再调用后端 /api/qa/ask（RAG + LLM）取真实回答 */
  function ask() {
    const el = document.getElementById('ask-input');
    if (!el) return;
    const q = el.value.trim();
    if (!q) { UI.toast('请先输入你的问题', 'warn'); return; }
    askTutor(q);
  }

  function retryAsk() {
    const q = A && A.ask && A.ask.q;
    if (q) askTutor(q);
  }

  async function askTutor(q) {
    const topic = Engine.topic(A.topicId) || {};
    const s = A.slides[A.i] || {};
    A.ask = { q, state: 'loading' };
    App.render();

    // 当前讲解页整页内容作为 grounded 上下文（同属该资料），
    // 让「这一页没讲透」的追问能被正常解释，而不是回答「资料里没有」。
    const rec = Engine.materialOfTopic(topic);
    const focus = [
      s.t ? `标题：${s.t}` : '',
      ...(s.items || []),
      s.tip ? `要点：${s.tip}` : '',
    ].filter(Boolean).join('\n');

    const r = await API.ask(
      q,
      rec ? (rec.materialId || rec.id) : null,
      rec ? rec.name : null,
      { topic: topic.name, focus }
    );

    // 等待期间用户可能已翻页 / 退出，丢弃过期结果
    if (!A || A.type !== 'explain' || !A.ask || A.ask.q !== q) return;

    if (r.ok && r.data) {
      A.ask = {
        q,
        state: 'done',
        answer: r.data.answer || '',
        sources: r.data.sources || [],
        grounded: r.data.grounded !== false,
      };
    } else {
      A.ask = { q, state: 'error', error: r.error || '' };
    }
    App.render();
    const box = document.querySelector('.ask-answer');
    if (box && box.scrollIntoView) box.scrollIntoView({ block: 'nearest' });
  }

  /* ---- 练习测验 ---- */
  function vPracticeQuiz() {
    const q = A.qs[A.i];
    if (!q || !q.q) {
      /* 没有题目时不给「完成练习」：原来点一下就走 finishPractice()，
         qs.length === 0 → ceil(0/2) === 0 → 0 >= 0 判为通过 → 白送 +12%。
         这里改成引导去看讲解，练习分只能靠真正答题拿到。 */
      return `
        <div class="card card-pad">
          ${UI.empty('该知识点暂无练习题', 'target')}
          <div class="text-sm mt-8" style="color:var(--text-2);text-align:center">
            这份资料的解析结果里没有为它生成测验题，先看讲解更有效。
          </div>
          <div class="row mt-16" style="justify-content:center">
            <button class="btn btn-primary" onclick="Student.start('explain','${A.topicId}')">${I('book',14)} 去学习讲解</button>
            <button class="btn btn-ghost" onclick="Student.quit()">返回知识点</button>
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
    const ok = i === A.qs[A.i].a;
    if (ok) A.correct++;
    if (!A.answers) A.answers = [];
    A.answers.push({ i: A.i, ok });
    App.render();
  }
  function nextQ() {
    if (A.i < A.qs.length - 1) { A.i++; A.sel = null; App.render(); }
  }
  function finishPractice() {
    /* 防御：题目为空时既不算完成也不加分（界面已不会走到这里，防止控制台误调用） */
    if (!A || !A.qs || !A.qs.length) {
      UI.toast('该知识点还没有练习题，无法生成练习成绩', 'warn');
      return;
    }
    finish({
      correct: A.correct,
      total: A.qs.length,
      answers: (A.answers || []).slice(),
    });
  }

  /* ---- 知识复习（错题闪卡） ---- */
  function vReview() {
    const c = A.cards[A.i];
    if (!c) {
      return `
        <div class="card card-pad">
          ${UI.empty('这个知识点暂时没有错题', 'check')}
          <div class="text-sm mt-8" style="color:var(--text-2);text-align:center">
            复习只针对练习中答错的题；没有错题时不需要复习，直接去练习即可。
          </div>
          <div class="row mt-16" style="justify-content:center">
            <button class="btn btn-primary" onclick="Student.quit()">返回知识点</button>
          </div>
        </div>`;
    }
    const last = A.i === A.cards.length - 1;
    return `
      <div class="flash" onclick="Student.flip()">
        <div class="flash-q">${escapeHtml(c.q)}</div>
        ${A.flip ? `<div class="flash-a gen-anim">${escapeHtml(c.a)}</div>
                    ${c.why ? `<div class="text-sm mt-8" style="color:var(--text-2)">${escapeHtml(c.why)}</div>` : ''}`
                 : `<div class="text-sm mt-16" style="color:var(--text-3)">点击卡片查看正确答案</div>`}
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

  /* ================= AI 问答记录（按课程资料分别存储） =================
     每份上传的资料拥有独立的问答记录桶，key 用上传记录 id —— 它在上传瞬间
     即确定，后续回填 materialId 也不会变化。刷新页面后仍保留。 */
  const CHAT_KEY = 'asa_student_chat_v2';    // { [rec.id]: { msgs: [] } }
  const CHAT_KEY_V1 = 'asa_student_chat_v1'; // 旧版单一记录，读取时自动迁移
  const QA_KEY = 'asa_qa_active_v1';         // 当前问答资料 id
  const GLOBAL_KEY = '__global__';           // 尚未上传任何资料时的兜底桶

  let Q = null;   // 当前问答资料 id（对应 Engine.uploads() 的 rec.id）

  /* 问答记录桶的 key：用上传记录 id，保证 materialId 回填后 key 不变 */
  function chatKey(rec) { return rec ? rec.id : GLOBAL_KEY; }

  function loadQ() {
    try { Q = localStorage.getItem(QA_KEY) || null; } catch (e) { Q = null; }
  }

  function saveQ() {
    try {
      if (Q) localStorage.setItem(QA_KEY, Q);
      else localStorage.removeItem(QA_KEY);
    } catch (e) {}
  }

  function loadChat() {
    let loaded = false;
    try {
      const raw = localStorage.getItem(CHAT_KEY);
      if (raw) {
        const parsed = JSON.parse(raw);
        if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
          M = parsed;
          loaded = true;
        }
      }
    } catch (e) { /* 忽略损坏数据 */ }
    if (!loaded) {
      M = {};
      // 兼容旧版单一问答记录：迁移到第一份资料的桶，避免历史记录丢失
      try {
        const old = localStorage.getItem(CHAT_KEY_V1);
        if (old) {
          const p = JSON.parse(old);
          if (p && Array.isArray(p.msgs) && p.msgs.length) {
            M[chatKey(Engine.uploads()[0])] = { msgs: p.msgs };
          }
        }
      } catch (e) {}
      try { localStorage.removeItem(CHAT_KEY_V1); } catch (e) {}
      saveChat();
    }
    loadQ();
  }

  function saveChat() {
    if (!M) return;
    try { localStorage.setItem(CHAT_KEY, JSON.stringify(M)); } catch (e) {}
  }

  function clearChat() {
    try {
      localStorage.removeItem(CHAT_KEY);
      localStorage.removeItem(CHAT_KEY_V1);
      localStorage.removeItem(QA_KEY);
    } catch (e) {}
    M = {}; Q = null;
  }

  /* 当前问答资料：优先使用用户点选的资料；未选或已失效时回退到第一份 */
  function qaRec() {
    const mine = Engine.uploads();
    if (!mine.length) return null;
    const rec = Q ? Engine.findMaterial(Q) : null;
    return rec || mine[0];
  }

  /* 当前资料对应的问答记录（不存在则创建） */
  function currentChat() {
    if (!M) loadChat();
    const key = chatKey(qaRec());
    if (!M[key] || !Array.isArray(M[key].msgs)) M[key] = { msgs: [] };
    return M[key];
  }

  /* 点击右侧资料行 → 切换为该资料对应的问答记录 */
  function selectQA(id) {
    if (!M) loadChat();
    const rec = Engine.findMaterial(id);
    if (!rec) return;
    Q = rec.id;
    saveQ();
    const y = window.scrollY;
    App.render();
    window.scrollTo(0, y);
  }

  function materials() {
    if (!M) loadChat();
    App.go('#/student/materials');
  }

  function materialsView() {
    if (!M) loadChat();

    const mine = Engine.uploads();
    const indexed = mine.filter(m => m.status === 'parsed' || m.status === 'indexed').length;
    const cur = qaRec();          // 当前问答资料
    const chat = currentChat();   // 该资料对应的问答记录

    /* 资料列表（整行可点选，切换到该资料对应的问答记录） */
    const rowOf = (m, removable) => `
      <div class="topic-row qa-pick${cur && cur.id === m.id ? ' is-active' : ''}"
           onclick="Student.selectQA('${m.id}')" title="点击切换到该资料的问答记录">
        <div class="topic-emoji">${fileIcon(m.kind)}</div>
        <div class="topic-main">
          <div class="topic-name">${escapeHtml(m.name)}</div>
          <div class="topic-meta">${m.kind} · ${m.size}${m.pages ? ' · ' + m.pages + ' 页' : ''} · ${m.from || '我的上传'}</div>
        </div>
        ${statusBadge(m)}
        ${Engine.materialStats(m).count > 0
          ? `<button class="btn btn-soft btn-sm" onclick="event.stopPropagation();Student.openMaterial('${m.id}')">${I('arrowRight',13)} 进入模块</button>`
          : ''}
        ${removable ? `<button class="btn btn-ghost btn-sm" onclick="event.stopPropagation();Student.rmUpload('${m.id}')">移除</button>` : ''}
      </div>`;

    /* 解析失败的资料，额外展示错误原因与重试 */
    const failedRows = mine.filter(m => m.status === 'error').map(m => `
      <div class="card-body" style="border-top:1px solid var(--border);background:var(--danger-soft, #fef2f2)">
        <div class="text-xs" style="color:var(--danger)">${I('bug',12)} <b>${escapeHtml(m.name)}</b> 解析失败：${escapeHtml(m.error || '未知错误')}</div>
      </div>`).join('');

    const fileRows = mine.length
      ? mine.map(m => rowOf(m, true)).join('')
      : `<div class="empty" style="padding:22px">还没有上传资料 · 上传后 AI 会解析并生成知识点与测验</div>`;

    /* 问答消息（只渲染当前选中资料的记录） */
    const msgs = chat.msgs.map(m => `
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
            <span class="text-xs" style="color:var(--text-3)">${cur
              ? '当前问答资料：' + escapeHtml(cur.name) + ' · 只依据该资料作答'
              : '只依据你的资料作答，并给出引用'}</span>
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
    // 每份新资料立即拥有独立的问答记录桶，并自动成为当前问答资料
    if (!M) loadChat();
    Q = rec.id;
    saveQ();
    if (!M[chatKey(rec)] || !Array.isArray(M[chatKey(rec)].msgs)) {
      M[chatKey(rec)] = { msgs: [] };
    }
    saveChat();
    App.render();

    // 1) 上传
    const up = await API.uploadMaterial(file);
    if (!up.ok) {
      if (API.isNetworkFailure(up)) {
        // 页面刷新/网络瞬断导致请求被中断：不是失败，转为可恢复状态
        Engine.markUpload(rec.id, { status: 'interrupted' });
        await resumeUpload(rec.id);
      } else {
        Engine.markUpload(rec.id, { status: 'error', error: up.error });
        UI.toast('上传失败：' + up.error, 'err');
        App.render();
      }
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
      if (API.isNetworkFailure(parsed)) {
        Engine.markUpload(rec.id, { status: 'interrupted' });
        await resumeUpload(rec.id);
      } else {
        Engine.markUpload(rec.id, { status: 'error', error: parsed.error });
        UI.toast('解析失败：' + parsed.error, 'err');
        App.render();
      }
      return;
    }
    finishParse(rec, parsed.data || {});
  }

  /* 解析结果落地：知识点去重入库、标记已解析、保证该资料的问答桶存在 */
  function finishParse(rec, data) {
    const added = Engine.ingestParsed({
      ...data,
      source: rec.name,
      material_id: data.material_id || rec.materialId,
    });
    Engine.markUpload(rec.id, {
      status: 'parsed',
      topicCount: added.length,
      pages: data.pages,
    });
    if (!M) loadChat();
    if (!M[chatKey(rec)] || !Array.isArray(M[chatKey(rec)].msgs)) {
      M[chatKey(rec)] = { msgs: [] };
    }
    saveChat();
    if (added.length) UI.toast(`已从「${rec.name}」解析出 ${added.length} 个知识点`, 'ok');
    App.render();
  }

  /* 刷新或网络瞬断后恢复一份资料。服务端解析线程不会被页面刷新取消，
     所以先查后端真实状态：已完成就直接取缓存结果（不再调 LLM），
     未完成才重新发起解析。只有后端明确报错才标记为真正的解析失败。 */
  async function resumeUpload(id) {
    const rec = Engine.uploads().find(x => x.id === id);
    if (!rec || rec.status === 'parsed' || rec.status === 'indexed' || rec.status === 'error') return;
    if (!rec.materialId) {
      // 上传阶段就被中断：File 对象已随页面销毁，只能请用户重新选择文件
      Engine.markUpload(id, { status: 'interrupted' });
      App.render();
      return;
    }
    Engine.markUpload(id, { status: 'parsing' });
    App.render();

    const info = await API.getMaterial(rec.materialId);
    if (!info.ok) {
      if (info.status === 404) {
        Engine.markUpload(id, {
          status: 'interrupted',
          error: '后端资料记录已丢失，请重新上传该文件',
        });
      } else if (API.isNetworkFailure(info)) {
        Engine.markUpload(id, { status: 'interrupted' });
      } else {
        Engine.markUpload(id, { status: 'error', error: info.error });
        UI.toast('解析失败：' + info.error, 'err');
      }
      App.render();
      return;
    }

    const cached = info.data && info.data.parsed ? info.data.result : null;
    if (cached) {
      finishParse(rec, cached);
      return;
    }

    const parsed = await API.parseMaterial(rec.materialId);
    if (!parsed.ok) {
      if (API.isNetworkFailure(parsed)) {
        Engine.markUpload(id, { status: 'interrupted' });
      } else {
        Engine.markUpload(id, { status: 'error', error: parsed.error });
        UI.toast('解析失败：' + parsed.error, 'err');
      }
      App.render();
      return;
    }
    finishParse(rec, parsed.data || {});
  }

  /* 页面加载后自动恢复全部被中断的资料（串行，避免并发打满 LLM） */
  async function resumePending() {
    const pending = Engine.uploads()
      .filter(u => u && u.status === 'interrupted')
      .map(u => u.id);
    for (const id of pending) {
      await resumeUpload(id);
    }
  }

  async function rmUpload(id) {
    const rec = Engine.uploads().find(x => x.id === id);
    // 始终请求后端清理：即使后端重启过（内存资料表已空），后端也会按
    // 资料 id / 文件名兜底删除磁盘文件与向量，避免「界面消失但数据残留」。
    // 上传中断还没回填 materialId 时，用本地记录 id 兜底（至少能按文件名清理索引）。
    const materialId = rec && (rec.materialId || rec.id);
    if (materialId) {
      const r = await API.deleteMaterial(materialId, rec && rec.name);
      if (r && !r.ok) {
        UI.toast('后端清理未完成：' + (r.error || '未知错误') + '，请确认后端已启动后重试', 'err');
      }
    }
    Engine.removeUpload(id);
    // 同步删除该资料的独立问答记录；若正选中它，切到剩余的第一份资料
    if (!M) loadChat();
    delete M[id];
    saveChat();
    if (Q === id) {
      const next = Engine.uploads()[0];
      Q = next ? next.id : null;
      saveQ();
    }
    UI.toast('已移除该资料（含索引与知识点）', 'warn');
    App.render();
  }

  function askAI() {
    const el = document.getElementById('qa-in');
    if (!el || !el.value.trim()) return;
    const q = el.value.trim();
    el.value = '';
    const chat = currentChat();
    chat.msgs.push({ who: 'me', text: q });
    saveChat();
    App.render();
    doAsk(q, chat);
  }

  function askExample(q) {
    const chat = currentChat();
    chat.msgs.push({ who: 'me', text: q });
    saveChat();
    App.render();
    doAsk(q, chat);
  }

  async function doAsk(q, chat) {
    // 优先走真实后端 RAG + LLM；后端不可用时给出明确提示
    if (!chat) chat = currentChat();
    // 发起请求时就锁定当前资料，避免等待期间切换资料导致串味
    const rec = qaRec();
    // 先插入「正在思考」占位，避免用户误以为卡死
    const pending = { who: 'ai', text: '', pending: true, sources: [] };
    chat.msgs.push(pending);
    App.render();
    const sc = document.getElementById('qa-scroll');
    if (sc) sc.scrollTop = sc.scrollHeight;

    const r = await API.ask(
      q,
      rec ? (rec.materialId || rec.id) : null,
      rec ? rec.name : null
    );
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
        <div class="topic-emoji">${kpIcon()}</div>
        <div class="topic-main">
          <div class="topic-name">${escapeHtml(x.topicName)} · ${x.actName}</div>
          <div class="topic-meta">${x.before}% → ${x.after}%</div>
        </div>
        <span class="badge ${x.delta > 0 ? 'badge-success' : (x.delta < 0 ? 'badge-danger' : 'badge-muted')}">${x.delta > 0 ? '+' : ''}${x.delta}%</span>
      </div>`).join('') || UI.empty('该范围内暂无学习记录', 'chart');

    const filterBar = `
      <div class="row row-wrap mb-16" style="gap:8px">
        <span class="text-xs" style="color:var(--text-3);margin-right:4px">资料范围：</span>
        <button class="chip ${showAll ? 'chip-active' : ''}" onclick="App.go('#/student/progress/all')">全部资料</button>
        ${mine.map(m => `
          <button class="chip ${!showAll && rec && rec.id === m.id ? 'chip-active' : ''}"
                  onclick="App.go('#/student/progress/${m.id}')">${escapeHtml(m.name)}</button>`).join('')}
      </div>`;

    /* 单份资料的滚动学习状态：第几轮、掌握到哪、Agent 排的下一步 */
    const roll = rec ? Engine.rollPlan(rec) : null;
    const rollCard = roll && roll.total ? `
      <div class="card mb-16">
        <div class="card-head">
          <span class="h-section">滚动学习</span>
          <span class="spacer"></span>
          <span class="badge badge-muted">第 ${roll.round} 轮</span>
        </div>
        <div class="card-body">
          <div class="row row-wrap" style="gap:8px">
            <span class="chip">已掌握 ${roll.mastered} / ${roll.total}</span>
            <span class="chip">待推进 ${roll.remaining}</span>
            ${roll.pendingPretest ? `<span class="chip">待 Pre-test ${roll.pendingPretest}</span>` : ''}
          </div>
          ${roll.next ? `
            <div class="row mt-16">
              <span class="text-sm" style="color:var(--text-2)">
                下一步：<b>${roll.next.activity.name}</b> · ${escapeHtml(roll.next.topic.name)}（${roll.next.topic.mastery}%）
              </span>
              <span class="spacer"></span>
              <button class="btn btn-primary btn-sm"
                      onclick="Student.start('${roll.next.activity.id}','${roll.next.topic.id}')">
                继续 ${I('arrowRight', 13)}
              </button>
            </div>` : `
            <div class="text-sm mt-16" style="color:var(--text-3)">
              ${roll.done ? '本资料已全部掌握，路径完成。' : '本轮没有可排的活动，先完成 Pre-test。'}
            </div>`}
        </div>
      </div>` : '';

    return `
      <div class="section-head">
        <span class="h-section">学习进度与掌握度</span>
        <span class="spacer"></span>
        <span class="badge badge-primary">${showAll ? '全部资料' : escapeHtml(rec.name)}</span>
      </div>

      ${filterBar}

      <div class="grid g-4 mb-16">
        ${kpi('整体掌握度', Engine.overallMastery(ts) + '%', 'focus', '', showAll ? '全部资料平均' : '本资料平均')}
        ${kpi('知识点数', ts.length, 'bookOpen', '', showAll ? '跨全部资料' : '本资料解析结果')}
        ${kpi('薄弱知识点', ts.filter(t => t.mastery < 45).length, 'bug', 'amber', '需优先关注')}
        ${kpi('已掌握', ts.filter(t => Engine.isMastered(t)).length, 'check', 'green', `共 ${ts.length} 个知识点`)}
      </div>

      ${rollCard}

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
                  <span style="font-weight:600;font-size:13.5px">${escapeHtml(t.name)}</span>
                  <span class="spacer"></span>
                  <span style="font-weight:800">${t.mastery}%</span>
                  <span class="badge ${UI.level(t.mastery).badge}">${UI.level(t.mastery).label}</span>
                </div>
                ${UI.bar(t.mastery)}
                <div class="text-xs mt-8" style="color:var(--text-3)">学习活动 ${t.attempts} 次 · 待纠正错题 ${t.errors} 道${t.lastActivity ? ' · 最近：' + escapeHtml(t.lastActivity) : ''}</div>
              </div>`).join('')}
              <div class="divider"></div>`).join('')}
            ${ts.length ? '' : UI.empty('该范围还没有知识点，先上传并解析课程资料', 'bookOpen')}
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
    // 破坏性操作：先二次确认，用户取消则完全不动任何数据
    const ok = await UI.confirm({
      title: '重置全部数据？',
      message: '该操作会清空当前学习状态，并清除后端的资料索引与已上传文件，重置后无法恢复。',
      details: [
        '各知识点掌握度、练习与复习记录',
        'Pre-test 结果与自主学习规划',
        'AI 问答与知识库聊天记录',
        '后端已上传的课程资料与向量索引',
      ],
      hint: '如需保留学习记录，请点击「取消」返回。',
      confirmText: '确认重置',
      cancelText: '取消',
      danger: true,
      icon: 'refresh',
    });
    if (!ok) {
      UI.toast('已取消重置', '');
      return;
    }

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
    UI.toast(backendOk ? '数据已重置（含后端索引）' : '数据已重置（后端未连接，索引可能未清除）', backendOk ? 'ok' : 'warn');
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

  /* 页面刷新后自动续传：延后一个 tick，等首屏渲染完成后再跑 */
  if (typeof document !== 'undefined' && typeof setTimeout === 'function') {
    const kick = () => setTimeout(resumePending, 0);
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', kick);
    } else {
      kick();
    }
  }

  return {
    dashboard, materialView, chooseMaterial, openMaterial,
    topicDetail, openTopic, activity, start, quit,
    revealAnswer,
    pretest, pretestView, pickPretest, nextPretest, skipPretest, commitPretest,
    slide, ask, retryAsk, pick, nextQ, finishPractice, flip, card, finish,
    materials, materialsView, onFiles, drop, rmUpload, selectQA, askAI, askExample,
    resumePending, resumeUpload,
    progress, reset, dismissAppend,
  };
})();
