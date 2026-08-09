## Context

政策数据库已存储清洗后的纯文本正文 `policies.content_text`（Text，非空，段落化），并建有 FTS5 全文索引。系统采用 FastAPI + SQLAlchemy 2.0 + SQLite（WAL + 外键）+ Vue3 架构。既有任务机制 `tasks/` 与采集强耦合：`crawl_tasks` 强制 `rule_id`、`TaskRunner` 写死采集逻辑、`TaskWorker` 基于 `ThreadPoolExecutor` + `claim_next` + 链式 `submit_next`。「政策分析」前端路由 `/analysis` 已存在并指向 `PlaceholderView`，`PageCode.ANALYSIS` 权限码已就绪，`navigation.ts` 菜单已含「政策分析」。前端 UI 全用原生 HTML + 自定义 CSS（`.data-table` 等），未注册 Element Plus；API 调用用原生 `fetch` 封装 `apiRequest`，写操作自动注入 `X-CSRF-Token`；任务轮询有 `createTaskPolling` 可复用。

需求文档建议的 Spring Boot + Redis + MySQL 架构与本仓库规范冲突，统一按 FastAPI + SQLite + Vue3 落地，任务机制复用既有进程内线程池模式。

## Goals / Non-Goals

**Goals:**
- 政策列表支持多选并创建「分词分析」任务，后台异步执行（不阻塞请求）。
- NLP 流程：jieba 分词 → 停用词过滤 → 词频统计 → TF-IDF（以选中政策集合为语料）→ 关键词共现（TOP-N 两两共同出现篇数）。
- 分析结果按任务持久化，支持历史查询；前端词频排行（可排序）、词云、关键词关系图三视图。
- 模块化、可扩展：NLP 为纯函数 `engine`，任务表 `task_type` 预留，便于后续 TopicAnalyzer/PolicySimilarityAnalyzer。
- 复用既有权限码与鉴权依赖，写操作组合 CSRF + 页面权限。

**Non-Goals:**
- 不做 AI 总结、知识图谱、政策预测、大模型分析、政策对比分析（Tab4）。
- 不引入 Redis/外部任务队列/MySQL/numpy/scikit-learn。
- 不改动既有采集任务机制与任务中心页面。
- 不做词性标注的深度语言学分析（`word_type` 字段预留，首期存 null）。
- 不做分布式/多节点并发。

## Decisions

### 决策 1：独立 `analysis` 模块与独立任务表，而非复用 `crawl_tasks`
新增 `analysis_tasks` 等表与 `AnalysisWorker`，结构仿 `TaskWorker`（`ThreadPoolExecutor + claim_next + 链式 submit_next`）但操作 analysis 表。`task_type` 字段（默认 `word_frequency`）为后续分析类型扩展预留。

**理由**：`crawl_tasks` 强制 `rule_id` 且 runner 写死采集逻辑，硬塞分析任务会污染采集语义与状态机（采集有 `partially_succeeded` 等采集专属状态）；分析任务状态更简单（pending/running/succeeded/failed），独立建表更清晰，且 `TaskWorker` 的线程池模式成熟可仿。

**备选**：复用 `TaskWorker` + 抽象新 runner。被否：`claim_next`/`finish` 与 `CrawlTask` 表绑定，抽象成本高且耦合难解。

### 决策 2：TF-IDF 以选中政策集合为语料自行实现，不引入 sklearn
语料 = 本次任务选中的政策集合（每篇政策一个 document）。`tf = freq_in_doc / total_terms_in_doc`，`idf = log((1 + N) / (1 + df)) + 1`（sklearn 风格平滑，`N` 文档数、`df` 含该词文档数），始终非负；单文档时 `idf = 1`（`tfidf == tf`）天然兜底。每个 `(task, policy, word)` 存该篇 `frequency` 与 `tfidf`；任务级排行用聚合总频次与平均 tfidf。

**理由**：jieba 自带 `analyse` 基于通用语料 idf，对政策领域区分度弱；以选中政策为语料更贴合「区分本次集合内普通高频词与领域关键词」的诉求；纯 `math` 实现避免 numpy/sklearn 重依赖，符合 SQLite 轻量哲学。

**备选**：sklearn `TfidfVectorizer`。被否：引入 numpy/scipy，部署体积与冷启动增大，与轻量架构不符。

### 决策 3：共现仅对任务级 TOP-N 词两两统计「共同出现篇数」
对任务级 TOP-N（默认 50）关键词，两两统计「同时出现在同一篇政策」的篇数作为 `co_count`，`word1 < word2` 规范化入库。不计算全词 N² 共现。

