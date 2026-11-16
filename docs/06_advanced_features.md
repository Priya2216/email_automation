# 06 - Optional / advanced features

All seven "candidates may additionally implement" items from the interview task are implemented.
Each section says **what it does, where the code is, how to see it working, and what is NOT covered**.

| # | Feature | Main code | Tests |
|---|---|---|---|
| 1 | OCR for attachments | `utils/attachments.py` | `tests/test_channels_ocr_api.py` |
| 2 | Multi-language (English / Hindi / Marathi) | `utils/nlp_rules.py`, `utils/i18n.py`, `utils/text_utils.py` | `tests/test_multilang.py` |
| 3 | Human-in-the-loop approval | `agents/human_review_agent.py`, `utils/review_service.py`, `utils/audit_db.py` | `tests/test_confidence_review.py` |
| 4 | RAG banking-policy lookup | `utils/rag.py`, `knowledge/*.md`, MCP tool `search_policy` | `tests/test_rag.py` |
| 5 | Omnichannel (email, chat, SMS, WhatsApp, voice) | `utils/channels.py`, `utils/speech.py`, `apis/orchestrator_api.py` | `tests/test_channels_ocr_api.py` |
| 6 | Confidence scoring | `utils/confidence.py`, `agents/confidence_agent.py` | `tests/test_confidence_review.py` |
| 7 | Real-time monitoring dashboard | `apis/static/dashboard.html`, `utils/audit_db.py` (`metrics`), `/metrics` | `tests/test_channels_ocr_api.py` |

Workflow now: `email_understanding -> (intent || sentiment) -> entities -> validation -> api_selection ->
api_execution -> response_generation -> confidence_scoring -> human_review_gate -> (deliver) -> audit`.

---
## 1. OCR for attachments
* Images (PNG/JPG/TIFF/...): grayscale + up-scaling + contrast, then Tesseract (`eng+hin+mar` - only the language
  packs that are installed are used).
* PDFs: text layer first (`pypdf`); a **scanned PDF** (almost no text) is rendered to images (`pdf2image` +
  poppler, PyMuPDF as a fallback) and OCR'd.
* OCR returns the **mean word confidence**. It is stored on the attachment, flows into the confidence score and,
  below `OCR_MIN_CONFIDENCE` (60), holds the reply for a human - a blurry photo must not be trusted for an account number.
* Demo: `python main.py --email sample_emails/inbox/11_image_attachment.eml --show-response`
* Not covered: handwriting, rotated/skewed pages, tables. Hindi/Marathi OCR needs `tesseract-ocr-hin/mar`
  (installed in the Dockerfile; on macOS `brew install tesseract-lang`).

## 2. Multi-language
* Understands **English, Hindi and Marathi** (Devanagari, Devanagari digits like `१२३४`, mixed language):
  intents, account/card numbers, statement periods (`एप्रिल २०२६`, `पिछले महीने`), transaction counts,
  sentiment/escalation words, clause splitting (`आणि`, `और`) and sign-offs.
* Replies are written **in the customer's language** from a message catalogue (`utils/i18n.py`). Numbers and
  facts still come from API data, so translation can never change a figure. `REPLY_LANGUAGE=en|hi|mr|auto`.
* The LLM prompts accept Hindi/Marathi too; the LLM path was NOT run against a live Llama here.
* Not covered: Latin-script Hindi ("mera balance batao") is detected but answered in English; the translations
  are a draft that a native speaker / compliance team must review; policy answers are English text unless the LLM
  paraphrases them (a localized lead-in is added).

## 3. Human-in-the-loop approval
The gate (`human_review_gate`) decides after the reply is drafted:

| Situation | Action | Queue item |
|---|---|---|
| Escalation (anger, fraud, legal threat) / confidence < threshold / low OCR | reply is **HELD**, not sent | `APPROVAL`, status `PENDING` |
| Unsupported or unanswered request ("forwarded to support") | reply is sent **and** a person is notified | `HANDOFF`, status `OPEN` |
| Everything else | sent automatically | - |

* Reviewer actions: `GET /v1/review-queue`, `GET /v1/review-queue/{id}`, `POST .../approve` (optionally with
  `edited_body`), `POST .../reject`, `POST .../resolve` (hand-offs) - or the dashboard buttons, or
  `python -m scripts.review_cli list|show|approve|reject|resolve`.
