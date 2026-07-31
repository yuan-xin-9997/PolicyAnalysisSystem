# 部署运维与交付实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 完成跨平台启停、日志轮转、systemd、Jenkins、需求与设计文档、真实 WebFetch 冒烟、GitHub 推送和 Ubuntu 部署验收。

**架构：** 本地脚本只使用项目相对路径；Linux 生产由单个 systemd 服务运行。Jenkins 在工作区测试和构建后，通过受控部署脚本增量同步代码，首次创建配置与数据，后续始终保留现场状态。

**技术栈：** Bash、PowerShell、systemd、Jenkins Declarative Pipeline、rsync、curl、pytest、GitHub SSH。

---

## 实施前提

- 先依次完成平台、采集后端和业务前端计划。
- 部署目标默认值为 `/opt/PolicyAnalysisSystem`，但脚本通过参数或 Jenkins 环境变量读取。
- 生产监听端口由 `src/config/app.json` 配置为 `30080`。
- 不提交服务器密码、Jenkins 密码、API Key 或生产会话密钥。
- 所有完成声明必须按 `verification-before-completion` 技能重新运行验证。

## 文件结构与职责

| 文件或目录 | 职责 |
| --- | --- |
| `start.sh`、`stop.sh`、`status.sh` | Linux/macOS 本地生命周期 |
| `start.ps1`、`stop.ps1`、`status.ps1` | Windows 本地生命周期 |
| `deploy/systemd/policy-analysis-system.service` | 单进程生产服务模板 |
| `deploy/install-systemd.sh` | 参数化安装服务和环境文件 |
| `deploy/jenkins-deploy.sh` | 安装为固定 root 所有入口的增量同步脚本 |
| `deploy/templates/password.txt` | 首次部署默认凭据模板 |
| `src/JenkinsConfig/Jenkinsfile` | 测试、构建、部署和健康检查 |
| `docs/requirements/policy-analysis-system-srs.md` | 需求规格说明书 |
| `docs/design/policy-analysis-system-design.md` | 当前实现设计说明书 |
| `src/tests/smoke/` | 生命周期、部署保留和生产冒烟测试 |

### 任务 1：实现日志轮转和应用 CLI

**文件：**
- 修改：`src/app/backend/policy_analysis/core/logging.py`
- 创建：`src/app/backend/policy_analysis/cli.py`
- 创建：`src/app/backend/policy_analysis/__main__.py`
- 测试：`src/tests/backend/core/test_logging.py`
- 测试：`src/tests/backend/test_cli.py`

- [ ] **步骤 1：编写日志文件名和密钥脱敏测试**

```python
# src/tests/backend/core/test_logging.py
from datetime import date

from policy_analysis.core.logging import configure_logging


def test_logging_writes_current_file_and_redacts_loaded_secrets(tmp_path) -> None:
    logger = configure_logging(tmp_path, secrets={"secret-api-key"}, retention_days=30)
    logger.info("authorization=secret-api-key")
    current = tmp_path / "app.log"
    assert current.exists()
    content = current.read_text(encoding="utf-8")
    assert "secret-api-key" not in content
    assert "********" in content
```

增加测试：午夜轮转名称为 `app.YYYY-MM-DD.log`、超出保留天数的单个历史日志被清理、非日志文件不删除、CLI 参数覆盖 host/port 但不修改 `app.json`。

- [ ] **步骤 2：运行并确认失败**

运行：`.venv/bin/pytest src/tests/backend/core/test_logging.py src/tests/backend/test_cli.py -v`

预期：FAIL，缺少日志配置或 CLI。

- [ ] **步骤 3：实现日志与 CLI**

使用标准库 `TimedRotatingFileHandler(when="midnight", backupCount=retention_days, encoding="utf-8")`，自定义 namer 生成 `app.YYYY-MM-DD.log`。Filter 同时按敏感字段名和已加载 SecretStr 值脱敏。

