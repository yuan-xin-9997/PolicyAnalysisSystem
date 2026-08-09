## 1. OpenSpec 变更工件

- [ ] 1.1 创建 `openspec/changes/2026-08-09-policy-word-frequency-analysis/` 下 proposal.md、design.md、tasks.md、specs/policy-analysis/spec.md
- [ ] 1.2 `openspec validate policy-word-frequency-analysis --strict` 通过

## 2. 后端数据模型与迁移

- [ ] 2.1 新增 `policy_analysis/analysis/models.py`：`AnalysisTask`、`AnalysisTaskPolicy`、`AnalysisWordResult`、`AnalysisWordRelation`、`AnalysisTaskLog`、`AnalysisTaskStatus`(StrEnum)
- [ ] 2.2 新增 `migrations/versions/0004_analysis_tables.py`（5 张表，时间字段 String(40)，FK 命名，CheckConstraint 状态枚举，唯一键与索引）
- [ ] 2.3 在 `core/database.py` 的 `create_schema()` 与 `migrations/env.py` 补 analysis models import
- [ ] 2.4 迁移升降级单测通过

## 3. 后端 NLP 引擎

- [ ] 3.1 新增 `policy_analysis/analysis/engine.py`：`tokenize`(jieba.lcut)、`filter_stopwords`、`compute_tfidf`(选中集合为语料，N≤1 兜底)、`compute_cooccurrence`(TOP-N 两两共现篇数)
- [ ] 3.2 新增 `policy_analysis/analysis/resources/stopwords.json`（中文常见停用词）
- [ ] 3.3 `pyproject.toml` 加 `jieba` 依赖与 `analysis.resources` package-data
- [ ] 3.4 engine 单测（分词、停用词、TF-IDF 边界、共现）

## 4. 后端 repository / state / schemas

- [ ] 4.1 新增 `analysis/state.py` 状态机（pending→running→{succeeded,failed}）
- [ ] 4.2 新增 `analysis/schemas.py`（CreateAnalysisTaskRequest/AnalysisTaskSummary/AnalysisTaskDetail/WordFrequencyItem/WordRelationItem 等，StrictModel）
- [ ] 4.3 新增 `analysis/repository.py`：create_task/claim_next/finish/get/list_tasks/load_policies/store_results/store_relations/list_words/list_relations/add_log/recover_interrupted

## 5. 后端 runner / worker

- [ ] 5.1 新增 `analysis/runner.py`：`AnalysisRunner.run_claimed(task_id)`（取正文→分词→过滤→词频→TF-IDF→共现→分批入库→finish）
- [ ] 5.2 新增 `analysis/worker.py`：`AnalysisWorker`（仿 TaskWorker：ThreadPoolExecutor + claim_next + 链式 submit_next + recover_interrupted）

## 6. 后端 service / routes / 接入

- [ ] 6.1 新增 `analysis/service.py`：`AnalysisService`（create_task/get_task/list_tasks/list_words/list_relations/list_logs）
- [ ] 6.2 新增 `analysis/routes.py`：POST/GET `/api/v1/analysis/tasks`、GET `/tasks/{id}`、`/tasks/{id}/words`、`/tasks/{id}/relations`、`/tasks/{id}/logs`
- [ ] 6.3 `auth/permissions.py` 加 `require_page_csrf`（CSRF + 页面权限）
- [ ] 6.4 `core/settings.py` 加 `AnalysisSettings` + `src/config/app.json` 加 `analysis` 键
- [ ] 6.5 `main.py` lifespan 启动 `AnalysisWorker` + `recover_interrupted` + `include_router`
- [ ] 6.6 routes 集成测试（创建/查询/结果/权限/CSRF）+ ruff 通过

## 7. 前端依赖 / 类型 / 路由

- [ ] 7.1 `package.json` 加 `echarts` + `echarts-wordcloud`，`npm ci` 安装
- [ ] 7.2 `api/types.ts` 加 `AnalysisTask`/`WordFrequencyItem`/`WordRelation` 等类型
- [ ] 7.3 `router/index.ts` 把 `analysis` 路由组件换为 `AnalysisView`

## 8. 前端政策列表多选

- [ ] 8.1 `PolicyListView.vue` 加 checkbox 列（全选 + 行选，`selectedIds` 跨页保留）与「分词分析」按钮
- [ ] 8.2 点击「分词分析」POST 创建任务并跳转 `/analysis?taskId=`

## 9. 前端 AnalysisView 与 ECharts

- [ ] 9.1 新增 `components/charts/BaseChart.vue`（init/dispose/resize 通用封装）
- [ ] 9.2 新增 `WordCloudChart.vue`（echarts-wordcloud）与 `RelationGraphChart.vue`（echarts graph 力导向）
- [ ] 9.3 新增 `views/analysis/AnalysisView.vue`（三 Tab + 任务轮询 + 历史列表 + 创建入口）
- [ ] 9.4 时间用 `formatBeijingTime`，无结果/加载/失败状态完备

## 10. 前端测试

- [ ] 10.1 `AnalysisView`/chart/`PolicyListView` 多选 vitest 用例
- [ ] 10.2 相关 Playwright e2e（登录→勾选→创建任务→轮询→出三视图）
- [ ] 10.3 `type-check`/`lint`/`build`/`test:e2e` 通过

## 11. 文档与部署

- [ ] 11.1 更新 `README.md`（政策分析页面与 API 章节）
- [ ] 11.2 更新 `docs/superpowers/specs/2026-07-31-policy-analysis-system-mvp-design.md`（政策分析从占位/非目标更新为已实现词频分析）
- [ ] 11.3 确认 `JenkinsConfig/Jenkinsfile` 含 `pip install` 装新依赖且不覆盖 `app.json`
- [ ] 11.4 `openspec validate --strict` + `openspec archive policy-word-frequency-analysis`
- [ ] 11.5 推送 main + API 触发 Jenkins 手工构建（推送前与用户确认）
