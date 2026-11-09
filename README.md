# Intelligent Email Processing & Automated Customer Response System (with MCP)

AI-powered automation for a bank's customer-support mailbox. It reads customer emails (and their PDF/image
attachments), detects **every** request and the customer's **sentiment**, extracts and **validates**
account/card details, decides which **core banking APIs** to call, calls them **in parallel through an MCP
server**, and sends **one professional, consolidated reply**. Every decision is **audited**, and sensitive data is **masked**.

**Stack:** Python · FastAPI · **Lingua** (language detection) · **MCP** (Model Context Protocol) ·
**LangGraph** (agent orchestration) · **Llama 3** via Ollama + LangChain · SQLAlchemy (SQLite/PostgreSQL) ·
IMAP/SMTP · pypdf / Tesseract OCR · Docker

| Document | Content |
|---|---|
| [docs/01_solution_architecture.md](docs/01_solution_architecture.md) | architecture, components, NFRs |
| [docs/02_low_level_design.md](docs/02_low_level_design.md) | modules, data model, agent I/O, error matrix |
| [docs/03_api_specifications.md](docs/03_api_specifications.md) | core banking API, MCP tools, orchestrator API |
| [docs/04_agent_workflow.md](docs/04_agent_workflow.md) | agents, graph, decision rules, scenario walk-through |
| [docs/05_assumptions_and_limitations.md](docs/05_assumptions_and_limitations.md) | assumptions, limitations, next steps |
| [docs/sample_outputs/](docs/sample_outputs/) | generated replies for the 5 task scenarios |

---

## 1. Quick start (MacBook + VS Code)

