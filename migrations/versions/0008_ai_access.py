"""Add independent automation credentials, OAuth tokens, audit and replay ledger."""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ai_credentials",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("secret_hash", sa.String(64), unique=True, nullable=False),
        sa.Column("scopes_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("expires_at", sa.String(40), nullable=False),
        sa.Column("revoked_at", sa.String(40), nullable=True),
    )
    op.create_index("ix_ai_credentials_user_id", "ai_credentials", ["user_id"])
    op.create_table(
        "ai_oauth_access",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("credential_id", sa.String(64), sa.ForeignKey("ai_credentials.id"), nullable=False),
        sa.Column("resource", sa.String(2048), nullable=False),
        sa.Column("scopes_json", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.String(40), nullable=False),
    )
    op.create_index("ix_ai_oauth_access_credential_id", "ai_oauth_access", ["credential_id"])
    op.create_table(
        "ai_idempotent_requests",
        sa.Column("credential_id", sa.String(64), sa.ForeignKey("ai_credentials.id"), primary_key=True),
        sa.Column("key_hash", sa.String(64), primary_key=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("response_body", sa.Text(), nullable=True),
        sa.Column("created_at", sa.String(40), nullable=False),
    )
    op.create_table(
        "ai_audit_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("credential_id", sa.String(64), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("operation", sa.String(16), nullable=False),
        sa.Column("target", sa.String(512), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
    )
    op.create_index("ix_ai_audit_events_credential_id", "ai_audit_events", ["credential_id"])


def downgrade():
    for table in ("ai_audit_events", "ai_idempotent_requests", "ai_oauth_access", "ai_credentials"):
        op.drop_table(table)
