## 1. 后端正文清洗实现

- [x] 1.1 在 `policy_analysis/collectors/xinhua.py` 中新增正文清洗函数：按行剥离开头页眉装饰行（含「>」面包屑、纯时间戳行、`来源：` 来源行），直到遇到第一个非装饰行
- [x] 1.2 在同一清洗函数中实现页脚剥离：定位首个「阅读下一篇」标记行并自该行起截断全部尾部内容
- [x] 1.3 在清洗函数中实现段落空白归一化：去除行首尾多余空白、合并连续空行为单空行，保留段落分隔
- [x] 1.4 在 `XinhuaCollector.classify` 中对 `article.content` 调用清洗函数，使 `Classification.content` 为清洗后正文；确认 `normalized_content`（用于判定）仍按既有 `_collapsed` 归一化，判定阈值与分类语义不变
- [x] 1.5 确认 `TaskRunner.run_claimed` 中 `content_hash` 与 `PolicyWrite.content_text` 自动使用清洗后的 `classification.content`，无需改动取值路径

## 2. 后端测试

- [x] 2.1 在 `src/tests/backend/collectors/test_xinhua_adapter.py` 新增页眉剥离测试：含「新华网 > 时政 > 正文 2026 07/30 14:36:12 来源：新华网」的样本清洗后以正文段落开头
- [x] 2.2 新增页脚剥离测试：含「阅读下一篇： 37 …」推荐区块的样本清洗后以最后一个正文段落结尾
- [x] 2.3 新增无装饰正文保留测试：不含页眉页脚的正文清洗后内容与段落不变
- [x] 2.4 新增正文主体保留测试：多段正式政策文本在清洗后主体段落与换行/空行分隔完整保留
- [x] 2.5 在 `src/tests/backend/tasks/test_task_runner.py` 扩展或新增用例，断言 `content_hash` 与持久化 `content_text` 基于清洗后正文、且采集判定结果不受清洗影响
- [x] 2.6 运行后端测试套件确认通过（`pytest src/tests/backend`）

## 3. 前端正文分段渲染与样式

- [x] 3.1 在 `PolicyDetailView.vue` 中将 `policy.content_text` 按换行/空行切分为段落数组，每个段落以独立 `<p>` 文本插值渲染（不使用 `v-html`）
- [x] 3.2 在 `src/styles/main.css` 中更新 `.policy-content` 样式：移除单块 `white-space: pre-wrap`，改为对 `.policy-content p` 施加 `text-indent: 2em`、段落间 `margin`、`overflow-wrap: anywhere`/`word-break: break-word` 折行
- [x] 3.3 确认窄屏下段落仍保持分段与首行缩进（沿用既有响应式断点）

## 4. 前端测试

- [x] 4.1 在 `src/tests/frontend/policy-detail.spec.ts` 新增段落分段测试：多段正文（换行/空行分隔）渲染为多个独立段落块
- [x] 4.2 新增首行缩进测试：段落元素具有 `text-indent` 对应样式（两个汉字宽度）
- [x] 4.3 保留并扩展纯文本安全测试：含 `<script>`/标签字符序列的正文作为可见纯文本显示、不执行 HTML
- [x] 4.4 运行前端单元测试（`vitest`）与相关 Playwright 端到端测试确认通过

## 5. 文档同步

- [x] 5.1 在 `README.md` 与 `docs/superpowers/specs/2026-07-31-policy-analysis-system-mvp-design.md` 中补充正文清洗规则（剥离页眉/页脚装饰、清洗作用于去重与持久化）与详情页段落分段/首行缩进排版的说明

## 6. 验证与归档

- [x] 6.1 运行 `openspec validate clean-policy-content-and-display --strict` 确认规格与变更工件校验通过
- [x] 6.2 运行后端与前端全量测试确认无回归
- [x] 6.3 完成后执行 `openspec archive clean-policy-content-and-display` 归档变更并合并到 `openspec/specs/`