### Prerequisites
* Python **3.10+** (`python3 --version`)
* *(optional)* [Ollama](https://ollama.com/download) for Llama 3: install, then `ollama pull llama3`
* *(optional)* Tesseract for OCR of image attachments: `brew install tesseract`

Without Ollama or Tesseract the project still runs end-to-end. It uses the built-in rule-based NLP engine
and skips image OCR.

### Step 1: Open the project in VS Code
```bash
cd ~/Downloads/intelligent-email-automation-mcp
code .
```
(or **File → Open Folder…**). Install the **Python** extension if VS Code asks.

### Step 2: Create the virtual environment and install dependencies
Open the VS Code terminal (**Ctrl + `**):
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```
VS Code picks up `.venv` automatically (`.vscode/settings.json`). If it doesn't, use
**Cmd+Shift+P → Python: Select Interpreter → .venv**.

### Step 3: Run the demo
```bash
python main.py --show-response
```
This will:
1. start the mock core banking API on `127.0.0.1:8001` (automatically),
2. launch the MCP server and connect the agents to it,
3. process the 12 sample emails in `sample_emails/inbox/`,
4. write replies to `outbox/`, statements to `outbox/statements/`, and the audit trail to `logs/`.

Or run `./run_demo.sh`, which does steps 2 and 3 in one go.

### Step 4: Run the tests
```bash
python -m pytest -v        # NLP units, all task scenarios end-to-end, LLM path
```

### Step 5 (optional): Use Llama 3
```bash
# install Ollama from https://ollama.com/download, then:
ollama pull llama3
ollama serve              # if the Ollama app isn't already running
python main.py            # prints "LLM: Llama 3 via Ollama (llama3)"
```
`LLM_PROVIDER=rules python main.py` forces the rule engine; `LLM_PROVIDER=ollama` forces the LLM.

### Step 6 (optional): REST APIs and Swagger
```bash
uvicorn apis.orchestrator_api:app --port 8000
```
* http://127.0.0.1:8000/docs → **POST /v1/process-email** → *Try it out* → paste any customer email
* http://127.0.0.1:8001/docs → the mock core banking APIs

VS Code: **Run and Debug** (Cmd+Shift+D) has ready-made launch configurations for everything above.

### Docker
```bash
docker compose up --build
docker compose exec ollama ollama pull llama3     # first time only
```

---

## 2. Useful commands

| Command | What it does |
|---|---|
| `python main.py` | process all emails in `sample_emails/inbox` |
| `python main.py --email sample_emails/inbox/05_partial_validation_failure.eml --show-response` | one email, print the reply |
| `python main.py --text "Send my statement for last month" --sender john.doe@example.com --show-response` | ad-hoc email |
| `python main.py --source imap` | read unseen emails from a real mailbox (configure `.env`) |
| `CORE_API_FAILURE_RATE=0.3 python main.py` | chaos test: 30% of core API calls fail with 503 (retried) |
| `python -m scripts.generate_sample_emails` | regenerate the sample `.eml` files |
| `python -m mcp_server.banking_mcp_server` | run the MCP server standalone (stdio) |

Configuration: copy `config/.env.example` to `.env` in the project root and edit it.

---

## 3. How the project works, step by step

```
 Incoming Email ──► Email Reader ──► Email Understanding Agent
 (IMAP / .eml)      (downloads        (clean text, language, summary,
                     attachments)       PDF text / image OCR)
                                               │
                         ┌─────────────────────┴─────────────────────┐   in parallel
                 Intent Classification Agent              Sentiment Analysis Agent
                 (multi-intent)                           (+ human escalation flag)
                         └─────────────────────┬─────────────────────┘
                                   Entity Extraction Agent
                         (account, card, period, count, name)
                                               │
                                     Validation Agent ───► MCP ─► ValidateAccount/ValidateCard
                         (missing info, format, ownership)              (parallel)
                                               │
                                    API Selection Agent  (API Decision Engine)
                         (which APIs, sequence, parallel, de-dup)
                                               │
                                       API Executor ───► MCP ─► GetBalance / GetCardTxns / GetStatement
                                               │                 (parallel, timeout + retry)
                                  Response Generation Agent
                    (one email, partial success, clarifications, PDF statement)
                                               │
                              Email Sender (outbox / SMTP) ──► Audit / Logging Agent
```

**Step 1: Read the email.** `utils/email_io.py` parses the `.eml` (or fetches from IMAP), saves each
attachment to `outbox/downloaded_attachments/<email>/`, and extracts its text: PDF via `pypdf`, images
via Tesseract OCR (`utils/attachments.py`).

**Step 2: Understand it.** The *Email Understanding Agent* removes quoted replies and signatures, detects the
language of the cleaned email body with Lingua, and writes a one-line summary (masked). The `language` result
is an ISO 639-1 code when available (for example, `mr` for Marathi); otherwise it is `unknown`. Language
detection currently analyzes the email body, not extracted PDF or image attachment text.

**Step 3: Find every request.** The *Intent Classification Agent* splits the text into clauses ("… balance for
account X **and also** send last 3 transactions …") and classifies each one as `BALANCE_ENQUIRY`,
`CARD_TRANSACTIONS`, `STATEMENT_REQUEST` or `UNSUPPORTED`. With Llama 3, the LLM does this, and its output is
validated. If the body has no request, the attachment text is checked (e.g. a request form).

**Step 4: Detect sentiment** (in parallel with step 3). POSITIVE / NEUTRAL / NEGATIVE / URGENT, a score, and
an `escalate_to_human` flag for angry, escalation-toned or fraud-related mails.

**Step 5: Extract entities.** The *Entity Extraction Agent* finds account numbers, card numbers (full, masked
`4567XXXX8901`, or 9-digit reference), statement periods ("April 2026", "01/08/2026 to 31/08/2026",
"last month") and transaction counts ("last 5", "three"). It looks in the request clause first, then the
rest of the email, then the attachments. Numbers proposed by the LLM must literally appear in the email.

**Step 6: Validate.** The *Validation Agent* looks up the sender's customer profile, fills a missing account
with the customer's primary account, flags missing details (card number, period) for clarification, checks
formats, then validates all numbers **in parallel** via MCP. It also checks that each account/card
**belongs to the sender**.

**Step 7: Decide which APIs to call.** The *API Selection Agent* maps each valid request to an MCP tool,
removes duplicate calls, and puts independent calls in the same parallel wave.

**Step 8: Call the core system.** The *API Executor* runs the plan through the **MCP server**, which calls the
FastAPI mock core banking API with a 5 s timeout and retries. A failure affects only its own request
(partial success).

**Step 9: Write the reply.** The *Response Generation Agent* builds one email with a section per request:
balance, transaction table, statement (PDF attached), "could not be validated, kindly recheck",
"please send us …" (clarification), "temporary system issue" (API error), or "forwarded to our team"
(unsupported). Numbers are masked (XXXX7890). The LLM may only write the empathetic opening line.

**Step 10: Send and audit.** The reply is written to `outbox/` (or sent by SMTP). The *Audit Agent* stores
every decision and API call (masked) in `logs/audit.db` and `logs/runs/<email>.json`.

---

## 4. Sample emails and expected results

| File | Scenario | Result |
|---|---|---|
| 01_balance_enquiry | Task scenario 1 | balance for XXXX7890 |
| 02_card_transactions | Task scenario 2 (masked card) | last 5 transactions |
| 03_statement_request | Task scenario 3 (no account given) | April 2026 statement for the primary account, PDF attached |
| 04_multiple_requests | Task scenario 4 | balance + last 3 card transactions in one email |
| 05_partial_validation_failure | Task scenario 5 | balance + "card could not be validated, kindly recheck" |
| 06_pdf_attachment | request form in a PDF | balance + September statement from the PDF's account |
| 07_angry_urgent_customer | negative + urgent + fraud words | apology, transactions, **escalated to a human** |
| 08_missing_details | no card number, no period | clarification request |
| 09_unsupported_request | cheque book + balance | balance + "forwarded to support" |
| 10_core_system_timeout | core API timeout | one balance OK + one "temporary system issue" |
| 11_image_attachment | request in a PNG | read via OCR (needs Tesseract), otherwise asks for the period |
| 12_multiple_accounts_one_closed | 2 accounts, 1 closed | one balance + one "could not be validated" |

Example reply (scenario 5):
```
Dear John Doe,

Thank you for contacting us. We have processed your request. Please find the details below.

Account Balance:
  • Account Number: XXXX7890
  • Account Type: Savings
  • Available Balance: INR 1,25,000.00
  ...
Credit Card Request:
  The credit card number provided (XXXX XXXX XXXX 9999) could not be validated. Kindly recheck the card
  details and resend your request.

Thank you,
Customer Support Team
```

---

## 5. Where to look in the code

| Requirement in the task | File |
|---|---|
| Email processing, attachments download | `utils/email_io.py`, `utils/attachments.py` |
| Intent detection (multi-intent) | `agents/intent_agent.py`, `utils/nlp_rules.py`, `prompts/intent_classification.txt` |
| Sentiment analysis | `agents/sentiment_agent.py` |
| Entity extraction | `agents/entity_agent.py` |
| Validation | `agents/validation_agent.py` |
| API decision engine | `agents/api_selection_agent.py`, `agents/api_executor.py` |
| Mock core APIs (5 APIs) | `apis/core_banking_api.py`, `apis/core_banking_service.py` |
| MCP integration | `mcp_server/banking_mcp_server.py`, `utils/mcp_client.py` |
| LLM (Llama 3) integration | `utils/llm.py`, `prompts/` |
| Agent orchestration | `workflows/email_workflow.py` (LangGraph) |
| Response generation | `agents/response_agent.py` |
| Logging / audit / masking | `utils/logger.py`, `utils/audit_db.py`, `agents/audit_agent.py`, `utils/masking.py` |
| Configuration | `config/settings.py`, `config/.env.example` |
| Tests / sample emails | `tests/`, `sample_emails/inbox/` |
