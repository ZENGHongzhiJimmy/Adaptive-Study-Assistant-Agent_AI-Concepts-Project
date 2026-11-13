/* ============================================================
   学习规则回归测试（无需浏览器，直接跑 engine.js）
   运行：node tests/learning-rules.test.js   或   npm test
   覆盖：掌握度结算、错题本、复习、已掌握终态、无效题过滤、旧数据迁移
   ============================================================ */

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const JS_DIR = path.join(__dirname, '..', 'js');

/* 在隔离的 VM 里加载 data.js + engine.js，可注入一份「上次保存的状态」 */
function boot(saved) {
  const store = {};
  if (saved) store.asa_student_state_v1 = JSON.stringify(saved);
  const ctx = {
    console, Date, Math, JSON,
    localStorage: {
      getItem: k => (k in store ? store[k] : null),
      setItem: (k, v) => { store[k] = v; },
      removeItem: k => { delete store[k]; },
    },
  };
  vm.createContext(ctx);
  ['data.js', 'engine.js'].forEach(f => {
    vm.runInContext(fs.readFileSync(path.join(JS_DIR, f), 'utf8'), ctx, { filename: f });
  });
  return vm.runInContext('Engine', ctx);
}

let failed = 0;
function check(name, cond, extra) {
  if (cond) {
    console.log('  pass  ' + name);
  } else {
    failed++;
    console.log('  FAIL  ' + name + (extra ? '  → ' + extra : ''));
  }
}

const E = boot();
E.state.uploads.push({ id: 'up1', name: 'a.pdf', status: 'parsed', materialId: 'm1' });

function mk(id, name, mastery, o) {
  const t = Object.assign({
    id, name, desc: '', mastery, attempts: 0, errors: 0, pretested: true,
    explainDone: false, explainRuns: 0, mistakes: [], failStreak: 0, needHelp: false,
    src: 'a.pdf', _materialId: 'm1',
    content: {
      explain: [{ t: 'x', items: ['a'], tip: 't' }],
      quiz: [{ q: 'Q', opts: ['a', 'b'], a: 0, why: '' }],
      pretest: null,
    },
  }, o || {});
  E.state.topics.push(t);
  return t;
}

console.log('\n掌握度结算');
{
  const t = mk('t1', '已掌握', 100, { explainDone: true, explainRuns: 1 });
  E.applyResult('t1', 'explain', {});
  check('已掌握后重看讲解不会被降到 55%', E.topic('t1').mastery === 100, 'mastery=' + E.topic('t1').mastery);

  const t2 = mk('t2', '讲解封顶', 45);
  for (let i = 0; i < 5; i++) E.applyResult('t2', 'explain', {});
  check('反复学讲解最多到 55%', E.topic('t2').mastery === 55, 'mastery=' + E.topic('t2').mastery);

  const t3 = mk('t3', '空内容', 30, { content: { explain: [], quiz: [], pretest: null } });
  const r3 = E.applyResult('t3', 'explain', {});
  E.applyResult('t3', 'practice', { answers: [], total: 0, correct: 0 });
  E.applyResult('t3', 'review', {});
  check('没有内容的活动不加分', E.topic('t3').mastery === 30 && r3.delta === 0, 'mastery=' + E.topic('t3').mastery);

  const t4 = mk('t4', '全错', 50, { explainDone: true });
  E.applyResult('t4', 'practice', { answers: [{ i: 0, ok: false }], total: 1, correct: 0 });
  check('练习答错掌握度回落', E.topic('t4').mastery < 50, 'mastery=' + E.topic('t4').mastery);

  E.applyResult('t4', 'practice', { answers: [{ i: 0, ok: true }], total: 1, correct: 1 });
  check('答对后掌握度回升', E.topic('t4').mastery > 50, 'mastery=' + E.topic('t4').mastery);
}

console.log('\n错题本与复习');
{
  const t = mk('t5', '错题', 60, {
    explainDone: true,
    mistakes: [{ q: 'Q', opts: ['a', 'b'], a: 0, why: '' }],
    errors: 1,
  });
  check('错题会生成复习卡片', E.reviewCards('t5').length === 1);
  const r = E.applyResult('t5', 'review', {});
  check('复习不加掌握度', r.delta === 0 && E.topic('t5').mastery === 60, 'delta=' + r.delta);
  check('复习清空错题', E.mistakesOf(E.topic('t5')).length === 0);
  check('复习后 errors 归零', E.topic('t5').errors === 0);
}

