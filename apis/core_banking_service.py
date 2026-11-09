"""
Business logic of the *simulated* core banking system.

The FastAPI app in apis/core_banking_api.py is a thin HTTP layer over these
functions. Data comes from apis/data/core_banking_data.json; account
statements are generated deterministically and rendered to PDF.
"""
from __future__ import annotations

import calendar
import json
import random
import re
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from fpdf import FPDF  # imported at startup: a cold first import can take seconds

from config.settings import get_settings
from utils.masking import mask_account

DATA_FILE = Path(__file__).parent / "data" / "core_banking_data.json"


class CoreBankingError(Exception):
    def __init__(self, code: str, message: str, http_status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


def _load() -> dict:
    with open(DATA_FILE, encoding="utf-8") as fh:
        return json.load(fh)


_DATA = _load()
_ACCOUNTS = {a["account_number"]: a for a in _DATA["accounts"]}
_CARDS = _DATA["cards"]
_CUSTOMERS = {c["customer_id"]: c for c in _DATA["customers"]}


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def _customer(customer_id: str) -> dict:
    return _CUSTOMERS.get(customer_id, {})


def simulation_mode(account_number: str) -> Optional[str]:
    """'timeout' / 'error' for the special demo accounts, else None."""
    acc = _ACCOUNTS.get(_digits(account_number))
    return acc.get("_simulate") if acc else None


# --------------------------------------------------------------------------- #
# Customers
# --------------------------------------------------------------------------- #
def get_customer_by_email(email: str) -> dict:
    email = (email or "").strip().lower()
    for cust in _CUSTOMERS.values():
        if cust["email"].lower() == email:
            return {"status": "FOUND", **cust}
    return {"status": "NOT_FOUND", "email": email}


# --------------------------------------------------------------------------- #
# Accounts
# --------------------------------------------------------------------------- #
def validate_account(account_number: str) -> dict:
    number = _digits(account_number)
    if not re.fullmatch(r"\d{10,12}", number):
        return {"status": "INVALID", "reason": "Account number must be 10-12 digits"}
    acc = _ACCOUNTS.get(number)
    if not acc:
        return {"status": "INVALID", "reason": "Account not found"}
    if acc["status"] != "ACTIVE":
        return {"status": "INVALID", "reason": f"Account is {acc['status'].lower()}"}
    cust = _customer(acc["customer_id"])
    return {
        "status": "VALID",
        "customer_name": cust.get("name", ""),
        "customer_id": acc["customer_id"],
        "account_type": acc["account_type"],
    }


def _require_active_account(account_number: str) -> dict:
    v = validate_account(account_number)
    if v["status"] != "VALID":
        raise CoreBankingError("INVALID_ACCOUNT", v["reason"], 404)
    return _ACCOUNTS[_digits(account_number)]


def get_account_balance(account_number: str) -> dict:
    acc = _require_active_account(account_number)
    return {
        "account_number": acc["account_number"],
        "account_type": acc["account_type"],
        "currency": acc["currency"],
        "available_balance": acc["available_balance"],
        "ledger_balance": acc["ledger_balance"],
        "as_of": datetime.now().isoformat(timespec="seconds"),
    }


# --------------------------------------------------------------------------- #
# Cards
# --------------------------------------------------------------------------- #
def _find_card(card_number: str) -> Optional[dict]:
    """
    Accepts a full 16-digit number, a partly masked one (4567XXXX8901,
    4567-XXXX-XXXX-8901) or the 9-digit card reference printed on statements.
    """
    raw = (card_number or "").strip()
    digits = _digits(raw)
    masked = bool(re.search(r"[Xx*]", raw))
    for card in _CARDS:
        if not masked and digits in (card["card_number"], card["card_reference"]):
            return card
        if masked:
            first4, last4 = digits[:4], digits[-4:]
            if len(digits) >= 8 and card["card_number"].startswith(first4) and card["card_number"].endswith(last4):
                return card
    return None


def validate_card(card_number: str) -> dict:
    card = _find_card(card_number)
    if not card:
        return {"status": "INVALID", "reason": "Card not found"}
    if card["status"] != "ACTIVE":
        return {"status": "INVALID", "reason": f"Card is {card['status'].lower()}"}
    cust = _customer(card["customer_id"])
    return {
        "status": "VALID",
        "customer_name": cust.get("name", ""),
        "customer_id": card["customer_id"],
        "card_type": card["card_type"],
        "card_last4": card["card_number"][-4:],
    }


def get_card_transactions(card_number: str, count: int = 5) -> dict:
    v = validate_card(card_number)
    if v["status"] != "VALID":
        raise CoreBankingError("INVALID_CARD", v["reason"], 404)
    card = _find_card(card_number)
    count = max(1, min(int(count or 5), 50))
    txns = sorted(card["transactions"], key=lambda t: t["date"], reverse=True)[:count]
    return {
        "card_last4": card["card_number"][-4:],
        "card_type": card["card_type"],
        "network": card["network"],
        "credit_limit": card["credit_limit"],
        "available_limit": card["available_limit"],
        "requested_count": count,
        "returned_count": len(txns),
        "transactions": txns,
    }


# --------------------------------------------------------------------------- #
# Statements
# --------------------------------------------------------------------------- #
_MERCHANTS = [
    ("UPI/Swiggy", "DEBIT", (150, 900)), ("NEFT/Salary Credit", "CREDIT", (85000, 85000)),
    ("ATM Withdrawal", "DEBIT", (2000, 10000)), ("UPI/BigBasket", "DEBIT", (600, 3500)),
    ("Electricity Bill - MSEDCL", "DEBIT", (900, 2600)), ("IMPS/Rent", "DEBIT", (18000, 18000)),
    ("Interest Credit", "CREDIT", (120, 450)), ("UPI/Amazon Pay", "DEBIT", (300, 6000)),
    ("Credit Card Bill Payment", "DEBIT", (5000, 25000)), ("Mutual Fund SIP", "DEBIT", (5000, 5000)),
]


def _generate_statement_lines(account_number: str, start: date, end: date) -> list[dict]:
    """Deterministic pseudo-random transactions (same input -> same output)."""
    lines = []
    day = start
    while day <= end:
        rng = random.Random(f"{account_number}-{day.isoformat()}")
        if day.day == 1:
            desc, typ, (lo, hi) = _MERCHANTS[1]
            lines.append({"date": day.isoformat(), "description": desc, "type": typ, "amount": float(lo)})
        if rng.random() < 0.35:
            desc, typ, (lo, hi) = rng.choice(_MERCHANTS[2:])
            lines.append({"date": day.isoformat(), "description": desc, "type": typ,
                          "amount": round(rng.uniform(lo, hi), 2)})
        day += timedelta(days=1)
    return lines


def get_statement(account_number: str, period_start: str, period_end: str) -> dict:
    acc = _require_active_account(account_number)
    try:
        start, end = date.fromisoformat(period_start), date.fromisoformat(period_end)
    except ValueError as exc:
        raise CoreBankingError("INVALID_PERIOD", f"Invalid date: {exc}", 400)
    if end < start:
        raise CoreBankingError("INVALID_PERIOD", "period_end is before period_start", 400)
    if (end - start).days > 366:
        raise CoreBankingError("INVALID_PERIOD", "Statement period cannot exceed 12 months", 400)
    if start > date.today():
        raise CoreBankingError("INVALID_PERIOD", "Statement period is in the future", 400)
    end = min(end, date.today())

    # Opening balance = Jan-1 balance rolled forward to the period start
    opening = acc["opening_balance_2026_01_01"]
    if start > date(2026, 1, 1):
        for line in _generate_statement_lines(acc["account_number"], date(2026, 1, 1), start - timedelta(days=1)):
            opening += line["amount"] if line["type"] == "CREDIT" else -line["amount"]
    lines = _generate_statement_lines(acc["account_number"], start, end)
    balance = opening
    for line in lines:
        balance += line["amount"] if line["type"] == "CREDIT" else -line["amount"]
        line["balance"] = round(balance, 2)

    statement_id = f"STMT-{acc['account_number'][-4:]}-{start:%Y%m%d}-{uuid.uuid4().hex[:6].upper()}"
    pdf_path = _render_statement_pdf(statement_id, acc, start, end, round(opening, 2), lines)
    return {
        "statement_id": statement_id,
        "account_number": acc["account_number"],
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "opening_balance": round(opening, 2),
        "closing_balance": round(balance, 2),
        "transaction_count": len(lines),
        "currency": acc["currency"],
        "document_path": str(pdf_path),
    }


def _render_statement_pdf(statement_id: str, acc: dict, start: date, end: date,
                          opening: float, lines: list[dict]) -> Path:
    out_dir = get_settings().statements_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    cust = _customer(acc["customer_id"])

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "Demo Bank Ltd. - Account Statement", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=10)
    for label, value in [
        ("Statement ID", statement_id),
        ("Customer", cust.get("name", "")),
        ("Account", f"{mask_account(acc['account_number'])} ({acc['account_type']})"),
        ("Branch", acc["branch"]),
        ("Period", f"{start:%d-%b-%Y} to {end:%d-%b-%Y}"),
        ("Opening balance", f"INR {opening:,.2f}"),
    ]:
        pdf.cell(0, 6, f"{label}: {value}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    pdf.set_font("Helvetica", "B", 10)
    for title, width in [("Date", 28), ("Description", 80), ("Debit", 27), ("Credit", 27), ("Balance", 28)]:
        pdf.cell(width, 7, title, border=1)
    pdf.ln()
    pdf.set_font("Helvetica", size=9)
    for line in lines:
        debit = f"{line['amount']:,.2f}" if line["type"] == "DEBIT" else ""
        credit = f"{line['amount']:,.2f}" if line["type"] == "CREDIT" else ""
        for text, width in [(line["date"], 28), (line["description"][:42], 80), (debit, 27),
                            (credit, 27), (f"{line['balance']:,.2f}", 28)]:
            pdf.cell(width, 6, text, border=1)
        pdf.ln()
    if not lines:
        pdf.cell(0, 7, "No transactions in this period.", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    pdf.set_font("Helvetica", "I", 8)
    pdf.multi_cell(0, 5, "This is a system-generated statement from a SIMULATED core banking system "
                         "for demonstration purposes only.")
    path = out_dir / f"{statement_id}.pdf"
    pdf.output(str(path))
    return path


def month_bounds(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])
