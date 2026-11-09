"""LangGraph state passed between agents."""
import operator
from typing import Annotated, Any, TypedDict

from models.schemas import (CustomerRequest, EmailUnderstanding, IncomingEmail, PlannedCall,
                            ResponseEmail, SentimentResult, ToolCallRecord)


class WorkflowState(TypedDict, total=False):
    run_id: str
    started_at: float
    email: IncomingEmail
    understanding: EmailUnderstanding
    sentiment: SentimentResult
    requests: list[CustomerRequest]
    customer_profile: dict[str, Any]
    plan: list[PlannedCall]
    response: ResponseEmail
    output_files: dict[str, str]
    # list fields are appended to by several (possibly parallel) agents
    tool_calls: Annotated[list[ToolCallRecord], operator.add]
    trace: Annotated[list[dict], operator.add]
    llm_agents: Annotated[list[str], operator.add]
