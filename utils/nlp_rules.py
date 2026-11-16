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

# --- Hindi / Marathi (Devanagari). Python's \b does not work next to Devanagari vowel signs, so these
#     are plain substring patterns; the words are distinctive enough to avoid false hits.
_BALANCE_DEV = re.compile(r"बॅलन्स|बॅलेन्स|बैलेंस|बैलन्स|शिल्लक|शेष राशि|बकाया राशि|खात्यात किती|खात्यातील रक्कम|"
                          r"खाते में कितने|कितना पैसा|कितने पैसे|किती पैसे")
_STATEMENT_DEV = re.compile(r"स्टेटमेंट|स्टेटमेन्ट|खाते उतारा|खातेउतारा|उतारा|पासबुक|खाते का विवरण")
_TXN_DEV = re.compile(r"व्यवहार|ट्रान्झॅक्शन|ट्रान्झेक्शन|ट्रांजैक्शन|ट्रांजेक्शन|लेनदेन|लेन-देन|खर्च|खरेदी")
_CARD_DEV = re.compile(r"कार्ड")
_ACCOUNT_DEV = re.compile(r"खाते|खाता|खात्या|अकाउंट|बचत")
_REQUEST_VERB_DEV = re.compile(r"कृपया|पाठवा|पाठवावे|पाठवावी|सांगा|द्या|द्यावे|द्यावी|हवे|हवी|भेजें|भेजिए|भेज दें|"
                               r"बताएं|बताइए|चाहिए|दीजिए|करा|करें")
_UNSUPPORTED_DEV = re.compile(r"कर्ज|लोन|चेकबुक|चेक बुक|पत्ता बदल|पता बदल|केवायसी|विमा|बीमा|एफडी|ठेव")

# --- Policy / information questions ("what are the late payment charges?") -> RAG, not a data API
_QUESTION = re.compile(
    r"\?|^\s*(?:what|how|when|why|which|where|is|are|can|could|does|do|will|would)\b|"
    r"\b(?:tell me|explain|information (?:about|on)|details (?:of|about)|want to know|wanted to know|know about)\b",
    re.I)
_POLICY_TOPIC = re.compile(
    r"\b(charges?|fees?|penalt(?:y|ies)|policy|policies|procedure|process|terms|conditions|eligib\w+|"
    r"documents? required|minimum (?:average )?balance|average (?:monthly )?balance|maintenance|"
    r"interest rates?|rate of interest|late payment|annual fee|joining fee|cash advance|forex|chargeback|"
    r"dispute|lost|stolen|zero liability|grievance|ombudsman|timeline|turnaround|how long|how many days|"
    r"working days|cut-?off|block(?:ing)?|re-?issue|replacement|kyc|nominee|cheque ?book)\b", re.I)
_QUESTION_DEV = re.compile(r"\?|कसे|कैसे|काय|क्या|किती|कितना|कितने|केव्हा|कब |माहिती|जानकारी")
_POLICY_TOPIC_DEV = re.compile(
    r"शुल्क|चार्ज|दंड|पॉलिसी|धोरण|नियम|प्रक्रिया|पात्रता|कागदपत्र|दस्तऐवज|दस्तावेज|किमान शिल्लक|मिनिमम|न्यूनतम|"
    r"व्याज|ब्याज|हरवले|हरवला|हरवल|खो गया|खो गई|चोरी|ब्लॉक|विवाद|तक्रार निवारण|शिकायत निवारण|किती दिवस|कितने दिन|"
    r"चेकबुक|चेक बुक")

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?।])\s+|\n+")
_CLAUSE_SPLIT = re.compile(r"\s*(?:,\s*)?\b(?:and also|and|also|as well as|plus|along with)\b\s*|\s*;\s*|"
                           r"\s+(?:आणि|तसेच|और|तथा)\s+", re.I)


def _any(clause: str, *patterns) -> bool:
    return any(p.search(clause) for p in patterns)


def is_policy_question(clause: str) -> bool:
    return (_any(clause, _QUESTION, _QUESTION_DEV)) and _any(clause, _POLICY_TOPIC, _POLICY_TOPIC_DEV)


