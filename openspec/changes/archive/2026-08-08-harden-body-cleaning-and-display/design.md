## Context

采集链路为：`TaskRunner` 调 `WebFetchClient.extract_article(url)` 取回 `ExtractedArticle`（`content` 来自 WebFetch `generic.article` 适配器），交 `XinhuaCollector.classify` 产出 `Classification`，`TaskRunner` 再以 `classification.content` 计算 `content_hash` 并写入 `PolicyWrite.content_text`。`WebFetch` Protocol 同时暴露 `fetch_text(url)`（返回原始 `body`，用于 RSS/链接发现）。

实测真实新华网文章（`http://www.news.cn/politics/...c_*.htm`）发现根因：`generic.article` 适配器把正文抽取为**单行空格拼接文本（0 个换行、0 个双空格）**，既丢失原文 `<p>` 段落结构，又把页眉「新华网 > > 正文 2022 12/ 14 11:37:37 来源：新华社」、编辑署名页脚「策划：… 新华社音视频部制作 新华通讯社出品」「【纠错】 【责任编辑:xxx】」「阅读下一篇：N …」及尾部跟踪 ID「01002002011000…」内联进正文。原始 HTML（`v1/fetch` 取回）则结构良好：正文位于 `<div id="detail">` 内，含 27 个 `<p>` 块，每块为干净段落。

现有 `_clean_content` 按 `splitlines()` 行剥离装饰、详情页 `paragraphs` 按 `\n` 切分，二者都假定正文带换行。测试夹具注入了 `\n`（如 `…来源：新华网\n新华社北京7月30日电…`）故通过；真实数据无换行，清洗与分段双双失效。此外 `_SOURCE_SEGMENT = 来源：[^\s]+` 在「来源：新华社」与正文无空格粘连时会贪心吞掉整段正文。

约束：WebFetch 客户端契约不可变；采集判定阈值（最小正文字数、关键词、来源校验、滚动窗口）不可放松；正文须纯文本安全渲染、不得执行 HTML；不引入新数据库；`XinhuaCollector` 保持纯函数式、无 I/O。

## Goals / Non-Goals

**Goals:**
- 从抓取源头恢复政策正文段落结构：按原始 HTML `<p>` 提取段落，使 `content_text` 为换行分隔的纯净段落化正文。
- 剥离真实页面装饰变体：双 `>` 面包屑、带空格时间戳、编辑署名块、`【纠错】`/`【责任编辑:xxx】`、`阅读下一篇：N …`、尾部跟踪 ID；修正 `来源：[^\s]+` 吞正文缺陷。
- 详情页分段 + 首行缩进对真实（段落化）正文生效，并对无换行存量正文提供安全兜底。
- 清洗逻辑无 I/O、可单测；测试夹具反映真实 WebFetch 输出格式。

**Non-Goals:**
- 不修改 WebFetch 服务（`generic.article` 适配器的扁平化行为属外部服务，本仓库不改）。
- 不为存量政策重新抓取 HTML 以即时恢复段落（依赖下次 upsert 重新抓取自然恢复；重洗脚本仅做内联清洗）。
- 不清洗标题、作者、发布时间等元数据字段。
- 不引入通用 NLP 段落归纳或正文摘要。

## Decisions

### 决策 1：按原始 HTML `<p>` 结构恢复段落，而非清洗扁平化文本
对通过采集判定的文章，`TaskRunner` 额外调 `fetch_text(url)` 取回原始 HTML，由纯函数助手解析正文区域（优先 `<div id="detail">`，回退到全文）内的 `<p>` 块，逐块提取纯文本并以 `\n` 拼接为段落化正文，作为 `content_text` 来源；`extract_article` 仍提供标题/作者/发布时间/`artifact_id`。

**理由**：WebFetch 扁平化文本已不可逆地丢失段落边界（单空格既分隔句内词也分隔段间），任何基于扁平文本的启发式切分（如按「。」切句）只能得到句子级而非段落级分段，无法满足「段落分段」诉求；原始 HTML 的 `<p>` 结构是段落边界的唯一可靠来源，且解析 `<p>` 还天然排除多数位于非 `<p>` 元素的页眉装饰（面包屑/来源行不在 `<div id="detail">` 的 `<p>` 内）。

**备选**：保留 `generic.article` 扁平文本，重写清洗为内联正则剥离装饰 + 按「。」切句分段。被否：切句产生过多短段、非真实段落；且内联剥离双 `>`/带空格时间戳等变体的正则复杂且易误伤。

### 决策 2：HTML 解析为纯函数助手置于 collectors 模块，I/O 仍在 runner
新增 `extract_paragraphs(html: str) -> str` 纯函数（基于标准库 `html.parser`，无新依赖、无 I/O），在 `TaskRunner` 取回 HTML 后调用，产出的段落化正文随 `ExtractedArticle` 一并传入 `classify`。`XinhuaCollector` 仍只做纯计算。