* **Approving is what sends the reply.** Every decision (who, when, note, edited or not) is written to the audit trail
  (`HUMAN_APPROVED`, `HUMAN_REJECTED`, `HANDOFF_RESOLVED`). The queue stores the customer message masked.
* `REVIEWER_API_KEY` protects the review endpoints with the `X-Reviewer-Key` header; settings:
  `HUMAN_REVIEW_ENABLED`, `REVIEW_ON_ESCALATION`, `REVIEW_ON_LOW_CONFIDENCE`.
* Not covered: no e-mail/Slack notification to reviewers, no SLA timers, reviewer identity is self-declared
  (use real SSO in production).

## 4. RAG policy lookup
* `knowledge/*.md` (9 sample **fictional** policy documents) are chunked by `##` section.
* Retrieval: **BM25** (pure Python, offline, understands Hindi/Marathi questions through a term map) or - with
  `RAG_EMBEDDING_MODEL=nomic-embed-text` / `bge-m3` and Ollama - **hybrid embeddings + BM25**.
* New intent `POLICY_QUERY` ("what are the late payment charges?"). It goes through the normal agents:
  API selection plans the MCP tool **`search_policy`**; the Response Agent answers from the passages and **cites the
  source**. With an LLM the answer is a paraphrase that is **rejected if it contains a number that is not in the
  passages**; otherwise the passage is quoted. No relevant passage -> hand-off to a person (never a guess).
* Try: `python main.py --text "What are the late payment charges on my credit card?" --show-response`
  or `GET /v1/policy/search?q=how to block a lost card`.
* Not covered: the policies are demo text; no document versioning / access control; embeddings were tested with a
  fake embedder only (no live Ollama embedding model here).

## 5. Omnichannel
| Channel | Endpoint | Reply |
|---|---|---|
| email | `/v1/process-email`, `/v1/process-eml`, `main.py` | formal email (+ PDF) |
| chat / WhatsApp | `/v1/process-message` | compact message |
| SMS | `/v1/process-message` | one paragraph, <= 480 chars |
| voice | `/v1/process-voice` (audio or `transcript`) | spoken style (`1,25,000 rupees`, `ending 7890`) |

* Chat/SMS/WhatsApp/voice customers are identified by **mobile number** (`get_customer_by_phone` MCP tool +
  `GetCustomerByPhoneAPI`). A statement PDF cannot travel by SMS/chat, so it is **emailed** to the registered address.
* Voice: speech-to-text with `faster-whisper` (`pip install -r requirements-voice.txt`); without it, send the
  transcript. Text-to-speech and the telephony / WhatsApp / SMS gateways are NOT included - the API returns `reply_text`
  for them to send. Replies are written to `outbox/<channel>/`.
* Not covered: no real Twilio/WhatsApp webhooks, no OTP/MPIN caller verification (identity = phone number only).

## 6. Confidence scoring
Per request: `0.60*intent + 0.25*entity + 0.15*execution`. Overall = `0.6*mean + 0.4*weakest`, `-0.05` for non-English
rule-based input. The rule engine now produces *evidence-based* intent confidence (keyword + number/period = high,
guessed from context = low) instead of a constant. A transactions request with no card word and no account word
(`0.55`) is the classic ambiguous case and lands in human review. Below `CONFIDENCE_THRESHOLD` (0.65) the reply is held.
The breakdown is returned in `confidence_breakdown`, audited (`CONFIDENCE_SCORED`) and shown on the dashboard.
* Not covered: the weights and the threshold are sensible defaults, **not calibrated on real labelled data**.

## 7. Real-time monitoring dashboard
* `http://127.0.0.1:8000/dashboard` - refreshes every 3 s: throughput per minute, outcomes, intents, channels,
  languages, sentiment, p50/p95 latency, average confidence, **MCP tool health (calls / failures / avg ms)**, recent
  runs, and the **review queue with approve / reject / resolve buttons**.
* `GET /v1/metrics` (JSON) and `GET /metrics` (Prometheus text) for Grafana; `GET /health` shows LLM, RAG, OCR, STT.
* Data comes from the `run_summary` table (one row per message), written by the audit agent.
* Not covered: polling instead of websockets; metrics are computed from the database (fine for demo volumes, use a
  time-series store at scale); no alerting rules.
