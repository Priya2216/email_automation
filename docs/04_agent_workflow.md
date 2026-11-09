# 4. Agent Workflow

## 4.1 The agents

| # | Agent | Responsibility (task §7) | LLM use | Fallback |
|---|---|---|---|---|
| 1 | Email Understanding Agent | Detect the cleaned email body's language with Lingua; summarise the email and read downloaded attachments | 1-line summary | first sentence |
| 2 | Intent Classification Agent | Identify every customer request (multi-intent) | split + classify | clause rules |
| 3 | Sentiment Analysis Agent | Neutral / Positive / Negative / Urgent + escalation | label + reasons | lexicon |
| 4 | Entity Extraction Agent | Account, card, date range, count, name | per-request JSON | regex + context |
| 5 | Validation Agent | Completeness, format, backend validation, ownership | – | – |
| 6 | API Selection Agent | Which APIs, sequence, parallelism, de-duplication | – | – |
| – | API Executor | Execute the plan through MCP (parallel) | – | – |
| 7 | Response Generation Agent | Consolidated professional reply | opening sentence only | templates |
| 8 | Audit / Logging Agent | Store execution logs | – | – |

The **Agent Orchestrator** is the LangGraph graph in `workflows/email_workflow.py`; agents communicate only
through the shared, typed `WorkflowState`.

## 4.2 Walk-through: Scenario 5 (partial validation failure)

> *"Please provide balance for account 1234567890 and card transactions for card 999999999."*
> from `john.doe@example.com`

| Step | Agent | Decision (from the audit trail) |
|---|---|---|
| 1 | Email Understanding | Lingua detects language `en` from the cleaned email body; summary "Please provide balance for account XXXX7890 and card transactions for card XXXX9999." |
| 2a | Intent Classification | clause split on "and" → `R1 BALANCE_ENQUIRY` (0.9), `R2 CARD_TRANSACTIONS` (0.9); `multi_intent: true` |
| 2b | Sentiment (parallel) | `NEUTRAL`, score 0.0, no escalation |
| 3 | Entity Extraction | R1 → account `1234567890` (source body); R2 → card `999999999` (source body), count 5 (default) |
| 4 | Validation | `get_customer_by_email` → CUST001; `validate_account(1234567890)` ‖ `validate_card(999999999)` in parallel → account VALID & owned by sender, card **INVALID "Card not found"** |
| 5 | API Selection | R1 → `get_account_balance` (step S1, wave 1); R2 → skip (INVALID) |
| 6 | API Executor | S1 ok in ~40 ms → R1 SUCCESS |
| 7 | Response Generation | balance section + "The credit card number provided (XXXX XXXX XXXX 9999) could not be validated. Kindly recheck the card details and resend your request." → `PARTIAL_SUCCESS` |
| 8 | Deliver + Audit | `outbox/…_response.eml`, `logs/runs/….json`, 9 events in `audit_log` |

## 4.3 Decision rules worth knowing

**Multi-intent splitting.** Sentences are split into clauses on *and / also / as well as / plus / ;*.
A clause with no intent keyword but with an entity (e.g. "and 2345678901", "February 2026") is merged into
the previous request; repeated mentions of the same intent without new numbers refine the earlier request
instead of creating a duplicate. One clause with two account numbers becomes two requests (`R1.1`, `R1.2`).

**Card vs. account.** A number is a *card* if it is masked (`4567XXXX8901`), 16 digits, or the nearest
preceding keyword is "card"; an *account* if the nearest keyword is "account/a/c/savings/current";
otherwise by length (10–12 → account, 9 → card reference).

**Where an entity can come from.** clause → rest of the body (only if unambiguous) → attachment text →
(account only) the sender's registered primary account. The source is recorded in `entity_sources` and shown
in the reply ("your registered primary account").

**Transactions without "card".** "account transactions for April" is treated as a statement request.

**Escalation (human-in-the-loop).** Set when the email contains escalation language (ombudsman, legal,
social media, fraud/unauthorised…), negative + urgent tone, or strong negativity. The reply tells the
customer a senior officer will call, and the result carries `escalate_to_human: true` so a queue/dashboard
can route it to an agent for review before or after sending.

**Grounded responses.** Facts are rendered from API data by templates. The LLM may only write the opening
sentence and that text is dropped if it contains any digit.

## 4.4 Adding a new intent (example: "credit card bill due date")
1. `models/schemas.py` – add `CARD_DUE_DATE` to `IntentType`.
2. `utils/nlp_rules.py` – keyword rule in `classify_clause`; `prompts/intent_classification.txt` – one line.
3. `apis/core_banking_api.py` + `mcp_server/banking_mcp_server.py` – endpoint + `get_card_due_date` tool.
4. `agents/api_selection_agent.py` – one entry in `INTENT_TO_TOOL`.
5. `agents/response_agent.py` – one template section; add a test in `tests/test_scenarios.py`.
