# 平台基础与认证实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 建立可运行的 FastAPI + Vue 3 单体应用，完成配置、SQLite、登录会话、用户权限、系统配置页面和基础导航。

**架构：** 后端包位于 `src/app/backend/policy_analysis`，通过 FastAPI 提供 `/api/v1` API，并在生产模式托管 Vue 静态文件。认证凭据以 `src/data/password.txt` 为持久来源，SQLite 保存 Argon2id 哈希、角色、页面权限和服务端会话。

**技术栈：** Python 3.12、FastAPI、SQLAlchemy 2、Alembic、Pydantic 2、Argon2、pytest、Vue 3、TypeScript、Pinia、Vue Router、Element Plus、Vitest。

---

## 实施前提

- 工作目录：`/Users/xinyuan/dev/PolicyAnalysisSystem/.worktrees/policy-analysis-mvp`
- 分支：`codex/policy-analysis-mvp`
- 设计依据：`docs/superpowers/specs/2026-07-31-policy-analysis-system-mvp-design.md`
- 必须遵循 TDD：每个生产行为先写测试并确认按预期失败，再写最少实现。
- 不得把真实密码、WebFetch API Key、会话密钥或环境 IP 写入提交。

## 文件结构与职责

| 文件或目录 | 职责 |
| --- | --- |
| `pyproject.toml` | Python 依赖、包发现、pytest、coverage 和 Ruff 配置 |
| `src/app/backend/policy_analysis/main.py` | FastAPI 应用工厂和生命周期 |
| `src/app/backend/policy_analysis/core/settings.py` | JSON + 环境变量配置加载与脱敏 |
| `src/app/backend/policy_analysis/core/paths.py` | 从项目根目录解析相对路径 |
| `src/app/backend/policy_analysis/core/database.py` | SQLite 引擎、Session 和 PRAGMA |
| `src/app/backend/policy_analysis/core/logging.py` | 日志轮转、上下文和脱敏 |
| `src/app/backend/policy_analysis/core/errors.py` | 统一业务异常与 API 错误响应 |
| `src/app/backend/policy_analysis/auth/models.py` | 用户、页面权限和会话 ORM 模型 |
| `src/app/backend/policy_analysis/auth/password_file.py` | `password.txt` 解析、渲染和原子替换 |
| `src/app/backend/policy_analysis/auth/service.py` | 密码同步、登录、会话和用户维护 |
| `src/app/backend/policy_analysis/auth/dependencies.py` | 当前用户、CSRF 和权限依赖 |
| `src/app/backend/policy_analysis/auth/routes.py` | 登录、退出、当前用户和用户管理 API |
| `src/app/backend/policy_analysis/settings/routes.py` | 脱敏生效配置 API |
| `src/app/backend/policy_analysis/system/routes.py` | 健康检查和版本信息 API |
| `src/app/frontend/` | Vue SPA、测试和生产构建 |
| `src/config/app.json` | 无密钥默认配置 |
| `src/tests/backend/` | 后端单元与 API 集成测试 |
| `src/tests/frontend/` | 前端组件测试 |
| `migrations/` | Alembic 迁移环境和版本 |

### 任务 1：建立 Python 测试与包骨架

**文件：**
- 创建：`pyproject.toml`
- 创建：`src/app/backend/policy_analysis/__init__.py`
- 创建：`src/app/backend/policy_analysis/main.py`
- 创建：`src/tests/backend/test_bootstrap.py`
- 修改：`.gitignore`

- [ ] **步骤 1：创建测试工具配置并写应用导入测试**

`pyproject.toml` 是工具配置，属于 TDD 允许的配置例外。先创建下方完整配置并安装测试依赖，再写应用测试；此时仍不得创建生产包。

```toml
[build-system]
requires = ["setuptools>=75", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "policy-analysis-system"
version = "0.1.0"
description = "抓取、存储和检索政府政策信息"
requires-python = ">=3.12"
dependencies = [
  "alembic>=1.14,<2",
  "apscheduler>=3.11,<4",
  "argon2-cffi>=23.1,<26",
  "defusedxml>=0.7,<1",
  "fastapi>=0.115,<1",
  "httpx>=0.28,<1",
  "pydantic>=2.10,<3",
  "sqlalchemy>=2.0,<3",
  "uvicorn[standard]>=0.34,<1",
]

[project.optional-dependencies]
dev = [
  "pytest>=8.3,<9",
  "pytest-cov>=6,<7",
  "ruff>=0.11,<1",
]

[tool.setuptools.packages.find]
where = ["src/app/backend"]

[tool.pytest.ini_options]
testpaths = ["src/tests/backend"]
addopts = "-ra"

[tool.coverage.run]
source = ["policy_analysis"]

[tool.ruff]
line-length = 110
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM"]

[tool.ruff.lint.flake8-bugbear]
extend-immutable-calls = ["fastapi.Depends"]
```

