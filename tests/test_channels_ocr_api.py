"""OCR of attachments, omnichannel replies (chat / SMS / WhatsApp / voice) and the REST API
(review queue, metrics, Prometheus, dashboard)."""
import io
import shutil
from email.message import EmailMessage
from pathlib import Path

import pytest
from conftest import make_email, run
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFont

from config.settings import get_settings
from models.schemas import IncomingEmail, IntentType, RequestStatus
from utils.attachments import extract_text_with_confidence, tesseract_available
from utils.channels import format_reply, to_spoken
from utils.email_io import parse_email_bytes

needs_tesseract = pytest.mark.skipif(not tesseract_available(), reason="Tesseract is not installed")
needs_poppler = pytest.mark.skipif(not shutil.which("pdftoppm"), reason="poppler is not installed")


def _scan(path: Path, lines: list[str], suffix: str = "png") -> Path:
    img = Image.new("RGB", (1500, 120 + 100 * len(lines)), "white")
    draw, font = ImageDraw.Draw(img), ImageFont.load_default(size=46)
    for i, line in enumerate(lines):
        draw.text((30, 40 + 100 * i), line, fill="black", font=font)
    out = path.with_suffix(f".{suffix}")
    img.save(out)
    return out


# --------------------------------------------------------------------------- OCR
@needs_tesseract
def test_ocr_reads_an_image_and_reports_confidence(tmp_path):
    f = _scan(tmp_path / "form", ["Please share the balance of account 1234567890"])
    text, method, conf = extract_text_with_confidence(f, "image/png")
    assert method == "ocr" and "1234567890" in text and conf > 70


@needs_tesseract
@needs_poppler
def test_scanned_pdf_is_read_with_ocr(tmp_path):
    f = _scan(tmp_path / "scan", ["Statement for April 2026", "account 1234567890"], suffix="pdf")
    text, method, conf = extract_text_with_confidence(f, "application/pdf")
    assert method == "pdf-ocr" and "1234567890" in text and conf > 70


@needs_tesseract
async def test_request_inside_a_photo_is_processed(tmp_path):
    photo = _scan(tmp_path / "request", ["Please share the balance of account 1234567890"])
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = "John Doe <john.doe@example.com>", "support@demobank.example", "see attached"
    msg.set_content("Hi, please see the attached photo.")
    msg.add_attachment(photo.read_bytes(), maintype="image", subtype="png", filename="request.png")
    email = parse_email_bytes(msg.as_bytes(), "ocr-photo")
    assert email.attachments[0].extraction_method == "ocr" and email.attachments[0].ocr_confidence > 70
    r = await run(email)
    assert r.requests[0].intent == IntentType.BALANCE_ENQUIRY and r.requests[0].status == RequestStatus.SUCCESS
    assert r.requests[0].entity_sources["account_number"] == "attachment"


# --------------------------------------------------------------------------- channels
def _msg(channel, text, sender="+919820000001"):
    return IncomingEmail(email_id=f"{channel}-test", sender=sender, subject="chat", body=text, channel=channel)


async def test_whatsapp_marathi_customer_identified_by_mobile_number():
    r = await run(_msg("whatsapp", "कृपया माझ्या खात्याची शिल्लक सांगा"))
    assert r.channel == "whatsapp" and r.reply_language == "mr"
    assert r.requests[0].status == RequestStatus.SUCCESS          # found via get_customer_by_phone
    assert any(c.tool == "get_customer_by_phone" for c in r.tool_calls)
    assert "Dear" not in r.reply_text and "INR 1,25,000.00" in r.reply_text and "XXXX7890" in r.reply_text
    assert Path(r.output_files["txt"]).parent.name == "whatsapp" and "eml" not in r.output_files


async def test_sms_reply_is_short():
    r = await run(_msg("sms", "Please send the last 5 transactions of card 987654321"))
    assert len(r.reply_text) <= 480 and "\n" not in r.reply_text


async def test_voice_reply_is_spoken_style():
    r = await run(_msg("voice", "what is my account balance for account 1234567890"))
    assert "rupees" in r.reply_text and "INR" not in r.reply_text and "ending 7890" in r.reply_text
    assert to_spoken("INR 1,27,450.50") == "1,27,450 rupees and 50 paise"


