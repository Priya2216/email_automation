"""Confidence scoring + human-in-the-loop approval (hold -> approve / reject, hand-off, audit trail)."""
from pathlib import Path

import pytest
from conftest import make_email, run
from config.settings import get_settings
from models.schemas import CustomerRequest, IntentType, RequestStatus
from utils import audit_db, review_service
from utils.confidence import score_requests

ANGRY = ("Third time asking!! Unacceptable. Send last 2 transactions of card 4567123456788901 immediately "
         "or I will escalate to the ombudsman.")


def _req(intent, conf, status, **kw):
    return CustomerRequest(request_id="R1", intent=intent, confidence=conf, status=status, **kw)


def test_confidence_orders_clear_above_ambiguous():
    clear = score_requests([_req(IntentType.BALANCE_ENQUIRY, 0.95, RequestStatus.SUCCESS,
                                 entity_sources={"account_number": "body"})])
    guessed = score_requests([_req(IntentType.STATEMENT_REQUEST, 0.55, RequestStatus.MISSING_INFO,
                                   entity_sources={"account_number": "profile"})])
    assert clear["overall"] > 0.9 > get_settings().confidence_threshold > guessed["overall"]
    assert clear["requests"][0]["intent_confidence"] == 0.95


def test_blurry_scan_lowers_entity_confidence():
    kw = dict(entity_sources={"account_number": "attachment"})
    sharp = score_requests([_req(IntentType.BALANCE_ENQUIRY, 0.95, RequestStatus.SUCCESS, **kw)], ocr_confidence=95)
    blurry = score_requests([_req(IntentType.BALANCE_ENQUIRY, 0.95, RequestStatus.SUCCESS, **kw)], ocr_confidence=40)
    assert blurry["overall"] < sharp["overall"]


async def test_normal_email_is_sent_automatically():
    r = await run(make_email("Please share my balance for account 1234567890.", email_id="auto-send"))
    assert r.overall_confidence >= 0.9 and not r.needs_human_review and r.review_status == "NOT_REQUIRED"
    assert Path(r.output_files["eml"]).exists()
    assert {b["id"] for b in r.confidence_breakdown["requests"]} == {"R1"}


async def test_escalated_email_is_held_then_approved_and_sent():
    r = await run(make_email(ANGRY, email_id="held-approve"))
    assert r.escalate_to_human and r.needs_human_review and r.review_status == "PENDING" and r.review_id
    assert "ESCALATION" in r.review_reasons[0]
    assert "eml" not in r.output_files                      # NOT sent yet
    assert Path(r.output_files["pending_review_draft"]).exists()

    item = audit_db.get_review(r.review_id)
    assert item["kind"] == "APPROVAL" and item["status"] == "PENDING"
    assert "4567123456788901" not in item["customer_message"]  # masked in the queue

    done = review_service.approve(r.review_id, "priya", "checked with fraud team", edited_body=None)
    assert done["status"] == "APPROVED" and done["reviewer"] == "priya"
    assert Path(done["delivered"]["eml"]).exists()          # approval is what sends it
    events = {e["event"] for e in audit_db.fetch(r.run_id)}
    assert {"CONFIDENCE_SCORED", "REVIEW_DECISION", "HUMAN_APPROVED"} <= events
    with pytest.raises(review_service.ReviewError):         # cannot decide twice
        review_service.approve(r.review_id, "someone-else")


async def test_reviewer_can_edit_the_reply_before_sending():
    r = await run(make_email(ANGRY, email_id="held-edit"))
    done = review_service.approve(r.review_id, "priya", edited_body="Dear Sir,\n\nEdited by a human.\n")
    assert Path(done["delivered"]["txt"]).read_text(encoding="utf-8").strip().endswith("Edited by a human.")
    assert audit_db.get_review(r.review_id)["final_body"].startswith("Dear Sir")


async def test_rejected_reply_is_never_sent():
    r = await run(make_email(ANGRY, email_id="held-reject"))
    done = review_service.reject(r.review_id, "priya", "wrong customer")
    assert done["status"] == "REJECTED" and done["delivered"] == {}
    assert not (get_settings().outbox_dir / "held-reject_response.eml").exists()


async def test_low_confidence_email_goes_to_review():
    r = await run(make_email("Hello, can you look into the thing we discussed yesterday?", email_id="vague"))
    assert r.needs_human_review and any(x.startswith("LOW_CONFIDENCE") for x in r.review_reasons)
    assert r.overall_confidence < get_settings().confidence_threshold


async def test_unsupported_request_is_sent_and_handed_off():
    r = await run(make_email("I need a new cheque book for account 1234567890.", email_id="handoff"))
    assert r.review_status == "HANDOFF_OPEN" and not r.needs_human_review
    assert Path(r.output_files["eml"]).exists()             # the acknowledgement IS sent
    item = audit_db.get_review(r.review_id)
    assert item["kind"] == "HANDOFF" and item["status"] == "OPEN"
    assert review_service.resolve_handoff(r.review_id, "priya", "called the customer")["status"] == "RESOLVED"
    with pytest.raises(review_service.ReviewError):         # a handoff is not an approval item
        review_service.approve(r.review_id, "priya")
