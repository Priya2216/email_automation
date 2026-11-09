"""
Email Reader / Sender module.

Input:
  * folder mode (default): parse .eml files from sample_emails/inbox
  * imap mode: fetch UNSEEN messages from a real mailbox (IMAP over SSL)
Output:
  * file mode (default): write the reply as .eml + .txt into outbox/
  * smtp mode: send the reply with SMTP (STARTTLS)

Attachments of incoming mails are downloaded to outbox/downloaded_attachments/<email_id>/.
"""
from __future__ import annotations

import imaplib
import logging
import mimetypes
import re
import smtplib
from datetime import datetime
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import getaddresses, make_msgid, parsedate_to_datetime
from html import unescape
from pathlib import Path

from config.settings import get_settings
from models.schemas import Attachment, IncomingEmail, ResponseEmail
from utils.attachments import extract_text

log = logging.getLogger(__name__)


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value)[:80] or "file"


def _html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>", "\n", html)
    return unescape(re.sub(r"<[^>]+>", " ", html))


def parse_email_bytes(raw: bytes, email_id: str) -> IncomingEmail:
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    sender_name, sender = (getaddresses([str(msg.get("From", ""))]) or [("", "")])[0]

    body_part = msg.get_body(preferencelist=("plain", "html"))
    body = ""
    if body_part is not None:
        body = body_part.get_content()
        if body_part.get_content_type() == "text/html":
            body = _html_to_text(body)

    settings = get_settings()
    att_dir = settings.attachments_dir / _safe_name(email_id)
    attachments: list[Attachment] = []
    for part in msg.iter_attachments():
        filename = _safe_name(part.get_filename() or f"attachment{len(attachments) + 1}")
        payload = part.get_payload(decode=True) or b""
        att_dir.mkdir(parents=True, exist_ok=True)
        saved = att_dir / filename
        saved.write_bytes(payload)  # "Download the attached document"
        text, method = extract_text(saved, part.get_content_type())
        attachments.append(Attachment(filename=filename, content_type=part.get_content_type(),
                                      saved_path=str(saved), size_bytes=len(payload),
                                      extracted_text=text, extraction_method=method))
    try:
        received = parsedate_to_datetime(msg["Date"]) if msg["Date"] else datetime.now()
    except (TypeError, ValueError):
        received = datetime.now()

    return IncomingEmail(email_id=email_id, message_id=msg.get("Message-ID", ""), sender=sender.lower(),
                         sender_name=sender_name, subject=str(msg.get("Subject", "")), body=body,
                         received_at=received, attachments=attachments)


def read_inbox_folder(folder: Path | None = None) -> list[IncomingEmail]:
    folder = folder or get_settings().inbox_dir
    return [parse_email_bytes(path.read_bytes(), path.stem) for path in sorted(folder.glob("*.eml"))]


def read_imap_unseen(limit: int = 20) -> list[IncomingEmail]:
    s = get_settings()
    emails = []
    with imaplib.IMAP4_SSL(s.imap_host, s.imap_port) as imap:
        imap.login(s.imap_user, s.imap_password)
        imap.select(s.imap_folder)
        _, data = imap.search(None, "UNSEEN")
        for num in data[0].split()[:limit]:
            _, msg_data = imap.fetch(num, "(RFC822)")
            emails.append(parse_email_bytes(msg_data[0][1], f"imap-{num.decode()}"))
    return emails


def build_message(response: ResponseEmail) -> EmailMessage:
    s = get_settings()
    msg = EmailMessage()
    msg["From"] = s.support_from_address
    msg["To"] = response.to
    msg["Subject"] = response.subject
    msg["Message-ID"] = make_msgid(domain="demobank.example")
    if response.in_reply_to:
        msg["In-Reply-To"] = response.in_reply_to
        msg["References"] = response.in_reply_to
    msg.set_content(response.body)
    for file_path in response.attachments:
        path = Path(file_path)
        ctype, _ = mimetypes.guess_type(path.name)
        maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
        msg.add_attachment(path.read_bytes(), maintype=maintype, subtype=subtype, filename=path.name)
    return msg


def deliver(response: ResponseEmail, email_id: str) -> dict[str, str]:
    """Write the reply to outbox/ (always) and send it via SMTP when configured."""
    s = get_settings()
    s.outbox_dir.mkdir(parents=True, exist_ok=True)
    msg = build_message(response)
    eml_path = s.outbox_dir / f"{_safe_name(email_id)}_response.eml"
    txt_path = s.outbox_dir / f"{_safe_name(email_id)}_response.txt"
    eml_path.write_bytes(msg.as_bytes())
    txt_path.write_text(f"To: {response.to}\nSubject: {response.subject}\n"
                        f"Attachments: {', '.join(Path(a).name for a in response.attachments) or '-'}\n\n"
                        f"{response.body}\n", encoding="utf-8")
    files = {"eml": str(eml_path), "txt": str(txt_path)}

    if s.send_mode == "smtp":
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=30) as smtp:
            smtp.starttls()
            smtp.login(s.smtp_user, s.smtp_password)
            smtp.send_message(msg)
        files["smtp"] = f"sent to {response.to}"
    return files
