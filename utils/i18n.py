"""
Customer-facing message catalog: English (en), Hindi (hi) and Marathi (mr).

The reply is written in the customer's language. Facts (balances, transactions, dates) still come from the
API data and are rendered by templates, so translation can never change a number. Amounts keep the
"INR 1,25,000.00" format and ASCII digits in every language (unambiguous in banking).

NOTE: these translations are a working draft for the demo. A bank must have them reviewed by a native
speaker / compliance team before production use.
"""
from __future__ import annotations

SUPPORTED = ("en", "hi", "mr")
LANGUAGE_NAMES = {"en": "English", "hi": "Hindi", "mr": "Marathi"}


def reply_language(detected: str, setting: str = "auto") -> str:
    """Language of the reply: the configured one, else the customer's (Latin-script Hindi -> English)."""
    if setting in SUPPORTED:
        return setting
    return detected if detected in SUPPORTED else "en"


_C: dict[str, dict[str, str]] = {
    # ---------------------------------------------------------------- headings
    "greeting": {"en": "Dear {name},", "hi": "प्रिय {name},", "mr": "प्रिय {name},"},
    "label_balance": {"en": "Account Balance", "hi": "खाता शेष राशि", "mr": "खात्यातील शिल्लक"},
    "label_txn": {"en": "Credit Card Transactions", "hi": "क्रेडिट कार्ड लेनदेन", "mr": "क्रेडिट कार्ड व्यवहार"},
    "label_statement": {"en": "Account Statement", "hi": "खाता विवरण", "mr": "खाते उतारा"},
    "label_other": {"en": "Other Request", "hi": "अन्य अनुरोध", "mr": "इतर विनंती"},
    "label_policy": {"en": "Policy Information", "hi": "नीति संबंधी जानकारी", "mr": "धोरणाविषयी माहिती"},
    "label_card_req": {"en": "Credit Card", "hi": "क्रेडिट कार्ड", "mr": "क्रेडिट कार्ड"},
    # ---------------------------------------------------------------- balance
    "f_account_number": {"en": "Account Number", "hi": "खाता संख्या", "mr": "खाते क्रमांक"},
    "f_account_type": {"en": "Account Type", "hi": "खाता प्रकार", "mr": "खात्याचा प्रकार"},
    "f_available": {"en": "Available Balance", "hi": "उपलब्ध शेष राशि", "mr": "उपलब्ध शिल्लक"},
    "f_ledger": {"en": "Ledger Balance", "hi": "लेजर शेष राशि", "mr": "लेजर शिल्लक"},
    "f_as_on": {"en": "As on", "hi": "दिनांक/समय", "mr": "दिनांक/वेळ"},
    "primary_note": {"en": " (your registered primary account)", "hi": " (आपका पंजीकृत प्राथमिक खाता)",
                     "mr": " (तुमचे नोंदणीकृत प्राथमिक खाते)"},
    # ---------------------------------------------------------------- card transactions
    "txn_header": {"en": "Credit Card Transactions - Card {card} ({kind}), last {n}:",
                   "hi": "क्रेडिट कार्ड लेनदेन - कार्ड {card} ({kind}), अंतिम {n}:",
                   "mr": "क्रेडिट कार्ड व्यवहार - कार्ड {card} ({kind}), शेवटचे {n}:"},
    "txn_only": {"en": "(Only {n} transaction(s) are available for this card.)",
                 "hi": "(इस कार्ड के लिए केवल {n} लेनदेन उपलब्ध हैं।)",
                 "mr": "(या कार्डसाठी फक्त {n} व्यवहार उपलब्ध आहेत.)"},
    "txn_none": {"en": "No transactions found on this card.", "hi": "इस कार्ड पर कोई लेनदेन नहीं मिला।",
                 "mr": "या कार्डवर कोणताही व्यवहार आढळला नाही."},
    "txn_limit": {"en": "Available credit limit", "hi": "उपलब्ध क्रेडिट सीमा", "mr": "उपलब्ध क्रेडिट मर्यादा"},
    # ---------------------------------------------------------------- statement
    "stmt_header": {"en": "Account Statement - {period}:", "hi": "खाता विवरण - {period}:", "mr": "खाते उतारा - {period}:"},
    "stmt_period": {"en": "Period", "hi": "अवधि", "mr": "कालावधी"},
    "stmt_opening": {"en": "Opening Balance", "hi": "प्रारंभिक शेष", "mr": "सुरुवातीची शिल्लक"},
    "stmt_closing": {"en": "Closing Balance", "hi": "अंतिम शेष", "mr": "अखेरची शिल्लक"},
    "stmt_txns": {"en": "Transactions", "hi": "लेनदेन", "mr": "व्यवहार"},
    "stmt_ref": {"en": "Statement Reference", "hi": "विवरण संदर्भ", "mr": "उताऱ्याचा संदर्भ"},
    "stmt_attached": {"en": "The statement is attached to this email as a PDF.",
                      "hi": "विवरण इस ईमेल के साथ PDF के रूप में संलग्न है।",
                      "mr": "हा उतारा या ईमेलसोबत PDF स्वरूपात जोडला आहे."},
    "stmt_emailed": {"en": "The statement PDF has been sent to your registered email address.",
                     "hi": "विवरण की PDF आपके पंजीकृत ईमेल पते पर भेज दी गई है।",
                     "mr": "उताऱ्याची PDF तुमच्या नोंदणीकृत ईमेल पत्त्यावर पाठवली आहे."},
    # ---------------------------------------------------------------- partial-failure messages
    "invalid_card": {
        "en": "{label} Request:\n  The credit card number provided ({n}) could not be validated. "
              "Kindly recheck the card details and resend your request.",
        "hi": "{label} अनुरोध:\n  दिया गया क्रेडिट कार्ड नंबर ({n}) सत्यापित नहीं हो सका। "
              "कृपया कार्ड विवरण जाँचकर अपना अनुरोध पुनः भेजें।",
        "mr": "{label} विनंती:\n  दिलेला क्रेडिट कार्ड क्रमांक ({n}) पडताळता आला नाही. "
              "कृपया कार्डचा तपशील तपासून तुमची विनंती पुन्हा पाठवा."},
    "invalid_account": {
        "en": "{label} Request:\n  The account number provided ({n}) could not be validated. "
              "Kindly recheck the account details and resend your request.",
        "hi": "{label} अनुरोध:\n  दिया गया खाता नंबर ({n}) सत्यापित नहीं हो सका। "
              "कृपया खाता विवरण जाँचकर अपना अनुरोध पुनः भेजें।",
        "mr": "{label} विनंती:\n  दिलेला खाते क्रमांक ({n}) पडताळता आला नाही. "
              "कृपया खात्याचा तपशील तपासून तुमची विनंती पुन्हा पाठवा."},
    "missing": {
        "en": "{label} Request:\n  To process this request we need your {missing}. "
              "Kindly reply to this email with the details.",
        "hi": "{label} अनुरोध:\n  इस अनुरोध को पूरा करने के लिए हमें आपका {missing} चाहिए। "
              "कृपया इस संदेश का उत्तर देकर विवरण भेजें।",
        "mr": "{label} विनंती:\n  ही विनंती पूर्ण करण्यासाठी आम्हाला तुमचा {missing} हवा आहे. "
              "कृपया या संदेशाला उत्तर देऊन तपशील पाठवा."},
    "api_error": {
        "en": "{label} Request:\n  We could not retrieve this information{ref} right now due to a temporary "
              "system issue. Our team has been notified and will share the details with you shortly "
              "(reference {run}).",
        "hi": "{label} अनुरोध:\n  अस्थायी तकनीकी समस्या के कारण हम यह जानकारी{ref} अभी प्राप्त नहीं कर सके। "
              "हमारी टीम को सूचित कर दिया गया है और वह शीघ्र ही आपको विवरण भेजेगी (संदर्भ {run})।",
        "mr": "{label} विनंती:\n  तात्पुरत्या तांत्रिक अडचणीमुळे आम्ही ही माहिती{ref} आत्ता मिळवू शकलो नाही. "
              "आमच्या टीमला कळवले आहे आणि ते लवकरच तुम्हाला तपशील पाठवतील (संदर्भ {run})."},
    "ref_card": {"en": " for card {n}", "hi": " कार्ड {n} के लिए", "mr": " कार्ड {n} साठी"},
    "ref_account": {"en": " for account {n}", "hi": " खाता {n} के लिए", "mr": " खाते {n} साठी"},
    "unsupported": {
        "en": "Other Request:\n  Your request (\"{span}\") cannot be completed over email automatically. "
              "It has been forwarded to our customer support team, who will contact you.",
        "hi": "अन्य अनुरोध:\n  आपका अनुरोध (\"{span}\") ईमेल द्वारा स्वचालित रूप से पूरा नहीं किया जा सकता। "
              "इसे हमारी ग्राहक सहायता टीम को भेज दिया गया है, जो आपसे संपर्क करेगी।",
        "mr": "इतर विनंती:\n  तुमची विनंती (\"{span}\") आपोआप पूर्ण करता येत नाही. "
              "ती आमच्या ग्राहक सेवा टीमकडे पाठवली आहे, ते तुमच्याशी संपर्क साधतील."},
    "policy_none": {
        "en": "Policy Information:\n  We could not find a policy that answers your question (\"{span}\"). "
              "It has been forwarded to our customer support team, who will contact you.",
        "hi": "नीति संबंधी जानकारी:\n  हमें आपके प्रश्न (\"{span}\") से संबंधित नीति नहीं मिली। "
              "इसे हमारी ग्राहक सहायता टीम को भेज दिया गया है, जो आपसे संपर्क करेगी।",
        "mr": "धोरणाविषयी माहिती:\n  तुमच्या प्रश्नाशी (\"{span}\") संबंधित धोरण आम्हाला सापडले नाही. "
              "तो आमच्या ग्राहक सेवा टीमकडे पाठवला आहे, ते तुमच्याशी संपर्क साधतील."},
    "policy_lead": {"en": "", "hi": "हमारी नीति के अनुसार (अंग्रेज़ी में):", "mr": "आमच्या धोरणानुसार (इंग्रजीत):"},
    "policy_source": {"en": "Source", "hi": "स्रोत", "mr": "स्रोत"},
    # ---------------------------------------------------------------- opening / closing
    "open_negative": {"en": "We sincerely apologise for the inconvenience you have experienced.",
                      "hi": "हमें हुई असुविधा के लिए हम हृदय से क्षमा चाहते हैं।",
                      "mr": "तुम्हाला झालेल्या गैरसोयीबद्दल आम्ही मनापासून दिलगीर आहोत."},
    "open_urgent": {"en": "We understand the urgency of your request and have prioritised it.",
                    "hi": "हम आपके अनुरोध की तात्कालिकता को समझते हैं और इसे प्राथमिकता दी गई है।",
                    "mr": "तुमच्या विनंतीची निकड आम्ही समजतो आणि तिला प्राधान्य दिले आहे."},
    "open_neutral": {"en": "Thank you for contacting us.", "hi": "हमसे संपर्क करने के लिए धन्यवाद।",
                     "mr": "आमच्याशी संपर्क साधल्याबद्दल धन्यवाद."},
    "tail_all": {"en": " We have processed your request successfully.",
                 "hi": " हमने आपका अनुरोध सफलतापूर्वक संसाधित कर दिया है।",
                 "mr": " आम्ही तुमची विनंती यशस्वीरित्या पूर्ण केली आहे."},
    "tail_any": {"en": " We have processed your request. Please find the details below.",
                 "hi": " हमने आपका अनुरोध संसाधित किया है। कृपया विवरण नीचे देखें।",
                 "mr": " आम्ही तुमची विनंती हाताळली आहे. कृपया तपशील खाली पाहा."},
    "tail_none": {"en": " We have reviewed your request; please see the details below.",
                  "hi": " हमने आपके अनुरोध की समीक्षा की है; कृपया विवरण नीचे देखें।",
                  "mr": " आम्ही तुमच्या विनंतीचा आढावा घेतला आहे; कृपया तपशील खाली पाहा."},
    "escalation": {"en": "We have flagged your email as a priority. A senior customer relationship officer "
                         "will contact you within 24 hours.",
                   "hi": "हमने आपके संदेश को प्राथमिकता के रूप में चिह्नित किया है। एक वरिष्ठ ग्राहक संबंध अधिकारी "
                         "24 घंटे के भीतर आपसे संपर्क करेंगे।",
                   "mr": "आम्ही तुमचा संदेश प्राधान्याचा म्हणून नोंदवला आहे. एक वरिष्ठ ग्राहक संबंध अधिकारी "
                         "24 तासांत तुमच्याशी संपर्क साधतील."},
    "security": {"en": "For your security, we will never ask for your PIN, OTP or password over email.",
                 "hi": "आपकी सुरक्षा के लिए: हम ईमेल पर कभी भी आपका PIN, OTP या पासवर्ड नहीं माँगते।",
                 "mr": "तुमच्या सुरक्षिततेसाठी: आम्ही ईमेलवर तुमचा PIN, OTP किंवा पासवर्ड कधीही मागत नाही."},
    "signoff": {"en": "Thank you,\nCustomer Support Team\nDemo Bank Ltd.",
                "hi": "धन्यवाद,\nग्राहक सहायता टीम\nडेमो बैंक लिमिटेड",
                "mr": "धन्यवाद,\nग्राहक सेवा टीम\nडेमो बँक लिमिटेड"},
    "stmt_mail_subject": {"en": "Your account statement", "hi": "आपका खाता विवरण", "mr": "तुमचा खाते उतारा"},
    "stmt_mail_body": {"en": "Dear {name},\n\nPlease find the account statement you requested attached to this email.\n\n"
                             "Thank you,\nCustomer Support Team\nDemo Bank Ltd.",
                       "hi": "प्रिय {name},\n\nआपके द्वारा माँगा गया खाता विवरण इस ईमेल के साथ संलग्न है।\n\n"
                             "धन्यवाद,\nग्राहक सहायता टीम\nडेमो बैंक लिमिटेड",
                       "mr": "प्रिय {name},\n\nतुम्ही मागितलेला खाते उतारा या ईमेलसोबत जोडला आहे.\n\n"
                             "धन्यवाद,\nग्राहक सेवा टीम\nडेमो बँक लिमिटेड"},
    # ---------------------------------------------------------------- compact (chat / SMS / WhatsApp / voice)
    "c_greeting": {"en": "Hi {name},", "hi": "नमस्ते {name},", "mr": "नमस्कार {name},"},
    "c_balance": {"en": "Account {acct}: available balance {avail}, ledger balance {ledger}.",
                  "hi": "खाता {acct}: उपलब्ध शेष राशि {avail}, लेजर शेष राशि {ledger}।",
                  "mr": "खाते {acct}: उपलब्ध शिल्लक {avail}, लेजर शिल्लक {ledger}."},
    "c_txns": {"en": "Card {card}, last {n}: {items}. Available limit {limit}.",
               "hi": "कार्ड {card}, अंतिम {n}: {items}। उपलब्ध सीमा {limit}।",
               "mr": "कार्ड {card}, शेवटचे {n}: {items}. उपलब्ध मर्यादा {limit}."},
    "c_txns_none": {"en": "Card {card}: no transactions found.", "hi": "कार्ड {card}: कोई लेनदेन नहीं मिला।",
                    "mr": "कार्ड {card}: कोणताही व्यवहार आढळला नाही."},
    "c_statement": {"en": "Statement for {period} (closing balance {closing}, ref {ref}).",
                    "hi": "{period} का विवरण (अंतिम शेष {closing}, संदर्भ {ref})।",
                    "mr": "{period} चा उतारा (अखेरची शिल्लक {closing}, संदर्भ {ref})."},
    "c_security": {"en": "Never share your PIN or OTP with anyone.",
                   "hi": "अपना PIN या OTP किसी के साथ साझा न करें।",
                   "mr": "तुमचा PIN किंवा OTP कोणालाही सांगू नका."},
    "c_flagged": {"en": "Your request is flagged as priority; an officer will contact you within 24 hours.",
                  "hi": "आपका अनुरोध प्राथमिकता पर है; एक अधिकारी 24 घंटे में संपर्क करेंगे।",
                  "mr": "तुमची विनंती प्राधान्याची म्हणून नोंदवली आहे; अधिकारी 24 तासांत संपर्क साधतील."},
}

# validation notes are produced in English by the Validation Agent; map them for hi / mr replies
_MISSING_ITEMS = {
    "account number": {"hi": "खाता नंबर", "mr": "खाते क्रमांक"},
    "credit card number": {"hi": "क्रेडिट कार्ड नंबर", "mr": "क्रेडिट कार्ड क्रमांक"},
    "statement period (month/year or date range)": {"hi": "विवरण की अवधि (माह/वर्ष या तारीख सीमा)",
                                                   "mr": "उताऱ्याचा कालावधी (महिना/वर्ष किंवा तारखांची श्रेणी)"},
}


def t(lang: str, key: str, **kwargs) -> str:
    entry = _C[key]
    text = entry.get(lang) or entry["en"]
    return text.format(**kwargs) if kwargs else text


def t_missing(lang: str, item: str) -> str:
    """Translate a 'Missing <item>' note's item into the reply language."""
    return _MISSING_ITEMS.get(item, {}).get(lang, item) if lang != "en" else item
