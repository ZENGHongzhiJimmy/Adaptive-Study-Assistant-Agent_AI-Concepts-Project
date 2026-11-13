# Adaptive Study Assistant Agent — 学生端

> **面向自主学习的自适应学习助手**  
> 学生自主上传课程资料，系统基于 LLM 与 RAG 自动解析并生成讲解与 quiz，提供自主学习路径推荐与 AI 问答。

![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![Frontend](https://img.shields.io/badge/Frontend-原生%20HTML%2FCSS%2FJS-orange)
![Backend](https://img.shields.io/badge/Backend-FastAPI%20%2B%20LangGraph%20%2B%20ChromaDB-green)
![LLM](https://img.shields.io/badge/LLM-OpenRouter-red)
![License](https://img.shields.io/badge/License-Coursework-lightgrey)

| 项目信息 | 内容 |
| ---- | ---- |
| 项目名称 | Adaptive Study Assistant Agent — 学生端 |
| 项目类型 | 课程项目 / 原型系统 |
| 目标用户 | 需要自主学习专业课程知识的大学生 |
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
- [9. 已知限制](#9-已知限制)
- [10. 开发约定](#10-开发约定)

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

| 页面 | 功能 |
| ---- | ---- |
| **学习主页** | 平台功能概览、自主学习规划、课程资料文件卡片（知识点数 / 掌握度 / 解析状态）；点击卡片选中资料，点「进入学习模块」进入对应模块 |
| **资料学习模块**（选中资料后） | 该资料的课件解析概览、自主学习路径（先 Pre-test，再由 Agent 推荐下一步）、课件解析后的知识点（可展开查看讲解要点与生成测验）、本资料学习记录 |
| **课程资料 · 问答** | 自主上传课程资料（PDF / TXT / MD）、AI 解析资料生成知识点与 quiz、AI 问答（带引用出处）、从资料列表直接进入模块 |
| **学习进度** | 按资料范围筛选的整体掌握度、各知识点掌握度条形图、学习轨迹 |

> **按资料隔离**：每个知识点通过 `_materialId` 绑定所属资料，Pre-test 状态、掌握度、推荐活动与学习记录都只在该资料模块内生效，不同资料之间互不干扰。

三种学习活动（由自适应规划 Agent 依据掌握度分派）：

| 活动 | 触发条件 | 说明 |
| ---- | ---- | ---- |
| **知识点讲解** | 掌握度低（< 45%） | 基于上传资料生成的分页讲解，可随时暂停提问（提问由 RAG + LLM 现场作答，并附引用出处） |
| **练习测验** | 有一定基础（45–80%） | 针对资料内容生成的测验题，检验掌握度 |
| **知识复习** | 反复出错 / 已达熟练 | 针对历史错误的闪卡回顾 |

自适应规划遵循 `Plan → Act → Observe → Update → Re-plan` 闭环：每次作答后按动量式公式更新掌握度，Agent 重新决定下一项活动。

---

## 3. 系统架构

```
┌─────────────────────────────────────────────────────────┐
│              前端（原生 HTML / CSS / JS）                 │
│  学习主页（按课程资料分类）→ 资料学习模块                  │
│  课程资料 · 问答  │  学习进度  │  Pre-test / 学习活动       │
│   (index.html + styles.css + js/*)                       │
└────────────────────────┬────────────────────────────────┘
                         │  fetch (REST)
                         ▼
┌─────────────────────────────────────────────────────────┐
│              后端 API（FastAPI · src/api/）              │
│  /materials/upload · parse · /quiz/generate · /qa/ask     │
└──────────┬──────────────────────────┬───────────────────┘
           │                          │
           ▼                          ▼
┌──────────────────────┐   ┌──────────────────────────────┐
│  LLM (OpenRouter)     │   │  RAG 知识库 (ChromaDB)        │
│  解析资料 / 生成 quiz  │   │  上传资料切片 + 向量检索        │
│  问答生成             │   │  (Hashing / sentence-tf)      │
└──────────────────────┘   └──────────────────────────────┘
```

数据流：学生上传资料 → 后端保存并向量化入库（ChromaDB）→ LLM 解析资料抽取知识点并生成讲解/quiz → 前端按资料 id 把知识点归入对应模块，并本地维护该模块的学习状态（Pre-test、掌握度、轨迹）→ 问答时后端检索相关片段 + LLM 作答并附引用。

---

## 4. 前端技术栈

前端**完全采用原生 HTML / CSS / JavaScript**，无任何框架与构建步骤（零依赖，双击 `index.html` 即可运行，或经本地静态服务器访问）。

| 组件 | 技术 | 说明 |
| ---- | ---- | ---- |
| 页面结构 | **HTML5** | 单页应用外壳（`#app` 根节点 + hash 路由） |
| 样式 | **CSS3** | `styles.css` 设计系统（CSS 变量、卡片、进度条、环形进度等） |
| 交互逻辑 | **原生 JavaScript** | 模块化 IIFE：`ui.js` / `data.js` / `engine.js` / `views-student.js` / `app.js` / `api.js` |
| 路由 | **hash 路由** | `#/student/dashboard`、`#/student/material/:id`、`#/student/pretest/:id`、`#/student/progress/:scope` 等，无需后端路由 |
| 状态持久化 | **localStorage** | 学习状态（当前资料、掌握度、轨迹）本地保存，刷新不丢 |
| 后端通信 | **fetch API** | `js/api.js` 封装 REST 调用（`/api/*`） |
| 本地服务 | **Node.js 内置 http** | `server.js` 零依赖静态服务器（可选） |

前端脚本依赖关系（`index.html` 中的加载顺序）：

```
js/ui.js              # 图标 + 通用 UI 组件（toast / bar / ring / card）
js/data.js            # 活动类型 + 问答欢迎语（后端不可用时的演示兜底）
js/engine.js          # 按资料隔离的知识模型 + 自适应规划 Agent + 上传状态
js/api.js             # 后端 HTTP API 封装
js/views-student.js   # 学习主页 / 资料模块 / 问答 / 进度 / Pre-test / 活动视图
js/app.js             # 应用外壳 + hash 路由（含资料模块与资料级路由参数）
```

---

## 5. 后端技术栈

| 组件 | 技术 | 作用 |
| ---- | ---- | ---- |
| API 框架 | **FastAPI** | 暴露学生端 REST 接口（`src/api/`） |
| LLM 接入 | **OpenRouter**（OpenAI 兼容） | 解析资料抽取知识点、生成 quiz、RAG 问答 |
| 向量检索 | **ChromaDB** | 上传资料的切片向量存储与语义检索 |
| 文本向量化 | **Hashing（确定性词袋哈希）** | 无外部模型依赖，离线可复现的文本向量 |
| 资料解析 | **PyMuPDF** + 纯文本 | PDF / TXT / MD 提取正文 |
| Agent 编排 | **LangGraph** | 自适应规划闭环（由前端 `engine.js` 承担规划，后端专注内容生成） |

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
│       ├── data.js                 # 活动类型 + 演示兜底数据
│       ├── engine.js               # 按资料隔离的知识模型 + 自适应规划 + 上传状态
│       ├── api.js                  # 后端 HTTP API 封装
│       ├── views-student.js        # 学习主页 / 资料模块 / 问答 / 进度等视图
│       └── app.js                  # 应用外壳 + 路由
├── src/
│   ├── api/                        # ★ 学生端 HTTP API
│   │   ├── app.py                  # FastAPI 应用与路由
│   │   ├── services.py             # 资料解析 / quiz 生成 / RAG 问答
│   │   └── run.py                  # 启动入口
│   ├── agent/                      # Agent 编排
│   ├── ingestion/                  # PDF/TXT 解析与切片
│   ├── retrieval/                  # 向量库与嵌入
│   ├── student_model/              # 学生状态模型
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

浏览器打开 http://localhost:8080 ，默认后端地址 `http://localhost:8000`。

> 若后端部署在其他地址，可在浏览器控制台设置 `localStorage.asa_api_base = 'http://你的后端地址'` 后刷新。

---

## 8. API 接口

| 方法 | 路径 | 说明 |
| ---- | ---- | ---- |
| GET | `/api/health` | 健康检查，返回 LLM 配置状态 |
| POST | `/api/materials/upload` | 上传资料（multipart，PDF/TXT/MD） |
| POST | `/api/materials/parse` | LLM 解析资料 → 抽取知识点 + 生成讲解与 quiz |
| DELETE | `/api/materials/{material_id}` | 删除指定资料 |
| POST | `/api/materials/reset` | 重置全部数据（上传资料、课程材料、ChromaDB 索引与向量库文件），平台回到初始状态 |
| POST | `/api/quiz/generate` | 基于资料生成 quiz（可选独立入口） |
| POST | `/api/qa/ask` | RAG + LLM 问答，返回 `{ answer, sources, grounded, mode }`；可选 `material_id` / `material_name`（限定检索资料）与 `topic` / `focus`（知识点讲解页随堂提问：当前知识点名 + 当页讲解内容，答案按助教讲解方式组织，`mode="tutor"`） |
| GET | `/api/progress` | 学习进度，返回 `{ topics, overall_mastery }` |

示例（解析资料）：

```bash
curl -X POST http://127.0.0.1:8000/api/materials/parse \
  -H "Content-Type: application/json" \
  -d '{"material_id": "<上传返回的 id>"}'
```

---

## 9. 已知限制

- **LLM 模型配置与限流**：默认主模型为 OpenRouter 免费模型 `(baidu/ernie-5.5-flash-preview:free)`，并在 `fallback_models` 中配置付费模型 `(deepseek/deepseek-v4.1-flash)` 作为兜底。主模型正常时只调用免费模型、不额外消耗付费额度；当主模型不可用（如被下架或限流返回 404/429）时，自动按序切换到付费兜底模型完成解析与问答。模型均可根据个人需求在 `config.yaml -> llm` 段自由更换（改 `model` 与 `fallback_models`）。
- **学习状态存于前端**：掌握度与学习轨迹由前端 `localStorage` 维护，后端不持久化学生作答状态。
- **嵌入后端**：固定为 `hashing`（确定性词袋哈希 + L2 归一化 cosine），无外部模型依赖、离线可复现；属于词面匹配而非语义嵌入，同义改写与跨语言检索能力有限。
- **资料格式**：支持 PDF / TXT / MD；PPT 暂不支持（可先导出为 PDF）。
- **单用户**：当前为课程原型，未实现多用户认证与数据隔离。

---

## 10. 开发约定

- 前端无构建步骤，修改 `frontend/` 下文件后刷新浏览器即可生效。
- 敏感信息（`OPENROUTER_API_KEY`）只放在本地 `.env`，绝不写入代码 / README / 前端；提交前运行 `python -m src.utils.secret_guard`。
- 阈值、模型、路径统一在 `config.yaml` 维护，`.env` 只放密钥与端点。
- 提交信息建议格式：`feat:` / `fix:` / `docs:` / `exp:`。
