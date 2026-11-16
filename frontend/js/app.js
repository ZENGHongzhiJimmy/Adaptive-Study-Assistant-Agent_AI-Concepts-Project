/* ============================================================
   app.js — 应用外壳与路由（学生端）
   结构：侧边栏（品牌 + 导航） / 顶栏（标题 + 用户）
   本版本只保留学生端，不包含教师/管理员角色切换。

   路由：
   #/student/dashboard                 学习主页（规划 + 功能概述 + 资料模块入口）
   #/student/material/:id              某份课程资料的学习模块
   #/student/pretest/:id               该资料的 Pre-test
   #/student/topic/:topicId            知识点详情
   #/student/activity/:type/:topicId   学习活动运行器
   #/student/materials                 课程资料 · AI 问答
   #/student/progress/:scope           学习进度（资料 id 或 all）
   ============================================================ */

const App = (() => {

  const I = UI.icon;

  /* ---------------- 导航配置 ---------------- */
  const NAV = [
    { hash: '#/student/dashboard', label: '学习主页',        icon: 'dashboard' },
    { hash: '#/student/materials', label: '课程资料 · AI 问答', icon: 'book' },
    { hash: '#/student/progress',  label: '学习进度',        icon: 'target' },
  ];

  const TITLES = {
    '#/student/dashboard': ['学习主页', '自主学习规划 · 按课程资料进入学习模块'],
    '#/student/progress':  ['学习进度', '各知识点掌握度与学习轨迹'],
    '#/student/materials': ['课程资料 · AI 问答', '上传资料，AI 解析生成 quiz 与问答'],
    '#/student/pretest':   ['Pre-test', '建立该资料的初始知识状态'],
  };

  const USER = { name: 'Student', initial: 'S', role: '学生' };

  /* 平台功能：原「学习主页 · 平台功能」四张卡片，改为侧边栏的能力说明区。
     desc 用于侧栏一行短描述，tip 是完整说明（悬停可见）。 */
  const FEATURES = [
    ['upload',   '上传与 AI 解析', 'PDF / TXT / MD 自动解析',
     'PDF / TXT / MD 上传后，LLM 自动抽取知识点、讲解与测验题'],
    ['sparkles', '自适应学习路径', '大模型 Agent 决策下一步',
     '大模型 Agent 读取掌握度、错题与本轮进度，在合法候选里决定讲解 / 练习 / 复习，并给出理由'],
    ['chat',     '课程资料 AI 问答', '只依据资料作答 · 带引用',
     'RAG 只依据你上传的资料作答，并给出可核对的引用出处'],
    ['chart',    '学习进度追踪', '掌握度与轨迹按资料记录',
     '掌握度、错误与学习轨迹按资料模块独立记录，随时回看'],
  ];

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  function decodeSeg(s) {
    try { return decodeURIComponent(s || ''); } catch (e) { return s || ''; }
  }

  /* ---------------- 动态标题 ---------------- */
  function titleFor(h) {
    if (TITLES[h]) return TITLES[h];
    if (h.startsWith('#/student/material/')) {
      const rec = Engine.findMaterial(decodeSeg(h.split('/')[3]));
      return [rec ? rec.name : '资料学习模块', '该资料的自主学习路径与课件解析知识点'];
    }
    if (h.startsWith('#/student/pretest'))   return ['Pre-test', '建立该资料的初始知识状态'];
    if (h.startsWith('#/student/progress'))  return ['学习进度', '按课程资料查看掌握度与学习轨迹'];
    if (h.startsWith('#/student/topic/'))    return ['知识点详情', '选择讲解 / 练习 / 复习活动'];
    if (h.startsWith('#/student/activity/')) return ['学习活动', '完成后自动更新掌握度并重新规划'];
    return ['学习主页', '自主学习规划 · 按课程资料进入学习模块'];
  }

  function isActive(h, navHash) {
    if (h === navHash || h.startsWith(navHash + '/')) return true;
    // 资料模块、知识点、活动、Pre-test 都归属「学习主页」这一栏目
    // 注意必须限定路径边界：material 不能误匹配 materials（课程资料 · AI 问答）
    if (navHash === '#/student/dashboard') {
      return /^#\/student\/(material|topic|activity|pretest)(\/|$)/.test(h);
    }
    return false;
  }

  /* ---------------- 路由 ---------------- */
  function view() {
    const seg = (location.hash || '#/student/dashboard').replace('#/', '').split('/');

    if (seg[0] === 'student') {
      if (seg[1] === 'activity')  return Student.activity(seg[2], decodeSeg(seg[3]));
      if (seg[1] === 'topic')     return Student.topicDetail(decodeSeg(seg[2]));
      if (seg[1] === 'material')  return Student.materialView(decodeSeg(seg[2]));
      if (seg[1] === 'pretest')   return Student.pretestView(decodeSeg(seg[2]));
      if (seg[1] === 'materials') return Student.materialsView();
      if (seg[1] === 'progress')  return Student.progress(decodeSeg(seg[2]));
      return Student.dashboard();
    }
    return Student.dashboard();
  }

  /* ---------------- Agent 快照 ----------------
     页面上的「下一步」由后端 LangGraph agent 决定，前端只做缓存渲染。
     进入页面时如果没有这个资料的快照（首次进入 / 刚解析完 / 知识点数量变了），
     就向 agent 要一次；拿到后再渲染一次。请求失败不会循环重试。 */
  let agentInflight = null;

  function routeMaterial(h) {
    const seg = h.replace('#/', '').split('/');
    if (seg[1] === 'material') return Engine.findMaterial(decodeSeg(seg[2]));
    if (seg[1] === 'topic') return Engine.materialOfTopic(Engine.topic(decodeSeg(seg[2])));
    if (seg[1] === 'activity') return Engine.materialOfTopic(Engine.topic(decodeSeg(seg[3])));
    if (seg[1] === 'pretest') return Engine.findMaterial(decodeSeg(seg[2]));
    return Engine.activeMaterial();
  }

  function needsAgent(rec) {
    const snap = Engine.agentSnapshot(rec);
    if (!snap) return true;
    // 两段式中间态：掌握度已经存下、下一步还没算完（例如正好这时刷新了页面）。
    // 不补这一次请求，页面会永远停在「正在安排下一步」的骨架上。
    if (snap.decision_pending) return true;
    return Number(snap.total) !== Engine.topicsOf(rec).length;
  }

  function ensureAgent(h) {
    const rec = routeMaterial(h);
    if (!rec || !Engine.topicsOf(rec).length) return;
    if (!needsAgent(rec)) return;
    const key = String(rec.materialId || rec.id);
    if (agentInflight === key) return;
    agentInflight = key;
    Engine.syncAgent(rec).then(snap => {
      agentInflight = null;
      // 路由已经变了就不再打扰用户（避免把 A 资料的渲染结果刷到 B 页面）
      if (snap && (location.hash || '#/student/dashboard') === h) render();
    });
  }

  /* ---------------- 渲染 ---------------- */
  function render() {
    const h = location.hash || '#/student/dashboard';
    const title = titleFor(h);
    const u = USER;
    const active = Engine.activeMaterial();
    const sidebar = `
      <aside class="sidebar">
        <div class="brand">
          <div class="brand-logo">${I('bookOpen', 17)}</div>
          <div class="brand-name">Adaptive Study Assistant</div>
        </div>
        <div class="sidebar-scroll">
          <div class="nav-label">学生菜单</div>
          ${NAV.map(n => `
            <button class="nav-item ${isActive(h, n.hash) ? 'active' : ''}" onclick="App.go('${n.hash}')">
              <span class="ico">${I(n.icon, 17)}</span>${n.label}
            </button>`).join('')}
          <div class="side-bottom">
            <div class="nav-label">平台功能</div>
            <div class="side-feats">
              ${FEATURES.map(f => `
                <div class="side-feat" title="${esc(f[1])}：${esc(f[3])}">
                  <span class="side-feat-ico">${I(f[0], 14)}</span>
                  <span class="side-feat-main">
                    <span class="side-feat-t">${f[1]}</span>
                    <span class="side-feat-d">${f[2]}</span>
                  </span>
                </div>`).join('')}
            </div>
          </div>
        </div>
        <div class="sidebar-foot">自适应学习助手 · 学生端</div>
      </aside>`;

    const materialChip = active ? `
      <div class="topbar-material" title="当前课程资料：${esc(active.name)}">
        ${I('book', 13)} <span>${esc(active.name)}</span>
      </div>` : '';

    const topbar = `
      <header class="topbar">
        <div>
          <div class="topbar-title">${esc(title[0])}</div>
          <div class="topbar-sub">${esc(title[1])}</div>
        </div>
        <div class="topbar-right">
          ${materialChip}
          <div class="user-menu">
            <button class="user-menu-trigger" onclick="App.toggleUserMenu(event)">
              <div class="row" style="gap:8px">
                <span class="avatar">${u.initial}</span>
                <div style="text-align:left">
                  <div class="u-name">${u.name}</div>
                  <div class="text-xs" style="color:var(--text-3)">${u.role}</div>
                </div>
              </div>
              <span class="user-caret"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m6 9 6 6 6-6"/></svg></span>
            </button>
            <div class="user-menu-panel" id="user-menu-panel">
              <button class="user-menu-item" onclick="Student.reset()">${I('refresh', 14)} 重置数据</button>
            </div>
          </div>
        </div>
      </header>`;

    /* 正在向 agent 要「下一步」时，页面顶部有一条进度条。
       没有它，首次进入资料页的那 3-4 秒看起来就像页面渲染坏了。
       做题页不显示（那一步不需要模型）。 */
    const waiting = (() => {
      const seg = h.replace('#/', '').split('/');
      if (seg[1] === 'activity') return false;
      const rec = routeMaterial(h);
      if (!rec || !Engine.topicsOf(rec).length) return false;
      if (Engine.state.agent && Engine.state.agent.error) return false;
      return needsAgent(rec);
    })();

    document.getElementById('app').innerHTML = `
      ${sidebar}
      <main class="main">
        ${topbar}
        <div class="content">
          <div class="content-inner page-in">
            ${waiting ? `<div class="agent-bar"></div>
              <div class="agent-wait mb-16"><span class="agent-spin"></span>
                Agent 正在按最新掌握度安排下一步…</div>` : ''}
            ${view()}
          </div>
        </div>
      </main>`;

    // 首屏先用缓存/空态渲染，agent 快照到达后再补一次（见 ensureAgent）
    ensureAgent(h);
  }

  /* ---------------- 用户菜单 ---------------- */
  function toggleUserMenu(event) {
    const panel = document.getElementById('user-menu-panel');
    if (!panel) return;
    const menu = panel.closest('.user-menu');
    const isOpen = panel.classList.contains('open');
    closeUserMenu();
    if (!isOpen) {
      panel.classList.add('open');
      if (menu) menu.classList.add('open');
      event && event.stopPropagation();
    }
  }

  function closeUserMenu() {
    const panel = document.getElementById('user-menu-panel');
    if (!panel) return;
    panel.classList.remove('open');
    const menu = panel.closest('.user-menu');
    if (menu) menu.classList.remove('open');
  }

  /* ---------------- 导航 ---------------- */
  function go(hash) {
    if (location.hash === hash) { render(); return; }
    location.hash = hash;
  }

  /* ---------------- 启动 ---------------- */
  function boot() {
    if (!location.hash) location.hash = '#/student/dashboard';
    window.addEventListener('hashchange', render);
    // 点击页面其他区域关闭用户菜单
    document.addEventListener('click', closeUserMenu);
    render();
  }

  document.addEventListener('DOMContentLoaded', boot);

  return { render, go, view, titleFor, isActive, toggleUserMenu, closeUserMenu };
})();
