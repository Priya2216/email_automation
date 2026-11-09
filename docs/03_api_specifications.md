# 3. API Specifications

Three interfaces exist:

1. **Mock Core Banking REST API** (FastAPI, port 8001) – the simulated core system.
2. **Core Banking MCP Server** (stdio) – the same operations as MCP tools; used by the agents.
3. **Orchestrator REST API** (FastAPI, port 8000) – submit an email, get the processing result.

Interactive Swagger docs: `http://127.0.0.1:8001/docs` and `http://127.0.0.1:8000/docs`.

---

## 3.1 Mock Core Banking REST API

Base URL: `http://127.0.0.1:8001/api/v1` — all endpoints are `POST` with a JSON body.
Errors use one format: `{"error": {"code": "...", "message": "..."}}`.

### ValidateAccountAPI
`POST /ValidateAccountAPI`
```json
// request
{ "account_number": "1234567890" }
// 200 response (valid)
{ "status": "VALID", "customer_name": "John Doe", "customer_id": "CUST001", "account_type": "Savings" }
// 200 response (invalid)
{ "status": "INVALID", "reason": "Account not found" }
```

### ValidateCardAPI
`POST /ValidateCardAPI` — accepts a full 16-digit number, a masked number (`4567XXXX8901`) or the 9-digit card reference.
```json
{ "card_number": "4567XXXX8901" }
{ "status": "VALID", "customer_name": "John Doe", "customer_id": "CUST001", "card_type": "Credit", "card_last4": "8901" }
```

### GetAccountBalanceAPI
`POST /GetAccountBalanceAPI`
```json
{ "account_number": "1234567890" }
{ "account_number": "1234567890", "account_type": "Savings", "currency": "INR",
  "available_balance": 125000.0, "ledger_balance": 127450.0, "as_of": "2026-11-04T10:15:02" }
```
Errors: `404 INVALID_ACCOUNT`.

### GetCardTransactionsAPI
`POST /GetCardTransactionsAPI`
```json
{ "card_number": "987654321", "count": 3 }
{ "card_last4": "1098", "card_type": "Credit", "network": "Mastercard",
  "credit_limit": 100000.0, "available_limit": 86400.0,
  "requested_count": 3, "returned_count": 3,
  "transactions": [ { "date": "2026-10-30", "description": "Uber India", "category": "Travel",
                      "amount": 386.0, "type": "DEBIT" } ] }
```
`count`: 1–50. Errors: `404 INVALID_CARD`.

### GetStatementAPI
`POST /GetStatementAPI` — generates a PDF statement.
```json
{ "account_number": "1234567890", "period_start": "2026-04-01", "period_end": "2026-04-30" }
{ "statement_id": "STMT-7890-20260401-3F9A1C", "account_number": "1234567890",
  "period_start": "2026-04-01", "period_end": "2026-04-30",
  "opening_balance": 102345.5, "closing_balance": 118020.25, "transaction_count": 12,
  "currency": "INR", "document_path": "outbox/statements/STMT-7890-20260401-3F9A1C.pdf" }
```
Errors: `404 INVALID_ACCOUNT`, `400 INVALID_PERIOD` (end before start, > 12 months, future).

### GetCustomerByEmailAPI
`POST /GetCustomerByEmailAPI`
```json
{ "email": "john.doe@example.com" }
{ "status": "FOUND", "customer_id": "CUST001", "name": "John Doe",
  "email": "john.doe@example.com", "primary_account": "1234567890" }
```

### Health
`GET /health` → `{"status": "UP", "service": "mock-core-banking"}`

### Simulation / chaos hooks
| Trigger | Effect |
|---|---|
| account `5555555555` | sleeps longer than the client timeout → **timeout** |
| account `6666666666` | HTTP 500 `INTERNAL_ERROR` |
| `CORE_API_FAILURE_RATE=0.3` | 30 % of calls return HTTP 503 (retried by the MCP server) |

