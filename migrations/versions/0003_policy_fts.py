"""Add the external-content policy full-text index.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-03
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE VIRTUAL TABLE policies_fts USING fts5(
            title,
            content_text,
            content='policies',
            content_rowid='id',
            tokenize='trigram'
        )
        """
    )
    op.execute(
        """
        CREATE TRIGGER policies_fts_ai AFTER INSERT ON policies BEGIN
            INSERT INTO policies_fts(rowid, title, content_text)
            VALUES (new.id, new.title, new.content_text);
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER policies_fts_ad AFTER DELETE ON policies BEGIN
            INSERT INTO policies_fts(policies_fts, rowid, title, content_text)
            VALUES ('delete', old.id, old.title, old.content_text);
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER policies_fts_au AFTER UPDATE OF title, content_text ON policies BEGIN
            INSERT INTO policies_fts(policies_fts, rowid, title, content_text)
            VALUES ('delete', old.id, old.title, old.content_text);
            INSERT INTO policies_fts(rowid, title, content_text)
            VALUES (new.id, new.title, new.content_text);
        END
        """
    )
    op.execute("INSERT INTO policies_fts(policies_fts) VALUES ('rebuild')")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS policies_fts_au")
    op.execute("DROP TRIGGER IF EXISTS policies_fts_ad")
    op.execute("DROP TRIGGER IF EXISTS policies_fts_ai")
    op.execute("DROP TABLE IF EXISTS policies_fts")
