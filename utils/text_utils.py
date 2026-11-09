"""Text helpers: cleaning, language detection, Indian currency formatting."""
from __future__ import annotations

import re

from lingua import LanguageDetectorBuilder

_QUOTE_MARKERS = re.compile(
    r"^(On .+ wrote:|-{2,}\s*Original Message\s*-{2,}|From: .+|Sent from my .+)$", re.I | re.M
)
_SIGN_OFF = re.compile(r"^\s*(regards|thanks|thank you|thanks and regards|thanks & regards|best regards|"
                       r"kind regards|warm regards|sincerely|cheers)\b[,!.]?\s*$", re.I | re.M)

_LANGUAGE_DETECTOR = LanguageDetectorBuilder.from_all_languages().build()


def clean_email_text(body: str) -> str:
    """Remove quoted replies, signatures and extra whitespace."""
    text = body.replace("\r\n", "\n")
    match = _QUOTE_MARKERS.search(text)
    if match:
        text = text[: match.start()]
    text = "\n".join(line for line in text.split("\n") if not line.lstrip().startswith(">"))
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def strip_sign_off(text: str) -> str:
    match = _SIGN_OFF.search(text)
    return text[: match.start()].strip() if match else text


def sign_off_name(body: str) -> str:
    """'Regards,\\nJohn Doe' -> 'John Doe' (optional customer-name entity)."""
    match = _SIGN_OFF.search(body or "")
    if not match:
        return ""
    for line in body[match.end():].splitlines():
        line = line.strip()
        if line:
            return line if re.fullmatch(r"[A-Za-z][A-Za-z .'-]{1,40}", line) else ""
    return ""


def detect_language(text: str) -> str:
    """Detect the email language and return its ISO 639-1 code when available."""
    language = _LANGUAGE_DETECTOR.detect_language_of(text.strip())
    if language is None or language.iso_code_639_1 is None:
        return "unknown"
    return language.iso_code_639_1.name.lower()


def format_inr(amount: float) -> str:
    """125000 -> 'INR 1,25,000.00' (Indian digit grouping)."""
    negative = amount < 0
    whole, frac = f"{abs(amount):.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        head = ",".join(re.findall(r"\d{1,2}", head[::-1]))[::-1]
        whole = f"{head},{tail}"
    return f"{'-' if negative else ''}INR {whole}.{frac}"
