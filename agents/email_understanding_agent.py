"""
Email Understanding Agent - reads the email and its downloaded attachments,
cleans the text, detects the language and writes a short summary.
"""
from __future__ import annotations

import re

from agents.audit_agent import audit
from models.schemas import EmailUnderstanding
from utils.llm import ask_json
from utils.masking import mask_text
from utils.text_utils import clean_email_text, detect_language, strip_sign_off
from workflows.state import WorkflowState


class EmailUnderstandingAgent:
    name = "email_understanding_agent"

    async def run(self, state: WorkflowState) -> dict:
        email = state["email"]
        clean = clean_email_text(email.body)
        language = detect_language(clean)

        attachment_text = "\n\n".join(
            f"[Attachment: {a.filename}]\n{a.extracted_text}" for a in email.attachments if a.extracted_text
        )

        llm_agents = []
        reply = await ask_json("email_understanding", email_text=clean[:4000])
        if isinstance(reply, dict) and reply.get("summary"):
            summary = mask_text(str(reply["summary"]))
            llm_agents.append(self.name)
        else:
            body = strip_sign_off(clean)
            body = re.sub(r"^(dear|hi|hello)\b[^\n,]*[,\n]\s*", "", body, flags=re.I)
            first = re.split(r"(?<=[.!?])\s+", body.strip(), maxsplit=1)[0]
            summary = mask_text(first[:200])

        understanding = EmailUnderstanding(clean_text=clean, summary=summary, language=language,
                                           attachment_text=attachment_text)
        entry = audit(state, self.name, "EMAIL_UNDERSTOOD", {
            "subject": email.subject, "language": language, "summary": summary,
            "attachments": [{"file": a.filename, "type": a.content_type, "method": a.extraction_method,
                             "chars_extracted": len(a.extracted_text)} for a in email.attachments],
        })
        return {"understanding": understanding, "trace": [entry], "llm_agents": llm_agents}
