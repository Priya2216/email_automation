"""Text helpers: cleaning, language detection, Indian currency formatting."""
from __future__ import annotations

import re

_QUOTE_MARKERS = re.compile(
    r"^(On .+ wrote:|-{2,}\s*Original Message\s*-{2,}|From: .+|Sent from my .+)$", re.I | re.M
)
_SIGN_OFF = re.compile(r"^\s*(regards|thanks|thank you|thanks and regards|thanks & regards|best regards|"
                       r"kind regards|warm regards|sincerely|cheers)\b[,!.]?\s*$", re.I | re.M)

# Devanagari (Hindi / Marathi) sign-offs; Python's \b does not work next to Devanagari vowel signs
_SIGN_OFF_DEV = re.compile(r"^\s*(धन्यवाद|आभारी आहे|आभार|शुक्रिया|भवदीय|आपला विश्वासू|आपका|आपकी)[,!.।]?\s*$", re.M)

_EN = {"the", "and", "my", "please", "account", "for", "of", "is", "to", "kindly", "card", "send", "share"}
_HI_LATIN = {"mera", "mujhe", "kripya", "khata", "hai", "ka", "ki", "bhejo", "batao"}
# whole-word markers (exact Devanagari tokens); prefixes cover inflections (माझा/माझी/माझ्या ...)
_MR_WORDS = {"आहे", "आहेत", "मला", "आणि", "तसेच", "किती", "द्या", "सांगा", "पाठवा", "हवे", "हवी", "करा", "नाही",
             "झाले", "पासून", "साठी", "बद्दल", "तुम्ही", "मी", "शिल्लक", "व्यवहार", "द्यावी", "पाठवावी", "मध्ये"}
_MR_PREFIX = ("माझ", "खात्या", "तुमच", "आमच", "गेल्या", "मागील")
_HI_WORDS = {"है", "हैं", "और", "का", "की", "के", "में", "चाहिए", "भेजें", "भेजिए", "बताएं", "बताइए", "कितना",
             "कितने", "क्या", "नहीं", "मुझे", "मेरा", "मेरी", "मेरे", "लेनदेन", "तथा", "दीजिए", "पिछले", "आपका"}

_DEV_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")


def normalize_digits(text: str) -> str:
    """Devanagari digits (२०२६, १२३४५६७८९०) -> ASCII digits so every regex/validator works."""
    return text.translate(_DEV_DIGITS)


def clean_email_text(body: str) -> str:
    """Remove quoted replies, signatures and extra whitespace."""
    text = normalize_digits(body.replace("\r\n", "\n"))
    match = _QUOTE_MARKERS.search(text)
    if match:
        text = text[: match.start()]
    text = "\n".join(line for line in text.split("\n") if not line.lstrip().startswith(">"))
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _find_sign_off(text: str):
    matches = [m for m in (_SIGN_OFF.search(text), _SIGN_OFF_DEV.search(text)) if m]
    return min(matches, key=lambda m: m.start()) if matches else None


def strip_sign_off(text: str) -> str:
    match = _find_sign_off(text)
    return text[: match.start()].strip() if match else text


def sign_off_name(body: str) -> str:
    """'Regards,\\nJohn Doe' -> 'John Doe' (optional customer-name entity)."""
    match = _find_sign_off(body or "")
    if not match:
        return ""
    for line in body[match.end():].splitlines():
        line = line.strip()
        if line:
            return line if re.fullmatch(r"[A-Za-z][A-Za-z .'-]{1,40}", line) else ""
    return ""


def detect_language(text: str) -> str:
    """Lightweight detector: en / hi / mr / hi-latn / unknown (Marathi vs Hindi scored by marker words)."""
    if re.search(r"[ऀ-ॿ]", text):
        tokens = re.findall(r"[ऀ-ॿ]+", text)
        mr = sum(t in _MR_WORDS or t.startswith(_MR_PREFIX) for t in tokens)
        hi = sum(t in _HI_WORDS for t in tokens)
        return "mr" if mr > hi else "hi"
    words = set(re.findall(r"[a-z]+", text.lower()))
    if len(words & _HI_LATIN) >= 2:
        return "hi-latn"
    return "en" if words & _EN or not words else "unknown"


def format_inr(amount: float) -> str:
    """125000 -> 'INR 1,25,000.00' (Indian digit grouping)."""
    negative = amount < 0
    whole, frac = f"{abs(amount):.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        head = ",".join(re.findall(r"\d{1,2}", head[::-1]))[::-1]
        whole = f"{head},{tail}"
    return f"{'-' if negative else ''}INR {whole}.{frac}"
