"""One-shot maintenance script: re-clean stale policy body text.

Records scraped before the body-cleaning pipeline shipped still carry webpage
page-chrome (breadcrumb/timestamp/source header, "阅读下一篇" footer) in their
stored ``content_text``. This script re-applies cleaning to every policy record
so legacy data is cleaned the same way as fresh scrapes, and recomputes
``content_hash`` to keep deduplication consistent.

Two modes:

- **默认（扁平清洗）**：仅对存量 ``content_text`` 跑 :func:`_clean_content`，剥离
  页脚/工具栏/编辑署名等装饰。但 WebFetch 的 ``generic.article`` 适配器会把标题
  预置在正文最前面，导致页眉（``新华网 > 时政 > 正文 … 来源：新华网``）不在行首，
  ``_clean_content`` 的行首锚定正则无法剥离它；且扁平正文没有换行，无法分段。

- **``--refetch``（重抓分段）**：按每条政策的 ``canonical_url`` 重新抓取原始 HTML，
  从 ``<p>`` 元素还原段落结构（:func:`extract_paragraphs` + :func:`_clean_content`），
  天然排除位于 ``<p>`` 之外的标题与页眉，并恢复多段正文。抓取失败或解析为空时，
  安全回退到扁平清洗（同默认模式），绝不因重抓失败阻塞清洗。

- 仅更新清洗后内容确有变化的行；未变化的行不写库。
- ``policies_fts_au`` 触发器会在 ``UPDATE OF content_text`` 时自动同步全文索引。
- 历史 ``policy_revisions`` 不在详情页展示，保持原样不动。
- 幂等：可重复执行，第二次运行不会产生变更。

用法（在 App 运行环境，venv 已激活，WebFetch 配置已通过 ``service.env`` 等注入）::

    python scripts/reclean_policy_content.py --dry-run              # 预览扁平清洗将变更的行
    python scripts/reclean_policy_content.py                         # 扁平清洗写库
    python scripts/reclean_policy_content.py --refetch --dry-run     # 预览重抓分段将变更的行
    python scripts/reclean_policy_content.py --refetch               # 重抓 HTML 还原段落并写库
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sqlite3
import sys
from collections.abc import Callable
from pathlib import Path

from policy_analysis.collectors.base import WebFetchClientError
from policy_analysis.collectors.xinhua import _clean_content, extract_paragraphs
from policy_analysis.core.settings import load_settings

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_CONFIG_PATH = _PROJECT_ROOT / "src/config/app.json"
_PREVIEW_CHARS = 80
_PREVIEW_SAMPLES = 3

#: Type alias for an injectable HTML fetcher (``url -> html``), used both by the
#: real :class:`WebFetchClient` and by tests faking the network.
FetchText = Callable[[str], str]


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _paragraph_body_from_html(html: str) -> str:
    """Return the cleaned, paragraph-structured body parsed from raw HTML.

    Mirrors :meth:`XinhuaCollector.paragraph_body`: parse ``<p>`` blocks then
    strip residual page chrome. Returns ``""`` when no paragraphs are found.
    """
    return _clean_content(extract_paragraphs(html))


def _refetch_body(fetch_text: FetchText, url: str, fallback: str) -> tuple[str, bool]:
    """Re-fetch HTML for ``url`` and return its paragraph-structured body.

    Returns ``(body, refetched)`` where ``refetched`` is ``True`` when the
    paragraph-structured body from re-fetched HTML was used, ``False`` when the
    fetch failed or yielded no paragraphs and ``fallback`` (the flat-cleaned
    content) was used instead.
    """
    try:
        html = fetch_text(url)
    except (WebFetchClientError, ValueError):
        return fallback, False
    body = _paragraph_body_from_html(html)
    if not body:
        return fallback, False
    return body, True


def _resolve_database_path(args: argparse.Namespace) -> Path:
    if args.db is not None:
        return args.db.expanduser().resolve()
    settings = load_settings(args.config, args.project_root, os.environ)
    return settings.database.path


def _build_fetcher(
    args: argparse.Namespace, fetcher: FetchText | None
) -> tuple[FetchText | None, str | None]:
    """Resolve the HTML fetcher for ``--refetch`` mode.

    When an explicit ``fetcher`` is injected (tests), it is used directly.
    Otherwise a real :class:`WebFetchClient` is built from App settings. Returns
    ``(fetcher, error)``; when WebFetch is not configured, ``fetcher`` is
    ``None`` and ``error`` carries a user-facing message.
    """
    if fetcher is not None:
        return fetcher, None
    if not args.refetch:
        return None, None
    settings = load_settings(args.config, args.project_root, os.environ)
    base_url = settings.webfetch.base_url.strip()
    api_key = settings.webfetch.api_key.get_secret_value().strip()
    if not base_url or not api_key:
        return None, "WebFetch 服务未配置（base_url/api_key 为空），无法执行 --refetch。"
    # Import lazily so the default flat-clean path does not require httpx.
    from policy_analysis.collectors.webfetch import WebFetchClient

    client = WebFetchClient(
        base_url,
        api_key,
        timeout_seconds=settings.webfetch.timeout_seconds,
        max_attempts=settings.tasks.retry_attempts,
    )
    return client.fetch_text, None


def main(
    argv: list[str] | None = None,
    *,
    fetcher: FetchText | None = None,
) -> int:
    parser = argparse.ArgumentParser(description="重新清洗存量政策正文，剥离网页装饰信息。")
    parser.add_argument("--dry-run", action="store_true", help="只预览将变更的行，不写库。")
    parser.add_argument(
        "--refetch",
        action="store_true",
        help="按 canonical_url 重新抓取 HTML 还原 <p> 段落结构（彻底去页眉并分段）；抓取失败时回退扁平清洗。",
    )
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

    resolved_fetcher, fetcher_error = _build_fetcher(args, fetcher)
    if args.refetch and resolved_fetcher is None:
        print(fetcher_error, file=sys.stderr)
        return 1

    connection = sqlite3.connect(str(db_path))
    try:
        try:
            rows = connection.execute(
                "SELECT id, canonical_url, content_text FROM policies ORDER BY id"
            ).fetchall()
        except sqlite3.OperationalError as error:
            print(f"读取 policies 表失败: {error}", file=sys.stderr)
            return 1

        scanned = len(rows)
        changed = 0
        unchanged = 0
        written = 0
        refetched = 0
        fell_back = 0
        samples: list[tuple[int, str]] = []

        for policy_id, canonical_url, content_text in rows:
            if not isinstance(content_text, str):
                unchanged += 1
                continue
            fallback = _clean_content(content_text)
            if args.refetch and isinstance(canonical_url, str) and canonical_url.strip():
                cleaned, used_refetch = _refetch_body(resolved_fetcher, canonical_url, fallback)
                if used_refetch:
                    refetched += 1
                else:
                    fell_back += 1
            else:
                cleaned = fallback
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
            mode = "重抓分段" if args.refetch else "扁平清洗"
            extra = f"，重抓 {refetched} 条，回退 {fell_back} 条" if args.refetch else ""
            print(f"[dry-run][{mode}] 扫描 {scanned} 条，将变更 {changed} 条，未变 {unchanged} 条{extra}。")
            for policy_id, preview in samples:
                print(f"  - policy #{policy_id} 清洗后预览: {preview}…")
            return 0

        connection.commit()
        mode = "重抓分段" if args.refetch else "扁平清洗"
        extra = f"，重抓 {refetched} 条，回退 {fell_back} 条" if args.refetch else ""
        print(f"[{mode}] 扫描 {scanned} 条，更新 {written} 条，未变 {unchanged} 条{extra}。")
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