运行：`python3 -m venv .venv`

运行：`.venv/bin/pip install -e '.[dev]'`

```python
# src/tests/backend/test_bootstrap.py
from fastapi import FastAPI

from policy_analysis.main import create_app


def test_create_app_returns_fastapi() -> None:
    app = create_app()
    assert isinstance(app, FastAPI)
    assert app.title == "政策分析系统"
```

- [ ] **步骤 2：运行测试并确认正确失败**

运行：`.venv/bin/pytest src/tests/backend/test_bootstrap.py -v`

预期：FAIL，错误包含 `ModuleNotFoundError: No module named 'policy_analysis'`。

- [ ] **步骤 3：创建最小应用工厂**

```python
# src/app/backend/policy_analysis/main.py
from fastapi import FastAPI


def create_app() -> FastAPI:
    return FastAPI(title="政策分析系统", version="0.1.0")


app = create_app()
```

在 `.gitignore` 中加入 `.venv/`、`node_modules/`、前端 `dist/`、覆盖率产物和 `src/logs/`；不得加入 `src/data/`。

- [ ] **步骤 4：验证绿灯**

运行：`.venv/bin/pytest src/tests/backend/test_bootstrap.py -v`

预期：`1 passed`，无 warning。

- [ ] **步骤 5：执行静态检查并提交**

运行：`.venv/bin/ruff check src/app/backend src/tests/backend`

预期：`All checks passed!`

```bash
git add .gitignore pyproject.toml src/app/backend/policy_analysis src/tests/backend/test_bootstrap.py
git commit -m "chore(基础): 建立后端项目与测试骨架"
```

### 任务 2：实现配置、路径与敏感字段脱敏

**文件：**
- 创建：`src/config/app.json`
- 创建：`src/app/backend/policy_analysis/core/__init__.py`
- 创建：`src/app/backend/policy_analysis/core/paths.py`
- 创建：`src/app/backend/policy_analysis/core/settings.py`
- 测试：`src/tests/backend/core/test_settings.py`

- [ ] **步骤 1：编写配置优先级和脱敏失败测试**

```python
# src/tests/backend/core/test_settings.py
import json

from policy_analysis.core.settings import load_settings, masked_settings, settings_sources


def test_environment_overrides_json_and_resolves_project_path(tmp_path) -> None:
    config = tmp_path / "app.json"
    config.write_text(
        json.dumps({"server": {"port": 30080}, "database": {"path": "src/data/test.sqlite3"}}),
        encoding="utf-8",
    )
    settings = load_settings(
        config_path=config,
        project_root=tmp_path,
        environ={"POLICY_ANALYSIS_SERVER__PORT": "30123"},
    )
    assert settings.server.port == 30123
    assert settings.database.path == tmp_path / "src/data/test.sqlite3"
    sources = settings_sources(config, {"POLICY_ANALYSIS_SERVER__PORT": "30123"})
    assert sources["server.port"] == "environment"
    assert sources["database.path"] == "config_file"


def test_masked_settings_never_returns_secret_values(tmp_path) -> None:
    config = tmp_path / "app.json"
    config.write_text("{}", encoding="utf-8")
    settings = load_settings(
        config_path=config,
        project_root=tmp_path,
        environ={
            "POLICY_ANALYSIS_WEBFETCH__API_KEY": "secret-webfetch-key",
            "POLICY_ANALYSIS_AUTH__SESSION_SECRET": "secret-session-key",
        },
    )
    visible = masked_settings(settings)
    serialized = json.dumps(visible, ensure_ascii=False)
    assert "secret-webfetch-key" not in serialized
    assert "secret-session-key" not in serialized
    assert visible["webfetch"]["api_key"] == "********"
```

- [ ] **步骤 2：运行测试并确认失败原因**

