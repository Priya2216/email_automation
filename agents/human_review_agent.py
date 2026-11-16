"""
Human-in-the-loop gate.

Decides what happens to the drafted reply:

  HOLD (kind=APPROVAL)   the reply is NOT sent; it waits in the review queue until a person approves
                         (optionally edits) or rejects it. Triggered by
                           * escalation   - angry customer, threats, fraud
                           * low overall confidence (< CONFIDENCE_THRESHOLD)
                           * low OCR confidence on a scanned attachment the request relied on
  HANDOFF (kind=HANDOFF) the reply IS sent, but the case is also queued for a person, because the reply says
                         "forwarded to our customer support team" (unsupported or unanswered request).
  NONE                   fully automatic.
"""
from __future__ import annotations

from pathlib import Path

from agents.audit_agent import audit
from config.settings import get_settings
from models.schemas import RequestStatus
from utils import audit_db
from workflows.state import WorkflowState


class HumanReviewGateAgent:
    name = "human_review_gate"

    async def run(self, state: WorkflowState) -> dict:
        s = get_settings()
        email, response = state["email"], state["response"]
        conf, sentiment = state["confidence"], state["sentiment"]
        understanding = state["understanding"]
        profile = state.get("customer_profile") or {}

        hold_reasons: list[str] = []
        if sentiment.escalate_to_human and s.review_on_escalation:
            hold_reasons.append("ESCALATION: " + (", ".join(sentiment.reasons[:5]) or "customer must be escalated"))
        if conf["overall"] < s.confidence_threshold and s.review_on_low_confidence:
            hold_reasons.append(f"LOW_CONFIDENCE: {conf['overall']} < threshold {s.confidence_threshold}")
        ocr = understanding.ocr_confidence
        uses_scan = any("attachment" in r.entity_sources.values() for r in state["requests"])
        if ocr is not None and uses_scan and ocr < s.ocr_min_confidence:
            hold_reasons.append(f"LOW_OCR_CONFIDENCE: {ocr} < {s.ocr_min_confidence}")

        unsupported = [r for r in state["requests"] if r.status == RequestStatus.UNSUPPORTED]
        if unsupported and s.human_review_enabled and not hold_reasons:
            kind, reasons = "HANDOFF", [f"UNSUPPORTED_REQUEST: {r.request_id} {r.intent.value}" for r in unsupported]
        elif hold_reasons and s.human_review_enabled:
            kind, reasons = "APPROVAL", hold_reasons + [
                f"UNSUPPORTED_REQUEST: {r.request_id}" for r in unsupported]
        else:
            kind, reasons = None, []

        review = {"hold": kind == "APPROVAL", "kind": kind, "reasons": reasons, "status": "NOT_REQUIRED",
                  "review_id": None}
        files = dict(state.get("output_files", {}))
        if kind:
            review_id = audit_db.create_review(
                kind=kind, run_id=state["run_id"], email_id=email.email_id, channel=email.channel,
                language=state.get("reply_language", "en"), sender=email.sender,
                customer_email=profile.get("email") or (email.sender if "@" in email.sender else ""),
                subject=email.subject, customer_message=understanding.clean_text, reasons=reasons,
                overall_confidence=conf["overall"], escalate=sentiment.escalate_to_human, draft_to=response.to,
                draft_subject=response.subject, draft_body=response.body, draft_attachments=response.attachments,
                in_reply_to=response.in_reply_to)
            review.update(review_id=review_id, status="PENDING" if kind == "APPROVAL" else "HANDOFF_OPEN")
            if kind == "APPROVAL":
                pending = s.outbox_dir / "pending_review"
                pending.mkdir(parents=True, exist_ok=True)
                draft = pending / f"{email.email_id}_draft.txt"
                draft.write_text(f"[HELD FOR HUMAN REVIEW #{review_id}] reasons: {'; '.join(reasons)}\n"
                                 f"To: {response.to}\nSubject: {response.subject}\n\n{response.body}\n",
                                 encoding="utf-8")
                files["pending_review_draft"] = str(Path(draft))
        entry = audit(state, self.name, "REVIEW_DECISION", {
            "decision": {"APPROVAL": "HELD_FOR_APPROVAL", "HANDOFF": "SENT_AND_HANDED_OFF", None: "AUTO_SEND"}[kind],
            "reasons": reasons, "review_id": review["review_id"], "overall_confidence": conf["overall"]})
        return {"review": review, "output_files": files, "trace": [entry]}
