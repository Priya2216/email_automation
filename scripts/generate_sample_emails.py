"""
Generate the sample test emails (.eml) in sample_emails/inbox/.

    python -m scripts.generate_sample_emails

Covers the 5 scenarios of the task + attachments (PDF, image), sentiment,
missing details, unsupported requests and core-system failures.
"""
from __future__ import annotations

import io
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
from datetime import datetime, timedelta

from config.settings import PROJECT_ROOT

INBOX = PROJECT_ROOT / "sample_emails" / "inbox"


def _pdf_bytes(lines: list[str]) -> bytes:
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, lines[0], new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=11)
    for line in lines[1:]:
        pdf.cell(0, 8, line, new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


def _png_bytes(lines: list[str]) -> bytes:
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (900, 60 + 50 * len(lines)), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 30)
    except OSError:
        font = ImageFont.load_default()
    for i, line in enumerate(lines):
        draw.text((30, 30 + 50 * i), line, fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


EMAILS = [
    ("01_balance_enquiry", "John Doe <john.doe@example.com>", "Balance enquiry",
     "Dear Team,\n\nPlease provide my savings account balance for account number 1234567890.\n\n"
     "Regards,\nJohn Doe", None),
    ("02_card_transactions", "John Doe <john.doe@example.com>", "Credit card transactions",
     "Hello,\n\nPlease share my last 5 credit card transactions for card 4567XXXX8901.\n\nThanks,\nJohn", None),
    ("03_statement_request", "John Doe <john.doe@example.com>", "Bank statement",
     "Hi,\n\nKindly send my bank statement for April 2026.\n\nRegards,\nJohn Doe", None),
    ("04_multiple_requests", "John Doe <john.doe@example.com>", "Balance and card transactions",
     "Dear Sir/Madam,\n\nPlease share my account balance for account 1234567890 and also send last 3 "
     "transactions of my credit card 987654321.\n\nThank you,\nJohn Doe", None),
    ("05_partial_validation_failure", "John Doe <john.doe@example.com>", "Balance and card details",
     "Hello,\n\nPlease provide balance for account 1234567890 and card transactions for card 999999999."
     "\n\nRegards,\nJohn Doe", None),
    ("06_pdf_attachment", "Priya Sharma <priya.sharma@example.com>", "Request form attached",
     "Dear Team,\n\nPlease process the request in the attached form.\n\nRegards,\nPriya Sharma",
     ("request_form.pdf", "application/pdf",
      _pdf_bytes(["Customer Service Request Form", "Customer name: Priya Sharma",
                  "Request type: Account balance enquiry and account statement",
                  "Savings account number: 2345678901", "Statement period: September 2026"]))),
    ("07_angry_urgent_customer", "John Doe <john.doe@example.com>", "URGENT - third time asking!!",
     "This is the THIRD time I am writing and still no response!! This is unacceptable.\n\n"
     "I need the last 3 transactions on my credit card 4567123456788901 IMMEDIATELY, I think there is "
     "an unauthorized charge. If this is not resolved today I will escalate to the banking ombudsman.\n\n"
     "John Doe", None),
    ("08_missing_details", "Rahul Verma <rahul.verma@example.com>", "Need details",
     "Hi team,\n\nPlease send my credit card transactions and also my account statement.\n\n"
     "Thanks,\nRahul", None),
    ("09_unsupported_request", "Priya Sharma <priya.sharma@example.com>", "Cheque book and balance",
     "Hello,\n\nKindly issue a new cheque book for my account 2345678901 and please also tell me the "
     "balance of the same account.\n\nThanks and regards,\nPriya", None),
    ("10_core_system_timeout", "John Doe <john.doe@example.com>", "Balance please",
     "Hi,\n\nCan you share the balance of my account 5555555555 and the balance of account 1234567890?"
     "\n\nRegards,\nJohn Doe", None),
    ("11_image_attachment", "Rahul Verma <rahul.verma@example.com>", "Statement request (scan attached)",
     "Hello,\n\nPlease send my account statement for the period mentioned in the attached scan.\n\nRahul Verma",
     ("scan.png", "image/png",
      _png_bytes(["STATEMENT REQUEST", "Account number: 3456789012", "Period: 01/08/2026 to 31/08/2026"]))),
    ("12_multiple_accounts_one_closed", "John Doe <john.doe@example.com>", "Balances",
     "Dear team, please let me know the balance of accounts 1234567890 and 1111222233. "
     "Thank you for the great service!\n\nJohn Doe", None),
]


def main() -> None:
    INBOX.mkdir(parents=True, exist_ok=True)
    base_time = datetime(2026, 11, 3, 9, 0).astimezone()
    for i, (name, sender, subject, body, attachment) in enumerate(EMAILS):
        msg = EmailMessage()
        msg["From"] = sender
        msg["To"] = "customer.support@demobank.example"
        msg["Subject"] = subject
        msg["Date"] = format_datetime(base_time + timedelta(minutes=7 * i))
        msg["Message-ID"] = make_msgid(domain="example.com")
        msg.set_content(body)
        if attachment:
            filename, ctype, data = attachment
            maintype, subtype = ctype.split("/")
            msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
        (INBOX / f"{name}.eml").write_bytes(msg.as_bytes())
        print(f"wrote {name}.eml")


if __name__ == "__main__":
    main()
