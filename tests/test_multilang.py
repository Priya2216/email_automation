"""Hindi / Marathi support: intent, entities, sentiment, language detection and replies in the customer's language."""
from datetime import date

from conftest import make_email, run
from models.schemas import IncomingEmail, IntentType, RequestStatus
from utils.nlp_rules import analyse_sentiment, detect_intents, extract_count, extract_period, find_numbers
from utils.text_utils import clean_email_text, detect_language, normalize_digits

MR_BALANCE = "कृपया माझ्या खात्याची शिल्लक सांगा, खाते १२३४५६७८९०"
HI_MIXED = "कृपया मेरे खाते 1234567890 का बैलेंस बताएं और अप्रैल 2026 का स्टेटमेंट भेजें"


def _intents(text):
    return [(d.intent, d.confidence) for d in detect_intents(clean_email_text(text))]


def test_devanagari_digits_are_normalised():
    assert normalize_digits("खाते १२३४५६७८९० एप्रिल २०२६") == "खाते 1234567890 एप्रिल 2026"
    assert clean_email_text(MR_BALANCE).endswith("1234567890")


def test_language_detection():
    assert detect_language(MR_BALANCE) == "mr"
    assert detect_language(HI_MIXED) == "hi"
    assert detect_language("Please share my balance") == "en"


def test_marathi_and_hindi_intents():
    assert [i for i, _ in _intents(MR_BALANCE)] == [IntentType.BALANCE_ENQUIRY]
    assert [i for i, _ in _intents(HI_MIXED)] == [IntentType.BALANCE_ENQUIRY, IntentType.STATEMENT_REQUEST]
    mixed = "कृपया माझ्या खात्याची शिल्लक सांगा आणि कार्ड 987654321 चे शेवटचे ३ व्यवहार पाठवा"
    assert [i for i, _ in _intents(mixed)] == [IntentType.BALANCE_ENQUIRY, IntentType.CARD_TRANSACTIONS]


def test_marathi_entities():
    text = clean_email_text("एप्रिल २०२६ चे स्टेटमेंट, कार्ड ९८७६५४३२१ चे शेवटचे ३ व्यवहार")
    assert extract_period(text)[:2] == (date(2026, 4, 1), date(2026, 4, 30))
    assert extract_count(text) == 3
    assert {(f.kind, f.value) for f in find_numbers("खाते 1234567890 आणि कार्ड 987654321")} == \
        {("account", "1234567890"), ("card", "987654321")}


def test_devanagari_sentiment_and_escalation():
    angry = analyse_sentiment(clean_email_text("मी खूप नाराज आहे, तिसऱ्यांदा लिहितोय! लोकपालकडे तक्रार करीन"))
    assert angry["escalate_to_human"] and angry["label"].value == "NEGATIVE"
    assert analyse_sentiment("कृपया तात्काळ माझे स्टेटमेंट पाठवा")["is_urgent"]


async def test_marathi_email_gets_marathi_reply():
    r = await run(make_email(MR_BALANCE))
    assert r.language == "mr" and r.reply_language == "mr"
    assert r.requests[0].status == RequestStatus.SUCCESS and r.requests[0].account_number == "1234567890"
    assert "खात्यातील शिल्लक" in r.response.body and "INR 1,25,000.00" in r.response.body
    assert "XXXX7890" in r.response.body and "1234567890" not in r.response.body
    assert r.response.body.startswith("प्रिय")


async def test_hindi_email_partial_failure_reply():
    r = await run(make_email("कृपया मेरे खाते 1234567890 का बैलेंस बताएं और कार्ड 999999999 के अंतिम 2 लेनदेन भेजें"))
    assert r.reply_language == "hi"
    assert [q.status for q in r.requests] == [RequestStatus.SUCCESS, RequestStatus.INVALID]
    assert "सत्यापित नहीं हो सका" in r.response.body and "INR 1,25,000.00" in r.response.body


async def test_missing_info_is_translated():
    r = await run(make_email("कृपया माझ्या क्रेडिट कार्डचे व्यवहार पाठवा", sender="rahul.verma@example.com"))
    assert r.requests[0].status == RequestStatus.MISSING_INFO
    assert "क्रेडिट कार्ड क्रमांक" in r.response.body


async def test_latin_hinglish_falls_back_to_english_reply():
    r = await run(make_email("mera khata balance batao please, account 1234567890 hai"))
    assert r.reply_language == "en"