CLI 固定支持：

```text
python -m policy_analysis serve --config src/config/app.json
python -m policy_analysis migrate --config src/config/app.json
python -m policy_analysis create-default-password --path src/data/password.txt
```

`serve` 以单进程启动 Uvicorn，拒绝 `workers > 1` 配置。

- [ ] **步骤 4：验证**

运行：`.venv/bin/pytest src/tests/backend/core/test_logging.py src/tests/backend/test_cli.py -v`

预期：全部 PASS。

- [ ] **步骤 5：提交**

```bash
git add src/app/backend/policy_analysis src/tests/backend/core/test_logging.py src/tests/backend/test_cli.py
git commit -m "feat(运维): 添加日志轮转与应用命令行"
```

### 任务 2：实现 Linux 与 Windows 生命周期脚本

**文件：**
- 创建：`start.sh`
- 创建：`stop.sh`
- 创建：`status.sh`
- 创建：`start.ps1`
- 创建：`stop.ps1`
- 创建：`status.ps1`
- 测试：`src/tests/smoke/test_lifecycle_scripts.py`

- [ ] **步骤 1：先写临时项目生命周期测试**

测试复制脚本到临时目录，用一个提供 `/health/live` 的短生命周期测试服务替代真实应用，验证：重复启动不创建第二进程、PID 文件准确、状态同时检查 PID 与健康接口、停止只终止 PID 文件指定进程、失效 PID 自动清理。

```python
# src/tests/smoke/test_lifecycle_scripts.py
def test_shell_lifecycle_is_idempotent(lifecycle_project) -> None:
    first = lifecycle_project.run("start.sh")
    second = lifecycle_project.run("start.sh")
    assert first.returncode == 0
    assert second.returncode == 0
    assert "已在运行" in second.stdout
    assert lifecycle_project.run("status.sh").returncode == 0
    assert lifecycle_project.run("stop.sh").returncode == 0
    assert lifecycle_project.run("status.sh").returncode != 0
```

- [ ] **步骤 2：运行并确认脚本缺失失败**

运行：`.venv/bin/pytest src/tests/smoke/test_lifecycle_scripts.py -v`

预期：FAIL，`start.sh` 不存在。

- [ ] **步骤 3：实现 6 个脚本**

Shell 脚本使用 `SCRIPT_DIR` 解析项目根目录，禁止依赖调用者当前目录。启动前创建 `src/logs` 和 `src/data`，缺少密码文件时调用 CLI 创建默认模板；生产密钥缺失时启动失败并给出配置名，不回显值。

PowerShell 使用 `$PSScriptRoot`，语义与 Shell 一致。PID 文件固定为 `src/logs/server.pid`。停止流程先发送正常终止并等待可配置超时，超时后才终止该精确 PID，不使用进程名批量杀进程。

- [ ] **步骤 4：验证语法和行为**

运行：`bash -n start.sh stop.sh status.sh`

运行：`.venv/bin/pytest src/tests/smoke/test_lifecycle_scripts.py -v`

如果当前环境存在 `pwsh`，运行：`pwsh -NoProfile -Command "@('start.ps1','stop.ps1','status.ps1') | ForEach-Object { [scriptblock]::Create((Get-Content $_ -Raw)) | Out-Null }"`。

预期：Shell 语法和行为测试 PASS；PowerShell 可用时语法 PASS。

- [ ] **步骤 5：提交**

```bash
git add start.sh stop.sh status.sh start.ps1 stop.ps1 status.ps1 src/tests/smoke/test_lifecycle_scripts.py
git commit -m "feat(运维): 添加跨平台启停与状态脚本"
```

### 任务 3：实现 systemd 与保留现场状态的部署脚本

**文件：**
- 创建：`deploy/systemd/policy-analysis-system.service`
- 创建：`deploy/install-systemd.sh`
- 创建：`deploy/jenkins-deploy.sh`
- 创建：`deploy/templates/password.txt`
- 测试：`src/tests/smoke/test_deploy_preserves_state.py`
- 测试：`src/tests/smoke/test_systemd_unit.py`