### Test data
| Customer | Email | Accounts | Cards |
|---|---|---|---|
| John Doe (CUST001) | john.doe@example.com | 1234567890 (primary), 1111222233 (closed), 5555555555, 6666666666 | 4567123456788901 / ref 456712345, 5243987654321098 / ref 987654321, 4000123412341234 (blocked) |
| Priya Sharma (CUST002) | priya.sharma@example.com | 2345678901 | 4111222233334444 |
| Rahul Verma (CUST003) | rahul.verma@example.com | 3456789012 | – |

---

## 3.2 Core Banking MCP Server (`mcp_server/banking_mcp_server.py`)

Transport: stdio (JSON-RPC 2.0). Server name: `core-banking`.

| Tool | Arguments | Wraps |
|---|---|---|
| `validate_account` | `account_number: str` | ValidateAccountAPI |
| `validate_card` | `card_number: str` | ValidateCardAPI |
| `get_account_balance` | `account_number: str` | GetAccountBalanceAPI |
| `get_card_transactions` | `card_number: str, count: int = 5` | GetCardTransactionsAPI |
| `get_statement` | `account_number: str, period_start: str, period_end: str` | GetStatementAPI |
| `get_customer_by_email` | `email: str` | GetCustomerByEmailAPI |

Every tool returns the envelope:
```json
{ "ok": true,  "data": { ... API response ... } }
{ "ok": false, "error": { "code": "TIMEOUT | SERVICE_UNAVAILABLE | INVALID_ACCOUNT | INVALID_CARD | INVALID_PERIOD | INTERNAL_ERROR | UNEXPECTED",
                          "message": "...", "retryable": true } }
```
Resilience: HTTP timeout `CORE_API_TIMEOUT_SECONDS` (5 s), retries `CORE_API_MAX_RETRIES` (2) with
exponential back-off on 502/503/504 and connection errors.

Use from any MCP client, e.g. Claude Desktop `claude_desktop_config.json`:
```json
{ "mcpServers": { "core-banking": {
    "command": "/path/to/project/.venv/bin/python",
    "args": ["-m", "mcp_server.banking_mcp_server"],
    "cwd": "/path/to/project" } } }
```

---

## 3.3 Orchestrator REST API

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/health` | – | status + list of MCP tools |
| POST | `/v1/process-email` | `{"sender", "subject", "body"}` | `ProcessingResult` |
| POST | `/v1/process-eml` | multipart file (`.eml`, with attachments) | `ProcessingResult` |
| GET | `/v1/audit?run_id=&limit=` | – | audit events (masked) |

`ProcessingResult`:
```json
{
  "email_id": "api-20261104101500123456", "run_id": "8f1c…", "sender": "john.doe@example.com",
  "language": "en",
  "sentiment": { "label": "NEUTRAL", "score": 0.0, "is_urgent": false, "escalate_to_human": false, "reasons": [], "method": "rules" },
  "requests": [
    { "request_id": "R1", "intent": "BALANCE_ENQUIRY", "status": "SUCCESS", "account_number": "1234567890", "...": "..." },
    { "request_id": "R2", "intent": "CARD_TRANSACTIONS", "status": "INVALID", "card_number": "999999999", "validation_notes": ["Card not found"] }
  ],
  "tool_calls": [ { "step_id": "VA1", "tool": "validate_account", "ok": true, "duration_ms": 41 } ],
  "response": { "to": "john.doe@example.com", "subject": "Re: …", "body": "Dear John Doe, …", "attachments": [] },
  "escalate_to_human": false, "llm_used": false, "duration_ms": 812,
  "output_files": { "eml": "outbox/…_response.eml", "txt": "outbox/…_response.txt", "audit_json": "logs/runs/….json" }
}
```

`language` is detected by Lingua from the cleaned email body and is returned as an ISO 639-1 code when
available (for example, `mr` for Marathi); otherwise it is `unknown`. Extracted PDF/image attachment text is
available to other workflow steps, but is not included in this language-detection input.
