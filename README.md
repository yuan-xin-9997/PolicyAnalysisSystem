# PolicyAnalysisSystem

PolicyAnalysisSystem 是一个使用 FastAPI、Vue 3 和 SQLite 构建的政策采集与分析系统。当前后端已完成登录、权限、配置展示、来源规则、采集任务、WebFetch 抓取、政策去重入库、全文检索和任务调度能力。

## 本地开发

### 前置条件

- Python 3.12
- Node.js `^20.19.0 || >=22.12.0`
- npm（随 Node.js 安装）

在仓库根目录创建后端虚拟环境并安装开发依赖：

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

安装前端锁定依赖：

```bash
npm --prefix src/app/frontend ci
```

### 配置

[`src/config/app.json`](src/config/app.json) 只保存无密钥配置。配置优先级从高到低为：

1. `POLICY_ANALYSIS_` 前缀的环境变量；
2. `src/config/app.json`；
3. 代码中的非环境默认值。

嵌套配置使用双下划线分隔。例如，以下命令只展示虚构占位符，运行时应替换为本机密钥，并且不得把真实值提交到版本库：

```bash
export POLICY_ANALYSIS_AUTH__SESSION_SECRET='replace-with-a-long-random-session-secret'
export POLICY_ANALYSIS_WEBFETCH__BASE_URL='http://127.0.0.1:33333'
export POLICY_ANALYSIS_WEBFETCH__API_KEY='replace-with-your-webfetch-api-key'
```

数据库文件和密码文件默认使用项目内相对路径。服务器端口在 `src/config/app.json` 中配置为 `30080`。

### 首次准备登录账号

首次本地运行前创建 `src/data/password.txt`，每行格式为：

```text
username:password:role
```

`role` 只能是 `admin` 或 `user`。首次部署允许使用项目约定的默认管理员：

```text
# 仅用于首次部署；首次登录后必须立即修改密码
admin:admin123:admin
```

先创建 `src/data` 目录，并确保 `password.txt` 只允许当前用户读写。在 Linux 或 macOS 上可以执行：

```bash
mkdir -p src/data
touch src/data/password.txt
chmod 600 src/data/password.txt
```

把上面的默认内容写入新文件后即可首次登录。不要把生产账号、生产密码或任何服务密钥写入 README、配置文件或提交记录。

### 启动与访问

先构建前端，再从仓库根目录启动后端；FastAPI 会提供已构建的单页应用：

```bash
npm --prefix src/app/frontend run build
.venv/bin/uvicorn policy_analysis.main:app --app-dir src/app/backend --host 127.0.0.1 --port 30080
```

本地访问地址：

- 系统页面：<http://127.0.0.1:30080/>
- 存活检查：<http://127.0.0.1:30080/health/live>
- 就绪检查：<http://127.0.0.1:30080/health/ready>

根目录启停脚本、systemd 和 Jenkins 部署能力尚未在当前平台阶段交付，不能用这些入口替代上述开发命令。

## 平台阶段验证

从仓库根目录运行后端测试、静态检查和格式检查：

```bash
.venv/bin/pytest src/tests/backend/test_platform_smoke.py -v
.venv/bin/ruff check src/app/backend src/tests/backend
.venv/bin/ruff format --check src/app/backend src/tests/backend
.venv/bin/pytest --cov=policy_analysis --cov-report=term-missing --cov-fail-under=80 src/tests/backend -q
```

运行前端类型检查、静态检查、单元测试和生产构建：

```bash
npm --prefix src/app/frontend run type-check
npm --prefix src/app/frontend run lint
npm --prefix src/app/frontend run test -- --run
npm --prefix src/app/frontend run build
npm --prefix src/app/frontend run test:e2e
```

`test:e2e` 使用 Playwright 启动 Vite 开发服务器，并通过浏览器网络拦截模拟安全的后端测试数据，覆盖管理员登录、主导航、政策检索、政策详情纯文本展示、手工触发采集任务和任务详情日志等主路径。

## 政策数据库页面

政策数据库为有 `policies` 页面权限的用户提供以下查询与阅读能力：

- “标题关键词”仅在政策标题中执行包含匹配；
- “正文全文检索”使用 SQLite FTS5 trigram 索引查询政策正文，一至二字短词使用安全的字面包含匹配；
- 发布部门、政策类别和政策来源使用数据库中已有政策派生的下拉选项，不需要手工输入 ID；
- 发布时间与最近抓取时间支持升序、降序切换，并明确显示“最早优先”或“最新优先”；
- 所有筛选、分页和排序状态写入 URL，可通过刷新或分享链接恢复；
- 政策详情以纯文本显示正文并保留换行，不解析或执行抓取内容中的 HTML。

相关只读 API：

- `GET /api/v1/policies/filters`：返回去重且稳定排序的 `publishers`、`categories`、`sources`；选项仅包含至少关联一条政策的数据；
- `GET /api/v1/policies`：支持 `keyword`、`full_text`、`publisher`、`category_id`、`source_id`、发布时间/抓取时间范围、分页及排序参数；多个条件使用 AND 组合；
- `GET /api/v1/policies/{policy_id}`：返回政策元数据和纯文本正文。

接口均要求登录并具有政策数据库页面权限。页面显示时间统一转换为北京时间。需求行为与技术决策分别维护在 OpenSpec 变更的 `specs/policy-database-experience/spec.md` 和 `design.md` 中。

## 政策采集后端

采集后端通过 WebFetch 服务获取 RSS、栏目页面和文章正文。运行前需要配置：

```bash
export POLICY_ANALYSIS_WEBFETCH__BASE_URL='http://your-webfetch-service'
export POLICY_ANALYSIS_WEBFETCH__API_KEY='replace-with-your-webfetch-api-key'
```

管理员登录后，在任务中心相关 API 中维护来源、采集规则和定时计划。首次回填建议流程：

1. 创建或确认来源、政策类别和采集规则；
2. 先使用 `POST /api/v1/tasks` 手工触发一次回填；
3. 通过 `GET /api/v1/tasks/{task_id}` 查看终态和进度；
4. 通过 `GET /api/v1/tasks/{task_id}/items` 查看每篇候选文章的处理结果；
5. 通过 `GET /api/v1/tasks/{task_id}/logs` 查看采集日志和失败原因；
6. 确认政策库检索结果正确后，再启用对应定时计划。

常见失败会记录为稳定的 `reason_code`，例如标题未命中、正文过短、超出回填窗口、来源非官方、重复政策或 WebFetch 临时不可用。任务调度使用单进程 worker 和 APScheduler；`/health/ready` 会在 SQLite 可用且 worker 已启动时返回就绪。
