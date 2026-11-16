"""Pydantic data models shared by every agent, the workflow and the APIs."""
from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class IntentType(str, Enum):
    BALANCE_ENQUIRY = "BALANCE_ENQUIRY"
    CARD_TRANSACTIONS = "CARD_TRANSACTIONS"
    STATEMENT_REQUEST = "STATEMENT_REQUEST"
    POLICY_QUERY = "POLICY_QUERY"      # "what are the late payment charges?" -> RAG over bank policies
    UNSUPPORTED = "UNSUPPORTED"


class SentimentLabel(str, Enum):
    POSITIVE = "POSITIVE"
    NEUTRAL = "NEUTRAL"
    NEGATIVE = "NEGATIVE"
    URGENT = "URGENT"


class RequestStatus(str, Enum):
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    INVALID = "INVALID"            # failed backend validation
    MISSING_INFO = "MISSING_INFO"  # needs clarification from the customer
    API_ERROR = "API_ERROR"        # core system failure / timeout
    UNSUPPORTED = "UNSUPPORTED"


# --------------------------------------------------------------------------- #
# Email
# --------------------------------------------------------------------------- #
class Attachment(BaseModel):
    filename: str
    content_type: str
    saved_path: str
    size_bytes: int
    extracted_text: str = ""
    extraction_method: str = ""  # pdf-text | pdf-ocr | ocr | plain-text | unsupported
    ocr_confidence: Optional[float] = None  # mean word confidence 0..100 (OCR only)


class IncomingEmail(BaseModel):
    email_id: str
    message_id: str = ""
    sender: str
    sender_name: str = ""
    subject: str = ""
    body: str
    received_at: datetime = Field(default_factory=datetime.now)
    attachments: list[Attachment] = []
    channel: str = "email"  # email | chat | sms | whatsapp | voice


# --------------------------------------------------------------------------- #
# Agent outputs
# --------------------------------------------------------------------------- #
class EmailUnderstanding(BaseModel):
    clean_text: str
    summary: str
    language: str = "en"
    attachment_text: str = ""
    ocr_confidence: Optional[float] = None  # lowest OCR confidence among the attachments


class SentimentResult(BaseModel):
    label: SentimentLabel = SentimentLabel.NEUTRAL
    score: float = 0.0               # -1 (very negative) .. +1 (very positive)
    is_urgent: bool = False
    escalate_to_human: bool = False  # human-in-the-loop flag
    reasons: list[str] = []
    method: str = "rules"


class CustomerRequest(BaseModel):
    """One actionable request found in an email (an email can contain many)."""
    request_id: str
    intent: IntentType
    text_span: str = ""
    confidence: float = 0.0
    method: str = "rules"
    # entities
    account_number: Optional[str] = None
    card_number: Optional[str] = None
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    period_label: Optional[str] = None
    transaction_count: Optional[int] = None
    entity_sources: dict[str, str] = {}  # entity -> body | attachment | profile
    # validation / execution
    status: RequestStatus = RequestStatus.PENDING
    validation_notes: list[str] = []
    result: dict[str, Any] = {}
    error: Optional[str] = None


class PlannedCall(BaseModel):
    step_id: str
    request_id: str
    tool: str
    arguments: dict[str, Any]
    wave: int = 1  # calls in the same wave run in parallel


class ToolCallRecord(BaseModel):
    step_id: str
    tool: str
    arguments: dict[str, Any]
    ok: bool
    duration_ms: int
    response: dict[str, Any] = {}
    error: Optional[str] = None


class ResponseEmail(BaseModel):
    to: str
    subject: str
    body: str
    attachments: list[str] = []
    in_reply_to: str = ""


class ProcessingResult(BaseModel):
    email_id: str
    run_id: str
    sender: str
    language: str
    sentiment: SentimentResult
    requests: list[CustomerRequest]
    tool_calls: list[ToolCallRecord]
    response: ResponseEmail
    escalate_to_human: bool
    llm_used: bool
    duration_ms: int
    output_files: dict[str, str] = {}
    # --- channel / language -------------------------------------------------------
    channel: str = "email"
    reply_language: str = "en"
    reply_text: str = ""  # the reply formatted for the channel (plain text; spoken style for voice)
    # --- confidence scoring + human-in-the-loop -----------------------------------
    overall_confidence: float = 0.0
    confidence_breakdown: dict[str, Any] = {}
    needs_human_review: bool = False
    review_reasons: list[str] = []
    review_status: str = "NOT_REQUIRED"  # NOT_REQUIRED | PENDING | APPROVED | REJECTED
    review_id: Optional[int] = None
