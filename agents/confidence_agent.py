"""Confidence Scoring Agent - scores the drafted reply (see utils/confidence.py) and records the breakdown."""
from __future__ import annotations

from agents.audit_agent import audit
from config.settings import get_settings
from utils.confidence import score_requests
from workflows.state import WorkflowState


class ConfidenceScoringAgent:
    name = "confidence_scoring_agent"

    async def run(self, state: WorkflowState) -> dict:
        understanding = state["understanding"]
        result = score_requests(state["requests"], state.get("reply_language") or understanding.language,
                                understanding.ocr_confidence)
        result["threshold"] = get_settings().confidence_threshold
        result["ocr_confidence"] = understanding.ocr_confidence
        entry = audit(state, self.name, "CONFIDENCE_SCORED", result)
        return {"confidence": result, "trace": [entry]}