运行：`.venv/bin/pytest src/tests/backend/core/test_settings.py -v`

预期：FAIL，错误为 `ModuleNotFoundError` 或缺少 `load_settings`，不是 JSON 拼写错误。

- [ ] **步骤 3：实现配置模型和双下划线环境覆盖**

实现以下公开接口，内部辅助函数保持纯函数并分别测试：

```python
# src/app/backend/policy_analysis/core/settings.py
import json
from pathlib import Path
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class StrictSettingsModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ServerSettings(StrictSettingsModel):
    host: str = "127.0.0.1"
    port: int = Field(default=30080, ge=1, le=65535)


class DatabaseSettings(StrictSettingsModel):
    path: Path = Path("src/data/app.sqlite3")


class AuthSettings(StrictSettingsModel):
    password_file: Path = Path("src/data/password.txt")
    session_secret: SecretStr = SecretStr("")
    session_hours: int = Field(default=12, ge=1, le=168)
    secure_cookie: bool = False
    login_attempts: int = Field(default=5, ge=1, le=20)
    login_window_seconds: int = Field(default=300, ge=30, le=3600)


class WebFetchSettings(StrictSettingsModel):
    base_url: str = ""
    api_key: SecretStr = SecretStr("")
    timeout_seconds: float = Field(default=30, gt=0, le=300)


class TaskSettings(StrictSettingsModel):
    max_workers: int = Field(default=2, ge=1, le=8)
    retry_attempts: int = Field(default=3, ge=1, le=5)


class AppSettings(StrictSettingsModel):
    server: ServerSettings = Field(default_factory=ServerSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    webfetch: WebFetchSettings = Field(default_factory=WebFetchSettings)
    tasks: TaskSettings = Field(default_factory=TaskSettings)


def _set_nested(data: dict[str, Any], keys: list[str], value: Any) -> None:
    current = data
    for key in keys[:-1]:
        child = current.setdefault(key, {})
        if not isinstance(child, dict):
            raise ValueError(f"配置路径冲突: {'__'.join(keys)}")
        current = child
    current[keys[-1]] = value


def _parse_environment_value(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _resolve_path(project_root: Path, value: Path) -> Path:
    return value if value.is_absolute() else (project_root / value).resolve()


def load_settings(
    config_path: Path,
    project_root: Path,
    environ: Mapping[str, str],
) -> AppSettings:
    data = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    for name, raw_value in environ.items():
        if not name.startswith("POLICY_ANALYSIS_"):
            continue
        keys = [part.lower() for part in name.removeprefix("POLICY_ANALYSIS_").split("__")]
        _set_nested(data, keys, _parse_environment_value(raw_value))
    settings = AppSettings.model_validate(data)
    return settings.model_copy(
        update={
            "database": settings.database.model_copy(
                update={"path": _resolve_path(project_root, settings.database.path)}
            ),
            "auth": settings.auth.model_copy(
                update={"password_file": _resolve_path(project_root, settings.auth.password_file)}
            ),
        }
    )


def masked_settings(settings: AppSettings) -> dict[str, object]:
    data = settings.model_dump(mode="json")
    sensitive_parts = ("password", "secret", "token", "api_key")

    def mask(value: Any, key: str = "") -> Any:
        if any(part in key.lower() for part in sensitive_parts):
            return "********"
        if isinstance(value, dict):
            return {child_key: mask(child, child_key) for child_key, child in value.items()}
        if isinstance(value, list):
            return [mask(child) for child in value]
        return value

    return mask(data)


def settings_sources(config_path: Path, environ: Mapping[str, str]) -> dict[str, str]:
    defaults = AppSettings().model_dump(mode="json")
    configured = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}

    def flatten(value: Any, prefix: str = "") -> dict[str, Any]:
        if not isinstance(value, dict):
            return {prefix: value}
        flattened: dict[str, Any] = {}
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else key
            flattened.update(flatten(child, child_prefix))
        return flattened

    sources = {path: "default" for path in flatten(defaults)}
    sources.update({path: "config_file" for path in flatten(configured)})
    for name in environ:
        if name.startswith("POLICY_ANALYSIS_"):
            path = name.removeprefix("POLICY_ANALYSIS_").lower().replace("__", ".")
            sources[path] = "environment"
    return sources
```