**理由**：全词共现 N² 爆炸（一篇可数千词）；TOP-N 已覆盖关系图可读范围内的关键节点；「共同出现篇数」语义清晰、稳定，适合 ECharts Graph 力导向布局。

### 决策 4：NLP 为纯函数 `engine.py`，I/O 仍在 runner
`tokenize`/`filter_stopwords`/`compute_tfidf`/`compute_cooccurrence` 为纯函数（接受文本/统计量、返回结构，无 DB/网络 I/O）。`AnalysisRunner` 负责从 DB 取正文、调 engine、把结果交 `AnalysisRepository` 入库。

**理由**：纯函数可独立单测（无需 DB）；后续 `KeywordExtractor`/`TopicAnalyzer` 可并列新增而不耦合 runner；与 `XinhuaCollector` 保持纯函数式一致。

### 决策 5：前端多选用原生表格 checkbox，不引入 Element Plus
`PolicyListView.vue` 的原生 `<table class="data-table">` 首列加 checkbox（全选 + 行选），`selectedIds` 跨页保留；顶部「分词分析」按钮 POST 创建任务后跳转 `/analysis?taskId=`。

**理由**：代码库一致使用原生 HTML 表格 + 自定义 CSS，Element Plus 虽在 `package.json` 但从未注册；引入会破坏风格一致性并带来大范围重构。ECharts 仅用于分析页图表封装。

### 决策 6：任务状态在「政策分析」页面内独立展示，不进任务中心
分析任务的创建、轮询、结果、历史列表均在 `AnalysisView` 内完成，复用 `createTaskPolling` 模式。

**理由**：任务中心现为采集专用且强绑采集语义；分析任务在分析页内闭环更聚焦，避免跨域混淆。后续如需统一可在任务中心增加 `task_type` 维度，本次不做。

## Risks / Trade-offs

- [单篇政策正文可能数十万字，批量几十篇耗时] -> 缓解：异步任务 + 线程池（默认 `max_workers=1` 避免影响采集与主线程）+ `max_policies_per_task` 上限；前端轮询进度。
- [SQLite 写入大批量词结果可能锁库] -> 缓解：分批提交（每篇或每若干篇一个事务）；WAL 模式；`busy_timeout=5000`。
- [jieba 首次加载词典较慢] -> 缓解：模块级初始化（进程内只加载一次）；worker 线程复用。
- [TF-IDF 在小语料（1-2 篇）区分度弱] -> 缓解：`N≤1` 时 idf=1 兜底；词频排行仍有效；文档说明小语料下 TF-IDF 参考价值有限。
- [共现 TOP-N 截断可能漏掉长尾关系] -> 缓解：`top_words_default` 可配；关系图聚焦核心节点，长尾关系可视化价值低。
- [跨页多选状态丢失] -> 缓解：`selectedIds` 跨页保留（不随翻页清空），显示已选数量。
- [新依赖 jieba/echarts 增加部署体积] -> 缓解：jieba 纯 Python 中等体积、echarts 按需引入；可接受。

## Migration Plan

1. 后端先行：建 `analysis/` 包（models/state/schemas/repository/engine/runner/worker/service/routes + resources）；迁移 `0004`；接入 `database.py`/`env.py`/`settings.py`/`permissions.py`/`main.py`。
2. 后端测试：engine/repository/runner/routes 单测 + 迁移升降级 + `pytest --cov-fail-under=80` + ruff。
3. 前端：依赖安装；`AnalysisView` + 图表组件；`PolicyListView` 多选；类型与路由；vitest + Playwright。
4. 文档：README、设计说明书、OpenSpec archive。
5. 部署：确认 Jenkinsfile 含 `pip install`（装 jieba）且不覆盖 `app.json`；推送 main + 触发 Jenkins。
6. 回滚：`alembic downgrade 0003` 删除表；移除 `analysis` 包与前端组件；`/analysis` 回退占位页。

## Open Questions

- 是否需要为普通 `user` 角色默认授予 `analysis` 页面权限？当前决定：不默认授予，由管理员在权限管理页按需配置（与 `policies` 等一致）；admin 默认拥有。
- 是否需要用户自定义词典（政策领域词如「高质量发展」）？当前决定：首期用 jieba 默认词典 + 停用词；预留 `engine` 扩展点，后续按需加 `userdict`。
