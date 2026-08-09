## Why

政策分析系统的「政策分析」页面目前是占位页（`PlaceholderView`，仅展示「功能规划中」），设计说明书将其列为首期非目标（「词频对比、表述差异和可视化分析」）。政策数据库已积累可观的清洗后纯文本正文（`policies.content_text`），但缺少对这些正文进行结构化分析的能力。需要基于已有政策正文落地「词频分析」作为政策分析的第一阶段，为后续 TF-IDF 关键词分析、主题分类、相似度分析、趋势分析与大模型摘要提供可扩展的架构基础。

## What Changes

- 新增后端 `policy_analysis.analysis` 模块：仿 `policies/` 与 `tasks/` 分层（models/state/schemas/repository/engine/runner/worker/service/routes），NLP 逻辑独立为纯函数 `engine.py`（分词、停用词过滤、TF-IDF、共现），便于后续扩展 `KeywordExtractor`/`TopicAnalyzer` 等。
- 中文分词采用 `jieba`；TF-IDF 以本次选中政策集合为语料自行实现（纯 `math`，不引入 numpy/sklearn）；停用词表为 `analysis/resources/stopwords.json`。
- 新增独立异步任务机制：`analysis_tasks` 等表 + `AnalysisWorker`（仿 `TaskWorker` 的 `ThreadPoolExecutor + claim_next + 链式 submit_next`），与采集任务解耦，不污染 `crawl_tasks`。任务状态在「政策分析」页面内独立展示与轮询。
- 新增 Alembic 迁移 `0004_analysis_tables`：`analysis_tasks`、`analysis_task_policies`、`analysis_word_results`、`analysis_word_relations`、`analysis_task_logs`。
- 前端将 `/analysis` 路由从 `PlaceholderView` 替换为 `AnalysisView`，含三个 Tab（词频排行、词云、关键词关系图）+ 历史任务列表；政策列表页新增 checkbox 多选与「分词分析」按钮；引入 `echarts` + `echarts-wordcloud`。
- 复用既有 `PageCode.ANALYSIS` 权限码与 `require_page`/CSRF 鉴权；新增 `require_page_csrf` 供写操作组合 CSRF 与页面权限。
- 新增 `AnalysisSettings`（`max_workers`/`top_words_default`/`min_word_length`/`max_policies_per_task`），同步 `core/settings.py` 与 `src/config/app.json`。
- 依赖：`pyproject.toml` 加 `jieba`；前端 `package.json` 加 `echarts`、`echarts-wordcloud`。

## Capabilities

### New Capabilities

- `policy-analysis`：基于选中政策正文的中文分词、停用词过滤、词频统计、TF-IDF 关键词重要度与关键词共现关系计算，以异步任务执行并持久化历史结果，前端提供词频排行、词云与关系图可视化。

### Modified Capabilities

（本次不修改既有能力。）

## Impact

- 后端：新增 `policy_analysis/analysis/` 包（models/state/schemas/repository/engine/runner/worker/service/routes + resources）；`core/database.py` 的 `create_schema()`、`migrations/env.py` 补 analysis models import；`main.py` lifespan 启动 `AnalysisWorker` + `recover_interrupted` + `include_router`；`auth/permissions.py` 加 `require_page_csrf`；`core/settings.py` 加 `AnalysisSettings`。
- 数据：新增 5 张表（Alembic `0004`，可升降级），不改动既有表；分析结果按任务持久化，支持历史查询。
- 前端：`router/index.ts` 替换 analysis 路由组件；`PolicyListView.vue` 加多选与分词分析入口；新增 `AnalysisView.vue` + `components/charts/`（BaseChart/WordCloudChart/RelationGraphChart）；`api/types.ts` 加分析类型。
- 依赖与文档：`pyproject.toml` 加 `jieba`、`package-data` 加 analysis resources；前端加 `echarts`/`echarts-wordcloud`；更新 README 与设计说明书；确认 Jenkinsfile 含依赖安装且不覆盖 `app.json`。
