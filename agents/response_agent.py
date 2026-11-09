"""
Response Generation Agent - drafts one consolidated, professional reply.

Facts (balances, transactions, statement details) are ALWAYS rendered from
API data by templates; the LLM is only allowed to write the empathetic
opening sentence (and it is rejected if it contains any digits). This keeps
the reply grounded: the LLM can never invent a balance.
"""
from __future__ import annotations

import re
from datetime import date, datetime

from agents.audit_agent import audit
from models.schemas import CustomerRequest, IntentType, RequestStatus, ResponseEmail, SentimentLabel
from utils.llm import ask_json
from utils.masking import mask_account, mask_card, mask_text
from utils.text_utils import format_inr
from workflows.state import WorkflowState

_LABELS = {
    IntentType.BALANCE_ENQUIRY: "Account Balance",
    IntentType.CARD_TRANSACTIONS: "Credit Card Transactions",
    IntentType.STATEMENT_REQUEST: "Account Statement",
    IntentType.UNSUPPORTED: "Other Request",
}


def _d(value: str) -> str:
    try:
        return date.fromisoformat(value[:10]).strftime("%d-%b-%Y")
    except (ValueError, TypeError):
        return str(value)


class ResponseGenerationAgent:
    name = "response_generation_agent"

    # ------------------------------------------------------------------ sections
    def _success(self, r: CustomerRequest) -> tuple[str, list[str]]:
        d = r.result
        if r.intent == IntentType.BALANCE_ENQUIRY:
            note = " (your registered primary account)" if r.entity_sources.get("account_number") == "profile" else ""
            as_of = datetime.fromisoformat(d["as_of"]).strftime("%d-%b-%Y %H:%M") if d.get("as_of") else ""
            return (f"Account Balance:\n"
                    f"  • Account Number: {mask_account(d['account_number'])}{note}\n"
                    f"  • Account Type: {d.get('account_type', '')}\n"
                    f"  • Available Balance: {format_inr(d['available_balance'])}\n"
                    f"  • Ledger Balance: {format_inr(d['ledger_balance'])}\n"
                    f"  • As on: {as_of}"), []
        if r.intent == IntentType.CARD_TRANSACTIONS:
            lines = [f"Credit Card Transactions - Card {mask_card(d['card_last4'])} ({d.get('network', '')} "
                     f"{d.get('card_type', '')}), last {d['requested_count']}:"]
            for i, t in enumerate(d["transactions"], 1):
                sign = "Cr" if t["type"] == "CREDIT" else "Dr"
                lines.append(f"  {i}. {_d(t['date'])}  {t['description']:<30} {format_inr(t['amount']):>16} {sign}")
            if d["returned_count"] < d["requested_count"]:
                lines.append(f"  (Only {d['returned_count']} transaction(s) are available for this card.)")
            if not d["transactions"]:
                lines.append("  No transactions found on this card.")
            lines.append(f"  • Available credit limit: {format_inr(d['available_limit'])}")
            return "\n".join(lines), []
        if r.intent == IntentType.STATEMENT_REQUEST:
            note = " (your registered primary account)" if r.entity_sources.get("account_number") == "profile" else ""
            doc = d.get("document_path", "")
            return (f"Account Statement - {r.period_label or ''}:\n"
                    f"  • Account Number: {mask_account(d['account_number'])}{note}\n"
                    f"  • Period: {_d(d['period_start'])} to {_d(d['period_end'])}\n"
                    f"  • Opening Balance: {format_inr(d['opening_balance'])}\n"
                    f"  • Closing Balance: {format_inr(d['closing_balance'])}\n"
                    f"  • Transactions: {d['transaction_count']}\n"
                    f"  • Statement Reference: {d['statement_id']}\n"
                    f"  The statement is attached to this email as a PDF."), [doc] if doc else []
        return "", []

    def _failure(self, r: CustomerRequest, run_id: str) -> str:
        label = _LABELS[r.intent]
        if r.status == RequestStatus.INVALID:
            if r.intent == IntentType.CARD_TRANSACTIONS:
                return (f"Credit Card Request:\n  The credit card number provided ({mask_card(r.card_number or '')}) "
                        f"could not be validated. Kindly recheck the card details and resend your request.")
            return (f"{label} Request:\n  The account number provided ({mask_account(r.account_number or '')}) "
                    f"could not be validated. Kindly recheck the account details and resend your request.")
        if r.status == RequestStatus.MISSING_INFO:
            missing = ", ".join(n.replace("Missing ", "") for n in r.validation_notes if n.startswith("Missing"))
            return (f"{label} Request:\n  To process this request we need your {missing}. "
                    f"Kindly reply to this email with the details.")
        if r.status == RequestStatus.API_ERROR:
            ref = (f" for card {mask_card(r.card_number)}" if r.card_number else
                   f" for account {mask_account(r.account_number)}" if r.account_number else "")
            return (f"{label} Request:\n  We could not retrieve this information{ref} right now due to a temporary "
                    f"system issue. Our team has been notified and will share the details with you shortly "
                    f"(reference {run_id[:8].upper()}).")
        if r.status == RequestStatus.UNSUPPORTED:
            span = mask_text(re.sub(r"\s+", " ", r.text_span).strip())
            span = span if len(span) <= 120 else span[:117] + "..."
            return (f"Other Request:\n  Your request (\"{span}\") cannot be completed over email automatically. "
                    f"It has been forwarded to our customer support team, who will contact you.")
        return ""

    # ------------------------------------------------------------------ opening
    async def _opening(self, state: WorkflowState, all_ok: bool, any_ok: bool) -> tuple[str, bool]:
        sentiment = state["sentiment"]
        reply = await ask_json("response_opening", sentiment=sentiment.label.value,
                               summary=state["understanding"].summary)
        text = reply.get("opening", "") if isinstance(reply, dict) else ""
        if text and not re.search(r"\d", text) and len(text) < 400:
            return text.strip(), True
        if sentiment.label == SentimentLabel.NEGATIVE:
            base = "We sincerely apologise for the inconvenience you have experienced."
        elif sentiment.label == SentimentLabel.URGENT:
            base = "We understand the urgency of your request and have prioritised it."
        else:
            base = "Thank you for contacting us."
        tail = (" We have processed your request successfully." if all_ok else
                " We have processed your request. Please find the details below." if any_ok else
                " We have reviewed your request; please see the details below.")
        return base + tail, False

    async def run(self, state: WorkflowState) -> dict:
        email = state["email"]
        requests = state["requests"]
        profile = state.get("customer_profile") or {}
        name = (profile.get("name") or profile.get("validated_name") or profile.get("name_in_email")
                or email.sender_name or "Customer")

        ok = [r for r in requests if r.status == RequestStatus.SUCCESS]
        not_ok = [r for r in requests if r.status != RequestStatus.SUCCESS]
        opening, llm_used = await self._opening(state, all_ok=not not_ok, any_ok=bool(ok))

        sections, attachments = [], []
        for r in ok:
            text, files = self._success(r)
            if text in sections:  # same data asked twice -> show once
                continue
            sections.append(text)
            attachments.extend(f for f in files if f not in attachments)
        sections.extend(self._failure(r, state["run_id"]) for r in not_ok)

        if state["sentiment"].escalate_to_human:
            sections.append("We have flagged your email as a priority. A senior customer relationship officer "
                            "will contact you within 24 hours.")

        body = (f"Dear {name},\n\n{opening}\n\n" + "\n\n".join(s for s in sections if s) +
                "\n\nFor your security, we will never ask for your PIN, OTP or password over email."
                "\n\nThank you,\nCustomer Support Team\nDemo Bank Ltd.")
        subject = email.subject if email.subject.lower().startswith("re:") else f"Re: {email.subject or 'Your request'}"
        response = ResponseEmail(to=email.sender, subject=subject, body=body, attachments=attachments,
                                 in_reply_to=email.message_id)

        entry = audit(state, self.name, "RESPONSE_DRAFTED", {
            "opening_by_llm": llm_used, "sections": len(sections), "attachments": attachments,
            "outcome": "FULL_SUCCESS" if not not_ok else "PARTIAL_SUCCESS" if ok else "NO_DATA_RETURNED",
        })
        return {"response": response, "trace": [entry], "llm_agents": [self.name] if llm_used else []}