- [ ] **步骤 1：编写首次部署和增量保留测试**

```python
# src/tests/smoke/test_deploy_preserves_state.py
def test_incremental_deploy_preserves_config_data_and_logs(deploy_fixture) -> None:
    target = deploy_fixture.first_deploy()
    config = target / "src/config/app.json"
    database = target / "src/data/app.sqlite3"
    log = target / "src/logs/app.log"
    config.write_text('{"server":{"port":39999}}', encoding="utf-8")
    database.write_bytes(b"user-database")
    log.write_text("user-log", encoding="utf-8")

    deploy_fixture.incremental_deploy()

    assert config.read_text(encoding="utf-8") == '{"server":{"port":39999}}'
    assert database.read_bytes() == b"user-database"
    assert log.read_text(encoding="utf-8") == "user-log"
```

首次部署测试断言创建配置、密码、数据和日志目录，密码文件权限为 `0600`。systemd 测试解析单元文件，断言单进程、低权限用户、环境文件、重启策略和健康相关依赖。

- [ ] **步骤 2：运行并确认失败**

运行：`.venv/bin/pytest src/tests/smoke/test_deploy_preserves_state.py src/tests/smoke/test_systemd_unit.py -v`

预期：FAIL，部署文件不存在。

- [ ] **步骤 3：实现参数化部署**

`deploy/jenkins-deploy.sh` 参数固定为源目录、目标目录、版本号和短 SHA。先验证两个路径不是空、`/`、用户主目录或同一路径，并校验版本与 SHA 只含安全字符，再使用 rsync 同步。增量同步排除：

```text
src/config/app.json
src/data/**
src/logs/**
.venv/**
```

首次部署时从仓库 `src/config/app.json` 和 `deploy/templates/password.txt` 复制缺失文件。每次同步后在目标目录创建或更新 `.venv`，执行 `pip install -e <目标目录>`；前端 `dist` 由 Jenkins 构建后随代码同步。版本与 SHA 写入 `/etc/policy-analysis-system/build.env`，不得修改包含密钥的 `service.env`。部署脚本只操作解析后的单个目标目录，不接受 glob，不删除目标根目录。

`install-systemd.sh` 将部署入口安装为 root 所有、不可由 Jenkins 修改的 `/usr/local/sbin/policy-analysis-jenkins-deploy`。systemd 单元通过 `/etc/policy-analysis-system/service.env` 读取部署目录、配置路径、WebFetch API Key 和会话密钥；`ExecStart` 使用目标 `.venv/bin/python -m policy_analysis serve`，不配置多 worker。

- [ ] **步骤 4：验证部署安全边界**

运行：`bash -n deploy/install-systemd.sh deploy/jenkins-deploy.sh`

运行：`.venv/bin/pytest src/tests/smoke/test_deploy_preserves_state.py src/tests/smoke/test_systemd_unit.py -v`

预期：全部 PASS；传入 `/` 和空目标的测试必须拒绝且不修改文件。

- [ ] **步骤 5：提交**

```bash
git add deploy src/tests/smoke/test_deploy_preserves_state.py src/tests/smoke/test_systemd_unit.py
git commit -m "feat(部署): 添加 systemd 与状态保留部署脚本"
```

### 任务 4：建立 Jenkins Pipeline

**文件：**
- 创建：`src/JenkinsConfig/Jenkinsfile`
- 创建：`src/tests/smoke/test_jenkinsfile.py`
- 修改：`deploy/jenkins-deploy.sh`

- [ ] **步骤 1：编写 Pipeline 结构测试**

