"""
REST API in front of the agent orchestrator (for demos / integration).

    uvicorn apis.orchestrator_api:app --port 8000
    open http://127.0.0.1:8000/docs         # Swagger UI
    open http://127.0.0.1:8000/dashboard    # real-time monitoring dashboard + review queue

Omnichannel : POST /v1/process-email | /v1/process-eml | /v1/process-message | /v1/process-voice
Human review: GET  /v1/review-queue, POST /v1/review-queue/{id}/approve | reject | resolve
Monitoring  : GET  /v1/metrics, /metrics (Prometheus), /dashboard, /health
RAG         : GET  /v1/policy/search?q=...

The mock core banking API must be running too (it is auto-started).
"""
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional
import tempfile

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel, Field

from config.settings import get_settings
from models.schemas import IncomingEmail, ProcessingResult
from utils import audit_db, rag, review_service, speech
from utils.attachments import ocr_languages, tesseract_available
from utils.core_api_runner import ensure_core_api_running
from utils.email_io import parse_email_bytes
from utils.llm import llm_enabled
from utils.logger import setup_logging
from utils.mcp_client import BankingMCPClient
from workflows.email_workflow import build_workflow, process_email

_state: dict = {}
_DASHBOARD = Path(__file__).parent / "static" / "dashboard.html"


@asynccontextmanager
async def lifespan(_: FastAPI):
    setup_logging()
    ensure_core_api_running()
    async with BankingMCPClient() as mcp:  # one MCP session for the app's lifetime
        _state["mcp"], _state["graph"] = mcp, build_workflow(mcp)
        yield


app = FastAPI(title="Email Automation Orchestrator", version="2.0.0", lifespan=lifespan)


# --------------------------------------------------------------------------- #
# Request models
# --------------------------------------------------------------------------- #
class EmailIn(BaseModel):
    sender: str = Field(..., examples=["john.doe@example.com"])
    subject: str = Field("Customer enquiry")
    body: str = Field(..., examples=["Please share my account balance for account 1234567890 and also "
                                     "send last 3 transactions of my credit card 987654321."])


class MessageIn(BaseModel):
    """A message from any channel. chat / SMS / WhatsApp / voice customers are identified by mobile number."""
    channel: Literal["email", "chat", "sms", "whatsapp", "voice"] = "chat"
    sender: str = Field(..., examples=["+919820000001"])
    text: str = Field(..., examples=["माझ्या खात्याची शिल्लक सांगा"])
    subject: str = "Customer enquiry"


class ReviewDecision(BaseModel):
    reviewer: str = Field("reviewer", examples=["priya.raut"])
    note: str = ""
    edited_body: Optional[str] = Field(None, description="optional corrected reply to send instead of the draft")


def require_reviewer(x_reviewer_key: Optional[str] = Header(default=None)) -> None:
    key = get_settings().reviewer_api_key
    if key and x_reviewer_key != key:
        raise HTTPException(401, "missing or invalid X-Reviewer-Key header")


async def _process(email: IncomingEmail) -> ProcessingResult:
    return await process_email(email, _state["mcp"], _state["graph"])


# --------------------------------------------------------------------------- #
# Health
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    s = get_settings()
    return {"status": "UP", "mcp_tools": _state["mcp"].available_tools if "mcp" in _state else [],
            "llm": f"ollama:{s.ollama_model}" if llm_enabled() else "rules (no LLM)",
            "rag": {"method": rag.get_index().method, "passages": len(rag.get_index().chunks)},
            "ocr": {"tesseract": tesseract_available(), "languages": ocr_languages() if tesseract_available() else ""},
            "speech_to_text": speech.stt_available(),
            "human_review": s.human_review_enabled, "confidence_threshold": s.confidence_threshold}


# --------------------------------------------------------------------------- #
# Processing (omnichannel)
# --------------------------------------------------------------------------- #
@app.post("/v1/process-email", response_model=ProcessingResult)
async def process_json(email: EmailIn):
    return await _process(IncomingEmail(email_id=f"api-{datetime.now():%Y%m%d%H%M%S%f}", sender=email.sender,
                                        subject=email.subject, body=email.body))


@app.post("/v1/process-eml", response_model=ProcessingResult)
async def process_eml(file: UploadFile = File(...)):
    """Upload a raw .eml (with attachments; images and scanned PDFs are read with OCR)."""
    incoming = parse_email_bytes(await file.read(), f"upload-{datetime.now():%Y%m%d%H%M%S%f}")
    return await _process(incoming)


@app.post("/v1/process-message", response_model=ProcessingResult)
async def process_message(msg: MessageIn):
    """Chat / SMS / WhatsApp / email text. The reply is shaped for the channel (see `reply_text`)."""
    return await _process(IncomingEmail(email_id=f"{msg.channel}-{datetime.now():%Y%m%d%H%M%S%f}",
                                        sender=msg.sender, subject=msg.subject, body=msg.text, channel=msg.channel))


