## 1. 后端：HTML 段落解析助手

- [x] 1.1 在 `policy_analysis/collectors/` 下新增纯函数 `extract_paragraphs(html: str) -> str`：基于标准库 `html.parser`（无新依赖），优先解析 `<div id="detail">` 内的 `<p>` 块，逐块提取纯文本（去除内联标签），以 `\n` 拼接为段落化正文
- [x] 1.2 实现 `<p>` 数量过少或正文区域缺失时的回退：回退到对扁平化文本做内联装饰剥离，绝不返回空串
- [x] 1.3 为 `extract_paragraphs` 编写单元测试：用真实新华网文章 HTML 片段（含 `<div id="detail">` + 多个 `<p>`）断言段落以换行分隔、内联标签被剥离、无 `<p>` 时安全回退

## 2. 后端：加固正文清洗规则

- [x] 2.1 扩展 `policy_analysis/collectors/xinhua.py` 的 `_clean_content`：剥离编辑署名页脚（「策划：」「监制：」「统筹：」「编导：」「记者：」「配音：」「新华社音视频部制作」「新华通讯社出品」等关键词所在段落）
- [x] 2.2 在 `_clean_content` 中剥离「【纠错】」「【责任编辑:xxx】」标记与「阅读下一篇：N …」推荐区块（自标记起截断）及尾部纯数字跟踪 ID
- [x] 2.3 修正 `_SOURCE_SEGMENT`（`来源：[^\s]+`）在无空格粘连时吞正文的缺陷：改为非贪心或在确认页眉上下文时才剥离；覆盖双 `>` 面包屑「新华网 > > 正文」与带空格时间戳「2022 12/ 14」等真实变体
- [x] 2.4 确认 `_clean_content` 对段落化正文（带 `\n`）与回退扁平文本（无 `\n`）两条路径均生效，且不删除正文主体段落

## 3. 后端：runner 接入段落化正文

- [x] 3.1 在 `policy_analysis/tasks/runner.py` 对通过采集判定的文章，额外调用 `self._webfetch.fetch_text(url)` 取回原始 HTML，调用 `extract_paragraphs` 产出段落化正文
- [x] 3.2 将段落化正文作为 `content_text` 来源（经 `_clean_content` 清洗），`content_hash` 基于该段落化清洗后正文计算；`extract_article` 仍提供标题/作者/发布时间/`artifact_id`
- [x] 3.3 确认 `fetch_text` 失败或 HTML 解析回退时不阻断采集（回退到 `extract_article.content` + 内联清洗），并记录可观测日志

## 4. 后端测试（真实格式夹具）

- [x] 4.1 在 `src/tests/backend/collectors/test_xinhua_adapter.py` 新增/改写夹具，使用真实 WebFetch 输出格式：单行空格拼接、双 `>` 面包屑、带空格时间戳、编辑署名页脚、`【纠错】`/`【责任编辑:xxx】`、`阅读下一篇：N …`、尾部跟踪 ID
- [x] 4.2 新增「段落结构从 HTML `<p>` 恢复」测试：扁平文本无换行、但 HTML 含多个 `<p>` 时，清洗后正文带段落分隔
- [x] 4.3 新增「残余页眉/编辑署名/推荐区块/跟踪 ID 被剥离」与「正文主体段落保留」测试
- [x] 4.4 新增「`来源：` 不吞正文」回归测试：`来源：新华社` 与正文无空格粘连时不丢失正文段落
- [x] 4.5 在 `src/tests/backend/tasks/test_task_runner.py` 扩展用例：`content_hash` 与 `content_text` 基于段落化清洗后正文、采集判定不受影响、`fetch_text` 失败时安全回退
- [x] 4.6 运行 `pytest src/tests/backend` 确认通过

## 5. 存量重洗脚本

- [x] 5.1 扩展 `scripts/reclean_policy_content.py`：应用新版 `_clean_content`（含编辑署名/纠错/推荐区块/跟踪 ID 剥离与 `来源：` 修正）对存量 `content_text` 重洗并重算 `content_hash`
- [x] 5.2 确认脚本幂等、仅更新确有变化的行、`policies_fts_au` 触发器同步全文索引；`--dry-run` 预览正常
- [x] 5.3 为脚本新增/扩展单元或冒烟测试（构造含装饰的存量正文，断言重洗后装饰被剥离、哈希重算）

## 6. 前端：分段与首行缩进兜底

- [x] 6.1 确认 `PolicyDetailView.vue` 的 `paragraphs` 切分与 `.policy-content p` 的 `text-indent: 2em` 对段落化正文（带换行）生效，渲染多个独立段落块
- [x] 6.2 补齐无换行存量正文的安全兜底：整体作为单段渲染仍施加首行缩进，不报错、不执行 HTML（确认不使用 `v-html`）
- [x] 6.3 在 `src/tests/frontend/policy-detail.spec.ts` 新增「正文无原始换行仍按段落分段」与「存量无换行正文安全兜底」用例；运行 `vitest` 与相关 Playwright 端到端测试确认通过

## 7. 文档同步

- [x] 7.1 在 `README.md` 与 `docs/superpowers/specs/2026-07-31-policy-analysis-system-mvp-design.md` 补充：正文按 HTML `<p>` 结构提取段落、扩展清洗覆盖的装饰变体、详情页分段不依赖原始换行及存量兜底说明
- [x] 7.2 同步需求规格说明书与设计说明书中正文清洗与详情页排版相关描述

## 8. 验证与归档

- [x] 8.1 运行 `openspec validate harden-body-cleaning-and-display --strict` 确认规格与变更工件校验通过
- [x] 8.2 运行后端与前端全量测试确认无回归
- [x] 8.3 完成后执行 `openspec archive harden-body-cleaning-and-display` 归档变更并合并到 `openspec/specs/`
