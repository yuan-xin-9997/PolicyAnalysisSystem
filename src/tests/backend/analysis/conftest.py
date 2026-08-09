from __future__ import annotations

from datetime import UTC, datetime

import pytest
from policy_analysis.policies.models import Policy
from policy_analysis.sources.models import PolicyCategory, Source
from sqlalchemy.orm import Session, sessionmaker

NOW = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
CONTENT = "推动人工智能产业高质量发展，加快数字经济发展。人工智能是核心，产业是基础。"


@pytest.fixture
def policy_id(database_sessions: sessionmaker[Session]) -> int:
    with database_sessions.begin() as database:
        category = PolicyCategory(code="ai", name="人工智能", description=None, is_active=True)
        source = Source(
            code="xinhua",
            name="新华网",
            organization="新华社",
            base_url="https://www.news.cn/",
            adapter_type="xinhua",
            allowed_domains_json='["news.cn"]',
            is_active=True,
        )
        database.add_all([category, source])
        database.flush()
        policy = Policy(
            source_id=source.id,
            category_id=category.id,
            title="人工智能产业规划",
            canonical_url="https://www.news.cn/ai1.htm",
            publisher="国务院",
            published_at=NOW,
            content_text=CONTENT,
            content_hash="a" * 64,
            webfetch_artifact_id="art-1",
            first_crawled_at=NOW,
            last_crawled_at=NOW,
        )
        database.add(policy)
        database.flush()
        return policy.id