def classify_clause_scored(clause: str, ctx_card: bool = False, ctx_account: bool = False
                           ) -> Optional[tuple[IntentType, float]]:
    """(intent, confidence) of one clause. Confidence reflects how unambiguous the evidence is:
    a keyword AND an identifier (account/card number, period) = high; a guess from context = low."""
    numbers = find_numbers(clause, default_kind="card")
    has_card_number = bool(numbers) and _any(clause, _CARD, _CARD_DEV)
    has_card_word = _any(clause, _CARD, _CARD_DEV)

    if is_policy_question(clause):
        return IntentType.POLICY_QUERY, 0.85
    if _any(clause, _STATEMENT, _STATEMENT_DEV):
        return IntentType.STATEMENT_REQUEST, 0.96 if extract_period(clause) else 0.92
    if _any(clause, _TXN, _TXN_DEV) and (has_card_word or has_card_number):
        return IntentType.CARD_TRANSACTIONS, 0.96 if numbers else 0.92
    if _any(clause, _BALANCE, _BALANCE_DEV):
        return IntentType.BALANCE_ENQUIRY, 0.95 if numbers else 0.9
    if _any(clause, _TXN, _TXN_DEV):
        # a transactions request with no card word in THIS clause: use the rest of the email as context
        if ctx_card and not ctx_account:
            return IntentType.CARD_TRANSACTIONS, 0.8
        # "account transactions for April" == a statement
        if ctx_account or re.search(r"\baccount\b", clause, re.I) or _ACCOUNT_DEV.search(clause):
            return IntentType.STATEMENT_REQUEST, 0.82
        return IntentType.STATEMENT_REQUEST, 0.55  # genuinely ambiguous -> low confidence -> human review
    if _any(clause, _UNSUPPORTED_TOPIC, _UNSUPPORTED_DEV) and _any(clause, _REQUEST_VERB, _REQUEST_VERB_DEV):
        return IntentType.UNSUPPORTED, 0.7
    return None


def classify_clause(clause: str) -> Optional[IntentType]:
    scored = classify_clause_scored(clause)
    return scored[0] if scored else None


@dataclass
class DetectedIntent:
    intent: IntentType
    text_span: str
    confidence: float


def detect_intents(text: str) -> list[DetectedIntent]:
    """Split the email into clauses, classify each, merge context clauses."""
    ctx_card = _any(text, _CARD, _CARD_DEV) or bool(numbers_of_kind(text, "card"))
    ctx_account = _any(text, re.compile(r"\b(?:account|a/c|acct)\b", re.I), _ACCOUNT_DEV) or \
        bool(numbers_of_kind(text, "account"))
    clauses: list[str] = []
    for sentence in _SENTENCE_SPLIT.split(text):
        sentence = sentence.strip()
        if sentence:
            clauses.extend(c.strip() for c in _CLAUSE_SPLIT.split(sentence) if c and c.strip())

    detected: list[DetectedIntent] = []
    pending_context = ""
    for clause in clauses:
        scored = classify_clause_scored(clause, ctx_card, ctx_account)
        if scored is None:
            # no intent keyword: it is context (e.g. "and 2345678901", "February 2026")
            if detected and _carries_entities(clause):
                detected[-1].text_span += f" and {clause}"
            elif _carries_entities(clause):
                pending_context += f" {clause}"
            continue
        intent, confidence = scored
        span = f"{pending_context.strip()} {clause}".strip() if pending_context else clause
        pending_context = ""
        # an intent mentioned again without new account/card numbers (e.g. "Statement period:
        # September 2026" after "account statement") refines the earlier request
        earlier = next((d for d in reversed(detected) if d.intent == intent), None)
        if earlier and intent != IntentType.POLICY_QUERY and not find_numbers(clause):
            earlier.text_span += f" and {span}"
            continue
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
        card_pos = max(before.rfind("card"), before.rfind("कार्ड"), -1)
        acc_match = list(_ACCOUNT_WORD.finditer(before)) + list(_ACCOUNT_DEV.finditer(before))
        acc_pos = max(m.start() for m in acc_match) if acc_match else -1
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


