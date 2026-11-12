/* ============================================================
   app.js — 应用外壳与路由（学生端）
   结构：侧边栏（品牌 + 导航） / 顶栏（标题 + 用户）
   本版本只保留学生端，不包含教师/管理员角色切换。
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
    '#/student/dashboard': ['学习主页', '自主学习路径 · Agent 推荐下一步'],
    '#/student/progress':  ['学习进度', '各知识点掌握度与学习轨迹'],
    '#/student/materials': ['课程资料 · AI 问答', '上传资料，AI 解析生成 quiz 与问答'],
    '#/student/pretest':   ['Pre-test', '建立你的初始知识状态'],
  };

  const USER = { name: 'student1', initial: 'S', role: '学生' };

  /* ---------------- 路由 ---------------- */
  function view() {
    const seg = (location.hash || '#/student/dashboard').replace('#/', '').split('/');

    if (seg[0] === 'student') {
      if (seg[1] === 'activity') return Student.activity(seg[2], seg[3]);
      if (seg[1] === 'topic')    return Student.topicDetail(seg[2]);
      if (seg[1] === 'pretest')   return Student.pretestView();
      if (seg[1] === 'materials') return Student.materialsView();
      if (seg[1] === 'progress')  return Student.progress();
      return Student.dashboard();
    }
    return Student.dashboard();
  }

  /* ---------------- 渲染 ---------------- */
  function render() {
    const h = location.hash || '#/student/dashboard';
    const title = TITLES[h] || ['', ''];
    const u = USER;

    const sidebar = `
      <aside class="sidebar">
        <div class="brand">
          <div class="brand-logo">${I('brain', 17)}</div>
          <div class="brand-name">Adaptive Study</div>
        </div>
        <div class="sidebar-scroll">
          <div class="nav-label">学生菜单</div>
          ${NAV.map(n => `
            <button class="nav-item ${h === n.hash ? 'active' : ''}" onclick="App.go('${n.hash}')">
              <span class="ico">${I(n.icon, 17)}</span>${n.label}
            </button>`).join('')}
        </div>
        <div class="sidebar-foot">自适应学习助手 · 学生端</div>
      </aside>`;

    const topbar = `
      <header class="topbar">
        <div>
          <div class="topbar-title">${title[0]}</div>
          <div class="topbar-sub">${title[1]}</div>
        </div>
        <div class="topbar-right">
          <div class="row" style="gap:8px">
            <span class="avatar">${u.initial}</span>
            <div>
              <div class="u-name">${u.name}</div>
              <div class="text-xs" style="color:var(--text-3)">${u.role}</div>
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

  /* ---------------- 导航 ---------------- */
  function go(hash) {
    if (location.hash === hash) { render(); return; }
    location.hash = hash;
  }

  /* ---------------- 启动 ---------------- */
  function boot() {
    if (!location.hash) location.hash = '#/student/dashboard';
    window.addEventListener('hashchange', render);
    render();
  }

  document.addEventListener('DOMContentLoaded', boot);

  return { render, go, view };
})();
