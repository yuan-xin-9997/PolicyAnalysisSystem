# policy-collection-dedup Specification

## Purpose
定义多源（新华网、人民日报、央视新闻等）采集同一场会议时的去重与升级语义，确保 `policies` 表对同一会议只保留权威度最高的那一条记录，并在升级时保留历史正文版本。
## Requirements
### Requirement: 跨源同一会议通过 meeting_key 唯一落库
系统 SHALL 在政策入库路径上以 `(category_id, title, published_date_in_beijing)` 三元组作为"同一会议"的判定键；当来自不同来源的多篇报道命中该 meeting_key 时，运行时 SHALL 仅在 `policies` 表落一条记录，且 `published_date` 统一以 `Asia/Shanghai` 时区对齐到日期（不依赖入库主机本地时区）。`title` 匹配为严格相等（不做"（受权发布）"等公共前后缀归一化），以避免误删正文中的合法片段。`category_id` 决定归属的政策类别。

#### Scenario: 同一会议在两个来源同时发稿
- **WHEN** 同一场会议分别由 news.cn 与 people.com.cn 同步发布，且两篇文章的 category、title、published_date 三元组完全一致
- **THEN** 入库路径仅在 `policies` 表创建一条记录，第二篇按既定的优先级语义处理（升级或判重），不产生重复行

#### Scenario: 标题与日期微差不视为同一会议
- **WHEN** 两篇文章的标题存在后缀差异（例如"中央财经委员会第十次会议" vs "中央财经委员会第十次会议（受权发布）"）或发布日期相差一天
- **THEN** 系统不视为同一会议，按两条独立政策分别入库

#### Scenario: published_at 跨时区统一对齐
- **WHEN** 同一会议的 published_at 在不同来源以不同时区表达（如 UTC+0 与 UTC+8）
- **THEN** meeting_key 比较基于转换为 `Asia/Shanghai` 后的日期部分，跨时区表达不造成同次会议被错误判重

### Requirement: 跨源命中按源优先级升级或判重
系统 SHALL 为采集源硬编码以下权威度优先级（值越大越权威）：`news.cn` / `xinhuanet.com` = 3，`people.com.cn` = 2，`cctv.com` = 1，其他 = 0。判定逻辑：
- 当 meeting_key 命中且**新源优先级高于**已存源时，把已存 policy 整体升级为新源（`source_id` / `canonical_url` / `publisher` / `content_text` / `content_hash` / `webfetch_artifact_id` / `last_crawled_at` 全部覆盖），旧 `content_text` 写入 `PolicyRevision`，并返回 `outcome="updated"`；
- 当 meeting_key 命中且**新源优先级不高于**已存源时，仅标记 `outcome="duplicate"`，已存 policy 不做任何字段变更；
- 同源 + 相同 `(source_id, canonical_url)` 或 `(source_id, content_hash)` 时仍走 source-scoped dedup，跨源 meeting_key 检查不破坏同源升级路径。

#### Scenario: 低优先级源后入被判重
- **WHEN** 一条 meeting_key 已由 xinhua 入库（优先级 3），随后 people.com.cn（优先级 2）发布同一会议
- **THEN** 第二次 upsert 返回 `outcome="duplicate"`，`policies` 表行数仍为 1，policy 的 `source_id` 仍为 xinhua，不写入 `PolicyRevision`

#### Scenario: 高优先级源后入触发升级
- **WHEN** 一条 meeting_key 已由 people.com.cn 入库（优先级 2），随后 xinhua（优先级 3）发布同一会议
- **THEN** 第二次 upsert 返回 `outcome="updated"`，`policies` 行的 `source_id` / `canonical_url` / `content_text` 等被覆盖，旧 `content_text` 写入 `PolicyRevision`

#### Scenario: 同源同 URL 或同哈希仍走原 dedup
- **WHEN** 同源（xinhua）下两篇 upsert 共享 `(source_id, canonical_url)` 或 `(source_id, content_hash)`
- **THEN** 行为由 source-scoped dedup 决定（duplicate 或 update），meeting_key 跨源检查不截断

### Requirement: 跨源 meeting_key 检索由专用索引支撑
系统 SHALL 在 `policies` 表上提供 `(category_id, title, published_at)` 复合索引 `ix_policies_category_title_published` 以支撑跨源 meeting_key 检索，且 SHALL 不替代原有的 `uq_policies_source_canonical_url` 与 `ix_policies_source_content_hash` 唯一约束与索引。索引 SHALL 由迁移脚本（`migrations/versions/0005_*`）以 `op.create_index` 形式创建，命名遵循既有 `ix_policies_*` 习惯。

#### Scenario: 索引存在且仅服务于 meeting_key 检索
- **WHEN** 数据库被升级到最新 head 迁移
- **THEN** `policies` 表同时存在 `ix_policies_category_title_published`、既有 `uq_policies_source_canonical_url` 与 `ix_policies_source_content_hash`，三者职责独立且互不替代

### Requirement: 种子清单来源白名单按基域名后缀匹配
种子清单 URL 校验 SHALL 只接受 `news.cn`、`xinhuanet.com`、`people.com.cn`、`cctv.com` 四个基域名及其任意子域（主机名等于基域名或以 `.基域名` 结尾），URL SHALL 为 https、无端口、无 query/fragment 且保持 canonical 形式；URL 中的发布日期 SHALL 由各来源的路径格式（新华网 `YYYYMMDD/c_*.htm`、人民网 `/n1/YYYY/MMDD/`、央视 `/YYYY/MM/DD/ARTI|VIDE`）解析并与清单声明的 `expected_published_date` 一致。

#### Scenario: 出版方子域被接受
- **WHEN** 种子 URL 主机为 `politics.people.com.cn`、`cpc.people.com.cn`、`news.cctv.com`、`tv.cctv.com` 等基域名的子域
- **THEN** 校验通过，URL 按原样 canonical 化入库

#### Scenario: 伪装域名被拒绝
- **WHEN** 种子 URL 主机为 `news.cn.evil.example`、`notpeople.com.cn`、`m.people.cn`（`.cn` 非 `.com.cn`）等不属于四个基域名的域名
- **THEN** 校验失败并拒绝整份清单，错误信息不回显原始输入
