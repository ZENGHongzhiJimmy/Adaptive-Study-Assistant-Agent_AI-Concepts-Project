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
    { hash: '#/student/materials', label: '课程资料 · 问答', icon: 'book' },
    { hash: '#/student/progress',  label: '学习进度',        icon: 'target' },
  ];

  const TITLES = {
    '#/student/dashboard': ['学习主页', '自主学习规划 · 按课程资料进入学习模块'],
    '#/student/progress':  ['学习进度', '各知识点掌握度与学习轨迹'],
    '#/student/materials': ['课程资料 · AI 问答', '上传资料，AI 解析生成 quiz 与问答'],
    '#/student/pretest':   ['Pre-test', '建立该资料的初始知识状态'],
  };

  const USER = { name: 'Student', initial: 'S', role: '学生' };

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
    // 注意必须限定路径边界：material 不能误匹配 materials（课程资料 · 问答）
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
          <div class="brand-name">Adaptive Study</div>
        </div>
        <div class="sidebar-scroll">
          <div class="nav-label">学生菜单</div>
          ${NAV.map(n => `
            <button class="nav-item ${isActive(h, n.hash) ? 'active' : ''}" onclick="App.go('${n.hash}')">
              <span class="ico">${I(n.icon, 17)}</span>${n.label}
            </button>`).join('')}
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

    document.getElementById('app').innerHTML = `
      ${sidebar}
      <main class="main">
        ${topbar}
        <div class="content">
          <div class="content-inner page-in">${view()}</div>
        </div>
      </main>`;
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
