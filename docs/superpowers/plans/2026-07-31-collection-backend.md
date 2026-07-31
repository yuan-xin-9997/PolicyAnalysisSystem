# 政策采集后端实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在平台基础上完成新华社来源配置、近 5 年种子回填、RSS/栏目增量发现、WebFetch 抓取、政策去重检索、任务日志和定时调度。

**架构：** 采集任务由 SQLite 持久化，单进程 APScheduler 创建任务，受控线程池领取并执行。来源适配器只负责发现和判定，WebFetch 客户端负责外部请求，政策服务负责短事务去重入库。

**技术栈：** FastAPI、SQLAlchemy 2、Alembic、SQLite FTS5、httpx、APScheduler 3、defusedxml、pytest。

---

## 实施前提

- 先完整执行 `docs/superpowers/plans/2026-07-31-platform-foundation.md`。
- WebFetch 契约以其仓库 `src/webfetch_service/schemas.py` 为准。
- `/v1/extract` 的真实响应字段为 `request_id`、`adapter`、`adapter_version`、`artifact_id`、`data`。
- `generic.article` 的 `data` 包含 `title`、`content`、`author`、`date`。
- `/v1/fetch` 的真实错误包含 `request_id`、`success=false` 和 `error.code/message/retryable/retry_after_seconds`。
- 单元与 CI 测试不得访问真实新华网或 WebFetch；真实服务只用于最终受控冒烟。

## 文件结构与职责

| 文件或目录 | 职责 |
| --- | --- |
| `policy_analysis/sources/models.py` | 类别、来源、规则、种子和计划 ORM 模型 |
| `policy_analysis/sources/schemas.py` | 来源与规则 API 模型 |
| `policy_analysis/sources/service.py` | 规则校验、种子导入和计划维护 |
| `policy_analysis/collectors/base.py` | 发现候选、文章结果和适配器 Protocol |
| `policy_analysis/collectors/webfetch.py` | WebFetch 请求、响应、错误和重试 |
| `policy_analysis/collectors/xinhua.py` | 新华网 URL、RSS、标题、正文和日期判定 |
| `policy_analysis/collectors/resources/xinhua_politburo_seed_urls.json` | 已核验历史种子清单 |
| `policy_analysis/policies/models.py` | 政策和修订 ORM 模型 |
| `policy_analysis/policies/service.py` | 去重、修订、FTS5 检索和详情 |
| `policy_analysis/tasks/models.py` | 任务、候选明细和日志 ORM 模型 |
| `policy_analysis/tasks/state.py` | 合法状态转换纯函数 |
| `policy_analysis/tasks/runner.py` | 发现、抓取、判定和入库编排 |
| `policy_analysis/tasks/worker.py` | 持久任务领取、线程池和恢复 |
| `policy_analysis/tasks/scheduler.py` | APScheduler 与数据库计划同步 |
| `policy_analysis/*/routes.py` | `/api/v1` 资源 API |
| `migrations/versions/0002_collection_tables.py` | 采集领域表与索引 |
| `migrations/versions/0003_policy_fts.py` | FTS5 虚表和同步触发器 |

### 任务 1：建立采集领域数据表与迁移

**文件：**
- 创建：`src/app/backend/policy_analysis/sources/models.py`
- 创建：`src/app/backend/policy_analysis/policies/models.py`
- 创建：`src/app/backend/policy_analysis/tasks/models.py`
- 创建：`migrations/versions/0002_collection_tables.py`
- 修改：`src/app/backend/policy_analysis/core/database.py`
- 修改：`migrations/env.py`
- 测试：`src/tests/backend/collection/test_collection_schema.py`

- [ ] **步骤 1：编写表、约束和级联测试**

```python
# src/tests/backend/collection/test_collection_schema.py
from sqlalchemy import inspect

from policy_analysis.core.database import build_engine, create_schema


def test_collection_schema_has_required_tables_and_unique_constraints(tmp_path) -> None:
    engine = build_engine(tmp_path / "collection.sqlite3")
    create_schema(engine)
    inspector = inspect(engine)
    expected = {
        "policy_categories", "sources", "collection_rules", "seed_urls", "schedules",
        "policies", "policy_revisions", "crawl_tasks", "crawl_task_items", "crawl_task_logs",
    }
    assert expected.issubset(inspector.get_table_names())
    policy_unique_columns = {
        tuple(constraint["column_names"]) for constraint in inspector.get_unique_constraints("policies")
    }
    assert ("source_id", "canonical_url") in policy_unique_columns
```

再增加外键级联、`rule_id + url` 唯一、任务状态 CHECK、任务明细 reason 字段和 `request_snapshot_json` 非空测试。

