## Why

抓取入库的政策正文目前保留了网页页面装饰信息：开头混入面包屑与来源行（如「新华网 > 时政 > 正文 2026 07/30 14:36:12 来源：新华网」），结尾混入「阅读下一篇」推荐区块，污染了正文内容与去重哈希；同时政策详情页将正文作为单个 `pre-wrap` 文本块渲染，没有段落分段和首行缩进，长篇政策阅读体验不佳。需要在前端展示之外，从抓取源头清洗正文，并改进详情页正文排版。

## What Changes

- 在采集器中对抓取到的文章正文进行清洗，剥离网页页面装饰信息：移除开头的面包屑/来源/时间行（如「新华网 > 时政 > 正文 … 来源：新华网」）以及结尾的「阅读下一篇：」推荐区块。
- 清洗在计算 `content_hash` 与持久化 `content_text` 之前完成，使去重哈希与存储内容均反映清洗后的纯正文。
- 将政策详情页正文由单个 `pre-wrap` 文本块改为按段落分段渲染，并对每个段落施加首行缩进，同时保持正文按纯文本安全渲染、不执行 HTML。
- 扩展采集器与详情页相关后端、前端测试，并同步项目文档。

## Capabilities

### New Capabilities
- `policy-content-cleaning`: 定义采集入库前对政策文章正文的清洗规则——剥离网页面包屑/来源/时间页眉行与「阅读下一篇」页脚区块，并归一化段落空白，保证存储与去重的正文为纯政策文本。

### Modified Capabilities
- `policy-database-experience`: 「可读且安全的政策正文详情」需求新增段落分段与首行缩进的正文排版要求，正文不再以单个预格式文本块展示。

## Impact

- 后端：`policy_analysis.collectors.xinhua`（新增正文清洗逻辑，并作用于 `Classification.content`）、`policy_analysis.tasks.runner`（确认清洗后的正文参与 `content_hash` 与 `PolicyWrite.content_text`）；不改变 WebFetch 客户端契约与现有采集判定阈值。
- 前端：`PolicyDetailView.vue` 与 `src/styles/main.css` 的 `.policy-content` 排版，由单块 `pre-wrap` 改为段落分段 + 首行缩进。
- 数据：不引入数据库迁移；仅影响新抓取政策的正文内容与哈希，已入库政策的旧正文不回填（详见 design）。
- 测试与文档：扩展新华网适配器/runner 单元测试、政策详情前端测试，并同步 README、需求与设计说明书中正文清洗与排版相关描述。
