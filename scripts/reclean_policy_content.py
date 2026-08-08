"""One-shot maintenance script: re-clean stale policy body text.

Records scraped before the body-cleaning pipeline shipped still carry webpage
page-chrome (breadcrumb/timestamp/source header, "阅读下一篇" footer) in their
stored ``content_text``. This script re-applies :func:`_clean_content` to every
policy record so that legacy data is cleaned the same way as fresh scrapes, and
recomputes ``content_hash`` to keep deduplication consistent.

- 仅更新清洗后内容确有变化的行；未变化的行不写库。
- ``policies_fts_au`` 触发器会在 ``UPDATE OF content_text`` 时自动同步全文索引。
- 历史 ``policy_revisions`` 不在详情页展示，保持原样不动。
- 幂等：可重复执行，第二次运行不会产生变更。

用法（在 App 运行环境，venv 已激活）::

    python scripts/reclean_policy_content.py --dry-run   # 预览将变更的行
    python scripts/reclean_policy_content.py              # 正式执行写库
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sqlite3
import sys
from pathlib import Path

from policy_analysis.collectors.xinhua import _clean_content
from policy_analysis.core.settings import load_settings

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_CONFIG_PATH = _PROJECT_ROOT / "src/config/app.json"
_PREVIEW_CHARS = 80
_PREVIEW_SAMPLES = 3


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _resolve_database_path(args: argparse.Namespace) -> Path:
    if args.db is not None:
        return args.db.expanduser().resolve()
    settings = load_settings(args.config, args.project_root, os.environ)
    return settings.database.path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="重新清洗存量政策正文，剥离网页装饰信息。")
    parser.add_argument("--dry-run", action="store_true", help="只预览将变更的行，不写库。")
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="直接指定数据库文件路径；默认从 App 配置（src/config/app.json 或环境变量）解析。",
    )
    parser.add_argument("--config", type=Path, default=_CONFIG_PATH, help="配置文件路径。")
    parser.add_argument("--project-root", type=Path, default=_PROJECT_ROOT, help="项目根目录。")
    args = parser.parse_args(argv)

    db_path = _resolve_database_path(args)
    if not db_path.exists():
        print(f"数据库不存在: {db_path}", file=sys.stderr)
        return 1

    connection = sqlite3.connect(str(db_path))
    try:
        try:
            rows = connection.execute("SELECT id, content_text FROM policies ORDER BY id").fetchall()
        except sqlite3.OperationalError as error:
            print(f"读取 policies 表失败: {error}", file=sys.stderr)
            return 1

        scanned = len(rows)
        changed = 0
        unchanged = 0
        written = 0
        samples: list[tuple[int, str]] = []

        for policy_id, content_text in rows:
            if not isinstance(content_text, str):
                unchanged += 1
                continue
            cleaned = _clean_content(content_text)
            if cleaned == content_text:
                unchanged += 1
                continue
            changed += 1
            if len(samples) < _PREVIEW_SAMPLES:
                samples.append((policy_id, cleaned[:_PREVIEW_CHARS]))
            if not args.dry_run:
                connection.execute(
                    "UPDATE policies SET content_text = ?, content_hash = ? WHERE id = ?",
                    (cleaned, _content_hash(cleaned), policy_id),
                )
                written += 1

        if args.dry_run:
            print(f"[dry-run] 扫描 {scanned} 条，将变更 {changed} 条，未变 {unchanged} 条。")
            for policy_id, preview in samples:
                print(f"  - policy #{policy_id} 清洗后预览: {preview}…")
            return 0

        connection.commit()
        print(f"扫描 {scanned} 条，更新 {written} 条，未变 {unchanged} 条。")
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