console.log('\n规划与终态');
{
  /* 这一段单独用一个干净的知识点集合，避免前面场景的掌握度干扰排序 */
  E.state.topics.length = 0;

  mk('t6', '完成', 85, { explainDone: true });
  const p = E.plan('t6');
  check('已掌握不再推荐活动', p.activity === null && p.mastered === true);
  check('只剩已掌握的知识点时 nextPlan 返回 null', E.nextPlan('m1') === null);

  const weak = mk('t7', '薄弱', 30);
  const order = E.nextTopic('m1');
  check('优先推荐最薄弱的知识点', order && order.id === 't7');

  weak.pretested = false;
  check('未做 Pre-test 的知识点不会被推荐', E.nextTopic('m1') === null);
  weak.pretested = true;
}

console.log('\n数据清洗与 Pre-test');
{
  const added = E.ingestParsed({
    material_id: 'm2', source: 'b.pdf',
    topics: [{
      name: 'T',
      quiz: [
        { q: '无答案', opts: ['A', 'B'] },
        { q: '单选项', opts: ['A'], a: 0 },
        { q: '好题', opts: ['A', 'B'], a: 1, why: '' },
      ],
    }],
  });
  check('丢弃无法作答的题', added[0].content.quiz.length === 1 && added[0].content.quiz[0].a === 1,
    JSON.stringify(added[0].content.quiz));

  const t8 = mk('t8', '待评估', 30, { pretested: false });
  E.applyPreTest([{ topicId: 't8', correct: false }]);
  check('Pre-test 答错不记入错题本', E.topic('t8').errors === 0 && E.mistakesOf(E.topic('t8')).length === 0);
  check('Pre-test 答错起点为 20%', E.topic('t8').mastery === 20);
  check('Pre-test 后标记已评估', E.topic('t8').pretested === true);
}

console.log('\n旧数据迁移');
{
  const old = boot({
    role: 'student',
    topics: [{
      id: 'o1', name: '旧知识点', mastery: 100, attempts: 4, errors: 2, pretested: true,
      src: 'a.pdf', _materialId: 'm1',
      content: { explain: [{ t: 'x', items: [] }], quiz: [{ q: 'Q', opts: ['a', 'b'], a: 0 }], pretest: null },
    }],
    uploads: [{ id: 'u1', name: 'a.pdf', status: 'parsed', materialId: 'm1' }],
    trace: [], completed: 0, streak: 5, hasPreTest: true, loopStage: 'plan',
    lastActivity: null, activeMaterialId: null,
  });
  const o = old.topic('o1');
  check('补齐 mistakes / explainRuns / failStreak',
    Array.isArray(o.mistakes) && o.explainRuns === 0 && o.failStreak === 0);
  check('没有错题卡时清掉历史 errors', o.errors === 0);
  old.applyResult('o1', 'explain', {});
  check('旧数据重看讲解不被降级', o.mastery === 100, 'mastery=' + o.mastery);
  check('旧数据已掌握后不再推荐', old.plan('o1').activity === null);
}

