## Context

政策采集链路为：`TaskRunner` 调用 `WebFetchClient.extract_article(url)` 取回 `ExtractedArticle`（其 `content` 来自 WebFetch `generic.article` 适配器抽取的正文文本），交由 `XinhuaCollector.classify` 产出 `Classification`，最终在 `TaskRunner` 中以 `classification.content` 计算 `content_hash` 并写入 `PolicyWrite.content_text` 持久化。当前 `classify` 将 `article.content` 原样透传，未剥离网页页面装饰，导致入库正文混入：

- 开头页眉：面包屑/栏目/时间/来源行，如「新华网 > 时政 > 正文 2026 07/30 14:36:12 来源：新华网」；
- 结尾页脚：「阅读下一篇：」及后续推荐标题列表。

前端 `PolicyDetailView.vue` 将 `policy.content_text` 渲染进单个 `<div class="policy-content">`，CSS 为 `white-space: pre-wrap`，仅保留换行、不分段、无首行缩进。现有 spec `policy-database-experience` 的「可读且安全的政策正文详情」要求正文按纯文本安全渲染、保留换行、安全折行。

约束：WebFetch 客户端契约不可变；采集判定阈值（最小正文字数、关键词、来源校验、滚动窗口）不可放松；正文必须按纯文本安全渲染、不得执行 HTML；不引入新数据库或外部搜索服务。

## Goals / Non-Goals

**Goals:**
- 从抓取源头剥离新华网文章正文的页眉（面包屑/来源/时间）与页脚（「阅读下一篇」）装饰，使 `content_text` 与 `content_hash` 反映纯政策正文。
- 政策详情页正文按段落分段渲染、首行缩进两个汉字宽度，保持纯文本安全渲染与窄屏可读。
- 清洗逻辑单元可测、无 I/O，且不改变采集判定语义。

**Non-Goals:**
- 不回填已入库政策的历史正文（仅新抓取/重新抓取的政策享受清洗）。
- 不重新抓取或重新解析 HTML；清洗基于 WebFetch 已返回的文本正文。
- 不清洗标题、作者、发布时间等元数据字段（这些已有独立提取与校验）。
- 不引入通用 NLP 段落归纳或正文摘要。

## Decisions

### 决策 1：清洗逻辑放在 `XinhuaCollector` 内部，而非 `TaskRunner`
清洗在 `classify` 中对 `article.content` 执行，产出的 `Classification.content` 即为清洗后正文，`TaskRunner` 无需改动 `content_hash` 与 `PolicyWrite.content_text` 的取值路径（它们已经引用 `classification.content`）。

**理由**：`XinhuaCollector` 是纯函数式、无 I/O 的适配器，已持有新华网正文相关领域知识（页眉 `来源：新华网`、`新华社北京` 等模式），且 `classify` 已对 content 做归一化用于判定。把清洗放在这里保持单一职责、可单元测试，且不污染 runner 的编排逻辑。

**备选**：在 `TaskRunner` 中于 `classify` 之后清洗。被否，因为会把新华网特定的页面装饰模式散落到编排层、绕过适配器封装，且与判定的归一化内容重复处理。

### 决策 2：基于文本行与正则的规则式剥离，不重新解析 HTML
WebFetch `generic.article` 适配器返回的是抽取后的文本正文（非 HTML），因此清洗按行/正则操作：

- **页眉剥离**：移除正文开头的连续「装饰行」——含面包屑分隔符「>」的行、纯时间戳行（匹配既有 `_DASHED_DATE`/数字时间样式）、以及 `来源：` 来源行；逐行判断直到遇到第一个非装饰行作为正文起点。
- **页脚剥离**：定位首个「阅读下一篇」标记行，自该行起全部截断。
- **空白归一化**：去除行首尾多余空白，合并连续空行，保留段落间的单空行分隔以供前端分段。

**理由**：在已抽取文本上做规则剥离最直接、可预测、可测试；不引入额外 I/O 或 HTML 解析依赖。