```python
# src/tests/smoke/test_jenkinsfile.py
from pathlib import Path


def test_jenkinsfile_has_poll_test_build_deploy_and_health_stages() -> None:
    text = Path("src/JenkinsConfig/Jenkinsfile").read_text(encoding="utf-8")
    assert "pollSCM('H/30 * * * *')" in text
    for stage in ["Checkout", "Version", "Backend Test", "Frontend Test", "Build", "Frontend E2E", "Stop", "Deploy", "Start", "Health Check"]:
        assert f"stage('{stage}')" in text
    assert "src/config/app.json" in text
    assert "src/data" in text
    assert "src/logs" in text
    assert "/usr/local/sbin/policy-analysis-jenkins-deploy" in text
    assert "curl" in text and "/health/ready" in text
```

增加测试：部署目录来自参数、仓库地址不硬编码 HTTPS、Pipeline 不包含密码/API Key、版本使用 Git 提交总数与短 SHA。

- [ ] **步骤 2：运行并确认 Jenkinsfile 缺失失败**

运行：`.venv/bin/pytest src/tests/smoke/test_jenkinsfile.py -v`

预期：FAIL，Jenkinsfile 不存在。

- [ ] **步骤 3：实现 Declarative Pipeline**

Pipeline 必须包含：

```groovy
pipeline {
  agent any
  triggers { pollSCM('H/30 * * * *') }
  parameters {
    string(name: 'DEPLOY_DIR', defaultValue: '/opt/PolicyAnalysisSystem', description: '部署目录')
    string(name: 'HEALTH_URL', defaultValue: 'http://127.0.0.1:30080/health/ready', description: '就绪检查地址')
  }
  environment {
    VENV_DIR = '.jenkins-venv'
  }
  stages {
    stage('Checkout') { steps { checkout scm } }
    stage('Version') { steps { script { env.APP_VERSION = "v0.${sh(script: 'git rev-list --count HEAD', returnStdout: true).trim()}"; env.COMMIT_SHA = sh(script: 'git rev-parse --short HEAD', returnStdout: true).trim() } } }
    stage('Backend Test') { steps { sh 'python3 -m venv $VENV_DIR && $VENV_DIR/bin/pip install -e ".[dev]" && $VENV_DIR/bin/ruff check src/app/backend src/tests/backend scripts && $VENV_DIR/bin/pytest --cov=policy_analysis --cov-fail-under=80 src/tests/backend src/tests/smoke -q' } }
    stage('Frontend Test') { steps { sh 'npm --prefix src/app/frontend ci && npm --prefix src/app/frontend run type-check && npm --prefix src/app/frontend run lint && npm --prefix src/app/frontend run test -- --run' } }
    stage('Build') { steps { sh 'npm --prefix src/app/frontend run build' } }
    stage('Frontend E2E') { steps { sh 'npm --prefix src/app/frontend exec -- playwright install chromium && npm --prefix src/app/frontend run test:e2e' } }
    stage('Stop') { steps { sh 'if sudo systemctl is-active --quiet policy-analysis-system; then sudo systemctl stop policy-analysis-system; fi' } }
    stage('Deploy') { steps { sh 'sudo /usr/local/sbin/policy-analysis-jenkins-deploy "$WORKSPACE" "$DEPLOY_DIR" "$APP_VERSION" "$COMMIT_SHA"' } }
    stage('Start') { steps { sh 'sudo systemctl start policy-analysis-system' } }
    stage('Health Check') { steps { sh 'curl --fail --retry 10 --retry-delay 3 "$HEALTH_URL"' } }
  }
}
```

systemd 同时读取 `service.env` 和 `build.env`，后者提供 `POLICY_ANALYSIS_VERSION` 与 `POLICY_ANALYSIS_COMMIT_SHA`。`Stop` 只在单元测试、构建和 E2E 全部成功后执行。sudo 权限只允许固定入口 `/usr/local/sbin/policy-analysis-jenkins-deploy` 和 `policy-analysis-system` 服务的启停状态命令，不授予全局免密 sudo，也不允许 Jenkins 修改固定入口。