实现时先让第一个测试通过，再为无效端口、未知字段和相对路径增加失败测试；不要跳过红灯。`src/config/app.json` 写入端口、相对数据库路径、相对密码文件路径、任务并发和 WebFetch 地址占位，不写 API Key。

- [ ] **步骤 4：验证配置测试与全量回归**

运行：`.venv/bin/pytest src/tests/backend/core/test_settings.py -v`

预期：全部 PASS。

运行：`.venv/bin/pytest src/tests/backend -q`

预期：全部 PASS。

- [ ] **步骤 5：提交**

```bash
git add src/config/app.json src/app/backend/policy_analysis/core src/tests/backend/core
git commit -m "feat(配置): 添加分层配置与敏感字段脱敏"
```

### 任务 3：建立 SQLite、Alembic 与认证数据表

**文件：**
- 创建：`alembic.ini`
- 创建：`migrations/env.py`
- 创建：`migrations/versions/0001_auth_tables.py`
- 创建：`src/app/backend/policy_analysis/core/database.py`
- 创建：`src/app/backend/policy_analysis/auth/models.py`
- 测试：`src/tests/backend/core/test_database.py`

- [ ] **步骤 1：编写 SQLite PRAGMA 与表结构测试**

```python
# src/tests/backend/core/test_database.py
from sqlalchemy import inspect, text

from policy_analysis.core.database import build_engine, create_schema


def test_sqlite_enables_foreign_keys_wal_and_auth_tables(tmp_path) -> None:
    engine = build_engine(tmp_path / "app.sqlite3")
    create_schema(engine)
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        assert connection.execute(text("PRAGMA journal_mode")).scalar_one().lower() == "wal"
    assert {"users", "page_permissions", "sessions"}.issubset(inspect(engine).get_table_names())
```

- [ ] **步骤 2：运行并验证红灯**

运行：`.venv/bin/pytest src/tests/backend/core/test_database.py -v`

预期：FAIL，缺少 `policy_analysis.core.database`。

- [ ] **步骤 3：实现数据库工厂、模型和初始迁移**

公开接口固定为：

```python
# src/app/backend/policy_analysis/core/database.py
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def build_engine(database_path: Path) -> Engine:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite+pysqlite:///{database_path}", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    return engine


def create_schema(engine: Engine) -> None:
    from policy_analysis.auth import models

    del models
    Base.metadata.create_all(engine)


def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    with factory() as session:
        yield session
```

`auth/models.py` 精确实现设计中的 `users`、`page_permissions` 和 `sessions` 字段、唯一约束和外键级联。Alembic 迁移必须与模型一致；不要只依赖 `create_all` 作为生产迁移。

- [ ] **步骤 4：运行迁移与测试**

运行：`.venv/bin/alembic upgrade head`

预期：创建 `src/data/app.sqlite3`，迁移版本为 `0001`。

运行：`.venv/bin/pytest src/tests/backend/core/test_database.py -v`

预期：PASS。

- [ ] **步骤 5：移除本地运行数据库并提交**

将本次命令生成的 `src/data/app.sqlite3` 移至系统废纸篓或明确删除单个文件，仓库仅保留 `src/data/.gitkeep`。不要忽略整个 `src/data/`。

```bash
git add alembic.ini migrations src/app/backend/policy_analysis/core/database.py src/app/backend/policy_analysis/auth src/tests/backend/core/test_database.py src/data/.gitkeep
git commit -m "feat(数据库): 建立认证数据表与迁移框架"
```

### 任务 4：实现密码文件解析、同步与原子更新

**文件：**
- 创建：`src/app/backend/policy_analysis/auth/password_file.py`
- 创建：`src/app/backend/policy_analysis/auth/repository.py`
- 创建：`src/app/backend/policy_analysis/auth/service.py`
- 测试：`src/tests/backend/auth/test_password_file.py`
- 测试：`src/tests/backend/auth/test_user_sync.py`

- [ ] **步骤 1：编写解析和同步行为测试**

```python
# src/tests/backend/auth/test_password_file.py
from policy_analysis.auth.password_file import PasswordEntry, parse_password_text, render_password_text


def test_parse_ignores_comments_and_preserves_valid_roles() -> None:
    text = "# comment\nadmin:admin123:admin\nreader:read123:user\n"
    assert parse_password_text(text) == [
        PasswordEntry("admin", "admin123", "admin"),
        PasswordEntry("reader", "read123", "user"),
    ]


def test_render_round_trips_entries_without_exposing_old_content() -> None:
    entries = [PasswordEntry("reader", "new-password", "user")]
    rendered = render_password_text(entries)
    assert parse_password_text(rendered) == entries
    assert rendered.startswith("# 格式: username:password:role")
```

