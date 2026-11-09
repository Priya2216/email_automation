"""
Audit / Logging Agent.

* `audit()` is called by every agent to record a decision (DB + log + trace).
* `AuditAgent.run()` is the final workflow node: it writes the complete,
  masked execution record of the email to logs/runs/<email_id>.json.
"""
from __future__ import annotations

import json
import logging
import time

from config.settings import get_settings
from utils import audit_db
from utils.masking import mask_value
from workflows.state import WorkflowState

log = logging.getLogger("agents.audit")


def audit(state: WorkflowState, agent: str, event: str, detail) -> dict:
    """Persist one audit event; returns it so agents can add it to the trace."""
    email_id = state["email"].email_id
    entry = audit_db.record(state["run_id"], email_id, agent, event, detail)
    log.info("[%s] %s.%s %s", email_id, agent, event, json.dumps(entry["detail"], default=str)[:400])
    return entry


class AuditAgent:
    name = "audit_agent"

    async def run(self, state: WorkflowState) -> dict:
        settings = get_settings()
        runs_dir = settings.log_dir / "runs"
        runs_dir.mkdir(parents=True, exist_ok=True)

        email = state["email"]
        duration_ms = int((time.time() - state["started_at"]) * 1000)
        summary = {
            "run_id": state["run_id"],
            "email_id": email.email_id,
            "sender": email.sender,
            "subject": email.subject,
            "duration_ms": duration_ms,
            "llm_agents": sorted(set(state.get("llm_agents", []))),
            "sentiment": state["sentiment"].model_dump(mode="json"),
            "requests": [r.model_dump(mode="json") for r in state.get("requests", [])],
            "tool_calls": [c.model_dump(mode="json") for c in state.get("tool_calls", [])],
            "response": state["response"].model_dump(mode="json"),
            "trace": state.get("trace", []),
        }
        path = runs_dir / f"{email.email_id}.json"
        path.write_text(json.dumps(mask_value(summary), indent=2, default=str), encoding="utf-8")

        entry = audit(state, self.name, "RUN_COMPLETED",
                      {"duration_ms": duration_ms, "requests": len(summary["requests"]),
                       "tool_calls": len(summary["tool_calls"]),
                       "statuses": [r["status"] for r in summary["requests"]]})
        files = dict(state.get("output_files", {}))
        files["audit_json"] = str(path)
        return {"trace": [entry], "output_files": files}