- [ ] **步骤 2：运行并确认模型缺失失败**

运行：`.venv/bin/pytest src/tests/backend/collection/test_collection_schema.py -v`

预期：FAIL，预期表不在 schema 中。

- [ ] **步骤 3：实现模型和 Alembic 迁移**

模型字段必须逐项对应设计文档第 6 节。补充以下枚举，数据库保存其字符串值：

```python
from enum import StrEnum


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIALLY_SUCCEEDED = "partially_succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskItemStatus(StrEnum):
    STORED = "stored"
    UPDATED = "updated"
    DUPLICATE = "duplicate"
    FILTERED = "filtered"
    FAILED = "failed"
```

`crawl_tasks` 保存规则、触发类型、请求者、时间、取消标记、请求快照、5 个统计数和错误摘要。`policy_revisions.task_item_id` 允许空值，以支持系统维护产生的修订。

同步修改 `create_schema` 和 `migrations/env.py`，显式导入 `auth.models`、`sources.models`、`policies.models` 与 `tasks.models`，确保测试建表和 Alembic 自动迁移使用同一份 `Base.metadata`。

- [ ] **步骤 4：执行迁移和 schema 测试**

运行：`.venv/bin/alembic upgrade head`

运行：`.venv/bin/pytest src/tests/backend/collection/test_collection_schema.py -v`

预期：迁移到 `0002`，测试全部 PASS。

- [ ] **步骤 5：提交**

```bash
git add src/app/backend/policy_analysis/core/database.py src/app/backend/policy_analysis/sources src/app/backend/policy_analysis/policies src/app/backend/policy_analysis/tasks migrations/env.py migrations/versions/0002_collection_tables.py src/tests/backend/collection/test_collection_schema.py
git commit -m "feat(采集): 建立来源政策与任务数据模型"
```

### 任务 2：实现来源、规则、种子和计划服务

**文件：**
- 创建：`src/app/backend/policy_analysis/sources/schemas.py`
- 创建：`src/app/backend/policy_analysis/sources/repository.py`
- 创建：`src/app/backend/policy_analysis/sources/service.py`
- 创建：`src/app/backend/policy_analysis/sources/routes.py`
- 修改：`src/app/backend/policy_analysis/main.py`
- 测试：`src/tests/backend/sources/test_source_service.py`
- 测试：`src/tests/backend/sources/test_source_api.py`

- [ ] **步骤 1：编写规则约束和管理员 API 测试**

```python
# src/tests/backend/sources/test_source_service.py
import pytest
from pydantic import ValidationError

from policy_analysis.sources.schemas import CollectionRuleCreate


def test_collection_rule_requires_allowed_domain_keywords_and_five_year_window(source_service) -> None:
    payload = {
        "name": "中央政治局会议",
        "source_code": "xinhua",
        "category_code": "politburo_meeting",
        "include_keywords": ["中共中央政治局召开会议"],
        "exclude_keywords": ["视频"],
        "history_years": 5,
        "discovery": {"rss_urls": ["http://www.xinhuanet.com/politics/news_politics.xml"]},
    }
    rule = source_service.create_rule(CollectionRuleCreate.model_validate(payload))
    assert rule.history_years == 5

    with pytest.raises(ValidationError):
        CollectionRuleCreate.model_validate({**payload, "history_years": 0})
```

API 测试覆盖管理员 CRUD、普通用户 403、无效 Cron 422、初始计划默认停用，以及种子重复导入不覆盖现场数据。

- [ ] **步骤 2：运行并确认红灯**

运行：`.venv/bin/pytest src/tests/backend/sources -v`

预期：FAIL，缺少 schema、service 或路由。

- [ ] **步骤 3：实现服务和 API**

`CollectionRuleCreate` 使用 Pydantic 验证：名称非空、`history_years` 为 1–20、包含词至少 1 个、发现配置至少有 1 个 RSS 或栏目 URL。所有 URL 主机必须落在关联来源 `allowed_domains_json` 中。

路由固定为：

```text
GET    /api/v1/policy-categories
GET    /api/v1/sources
GET    /api/v1/collection-rules
POST   /api/v1/collection-rules
PATCH  /api/v1/collection-rules/{rule_id}
GET    /api/v1/schedules
POST   /api/v1/schedules
PATCH  /api/v1/schedules/{schedule_id}
```

写操作要求管理员和 CSRF，读操作要求 `tasks` 页面权限。Cron 校验使用 APScheduler `CronTrigger.from_crontab`，统一时区 `Asia/Shanghai`。