`test_user_sync.py` 使用真实临时 SQLite、真实 Argon2 hasher 和临时密码文件，验证新用户、改密、改角色、文件删除后的禁用行为；不要 mock 仓储或 hasher。

- [ ] **步骤 2：确认测试按功能缺失失败**

运行：`.venv/bin/pytest src/tests/backend/auth/test_password_file.py src/tests/backend/auth/test_user_sync.py -v`

预期：FAIL，缺少 `PasswordEntry` 或 `UserSyncService`。

- [ ] **步骤 3：实现解析器与同步服务**

```python
# src/app/backend/policy_analysis/auth/password_file.py
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Role = Literal["admin", "user"]


@dataclass(frozen=True, slots=True)
class PasswordEntry:
    username: str
    password: str
    role: Role


def parse_password_text(text: str) -> list[PasswordEntry]:
    entries: list[PasswordEntry] = []
    seen: set[str] = set()
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(":")
        if len(parts) != 3:
            raise ValueError(f"password.txt 第 {line_number} 行格式无效")
        username, password, role = (part.strip() for part in parts)
        if not username or not password or role not in {"admin", "user"} or username in seen:
            raise ValueError(f"password.txt 第 {line_number} 行内容无效")
        seen.add(username)
        entries.append(PasswordEntry(username, password, role))
    return entries


def render_password_text(entries: list[PasswordEntry]) -> str:
    header = "# 格式: username:password:role  (role 取值: admin | user)"
    lines = [header, "# admin 默认拥有所有页面权限；user 的页面权限由管理员配置。"]
    lines.extend(f"{entry.username}:{entry.password}:{entry.role}" for entry in entries)
    return "\n".join(lines) + "\n"


def replace_password_file(path: Path, entries: list[PasswordEntry]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(render_password_text(entries))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        os.chmod(path, 0o600)
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        temporary_path.unlink(missing_ok=True)
```

继续用失败测试驱动 `replace_password_file` 的权限、原子替换和异常补偿。`UserSyncService` 记录文件 `mtime_ns`，仅在变化时重新计算 Argon2id 哈希；文件中消失的账号设为不可登录，但不删除数据库记录。

- [ ] **步骤 4：验证真实行为**

运行：`.venv/bin/pytest src/tests/backend/auth -v`

预期：全部 PASS，测试结束后没有遗留 `.tmp` 或 `.bak` 文件。

- [ ] **步骤 5：提交**

```bash
git add src/app/backend/policy_analysis/auth src/tests/backend/auth
git commit -m "feat(认证): 实现密码文件同步与原子更新"
```

### 任务 5：实现服务端会话、登录限速与 CSRF

**文件：**
- 创建：`src/app/backend/policy_analysis/core/errors.py`
- 创建：`src/app/backend/policy_analysis/auth/dependencies.py`
- 创建：`src/app/backend/policy_analysis/auth/routes.py`
- 修改：`src/app/backend/policy_analysis/auth/service.py`
- 修改：`src/app/backend/policy_analysis/main.py`
- 创建：`src/tests/backend/conftest.py`
- 测试：`src/tests/backend/auth/test_auth_api.py`

- [ ] **步骤 1：编写登录主路径和安全边界测试**

```python
# src/tests/backend/auth/test_auth_api.py
def test_login_me_csrf_and_logout(client, password_file) -> None:
    password_file.write_text("admin:admin123:admin\n", encoding="utf-8")
    login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
    assert login.status_code == 200
    assert login.json()["user"]["username"] == "admin"
    assert "session=" in login.headers["set-cookie"]
    csrf = login.json()["csrf_token"]

    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["role"] == "admin"

    rejected = client.post("/api/v1/auth/logout")
    assert rejected.status_code == 403
    assert rejected.json()["error"]["code"] == "CSRF_INVALID"

    logout = client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
    assert logout.status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 401
```

再分别增加错误密码统一提示、停用用户、会话过期和连续失败触发 429 的测试。`src/tests/backend/conftest.py` 提供临时项目根目录、真实 SQLite、临时密码文件、应用 TestClient、已登录管理员和普通用户客户端；夹具不 mock 路由依赖。

