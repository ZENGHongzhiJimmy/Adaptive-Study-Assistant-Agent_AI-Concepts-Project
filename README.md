# Adaptive Study Assistant Agent — 学生端

> **面向自主学习的自适应学习助手**  
> 学生自主上传课程资料，系统基于 LLM 与 RAG 自动解析并生成讲解与 quiz，提供自主学习路径推荐与 AI 问答。

![Python](https://img.shields.io/badge/Python-3.10%2B-blue)

![Frontend](https://img.shields.io/badge/Frontend-原生%20HTML%2FCSS%2FJS-orange)

![Backend](https://img.shields.io/badge/Backend-FastAPI%20%2B%20ChromaDB%20%2B%20OpenAI%20SDK-green)

![LLM](https://img.shields.io/badge/LLM-OpenRouter-red)

![License](https://img.shields.io/badge/License-Coursework-lightgrey)

| 项目信息 | 内容                                                                    |
| ---- | --------------------------------------------------------------------- |
| 项目名称 | Adaptive Study Assistant Agent — 学生端                                  |
| 项目类型 | 课程项目 / 原型系统                                                           |
| 目标用户 | 需要自主学习专业课程知识的大学生                                                      |
| 核心能力 | 学生自主上传课程资料 → LLM 自动解析并生成 quiz；学习主页按资料文件分类进入独立学习模块；课程资料 + AI 问答；学习进度追踪 |



---

## 目录

- [1. 项目简介](#1-项目简介)
- [2. 功能特性](#2-功能特性)
- [3. 系统架构](#3-系统架构)
- [4. 前端技术栈](#4-前端技术栈)
- [5. 后端技术栈](#5-后端技术栈)
- [6. 项目结构](#6-项目结构)
- [7. 快速开始](#7-快速开始)
- [8. API 接口](#8-api-接口)
- [9. 实验](#9-实验)
- [10. 已知限制](#10-已知限制)
- [11. 开发约定](#11-开发约定)
- [12. 示例输入](#12-示例输入example-inputs)
- [13. 参考文献与外部资源](#13-参考文献与外部资源references--acknowledgements)

---

## 1. 项目简介

系统围绕「学生自主上传课程资料」这一入口，形成完整闭环：

```
学生上传课程资料（每份资料 = 一个独立学习模块）
    → LLM 自动解析资料、抽取知识点并生成讲解与 quiz
    → 学习主页按「课程资料文件」分类展示模块卡片，点击选择一份资料
    → 进入该资料模块后才显示自主学习路径：Pre-test → 推荐活动 → 进度追踪
    → 模块内可展开查看课件解析后的知识点（讲解要点 / 生成测验 / 掌握度）
    → 学生完成讲解 / 练习 / 复习，系统只更新该资料的掌握度并重新规划
```

学习主页提供平台功能概览、自主学习规划与课程资料模块入口。选中资料后进入对应模块，完成 Pre-test、讲解 / 练习 / 复习、课件知识点查看与进度追踪，各模块进度独立。「课程资料 · AI 问答」基于上传资料做 RAG 问答并附引用出处；知识点讲解页内也可随时暂停提问，同样由 RAG + LLM 现场作答（以当前讲解页为上下文，答案更贴合正在学的知识点）。

---

## 2. 功能特性

学生端共三个一级页面（另有按资料进入的模块页）+ 三种学习活动：

| 页面                | 功能                                                                             |
| ----------------- | ------------------------------------------------------------------------------ |
| **学习主页**          | 平台功能概览、自主学习规划、课程资料文件卡片（知识点数 / 掌握度 / 解析状态）；点击卡片选中资料，点「进入学习模块」进入对应模块             |
| **资料学习模块**（选中资料后） | 该资料的课件解析概览、自主学习路径（先 Pre-test，再由 Agent 推荐下一步）、课件解析后的知识点（可展开查看讲解要点与生成测验）、本资料学习记录 |
| **课程资料 · 问答**     | 自主上传课程资料（PDF / TXT / MD）、AI 解析资料生成知识点与 quiz、AI 问答（带引用出处）、从资料列表直接进入模块           |
| **学习进度**          | 按资料范围筛选的整体掌握度、各知识点掌握度条形图、学习轨迹                                                  |

> **按资料隔离**：每个知识点通过 `_materialId` 绑定所属资料，Pre-test 状态、掌握度、推荐活动与学习记录都只在该资料模块内生效，不同资料之间互不干扰。

三种学习活动（由 `src/agent/policy.py` 按「错题本 + 连续失败 + 是否学过讲解」分派，不是单纯的分数阈值）：

| 活动        | 触发条件（policy.decide 的分支顺序）     | 说明                                                             |
| --------- | ----------------------------- | -------------------------------------------------------------- |
| **知识点讲解** | 连续 2 次练习正确率 < 50%（重学）／ 从未看过讲解 | 基于上传资料生成的分页讲解，可随时暂停提问（RAG + LLM 现场作答，并附引用出处）；看完最多到 55%         |
| **知识复习**  | 错题本还有 ≥ 2 道题                  | 针对历史错误的闪卡回顾，只清错题、不涨掌握度                                         |
| **练习测验**  | 上面都不满足，且题库非空                  | 针对资料内容生成的测验题；`Δ = clamp(round((正确率×100 − 掌握度)×0.6), −10, +25)` |
| **收尾**    | 掌握度 ≥ 80% 且错题清空               | 该知识点结束，不再推荐活动                                                  |

自适应规划遵循 `Plan → Act（浏览器）→ Observe → Update → Re-plan` 闭环：每次作答后由 agent 更新掌握度与错题本，再重新排出本轮下一步。

---

## 3. 系统架构

```mermaid
flowchart LR
    subgraph FE["浏览器 · 原生 HTML / CSS / JS"]
        PAGES["学习主页 · 资料学习模块<br/>问答 · 进度 · Pre-test / 学习活动"]
        STATE[("localStorage<br/>资料 / 知识点 · Agent 计划快照")]
        PAGES <--> STATE
    end

    subgraph AGENT["Agent 运行时 · src/agent/"]
        GRAPH["LangGraph<br/>observe → planner"]
        POLICY["policy.py<br/>唯一的规划 + 掌握度规则"]
        LEARN[("SQLite learner_*<br/>掌握度 · 错题本 · 轮次 · 决策轨迹")]
        GRAPH --> POLICY --> LEARN
    end

    subgraph API["FastAPI · src/api/ · /api/*"]
        UP["上传 · 解析<br/>upload / parse / quiz/generate"]
        QA["RAG 问答<br/>qa/ask"]
        AG["Agent 接口<br/>agent/plan · observe · pre-test"]
        MG["资料管理<br/>health / status / progress / delete / reset"]
    end

    subgraph CAP["能力层 · src/ingestion · retrieval · utils"]
        DOC["PyMuPDF / Hashing Embedder<br/>PDF · TXT · MD → 切片向量"]
        VEC[("ChromaDB<br/>切片向量库")]
        LLM["OpenRouter LLM<br/>OpenAI 兼容接口"]
        DOC --> VEC
    end

    FE -->|"fetch · REST"| API
    API -->|"解析 · 生成 · 检索"| CAP
    AG -->|"invoke graph"| AGENT

    classDef fe stroke:#6366f1,stroke-width:2px
    classDef api stroke:#0ea5e9,stroke-width:2px
    classDef cap stroke:#10b981,stroke-width:2px
    classDef ag stroke:#f59e0b,stroke-width:2px
    class PAGES,STATE fe
    class UP,QA,AG,MG api
    class DOC,VEC,LLM cap
    class GRAPH,POLICY,LEARN ag
```

上传 → 切片向量化入库 → LLM 生成知识点 / 讲解 / quiz → 前端按资料归属；问答时检索相关片段 + LLM 作答并附引用。

**规划只有一条路径**：页面上的「下一步」不再是前端算出来的，而是 `POST /api/agent/plan` 调用 LangGraph agent，由 `src/agent/policy.py` 决策后返回。前端只缓存与渲染这份快照（`localStorage` 里的 `agent.plans`），没有任何备用规则——后端不可用时界面显示「Agent 未连接」，不会偷偷换一套逻辑。

---

## 4. 前端技术栈

前端**完全采用原生 HTML / CSS / JavaScript**，无任何框架与构建步骤（零依赖，双击 `index.html` 即可运行，或经本地静态服务器访问）。

| 组件    | 技术                  | 说明                                                                                                          |
| ----- | ------------------- | ----------------------------------------------------------------------------------------------------------- |
| 页面结构  | **HTML5**           | 单页应用外壳（`#app` 根节点 + hash 路由）                                                                                |
| 样式    | **CSS3**            | `styles.css` 设计系统（CSS 变量、卡片、进度条、环形进度等）                                                                      |
| 交互逻辑  | **原生 JavaScript**   | 模块化 IIFE：`ui.js` / `data.js` / `engine.js` / `views-student.js` / `app.js` / `api.js`                       |
| 路由    | **hash 路由**         | `#/student/dashboard`、`#/student/material/:id`、`#/student/pretest/:id`、`#/student/progress/:scope` 等，无需后端路由 |
| 状态持久化 | **localStorage**    | 学习状态（当前资料、掌握度、轨迹）本地保存，刷新不丢                                                                                  |
| 后端通信  | **fetch API**       | `js/api.js` 封装 REST 调用（`/api/*`）                                                                            |
| 本地服务  | **Node.js 内置 http** | `server.js` 零依赖静态服务器（可选）                                                                                    |

前端脚本依赖关系（`index.html` 中的加载顺序）：

```
js/ui.js              # 图标 + 通用 UI 组件（toast / bar / ring / card）
js/data.js            # 活动类型 + 问答文案
js/engine.js          # 按资料隔离的知识模型 + Agent 计划快照（规则在服务端）
js/api.js             # 后端 HTTP API 封装（含 /api/agent/*）
js/views-student.js   # 学习主页 / 资料模块 / 问答 / 进度 / Pre-test / 活动视图
js/app.js             # 应用外壳 + hash 路由（含资料模块与资料级路由参数）
```

---

## 5. 后端技术栈

| 组件       | 技术                        | 作用                                                                |
| -------- | ------------------------- | ----------------------------------------------------------------- |
| API 框架   | **FastAPI**               | 暴露学生端 REST 接口（`src/api/`）                                         |
| LLM 接入   | **OpenRouter**（OpenAI 兼容） | 解析资料抽取知识点、生成 quiz、RAG 问答                                          |
| 向量检索     | **ChromaDB**              | 上传资料的切片向量存储与语义检索                                                  |
| 文本向量化    | **Hashing（确定性词袋哈希）**      | 无外部模型依赖，离线可复现的文本向量                                                |
| 资料解析     | **PyMuPDF** + 纯文本         | PDF / TXT / MD 提取正文                                               |
| Agent 编排 | **LangGraph**             | `observe → planner` 两个节点：先更新状态，再重新规划；`/api/agent/*` 直接 invoke 这个图 |
| 规划策略     | **`src/agent/policy.py`** | 唯一实现（讲解 / 练习 / 复习 / 收尾 + 滚动轮次），无 LLM、纯函数，实验脚本复用同一份                |
| 学习状态     | **SQLite（`learner_*` 表）** | 掌握度、错题本、轮次、决策轨迹（`learner_step`）由服务端持久化                            |

---

## 6. 项目结构

```text
Adaptive-Study-Assistant-Agent_AI-Concepts-Project/
├── README.md                       # 本文档
├── requirements.txt                # Python 依赖（含 fastapi / uvicorn）
├── .env.example                    # 密钥 / 端点模板（不含真实密钥）
├── .gitignore                      # 忽略 .env / 数据 / 向量库 / 数据库
├── config.yaml                     # 非敏感配置（唯一配置源）
├── frontend/                       # ★ 学生端前端（原生 HTML/CSS/JS）
│   ├── index.html                  # 入口
│   ├── styles.css                  # 设计系统
│   ├── server.js                   # 零依赖本地静态服务器（可选）
│   ├── package.json                # npm start 脚本
│   └── js/
│       ├── ui.js                   # 图标 + UI 组件
│       ├── data.js                 # 活动类型 + 问答文案
│       ├── engine.js               # 把 agent 快照翻译成界面状态 + 上传流程
│       ├── api.js                  # 后端 HTTP API 封装
│       ├── views-student.js        # 学习主页 / 资料模块 / 问答 / 进度等视图
│       └── app.js                  # 应用外壳 + 路由
├── src/
│   ├── api/                        # ★ 学生端 HTTP API
│   │   ├── app.py                  # FastAPI 应用与路由（资料 / 问答 / 进度）
│   │   ├── agent.py                # /api/agent/*：规划、状态更新、Pre-test、轨迹
│   │   ├── services.py             # 资料解析 / quiz 生成 / RAG 问答
│   │   └── run.py                  # 启动入口
│   ├── agent/                      # Agent 运行时
│   │   ├── policy.py               # ★ 唯一的自适应策略（决策 + 掌握度 + 滚动轮次）
│   │   ├── learner_store.py        # learner_topic / learner_roll / learner_step
│   │   ├── serve.py                # LangGraph 图 + 服务封装（产品实际调用）
│   │   ├── graph.py                # CLI 闭环（python -m src.agent.run），含 Teach 渲染
│   │   ├── planner.py              # CLI 阈值规划器（0–1 标度，实验不使用）
│   │   └── run.py                  # CLI 驱动
│   ├── ingestion/                  # PDF/TXT 解析与切片
│   ├── retrieval/                  # 向量库与嵌入
│   ├── student_model/              # CLI 侧学生状态模型 + events 日志
│   └── utils/                      # 配置 / LLM 客户端 / 密钥守卫
├── data/
│   ├── course_materials/           # 课程 PDF（不入库）
│   ├── uploads/                    # 学生上传的资料（不入库）
│   └── chroma_db/                  # ChromaDB 持久化（不入库）
└── tests/                          # 离线单元测试
```

---

## 7. 快速开始

### 7.1 环境要求

- Python 3.10+、pip
- Node.js ≥ 16（仅前端可选本地服务需要；也可直接用 Python http.server）
- OpenRouter API Key（LLM 解析 / 问答需要，填在本地 `.env`）
- 内存：无大模型加载需求（内置 hashing 嵌入，常规配置即可）

### 7.2 后端安装与启动

```bash
cd Adaptive-Study-Assistant-Agent_AI-Concepts-Project

python -m venv .venv
# Windows (PowerShell)：.venv\Scripts\Activate.ps1
# Windows (Git Bash)：source .venv/Scripts/activate
# macOS / Linux：source .venv/bin/activate

python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env        # Windows PowerShell: Copy-Item .env.example .env
```

在 `.env` 中填入真实 Key（`.env` 已被 `.gitignore` 忽略，绝不入库）：

```dotenv
OPENROUTER_API_KEY=sk-or-your-key-here
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
```

启动后端：

```bash
python -m src.api.run --port 8000
# 默认 http://127.0.0.1:8000，健康检查：
# curl http://127.0.0.1:8000/api/health
```

### 7.3 前端启动

```bash
# 方式一（推荐）：Python 静态服务器
cd frontend
python -m http.server 8080

# 方式二：Node 零依赖服务器
cd frontend
npm start        # 或 node server.js

# 方式三：直接双击 index.html（部分浏览器 file:// 下会禁用 localStorage）
```

浏览器打开 <http://localhost:8080> ，默认后端地址 `http://localhost:8000`。

> 若后端部署在其他地址，可在浏览器控制台设置 `localStorage.asa_api_base = 'http://你的后端地址'` 后刷新。

---

## 8. API 接口

| 方法     | 路径                             | 说明                                                                                                                                                                         |
| ------ | ------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| GET    | `/api/health`                  | 健康检查，返回 LLM 配置状态                                                                                                                                                           |
| POST   | `/api/materials/upload`        | 上传资料（multipart，PDF/TXT/MD）                                                                                                                                                 |
| POST   | `/api/materials/parse`         | LLM 解析资料 → 抽取知识点 + 生成讲解与 quiz；解析途中资料被删除时返回 **410**（结果整体丢弃，不写缓存）                                                                                                            |
| GET    | `/api/materials/{material_id}` | 查询资料解析状态与缓存结果（刷新后恢复）                                                                                                                                                       |
| DELETE | `/api/materials/{material_id}` | 删除指定资料：磁盘文件、ChromaDB 向量、解析缓存、知识点与 agent 学习状态一并清理；在跑的解析线程随后会看到删除标记并丢弃结果                                                                                                     |
| POST   | `/api/materials/reset`         | 重置全部数据（上传资料、课程材料、ChromaDB 索引与向量库文件），平台回到初始状态                                                                                                                               |
| POST   | `/api/quiz/generate`           | 基于资料生成 quiz（可选独立入口）                                                                                                                                                        |
| POST   | `/api/qa/ask`                  | RAG + LLM 问答，返回 `{ answer, sources, grounded, mode }`；可选 `material_id` / `material_name`（限定检索资料）与 `topic` / `focus`（知识点讲解页随堂提问：当前知识点名 + 当页讲解内容，答案按助教讲解方式组织，`mode="tutor"`） |
| GET    | `/api/progress`                | 已解析知识点列表（掌握度由 agent 在 SQLite 中维护，见 8.1）                                                                                                                                    |

`/api/quiz/generate` 与 `/api/progress` 是可选的独立入口，当前前端不调用；页面上的规划与掌握度全部来自 `/api/agent/*`。

### 8.1 Agent 接口（页面上的「下一步」唯一来源）

| 方法   | 路径                               | 说明                                                                  |
| ---- | -------------------------------- | ------------------------------------------------------------------- |
| GET  | `/api/agent/policy`              | 当前服务的策略与引擎：`{policy:"adaptive-v1", engine:"langgraph"}`             |
| POST | `/api/agent/plan`                | 重新规划：返回 `{round, steps, next, by_topic, topics}`；`next` 就是界面上的「下一步」 |
| POST | `/api/agent/observe`             | 一次活动结束后更新状态并重新规划：返回 `{result:{before,after,delta,note}, ...plan}`   |
| POST | `/api/agent/pre-test`            | 写入各知识点的起点分（答对 45% / 答错 20% / 跳过不变）                                  |
| GET  | `/api/agent/state/{material_id}` | 该资料的权威学习状态 + 最近 20 条决策轨迹                                            |
| POST | `/api/agent/reset`               | 清空某份资料（或全部）的学习状态                                                    |


`plan` 请求里的 `topics` 只在**首次接触**时用于播种（浏览器升级前已有进度不会丢）；之后以 SQLite 为准，浏览器清缓存不会清掉学习记录。

示例（解析资料）：

```bash
curl -X POST http://127.0.0.1:8000/api/materials/parse \
  -H "Content-Type: application/json" \
  -d '{"material_id": "<上传返回的 id>"}'
```

### 8.2 方法 vs 基线（报告口径）

报告里只能有一个 method。当前仓库里确实存在两套掌握度模型，但它们不是"两份没写完的实现"，而是 **method 与 baseline**，边界由 `tests/test_p9_single_method.py` 守着：

| 维度 | Method（被评估） | Baseline（仅对照） |
| ---- | ---- | ---- |
| 代码 | `src/agent/policy.py`（`adaptive-v1`） | `src/student_model/mastery.py` + `src/agent/planner.py` |
| 谁在用 | 产品 `/api/agent/*`（`serve.py`）；实验里的 `adaptive` 方法 | 仅 `src/agent/graph.py`（旧 CLI 链路，实验不使用） |
| 掌握度标度 | 0–100 整数百分比 | 0–1 浮点 |
| 更新式 | `Δ = clamp(round((正确率×100 − m) × 0.6), −10, +25)`；讲解首次 +10、重看 +2、封顶 55 | `m' = 0.7·m + 0.3·p` |
| 达标条件 | `m ≥ 80` **且**错题本为空 | `m ≥ 0.85`（无错题概念） |
| 决策输入 | 掌握度 + 错题本 + `failStreak` + 讲解次数 + 内容可用性 | 掌握度 + `recent_errors` + 上一步活动 |
| 阈值来源 | 代码常量，随每次规划下发（`mastered_at` / `explain_cap`） | `config.yaml` 的 `mastery:` / `planner:` 段 |
| 报告角色 | **method** | **遗留 CLI 实现（非实验基线）** |

> 实验的三条基线（LLM-only / RAG / Fixed-strategy）定义在 `experiments/methods.py`，是 `(content, planner)` 配对；其中固定课程表用 `methods.CyclicPlanner`，**不是** `planner.py`。报告口径以 `policy.py` + `methods.py` 这一条链路为准。

两条纪律：

1. `config.yaml` 的 `mastery:` 与 `planner:` 两段已显式标注 **BASELINE**，且不含 method 的任何常量——method 的数字只在 `policy.py` 里有一份。
2. 前端不保存任何阈值：`isMastered()` 读快照的 `by_topic[topic_id].mastered`，文案里的百分比读 `mastered_at`。所以界面上的"已掌握"与报告里的判定永远是同一个函数算的。

---

## 9. 实验

实验分两步：先把课程讲义冻结成题库与两种讲解条件，再离线跑四个 tutor 的等预算对比。两步各有一个脚本和一个产物：

| 步骤 | 脚本 | 内容 | 产物 |
| ---- | ---- | ---- | ---- |
| 内容准备（先） | `experiments/content.py` | 两种讲解条件的生成与打分，16 次模型调用 | `data/materials.json` |
| 等预算对比（后） | `experiments/run_experiment.py` | 四个 tutor 对比，160 次会话 | `results/e1_main.json` + 6 张图 |

内容准备是对比实验的输入：模拟学生把讲解带来的增益乘以算出的 `coverage`，所以先有覆盖率的测量，才有 LLM-only 与 RAG 的差距。对比实验本身离线运行，不联网、不需要 API Key。

### 9.1 数据集（两步共用）

题库不是手写示例，而是用本产品的解析链路跑出来的。课程发的实验讲义已随仓库提供，一条命令即可复现：

```bash
python -m experiments.build_bank     # 默认读 experiments/data/source/Experiment(Lecture 1 Intro 2026).pdf
```

该命令按页分片调用 `src/api/services.py` 的 `EXTRACT_SYSTEM` / `EXTRACT_USER`（与网页上传同一套提示词），输出 `experiments/data/parse_raw.json`。本次从 68 页讲义得到 **20 个知识点、56 道题**，每条带页码。分片是必要的：免费模型单次补全约 1 万字符即被截断（实测 `Unterminated string`）。

> 课件版权归课程与授课教师所有，随仓库提供仅用于实验复现与课程评审，不得再分发。仓库若转为公开，先删除 `experiments/data/source/`：`parse_raw.json` 已含带页码的摘录。

`parse_raw.json` 只是来源凭证，实验**只读**人工整理后的 `experiments/data/bank.json`：

| 项目 | 数值 |
| ---- | ---- |
| 知识点 | 8 个（AI 定义 / ML 的 `<P,T,E>` / 深度学习 / 大数据 4V / 数据驱动转向 / AI 任务与应用 / 回归 vs 分类 / ML 三分类） |
| 练习题 | 32 道（每点 4 道，供练习活动使用） |
| 探针题 | 24 道（每点 3 道，只用于前测/后测，从不出现在练习中） |
| 难度 | 每题手工标注 0–1，供模拟学生计算答对概率 |
| 校验 | id 唯一、答案下标合法、练习与探针互不重叠（`experiments/bank.py`） |

被丢弃的是课程行政页（roadmap、物流、关键词趋势）与 5 道残缺题。

### 9.2 两种内容条件（内容准备）

proposal 的三条基线里有两条差的是**讲解从哪来**，不是怎么排活动。题库只有题、没有讲解，所以这一层必须单独生成：

```bash
python -m experiments.content            # 生成缺失的部分
python -m experiments.content --force    # 全部重新生成
```

对 8 个知识点，用同一任务、同一模型（temperature 0）各生成一次：

| 条件 | 提示词 | 上下文 |
| ---- | ---- | ---- |
| `rag` | `TEACH_SYSTEM`（只用给出的讲义摘录） | 检索到的 3 段讲义原文 |
| `llm_only` | 换成"你没有课件，凭自身知识讲，不要编造页码" | 无 |

检索优先走产品自己的 Chroma 检索器；干净 clone 里通常没有索引，因此回退到同一份 PDF 的 TF-IDF 索引，切块参数与产品一致（800 字符、100 重叠）。实际使用的后端写入 `materials.json` 的 `retrieval_backend`（本次为 `tfidf-chunks`）。

生成结果与两项离线打分冻结在 `experiments/data/materials.json`：

| 指标 | 定义 | LLM-only | RAG |
| ---- | ---- | ---- | ---- |
| `coverage` | 讲解覆盖"探针题考点词"的比例 | 0.274 | **0.439** |
| `grounding` | 讲解与检索段落的最高 TF-IDF 余弦 | 0.141 | **0.289** |
| tokens / 知识点 | 生成成本 | 568 | 1040 |
| 平均延迟 | 单次调用 | 3.2 s | 3.0 s |

`coverage` 是主实验实际消费的数；`grounding` 解释它为什么更高——RAG 的讲解更贴讲义措辞。两者在个别知识点上会打架（`what is AI` 覆盖率持平但 grounding 差一倍），图 5 里原样保留。

内容准备的样本是 8 个知识点、每条件只生成一次，因此它是**描述性对比**，不报显著性或置信区间。

### 9.3 四个 tutor = 内容源 × 调度器

| 方法 | 内容源 | 调度 | 相对上一行多出的能力 |
| ---- | ---- | ---- | ---- |
| `llm_only` | LLM-only | 每点 1 讲解 + 1 练习 | —（proposal 的 LLM-only Tutor） |
| `rag` | RAG | 每点 1 讲解 + 1 练习 | 检索（proposal 的 RAG Tutor） |
| `fixed` | RAG | 讲解→练习→练习，固定循环 | 教学流程（proposal 的 Fixed-strategy Tutor） |
| `adaptive` | RAG | `policy.plan_material()` | 读学生状态（本方法） |

相邻两行只差一个变量，所以差值可归因：`llm_only → rag` 是检索，`rag → fixed` 是教学流程，`fixed → adaptive` 是自适应。

`adaptive` 直接调用 `src/agent/policy.py`，没有复制一份逻辑（`tests/test_p10_experiment.py` 用 monkeypatch 校验这次调用）。

### 9.4 一次会话怎么跑

`experiments/simulator.py` 的模拟学生持有每个知识点各自的私有能力 θ，Agent 看不到它，只能看到答对/答错。答对概率由能力与题目难度之差决定，再叠加 8% 手滑与 20% 蒙对。

一次会话：前测（每点 1 道探针定起点）→ 循环「调度器选活动 → 学生现场作答 → `policy.apply_result` 更新掌握度 → 重新规划」→ 到预算或全部掌握为止 → 后测（同一组探针题的期望得分）。

讲解增益乘以该知识点的 `coverage`；练习增益不缩放，因为四个方法共用同一套练习题，题目质量固定，只有讲解在变。

### 9.5 主实验结果

```bash
python -m experiments.run_experiment --seeds 8 --budget 32
```

160 次会话（4 方法 × 5 画像 × 8 种子）约 2 秒完成，结果写入 `experiments/results/e1_main.json`：

```
method          pre   post    gain   nGain  learned  steps    eff  toTarget    cov    MAE
llm_only      0.501  0.624   0.123   0.286      3.5   31.3  0.116      25.8  0.274  0.093
rag           0.501  0.644   0.143   0.324      3.7   31.3  0.123      25.8  0.439  0.095
fixed         0.501  0.664   0.163   0.367      4.1   31.0  0.140      25.8  0.439  0.086
adaptive      0.501  0.672   0.171   0.377      4.1   30.7  0.143      25.8  0.439  0.097
```

配对差值（同画像同种子，bootstrap 95% CI）：

| 对照 | Δgain | 95% CI | 胜场 |
| ---- | ---- | ---- | ---- |
| LLM-only Tutor | +0.049 | [+0.040, +0.057] | 39/40 |
| RAG Tutor | +0.029 | [+0.023, +0.035] | 38/40 |
| Fixed-strategy Tutor | +0.008 | [+0.003, +0.013] | 26/40 |

1. 检索是单项收益最大的一步：同样调度、同样题目，只把讲解换成检索版，增益 0.123 → 0.143。
2. 教学流程的收益与之相当：加上讲解→练习→练习的固定循环后升到 0.163。
3. 自适应再进一步但幅度小：0.163 → 0.171，胜场 26/40。在 8 个难度相近的知识点上，固定流程已接近上限；自适应的价值更多体现在少花 0.3 个活动收工（30.7 vs 31.0）。
4. 专长反转成立：`struggling` 下 adaptive 0.122、LLM-only 0.063；`strong` 下四者几乎打平（0.068–0.076）。

`activities_to_target` 四个方法都是 25.8——"全部 8 点达到 0.75"由学生能力上限决定，不区分方法，所以效率改用 `eff`。

### 9.6 指标定义

| 指标 | 定义 |
| ---- | ---- |
| `pre` / `post` | 探针题期望得分均值（0–1）；`post` 即 Final Mastery |
| `learning_gain` | `post − pre` |
| `nGain` | 归一化增益 `(post − pre) / (1 − pre)` |
| `learned` | 后测 ≥ 0.75 的知识点个数（与 Agent 内部达标线无关，避免自证） |
| `eff` | `learned / steps`，每花一个活动换来多少达标知识点 |
| `toTarget` | 全部知识点达到 0.75 所需活动数；未达到记满预算，会被截断 |
| `cov` | 该方法所用讲解的平均覆盖率 |
| `MAE` | `|Agent 掌握度 − 学生真实水平|` |
| `tokens` / 延迟 | 生成整套讲解的 LLM 成本，来自 `materials.json` |

### 9.7 出图

```bash
python -m experiments.plots
```

只读 `e1_main.json` 与 `materials.json`，不重新模拟，因此图与提交的 JSON 一一对应。6 张 300 dpi PNG 写入 `experiments/results/figures/`，英文图注为同目录 `CAPTIONS.md`：

| 文件 | 内容 | 对应论点 |
| ---- | ---- | ---- |
| `fig1_learning_gain.png` | 四方法学习增益 + 95% CI | 主结果 |
| `fig2_learning_curve.png` | 探针分数随活动数的曲线 | 过程而不仅是终点 |
| `fig3_gain_by_profile.png` | 按画像拆分的增益 | 专长反转 |
| `fig4_paired_delta.png` | 配对差值 + bootstrap CI | 显著性 |
| `fig5_content_quality.png` | 覆盖率 / 贴讲义程度 / LLM 成本 | 检索买到了什么 |
| `fig6_activity_mix.png` | 各方法活动配比 | 机制 |

`fig2` 中提前结束（全部掌握）的会话在后续 checkpoint 没有读数，脚本把最终分数结转（LOCF）到剩余 checkpoint；否则缺数据的恰好是学得最快的会话，会让最先停的方法看起来最差。

---

## 10. 已知限制

实验层面（报告必须写明的部分）：

- **模拟学生不是真人**：能力 θ、手滑 8%、蒙对 20%、三条增益假设（讲解收益递减、练习按成功检索计收益、复习只在有错时有效，见 `experiments/simulator.py` 模块 docstring）都是模型假设。实验检验的是"这些假设下四种方法谁更好"，不是人类学习曲线。
- **内容质量按线性方式接入**：讲解增益乘以 `coverage` 是一次线性缩放，未校准真实学习者的"覆盖率→掌握度"映射，也未做敏感性扫描。该映射若次线性，RAG 与 LLM-only 的差距被高估；若超线性则被低估。
- **`coverage` 是词面指标**：数的是"探针考点关键词是否出现在讲解里"，同义改写会漏记，因此绝对值偏低（0.27–0.44）不代表讲解只有三成有效。它适合比较两个条件，不适合当绝对质量分。
- **每个条件只生成一次**：temperature 0 下每个知识点只有一条讲解，未估计生成方差；16 次调用中出现过 3 次拒答/截断（已重试）。
- **RAG 检索可能不是产品那套**：干净 clone 没有 Chroma 索引，脚本回退到同一份 PDF 的 TF-IDF 分块索引；只有 `retrieval_backend` 为 `product-chroma` 时才是产品链路。
- **难度由人工标注**：`bank.json` 的难度是手工标注，不是实证校准；`policy.decide()` 不按难度选题，只选活动类型。因此实验衡量的是 activity-level adaptation，不是 difficulty-level adaptation。
- **`toTarget` 被预算截断**：四个方法都停在 25.8 左右，达不到"全部 8 点 ≥ 0.75"就记满 32，所以效率指标改用 `eff`。
- **自适应的增量很小**：对固定流程只有 +0.008（26/40 胜）。在 8 个难度相近的知识点上，"固定课程表 + 讲解→练习→练习"已接近上限；这是结论的一部分，不是失败。
- **主实验离线运行**：讲解由内容准备阶段生成一次后冻结在 `materials.json`，模拟阶段不联网、不需要 API Key。

产品层面：

- **LLM 模型配置与限流**：默认主模型为 OpenRouter 免费模型 `(baidu/ernie-5.5-flash-preview:free)`，`fallback_models` 中配置付费模型 `(deepseek/deepseek-v4.1-flash)` 兜底。主模型正常时只调用免费模型；主模型不可用（下架或 429）时按序切换。可在 `config.yaml -> llm` 更换 `model` 与 `fallback_models`。
- **学习状态存于后端 SQLite**：掌握度、错题本、滚动轮次与决策轨迹由 `learner_*` 表持久化，浏览器只缓存最近一次 agent 快照用于渲染。清浏览器数据不丢记录，但换机器/换浏览器共享同一份状态（单用户原型，未做账号隔离）。
- **嵌入后端**：固定为 `hashing`（确定性词袋哈希 + L2 归一化 cosine），无外部模型依赖、离线可复现；属于词面匹配而非语义嵌入，同义改写与跨语言检索能力有限。
- **资料格式**：支持 PDF / TXT / MD；PPT 暂不支持（可先导出为 PDF）。
- **单用户**：课程原型，未实现多用户认证与数据隔离。

---

## 11. 开发约定

- 前端无构建步骤，修改 `frontend/` 下文件后刷新浏览器即可生效。
- 敏感信息（`OPENROUTER_API_KEY`）只放在本地 `.env`，绝不写入代码 / README / 前端；提交前运行 `python -m src.utils.secret_guard`。
- 基础设施参数（模型、路径、检索、种子）在 `config.yaml`；method 的阈值与常量只在 `src/agent/policy.py`，不复制到 YAML。`.env` 只放密钥与端点。
- 提交信息建议格式：`feat:` / `fix:` / `docs:` / `exp:`。

---

## 12. 示例输入（Example inputs）

课程 spec 要求源码包附带可直接试跑的输入。以下三种都不需要额外下载数据：

**A. 产品（网页 / HTTP）**：把仓库自带的 `experiments/data/source/Experiment(Lecture 1 Intro 2026).pdf` 在页面「资料上传」上传后点「解析」。等价的 API 调用（把 `m_abc123` 换成 upload 返回的 `material_id`）：

```bash
curl -X POST http://127.0.0.1:8000/api/materials/upload \
  -F "file=@experiments/data/source/Experiment(Lecture 1 Intro 2026).pdf"
curl -X POST http://127.0.0.1:8000/api/materials/parse \
  -H "Content-Type: application/json" -d '{"material_id":"m_abc123"}'
curl -X POST http://127.0.0.1:8000/api/agent/plan \
  -H "Content-Type: application/json" -d '{"material_id":"m_abc123"}'
```

**B. 实验（离线，推荐）**：实验输入是两个已冻结的 JSON（`bank.json` + `materials.json`），不需要 API Key：

```bash
python -m experiments.bank --check                  # 打印 8 个知识点 / 56 道题及答案
python -m experiments.run_experiment --seeds 8 --budget 32
python -m experiments.plots
```

**C. 最小可复现单元**：`tests/test_p10_experiment.py` 用 `bank.json` 的单个知识点加一个 seed 构造 `SimulatedStudent`，`pytest -q` 即可观察前测 → 答题 → 能力增长 → 后测的完整链路。

---

## 13. 参考文献与外部资源（References / Acknowledgements）

- COMP 5511 Artificial Intelligence Concepts, *Final Project Specifications*, Sem 1 2026/27（课程项目要求与评分口径）。
- W. Fan, *Lecture 1 – Artificial Intelligence: An Introduction*, COMP 5511, 2026（实验讲义；版权与使用限制见 `experiments/data/README.md`）。
- Ning, Liangbo et al., *A Survey of WebAgents: Towards Next-Generation AI Agents for Web Automation with Large Foundation Models*, arXiv:2503.23350, 2025（课程说明 [1]）。
- 代码依赖：LangGraph（agent 编排）、FastAPI / Uvicorn（HTTP API）、ChromaDB（向量库）、PyMuPDF（PDF 解析）、scikit-learn（TF-IDF 打分）、matplotlib（出图）、OpenAI SDK / OpenRouter（LLM 调用）。版本约束见 `requirements.txt`。

> 报告正文的 References 需按引用格式单独补齐，并覆盖正文实际引用到的每一篇文献与数据集；本表只列出代码包使用的外部资源。