- [ ] **步骤 4：验证 API 和全量后端测试**

运行：`.venv/bin/pytest src/tests/backend/sources -v`

运行：`.venv/bin/pytest src/tests/backend -q`

预期：全部 PASS。

- [ ] **步骤 5：提交**

```bash
git add src/app/backend/policy_analysis/sources src/app/backend/policy_analysis/main.py src/tests/backend/sources
git commit -m "feat(来源): 添加采集规则与计划管理 API"
```

### 任务 3：实现 WebFetch 完整契约客户端与重试

**文件：**
- 创建：`src/app/backend/policy_analysis/collectors/__init__.py`
- 创建：`src/app/backend/policy_analysis/collectors/base.py`
- 创建：`src/app/backend/policy_analysis/collectors/webfetch.py`
- 修改：`src/app/backend/policy_analysis/settings/routes.py`
- 测试：`src/tests/backend/collectors/test_webfetch_client.py`
- 测试：`src/tests/backend/settings/test_webfetch_status.py`

- [ ] **步骤 1：用真实契约结构编写客户端测试**

```python
# src/tests/backend/collectors/test_webfetch_client.py
import httpx

from policy_analysis.collectors.webfetch import WebFetchClient


def test_extract_article_maps_complete_webfetch_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer test-key"
        assert request.url.path == "/v1/extract"
        assert request.read()
        return httpx.Response(
            200,
            json={
                "request_id": "req_1",
                "adapter": "generic.article",
                "adapter_version": "1",
                "artifact_id": "art_1",
                "data": {
                    "title": "中共中央政治局召开会议",
                    "content": "新华社北京7月30日电 中共中央政治局7月30日召开会议。",
                    "author": "",
                    "date": "2026-07-30T14:00:00+08:00",
                },
            },
        )

    client = WebFetchClient("http://webfetch", "test-key", transport=httpx.MockTransport(handler))
    result = client.extract_article("https://www.news.cn/example/c.html")
    assert result.artifact_id == "art_1"
    assert result.title == "中共中央政治局召开会议"
    assert result.published_hint == "2026-07-30T14:00:00+08:00"
```

增加测试：`/v1/fetch` 返回 RSS body、`/health/ready` 映射就绪状态、429 `retryable=true` 按 `retry_after_seconds` 重试、422 不重试、响应缺字段产生稳定的 `WEBFETCH_CONTRACT_INVALID`，以及日志不包含 API Key。配置 API 测试断言 WebFetch 不可用时仍返回配置，但 `webfetch_status=unavailable`。

- [ ] **步骤 2：运行并确认红灯**

运行：`.venv/bin/pytest src/tests/backend/collectors/test_webfetch_client.py -v`

预期：FAIL，缺少 `WebFetchClient`。

- [ ] **步骤 3：实现客户端和可注入等待函数**

```python
# src/app/backend/policy_analysis/collectors/base.py
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ExtractedArticle:
    request_id: str
    artifact_id: str
    title: str
    content: str
    author: str
    published_hint: str


@dataclass(frozen=True, slots=True)
class DiscoveredLink:
    url: str
    text: str
    origin: str
```

`WebFetchClient.extract_article` 向 `/v1/extract` 发送：

```json
{
  "url": "https://www.news.cn/example/c.html",
  "adapter": "generic.article",
  "fetch_options": {
    "url": "https://www.news.cn/example/c.html",
    "mode": "auto",
    "save_artifact": true
  }
}
```

`fetch_text` 向 `/v1/fetch` 发送 `mode=auto`、`save_artifact=false` 并返回完整 body。客户端构造函数允许注入 `httpx.BaseTransport` 和 `sleep: Callable[[float], None]`；测试只替换网络和等待，不 mock 客户端业务逻辑。

`WebFetchClient.ready()` 调用 `/health/ready`，只有 HTTP 200 且 `status=ok` 时返回 `True`。扩展配置 API，使其在短超时内调用 `ready()` 并返回 `ready` 或 `unavailable`；异常不得泄露 URL 凭据或影响配置主体响应。

- [ ] **步骤 4：验证重试与契约映射**

运行：`.venv/bin/pytest src/tests/backend/collectors/test_webfetch_client.py -v`

预期：全部 PASS；429 用例调用 3 次，422 用例只调用 1 次，但断言最终业务结果和错误代码，不单独测试 mock 对象存在。

- [ ] **步骤 5：提交**

```bash
git add src/app/backend/policy_analysis/collectors src/app/backend/policy_analysis/settings/routes.py src/tests/backend/collectors/test_webfetch_client.py src/tests/backend/settings/test_webfetch_status.py
git commit -m "feat(抓取): 添加 WebFetch 契约客户端与重试"
```

