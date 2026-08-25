# policy-analysis Specification

## Purpose
TBD - created by archiving change 2026-08-09-policy-word-frequency-analysis. Update Purpose after archive.
## Requirements
### Requirement: 政策多选与分词分析任务创建
系统 SHALL 允许具有政策分析页面权限的用户在政策列表中勾选一篇或多篇政策，并 SHALL 提供「分词分析」操作创建分析任务；创建接口 SHALL 校验 CSRF 与政策分析页面权限，并 SHALL 限制单次任务政策数量不超过配置上限。

#### Scenario: 单篇政策分析
- **WHEN** 用户勾选一篇政策并点击「分词分析」
- **THEN** 系统创建一个 `word_frequency` 类型的分析任务，返回任务 ID，并在政策分析页面开始轮询任务状态

#### Scenario: 多篇政策批量分析
- **WHEN** 用户勾选多篇政策并点击「分词分析」
- **THEN** 系统创建一个包含全部所选政策的分析任务，跨页保留选中状态，任务异步执行不阻塞列表页

#### Scenario: 未选择政策
- **WHEN** 用户未勾选任何政策即点击「分词分析」
- **THEN** 系统拒绝创建任务并提示需选择至少一篇政策

#### Scenario: 超出单次任务政策上限
- **WHEN** 用户勾选的政策数量超过配置的 `max_policies_per_task`
- **THEN** 系统拒绝创建任务并提示超出上限

#### Scenario: 未授权创建分析任务
- **WHEN** 不具有政策分析页面权限的用户请求创建分析任务
- **THEN** 系统拒绝请求且不创建任务

#### Scenario: 缺少 CSRF 令牌
- **WHEN** 已授权用户的创建请求缺少有效的 X-CSRF-Token
- **THEN** 系统拒绝请求

### Requirement: 后台异步分词与词频统计
系统 SHALL 以异步任务执行 NLP 流程：中文分词（jieba）→ 停用词过滤 → 词频统计 → TF-IDF → 关键词共现；分词与统计算法 SHALL 为无 I/O 的纯函数；任务 SHALL 在独立线程池执行且不阻塞 HTTP 请求。

#### Scenario: 异步执行不阻塞请求
- **WHEN** 创建任务接口返回
- **THEN** 任务在后台线程池执行，创建接口立即返回任务 ID 与 pending 状态

#### Scenario: 服务中断后恢复
- **WHEN** 服务在分析任务 running 时中断并重启
- **THEN** 系统将中断的 running 任务标记为 failed，不影响新任务执行

#### Scenario: 分词与停用词过滤
- **WHEN** 系统对政策正文执行分词
- **THEN** 系统使用 jieba 分词并过滤停用词表中的词，仅保留有效词进入词频统计

### Requirement: TF-IDF 以选中政策集合为语料
系统 SHALL 以本次任务选中的政策集合作为 TF-IDF 语料（每篇政策为一个文档），为每个词计算其在该篇政策的 TF-IDF 值；当语料文档数不大于 1 时 SHALL 以 idf=1 兜底避免负值或除零。

#### Scenario: 多篇政策 TF-IDF
- **WHEN** 任务包含多篇政策
- **THEN** 系统基于选中集合计算每个词的 idf，并为每个 (任务, 政策, 词) 存储该篇 frequency 与 tfidf

#### Scenario: 单篇政策 TF-IDF 兜底
- **WHEN** 任务只包含一篇政策
- **THEN** 系统以 idf=1 计算 tfidf，不产生负值或除零错误

### Requirement: 关键词共现关系
系统 SHALL 对任务级 TOP-N 关键词两两统计「同时出现在同一篇政策」的篇数作为共现计数，并 SHALL 以 word1 < word2 规范化入库；关系图 SHALL 仅展示 TOP-N 范围内的关键词节点与共现边。

#### Scenario: 计算共现关系
- **WHEN** 任务完成词频统计
- **THEN** 系统对 TOP-N 关键词两两计算共同出现的政策篇数并存入关系表

#### Scenario: 关系图展示
- **WHEN** 用户查看任务的关系图
- **THEN** 系统返回 TOP-N 关键词节点及其共现边，前端以力导向图展示

### Requirement: 分析结果持久化与历史查询
系统 SHALL 持久化每个任务的词频结果、TF-IDF、共现关系与任务日志，并 SHALL 支持历史任务列表与历史结果查询；历史结果 SHALL 不因新任务创建而丢失。

#### Scenario: 持久化词频结果
- **WHEN** 任务执行完成
- **THEN** 系统将每篇每词的 frequency、tfidf 与共现关系持久化到数据库

#### Scenario: 查询历史任务
- **WHEN** 用户在政策分析页面查看历史任务列表
- **THEN** 系统返回历史分析任务分页列表，用户可点击查看任一历史任务的结果

#### Scenario: 查询词频排行
- **WHEN** 用户查看某任务的词频排行
- **THEN** 系统返回聚合后的关键词、总频次与 TF-IDF，支持按频次或 TF-IDF 排序与 TOP-N 限制

### Requirement: 前端分析视图
系统 SHALL 在政策分析页面提供词频排行、词云与关键词关系图三个视图；视图 SHALL 在任务完成后展示结果，任务进行中 SHALL 展示轮询状态；所有时间 SHALL 以北京时间显示。

#### Scenario: 词频排行视图
- **WHEN** 任务完成后用户查看词频排行 Tab
- **THEN** 系统展示 TOP 关键词、出现次数与 TF-IDF，支持按次数或 TF-IDF 排序

#### Scenario: 词云视图
- **WHEN** 用户查看词云 Tab
- **THEN** 系统以词云展示高频关键词，词的大小反映频次

#### Scenario: 关系图视图
- **WHEN** 用户查看关系图 Tab
- **THEN** 系统以力导向图展示关键词共现关系

#### Scenario: 任务进行中状态
- **WHEN** 任务处于 pending 或 running
- **THEN** 页面展示进行中状态并持续轮询，直到进入终态后展示结果或错误

### Requirement: 多政策差异比对
系统 SHALL 允许用户选择两篇或多篇不同政策创建 `policy_comparison` 任务，并 SHALL 基于政策正文生成结构化差异分析报告；报告 SHALL 持久化并可从历史任务再次查看。

#### Scenario: 创建政策比对任务
- **WHEN** 用户选择至少两篇不同政策并点击「政策比对」
- **THEN** 系统异步创建政策比对任务，并跳转到政策分析页面轮询任务状态

#### Scenario: 选择数量不足
- **WHEN** 用户只选择一篇政策或重复提交同一篇政策
- **THEN** 系统拒绝创建任务并提示至少需要两篇不同政策

#### Scenario: 生成差异分析报告
- **WHEN** 政策比对任务执行成功
- **THEN** 报告展示政策概览、全部政策共同关键词、每篇核心关键词以及每两篇政策的相似度、共同重点和各自独有重点

#### Scenario: 多篇政策两两比对
- **WHEN** 用户选择 N 篇政策且 N 大于 2
- **THEN** 系统生成 N×(N−1)/2 组两两差异，不遗漏任意政策组合