_COUNT_DEV = [
    re.compile(r"(?:शेवटचे|शेवटच्या|शेवटचा|अंतिम|पिछले|आखिरी|ताजे|ताज्या|मागील|गेल्या)\s*(\d{1,2})"),
    re.compile(r"(\d{1,2})\s*(?:व्यवहार|ट्रान्झॅक्शन|ट्रान्झेक्शन|ट्रांजैक्शन|लेनदेन|लेन-देन)"),
]


def extract_count(text: str) -> Optional[int]:
    for regex in _COUNT_DEV:
        m = regex.search(text)
        if m:
            return int(m.group(1))
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


_DEV_MONTHS = {
    "जानेवारी": 1, "जनवरी": 1, "फेब्रुवारी": 2, "फरवरी": 2, "मार्च": 3, "एप्रिल": 4, "अप्रैल": 4, "अप्रेल": 4,
    "मई": 5, "जून": 6, "जुलै": 7, "जुलाई": 7, "ऑगस्ट": 8, "अगस्त": 8, "सप्टेंबर": 9, "सितंबर": 9, "सितम्बर": 9,
    "ऑक्टोबर": 10, "अक्टूबर": 10, "नोव्हेंबर": 11, "नवंबर": 11, "नवम्बर": 11, "डिसेंबर": 12, "दिसंबर": 12,
    "दिसम्बर": 12,
}
_DEV_MONTH_RE = re.compile("(" + "|".join(sorted(_DEV_MONTHS, key=len, reverse=True)) + r")(?:[ ,'-]*(\d{4}))?")
_MAY_DEV_RE = re.compile(r"मे[ ,'-]+(\d{4})")  # Marathi "May" (bare "मे" is too common without a year)
_LAST_MONTH_DEV = re.compile(r"(?:मागील|गेल्या|मागच्या|पिछले|पिछला|पिछली)\s*(?:महिन्या|महिने|महीने|महीना|महिना)")


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
    if re.search(r"\b(last|previous|past)\s+month\b", lower) or _LAST_MONTH_DEV.search(text):
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
    for mm in _DEV_MONTH_RE.finditer(text):
        months.append((_DEV_MONTHS[mm.group(1)], int(mm.group(2)) if mm.group(2) else None))
    for mm in _MAY_DEV_RE.finditer(text):
        months.append((5, int(mm.group(1))))
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


_NEGATIVE_DEV = re.compile(
    r"नाराज|त्रास|वैतागल|तक्रार|अस्वीकार्य|प्रतिसाद नाही|उत्तर नाही|जवाब नहीं|शिकायत|परेशान|फसवणूक|धोखाधड़ी|"
    r"धोखाधडी|अनधिकृत|खराब सेवा|वाईट सेवा|तिसऱ्यांदा|तीसरी बार|दुसऱ्यांदा|तीन वेळा")
_ESCALATE_DEV = re.compile(r"लोकपाल|वकील|कायदेशीर|कानूनी|मॅनेजर|मैनेजर|व्यवस्थापक|फसवणूक|धोखाधड़ी|धोखाधडी|अनधिकृत|"
                           r"सोशल मीडिया|खाते बंद")
_URGENT_DEV = re.compile(r"तात्काळ|ताबडतोब|तातडी|लगेच|तुरंत|अर्जेंट|अर्जंट|जल्द से जल्द|आजच|आज ही")
_POSITIVE_DEV = re.compile(r"धन्यवाद|आभार|शुक्रिया|बहुत अच्छा|उत्तम|छान")


def analyse_sentiment(text: str) -> dict:
    neg = [m.group(0) for m in _NEGATIVE.finditer(text)] + [m.group(0) for m in _NEGATIVE_DEV.finditer(text)]
    esc = [m.group(0) for m in _ESCALATE.finditer(text)] + [m.group(0) for m in _ESCALATE_DEV.finditer(text)]
    urg = [m.group(0) for m in _URGENT.finditer(text)] + [m.group(0) for m in _URGENT_DEV.finditer(text)]
    pos = [m.group(0) for m in _POSITIVE.finditer(text)] + [m.group(0) for m in _POSITIVE_DEV.finditer(text)]
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
