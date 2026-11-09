"""
Sentiment Analysis Agent - POSITIVE / NEUTRAL / NEGATIVE / URGENT, plus a
human-in-the-loop escalation flag for angry or escalation-toned emails.
Runs in parallel with the Intent Classification Agent.
"""
from __future__ import annotations

from agents.audit_agent import audit
from models.schemas import SentimentLabel, SentimentResult
from utils.llm import ask_json
from utils.nlp_rules import analyse_sentiment
from utils.text_utils import strip_sign_off
from workflows.state import WorkflowState


class SentimentAnalysisAgent:
    name = "sentiment_analysis_agent"

    async def run(self, state: WorkflowState) -> dict:
        # sign-offs ("Thanks, John") are politeness, not sentiment
        text = strip_sign_off(state["understanding"].clean_text)
        rules = analyse_sentiment(text)

        result, llm_agents = None, []
        reply = await ask_json("sentiment_analysis", email_text=text[:4000])
        if isinstance(reply, dict):
            try:
                result = SentimentResult(
                    label=SentimentLabel(str(reply.get("label", "NEUTRAL")).upper()),
                    score=float(reply.get("score", 0.0)),
                    is_urgent=bool(reply.get("is_urgent")) or rules["is_urgent"],
                    # never let the LLM *remove* an escalation the rules found
                    escalate_to_human=bool(reply.get("escalate_to_human")) or rules["escalate_to_human"],
                    reasons=[str(r) for r in reply.get("reasons", [])][:8],
                    method="llm",
                )
                llm_agents.append(self.name)
            except (ValueError, TypeError):
                result = None
        if result is None:
            result = SentimentResult(**rules, method="rules")

        entry = audit(state, self.name, "SENTIMENT_DETECTED", result.model_dump(mode="json"))
        return {"sentiment": result, "trace": [entry], "llm_agents": llm_agents}
