"""
Agent Orchestrator - the agentic workflow, built with LangGraph.

            ┌──────────────────────────┐
            │ email_understanding      │
            └────────────┬─────────────┘
              ┌──────────┴──────────┐        (parallel branches)
     ┌────────▼────────┐   ┌────────▼────────┐
     │ intent_classify │   │ sentiment       │
     └────────┬────────┘   └────────┬────────┘
              └──────────┬──────────┘
                ┌────────▼────────┐
                │ entity_extract  │
                └────────┬────────┘
          actionable? ───┤ no ──────────────────────┐
                ┌────────▼────────┐                 │
                │ validation      │  (MCP, parallel)│
                └────────┬────────┘                 │
                ┌────────▼────────┐                 │
                │ api_selection   │  (decision)     │
                └────────┬────────┘                 │
                ┌────────▼────────┐                 │
                │ api_execution   │  (MCP, parallel)│
                └────────┬────────┘                 │
                ┌────────▼────────┐◄────────────────┘
                │ response_gen    │
                └────────┬────────┘
                ┌────────▼────────┐
                │ deliver         │  (outbox / SMTP)
                └────────┬────────┘
                ┌────────▼────────┐
                │ audit           │
                └─────────────────┘
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid

from langgraph.graph import END, START, StateGraph

from agents.api_executor import APIExecutor
from agents.api_selection_agent import APISelectionAgent
from agents.audit_agent import AuditAgent, audit
from agents.email_understanding_agent import EmailUnderstandingAgent
from agents.entity_agent import EntityExtractionAgent
from agents.intent_agent import IntentClassificationAgent
from agents.response_agent import ResponseGenerationAgent
from agents.sentiment_agent import SentimentAnalysisAgent
from agents.validation_agent import ValidationAgent
from config.settings import get_settings
from models.schemas import IncomingEmail, ProcessingResult, RequestStatus
from utils.email_io import deliver
from utils.llm import llm_enabled
from utils.mcp_client import BankingMCPClient
from workflows.state import WorkflowState

log = logging.getLogger("workflow")


def build_workflow(mcp: BankingMCPClient):
    understanding = EmailUnderstandingAgent()
    intent = IntentClassificationAgent()
    sentiment = SentimentAnalysisAgent()
    entities = EntityExtractionAgent()
    validation = ValidationAgent(mcp)
    selection = APISelectionAgent()
    executor = APIExecutor(mcp)
    responder = ResponseGenerationAgent()
    auditor = AuditAgent()

    async def deliver_node(state: WorkflowState) -> dict:
        files = deliver(state["response"], state["email"].email_id)
        entry = audit(state, "email_sender", "RESPONSE_DELIVERED",
                      {"to": state["response"].to, "mode": get_settings().send_mode, "files": files})
        return {"output_files": files, "trace": [entry]}

    def route_after_entities(state: WorkflowState) -> str:
        actionable = any(r.status == RequestStatus.PENDING for r in state["requests"])
        return "validation" if actionable else "response_generation"

    g = StateGraph(WorkflowState)
    g.add_node("email_understanding", understanding.run)
    g.add_node("intent_classification", intent.run)
    g.add_node("sentiment_analysis", sentiment.run)
    g.add_node("entity_extraction", entities.run)
    g.add_node("validation", validation.run)
    g.add_node("api_selection", selection.run)
    g.add_node("api_execution", executor.run)
    g.add_node("response_generation", responder.run)
    g.add_node("deliver", deliver_node)
    g.add_node("audit", auditor.run)

    g.add_edge(START, "email_understanding")
    g.add_edge("email_understanding", "intent_classification")   # fan-out:
    g.add_edge("email_understanding", "sentiment_analysis")      # both run in parallel
    g.add_edge(["intent_classification", "sentiment_analysis"], "entity_extraction")  # join
    g.add_conditional_edges("entity_extraction", route_after_entities,
                            {"validation": "validation", "response_generation": "response_generation"})
    g.add_edge("validation", "api_selection")
    g.add_edge("api_selection", "api_execution")
    g.add_edge("api_execution", "response_generation")
    g.add_edge("response_generation", "deliver")
    g.add_edge("deliver", "audit")
    g.add_edge("audit", END)
    return g.compile()


async def process_email(email: IncomingEmail, mcp: BankingMCPClient, graph=None) -> ProcessingResult:
    graph = graph or build_workflow(mcp)
    run_id = uuid.uuid4().hex
    started = time.time()
    state = await graph.ainvoke({"run_id": run_id, "started_at": started, "email": email,
                                 "tool_calls": [], "trace": [], "llm_agents": []})
    return ProcessingResult(
        email_id=email.email_id, run_id=run_id, sender=email.sender,
        language=state["understanding"].language, sentiment=state["sentiment"],
        requests=state["requests"], tool_calls=state.get("tool_calls", []), response=state["response"],
        escalate_to_human=state["sentiment"].escalate_to_human,
        llm_used=bool(state.get("llm_agents")) and llm_enabled(),
        duration_ms=int((time.time() - started) * 1000), output_files=state.get("output_files", {}),
    )


async def process_batch(emails: list[IncomingEmail]) -> list[ProcessingResult]:
    """Process many emails concurrently (bounded) over one shared MCP session."""
    sem = asyncio.Semaphore(get_settings().max_parallel_emails)
    async with BankingMCPClient() as mcp:
        graph = build_workflow(mcp)

        async def one(e: IncomingEmail):
            async with sem:
                try:
                    return await process_email(e, mcp, graph)
                except Exception:  # noqa: BLE001 - one bad email must not stop the batch
                    log.exception("Failed to process email %s", e.email_id)
                    return None

        results = await asyncio.gather(*(one(e) for e in emails))
    return [r for r in results if r is not None]
