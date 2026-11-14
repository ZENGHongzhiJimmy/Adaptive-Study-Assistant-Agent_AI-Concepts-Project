# 实验数据集（E1）

这个目录是 E1 实验的全部数据：一份课件原件、一份模型抽取的原始结果、一份人工整理后的题库。三者是**同一条链路**，任何一步都能重跑。

```
experiments/data/
├── README.md                       ← 本文件：来源、结构、复现方式、使用限制
├── source/
│   └── Experiment(Lecture 1 Intro 2026).pdf   ← 课程发的实验用讲义（68 页，9.7 MB）
├── parse_raw.json                  ← 产品 EXTRACT 流程读这份 PDF 的输出（20 个知识点）
├── bank.json                       ← 实验实际使用的题库（8 个知识点 × 7 题 = 56 题）
└── materials.json                  ← 两种讲解条件（LLM-only / RAG）的原文与打分
```

## 1. 数据来源

| 项 | 值 |
| --- | --- |
| 课程 | COMP 5511 Artificial Intelligence Concepts |
| 讲义 | Lecture 1 – Artificial Intelligence: An Introduction |
| 授课 | Prof. Wenqi Fan, Department of Computing, The Hong Kong Polytechnic University |
| 学期 | Semester 1, 2026/27 |
| 页数 | 68 |
| 仓库内文件 | `source/Experiment(Lecture 1 Intro 2026).pdf`（保持课程发放时的原名） |
| 抽取日期 | 2026-11-14 |

选这一讲是因为它同时包含**定义类**（什么是 AI、ML、DL）、**对比类**（回归 vs 分类）、**分类体系类**（ML taxonomy、Big Data 4V）和**历史/应用类**内容，足以构造出难度分层的题目，而不是一讲只有名词解释。

## 2. 从 PDF 到题库

```
Experiment(Lecture 1 Intro 2026).pdf
        │  python -m experiments.build_bank --pdf ...
        │  按页切片（3200 字符/片，共 5 片），逐片调用产品自己的
        │  EXTRACT_SYSTEM / EXTRACT_USER prompt
        ▼
parse_raw.json        20 个知识点，每个含讲解要点 + 选择题
        │  人工筛选与补题
        │  · 丢掉行政性内容（课程安排、roadmap、ICLR 关键词趋势）
        │  · 丢掉 5 道解析异常题（题干为空 / 答案下标越界）
        │  · 保留 8 个可出题的知识点，补足每点 4 道练习 + 3 道探针
        ▼
bank.json             8 × (4 practice + 3 probe) = 56 题
```

关于题目来源，`bank.json` 的 `source.provenance` 字段写得很明确：**知识点名称和约一半的练习题直接来自 EXTRACT 输出**，其余题目由同一份幻灯片文本人工编写，以凑够每点 7 题。`parse_raw.json` 里每条记录都带页码，可以逐条对照原件。

每题带一个 `difficulty` 字段（0.15–0.45，IRT 式难度参数 b），供模拟学生计算答对概率；策略本身看不到难度，只看作答结果。

## 3. 两种讲解条件（LLM-only / RAG）

E1 的四个方法里，`llm_only` 与 `rag` 差的是**讲解从哪来**，不是怎么排活动。这一层无法用冻结题库代替（题库只有题，没有讲解），所以单独生成一次：

| 条件 | 上下文 | 提示词 |
| ---- | ---- | ---- |
| `rag` | 检索到的 3 段讲义原文 | `TEACH_SYSTEM`：只用给出的摘录 |
| `llm_only` | 无 | 换成"你没有课件，凭自身知识讲，不要编造页码" |

```bash
python -m experiments.content               # 只生成缺失的
python -m experiments.content --force       # 全部重新生成
python -m experiments.content --score-only  # 只重算分，不调模型
```

`materials.json` 里每条记录保存讲解原文、模型名、延迟、token 用量和调用次数，外加两项离线打分：

| 指标 | 含义 | LLM-only | RAG |
| ---- | ---- | ---- | ---- |
| `coverage` | 讲解覆盖"探针题考点词"的比例 | 0.274 | 0.439 |
| `grounding` | 与检索到的讲义段落的最大 TF-IDF 余弦 | 0.141 | 0.289 |

模拟学生把讲解带来的能力增长乘以 `coverage`，所以这一层直接决定 E1 里 LLM-only 与 RAG 的差距。

检索优先走产品自己的 Chroma 索引；干净 clone 里通常没有索引，脚本会回退到**用本目录 PDF 现建的 800 字符页级 TF-IDF 索引**（与产品 `chunk_size` 一致）。实际用的后端写在 `retrieval_backend` 字段里，不会含糊。

## 4. 复现 / 换数据

```bash
# 用仓库内的实验讲义重建抽取结果（需要 API key，会调用 LLM）
python -m experiments.build_bank

# 换一份讲义（路径含空格，记得加引号）
python -m experiments.build_bank --pdf "C:/path/another lecture.pdf"

# 校验题库：id 唯一、答案下标合法、练习与探针互斥
python -m experiments.bank --check

# 生成/评分两种讲解条件（需要 API key，只跑一次）
python -m experiments.content

# 跑实验（离线，不调用 LLM，读的是已冻结的 bank.json 与 materials.json）
python -m experiments.run_experiment --seeds 8 --budget 32

# 出图
python -m experiments.plots
```

换成别的讲义同样适用：把 PDF 放进 `source/`，改 `--pdf` 参数即可，后续整理步骤不变。`parse_raw.json` 和 `bank.json` 是**冻结产物**，实验运行时不会重新调用模型——免费额度会限流，生成文本也不可复现，所以只在建库时调用一次。

## 5. 使用限制

`source/Experiment(Lecture 1 Intro 2026).pdf` 是课程为本次实验提供的讲义，版权归课程与授课教师所有。放在本仓库仅用于：

- 课程项目的实验复现；
- 课程评审、答辩时核对数据来源。

**不得再分发、不得用于课程以外的用途。** 如果这个仓库将来转为公开仓库，请先删除 `source/` 目录——`parse_raw.json` 已包含带页码的知识点摘录，足够说明数据来源，不需要公开整份讲义。