console.log('\n滚动学习（整份资料按轮次推进）');
{
  /* 独立实例：三个知识点，起点都是 20%，每个 2 页讲解 + 2 道题 */
  const R = boot();
  R.state.uploads.push({ id: 'up1', name: 'a.pdf', status: 'parsed', materialId: 'm1' });
  ['A', 'B', 'C'].forEach((n, i) => {
    R.state.topics.push({
      id: 'r' + i, name: '知识点' + n, desc: '', mastery: 20, attempts: 0, errors: 0,
      pretested: true, explainDone: false, explainRuns: 0, mistakes: [], failStreak: 0, needHelp: false,
      src: 'a.pdf', _materialId: 'm1',
      content: {
        explain: [{ t: 'x', items: ['a'], tip: 't' }],
        quiz: [
          { q: 'Q1', opts: ['a', 'b'], a: 0, why: '' },
          { q: 'Q2', opts: ['a', 'b'], a: 1, why: '' },
        ],
        pretest: null,
      },
    });
  });
  const practice = (t, score) => {
    const qs = R.quizBank(t);
    const correct = Math.round(qs.length * score);
    return R.applyResult(t.id, 'practice', {
      answers: qs.map((q, i) => ({ i, ok: i < correct })), total: qs.length, correct,
    });
  };

  const r1 = R.rollPlan('m1');
  check('第 1 轮覆盖全部未掌握知识点', r1.round === 1 && r1.steps.length === 3,
    'steps=' + r1.steps.length);
  check('第 1 轮每个知识点各排一次（不重复同一知识点）',
    new Set(r1.steps.map(s => s.topic.id)).size === 3);
  check('第 1 轮全部是讲解（都还没学过讲解）',
    r1.steps.every(s => s.activity.id === 'explain'));

  /* 跟随滚动队列走到全部掌握 */
  const seq = [];
  for (let i = 0; i < 40; i++) {
    const r = R.rollPlan('m1');
    if (!r.next) break;
    seq.push(r.next.topic.name + ':' + r.next.activity.id);
    if (r.next.activity.id === 'practice') practice(r.next.topic, 1);
    else R.applyResult(r.next.topic.id, r.next.activity.id, {});
  }
  const firstRound = seq.slice(0, 3);
  check('第 1 轮依次过三个知识点，而不是把一个学到掌握',
    new Set(firstRound.map(s => s.split(':')[0])).size === 3, seq.join(' → '));
  check('滚动结束后三个知识点都达到掌握', R.state.topics.every(t => R.isMastered(t)),
    R.state.topics.map(t => t.name + '=' + t.mastery + '%').join(' '));
  check('完成后 rollPlan.done 为真', R.rollPlan('m1').done === true);
  check('完成后没有下一步', R.rollPlan('m1').next === null);
  check('轮次随轮换递增', R.rollPlan('m1').round >= 3, 'round=' + R.rollPlan('m1').round);

  /* 单知识点路径仍然可用，不受轮次影响 */
  const single = R.plan('r1');
  check('单知识点 plan() 仍然可用', !!single && single.topic.id === 'r1');

  /* 同一份资料里新解锁的知识点会进入滚动队列 */
  R.state.topics.push({
    id: 'r9', name: '新知识点', mastery: 20, attempts: 0, errors: 0, pretested: true,
    explainDone: false, explainRuns: 0, mistakes: [], failStreak: 0, needHelp: false,
    src: 'a.pdf', _materialId: 'm1',
    content: { explain: [{ t: 'x', items: ['a'], tip: 't' }], quiz: [{ q: 'Q', opts: ['a', 'b'], a: 0 }], pretest: null },
  });
  const r2 = R.rollPlan('m1');
  check('新增知识点会被纳入滚动队列', r2.steps.length === 1 && r2.steps[0].topic.id === 'r9');
  check('已掌握的知识点不会回到队列', r2.steps.every(s => s.topic.id === 'r9'));

  /* 未做 Pre-test 的知识点不进入队列 */
  R.state.topics.push({
    id: 'r10', name: '待评估', mastery: 30, attempts: 0, errors: 0, pretested: false,
    explainDone: false, explainRuns: 0, mistakes: [], failStreak: 0, needHelp: false,
    src: 'a.pdf', _materialId: 'm1',
    content: { explain: [], quiz: [], pretest: null },
  });
  const r3 = R.rollPlan('m1');
  check('未 Pre-test 的知识点不进入本轮队列',
    r3.steps.every(s => s.topic.id !== 'r10') && r3.pendingPretest === 1);
}

/* ---------------- 界面契约：默认路径不给学生「选谁先学」的决定权 ----------------
   这是产品规则，不是实现细节：自主规划路径默认只暴露一个动作（下一步），
   队列、分支按钮都不该出现；单知识点学习仍由「课件解析后的知识点」列表承担。 */
{
  const src = fs.readFileSync(path.join(JS_DIR, 'views-student.js'), 'utf8');
  check('资料模块不再展示本轮滚动队列', src.indexOf('本轮滚动队列') === -1);
  check('下一步不再有「只看这个知识点」分叉', src.indexOf('只看这个知识点') === -1);
  check('结果页不再提供「先把本知识点继续下去」', src.indexOf('继续下去') === -1);
  check('Pre-test 结束后直接进入 Agent 排好的第一步',
    /function commitPretest\(\)[\s\S]{0,600}startAct\(first\.activity\.id, first\.topic\.id\)/.test(src));
}

console.log(failed ? '\n' + failed + ' 项失败\n' : '\n全部通过\n');
process.exit(failed ? 1 : 0);
