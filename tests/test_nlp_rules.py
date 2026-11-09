"""Unit tests: intent detection, entity extraction, sentiment, masking."""
from datetime import date

from models.schemas import IntentType, SentimentLabel
from utils.masking import mask_account, mask_card, mask_text
from utils.nlp_rules import (analyse_sentiment, detect_intents, extract_count, extract_period,
                             find_numbers)
from utils.text_utils import detect_language, format_inr


def intents(text):
    return [d.intent for d in detect_intents(text)]


def test_single_intents():
    assert intents("Please provide my savings account balance for account number 1234567890.") == \
        [IntentType.BALANCE_ENQUIRY]
    assert intents("Please share my last 5 credit card transactions for card 4567XXXX8901.") == \
        [IntentType.CARD_TRANSACTIONS]
    assert intents("Kindly send my bank statement for April 2026.") == [IntentType.STATEMENT_REQUEST]


def test_multiple_intents():
    text = ("Please share my account balance for account 1234567890 and also send last 3 "
            "transactions of my credit card 987654321.")
    assert intents(text) == [IntentType.BALANCE_ENQUIRY, IntentType.CARD_TRANSACTIONS]


def test_unsupported_intent():
    assert IntentType.UNSUPPORTED in intents("Kindly issue a new cheque book for my account 2345678901.")


def test_number_classification():
    found = {(f.kind, f.value) for f in find_numbers(
        "balance for account 1234567890 and transactions for card 987654321 and 4567XXXX8901")}
    assert found == {("account", "1234567890"), ("card", "987654321"), ("card", "4567XXXX8901")}


def test_transaction_count():
    assert extract_count("last 5 credit card transactions") == 5
    assert extract_count("send last three transactions") == 3
    assert extract_count("my transactions") is None


def test_period_parsing():
    assert extract_period("statement for April 2026")[:2] == (date(2026, 4, 1), date(2026, 4, 30))
    assert extract_period("from 01/08/2026 to 15/08/2026")[:2] == (date(2026, 8, 1), date(2026, 8, 15))
    assert extract_period("January and February 2026")[:2] == (date(2026, 1, 1), date(2026, 2, 28))
    start, end, _ = extract_period("last month", today=date(2026, 11, 4))
    assert (start, end) == (date(2026, 10, 1), date(2026, 10, 31))
    assert extract_period("please send it") is None


def test_sentiment():
    assert analyse_sentiment("Please send my balance.")["label"] == SentimentLabel.NEUTRAL
    angry = analyse_sentiment("This is unacceptable! Third time asking. I will escalate to the ombudsman.")
    assert angry["label"] == SentimentLabel.NEGATIVE and angry["escalate_to_human"]
    assert analyse_sentiment("Need my statement urgently please")["label"] == SentimentLabel.URGENT
    assert analyse_sentiment("Thank you for the great service")["label"] == SentimentLabel.POSITIVE


def test_masking():
    assert mask_account("1234567890") == "XXXX7890"
    assert mask_card("4567123456788901") == "XXXX XXXX XXXX 8901"
    masked = mask_text("account 1234567890 and card 4567XXXX8901, ref 2026")
    assert "1234567890" not in masked and "XXXX7890" in masked and "ref 2026" in masked


def test_inr_format():
    assert format_inr(125000) == "INR 1,25,000.00"
    assert format_inr(1520000.5) == "INR 15,20,000.50"
    assert format_inr(386) == "INR 386.00"


def test_language_detection():
    assert detect_language("Please send my account balance. Thank you for your help.") == "en"
    assert detect_language("कृपया मेरे खाते का शेष बताएं और विवरण भेजें।") == "hi"
    assert detect_language("कृपया माझ्या खात्याची शिल्लक सांगा आणि तपशील पाठवा.") == "mr"
    assert detect_language("Por favor, envíeme el saldo de mi cuenta bancaria.") == "es"
    assert detect_language("") == "unknown"
