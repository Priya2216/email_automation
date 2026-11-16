"""
Human-review actions: approve (optionally with an edited body), reject, resolve a handoff.

Approving is what finally SENDS a held reply. Every decision is written to the audit trail
(HUMAN_APPROVED / HUMAN_REJECTED / HANDOFF_RESOLVED) together with the reviewer's name and note.
"""
from __future__ import annotations

from models.schemas import ResponseEmail
from utils import audit_db
from utils.email_io import deliver_reply


class ReviewError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _load(review_id: int, kind: str, allowed: tuple[str, ...]) -> dict:
    review = audit_db.get_review(review_id)
    if review is None:
        raise ReviewError(f"review #{review_id} not found", 404)
    if review["kind"] != kind:
        raise ReviewError(f"review #{review_id} is a {review['kind']} item, not {kind}", 409)
    if review["status"] not in allowed:
        raise ReviewError(f"review #{review_id} is already {review['status']}", 409)
    return review


def approve(review_id: int, reviewer: str, note: str = "", edited_body: str | None = None) -> dict:
    review = _load(review_id, "APPROVAL", ("PENDING",))
    draft = review["draft"]
    body = edited_body.strip() if edited_body and edited_body.strip() else draft["body"]
    response = ResponseEmail(to=draft["to"], subject=draft["subject"], body=body,
                             attachments=draft["attachments"])
    files = deliver_reply(response, review["email_id"], review["channel"], review["customer_email"],
                          language=review["language"])
    decided = audit_db.decide_review(review_id, "APPROVED", reviewer, note, body, files)
    audit_db.record(review["run_id"], review["email_id"], "human_review", "HUMAN_APPROVED",
                    {"review_id": review_id, "reviewer": reviewer, "edited": body != draft["body"],
                     "note": note, "files": files})
    audit_db.update_run_review_status(review["run_id"], "APPROVED")
    return decided


def reject(review_id: int, reviewer: str, note: str = "") -> dict:
    review = _load(review_id, "APPROVAL", ("PENDING",))
    decided = audit_db.decide_review(review_id, "REJECTED", reviewer, note, "", {})
    audit_db.record(review["run_id"], review["email_id"], "human_review", "HUMAN_REJECTED",
                    {"review_id": review_id, "reviewer": reviewer, "note": note})
    audit_db.update_run_review_status(review["run_id"], "REJECTED")
    return decided


def resolve_handoff(review_id: int, reviewer: str, note: str = "") -> dict:
    review = _load(review_id, "HANDOFF", ("OPEN",))
    decided = audit_db.decide_review(review_id, "RESOLVED", reviewer, note, "", {})
    audit_db.record(review["run_id"], review["email_id"], "human_review", "HANDOFF_RESOLVED",
                    {"review_id": review_id, "reviewer": reviewer, "note": note})
    audit_db.update_run_review_status(review["run_id"], "RESOLVED")
    return decided
