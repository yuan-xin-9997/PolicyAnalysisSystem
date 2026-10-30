# 开源准备验证记录（2026-10-30，北京时间）

## 范围与隔离

远程 origin 已核对为 yuan-xin-9997/PolicyAnalysisSystem；原工作区检查时干净。本轮在独立工作目录 E:/dev/PolicyAnalysisSystem-open-source、分支 codex/open-source-prep 实施，基于当前 HEAD 4a7c701（未使用旧审查 HEAD 覆盖现状）。没有提交、推送、部署、发布、修改 remote、访问私人服务或轮换凭据。

## 实际验收

- `python -m policy_analysis.demo --data-dir <全新临时目录>`：首次 Imported 3；同目录重跑 Imported 0。未输出口令。
- `test_demo.py`：独立 SQLite 初始化、重复导入、保留口令、短中文检索、随机账号登录、真实异步比较 API 与报告、CSRF 拒绝均通过。
- 真实本地 uvicorn + Chromium：随机管理员登录，显示三份样本，选择教育两篇生成比较报告并从页面读取。实际截图位于 images/policy-demo.png、images/comparison-demo.png；全过程不使用 WebFetch/模型密钥。
- 文本边界：相同非空有效文本相似度 1、空正文 0、二字中文“教育”保留；样本前两篇余弦相似度 0.7526，页面 75.3%。
- `npm --prefix src/app/frontend run test -- --run`：18 个文件、78 项通过。
- `npm --prefix src/app/frontend run type-check`、`run lint`、`run build`：退出码 0。构建仍有既有单个大 chunk 提示，未额外拆包。
- `npm --prefix src/app/frontend run test:e2e`：Chromium 8 项通过。该套件使用现有模拟 API；上述真实样本流程另行验证。
- `ruff check src/app/backend src/tests/backend scripts/initialize_admin.py scripts/deployment.py` 与对应 `ruff format --check`：通过。

## 环境差异及未检查项

Windows 全套历史回归试跑出现 52 失败、721 通过、1 跳过，集中于凭据文件 POSIX 权限/替换语义；本轮未更改该权限机制；另用原工作区未修改的代码重跑两项代表性失败（私有文件权限、密码文件变更同步），两项均同样失败，确认至少这些问题在基线中存在。Windows 新增初始化与真实 demo 相关检查通过（14 项）。最终 Linux 命令 `python -m pytest src/tests/backend src/tests/smoke -q`：**786 passed in 215.33s**，包含权限、CSRF、真实 MCP SDK、配置覆盖、采集错误处理、初始化、离线样本及生命周期冒烟。

WSL 直接读取 Windows worktree 的 .git 指针会因 E: 路径失败；Linux 验证时仅为命令指定 GIT_DIR=/mnt/e/dev/PolicyAnalysisSystem/.git/worktrees/PolicyAnalysisSystem-open-source，不更改任何 Git 配置或远程。回环 MCP 测试主动设置 NO_PROXY，防止本地主机的 HTTP 代理拦截 127.0.0.1；真实服务请求策略没有改变。Shell 文件新增 LF 属性约束，防止 Windows 检出后 bash 语法检查失败。

GitHub 托管 runner 尚未运行；Actions 已配置为后端+冒烟、前端单元/静态/构建与 Playwright。Jenkins 文件仅静态和相关测试验证，没有触发构建或部署。未实际验证个人 WebFetch、翻译、OCR、外部网页或模型服务；不影响离线体验。部署与生产权限、现有默认账号更改、三类服务凭据轮换需人工完成，详见 open-source-security.md。