- [ ] **步骤 2：运行并确认红灯**

运行：`.venv/bin/pytest src/tests/backend/auth/test_auth_api.py -v`

预期：FAIL，登录路由返回 404。

- [ ] **步骤 3：实现最少会话 API**

会话服务使用 `secrets.token_urlsafe(32)` 生成会话和 CSRF Token，只把 SHA-256 哈希写入数据库。Cookie 名称固定为 `session`，属性为 `HttpOnly`、`SameSite=Lax`、`Path=/`，`Secure` 由配置控制。

```python
# src/app/backend/policy_analysis/auth/routes.py
from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, Field

from policy_analysis.auth.dependencies import get_auth_service, require_csrf_session, require_user

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)


@router.post("/login")
def login(payload: LoginRequest, response: Response, service=Depends(get_auth_service)) -> dict[str, object]:
    result = service.login(payload.username, payload.password)
    response.set_cookie("session", result.token, httponly=True, samesite="lax", secure=service.secure_cookie)
    return {"user": result.user.to_public_dict(), "csrf_token": result.csrf_token}


@router.get("/me")
def me(current_user=Depends(require_user)) -> dict[str, object]:
    return current_user.to_public_dict()


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response, session=Depends(require_csrf_session)) -> None:
    session.service.logout(session.id)
    response.delete_cookie("session", path="/")
```

将 `get_auth_service`、`require_user` 和 `require_csrf_session` 放在 `dependencies.py`，避免路由创建全局数据库连接。统一异常处理返回设计中的 `error.code/message/request_id/details`。

- [ ] **步骤 4：验证安全测试和回归**

运行：`.venv/bin/pytest src/tests/backend/auth/test_auth_api.py -v`

预期：全部 PASS。

运行：`.venv/bin/pytest src/tests/backend -q`

预期：全部 PASS。

- [ ] **步骤 5：提交**

```bash
git add src/app/backend/policy_analysis src/tests/backend/auth
git commit -m "feat(认证): 添加安全会话与登录 API"
```

### 任务 6：实现 RBAC、用户管理、配置和系统 API

**文件：**
- 创建：`src/app/backend/policy_analysis/auth/permissions.py`
- 创建：`src/app/backend/policy_analysis/settings/routes.py`
- 创建：`src/app/backend/policy_analysis/system/routes.py`
- 修改：`src/app/backend/policy_analysis/auth/routes.py`
- 修改：`src/app/backend/policy_analysis/main.py`
- 测试：`src/tests/backend/auth/test_rbac_api.py`
- 测试：`src/tests/backend/settings/test_settings_api.py`
- 测试：`src/tests/backend/system/test_system_api.py`

- [ ] **步骤 1：编写管理员与普通用户权限测试**

```python
# src/tests/backend/auth/test_rbac_api.py
def test_user_management_requires_admin_and_page_permissions_gate_api(admin_client, user_client) -> None:
    created = admin_client.post(
        "/api/v1/users",
        json={"username": "analyst", "password": "safe-password", "role": "user", "pages": ["policies"]},
        headers=admin_client.csrf_headers,
    )
    assert created.status_code == 201
    assert created.json()["pages"] == ["policies"]

    assert user_client.get("/api/v1/users").status_code == 403
    assert user_client.get("/api/v1/settings/effective").status_code == 403
    assert user_client.get("/api/v1/system/info").status_code == 200
```

配置测试断言响应内不存在测试 API Key 和会话密钥，并断言每个叶子字段的来源为 `default`、`config_file` 或 `environment`。系统测试断言版本、短 SHA、北京时间时区和健康摘要结构。

- [ ] **步骤 2：运行并确认 404/403 的预期红灯**

运行：`.venv/bin/pytest src/tests/backend/auth/test_rbac_api.py src/tests/backend/settings src/tests/backend/system -v`

预期：FAIL，尚未实现的路由返回 404。

- [ ] **步骤 3：实现权限代码和管理 API**

```python
# src/app/backend/policy_analysis/auth/permissions.py
from enum import StrEnum


class PageCode(StrEnum):
    POLICIES = "policies"
    TASKS = "tasks"
    PUSH = "push"
    ANALYSIS = "analysis"
    USERS = "users"
    SETTINGS = "settings"


def can_access(role: str, granted_pages: set[str], required: PageCode) -> bool:
    return role == "admin" or required.value in granted_pages
```

