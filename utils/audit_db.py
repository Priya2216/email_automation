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

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, create_engine, select
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


class HumanReview(Base):
    """Human-in-the-loop queue. kind=APPROVAL: the reply is HELD until a reviewer approves/rejects it.
    kind=HANDOFF: the reply was sent but a person must follow up (unsupported / unanswered request)."""
    __tablename__ = "human_review"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)       # APPROVAL | HANDOFF
    status: Mapped[str] = mapped_column(String(16), index=True)     # PENDING|APPROVED|REJECTED|OPEN|RESOLVED
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    email_id: Mapped[str] = mapped_column(String(128), index=True)
    channel: Mapped[str] = mapped_column(String(16), default="email")
    language: Mapped[str] = mapped_column(String(8), default="en")
    sender: Mapped[str] = mapped_column(String(256))
    customer_email: Mapped[str] = mapped_column(String(256), default="")
    subject: Mapped[str] = mapped_column(String(512), default="")
    customer_message: Mapped[str] = mapped_column(Text, default="")  # masked original message
    reasons: Mapped[str] = mapped_column(Text, default="[]")         # JSON list
    overall_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    escalate: Mapped[bool] = mapped_column(Boolean, default=False)
    draft_to: Mapped[str] = mapped_column(String(256), default="")
    draft_subject: Mapped[str] = mapped_column(String(512), default="")
    draft_body: Mapped[str] = mapped_column(Text, default="")
    draft_attachments: Mapped[str] = mapped_column(Text, default="[]")  # JSON list of paths
    in_reply_to: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reviewer: Mapped[str] = mapped_column(String(128), default="")
    reviewer_note: Mapped[str] = mapped_column(Text, default="")
    final_body: Mapped[str] = mapped_column(Text, default="")        # body actually sent (maybe edited)
    delivered: Mapped[str] = mapped_column(Text, default="{}")       # JSON: files / SMTP result


class RunSummary(Base):
    """One row per processed message - feeds the monitoring dashboard and /metrics."""
    __tablename__ = "run_summary"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email_id: Mapped[str] = mapped_column(String(128), index=True)
    channel: Mapped[str] = mapped_column(String(16), default="email", index=True)
    language: Mapped[str] = mapped_column(String(8), default="en")
    sender: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    outcome: Mapped[str] = mapped_column(String(24), default="")      # FULL_SUCCESS|PARTIAL_SUCCESS|NO_DATA_RETURNED
    sentiment: Mapped[str] = mapped_column(String(12), default="")
    escalated: Mapped[bool] = mapped_column(Boolean, default=False)
    overall_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    review_status: Mapped[str] = mapped_column(String(16), default="NOT_REQUIRED")
    llm_used: Mapped[bool] = mapped_column(Boolean, default=False)
    intents: Mapped[str] = mapped_column(Text, default="[]")          # JSON list
    statuses: Mapped[str] = mapped_column(Text, default="[]")         # JSON list (per request)
    tool_calls: Mapped[str] = mapped_column(Text, default="[]")       # JSON [{tool, ok, ms}]


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


# --------------------------------------------------------------------------- #
# Human review queue
# --------------------------------------------------------------------------- #
def _review_dict(r: HumanReview, full: bool = True) -> dict:
    d = {"id": r.id, "kind": r.kind, "status": r.status, "run_id": r.run_id, "email_id": r.email_id,
         "channel": r.channel, "language": r.language, "sender": r.sender, "subject": r.subject,
         "reasons": json.loads(r.reasons), "overall_confidence": r.overall_confidence, "escalate": r.escalate,
         "created_at": r.created_at.isoformat(timespec="seconds"),
         "decided_at": r.decided_at.isoformat(timespec="seconds") if r.decided_at else None,
         "reviewer": r.reviewer, "reviewer_note": r.reviewer_note}
    if full:
        d.update({"customer_message": r.customer_message, "customer_email": r.customer_email,
                  "draft": {"to": r.draft_to, "subject": r.draft_subject, "body": r.draft_body,
                            "attachments": json.loads(r.draft_attachments)},
                  "final_body": r.final_body, "delivered": json.loads(r.delivered)})
    return d


