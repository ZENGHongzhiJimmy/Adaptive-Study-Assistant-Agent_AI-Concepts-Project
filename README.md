# Adaptive Study Assistant Agent — 学生端

> **面向自主学习的自适应学习助手（学生端）**  
> 复用 `dev/ZengHongzhi` 分支的前端界面，仅保留学生端视图；后端由本项目 Python（LangGraph + ChromaDB + LLM）提供 HTTP API。

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
| 核心能力 | 学生自主上传课程资料 → LLM 自动解析并生成 quiz；学习主页提示自主学习路径；课程资料 + AI 问答；学习进度追踪 |

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
- [11. 许可证与致谢](#11-许可证与致谢)

---

## 1. 项目简介

本项目把自适应学习助手聚焦为**纯学生端**体验：学生自主学习，无需教师/管理员参与。系统围绕「学生自主上传课程资料」这一入口，形成完整闭环：

```
学生上传课程资料
    → LLM 自动解析资料、抽取知识点并生成讲解与 quiz
    → 学习主页提示自主学习路径（Agent 推荐下一步）
    → 学生完成讲解 / 练习 / 复习，系统更新掌握度并重新规划
    → 学习进度页可视化各知识点掌握度与学习轨迹
```

同时，学生可在「课程资料 · AI 问答」中就上传的资料向 AI 提问，系统基于 RAG（检索增强生成）作答并给出**可核对的引用出处**，而不是凭空编造。

**与 `dev/ZengHongzhi` 分支的关系**：本项目复用了该分支的前端界面（原生 HTML/CSS/JS 学生端视图），但**只截取学生端**，去掉了教师端（AI 生成课程、审核发布）与管理员端（平台监控、用户管理）；知识点来源从「教师发布」改为「学生自主上传资料经 LLM 解析生成」。

---

## 2. 功能特性

学生端共三个页面 + 三种学习活动：

| 页面 | 功能 |
| ---- | ---- |
| **学习主页** | 自主学习路径提示、Agent 推荐下一步（一次只推一件事）、知识点掌握度总览、最近学习记录 |
| **课程资料 · 问答** | 自主上传课程资料（PDF / TXT / MD）、AI 解析资料生成知识点与 quiz、AI 问答（带引用出处） |
| **学习进度** | 整体掌握度、各知识点掌握度条形图、完整学习轨迹 |

三种学习活动（由自适应规划 Agent 依据掌握度分派）：

| 活动 | 触发条件 | 说明 |
| ---- | ---- | ---- |
| **知识点讲解** | 掌握度低（< 45%） | 基于上传资料生成的分页讲解，可随时暂停提问 |
| **练习测验** | 有一定基础（45–80%） | 针对资料内容生成的测验题，检验掌握度 |
| **知识复习** | 反复出错 / 已达熟练 | 针对历史错误的闪卡回顾 |

自适应规划遵循 `Plan → Act → Observe → Update → Re-plan` 闭环：每次作答后按动量式公式更新掌握度，Agent 重新决定下一项活动。

---

## 3. 系统架构

```
┌─────────────────────────────────────────────────────────┐
│              前端（原生 HTML / CSS / JS）                 │
│   学习主页  │  课程资料 · 问答  │  学习进度                 │
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

数据流：学生上传资料 → 后端保存并向量化入库（ChromaDB）→ LLM 解析资料抽取知识点并生成讲解/quiz → 前端本地维护学习状态（掌握度、轨迹），问答时后端检索相关片段 + LLM 作答并附引用。

---

## 4. 前端技术栈

前端**完全采用原生 HTML / CSS / JavaScript**，无任何框架与构建步骤（零依赖，双击 `index.html` 即可运行，或经本地静态服务器访问）。

| 组件 | 技术 | 说明 |
| ---- | ---- | ---- |
| 页面结构 | **HTML5** | 单页应用外壳（`#app` 根节点 + hash 路由） |
| 样式 | **CSS3** | `styles.css` 设计系统（CSS 变量、卡片、进度条、环形进度等） |
| 交互逻辑 | **原生 JavaScript** | 模块化 IIFE：`ui.js` / `data.js` / `engine.js` / `views-student.js` / `app.js` / `api.js` |
| 路由 | **hash 路由** | `#/student/dashboard` 等，无需后端路由 |
| 状态持久化 | **localStorage** | 学习状态（掌握度、轨迹）本地保存，刷新不丢 |
| 后端通信 | **fetch API** | `js/api.js` 封装 REST 调用（`/api/*`） |
| 本地服务 | **Node.js 内置 http** | `server.js` 零依赖静态服务器（可选） |

前端脚本依赖关系（`index.html` 中的加载顺序）：

```
js/ui.js              # 图标 + 通用 UI 组件（toast / bar / ring / card）
js/data.js            # 活动类型 + 问答欢迎语（后端不可用时的演示兜底）
js/engine.js          # 学生知识模型 + 自适应规划 Agent + 上传状态
js/api.js             # 后端 HTTP API 封装
js/views-student.js   # 学生端三大页面视图
js/app.js             # 应用外壳 + hash 路由
```

> 与参考项目 `dev/ZengHongzhi` 相比，本版本去掉了 `views-instructor.js`、`views-admin.js`、角色切换逻辑，并新增 `js/api.js` 用于对接后端；`engine.js` 的问答与上传逻辑从「前端 mock」改为「调用后端 LLM + RAG」。

---

## 5. 后端技术栈

| 组件 | 技术 | 作用 |
| ---- | ---- | ---- |
| API 框架 | **FastAPI** | 暴露学生端 REST 接口（`src/api/`） |
| LLM 接入 | **OpenRouter**（OpenAI 兼容） | 解析资料抽取知识点、生成 quiz、RAG 问答 |
| 向量检索 | **ChromaDB** | 上传资料的切片向量存储与语义检索 |
| 文本向量化 | **sentence-transformers / Hashing** | 语义嵌入（正式）/ 确定性离线嵌入（测试） |
| 资料解析 | **PyMuPDF** + 纯文本 | PDF / TXT / MD 提取正文 |
| Agent 编排 | **LangGraph**（复用） | 自适应规划闭环（本学生端版本由前端 `engine.js` 承担规划，后端专注内容生成） |

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
│       ├── engine.js               # 知识模型 + 自适应规划 + 上传状态
│       ├── api.js                  # 后端 HTTP API 封装
│       ├── views-student.js        # 学生端视图
│       └── app.js                  # 应用外壳 + 路由
├── src/
│   ├── api/                        # ★ 学生端 HTTP API
│   │   ├── app.py                  # FastAPI 应用与路由
│   │   ├── services.py             # 资料解析 / quiz 生成 / RAG 问答
│   │   └── run.py                  # 启动入口
│   ├── agent/                      # Agent 编排（复用）
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
- 可运行 sentence-transformers 的内存（建议 ≥ 4 GB；否则自动降级为 hashing 离线嵌入）

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
| POST | `/api/quiz/generate` | 基于资料生成 quiz（可选独立入口） |
| POST | `/api/qa/ask` | RAG + LLM 问答，返回 `{ answer, sources }` |
| GET | `/api/progress` | 学习进度（后端占位，前端本地状态为主） |

示例（解析资料）：

```bash
curl -X POST http://127.0.0.1:8000/api/materials/parse \
  -H "Content-Type: application/json" \
  -d '{"material_id": "<上传返回的 id>"}'
```

---

## 9. 已知限制

- **LLM 免费额度限流**：默认使用 OpenRouter 免费模型（`google/gemma-4-31b-it:free`），有 50 次/天限额且可能被提供方限流（HTTP 429）。此时「解析生成 quiz」与「AI 问答」会返回明确的 503 提示；上传与 RAG 检索不受影响。免费模型 ID 会轮换，可用 `python -m src.utils.llm_client --list-free-models` 查询后只改 `config.yaml -> llm.model`。
- **学习状态存于前端**：掌握度与学习轨迹由前端 `localStorage` 维护，后端不持久化学生作答状态（`/api/progress` 为占位）。
- **嵌入后端**：默认 `auto`，本机未安装 sentence-transformers 时降级为 hashing（确定性词袋哈希，非语义嵌入）。正式语义检索需安装并固定为 `sentence_transformers`。
- **资料格式**：支持 PDF / TXT / MD；PPT 暂不支持（可先导出为 PDF）。
- **单用户**：当前为课程原型，未实现多用户认证与数据隔离。

---

## 10. 开发约定

- 前端无构建步骤，修改 `frontend/` 下文件后刷新浏览器即可生效。
- 敏感信息（`OPENROUTER_API_KEY`）只放在本地 `.env`，绝不写入代码 / README / 前端；提交前运行 `python -m src.utils.secret_guard`。
- 阈值、模型、路径统一在 `config.yaml` 维护，`.env` 只放密钥与端点。
- 提交信息建议格式：`feat:` / `fix:` / `docs:` / `exp:`。

---

## 11. 许可证与致谢

- **License**：本仓库为课程作业项目，暂未指定开源许可证。
- **前端来源**：学生端界面复用自 `Wenbin-Feng/software_group_proj` 的 `dev/ZengHongzhi` 分支（MIT License），仅保留学生端视图并改造为对接本项目后端。
- **致谢**：感谢参考项目提供的设计系统与前端实现；技术实现参考 LangGraph、ChromaDB、FastAPI、sentence-transformers、PyMuPDF 与 OpenRouter 官方文档。