用户创建、改密、改角色、启停和页面授权走 `UserAdministrationService`。该服务持有文件锁，保存原文件备份，执行数据库事务和文件原子替换；异常时恢复备份，启动同步负责进程崩溃后的最终修复。

配置 API 返回 `values=masked_settings(settings)` 和 `sources=settings_sources(...)`。系统信息从 `POLICY_ANALYSIS_VERSION` 和 `POLICY_ANALYSIS_COMMIT_SHA` 读取；本地缺失时只读调用 Git 获取提交总数和短 SHA，失败则返回 `v0.dev` 和 `unknown`。

`GET /health/live` 只返回进程存活。`GET /health/ready` 执行 SQLite `SELECT 1` 并检查任务执行器是否已初始化；平台阶段任务执行器尚未启用时使用明确的 `not_configured` 状态，采集计划完成后改为强检查。ready 不调用 WebFetch，WebFetch 连通状态由配置 API 单独返回。应用不配置通配 CORS，浏览器生产访问保持同源。

- [ ] **步骤 4：验证全部 API**

运行：`.venv/bin/pytest src/tests/backend/auth/test_rbac_api.py src/tests/backend/settings src/tests/backend/system -v`

预期：全部 PASS。

运行：`.venv/bin/pytest --cov=policy_analysis --cov-report=term-missing src/tests/backend -q`

预期：全部 PASS；当前阶段不强制 80%，但不得低于已提交基线。

- [ ] **步骤 5：提交**

```bash
git add src/app/backend/policy_analysis src/tests/backend
git commit -m "feat(权限): 添加用户管理与系统配置 API"
```

### 任务 7：建立 Vue 登录、权限导航和基础页面

**文件：**
- 创建：`src/app/frontend/package.json` 及 Vite Vue TypeScript 标准文件
- 创建：`src/app/frontend/src/api/client.ts`
- 创建：`src/app/frontend/src/stores/auth.ts`
- 创建：`src/app/frontend/src/router/index.ts`
- 创建：`src/app/frontend/src/layouts/AppLayout.vue`
- 创建：`src/app/frontend/src/views/LoginView.vue`
- 创建：`src/app/frontend/src/views/UsersView.vue`
- 创建：`src/app/frontend/src/views/SettingsView.vue`
- 创建：`src/app/frontend/src/views/PlaceholderView.vue`
- 创建：`src/tests/frontend/auth.spec.ts`
- 修改：`src/app/backend/policy_analysis/main.py`

- [ ] **步骤 1：生成前端工具配置并先写组件测试**

运行：`npm create vite@latest src/app/frontend -- --template vue-ts`

运行：`npm --prefix src/app/frontend install vue-router@4 pinia element-plus`

运行：`npm --prefix src/app/frontend install -D vitest @vue/test-utils jsdom @testing-library/vue`

把测试目录配置为 `../../tests/frontend`，再创建测试：

```typescript
// src/tests/frontend/auth.spec.ts
import { fireEvent, render, screen } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { describe, expect, it, vi } from 'vitest'
import AppLayout from '../../app/frontend/src/layouts/AppLayout.vue'
import LoginView from '../../app/frontend/src/views/LoginView.vue'
import { useAuthStore } from '../../app/frontend/src/stores/auth'

describe('权限导航', () => {
  it('只显示普通用户获准页面并在左下角显示身份与版本', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const auth = useAuthStore()
    auth.user = { username: 'reader', role: 'user', pages: ['policies'] }
    auth.version = 'v0.12'
    render(AppLayout, { global: { plugins: [pinia] } })
    expect(screen.getByText('政策数据库')).toBeTruthy()
    expect(screen.queryByText('权限管理')).toBeNull()
    expect(screen.getByText('reader')).toBeTruthy()
    expect(screen.getByText('v0.12')).toBeTruthy()
  })

  it('登录成功后保存用户与 CSRF 并进入有权访问的首页', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const push = vi.fn()
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      user: { username: 'reader', role: 'user', pages: ['policies'] },
      csrf_token: 'csrf-test-token',
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })))
    render(LoginView, { global: { plugins: [pinia], mocks: { $router: { push } } } })
    await fireEvent.update(screen.getByLabelText('用户名'), 'reader')
    await fireEvent.update(screen.getByLabelText('密码'), 'read123')
    await fireEvent.click(screen.getByRole('button', { name: '登录' }))
    const auth = useAuthStore()
    expect(auth.user?.username).toBe('reader')
    expect(auth.csrfToken).toBe('csrf-test-token')
    expect(push).toHaveBeenCalledWith({ name: 'policies' })
  })
})
```