- [ ] **步骤 4：验证 Jenkinsfile 与完整测试**

运行：`.venv/bin/pytest src/tests/smoke/test_jenkinsfile.py -v`

运行：`.venv/bin/pytest src/tests/smoke -q`

预期：全部 PASS。

- [ ] **步骤 5：提交**

```bash
git add src/JenkinsConfig/Jenkinsfile deploy/jenkins-deploy.sh src/tests/smoke/test_jenkinsfile.py
git commit -m "ci(Jenkins): 添加测试构建与增量部署流水线"
```

### 任务 5：完成需求、设计、README 和运维文档

**文件：**
- 创建：`docs/requirements/policy-analysis-system-srs.md`
- 创建：`docs/design/policy-analysis-system-design.md`
- 修改：`README.md`
- 测试：`src/tests/smoke/test_documentation.py`

- [ ] **步骤 1：先写文档章节和链接测试**

```python
# src/tests/smoke/test_documentation.py
from pathlib import Path


def test_readme_contains_required_delivery_sections() -> None:
    text = Path("README.md").read_text(encoding="utf-8")
    for heading in ["系统介绍", "页面介绍", "配置文件说明", "部署方式", "运维方式", "访问方式"]:
        assert f"## {heading}" in text


def test_requirements_and_design_docs_exist_without_placeholders() -> None:
    paths = [
        Path("docs/requirements/policy-analysis-system-srs.md"),
        Path("docs/design/policy-analysis-system-design.md"),
    ]
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for marker in ("TO" + "DO", "T" + "BD", "待" + "定"):
            assert marker not in text
```

- [ ] **步骤 2：运行并确认文档不完整失败**

运行：`.venv/bin/pytest src/tests/smoke/test_documentation.py -v`

预期：FAIL，缺少需求或设计文档及 README 章节。

- [ ] **步骤 3：依据实现更新文档**

需求规格按角色、功能需求、非功能需求、数据、接口、错误、安全和验收用例编写。设计说明从 superpowers 规格同步实际文件、类、API、表和部署细节，删除未实现描述。README 覆盖系统介绍、页面说明、配置字段、密钥注入、本地开发、Windows/Linux 运维、systemd、Jenkins 和访问方式。

中文与英文、数字之间按中文文档规范留空格；链接使用相对路径；任何示例密钥使用不可用的明确占位值，不复制真实值。

- [ ] **步骤 4：验证文档与链接**

运行：`.venv/bin/pytest src/tests/smoke/test_documentation.py -v`

运行：`rg -n 'TO''DO|T''BD|FIX''ME|待''定|真实密码|966f9d9f' README.md docs src/config deploy`

预期：测试 PASS；搜索无输出。

- [ ] **步骤 5：提交**

```bash
git add README.md docs/requirements docs/design src/tests/smoke/test_documentation.py
git commit -m "docs(系统): 完善需求设计与部署运维文档"
```

### 任务 6：执行本地全量验证与受控真实 WebFetch 冒烟

**文件：**
- 创建：`scripts/webfetch_smoke.py`
- 创建：`src/tests/smoke/test_webfetch_smoke_script.py`
- 修改：`README.md`

- [ ] **步骤 1：先测试冒烟脚本的安全默认行为**

测试断言：未设置 API Key 时脚本明确退出且不发请求；只接受允许域名；默认最多抓取 1 篇配置的已核验文章；输出不含 API Key；只有显式 `--execute` 才访问服务。

- [ ] **步骤 2：运行并确认脚本缺失失败**

运行：`.venv/bin/pytest src/tests/smoke/test_webfetch_smoke_script.py -v`

预期：FAIL，脚本不存在。

- [ ] **步骤 3：实现只读受控冒烟脚本**

CLI 参数固定为：

```text
--webfetch-url http://192.168.0.111:33333
--article-url <已核验新华网文章 URL>
--execute
```

