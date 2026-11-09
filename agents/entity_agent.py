"""
Entity Extraction Agent - account number, card number, date range,
transaction count and (optional) customer name for every request.

Lookup order for each entity:
    1. the request's own clause          (source = "body")
    2. the rest of the email body        (only if unambiguous)
    3. text extracted from attachments   (source = "attachment")
The account can also come from the customer profile later (Validation Agent).

With Llama 3 the LLM proposes entities, but every number it returns must
literally appear in the email or attachment (anti-hallucination check).
"""
from __future__ import annotations

from datetime import date

from agents.audit_agent import audit
from config.settings import get_settings
from models.schemas import CustomerRequest, IntentType, RequestStatus
from utils.llm import ask_json
from utils.nlp_rules import extract_count, extract_period, number_in_text, numbers_of_kind
from utils.text_utils import sign_off_name, strip_sign_off
from workflows.state import WorkflowState

_KIND_FOR_INTENT = {
    IntentType.BALANCE_ENQUIRY: "account",
    IntentType.STATEMENT_REQUEST: "account",
    IntentType.CARD_TRANSACTIONS: "card",
}


class EntityExtractionAgent:
    name = "entity_extraction_agent"

    async def _llm_entities(self, req: CustomerRequest, email_text: str, sources: tuple[str, ...]) -> dict:
        reply = await ask_json("entity_extraction", text_span=req.text_span, email_text=email_text[:4000],
                               today=date.today().isoformat())
        if not isinstance(reply, dict):
            return {}
        out = {}
        for key in ("account_number", "card_number"):
            if reply.get(key) and number_in_text(str(reply[key]), *sources):
                out[key] = str(reply[key]).replace(" ", "").replace("-", "")
        for key in ("period_start", "period_end"):
            try:
                out[key] = date.fromisoformat(str(reply[key])) if reply.get(key) else None
            except ValueError:
                pass
        if str(reply.get("transaction_count") or "").isdigit():
            out["transaction_count"] = int(reply["transaction_count"])
        if reply.get("customer_name"):
            out["customer_name"] = str(reply["customer_name"])
        return out

    async def run(self, state: WorkflowState) -> dict:
        settings = get_settings()
        understanding = state["understanding"]
        body = strip_sign_off(understanding.clean_text)
        attachment_text = understanding.attachment_text
        all_requests = state["requests"]

        # numbers explicitly used by some clause can't be "borrowed" by another one
        claimed = {kind: set() for kind in ("account", "card")}
        for req in all_requests:
            kind = _KIND_FOR_INTENT.get(req.intent)
            if kind:
                claimed[kind].update(numbers_of_kind(req.text_span, kind, default_kind=kind))

        result: list[CustomerRequest] = []
        llm_used = False
        customer_name = sign_off_name(understanding.clean_text)

        for req in all_requests:
            kind = _KIND_FOR_INTENT.get(req.intent)
            if kind is None:  # UNSUPPORTED - nothing to extract
                req.status = RequestStatus.UNSUPPORTED
                result.append(req)
                continue

            llm = await self._llm_entities(req, body, (body, attachment_text)) if req.method == "llm" else {}
            llm_used = llm_used or bool(llm)
            customer_name = customer_name or llm.get("customer_name", "")

            # ---- account / card numbers ----------------------------------------
            key = f"{kind}_number"
            values = numbers_of_kind(req.text_span, kind, default_kind=kind)
            # the request itself may have been found in an attachment (e.g. a request form)
            source = "body" if req.text_span[:40].lower() in body.lower() else "attachment"
            if llm.get(key) and llm[key] not in values:
                values = [llm[key]] + values
            if not values:
                unclaimed = [v for v in numbers_of_kind(body, kind) if v not in claimed[kind]]
                if len(unclaimed) == 1:
                    values = unclaimed
            if not values and attachment_text:
                found = numbers_of_kind(attachment_text, kind)
                if len(found) == 1 or (found and len(all_requests) == 1):
                    values, source = found[:1], "attachment"

            # ---- statement period / transaction count ---------------------------
            period = None
            if req.intent == IntentType.STATEMENT_REQUEST:
                if llm.get("period_start") and llm.get("period_end"):
                    s, e = llm["period_start"], llm["period_end"]
                    period = (s, e, f"{s:%d-%b-%Y} to {e:%d-%b-%Y}")
                else:
                    period = (extract_period(req.text_span) or extract_period(body)
                              or extract_period(attachment_text or ""))
            count = None
            if req.intent == IntentType.CARD_TRANSACTIONS:
                count = llm.get("transaction_count") or extract_count(req.text_span) or extract_count(body)

            # one request per number ("balance of accounts A and B" -> 2 requests)
            for idx, value in enumerate(values or [None], start=1):
                r = req.model_copy(deep=True)
                if len(values) > 1:
                    r.request_id = f"{req.request_id}.{idx}"
                if value:
                    setattr(r, key, value)
                    r.entity_sources[key] = source
                if period:
                    r.period_start, r.period_end, r.period_label = period
                    r.entity_sources["period"] = "body"
                if r.intent == IntentType.CARD_TRANSACTIONS:
                    if count:
                        r.transaction_count = min(int(count), settings.max_transaction_count)
                        r.entity_sources["transaction_count"] = "body"
                    else:
                        r.transaction_count = settings.default_transaction_count
                        r.entity_sources["transaction_count"] = "default"
                result.append(r)

        profile = dict(state.get("customer_profile") or {})
        if customer_name:
            profile["name_in_email"] = customer_name
        entry = audit(state, self.name, "ENTITIES_EXTRACTED", {
            "method": "llm+rules" if llm_used else "rules",
            "customer_name_in_email": customer_name or None,
            "requests": [{"id": r.request_id, "intent": r.intent.value, "account": r.account_number,
                          "card": r.card_number, "period": r.period_label, "count": r.transaction_count,
                          "sources": r.entity_sources} for r in result],
        })
        return {"requests": result, "customer_profile": profile, "trace": [entry],
                "llm_agents": [self.name] if llm_used else []}