- [ ] **步骤 2：运行并确认组件缺失失败**

运行：`npm --prefix src/app/frontend run test -- --run src/tests/frontend/auth.spec.ts`

预期：FAIL，无法导入 `AppLayout.vue` 或 auth store。

- [ ] **步骤 3：实现 API 客户端、认证 store、路由和布局**

`api/client.ts` 使用同源 `fetch`、`credentials: 'include'`，状态变更时从 auth store 添加 `X-CSRF-Token`。遇到 401 清理本地身份并跳转登录；错误展示服务端 `error.message`。

```typescript
// src/app/frontend/src/stores/auth.ts
import { defineStore } from 'pinia'
import { ref } from 'vue'

export interface CurrentUser {
  username: string
  role: 'admin' | 'user'
  pages: string[]
}

export const useAuthStore = defineStore('auth', () => {
  const user = ref<CurrentUser | null>(null)
  const csrfToken = ref('')
  const version = ref('v0.dev')

  function canAccess(page: string): boolean {
    return user.value?.role === 'admin' || Boolean(user.value?.pages.includes(page))
  }

  function clear(): void {
    user.value = null
    csrfToken.value = ''
  }

  return { user, csrfToken, version, canAccess, clear }
})
```

菜单包含政策数据库、任务中心、推送管理、政策分析、权限管理和系统配置。`UsersView` 与 `SettingsView` 调用已实现 API；推送和分析使用只读占位页。布局底部固定显示用户、退出按钮和版本号。`package.json` 固定提供 `dev`、`build`、`type-check`、`lint`、`test` 和 `test:e2e` 脚本，后续计划只调用这些稳定入口。

- [ ] **步骤 4：验证前端与后端静态回退**

运行：`npm --prefix src/app/frontend run test -- --run`

预期：全部 PASS。

运行：`npm --prefix src/app/frontend run build`

预期：生成 `src/app/frontend/dist/index.html`。

新增后端测试：生产静态目录存在时，`GET /login` 返回 SPA；`/api/v1/not-found` 仍返回 JSON 404，不回退 HTML。运行后端测试并确认通过。

- [ ] **步骤 5：提交**

```bash
git add src/app/frontend src/tests/frontend src/app/backend/policy_analysis/main.py src/tests/backend
git commit -m "feat(前端): 添加登录与权限导航基础页面"
```

### 任务 8：完成阶段验证

**文件：**
- 修改：`README.md`
- 创建：`src/tests/backend/test_platform_smoke.py`

- [ ] **步骤 1：先写平台冒烟测试**

冒烟测试使用临时目录启动完整应用，写入默认管理员，执行登录、读取当前用户、读取系统信息、读取脱敏配置、退出，并断言数据库和密码文件均未泄露到响应。

- [ ] **步骤 2：运行并确认测试能捕获缺少的应用装配**

运行：`.venv/bin/pytest src/tests/backend/test_platform_smoke.py -v`

预期：首次运行若应用工厂尚未装配全部路由则 FAIL；补齐装配前不得修改断言。

- [ ] **步骤 3：完成最少装配并更新 README 的本地启动章节**

README 写明 Python 3.12、Node.js、虚拟环境、前端构建、无密钥配置方式、默认账号仅用于首次部署，以及开发启动命令。不得写入真实环境密码或 API Key。

- [ ] **步骤 4：执行阶段验证**

运行：`.venv/bin/ruff check src/app/backend src/tests/backend`

运行：`.venv/bin/pytest --cov=policy_analysis --cov-report=term-missing src/tests/backend -q`

运行：`npm --prefix src/app/frontend run test -- --run`

运行：`npm --prefix src/app/frontend run build`

预期：4 条命令全部退出码为 0，无未处理 warning。

- [ ] **步骤 5：提交阶段成果**

```bash
git add README.md src/tests/backend/test_platform_smoke.py src/app/backend/policy_analysis
git commit -m "test(平台): 添加认证与权限冒烟验证"
```
