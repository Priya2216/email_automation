"""
Tests of the LLM (Llama 3) code path without needing Ollama: a fake chat
model answers each agent's prompt. Verifies that LLM output is used, and
that hallucinated numbers / invalid output are rejected safely.
"""
import json
from types import SimpleNamespace

import pytest

from conftest import make_email, run
from models.schemas import IntentType, RequestStatus
from utils import llm as llm_module


class FakeLlama:
    """Answers based on which agent prompt it receives."""

    def __init__(self, answers: dict):
        self.answers = answers

    async def ainvoke(self, messages):
        system = messages[0].content
        for marker, answer in self.answers.items():
            if marker in system:
                return SimpleNamespace(content=json.dumps(answer))
        return SimpleNamespace(content="not json")


@pytest.fixture
def fake_llm(monkeypatch):
    def install(answers):
        monkeypatch.setattr(llm_module, "get_llm", lambda: FakeLlama(answers))
        monkeypatch.setattr(llm_module, "llm_enabled", lambda: True)
    return install


async def test_llm_results_are_used(fake_llm):
    fake_llm({
        "Email Understanding Agent": {"summary": "Customer wants their balance."},
        "Intent Classification Agent": {"requests": [
            {"intent": "BALANCE_ENQUIRY", "text_span": "how much money is in account 1234567890",
             "confidence": 0.97}]},
        "Sentiment Analysis Agent": {"label": "POSITIVE", "score": 0.6, "is_urgent": False,
                                     "escalate_to_human": False, "reasons": ["thanks"]},
        "Entity Extraction Agent": {"account_number": "1234567890"},
        "Response Generation Agent": {"opening": "Thank you for writing to us, we are happy to help."},
    })
    r = await run(make_email("Hi, how much money is in account 1234567890? Thanks a lot!"))
    assert r.requests[0].method == "llm" and r.requests[0].confidence == 0.97
    assert r.sentiment.method == "llm" and r.sentiment.label.value == "POSITIVE"
    assert r.requests[0].status == RequestStatus.SUCCESS
    assert "we are happy to help" in r.response.body


async def test_hallucinated_number_is_rejected(fake_llm):
    fake_llm({
        "Intent Classification Agent": {"requests": [
            {"intent": "BALANCE_ENQUIRY", "text_span": "what is my balance", "confidence": 0.9}]},
        # LLM invents an account number that is NOT in the email
        "Entity Extraction Agent": {"account_number": "3456789012"},
        # LLM tries to put a number in the opening -> must be ignored
        "Response Generation Agent": {"opening": "Your balance is 99,99,999."},
    })
    r = await run(make_email("Hello, what is my balance?"))
    req = r.requests[0]
    assert req.account_number == "1234567890"           # sender's own primary account
    assert req.entity_sources["account_number"] == "profile"
    assert "99,99,999" not in r.response.body


async def test_invalid_llm_output_falls_back_to_rules(fake_llm):
    fake_llm({"Intent Classification Agent": {"requests": [{"intent": "TRANSFER_MONEY", "text_span": "x"}]}})
    r = await run(make_email("Please send my balance for account 1234567890."))
    # unknown intent dropped -> nothing usable from the LLM -> rule engine takes over
    assert [(q.intent, q.method, q.status) for q in r.requests] == \
        [(IntentType.BALANCE_ENQUIRY, "rules", RequestStatus.SUCCESS)]
