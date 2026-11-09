"""
End-to-end tests of the business scenarios in the task document, running the
full agent workflow -> MCP server -> mock core banking API.
"""
from pathlib import Path

from conftest import make_email, run
from models.schemas import IntentType, RequestStatus
from utils.email_io import parse_email_bytes

INBOX = Path(__file__).resolve().parent.parent / "sample_emails" / "inbox"


def statuses(result):
    return [(r.intent, r.status) for r in result.requests]


async def test_scenario1_balance():
    r = await run(make_email(
        "Please provide my savings account balance for account number 1234567890."))
    assert statuses(r) == [(IntentType.BALANCE_ENQUIRY, RequestStatus.SUCCESS)]
    assert "XXXX7890" in r.response.body and "INR 1,25,000.00" in r.response.body
    assert "1234567890" not in r.response.body  # masked
    assert any(c.tool == "get_account_balance" for c in r.tool_calls)


async def test_scenario2_card_transactions():
    r = await run(make_email(
        "Please share my last 5 credit card transactions for card 4567XXXX8901."))
    assert statuses(r) == [(IntentType.CARD_TRANSACTIONS, RequestStatus.SUCCESS)]
    assert r.requests[0].transaction_count == 5
    assert r.requests[0].result["returned_count"] == 5


async def test_scenario3_statement():
    r = await run(make_email("Kindly send my bank statement for April 2026."))
    assert statuses(r) == [(IntentType.STATEMENT_REQUEST, RequestStatus.SUCCESS)]
    req = r.requests[0]
    assert req.entity_sources["account_number"] == "profile"  # resolved from sender's profile
    assert str(req.period_start) == "2026-04-01" and str(req.period_end) == "2026-04-30"
    assert len(r.response.attachments) == 1 and Path(r.response.attachments[0]).exists()


async def test_scenario4_multiple_requests():
    r = await run(make_email(
        "Please share my account balance for account 1234567890 and also send last 3 "
        "transactions of my credit card 987654321."))
    assert statuses(r) == [(IntentType.BALANCE_ENQUIRY, RequestStatus.SUCCESS),
                           (IntentType.CARD_TRANSACTIONS, RequestStatus.SUCCESS)]
    assert r.requests[1].result["returned_count"] == 3
    assert r.response.body.count("Dear ") == 1  # consolidated into ONE email


async def test_scenario5_partial_validation_failure():
    r = await run(make_email(
        "Please provide balance for account 1234567890 and card transactions for card 999999999."))
    assert statuses(r) == [(IntentType.BALANCE_ENQUIRY, RequestStatus.SUCCESS),
                           (IntentType.CARD_TRANSACTIONS, RequestStatus.INVALID)]
    body = r.response.body
    assert "INR 1,25,000.00" in body
    assert "could not be validated" in body and "recheck the card details" in body


async def test_missing_details_asks_for_clarification():
    r = await run(make_email("Please send my credit card transactions.",
                                       sender="rahul.verma@example.com"))
    assert statuses(r) == [(IntentType.CARD_TRANSACTIONS, RequestStatus.MISSING_INFO)]
    assert "need your credit card number" in r.response.body


async def test_core_system_timeout_is_partial_failure():
    r = await run(make_email(
        "Share the balance of account 5555555555 and the balance of account 1234567890."))
    assert [q.status for q in r.requests] == [RequestStatus.API_ERROR, RequestStatus.SUCCESS]
    assert "temporary system issue" in r.response.body


async def test_ownership_check_blocks_other_customers_account():
    # John asks for Priya's account -> must not leak her balance
    r = await run(make_email("What is the balance of account 2345678901?"))
    assert r.requests[0].status == RequestStatus.INVALID
    assert "4,82,310" not in r.response.body


async def test_angry_customer_is_escalated():
    r = await run(make_email(
        "Third time asking!! Unacceptable. Send last 2 transactions of card 4567123456788901 immediately "
        "or I will escalate."))
    assert r.escalate_to_human and r.sentiment.label.value == "NEGATIVE"
    assert "apologise" in r.response.body


async def test_pdf_attachment_is_downloaded_and_read():
    email = parse_email_bytes((INBOX / "06_pdf_attachment.eml").read_bytes(), "06_pdf_attachment")
    assert email.attachments and Path(email.attachments[0].saved_path).exists()
    assert "2345678901" in email.attachments[0].extracted_text
    r = await run(email)
    assert {q.intent for q in r.requests} == {IntentType.BALANCE_ENQUIRY, IntentType.STATEMENT_REQUEST}
    assert all(q.status == RequestStatus.SUCCESS for q in r.requests)
    assert all(q.entity_sources.get("account_number") == "attachment" for q in r.requests)


async def test_audit_trail_written():
    from utils import audit_db

    r = await run(make_email("Balance for account 1234567890 please"))
    events = {e["event"] for e in audit_db.fetch(r.run_id)}
    assert {"EMAIL_UNDERSTOOD", "INTENTS_DETECTED", "SENTIMENT_DETECTED", "ENTITIES_EXTRACTED",
            "VALIDATION_DONE", "EXECUTION_PLAN", "APIS_EXECUTED", "RESPONSE_DRAFTED",
            "RUN_COMPLETED"} <= events
    assert all("1234567890" not in str(e["detail"]) for e in audit_db.fetch(r.run_id))
