"""
Confidence scoring - "how sure is the system that the automatic reply is correct and complete?"

Per request:   0.60 * intent confidence       (how clearly the request was understood)
             + 0.25 * entity confidence       (were account / card / period found - and in the email itself,
                                               not guessed from the profile or a blurry OCR scan?)
             + 0.15 * execution confidence    (did validation + the API calls succeed?)

Overall = 0.6 * mean(requests) + 0.4 * weakest request, minus a small penalty when the (rule-based) language
support is weaker (Hindi / Marathi / unknown language). The overall score is compared with
CONFIDENCE_THRESHOLD: below it, the reply is held for a human reviewer.
"""
from __future__ import annotations

from typing import Optional

from models.schemas import CustomerRequest, IntentType, RequestStatus

W_INTENT, W_ENTITY, W_EXEC = 0.60, 0.25, 0.15

_EXEC_SCORE = {
    RequestStatus.SUCCESS: 1.0,
    RequestStatus.INVALID: 0.75,       # we are sure it is invalid; the customer must still follow up
    RequestStatus.MISSING_INFO: 0.70,
    RequestStatus.UNSUPPORTED: 0.50,
    RequestStatus.API_ERROR: 0.30,
    RequestStatus.PENDING: 0.40,
}


def _entity_score(r: CustomerRequest, ocr_confidence: Optional[float]) -> float:
    if r.intent == IntentType.POLICY_QUERY:
        best = float((r.result or {}).get("best_score") or 0.0)
        return min(1.0, 0.4 + best) if best else 0.2
    if r.intent == IntentType.UNSUPPORTED:
        return 0.7
    parts = []
    key = "card_number" if r.intent == IntentType.CARD_TRANSACTIONS else "account_number"
    source = r.entity_sources.get(key)
    parts.append({"body": 1.0, "attachment": 1.0, "profile": 0.85}.get(source or "", 0.4))
    if r.intent == IntentType.STATEMENT_REQUEST:
        parts.append(1.0 if r.period_start else 0.4)
    score = sum(parts) / len(parts)
    if ocr_confidence is not None and "attachment" in r.entity_sources.values():
        score *= min(1.0, ocr_confidence / 80.0)  # a blurry scan weakens every entity read from it
    return score


def score_requests(requests: list[CustomerRequest], language: str = "en", ocr_confidence: Optional[float] = None
                   ) -> dict:
    items = []
    for r in requests:
        intent_c = max(0.0, min(1.0, float(r.confidence or 0.0)))
        entity_c = _entity_score(r, ocr_confidence)
        exec_c = _EXEC_SCORE.get(r.status, 0.4)
        total = W_INTENT * intent_c + W_ENTITY * entity_c + W_EXEC * exec_c
        items.append({"id": r.request_id, "intent": r.intent.value, "status": r.status.value,
                      "intent_confidence": round(intent_c, 2), "entity_confidence": round(entity_c, 2),
                      "execution_confidence": round(exec_c, 2), "confidence": round(total, 2)})
    if not items:
        return {"overall": 0.0, "requests": [], "penalties": []}
    values = [i["confidence"] for i in items]
    overall = 0.6 * (sum(values) / len(values)) + 0.4 * min(values)
    penalties = []
    if language not in ("en",):
        overall -= 0.05
        penalties.append(f"language={language} (-0.05)")
    return {"overall": round(max(0.0, min(1.0, overall)), 2), "requests": items, "penalties": penalties}
