"""
Response Generation Agent - drafts one consolidated, professional reply.

Facts (balances, transactions, statement details) are ALWAYS rendered from API data by templates; the LLM is
only allowed to write the empathetic opening sentence (rejected if it contains any digit) and, for policy
questions, a paraphrase of the retrieved policy passages (rejected if it contains a number that is not in
the passages). This keeps the reply grounded: the LLM can never invent a balance or a fee.

The reply is written in the customer's language (English / Hindi / Marathi) and shaped for the channel:
a formal email, or a compact message for chat / SMS / WhatsApp / voice.
"""
from __future__ import annotations

import re
from datetime import date, datetime

from agents.audit_agent import audit
from config.settings import get_settings
from models.schemas import CustomerRequest, IntentType, RequestStatus, ResponseEmail, SentimentLabel
from utils.i18n import LANGUAGE_NAMES, reply_language, t, t_missing
from utils.llm import ask_json
from utils.masking import mask_account, mask_card, mask_text
from utils.text_utils import format_inr, normalize_digits
from workflows.state import WorkflowState

_LABEL_KEYS = {
    IntentType.BALANCE_ENQUIRY: "label_balance",
    IntentType.CARD_TRANSACTIONS: "label_txn",
    IntentType.STATEMENT_REQUEST: "label_statement",
    IntentType.POLICY_QUERY: "label_policy",
    IntentType.UNSUPPORTED: "label_other",
}


def _d(value: str) -> str:
    try:
        return date.fromisoformat(value[:10]).strftime("%d-%b-%Y")
    except (ValueError, TypeError):
        return str(value)


def _short_d(value: str) -> str:
    try:
        return date.fromisoformat(value[:10]).strftime("%d-%b")
    except (ValueError, TypeError):
        return str(value)


def _numbers(text: str) -> set[str]:
    nums = {n.rstrip(".,").replace(",", "") for n in re.findall(r"\d[\d,\.]*", normalize_digits(text or ""))}
    return {n for n in nums if n}


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("। "))
    return (cut[: end + 1] if end > limit * 0.5 else cut.rstrip() + "...").strip()