### 任务 4：实现新华网发现、规范化与文章判定

**文件：**
- 创建：`src/app/backend/policy_analysis/collectors/xinhua.py`
- 创建：`src/tests/backend/collectors/fixtures/xinhua_old_article.json`
- 创建：`src/tests/backend/collectors/fixtures/xinhua_current_article.json`
- 创建：`src/tests/backend/collectors/fixtures/xinhua_video_article.json`
- 测试：`src/tests/backend/collectors/test_xinhua_adapter.py`

- [ ] **步骤 1：先固定旧版、当前和视频稿真实结构夹具**

夹具只保存 WebFetch `generic.article` 的完整 JSON 响应，不保存大段受版权保护的全文。正文保留标题、发布日期、新华社导语和足以判断类型的短片段，每份夹具都包含契约要求的全部字段。

```python
# src/tests/backend/collectors/test_xinhua_adapter.py
from datetime import UTC, datetime

from policy_analysis.collectors.xinhua import XinhuaCollector


def test_accepts_official_meeting_article_and_rejects_video(load_fixture) -> None:
    collector = XinhuaCollector(
        allowed_domains={"news.cn", "www.news.cn", "xinhuanet.com", "www.xinhuanet.com"},
        include_keywords=("中共中央政治局召开会议",),
        exclude_keywords=("视频",),
        minimum_content_chars=80,
    )
    accepted = collector.classify(
        "https://www.news.cn/2021-10/18/c_1127969449.htm",
        load_fixture("xinhua_old_article.json"),
        cutoff=datetime(2021, 7, 31, tzinfo=UTC),
    )
    assert accepted.accepted is True
    assert accepted.published_at.isoformat().startswith("2021-10-18")

    rejected = collector.classify(
        "https://www.news.cn/20260130/e9daba7d39a040b2b52eb85cc1bf894a/c.html",
        load_fixture("xinhua_video_article.json"),
        cutoff=datetime(2021, 7, 31, tzinfo=UTC),
    )
    assert rejected.accepted is False
    assert rejected.reason_code == "VIDEO_ONLY"
```

增加 URL fragment/跟踪参数、非允许域名、窗口外日期、标题不符、导语不符、RSS XML 和栏目 links 的测试。

- [ ] **步骤 2：运行并确认红灯**

运行：`.venv/bin/pytest src/tests/backend/collectors/test_xinhua_adapter.py -v`

预期：FAIL，缺少 `XinhuaCollector`。

- [ ] **步骤 3：实现纯函数判定**

实现以下完整纯函数骨架，再用前述失败测试逐条补齐 reason code：

