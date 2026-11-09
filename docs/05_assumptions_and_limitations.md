# 5. Assumptions and Limitations

## 5.1 Assumptions (aligned with task §14)
1. Core banking APIs are **mocked**: a FastAPI service over a JSON data file. Statements are generated
   deterministically (same account + period → same transactions) and rendered as PDF.
2. **Authentication is simplified**: the sender's email address identifies the customer. A request is only
   answered for accounts/cards owned by that customer (ownership check). Real deployments would add
   SPF/DKIM/DMARC verification and possibly OTP/secure-portal delivery for sensitive data.
3. **Email server integration is simulated** by default: `.eml` files in `sample_emails/inbox` in,
   `.eml`/`.txt` files in `outbox/` out. Real IMAP/SMTP is implemented and enabled through `.env`.
4. **No real banking data** is used; all customers, accounts and cards are fictitious.
5. Currency is INR, dates in emails are interpreted as Indian **DD/MM/YYYY**.
6. A month without a year means the most recent such month (e.g. in November 2026, "April" → April 2026).
7. When no transaction count is given, the **last 5** card transactions are returned (configurable, capped at 20).
8. A balance/statement request without an account number refers to the customer's **primary account**.
9. The 9-digit card numbers in the task (`987654321`, `999999999`) are treated as **card reference numbers**;
   `987654321` exists in the mock data, `999999999` does not (scenario 5).

## 5.2 Limitations
| Area | Limitation | Possible improvement |
|---|---|---|
| LLM | Llama 3 needs Ollama (≈ 4.7 GB model, best with ≥ 16 GB RAM). Without it the rule engine is used — accurate for the defined intents and phrasings, but less flexible for unusual wording. | Run Ollama on a GPU server; fine-tune a small model (SLM) on labelled bank emails. |
| OCR | Image attachments need the Tesseract binary (`brew install tesseract`). Without it, image text is skipped (logged) and the email is still processed. Scanned PDFs (no text layer) are not OCR-ed. | Add `pdf2image` + Tesseract, or a vision LLM (e.g. Llama 3.2 Vision). |
| Language | Lingua detects the cleaned email body's language and returns an ISO 639-1 code when available (for example, `mr` for Marathi); extracted attachment text is not part of language detection. A detected language does not mean every downstream component understands that language: the rule-based intent and sentiment logic is English-centric. | Include attachment text in language detection if needed; add multilingual intent/entity/sentiment handling or translation before processing. |
| Intents | Only balance, card transactions, statements; others are forwarded as UNSUPPORTED. | See docs/04 §4.4 to add intents. |
| Security | The orchestrator REST API returns unmasked numbers in `requests[]` (internal use only) and has no authentication. | Add OAuth2/API keys; mask in the API response; run inside the bank network. |
| Statement delivery | The PDF is attached in clear. | Password-protect the PDF (e.g. DOB-based) or send a secure download link. |
| Scale | Single process; the in-memory concurrency limit is per instance. | Queue (Kafka/SQS/RabbitMQ) + multiple workers; PostgreSQL for audit; Redis for idempotency. |
| Idempotency | Re-processing the same `.eml` sends a second reply. | Track processed `Message-ID`s in the database. |
| MCP transport | stdio (server is a local subprocess). | Use MCP Streamable HTTP to run the server as a separate service. |
