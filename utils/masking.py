"""
Masking of sensitive data (account and card numbers).

Used in three places:
  * customer-facing emails (only the last 4 digits are shown)
  * application logs (a logging.Filter masks every long number)
  * audit records stored in the database
"""
import logging
import re

# 9+ digit numbers, optionally with spaces/dashes, and partly-masked card
# numbers such as 4567XXXX8901 or 4567-XXXX-XXXX-8901.
_SENSITIVE_RE = re.compile(
    r"\b(?:\d[ -]?){5,}(?:[Xx*]{2,}[ -]?)*(?:\d[ -]?){3}\d\b|\b\d{4}[ -]?(?:[Xx*]{4}[ -]?){1,2}\d{4}\b"
)


def last4(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    return digits[-4:] if len(digits) >= 4 else digits


def mask_account(account_number: str) -> str:
    """1234567890 -> XXXX7890"""
    return f"XXXX{last4(account_number)}" if account_number else ""


def mask_card(card_number: str) -> str:
    """4567123456788901 -> XXXX XXXX XXXX 8901"""
    return f"XXXX XXXX XXXX {last4(card_number)}" if card_number else ""


def mask_text(text: str) -> str:
    """Mask every account/card-like number inside free text."""
    if not text:
        return text

    def _repl(match: re.Match) -> str:
        raw = match.group(0)
        digits = re.sub(r"\D", "", raw)
        if len(digits) < 8 and not re.search(r"[Xx*]", raw):
            return raw
        return f"XXXX{digits[-4:]}"

    return _SENSITIVE_RE.sub(_repl, text)


def mask_value(value):
    """Recursively mask strings inside dicts/lists (for audit payloads)."""
    if isinstance(value, str):
        return mask_text(value)
    if isinstance(value, dict):
        return {k: mask_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [mask_value(v) for v in value]
    return value


class MaskingFilter(logging.Filter):
    """Logging filter that masks sensitive numbers in every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = mask_text(str(record.msg))
        if record.args:
            record.args = tuple(mask_value(a) for a in record.args) if isinstance(record.args, tuple) else record.args
        return True