```python
import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from defusedxml import ElementTree

from policy_analysis.collectors.base import DiscoveredLink, ExtractedArticle

BEIJING = ZoneInfo("Asia/Shanghai")
TRACKING_PARAMETERS = {"from", "spm", "utm_campaign", "utm_medium", "utm_source"}


@dataclass(frozen=True, slots=True)
class Classification:
    accepted: bool
    reason_code: str
    canonical_url: str
    title: str
    content: str
    publisher: str
    published_at: datetime | None
    artifact_id: str


class XinhuaCollector:
    def __init__(
        self,
        allowed_domains: set[str],
        include_keywords: tuple[str, ...],
        exclude_keywords: tuple[str, ...],
        minimum_content_chars: int,
    ) -> None:
        self.allowed_domains = {domain.lower() for domain in allowed_domains}
        self.include_keywords = include_keywords
        self.exclude_keywords = exclude_keywords
        self.minimum_content_chars = minimum_content_chars

    def discover_from_rss(self, xml_text: str, origin: str) -> list[DiscoveredLink]:
        root = ElementTree.fromstring(xml_text)
        discovered: list[DiscoveredLink] = []
        seen: set[str] = set()
        for item in root.findall(".//item"):
            url = (item.findtext("link") or "").strip()
            text = " ".join((item.findtext("title") or "").split())
            if not url:
                continue
            try:
                canonical = self.canonicalize(url)
            except ValueError:
                continue
            if canonical not in seen:
                seen.add(canonical)
                discovered.append(DiscoveredLink(canonical, text, origin))
        return discovered

    def discover_from_links(
        self, links: list[dict[str, str]], origin: str
    ) -> list[DiscoveredLink]:
        discovered: list[DiscoveredLink] = []
        seen: set[str] = set()
        for link in links:
            url = link.get("href", "")
            if not url:
                continue
            try:
                canonical = self.canonicalize(url)
            except ValueError:
                continue
            if canonical not in seen:
                seen.add(canonical)
                discovered.append(DiscoveredLink(canonical, " ".join(link.get("text", "").split()), origin))
        return discovered

    def canonicalize(self, url: str) -> str:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise ValueError("URL_SCHEME_OR_HOST_INVALID")
        hostname = parts.hostname.lower()
        port = parts.port
        netloc = hostname if port is None else f"{hostname}:{port}"
        query = urlencode(
            [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
             if key.lower() not in TRACKING_PARAMETERS]
        )
        return urlunsplit((parts.scheme.lower(), netloc, parts.path or "/", query, ""))

    def classify(self, url: str, article: ExtractedArticle, cutoff: datetime) -> Classification:
        canonical = self.canonicalize(url)
        host = urlsplit(canonical).hostname or ""
        published_at = self._published_at(article, canonical)
        reason = "ACCEPTED"
        if host not in self.allowed_domains:
            reason = "DOMAIN_NOT_ALLOWED"
        elif not any(keyword in article.title for keyword in self.include_keywords):
            reason = "TITLE_NOT_MATCHED"
        elif any(keyword in article.title for keyword in self.exclude_keywords):
            reason = "EXCLUDED_KEYWORD"
        elif len(article.content) < self.minimum_content_chars and any(
            marker in article.content for marker in ("编导：", "音视频部制作", "视频")
        ):
            reason = "VIDEO_ONLY"
        elif "中共中央政治局" not in article.content or "召开会议" not in article.content:
            reason = "LEAD_NOT_MATCHED"
        elif "新华社北京" not in article.content and "来源：新华" not in article.content:
            reason = "SOURCE_NOT_OFFICIAL"
        elif published_at is None:
            reason = "PUBLISHED_AT_MISSING"
        elif published_at < cutoff:
            reason = "OUTSIDE_WINDOW"
        elif len(article.content) < self.minimum_content_chars:
            reason = "CONTENT_TOO_SHORT"
        return Classification(
            accepted=reason == "ACCEPTED",
            reason_code=reason,
            canonical_url=canonical,
            title=article.title,
            content=article.content,
            publisher="新华社" if "新华社" in article.content else "新华网",
            published_at=published_at,
            artifact_id=article.artifact_id,
        )

    @staticmethod
    def _published_at(article: ExtractedArticle, canonical_url: str) -> datetime | None:
        if article.published_hint:
            try:
                parsed = datetime.fromisoformat(article.published_hint.replace("Z", "+00:00"))
                return parsed if parsed.tzinfo else parsed.replace(tzinfo=BEIJING)
            except ValueError:
                pass
        text_match = re.search(r"(20\d{2})[-年](\d{1,2})[-月](\d{1,2})", article.content)
        url_match = re.search(r"/(20\d{2})-?(\d{2})[-/]?(\d{2})/", canonical_url)
        match = text_match or url_match
        if match is None:
            return None
        year, month, day = (int(value) for value in match.groups())
        return datetime(year, month, day, tzinfo=BEIJING)
```

日期提取优先级固定为 WebFetch `date`、正文时间、URL 日期。无法解析日期返回 `PUBLISHED_AT_MISSING`；窗口外返回 `OUTSIDE_WINDOW`。视频稿以正文仅包含编导/制作信息且不足最小长度判定，不以 URL 猜测。

- [ ] **步骤 4：验证所有分类边界**

运行：`.venv/bin/pytest src/tests/backend/collectors/test_xinhua_adapter.py -v`

预期：全部 PASS。

- [ ] **步骤 5：提交**

```bash
git add src/app/backend/policy_analysis/collectors/xinhua.py src/tests/backend/collectors
git commit -m "feat(新华社): 添加链接发现与通报判定"
```

### 任务 5：建立已核验近 5 年种子清单和幂等导入

**文件：**
- 创建：`src/app/backend/policy_analysis/collectors/resources/xinhua_politburo_seed_urls.json`
- 创建：`src/app/backend/policy_analysis/sources/bootstrap.py`
- 创建：`scripts/validate_seed_manifest.py`
- 测试：`src/tests/backend/sources/test_seed_manifest.py`

- [ ] **步骤 1：编写清单结构和导入测试**

```python
# src/tests/backend/sources/test_seed_manifest.py
from urllib.parse import urlsplit

from policy_analysis.sources.bootstrap import load_seed_manifest


def test_seed_manifest_contains_only_verified_unique_official_urls() -> None:
    entries = load_seed_manifest()
    assert entries
    assert len({entry.url for entry in entries}) == len(entries)
    assert all(entry.is_verified for entry in entries)
    assert all(entry.expected_title.startswith("中共中央政治局召开会议") for entry in entries)
    allowed = {"www.news.cn", "news.cn", "www.xinhuanet.com", "xinhuanet.com"}
    assert all(urlsplit(entry.url).hostname in allowed for entry in entries)
```