API Key 只从 `POLICY_ANALYSIS_WEBFETCH__API_KEY` 读取。脚本先检查 `/health/ready`，再调用 1 次 `/v1/extract`，验证完整响应、标题、正文、artifact ID 和发布日期提示，不写业务数据库。

- [ ] **步骤 4：运行全部本地验证和真实冒烟**

运行：`.venv/bin/ruff check src/app/backend src/tests/backend scripts`

运行：`.venv/bin/pytest --cov=policy_analysis --cov-fail-under=80 src/tests/backend src/tests/smoke -q`

运行：`npm --prefix src/app/frontend run type-check`

运行：`npm --prefix src/app/frontend run lint`

运行：`npm --prefix src/app/frontend run test -- --run`

运行：`npm --prefix src/app/frontend run build`

运行：`npm --prefix src/app/frontend run test:e2e`

在安全注入 API Key 后运行：`.venv/bin/python scripts/webfetch_smoke.py --webfetch-url http://192.168.0.111:33333 --article-url https://www.news.cn/2021-10/18/c_1127969449.htm --execute`

预期：所有命令退出码为 0；真实冒烟输出 `WebFetch smoke passed`，不打印密钥和正文全文。

- [ ] **步骤 5：提交冒烟工具**

```bash
git add scripts/webfetch_smoke.py src/tests/smoke/test_webfetch_smoke_script.py README.md
git commit -m "test(冒烟): 添加受控 WebFetch 真实契约检查"
```

### 任务 7：推送、配置 Jenkins 并完成部署验收

**文件：**
- 不新增仓库文件；只操作已批准的 GitHub、Jenkins 和目标 Ubuntu 服务。

- [ ] **步骤 1：确认提交范围与工作区干净**

运行：`git status --short --branch`

运行：`git log --oneline --decorate -12`

预期：分支为 `codex/policy-analysis-mvp`，没有未提交或意外文件。

- [ ] **步骤 2：再次运行交付门槛**

重新执行任务 6 的全部验证命令。不得引用之前的输出。

预期：全部通过且覆盖率不低于 80%。

- [ ] **步骤 3：推送开发分支**

运行：`git push -u origin codex/policy-analysis-mvp`

预期：GitHub 创建或更新同名远程分支，命令退出码为 0。

- [ ] **步骤 4：创建或更新 Jenkins Pipeline 任务**

在 Jenkins 中使用 `Pipeline script from SCM`：

- SCM：Git；
- 仓库：`git@github.com:yuan-xin-9997/PolicyAnalysisSystem.git`；
- 分支：首次验收使用 `*/codex/policy-analysis-mvp`；
- Credentials：复用已有 GitHub SSH 凭据；不存在时在 Jenkins 凭据库中安全新增，不写入仓库；
- Script Path：`src/JenkinsConfig/Jenkinsfile`；
- 部署目录：`/opt/PolicyAnalysisSystem`；
- 轮询：由 Jenkinsfile 的 `H/30 * * * *` 提供。

配置生产环境文件，注入 WebFetch API Key 和随机会话密钥；不得在聊天、构建日志或命令回显中打印值。

- [ ] **步骤 5：手工触发并等待 Jenkins 构建完成**

手工点击 Build Now 或调用 Jenkins 的已认证构建接口。检查 Backend Test、Frontend Test、Build、Stop、Deploy、Start 和 Health Check 全部成功。

预期：构建状态 `SUCCESS`，目标服务 `systemctl status policy-analysis-system` 为 active。

- [ ] **步骤 6：执行部署后验收**

检查：

```text
http://192.168.0.111:30080/health/live
http://192.168.0.111:30080/health/ready
http://192.168.0.111:30080/
```

使用默认管理员首次登录，立即确认权限页、配置脱敏、手工回填、任务日志、政策检索、重复任务去重和退出。定时计划保持停用，待手工回填验收通过后再启用。

预期：健康接口为 200，Web UI 可访问，验收路径全部通过。