**备选**：重新抓取页面并按 DOM 选择器提取正文区域。被否，因违反「不重新抓取」约束、增加 I/O 与失败面，且 WebFetch 契约不可变。

### 决策 3：`content_hash` 基于清洗后正文计算
`TaskRunner` 已用 `classification.content` 计算 `content_hash`，清洗后该哈希自动反映纯正文。政策 upsert 以 `canonical_url` 为准，哈希变化触发既有行的内容更新而非新增重复行。

**理由**：去重应反映语义正文；同一政策在不同页面装饰下应得到一致哈希。

### 决策 4：前端按段落分段渲染 + `text-indent`，保持纯文本安全
`PolicyDetailView.vue` 将 `policy.content_text` 按换行/空行切分为段落数组，每个段落以独立 `<p>` 渲染（Vue 文本插值 `{{ }}`，不使用 `v-html`），CSS 对 `.policy-content p` 施加 `text-indent: 2em`、段落间 `margin`，移除单块的 `white-space: pre-wrap`（折行改由段落块自身的 `overflow-wrap: anywhere` 承担）。

**理由**：首行缩进必须以块级段落为单位，单块 `pre-wrap` 无法实现每段缩进；文本插值天然不执行 HTML，满足既有「不执行正文 HTML」要求。

**备选**：保留 `pre-wrap` 单块并用 CSS `text-indent`。被否，因为单块文本只有一个首行，无法对后续段落缩进。

### 决策 5：不回填历史数据
已入库政策的 `content_text` 保留原样；下次按 `canonical_url` 重新抓取时由 upsert 更新为清洗后内容。

**理由**：回填需重新抓取或离线清洗历史 HTML，成本与失败面高，且无离线清洗所需原始文本保证。

## Risks / Trade-offs

- [清洗规则过度激进，误删正文段落] -> 缓解：仅剥离匹配窄模式的行（含「>」面包屑、纯时间戳、`来源：` 行、`阅读下一篇` 标记后截断）；不匹配的行一律保留；用真实样本编写单元测试覆盖。
- [清洗模式为新华网专属，未来新增适配器需各自实现] -> 缓解：清洗逻辑封装于 `XinhuaCollector`；若后续抽象共享适配器基类，再提取公共清洗助手。
- [`content_hash` 变化导致与历史哈希不一致] -> 缓解：upsert 以 `canonical_url` 定位而非哈希；哈希变化仅触发既有行更新，不产生重复政策。
- [前端分段把单行正文也渲染为单个 `<p>`，缩进对极短正文略显突兀] -> 缓解：首行缩进为政策正文常规排版，可接受；段落级渲染对单段文本仍正常显示。
- [历史未清洗政策在详情页分段时仍可能残留装饰行] -> 缓解：详情页分段渲染对任意文本均安全；残留装饰将在该政策下次重新抓取后随清洗消失。

## Migration Plan

1. 后端先行：在 `XinhuaCollector` 实现清洗并扩展单元测试（含页眉/页脚/无装饰/正文保留样本）；确认 `runner` 中 `content_hash` 与 `content_text` 自动使用清洗后内容。
2. 前端：改造 `PolicyDetailView.vue` 段落渲染与 `main.css` 样式，扩展详情页前端测试。
3. 部署：随下一次采集任务运行，新抓取政策即入库清洗后正文；既有政策在下次重新抓取时自然更新，无需数据库迁移或回填脚本。
4. 回滚：后端清洗为适配器内纯函数改动，回滚即恢复 `classify` 透传 `article.content`；前端回滚即恢复单块 `pre-wrap` 模板与样式。两者互不依赖，可独立回滚。

## Open Questions

- 是否需要对已入库政策提供一次性回填脚本？当前决定：不提供，依赖自然重新抓取；如后续阅读体验仍有残留，再评估离线清洗脚本。
- 是否在清洗时一并去除「新华社北京X月X日电」电头？当前决定：保留，电头属政策正文惯例而非页面装饰。