增加测试：导入两次行数不变；升级清单只新增缺失记录；数据库现场新增种子不会被删除。

- [ ] **步骤 2：运行并确认清单缺失失败**

运行：`.venv/bin/pytest src/tests/backend/sources/test_seed_manifest.py -v`

预期：FAIL，资源文件不存在或清单为空。

- [ ] **步骤 3：逐月核验并写入种子清单**

从执行日向前滚动 5 年，按每个月检索完整短语「中共中央政治局召开会议」。只记录能打开且正文符合新华社通报条件的官方 URL。每条 JSON 对象固定为：

```json
{
  "url": "https://www.news.cn/2021-10/18/c_1127969449.htm",
  "expected_title": "中共中央政治局召开会议 讨论拟提请十九届六中全会审议的文件 中共中央总书记习近平主持会议",
  "expected_published_date": "2021-10-18",
  "is_verified": true
}
```

不要记录搜索结果页、视频稿、转载页或 URL 无法访问的文章。维护检索可使用搜索引擎辅助，但生产任务不得调用搜索引擎。

- [ ] **步骤 4：验证清单和幂等导入**

运行：`.venv/bin/python scripts/validate_seed_manifest.py`

预期：输出 `seed manifest valid: 0 invalid, 0 duplicate` 并以状态码 0 退出。

运行：`.venv/bin/pytest src/tests/backend/sources/test_seed_manifest.py -v`

预期：全部 PASS。

- [ ] **步骤 5：提交**

```bash
git add src/app/backend/policy_analysis/collectors/resources src/app/backend/policy_analysis/sources/bootstrap.py scripts/validate_seed_manifest.py src/tests/backend/sources/test_seed_manifest.py
git commit -m "feat(新华社): 添加近五年会议通报种子清单"
```

### 任务 6：实现政策去重、修订、FTS5 检索和 API

**文件：**
- 创建：`migrations/versions/0003_policy_fts.py`
- 创建：`src/app/backend/policy_analysis/policies/schemas.py`
- 创建：`src/app/backend/policy_analysis/policies/repository.py`
- 创建：`src/app/backend/policy_analysis/policies/service.py`
- 创建：`src/app/backend/policy_analysis/policies/routes.py`
- 修改：`src/app/backend/policy_analysis/main.py`
- 测试：`src/tests/backend/policies/test_policy_service.py`
- 测试：`src/tests/backend/policies/test_policy_api.py`

- [ ] **步骤 1：编写去重、修订和组合检索测试**

```python
# src/tests/backend/policies/test_policy_service.py
def test_upsert_distinguishes_duplicate_and_revision(policy_service, article_record) -> None:
    first = policy_service.upsert(article_record, task_item_id=1)
    assert first.outcome == "stored"

    duplicate = policy_service.upsert(article_record, task_item_id=2)
    assert duplicate.outcome == "duplicate"
    assert duplicate.policy_id == first.policy_id

    changed = article_record.model_copy(update={"content": article_record.content + " 新增内容。"})
    revision = policy_service.upsert(changed, task_item_id=3)
    assert revision.outcome == "updated"
    assert policy_service.revision_count(first.policy_id) == 1
```

API 测试使用真实 FTS5，验证标题/正文关键词、发布时间、抓取时间、发布部门、类别、来源、分页和排序，以及无权限用户 403。

- [ ] **步骤 2：运行并确认红灯**

运行：`.venv/bin/pytest src/tests/backend/policies -v`

预期：FAIL，缺少政策服务或 API。

- [ ] **步骤 3：实现短事务 upsert 和 FTS5**

`0003_policy_fts.py` 创建 `policies_fts`，字段为 `title` 和 `content_text`，使用 external content 指向 `policies`，并创建 INSERT、UPDATE、DELETE 触发器。迁移后执行 rebuild。

`policies/schemas.py` 定义 runner 与政策服务之间唯一的写入类型：

```python
from datetime import datetime

from pydantic import BaseModel, HttpUrl


class PolicyWrite(BaseModel):
    source_id: int
    category_id: int
    title: str
    canonical_url: HttpUrl
    publisher: str
    published_at: datetime
    content_text: str
    content_hash: str
    webfetch_artifact_id: str
    crawled_at: datetime
```

