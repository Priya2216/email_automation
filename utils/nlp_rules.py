"""
Rule-based NLP engine.

Used directly when no LLM is available, and as a safety net / cross-check
when Llama 3 is used (e.g. to verify that numbers the LLM returns really
appear in the email).
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from models.schemas import IntentType, SentimentLabel

# --------------------------------------------------------------------------- #
# Intent detection
# --------------------------------------------------------------------------- #
_BALANCE = re.compile(r"\bbalances?\b|\bhow much (?:money|funds?)\b|\bavailable funds?\b", re.I)
_STATEMENT = re.compile(r"\bstatements?\b|\be-?statements?\b|\bpassbook\b", re.I)
_TXN = re.compile(r"\btransactions?\b|\busage\b|\bspends?\b|\bspent\b|\bspending\b|\bpurchases?\b|\btxns?\b", re.I)
_CARD = re.compile(r"\bcards?\b", re.I)
_REQUEST_VERB = re.compile(
    r"\b(please|kindly|request|want|need|would like|can you|could you|help|send|share|provide|"
    r"update|change|issue|block|close|apply|increase)\b", re.I)
_UNSUPPORTED_TOPIC = re.compile(
    r"\b(loan|emi|cheque ?book|cheque|address|kyc|pin|block|unblock|limit increase|credit limit|"
    r"fixed deposit|\bfd\b|interest rate|close my account|nominee|mobile number|net ?banking password|"
    r"debit card replacement|new card|insurance|locker)\b", re.I)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_CLAUSE_SPLIT = re.compile(r"\s*(?:,\s*)?\b(?:and also|and|also|as well as|plus|along with)\b\s*|\s*;\s*", re.I)


def classify_clause(clause: str) -> Optional[IntentType]:
    has_card_number = bool(find_numbers(clause, default_kind="card")) and _CARD.search(clause)
    if _STATEMENT.search(clause):
        return IntentType.STATEMENT_REQUEST
    if _TXN.search(clause) and (_CARD.search(clause) or has_card_number):
        return IntentType.CARD_TRANSACTIONS
    if _BALANCE.search(clause):
        return IntentType.BALANCE_ENQUIRY
    if _TXN.search(clause):
        # "account transactions for April" == a statement
        return IntentType.STATEMENT_REQUEST
    if _UNSUPPORTED_TOPIC.search(clause) and _REQUEST_VERB.search(clause):
        return IntentType.UNSUPPORTED
    return None


@dataclass
class DetectedIntent:
    intent: IntentType
    text_span: str
    confidence: float


def detect_intents(text: str) -> list[DetectedIntent]:
    """Split the email into clauses, classify each, merge context clauses."""
    clauses: list[str] = []
    for sentence in _SENTENCE_SPLIT.split(text):
        sentence = sentence.strip()
        if sentence:
            clauses.extend(c.strip() for c in _CLAUSE_SPLIT.split(sentence) if c and c.strip())

    detected: list[DetectedIntent] = []
    pending_context = ""
    for clause in clauses:
        intent = classify_clause(clause)
        if intent is None:
            # no intent keyword: it is context (e.g. "and 2345678901", "February 2026")
            if detected and _carries_entities(clause):
                detected[-1].text_span += f" and {clause}"
            elif _carries_entities(clause):
                pending_context += f" {clause}"
            continue
        span = f"{pending_context.strip()} {clause}".strip() if pending_context else clause
        pending_context = ""
        # an intent mentioned again without new account/card numbers (e.g. "Statement period:
        # September 2026" after "account statement") refines the earlier request
        earlier = next((d for d in reversed(detected) if d.intent == intent), None)
        if earlier and not find_numbers(clause):
            earlier.text_span += f" and {span}"
            continue
        confidence = 0.75 if intent == IntentType.UNSUPPORTED else 0.9
        detected.append(DetectedIntent(intent, span, confidence))
    return detected


def _carries_entities(clause: str) -> bool:
    return bool(find_numbers(clause)) or bool(_MONTH_RE.search(clause)) or bool(re.search(r"\b\d{4}\b", clause))


# --------------------------------------------------------------------------- #
# Account / card numbers
# --------------------------------------------------------------------------- #
_MASKED_CARD = re.compile(r"\b\d{4}[ -]?(?:[Xx*]{4}[ -]?){1,2}\d{4}\b")
_FULL_CARD = re.compile(r"\b\d{4}([ -]?)\d{4}\1\d{4}\1\d{4}\b")
_PLAIN_NUMBER = re.compile(r"(?<![\d-])\d{9,12}(?![\d-])")
_ACCOUNT_WORD = re.compile(r"\b(account|a/c|acct|acc(?:ount)?\s*no|savings|current)\b", re.I)


@dataclass
class FoundNumber:
    kind: str   # account | card
    value: str  # as written (masking kept, separators removed for full cards)
    start: int


def find_numbers(text: str, default_kind: str | None = None) -> list[FoundNumber]:
    found: list[FoundNumber] = []
    taken: list[tuple[int, int]] = []
    for regex in (_MASKED_CARD, _FULL_CARD):
        for m in regex.finditer(text):
            found.append(FoundNumber("card", re.sub(r"[ -]", "", m.group(0)), m.start()))
            taken.append(m.span())
    for m in _PLAIN_NUMBER.finditer(text):
        if any(s <= m.start() < e for s, e in taken):
            continue
        before = text[max(0, m.start() - 40): m.start()].lower()
        card_pos = max(before.rfind("card"), -1)
        acc_match = list(_ACCOUNT_WORD.finditer(before))
        acc_pos = acc_match[-1].start() if acc_match else -1
        if card_pos > acc_pos:
            kind = "card"
        elif acc_pos > card_pos:
            kind = "account"
        elif default_kind:
            kind = default_kind
        else:
            kind = "account" if len(m.group(0)) >= 10 else "card"
        found.append(FoundNumber(kind, m.group(0), m.start()))
    return sorted(found, key=lambda f: f.start)


def numbers_of_kind(text: str, kind: str, default_kind: str | None = None) -> list[str]:
    seen, out = set(), []
    for f in find_numbers(text, default_kind):
        if f.kind == kind and f.value not in seen:
            seen.add(f.value)
            out.append(f.value)
    return out


def number_in_text(value: str, *texts: str) -> bool:
    """Anti-hallucination check: does `value` appear in any source text?"""
    norm = re.sub(r"[ -]", "", value or "").upper()
    return bool(norm) and any(norm in re.sub(r"[ -]", "", t or "").upper() for t in texts)


# --------------------------------------------------------------------------- #
# Transaction count
# --------------------------------------------------------------------------- #
_WORD_NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
                 "nine": 9, "ten": 10, "fifteen": 15, "twenty": 20}
_NUM = r"(\d{1,2}|" + "|".join(_WORD_NUMBERS) + r")"
_COUNT_RES = [
    re.compile(rf"\b(?:last|latest|recent|previous|past|top)\s+{_NUM}\b", re.I),
    re.compile(rf"\b{_NUM}\s+(?:most\s+)?(?:recent\s+|latest\s+|last\s+)?(?:credit\s+card\s+|card\s+)?"
               r"(?:transactions?|txns?|purchases?|spends?)\b", re.I),
]


def extract_count(text: str) -> Optional[int]:
    for regex in _COUNT_RES:
        m = regex.search(text)
        if m:
            raw = m.group(1).lower()
            return int(raw) if raw.isdigit() else _WORD_NUMBERS[raw]
    return None


# --------------------------------------------------------------------------- #
# Dates / statement period
# --------------------------------------------------------------------------- #
_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
_MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
_MONTHS["sept"] = 9
_MONTH_RE = re.compile(r"\b(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\b\.?(?:[ ,'-]*(\d{4}))?", re.I)
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_DMY_DATE = re.compile(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})\b")
_DAY_MONTH_YEAR = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(" + "|".join(sorted(_MONTHS, key=len, reverse=True))
                             + r")\.?,?\s+(\d{4})\b", re.I)
_MONTH_NUM_YEAR = re.compile(r"\b(\d{1,2})/(\d{4})\b")


def _month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def _shift_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    return date(d.year + y, m + 1, 1)


def extract_period(text: str, today: date | None = None) -> Optional[tuple[date, date, str]]:
    today = today or date.today()
    lower = text.lower()

    # explicit dates ("01/04/2026 to 30/04/2026", "2026-04-01 - 2026-04-30", "1 April 2026 to 15 April 2026")
    dates: list[tuple[int, date]] = []
    for m in _ISO_DATE.finditer(text):
        try:
            dates.append((m.start(), date(int(m[1]), int(m[2]), int(m[3]))))
        except ValueError:
            pass
    for m in _DMY_DATE.finditer(text):
        try:
            dates.append((m.start(), date(int(m[3]), int(m[2]), int(m[1]))))  # Indian DD/MM/YYYY
        except ValueError:
            pass
    for m in _DAY_MONTH_YEAR.finditer(text):
        try:
            dates.append((m.start(), date(int(m[3]), _MONTHS[m[2].lower()], int(m[1]))))
        except ValueError:
            pass
    if len(dates) >= 2:
        ds = sorted(d for _, d in dates)
        return ds[0], ds[-1], f"{ds[0]:%d-%b-%Y} to {ds[-1]:%d-%b-%Y}"

    # relative periods
    if re.search(r"\b(last|previous|past)\s+month\b", lower):
        start = _shift_months(today.replace(day=1), -1)
        return start, _month_end(start.year, start.month), f"{start:%B %Y}"
    if re.search(r"\b(this|current)\s+month\b", lower):
        start = today.replace(day=1)
        return start, today, f"{start:%B %Y} (month to date)"
    m = re.search(rf"\b(?:last|past|previous)\s+{_NUM}\s+months?\b", lower)
    if m:
        n = int(m[1]) if m[1].isdigit() else _WORD_NUMBERS[m[1]]
        start = _shift_months(today.replace(day=1), -n)
        end = today.replace(day=1) - timedelta(days=1)  # end of previous month
        return start, end, f"last {n} months ({start:%b %Y} - {end:%b %Y})"
    m = re.search(rf"\b(?:last|past)\s+{_NUM}\s+days\b", lower)
    if m:
        n = int(m[1]) if m[1].isdigit() else _WORD_NUMBERS[m[1]]
        return today - timedelta(days=n), today, f"last {n} days"

    # month names, optionally with a year ("April 2026", "Jan and Feb 2026", "04/2026")
    months: list[tuple[int, Optional[int]]] = []
    for mm in _MONTH_RE.finditer(text):
        word = mm.group(1).lower()
        if word == "may" and not mm.group(2) and not re.search(r"\b(in|for|of|from|to)\s+may\b", lower):
            continue  # "may" as a verb
        months.append((_MONTHS[word], int(mm.group(2)) if mm.group(2) else None))
    for mm in _MONTH_NUM_YEAR.finditer(text):
        if 1 <= int(mm[1]) <= 12:
            months.append((int(mm[1]), int(mm[2])))
    if months:
        explicit_year = next((y for _, y in reversed(months) if y), None)
        resolved = []
        for month, year in months:
            if year is None:
                year = explicit_year or (today.year if month <= today.month else today.year - 1)
            resolved.append(date(year, month, 1))
        start, last = min(resolved), max(resolved)
        end = _month_end(last.year, last.month)
        label = f"{start:%B %Y}" if start == last else f"{start:%B %Y} to {last:%B %Y}"
        return start, end, label
    return None


# --------------------------------------------------------------------------- #
# Sentiment
# --------------------------------------------------------------------------- #
_NEGATIVE = re.compile(
    r"\b(angry|frustrat\w*|disappoint\w*|unacceptable|worst|terrible|horrible|pathetic|useless|ridiculous|"
    r"annoy\w*|upset|fed up|not happy|unhappy|poor service|complain\w*|no response|still not|"
    r"(?:second|third|fourth|multiple) time|again and again|waste of time|fraud\w*|unauthori[sz]ed)\b", re.I)
_ESCALATE = re.compile(r"\b(escalat\w*|ombudsman|legal action|lawyer|consumer court|social media|twitter|"
                       r"close my account|switch banks?|manager|fraud\w*|unauthori[sz]ed)\b", re.I)
_URGENT = re.compile(r"\b(urgent\w*|immediately|asap|right away|at the earliest|today itself|emergency|"
                     r"by today|by tomorrow|time.?sensitive|critical)\b", re.I)
_POSITIVE = re.compile(r"\b(thank\w*|appreciat\w*|great|excellent|happy|pleased|wonderful|helpful|love)\b", re.I)


def analyse_sentiment(text: str) -> dict:
    neg = [m.group(0) for m in _NEGATIVE.finditer(text)]
    esc = [m.group(0) for m in _ESCALATE.finditer(text)]
    urg = [m.group(0) for m in _URGENT.finditer(text)]
    pos = [m.group(0) for m in _POSITIVE.finditer(text)]
    exclaim = text.count("!") >= 2 or bool(re.search(r"\b[A-Z]{4,}\b(?:\s+\b[A-Z]{3,}\b)+", text))

    score = 0.25 * len(pos) - 0.4 * len(neg) - 0.3 * len(esc) - (0.2 if exclaim else 0)
    score = max(-1.0, min(1.0, score))
    if neg or esc or score <= -0.3:
        label = SentimentLabel.NEGATIVE
    elif urg:
        label = SentimentLabel.URGENT
    elif score > 0.2:
        label = SentimentLabel.POSITIVE
    else:
        label = SentimentLabel.NEUTRAL
    return {
        "label": label,
        "score": round(score, 2),
        "is_urgent": bool(urg),
        "escalate_to_human": bool(esc) or (bool(neg) and bool(urg)) or len(neg) >= 3,
        "reasons": sorted(set(neg + esc + urg + pos))[:8],
    }
