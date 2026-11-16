"""RAG policy lookup: retrieval quality, grounded answers, MCP tool, optional embedding retriever."""
import hashlib
import re

import pytest
from conftest import make_email, run
from config.settings import get_settings
from models.schemas import IntentType, RequestStatus
from utils.nlp_rules import detect_intents
from utils.rag import PolicyIndex, get_index, load_chunks, search_policy


@pytest.mark.parametrize("question, heading", [
    ("What are the late payment charges on my credit card?", "Late payment charges"),
    ("What is the minimum balance for a savings account?", "Minimum average balance"),
    ("How do I block my lost card?", "How to block a lost or stolen card"),
    ("What are the documents required for KYC?", "KYC documents required"),
    ("What is the interest rate on a fixed deposit?", "Fixed deposit interest rate"),
    ("क्रेडिट कार्ड हरवले तर ते कसे ब्लॉक करायचे?", "How to block a lost or stolen card"),   # Marathi
    ("कार्ड की वार्षिक फी क्या है?", "Annual fee"),                                          # Hindi
])
def test_retrieval_finds_the_right_passage(question, heading):
    hits = search_policy(question)["hits"]
    assert hits and hits[0]["heading"] == heading and hits[0]["source"].endswith(".md")


@pytest.mark.parametrize("question", ["What is the weather in Mumbai today?",
                                      "What is the policy on cryptocurrency charges?"])
def test_out_of_scope_questions_find_nothing(question):
    assert search_policy(question)["hits"] == []


def test_knowledge_base_is_chunked_by_section():
    chunks = load_chunks(get_settings().kb_dir)
    assert len(chunks) >= 30 and all(c.heading and c.text for c in chunks)


def test_embedding_retriever_path_with_injected_model():
    """The embedding path is exercised with a deterministic fake embedder (no Ollama needed)."""
    def fake_embed(texts):
        out = []
        for t in texts:
            v = [0.0] * 4096
            for w in re.findall(r"[a-z]+", t.lower()):
                v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 4096] += 1.0
            out.append(v)
        return out

    index = PolicyIndex(load_chunks(get_settings().kb_dir), embed_fn=fake_embed)
    assert index.method == "embeddings"
    hits = index.search("late payment fee credit card", 3, 0.0)
    assert "Late payment charges" in [h["heading"] for h in hits]   # hybrid (embedding + BM25) ranking
    assert all(0.0 <= h["score"] <= 1.0 for h in hits)


def test_policy_questions_are_a_separate_intent():
    assert detect_intents("What are the annual fees on the platinum card?")[0].intent == IntentType.POLICY_QUERY
    assert detect_intents("What is my balance for account 1234567890?")[0].intent == IntentType.BALANCE_ENQUIRY
    # an ACTION is not a question -> still unsupported / human
    assert detect_intents("I need a new cheque book for account 1234567890.")[0].intent == IntentType.UNSUPPORTED


async def test_policy_answer_is_grounded_and_cited():
    r = await run(make_email("What are the late payment charges on my credit card?"))
    req = r.requests[0]
    assert req.intent == IntentType.POLICY_QUERY and req.status == RequestStatus.SUCCESS
    assert any(c.tool == "search_policy" for c in r.tool_calls)       # reached through MCP
    assert "INR 500" in r.response.body and "Source: Credit Card Fees and Charges" in r.response.body
    assert not r.needs_human_review


async def test_policy_and_account_data_in_one_email():
    r = await run(make_email("What is the annual fee of my card? Also share my balance for account 1234567890."))
    assert [q.intent for q in r.requests] == [IntentType.POLICY_QUERY, IntentType.BALANCE_ENQUIRY]
    assert "INR 999" in r.response.body and "INR 1,25,000.00" in r.response.body


async def test_unanswerable_policy_question_is_handed_to_a_person():
    r = await run(make_email("What is the policy on cryptocurrency charges?"))
    assert r.requests[0].status == RequestStatus.UNSUPPORTED
    assert "forwarded to our customer support team" in r.response.body
    assert r.review_status in ("HANDOFF_OPEN", "PENDING")


def test_index_is_cached():
    assert get_index() is get_index()
