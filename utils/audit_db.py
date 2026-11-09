"""
Audit trail (non-functional requirement: "Log all API calls and decisions").

Every agent decision and every MCP tool call is stored in the `audit_log`
table. SQLite by default; set DATABASE_URL to use PostgreSQL. All payloads
are masked before they are written.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from config.settings import get_settings
from utils.masking import mask_value


class Base(DeclarativeBase):
    pass


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    email_id: Mapped[str] = mapped_column(String(128), index=True)
    agent: Mapped[str] = mapped_column(String(64))
    event: Mapped[str] = mapped_column(String(64))
    detail: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        settings = get_settings()
        settings.log_dir.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(settings.database_url, future=True)
        Base.metadata.create_all(_engine)
    return _engine


def record(run_id: str, email_id: str, agent: str, event: str, detail: Any) -> dict:
    """Persist one audit event and return it (masked) for the in-memory trace."""
    masked = mask_value(detail)
    with Session(_get_engine()) as session:
        session.add(AuditLog(run_id=run_id, email_id=email_id, agent=agent, event=event,
                             detail=json.dumps(masked, default=str)))
        session.commit()
    return {"agent": agent, "event": event, "detail": masked, "at": datetime.now().isoformat(timespec="seconds")}


def fetch(run_id: str | None = None, limit: int = 200) -> list[dict]:
    with Session(_get_engine()) as session:
        stmt = select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)
        if run_id:
            stmt = stmt.where(AuditLog.run_id == run_id)
        return [{"run_id": r.run_id, "email_id": r.email_id, "agent": r.agent, "event": r.event,
                 "detail": json.loads(r.detail), "created_at": r.created_at.isoformat()}
                for r in session.scalars(stmt)]