def create_review(kind: str, run_id: str, email_id: str, channel: str, language: str, sender: str,
                  customer_email: str, subject: str, customer_message: str, reasons: list[str],
                  overall_confidence: float, escalate: bool, draft_to: str, draft_subject: str, draft_body: str,
                  draft_attachments: list[str], in_reply_to: str = "") -> int:
    status = "PENDING" if kind == "APPROVAL" else "OPEN"
    with Session(_get_engine()) as session:
        row = HumanReview(kind=kind, status=status, run_id=run_id, email_id=email_id, channel=channel,
                          language=language, sender=sender, customer_email=customer_email, subject=subject[:500],
                          customer_message=mask_value(customer_message)[:4000], reasons=json.dumps(mask_value(reasons)),
                          overall_confidence=overall_confidence, escalate=escalate, draft_to=draft_to,
                          draft_subject=draft_subject[:500], draft_body=draft_body,
                          draft_attachments=json.dumps(draft_attachments), in_reply_to=in_reply_to)
        session.add(row)
        session.commit()
        return row.id


def list_reviews(status: str | None = None, kind: str | None = None, limit: int = 100) -> list[dict]:
    with Session(_get_engine()) as session:
        stmt = select(HumanReview).order_by(HumanReview.id.desc()).limit(limit)
        if status:
            stmt = stmt.where(HumanReview.status == status.upper())
        if kind:
            stmt = stmt.where(HumanReview.kind == kind.upper())
        return [_review_dict(r, full=False) for r in session.scalars(stmt)]


def get_review(review_id: int) -> dict | None:
    with Session(_get_engine()) as session:
        row = session.get(HumanReview, review_id)
        return _review_dict(row) if row else None


def decide_review(review_id: int, status: str, reviewer: str, note: str = "", final_body: str = "",
                  delivered: dict | None = None) -> dict | None:
    with Session(_get_engine()) as session:
        row = session.get(HumanReview, review_id)
        if row is None:
            return None
        row.status, row.reviewer, row.reviewer_note = status, reviewer[:120], mask_value(note)[:2000]
        row.decided_at, row.final_body = datetime.now(), final_body
        row.delivered = json.dumps(delivered or {}, default=str)
        session.commit()
        return _review_dict(row)


# --------------------------------------------------------------------------- #
# Run summaries + metrics (monitoring dashboard)
# --------------------------------------------------------------------------- #
def record_run_summary(**f) -> None:
    with Session(_get_engine()) as session:
        session.add(RunSummary(
            run_id=f["run_id"], email_id=f["email_id"], channel=f.get("channel", "email"),
            language=f.get("language", "en"), sender=mask_value(f.get("sender", "")),
            duration_ms=f.get("duration_ms", 0), outcome=f.get("outcome", ""), sentiment=f.get("sentiment", ""),
            escalated=bool(f.get("escalated")), overall_confidence=float(f.get("overall_confidence", 0.0)),
            review_status=f.get("review_status", "NOT_REQUIRED"), llm_used=bool(f.get("llm_used")),
            intents=json.dumps(f.get("intents", [])), statuses=json.dumps(f.get("statuses", [])),
            tool_calls=json.dumps(f.get("tool_calls", []))))
        session.commit()


def update_run_review_status(run_id: str, review_status: str) -> None:
    with Session(_get_engine()) as session:
        row = session.scalars(select(RunSummary).where(RunSummary.run_id == run_id)).first()
        if row:
            row.review_status = review_status
            session.commit()


