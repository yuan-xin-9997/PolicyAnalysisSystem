from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from policy_analysis.auth.models import UTCDateTime
from policy_analysis.core.database import Base


class Credential(Base):
    __tablename__ = "ai_credentials"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    secret_hash: Mapped[str] = mapped_column(String(64), unique=True)
    scopes_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class OAuthAccess(Base):
    __tablename__ = "ai_oauth_access"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    credential_id: Mapped[str] = mapped_column(ForeignKey("ai_credentials.id"), index=True)
    resource: Mapped[str] = mapped_column(String(2048))
    scopes_json: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())


class IdempotentRequest(Base):
    __tablename__ = "ai_idempotent_requests"

    credential_id: Mapped[str] = mapped_column(ForeignKey("ai_credentials.id"), primary_key=True)
    key_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())


class AuditEvent(Base):
    __tablename__ = "ai_audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    credential_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    operation: Mapped[str] = mapped_column(String(16))
    target: Mapped[str] = mapped_column(String(512))
    status_code: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