@app.post("/v1/process-voice", response_model=ProcessingResult)
async def process_voice(sender: str = Form(..., description="caller's mobile number"),
                        transcript: str = Form("", description="ready transcript (skips speech-to-text)"),
                        audio: Optional[UploadFile] = File(None)):
    """Voice channel: audio -> speech-to-text (faster-whisper) -> same agent pipeline -> spoken-style reply."""
    text = transcript.strip()
    if not text:
        if audio is None:
            raise HTTPException(422, "send an audio file or a transcript")
        with tempfile.NamedTemporaryFile(suffix=Path(audio.filename or "call.wav").suffix, delete=False) as tmp:
            tmp.write(await audio.read())
        try:
            text, _lang = speech.transcribe(tmp.name)
        except speech.SpeechUnavailable as exc:
            raise HTTPException(501, f"{exc}. Send the `transcript` field instead.") from exc
        finally:
            Path(tmp.name).unlink(missing_ok=True)
    return await _process(IncomingEmail(email_id=f"voice-{datetime.now():%Y%m%d%H%M%S%f}", sender=sender,
                                        subject="Voice call", body=text, channel="voice"))


# --------------------------------------------------------------------------- #
# Human-in-the-loop review queue
# --------------------------------------------------------------------------- #
@app.get("/v1/review-queue", dependencies=[Depends(require_reviewer)])
def review_queue(status: Optional[str] = Query(None, description="PENDING | APPROVED | REJECTED | OPEN | RESOLVED"),
                 kind: Optional[str] = Query(None, description="APPROVAL | HANDOFF"), limit: int = 100):
    return audit_db.list_reviews(status, kind, limit)


@app.get("/v1/review-queue/{review_id}", dependencies=[Depends(require_reviewer)])
def review_detail(review_id: int):
    review = audit_db.get_review(review_id)
    if review is None:
        raise HTTPException(404, f"review #{review_id} not found")
    return review


def _decide(fn, review_id: int, *args):
    try:
        return fn(review_id, *args)
    except review_service.ReviewError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@app.post("/v1/review-queue/{review_id}/approve", dependencies=[Depends(require_reviewer)])
def review_approve(review_id: int, body: ReviewDecision):
    """Approve the held reply (optionally with a corrected body) - this is what actually sends it."""
    return _decide(review_service.approve, review_id, body.reviewer, body.note, body.edited_body)


@app.post("/v1/review-queue/{review_id}/reject", dependencies=[Depends(require_reviewer)])
def review_reject(review_id: int, body: ReviewDecision):
    return _decide(review_service.reject, review_id, body.reviewer, body.note)


@app.post("/v1/review-queue/{review_id}/resolve", dependencies=[Depends(require_reviewer)])
def review_resolve(review_id: int, body: ReviewDecision):
    """Close a HANDOFF item once the customer has been helped by a person."""
    return _decide(review_service.resolve_handoff, review_id, body.reviewer, body.note)


# --------------------------------------------------------------------------- #
# RAG, audit, monitoring
# --------------------------------------------------------------------------- #
@app.get("/v1/policy/search")
def policy_search(q: str, top_k: int = 3):
    """Direct look at what the RAG retriever returns (the agents use the same function via MCP)."""
    return rag.search_policy(q, top_k)


@app.get("/v1/audit")
def audit_log(run_id: str | None = None, limit: int = 100):
    return audit_db.fetch(run_id, limit)


@app.get("/v1/metrics")
def metrics(window_minutes: int = 60):
    return audit_db.metrics(window_minutes)


@app.get("/metrics", response_class=PlainTextResponse)
def prometheus_metrics():
    """Prometheus text format (scrape with Prometheus / Grafana Agent)."""
    m = audit_db.metrics(60 * 24 * 30)
    lines = ["# TYPE email_runs_total counter", f"email_runs_total {m['totals']['runs_in_window']}",
             "# TYPE email_escalations_total counter", f"email_escalations_total {m['totals']['escalations']}",
             "# TYPE email_run_latency_ms gauge", f"email_run_latency_ms{{stat=\"avg\"}} {m['latency_ms']['avg']}",
             f"email_run_latency_ms{{stat=\"p95\"}} {m['latency_ms']['p95']}",
             "# TYPE email_avg_confidence gauge", f"email_avg_confidence {m['avg_confidence']}",
             "# TYPE review_queue_pending gauge", f"review_queue_pending {m['review_queue']['pending_approval']}",
             f"review_queue_open_handoffs {m['review_queue']['open_handoffs']}",
             "# TYPE email_outcome_total counter"]
    lines += [f'email_outcome_total{{outcome="{k}"}} {v}' for k, v in m["outcomes"].items()]
    lines += ["# TYPE core_tool_calls_total counter"]
    lines += [f'core_tool_calls_total{{tool="{t["tool"]}"}} {t["calls"]}' for t in m["tools"]]
    lines += ["# TYPE core_tool_failures_total counter"]
    lines += [f'core_tool_failures_total{{tool="{t["tool"]}"}} {t["failures"]}' for t in m["tools"]]
    return "\n".join(lines) + "\n"


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    return HTMLResponse(_DASHBOARD.read_text(encoding="utf-8"))