**理由**：保持适配器无 I/O 的既有约束与可测性；HTML 解析是纯文本变换，归入 collectors 域合理；runner 继续独占 WebFetch 调用。

**备选**：在 `WebFetchClient.extract_article` 内部解析 `<p>`。被否：会改变 WebFetch 客户端契约与 `ExtractedArticle` 语义，且把新华网专属选择器散落到客户端。

### 决策 3：清洗规则扩展为「段落级 + 内联兜底」
`_clean_content` 在段落化正文（带 `\n`）上运行：按行/段落剥离残余装饰（编辑署名块关键词、`【纠错】`、`【责任编辑:xxx】`、`阅读下一篇` 起截断、尾部纯数字跟踪 ID），并修正 `来源：` 段为非贪心且仅在确认页眉上下文时剥离；对无 `<p>` 回退到的扁平文本，以内联前缀/后缀正则剥离。清洗仍发生于 `classify` 内，`classification.content` 即为段落化清洗后正文，`content_hash` 与 `content_text` 取值路径不变。

**理由**：`<p>` 解析已排除大部分页眉，清洗聚焦残余页脚与异常；保留按行规则可复用既有结构，内联兜底覆盖回退路径。修正 `来源：[^\s]+` 避免吞正文。

### 决策 4：存量数据重洗仅内联清洗，真段落靠重新抓取恢复
扩展 `scripts/reclean_policy_content.py`：对已入库扁平化 `content_text` 应用新清洗规则剥离装饰并重算 `content_hash`；不重新抓取 HTML，故存量正文段落结构不在重洗中恢复，而在该政策下次 upsert 重新抓取时由决策 1 自然恢复。详情页对无换行存量正文按决策 5 兜底。

**理由**：离线重抓 HTML 需对每条存量政策发起网络请求，成本与失败面高；既有 upsert 以 `canonical_url` 定位，重新抓取即更新为段落化正文，无需迁移。

### 决策 5：前端分段沿用 + 无换行安全兜底
`PolicyDetailView.vue` 的 `paragraphs` 按 `\n` 切分、`.policy-content p` 的 `text-indent: 2em` 沿用；补齐兜底：当 `content_text` 无换行时整体作为单段渲染仍施加首行缩进（既有 `filter(Boolean)` 已天然产生单段，仅需确认样式覆盖单段场景）。不引入 `v-html`。

**理由**：新数据已段落化，既有渲染即可；兜底保证存量数据不报错、不退化。

## Risks / Trade-offs

- [HTML 解析对页面模板脆弱，不同新华网模板 `<p>` 结构差异] -> 缓解：优先 `<div id="detail">` 内 `<p>`，`<p>` 数量过少或为空时回退到扁平文本 + 内联清洗；清洗作为安全网；用多模板真实样本写测试。
- [每篇通过判定文章多一次 `fetch_text` 调用，增加 I/O] -> 缓解：WebFetch 缓存；`extract_article` 与 `fetch_text` 命中同 URL。后续可优化为从 `extract_article` 保存的 artifact HTML 解析段落、或单次抓取并自行解析元数据以消除二次调用（见 Open Questions）。
- [清洗误删正文段落] -> 缓解：仅匹配窄模式（编辑署名关键词集合、`阅读下一篇` 标记截断、纯数字尾部 ID、确认页眉上下文的 `来源：`）；不匹配一律保留；真实样本测试。
- [`content_hash` 变化与历史哈希不一致] -> 缓解：upsert 以 `canonical_url` 定位而非哈希；哈希变化仅触发既有行更新，不产生重复政策。
- [存量正文无段落结构，详情页单段显示] -> 缓解：兜底单段 + 首行缩进可读；重洗剥离装饰；下次重新抓取恢复段落。

## Migration Plan

1. 后端先行：实现 `extract_paragraphs` 纯函数 + 扩展 `_clean_content` + runner 对通过判定文章取回 HTML 并以段落化正文入库；单元测试夹具改用真实 WebFetch 输出格式（单行、双 `>` 面包屑、带空格时间戳、编辑署名页脚、跟踪 ID）。
2. 存量重洗：扩展 `reclean_policy_content.py` 应用新清洗；在部署环境 `--dry-run` 预览后执行写库。
3. 前端：确认段落分段 + 首行缩进对段落化正文生效，补齐无换行兜底与测试。
4. 部署：新抓取政策入库段落化纯正文；存量经重洗剥离装饰；存量段落于下次重新抓取恢复。
5. 回滚：runner 回退为仅用 `extract_article.content`；清洗与前端兜底可独立回滚，互不依赖。

## Open Questions

- 是否让重洗脚本对存量政策重新抓取 HTML 以即时恢复段落（每条一次网络请求）？当前决定：否，依赖自然重新抓取；如用户希望存量即时分段，再评估离线重抓。
- 是否消除 `extract_article` + `fetch_text` 的二次抓取（改为单次 `fetch_text` 取 HTML 并自行解析标题/作者/时间）？当前决定：保留 `extract_article` 以复用适配器元数据提取的正确性；若 I/O 开销显著再优化。
