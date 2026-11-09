"""
REST API in front of the agent orchestrator (for demos / integration).

    uvicorn apis.orchestrator_api:app --port 8000
    open http://127.0.0.1:8000/docs

The mock core banking API must be running too (it is auto-started).
"""
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, File, UploadFile
from pydantic import BaseModel, Field

from models.schemas import IncomingEmail, ProcessingResult
from utils import audit_db
from utils.core_api_runner import ensure_core_api_running
from utils.email_io import parse_email_bytes
from utils.logger import setup_logging
from utils.mcp_client import BankingMCPClient
from workflows.email_workflow import build_workflow, process_email

_state: dict = {}


@asynccontextmanager
async def lifespan(_: FastAPI):
    setup_logging()
    ensure_core_api_running()
    async with BankingMCPClient() as mcp:  # one MCP session for the app's lifetime
        _state["mcp"], _state["graph"] = mcp, build_workflow(mcp)
        yield


app = FastAPI(title="Email Automation Orchestrator", version="1.0.0", lifespan=lifespan)


class EmailIn(BaseModel):
    sender: str = Field(..., examples=["john.doe@example.com"])
    subject: str = Field("Customer enquiry")
    body: str = Field(..., examples=["Please share my account balance for account 1234567890 and also "
                                     "send last 3 transactions of my credit card 987654321."])


@app.get("/health")
def health():
    return {"status": "UP", "mcp_tools": _state["mcp"].available_tools if "mcp" in _state else []}


@app.post("/v1/process-email", response_model=ProcessingResult)
async def process_json(email: EmailIn):
    incoming = IncomingEmail(email_id=f"api-{datetime.now():%Y%m%d%H%M%S%f}", sender=email.sender,
                             subject=email.subject, body=email.body)
    return await process_email(incoming, _state["mcp"], _state["graph"])


@app.post("/v1/process-eml", response_model=ProcessingResult)
async def process_eml(file: UploadFile = File(...)):
    """Upload a raw .eml (with attachments)."""
    incoming = parse_email_bytes(await file.read(), f"upload-{datetime.now():%Y%m%d%H%M%S%f}")
    return await process_email(incoming, _state["mcp"], _state["graph"])


@app.get("/v1/audit")
def audit_log(run_id: str | None = None, limit: int = 100):
    return audit_db.fetch(run_id, limit)
