"""Offline, fictional MIT sample corpus; never uses collection or model services."""

from __future__ import annotations

import argparse
import hashlib
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from policy_analysis.core.database import build_engine, session_factory
from policy_analysis.main import _upgrade_database
from policy_analysis.policies.models import Policy
from policy_analysis.sources.models import PolicyCategory, Source

SAMPLES = (
    (
        "教育资源共享办法（虚构样本）",
        "教育部门推动教育资源共享。学校开放图书资源，支持教师培训。每年公开项目进展。",
    ),
    (
        "教育数字资源办法（虚构样本）",
        "教育部门推动数字资源共享。学校加强数据安全，支持教师培训。每年公开项目进展。",
    ),
    (
        "社区绿色出行办法（虚构样本）",
        "社区鼓励绿色出行，完善步行设施。居民参与公共交通服务评价，每年公开项目进展。",
    ),
)


def import_demo(data_dir: Path) -> int:
    """Initialize an explicitly selected demo directory and insert missing samples."""
    from policy_analysis.auth.initialize import initialize_admin

    data_dir = data_dir.resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    initialize_admin(data_dir / "password.txt")
    database = data_dir / "app.sqlite3"
    _upgrade_database(database)
    engine = build_engine(database)
    inserted = 0
    try:
        with session_factory(engine).begin() as session:
            category = session.scalar(select(PolicyCategory).where(PolicyCategory.code == "offline-demo"))
            if category is None:
                category = PolicyCategory(
                    code="offline-demo", name="虚构演示", description="MIT 授权原创样本，无法律效力"
                )
                session.add(category)
            source = session.scalar(select(Source).where(Source.code == "offline-demo"))
            if source is None:
                source = Source(
                    code="offline-demo",
                    name="离线原创样本",
                    organization="虚构演示",
                    base_url="http://localhost/",
                    adapter_type="generic.article",
                    allowed_domains_json='["localhost"]',
                    is_active=False,
                )
                session.add(source)
            session.flush()
            now = datetime.now(UTC)
            for index, (title, body) in enumerate(SAMPLES, 1):
                url = f"http://localhost/offline-demo/{index}"
                if session.scalar(
                    select(Policy.id).where(Policy.source_id == source.id, Policy.canonical_url == url)
                ):
                    continue
                session.add(
                    Policy(
                        source_id=source.id,
                        category_id=category.id,
                        title=title,
                        canonical_url=url,
                        publisher="虚构演示机构",
                        published_at=datetime(2026, 1, index, tzinfo=UTC),
                        content_text=body,
                        content_hash=hashlib.sha256(body.encode()).hexdigest(),
                        webfetch_artifact_id=f"offline-demo-{index}",
                        first_crawled_at=now,
                        last_crawled_at=now,
                    )
                )
                inserted += 1
    finally:
        engine.dispose()
    return inserted


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True, type=Path, help="独立演示数据目录")
    args = parser.parse_args()
    count = import_demo(args.data_dir)
    print(f"Imported {count} samples. Data: {args.data_dir.resolve()}")
    print("Local login: admin; read password.txt privately. Never commit this file.")


if __name__ == "__main__":
    main()
