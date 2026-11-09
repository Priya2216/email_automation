"""
API Executor - run the plan produced by the API Selection Agent.

All calls of one wave are executed concurrently through the MCP client
(asyncio.gather). Failures (timeout, 5xx after retries, business errors) are
attached to the affected request only, so one failing API never blocks the
others (partial success).
"""
from __future__ import annotations

import asyncio
from itertools import groupby

from agents.audit_agent import audit
from models.schemas import RequestStatus, ToolCallRecord
from utils.mcp_client import BankingMCPClient
from workflows.state import WorkflowState


class APIExecutor:
    name = "api_executor"

    def __init__(self, mcp: BankingMCPClient):
        self.mcp = mcp

    async def _run(self, step_id: str, tool: str, args: dict) -> ToolCallRecord:
        envelope, ms = await self.mcp.call(tool, args)
        err = envelope.get("error") or {}
        return ToolCallRecord(step_id=step_id, tool=tool, arguments=args, ok=bool(envelope.get("ok")),
                              duration_ms=ms, response=envelope.get("data") or {},
                              error=f"{err.get('code')}: {err.get('message')}" if err else None)

    async def run(self, state: WorkflowState) -> dict:
        plan = sorted(state.get("plan", []), key=lambda p: p.wave)
        requests = [r.model_copy(deep=True) for r in state["requests"]]
        by_id = {r.request_id: r for r in requests}
        records: dict[str, ToolCallRecord] = {}

        for _wave, calls in groupby(plan, key=lambda p: p.wave):
            unique = {c.step_id: c for c in calls}
            done = await asyncio.gather(*(self._run(c.step_id, c.tool, c.arguments) for c in unique.values()))
            records.update({r.step_id: r for r in done})

        for call in plan:
            req, rec = by_id[call.request_id], records[call.step_id]
            if rec.ok:
                req.status = RequestStatus.SUCCESS
                req.result = rec.response
            else:
                code = (rec.error or "").split(":")[0]
                req.status = RequestStatus.INVALID if code in ("INVALID_ACCOUNT", "INVALID_CARD") else RequestStatus.API_ERROR
                req.error = rec.error

        entry = audit(state, self.name, "APIS_EXECUTED", {
            "calls": [{"step": r.step_id, "tool": r.tool, "ok": r.ok, "ms": r.duration_ms, "error": r.error}
                      for r in records.values()],
        })
        return {"requests": requests, "tool_calls": list(records.values()), "trace": [entry]}
