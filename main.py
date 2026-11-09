"""
Intelligent Email Processing & Automated Customer Response System (with MCP)

Usage:
    python main.py                         # process every .eml in sample_emails/inbox
    python main.py --email sample_emails/inbox/04_multiple_requests.eml
    python main.py --text "Please share my balance for account 1234567890" \
                   --sender john.doe@example.com
    python main.py --source imap           # read UNSEEN mails from a real mailbox (.env)
    python main.py --show-response         # also print every generated reply
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

from config.settings import get_settings
from models.schemas import IncomingEmail, ProcessingResult, RequestStatus
from utils.core_api_runner import ensure_core_api_running
from utils.email_io import parse_email_bytes, read_imap_unseen, read_inbox_folder
from utils.llm import llm_enabled
from utils.logger import setup_logging
from workflows.email_workflow import process_batch

STATUS_ICON = {RequestStatus.SUCCESS: "OK ", RequestStatus.INVALID: "INV", RequestStatus.MISSING_INFO: "ASK",
               RequestStatus.API_ERROR: "ERR", RequestStatus.UNSUPPORTED: "FWD", RequestStatus.PENDING: "..."}


def load_emails(args) -> list[IncomingEmail]:
    if args.text:
        return [IncomingEmail(email_id=f"adhoc-{datetime.now():%Y%m%d%H%M%S}", sender=args.sender,
                              subject=args.subject, body=args.text)]
    if args.email:
        path = Path(args.email)
        return [parse_email_bytes(path.read_bytes(), path.stem)]
    if args.source == "imap":
        return read_imap_unseen()
    return read_inbox_folder()


def print_result(r: ProcessingResult, show_response: bool) -> None:
    flag = "  !! ESCALATED TO HUMAN" if r.escalate_to_human else ""
    print(f"\n=== {r.email_id}  ({r.duration_ms} ms){flag}")
    print(f"    sentiment : {r.sentiment.label.value} (score {r.sentiment.score}, via {r.sentiment.method})"
          f"   language: {r.language}")
    for req in r.requests:
        detail = req.error or "; ".join(req.validation_notes[-1:]) or ""
        print(f"    [{STATUS_ICON[req.status]}] {req.request_id:<5} {req.intent.value:<18} "
              f"{req.status.value:<12} {detail}")
    print(f"    MCP calls : {len(r.tool_calls)}   reply -> {r.output_files.get('txt', '')}")
    if show_response:
        print("    " + "-" * 70)
        print("\n".join("    " + line for line in r.response.body.splitlines()))


def main() -> int:
    parser = argparse.ArgumentParser(description="Banking email automation (Agentic AI + MCP)")
    parser.add_argument("--email", help="process a single .eml file")
    parser.add_argument("--text", help="process an ad-hoc email body")
    parser.add_argument("--sender", default="john.doe@example.com", help="sender for --text")
    parser.add_argument("--subject", default="Customer enquiry", help="subject for --text")
    parser.add_argument("--source", choices=["folder", "imap"], default=None)
    parser.add_argument("--show-response", action="store_true", help="print the generated replies")
    args = parser.parse_args()
    args.source = args.source or get_settings().email_source

    setup_logging()
    if not ensure_core_api_running():
        print("ERROR: mock core banking API could not be started (see logs/core_api.log)", file=sys.stderr)
        return 1

    emails = load_emails(args)
    if not emails:
        print("No emails to process.")
        return 0
    print(f"LLM: {'Llama 3 via Ollama (' + get_settings().ollama_model + ')' if llm_enabled() else 'rule-based NLP (Ollama not available)'}")
    print(f"Processing {len(emails)} email(s)...")

    results = asyncio.run(process_batch(emails))
    for r in sorted(results, key=lambda x: x.email_id):
        print_result(r, args.show_response)

    print(f"\nDone: {len(results)}/{len(emails)} processed. Replies in {get_settings().outbox_dir}, "
          f"audit trail in logs/runs/ and logs/audit.db")
    return 0 if len(results) == len(emails) else 2


if __name__ == "__main__":
    sys.exit(main())