class ResponseGenerationAgent:
    name = "response_generation_agent"

    # ------------------------------------------------------------------ full (email) sections
    def _success(self, r: CustomerRequest, lang: str) -> tuple[str, list[str]]:
        d = r.result
        if r.intent == IntentType.BALANCE_ENQUIRY:
            note = t(lang, "primary_note") if r.entity_sources.get("account_number") == "profile" else ""
            as_on = datetime.fromisoformat(d["as_of"]).strftime("%d-%b-%Y %H:%M") if d.get("as_of") else ""
            return (f"{t(lang, 'label_balance')}:\n"
                    f"  • {t(lang, 'f_account_number')}: {mask_account(d['account_number'])}{note}\n"
                    f"  • {t(lang, 'f_account_type')}: {d.get('account_type', '')}\n"
                    f"  • {t(lang, 'f_available')}: {format_inr(d['available_balance'])}\n"
                    f"  • {t(lang, 'f_ledger')}: {format_inr(d['ledger_balance'])}\n"
                    f"  • {t(lang, 'f_as_on')}: {as_on}"), []
        if r.intent == IntentType.CARD_TRANSACTIONS:
            kind = f"{d.get('network', '')} {d.get('card_type', '')}".strip()
            lines = [t(lang, "txn_header", card=mask_card(d["card_last4"]), kind=kind, n=d["requested_count"])]
            for i, tx in enumerate(d["transactions"], 1):
                sign = "Cr" if tx["type"] == "CREDIT" else "Dr"
                lines.append(f"  {i}. {_d(tx['date'])}  {tx['description']:<30} {format_inr(tx['amount']):>16} {sign}")
            if d["returned_count"] < d["requested_count"]:
                lines.append(f"  {t(lang, 'txn_only', n=d['returned_count'])}")
            if not d["transactions"]:
                lines.append(f"  {t(lang, 'txn_none')}")
            lines.append(f"  • {t(lang, 'txn_limit')}: {format_inr(d['available_limit'])}")
            return "\n".join(lines), []
        if r.intent == IntentType.STATEMENT_REQUEST:
            note = t(lang, "primary_note") if r.entity_sources.get("account_number") == "profile" else ""
            doc = d.get("document_path", "")
            return (f"{t(lang, 'stmt_header', period=r.period_label or '')}\n"
                    f"  • {t(lang, 'f_account_number')}: {mask_account(d['account_number'])}{note}\n"
                    f"  • {t(lang, 'stmt_period')}: {_d(d['period_start'])} - {_d(d['period_end'])}\n"
                    f"  • {t(lang, 'stmt_opening')}: {format_inr(d['opening_balance'])}\n"
                    f"  • {t(lang, 'stmt_closing')}: {format_inr(d['closing_balance'])}\n"
                    f"  • {t(lang, 'stmt_txns')}: {d['transaction_count']}\n"
                    f"  • {t(lang, 'stmt_ref')}: {d['statement_id']}\n"
                    f"  {t(lang, 'stmt_attached')}"), [doc] if doc else []
        return "", []

    # ------------------------------------------------------------------ compact sections (chat / SMS / voice)
    def _success_compact(self, r: CustomerRequest, lang: str, has_email: bool) -> tuple[str, list[str]]:
        d = r.result
        if r.intent == IntentType.BALANCE_ENQUIRY:
            return t(lang, "c_balance", acct=mask_account(d["account_number"]),
                     avail=format_inr(d["available_balance"]), ledger=format_inr(d["ledger_balance"])), []
        if r.intent == IntentType.CARD_TRANSACTIONS:
            card = mask_card(d["card_last4"])
            if not d["transactions"]:
                return t(lang, "c_txns_none", card=card), []
            items = "; ".join(f"{_short_d(tx['date'])} {tx['description']} {tx['amount']:,.2f} "
                              f"{'Cr' if tx['type'] == 'CREDIT' else 'Dr'}" for tx in d["transactions"])
            return t(lang, "c_txns", card=card, n=d["returned_count"], items=items,
                     limit=format_inr(d["available_limit"])), []
        if r.intent == IntentType.STATEMENT_REQUEST:
            text = t(lang, "c_statement", period=r.period_label or "", closing=format_inr(d["closing_balance"]),
                     ref=d["statement_id"])
            doc = d.get("document_path", "")
            if doc and has_email:  # the PDF cannot travel by SMS/chat -> it is emailed (see workflow)
                text += " " + t(lang, "stmt_emailed")
            return text, [doc] if doc else []
        return "", []

    # ------------------------------------------------------------------ failures
    def _failure(self, r: CustomerRequest, run_id: str, lang: str) -> str:
        label = t(lang, _LABEL_KEYS[r.intent])
        if r.status == RequestStatus.INVALID:
            if r.intent == IntentType.CARD_TRANSACTIONS:
                return t(lang, "invalid_card", label=t(lang, "label_card_req"), n=mask_card(r.card_number or ""))
            return t(lang, "invalid_account", label=label, n=mask_account(r.account_number or ""))
        if r.status == RequestStatus.MISSING_INFO:
            items = [n.replace("Missing ", "") for n in r.validation_notes if n.startswith("Missing")]
            return t(lang, "missing", label=label, missing=", ".join(t_missing(lang, i) for i in items))
        if r.status == RequestStatus.API_ERROR:
            ref = (t(lang, "ref_card", n=mask_card(r.card_number)) if r.card_number else
                   t(lang, "ref_account", n=mask_account(r.account_number)) if r.account_number else "")
            return t(lang, "api_error", label=label, ref=ref, run=run_id[:8].upper())
        if r.status == RequestStatus.UNSUPPORTED:
            span = mask_text(re.sub(r"\s+", " ", r.text_span).strip())
            span = span if len(span) <= 120 else span[:117] + "..."
            return t(lang, "policy_none" if r.intent == IntentType.POLICY_QUERY else "unsupported", span=span)
        return ""

    # ------------------------------------------------------------------ policy answer (RAG)
    async def _policy(self, r: CustomerRequest, lang: str, compact: bool) -> tuple[str, bool, list[str]]:
        hits = (r.result or {}).get("hits") or []
        top = hits[:2]
        if len(top) == 2 and top[1]["score"] < 0.85 * top[0]["score"]:
            top = top[:1]
        context = "\n\n".join(f"[{i}] {h['title']} - {h['heading']}: {h['text']}" for i, h in enumerate(top, 1))

        reply = await ask_json("policy_answer", language=LANGUAGE_NAMES[lang], question=r.text_span[:500],
                               context=context)
        candidate = str(reply.get("answer", "")).strip() if isinstance(reply, dict) else ""
        used_llm = bool(candidate) and len(candidate) < 900 and \
            _numbers(candidate) <= _numbers(context + " " + r.text_span)  # no invented figures
        if used_llm:
            answer = candidate
        else:
            answer = " ".join(_truncate(h["text"], 450) for h in top)
            lead = t(lang, "policy_lead")
            answer = f"{lead} {answer}".strip() if lead else answer
        sources = list(dict.fromkeys(f"{h['title']} - {h['heading']}" for h in top))
        source_line = f"{t(lang, 'policy_source')}: {'; '.join(sources)}"
        if compact:
            return f"{_truncate(answer, 420)} ({source_line})", used_llm, sources
        return f"{t(lang, 'label_policy')}:\n  {answer}\n  {source_line}", used_llm, sources

    # ------------------------------------------------------------------ opening
    async def _opening(self, state: WorkflowState, lang: str, all_ok: bool, any_ok: bool) -> tuple[str, bool]:
        sentiment = state["sentiment"]
        reply = await ask_json("response_opening", language=LANGUAGE_NAMES[lang], sentiment=sentiment.label.value,
                               summary=state["understanding"].summary)
        text = reply.get("opening", "") if isinstance(reply, dict) else ""
        if text and not re.search(r"\d", text) and len(text) < 400:
            return text.strip(), True
        if sentiment.label == SentimentLabel.NEGATIVE:
            base = t(lang, "open_negative")
        elif sentiment.label == SentimentLabel.URGENT:
            base = t(lang, "open_urgent")
        else:
            base = t(lang, "open_neutral")
        tail = t(lang, "tail_all" if all_ok else "tail_any" if any_ok else "tail_none")
        return base + tail, False

    # ------------------------------------------------------------------ run
    async def run(self, state: WorkflowState) -> dict:
        email = state["email"]
        requests = state["requests"]
        profile = state.get("customer_profile") or {}
        lang = reply_language(state["understanding"].language, get_settings().reply_language)
        compact = email.channel != "email"
        name = (profile.get("name") or profile.get("validated_name") or profile.get("name_in_email")
                or email.sender_name or "Customer")

        ok = [r for r in requests if r.status == RequestStatus.SUCCESS]
        not_ok = [r for r in requests if r.status != RequestStatus.SUCCESS]

        sections, attachments, policy_sources, policy_llm = [], [], [], False
        for r in ok:
            if r.intent == IntentType.POLICY_QUERY:
                text, used, sources = await self._policy(r, lang, compact)
                policy_llm, files = policy_llm or used, []
                policy_sources.extend(s for s in sources if s not in policy_sources)
            elif compact:
                text, files = self._success_compact(r, lang, has_email=bool(profile.get("email")))
            else:
                text, files = self._success(r, lang)
            if text in sections:  # same data asked twice -> show once
                continue
            sections.append(text)
            attachments.extend(f for f in files if f not in attachments)
        sections.extend(self._failure(r, state["run_id"], lang) for r in not_ok)
        if compact:
            sections = [re.sub(r"\s+", " ", s).strip() for s in sections if s]

        escalate = state["sentiment"].escalate_to_human
        opening, llm_used = "", False
        if compact:
            if escalate:
                sections.append(t(lang, "c_flagged"))
            body = "\n".join([t(lang, "c_greeting", name=name.split()[0]), *[s for s in sections if s],
                              t(lang, "c_security")])
        else:
            opening, llm_used = await self._opening(state, lang, all_ok=not not_ok, any_ok=bool(ok))
            if escalate:
                sections.append(t(lang, "escalation"))
            body = (f"{t(lang, 'greeting', name=name)}\n\n{opening}\n\n" + "\n\n".join(s for s in sections if s) +
                    f"\n\n{t(lang, 'security')}\n\n{t(lang, 'signoff')}")
        subject = email.subject if email.subject.lower().startswith("re:") else f"Re: {email.subject or 'Your request'}"
        response = ResponseEmail(to=email.sender, subject=subject, body=body, attachments=attachments,
                                 in_reply_to=email.message_id)

        entry = audit(state, self.name, "RESPONSE_DRAFTED", {
            "opening_by_llm": llm_used, "policy_answer_by_llm": policy_llm, "sections": len(sections),
            "attachments": attachments, "language": lang, "channel": email.channel,
            "policy_sources": policy_sources,
            "outcome": "FULL_SUCCESS" if not not_ok else "PARTIAL_SUCCESS" if ok else "NO_DATA_RETURNED",
        })
        return {"response": response, "reply_language": lang, "trace": [entry],
                "llm_agents": [self.name] if (llm_used or policy_llm) else []}
