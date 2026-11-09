"""
API Selection Agent (API Decision Engine).

Decides, for every validated request:
  * WHICH core API (MCP tool) to call
  * the SEQUENCE: validations already ran in wave 0 (Validation Agent);
    data calls are independent of each other, so they all go in wave 1 and
    are executed in PARALLEL
  * de-duplication: the same call requested twice is executed once

Adding a new intent = add one entry to INTENT_TO_TOOL (extensibility NFR).
"""
from __future__ import annotations

from agents.audit_agent import audit
from models.schemas import CustomerRequest, IntentType, PlannedCall, RequestStatus
from workflows.state import WorkflowState


def _balance_args(r: CustomerRequest) -> dict:
    return {"account_number": r.account_number}


def _card_args(r: CustomerRequest) -> dict:
    return {"card_number": r.card_number, "count": r.transaction_count or 5}


def _statement_args(r: CustomerRequest) -> dict:
    return {"account_number": r.account_number, "period_start": r.period_start.isoformat(),
            "period_end": r.period_end.isoformat()}


INTENT_TO_TOOL = {
    IntentType.BALANCE_ENQUIRY: ("get_account_balance", _balance_args),
    IntentType.CARD_TRANSACTIONS: ("get_card_transactions", _card_args),
    IntentType.STATEMENT_REQUEST: ("get_statement", _statement_args),
}


class APISelectionAgent:
    name = "api_selection_agent"

    async def run(self, state: WorkflowState) -> dict:
        plan: list[PlannedCall] = []
        seen: dict[tuple, str] = {}
        decisions = []
        for req in state["requests"]:
            if req.status != RequestStatus.PENDING or req.intent not in INTENT_TO_TOOL:
                decisions.append({"id": req.request_id, "decision": "skip", "reason": req.status.value})
                continue
            tool, build_args = INTENT_TO_TOOL[req.intent]
            args = build_args(req)
            key = (tool, tuple(sorted(args.items())))
            if key in seen:  # identical call already planned
                plan.append(PlannedCall(step_id=seen[key], request_id=req.request_id, tool=tool,
                                        arguments=args, wave=1))
                decisions.append({"id": req.request_id, "decision": "reuse", "step": seen[key]})
                continue
            step_id = f"S{len(seen) + 1}"
            seen[key] = step_id
            plan.append(PlannedCall(step_id=step_id, request_id=req.request_id, tool=tool, arguments=args, wave=1))
            decisions.append({"id": req.request_id, "decision": "call", "tool": tool, "step": step_id})

        entry = audit(state, self.name, "EXECUTION_PLAN", {
            "distinct_calls": len(seen), "parallel_waves": 1 if seen else 0, "decisions": decisions,
        })
        return {"plan": plan, "trace": [entry]}