async def test_statement_pdf_is_emailed_when_asked_on_whatsapp():
    r = await run(_msg("whatsapp", "Please send my statement for April 2026"))
    assert r.requests[0].status == RequestStatus.SUCCESS
    assert "statement_mail_eml" in r.output_files and Path(r.output_files["statement_mail_eml"]).exists()
    assert "PDF has been sent" in r.reply_text


def test_sms_formatting_truncates():
    assert len(format_reply("sms", "word " * 400)) <= 480


# --------------------------------------------------------------------------- REST API
@pytest.fixture(scope="module")
def client():
    from apis.orchestrator_api import app

    with TestClient(app) as c:
        yield c


def test_health_reports_every_capability(client):
    h = client.get("/health").json()
    assert {"search_policy", "get_customer_by_phone", "get_account_balance"} <= set(h["mcp_tools"])
    assert h["rag"]["passages"] >= 30 and "tesseract" in h["ocr"] and h["human_review"] is True


def test_process_message_and_voice_transcript(client):
    r = client.post("/v1/process-message", json={"channel": "chat", "sender": "+919820000002",
                                                 "text": "what is my balance"}).json()
    assert r["channel"] == "chat" and r["requests"][0]["status"] == "SUCCESS"
    assert "XXXX8901" in r["reply_text"] and "2345678901" not in r["reply_text"]  # Priya's account, masked
    v = client.post("/v1/process-voice", data={"sender": "+919820000001",
                                                "transcript": "balance of account 1234567890"}).json()
    assert v["channel"] == "voice" and "rupees" in v["reply_text"]


def test_voice_without_transcript_or_stt_is_a_clear_error(client):
    r = client.post("/v1/process-voice", data={"sender": "+919820000001"},
                    files={"audio": ("call.wav", io.BytesIO(b"RIFF....."), "audio/wav")})
    assert r.status_code in (200, 501)  # 501 = faster-whisper is not installed (the message says what to send)
    assert client.post("/v1/process-voice", data={"sender": "+919820000001"}).status_code == 422


def test_review_queue_over_the_api(client):
    r = client.post("/v1/process-email", json={
        "sender": "john.doe@example.com", "subject": "URGENT",
        "body": "Third time!! Unacceptable. Last 2 transactions of card 4567123456788901 immediately or I escalate."}).json()
    assert r["needs_human_review"] and r["review_status"] == "PENDING"
    rid = r["review_id"]
    assert any(i["id"] == rid for i in client.get("/v1/review-queue?status=PENDING").json())
    detail = client.get(f"/v1/review-queue/{rid}").json()
    assert detail["draft"]["body"].startswith("Dear John Doe")
    ok = client.post(f"/v1/review-queue/{rid}/approve", json={"reviewer": "priya", "note": "ok"})
    assert ok.status_code == 200 and ok.json()["status"] == "APPROVED"
    assert client.post(f"/v1/review-queue/{rid}/approve", json={"reviewer": "x"}).status_code == 409
    assert client.get("/v1/review-queue/999999").status_code == 404


def test_reviewer_key_is_enforced_when_configured(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "reviewer_api_key", "s3cret")
    assert client.get("/v1/review-queue").status_code == 401
    assert client.get("/v1/review-queue", headers={"X-Reviewer-Key": "s3cret"}).status_code == 200


def test_metrics_prometheus_and_dashboard(client):
    client.post("/v1/process-email", json={"sender": "john.doe@example.com", "subject": "b",
                                           "body": "balance for account 1234567890"})
    m = client.get("/v1/metrics").json()
    assert m["totals"]["runs_in_window"] >= 1 and m["tools"] and "BALANCE_ENQUIRY" in m["intents"]
    assert m["latency_ms"]["p95"] > 0 and len(m["series"]) == 30 and m["recent_runs"]
    prom = client.get("/metrics").text
    assert "email_runs_total" in prom and "core_tool_calls_total" in prom
    page = client.get("/dashboard")
    assert page.status_code == 200 and "Human review queue" in page.text


def test_policy_search_endpoint(client):
    hits = client.get("/v1/policy/search", params={"q": "how to block a lost card"}).json()["hits"]
    assert hits and "block" in hits[0]["heading"].lower()
