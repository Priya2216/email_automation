"""
Validation Agent - validates the extracted data before any data is fetched.

1. Customer lookup by sender email (MCP: get_customer_by_email)
2. Fill gaps: a balance/statement request without an account number uses
   the customer's registered primary account
3. Missing details -> MISSING_INFO (customer is asked to clarify)
4. Format checks (10-12 digit account, 16-digit / masked / 9-digit card ref)
5. Backend validation, all numbers in parallel
   (MCP: validate_account / validate_card)
6. Ownership: the account/card must belong to the sender when the sender is
   a known customer (prevents data leakage to a third party)
"""
from __future__ import annotations

import asyncio
import re

from agents.audit_agent import audit
from models.schemas import CustomerRequest, IntentType, RequestStatus, ToolCallRecord
from utils.mcp_client import BankingMCPClient
from workflows.state import WorkflowState

_CARD_FORMAT = re.compile(r"^(\d{16}|\d{4}[Xx*]{4,8}\d{4}|\d{9})$")
_ACCOUNT_FORMAT = re.compile(r"^\d{10,12}$")


class ValidationAgent:
    name = "validation_agent"

    def __init__(self, mcp: BankingMCPClient):
        self.mcp = mcp

    async def _call(self, step_id: str, tool: str, args: dict) -> ToolCallRecord:
        envelope, ms = await self.mcp.call(tool, args)
        return ToolCallRecord(step_id=step_id, tool=tool, arguments=args, ok=bool(envelope.get("ok")),
                              duration_ms=ms, response=envelope.get("data") or {},
                              error=(envelope.get("error") or {}).get("code"))

    async def run(self, state: WorkflowState) -> dict:
        email = state["email"]
        requests: list[CustomerRequest] = [r.model_copy(deep=True) for r in state["requests"]]
        calls: list[ToolCallRecord] = []

        # 1. who is the sender?
        lookup = await self._call("V0", "get_customer_by_email", {"email": email.sender})
        calls.append(lookup)
        profile = dict(state.get("customer_profile") or {})
        if lookup.ok and lookup.response.get("status") == "FOUND":
            profile.update(lookup.response)
        known_customer = profile.get("status") == "FOUND"

        # 2-4. completeness + format
        for req in requests:
            if req.status != RequestStatus.PENDING:
                continue
            if req.intent in (IntentType.BALANCE_ENQUIRY, IntentType.STATEMENT_REQUEST) and not req.account_number:
                if known_customer and profile.get("primary_account"):
                    req.account_number = profile["primary_account"]
                    req.entity_sources["account_number"] = "profile"
                    req.validation_notes.append("Account taken from the customer's registered profile")
                else:
                    self._missing(req, "account number")
            if req.intent == IntentType.CARD_TRANSACTIONS and not req.card_number:
                self._missing(req, "credit card number")
            if req.intent == IntentType.STATEMENT_REQUEST and not req.period_start:
                self._missing(req, "statement period (month/year or date range)")
            if req.status != RequestStatus.PENDING:
                continue
            if req.account_number and not _ACCOUNT_FORMAT.match(req.account_number):
                self._invalid(req, "Account number format is invalid")
            if req.card_number and not _CARD_FORMAT.match(req.card_number):
                self._invalid(req, "Card number format is invalid")

        # 5. backend validation - every distinct number once, all in parallel
        accounts = sorted({r.account_number for r in requests if r.status == RequestStatus.PENDING and r.account_number})
        cards = sorted({r.card_number for r in requests if r.status == RequestStatus.PENDING and r.card_number})
        jobs = [self._call(f"VA{i}", "validate_account", {"account_number": a}) for i, a in enumerate(accounts, 1)]
        jobs += [self._call(f"VC{i}", "validate_card", {"card_number": c}) for i, c in enumerate(cards, 1)]
        results = await asyncio.gather(*jobs)
        calls.extend(results)
        by_number = {}
        for rec in results:
            number = rec.arguments.get("account_number") or rec.arguments.get("card_number")
            by_number[number] = rec

        # 6. apply results + ownership
        for req in requests:
            if req.status != RequestStatus.PENDING:
                continue
            for number in filter(None, (req.account_number, req.card_number)):
                rec = by_number[number]
                if not rec.ok:
                    req.status = RequestStatus.API_ERROR
                    req.error = rec.error
                    req.validation_notes.append(f"Validation API failed: {rec.error}")
                elif rec.response.get("status") != "VALID":
                    self._invalid(req, rec.response.get("reason", "Not found"))
                elif known_customer and rec.response.get("customer_id") != profile.get("customer_id"):
                    self._invalid(req, "Does not belong to the sender's registered customer profile")
                else:
                    req.validation_notes.append(f"{'Card' if number == req.card_number else 'Account'} validated")
                    profile.setdefault("validated_name", rec.response.get("customer_name"))
                if req.status != RequestStatus.PENDING:
                    break

        entry = audit(state, self.name, "VALIDATION_DONE", {
            "sender_known": known_customer, "customer_id": profile.get("customer_id"),
            "parallel_validation_calls": len(jobs),
            "results": [{"id": r.request_id, "status": r.status.value, "notes": r.validation_notes}
                        for r in requests],
        })
        return {"requests": requests, "customer_profile": profile, "tool_calls": calls, "trace": [entry]}

    @staticmethod
    def _missing(req: CustomerRequest, what: str) -> None:
        req.status = RequestStatus.MISSING_INFO
        req.validation_notes.append(f"Missing {what}")

    @staticmethod
    def _invalid(req: CustomerRequest, reason: str) -> None:
        req.status = RequestStatus.INVALID
        req.validation_notes.append(reason)