def _count(items) -> dict:
    out: dict = {}
    for i in items:
        out[i] = out.get(i, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def metrics(window_minutes: int = 60, recent: int = 15) -> dict:
    """Aggregated operational metrics over the last `window_minutes` (and totals since start)."""
    from datetime import timedelta

    now = datetime.now()
    since = now - timedelta(minutes=window_minutes)
    with Session(_get_engine()) as session:
        all_rows = list(session.scalars(select(RunSummary).order_by(RunSummary.id.desc()).limit(5000)))
        reviews = list(session.scalars(select(HumanReview).order_by(HumanReview.id.desc()).limit(2000)))
    rows = [r for r in all_rows if r.created_at >= since]

    durations = sorted(r.duration_ms for r in rows)
    pct = lambda p: durations[min(len(durations) - 1, int(len(durations) * p))] if durations else 0  # noqa: E731
    tools: dict[str, dict] = {}
    for r in rows:
        for c in json.loads(r.tool_calls):
            t = tools.setdefault(c["tool"], {"calls": 0, "failures": 0, "ms_total": 0})
            t["calls"] += 1
            t["failures"] += 0 if c.get("ok") else 1
            t["ms_total"] += c.get("ms", 0)
    tool_stats = [{"tool": k, "calls": v["calls"], "failures": v["failures"],
                   "avg_ms": round(v["ms_total"] / v["calls"]) if v["calls"] else 0,
                   "failure_rate": round(v["failures"] / v["calls"], 3) if v["calls"] else 0.0}
                  for k, v in sorted(tools.items())]

    buckets = {}  # runs per minute, last 30 minutes
    for r in all_rows:
        if r.created_at >= now - timedelta(minutes=30):
            key = r.created_at.replace(second=0, microsecond=0)
            buckets[key] = buckets.get(key, 0) + 1
    series = []
    for i in range(29, -1, -1):
        minute = (now - timedelta(minutes=i)).replace(second=0, microsecond=0)
        series.append({"minute": minute.strftime("%H:%M"), "runs": buckets.get(minute, 0)})

    decided = [r for r in reviews if r.decided_at]
    wait = [(r.decided_at - r.created_at).total_seconds() for r in decided if r.kind == "APPROVAL"]
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "window_minutes": window_minutes,
        "totals": {"runs_all_time": len(all_rows), "runs_in_window": len(rows),
                   "escalations": sum(r.escalated for r in rows),
                   "llm_used": sum(r.llm_used for r in rows)},
        "latency_ms": {"avg": round(sum(durations) / len(durations)) if durations else 0,
                       "p50": pct(0.5), "p95": pct(0.95), "max": durations[-1] if durations else 0},
        "avg_confidence": round(sum(r.overall_confidence for r in rows) / len(rows), 3) if rows else 0.0,
        "outcomes": _count(r.outcome for r in rows),
        "channels": _count(r.channel for r in rows),
        "languages": _count(r.language for r in rows),
        "sentiments": _count(r.sentiment for r in rows),
        "intents": _count(i for r in rows for i in json.loads(r.intents)),
        "request_statuses": _count(s for r in rows for s in json.loads(r.statuses)),
        "tools": tool_stats,
        "review_queue": {
            "pending_approval": sum(1 for r in reviews if r.kind == "APPROVAL" and r.status == "PENDING"),
            "open_handoffs": sum(1 for r in reviews if r.kind == "HANDOFF" and r.status == "OPEN"),
            "approved": sum(1 for r in reviews if r.status == "APPROVED"),
            "rejected": sum(1 for r in reviews if r.status == "REJECTED"),
            "resolved": sum(1 for r in reviews if r.status == "RESOLVED"),
            "avg_decision_seconds": round(sum(wait) / len(wait), 1) if wait else 0.0},
        "series": series,
        "recent_runs": [{"email_id": r.email_id, "channel": r.channel, "language": r.language,
                         "outcome": r.outcome, "sentiment": r.sentiment, "confidence": round(r.overall_confidence, 2),
                         "review": r.review_status, "escalated": r.escalated, "ms": r.duration_ms,
                         "intents": json.loads(r.intents), "at": r.created_at.strftime("%H:%M:%S")}
                        for r in all_rows[:recent]],
    }