Runner 将 `Classification` 转换为 `PolicyWrite`，`PolicyService.upsert(record: PolicyWrite, task_item_id: int)` 不接受松散字典。

`PolicyService.upsert` 在一个事务中：

1. 按 `source_id + canonical_url` 查询；
2. 不存在时按 `source_id + content_hash` 查询跨 URL 重复；
3. 新文档插入；
4. 同哈希只更新 `last_crawled_at`；
5. 不同哈希先插入 `policy_revisions`，再更新当前正文。

API：`GET /api/v1/policies`、`GET /api/v1/policies/{policy_id}`。正文始终返回纯文本。

- [ ] **步骤 4：运行迁移与测试**

运行：`.venv/bin/alembic upgrade head`

运行：`.venv/bin/pytest src/tests/backend/policies -v`

预期：全部 PASS，FTS5 查询命中标题和正文测试词。

- [ ] **步骤 5：提交**

```bash
git add migrations/versions/0003_policy_fts.py src/app/backend/policy_analysis/policies src/app/backend/policy_analysis/main.py src/tests/backend/policies
git commit -m "feat(政策): 添加去重修订与全文检索 API"
```

### 任务 7：实现任务状态机、日志和采集编排

**文件：**
- 创建：`src/app/backend/policy_analysis/tasks/state.py`
- 创建：`src/app/backend/policy_analysis/tasks/repository.py`
- 创建：`src/app/backend/policy_analysis/tasks/runner.py`
- 测试：`src/tests/backend/tasks/test_task_state.py`
- 测试：`src/tests/backend/tasks/test_task_runner.py`

- [ ] **步骤 1：编写状态转换和混合结果测试**

```python
# src/tests/backend/tasks/test_task_state.py
import pytest

from policy_analysis.tasks.state import TaskStatus, transition


def test_task_state_allows_only_documented_transitions() -> None:
    assert transition(TaskStatus.PENDING, TaskStatus.RUNNING) is TaskStatus.RUNNING
    assert transition(TaskStatus.RUNNING, TaskStatus.PARTIALLY_SUCCEEDED) is TaskStatus.PARTIALLY_SUCCEEDED
    with pytest.raises(ValueError, match="非法任务状态转换"):
        transition(TaskStatus.SUCCEEDED, TaskStatus.RUNNING)
```

Runner 测试使用真实临时数据库、真实新华适配器和实现完整契约的 Fake WebFetch transport。场景必须覆盖 stored、updated、duplicate、filtered、failed、取消，以及任一已核验种子失败导致回填验收失败。

- [ ] **步骤 2：运行并确认红灯**

运行：`.venv/bin/pytest src/tests/backend/tasks/test_task_state.py src/tests/backend/tasks/test_task_runner.py -v`

预期：FAIL，缺少任务状态或 runner。

- [ ] **步骤 3：实现状态机和 runner**

```python
# src/app/backend/policy_analysis/tasks/state.py
from policy_analysis.tasks.models import TaskStatus

ALLOWED_TRANSITIONS = {
    TaskStatus.PENDING: {TaskStatus.RUNNING, TaskStatus.CANCELLED},
    TaskStatus.RUNNING: {
        TaskStatus.SUCCEEDED,
        TaskStatus.PARTIALLY_SUCCEEDED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    },
}


def transition(current: TaskStatus, target: TaskStatus) -> TaskStatus:
    if target not in ALLOWED_TRANSITIONS.get(current, set()):
        raise ValueError(f"非法任务状态转换: {current} -> {target}")
    return target
```

`TaskRunner.run(task_id)` 固定顺序：领取任务、计算北京时间滚动边界、合并种子与入口候选、规范化去重、逐项抓取、判定、政策 upsert、保存明细、检查取消、汇总终态。任何外部请求期间不得持有数据库事务。

- [ ] **步骤 4：验证所有结果组合**

运行：`.venv/bin/pytest src/tests/backend/tasks/test_task_state.py src/tests/backend/tasks/test_task_runner.py -v`

预期：全部 PASS；失败场景的 `reason_code` 和任务统计精确匹配断言。

- [ ] **步骤 5：提交**

```bash
git add src/app/backend/policy_analysis/tasks src/tests/backend/tasks
git commit -m "feat(任务): 添加持久状态机与采集编排"
```

### 任务 8：实现任务工作线程、调度、恢复和 API

**文件：**
- 创建：`src/app/backend/policy_analysis/tasks/worker.py`
- 创建：`src/app/backend/policy_analysis/tasks/scheduler.py`
- 创建：`src/app/backend/policy_analysis/tasks/routes.py`
- 修改：`src/app/backend/policy_analysis/main.py`
- 测试：`src/tests/backend/tasks/test_worker_scheduler.py`
- 测试：`src/tests/backend/tasks/test_task_api.py`

