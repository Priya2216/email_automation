"""
Intent Classification Agent - finds every request in the email (multi-intent).

Llama 3 (when available) splits and classifies; its output is validated
(allowed intents, span must come from the email). Otherwise the rule engine
(utils/nlp_rules.detect_intents) is used. When the body itself contains no
banking request, the attachment text is checked (e.g. a scanned request form).
"""
from __future__ import annotations

from agents.audit_agent import audit
from models.schemas import CustomerRequest, IntentType
from utils.llm import ask_json
from utils.nlp_rules import DetectedIntent, detect_intents
from utils.text_utils import strip_sign_off
from workflows.state import WorkflowState


class IntentClassificationAgent:
    name = "intent_classification_agent"

    async def _llm_intents(self, text: str) -> list[DetectedIntent] | None:
        reply = await ask_json("intent_classification", email_text=text[:4000])
        items = reply.get("requests") if isinstance(reply, dict) else None
        if not isinstance(items, list):
            return None
        out = []
        for item in items:
            try:
                intent = IntentType(str(item.get("intent", "")).upper())
            except ValueError:
                continue
            span = str(item.get("text_span") or "").strip()
            if not span or span.lower() not in text.lower():
                span = text  # hallucinated span -> fall back to the whole email
            out.append(DetectedIntent(intent, span, float(item.get("confidence") or 0.8)))
        return out or None  # nothing usable -> let the rule engine decide

    async def run(self, state: WorkflowState) -> dict:
        understanding = state["understanding"]
        body = strip_sign_off(understanding.clean_text)

        method, source = "rules", "body"
        detected = await self._llm_intents(body)
        if detected is not None:
            method = "llm"
        else:
            detected = detect_intents(body)

        supported = [d for d in detected if d.intent != IntentType.UNSUPPORTED]
        if not supported and understanding.attachment_text:
            from_attachment = detect_intents(understanding.attachment_text)
            if any(d.intent != IntentType.UNSUPPORTED for d in from_attachment):
                detected, source = detected + from_attachment, "attachment"

        if not detected:
            detected = [DetectedIntent(IntentType.UNSUPPORTED, body[:300], 0.5)]

        requests = [
            CustomerRequest(request_id=f"R{i}", intent=d.intent, text_span=d.text_span,
                            confidence=round(d.confidence, 2), method=method)
            for i, d in enumerate(detected, start=1)
        ]
        entry = audit(state, self.name, "INTENTS_DETECTED", {
            "method": method, "source": source, "multi_intent": len(requests) > 1,
            "intents": [{"id": r.request_id, "intent": r.intent.value, "confidence": r.confidence,
                         "span": r.text_span} for r in requests],
        })
        return {"requests": requests, "trace": [entry], "llm_agents": [self.name] if method == "llm" else []}