- [ ] **步骤 1：编写互斥、恢复、取消和 API 测试**

```python
# src/tests/backend/tasks/test_worker_scheduler.py
def test_startup_marks_interrupted_running_task_failed_and_keeps_pending(task_repository) -> None:
    running = task_repository.create(rule_id=1, trigger_type="manual", requested_by=1)
    pending = task_repository.create(rule_id=2, trigger_type="manual", requested_by=1)
    task_repository.mark_running(running.id)

    recovered = task_repository.recover_interrupted()

    assert recovered == [running.id]
    assert task_repository.get(running.id).status == "failed"
    assert task_repository.get(running.id).error_summary == "服务异常中断"
    assert task_repository.get(pending.id).status == "pending"
```

增加同规则最多 1 个 running、相同计划时间幂等、计划初始停用、手工创建任务 201、普通用户只能读、取消需管理员与 CSRF、日志分页的测试。

- [ ] **步骤 2：运行并确认红灯**

运行：`.venv/bin/pytest src/tests/backend/tasks/test_worker_scheduler.py src/tests/backend/tasks/test_task_api.py -v`

预期：FAIL，缺少 worker、scheduler 或路由。

- [ ] **步骤 3：实现单进程调度和工作线程**

`TaskWorker` 使用 `ThreadPoolExecutor(max_workers=settings.tasks.max_workers)`，通过数据库原子 UPDATE 从 pending 领取任务。同规则已有 running 时保持 pending，不忙等；任务完成后再尝试下一项。

`TaskScheduler` 启动时读取启用计划并注册 APScheduler job，job 只创建持久任务，不直接抓取。应用 lifespan 顺序：迁移检查、密码同步、种子导入、任务恢复、worker 启动、scheduler 启动；关闭顺序相反并等待当前候选项完成。同步收紧 `/health/ready`：SQLite 可用且 worker 已启动时为 200；scheduler 或 WebFetch 短暂不可用只在 checks 中报告，不阻止平台就绪。

任务 API：

```text
GET  /api/v1/tasks
POST /api/v1/tasks
GET  /api/v1/tasks/{task_id}
POST /api/v1/tasks/{task_id}/cancel
GET  /api/v1/tasks/{task_id}/logs
GET  /api/v1/tasks/{task_id}/items
```

详情响应同时返回 5 个统计数和进度 `processed / discovered`。

- [ ] **步骤 4：验证调度与 API**

运行：`.venv/bin/pytest src/tests/backend/tasks -v`

运行：`.venv/bin/pytest src/tests/backend -q`

预期：全部 PASS，无测试线程遗留。

- [ ] **步骤 5：提交**

```bash
git add src/app/backend/policy_analysis/tasks src/app/backend/policy_analysis/main.py src/tests/backend/tasks
git commit -m "feat(调度): 添加任务工作线程与定时 API"
```

### 任务 9：完成采集后端集成验证

**文件：**
- 创建：`src/tests/backend/integration/test_collection_flow.py`
- 修改：`README.md`

- [ ] **步骤 1：先写完整纵向集成测试**

测试启动真实 FastAPI、临时 SQLite、临时密码文件、真实 service/repository/adapter 和 `httpx.MockTransport` WebFetch。登录管理员，创建或读取默认规则，手工触发任务，等待终态，检索政策，重复触发并断言政策总数不变，最后检查任务明细与日志。

- [ ] **步骤 2：运行并确认缺失装配导致红灯**

运行：`.venv/bin/pytest src/tests/backend/integration/test_collection_flow.py -v`

预期：如果 lifespan、依赖注入或任务通知尚未接通则 FAIL；失败必须来自真实缺口。

- [ ] **步骤 3：补齐最少应用装配和 README 采集说明**

只修复集成测试暴露的装配问题。README 说明 WebFetch 环境变量、来源规则、首次手工回填、确认后启用定时计划，以及日志和失败 reason code 的查看方式。

- [ ] **步骤 4：执行采集后端全量验证**

运行：`.venv/bin/ruff check src/app/backend src/tests/backend scripts`

运行：`.venv/bin/pytest --cov=policy_analysis --cov-report=term-missing --cov-fail-under=80 src/tests/backend -q`

预期：全部 PASS，覆盖率不低于 80%。

- [ ] **步骤 5：提交**

```bash
git add src/tests/backend/integration/test_collection_flow.py README.md src/app/backend/policy_analysis
git commit -m "test(采集): 添加政策回填与去重集成验证"
```
