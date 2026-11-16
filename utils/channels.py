"""
Omnichannel support: the same agent pipeline serves email, web chat, SMS, WhatsApp and voice.

    channel   input                      reply
    email     .eml / JSON                formal email (with PDF attachments)
    chat      JSON {text}                compact multi-line message
    whatsapp  JSON {text}                compact multi-line message
    sms       JSON {text}                one short paragraph, max SMS_MAX_CHARS
    voice     audio (speech-to-text)     "spoken style" text, ready for a text-to-speech engine

Chat / SMS / WhatsApp / voice customers are identified by their mobile number (sender).
"""
from __future__ import annotations

import re

CHANNELS = ("email", "chat", "sms", "whatsapp", "voice")
SMS_MAX_CHARS = 480


def to_spoken(text: str) -> str:
    """Make a reply easy to read aloud: no symbols, amounts as '... rupees', masked numbers as 'ending ....'"""
    text = re.sub(r"INR\s*([\d,]+)\.00\b", r"\1 rupees", text)
    text = re.sub(r"INR\s*([\d,]+)\.(\d\d)\b", r"\1 rupees and \2 paise", text)
    text = re.sub(r"INR\s*([\d,]+)\b", r"\1 rupees", text)
    text = re.sub(r"(?:XXXX\s?){1,3}(\d{4})", r"ending \1", text)
    text = re.sub(r"\b(\d[\d,\.]*)\s+Dr\b", r"\1 debit", text)
    text = re.sub(r"\b(\d[\d,\.]*)\s+Cr\b", r"\1 credit", text)
    text = text.replace("•", ",").replace(";", ",").replace("(", ", ").replace(")", ",")
    text = re.sub(r"[,;:]\s*\n", ", ", text)
    text = re.sub(r"\s*\n\s*", ". ", text)
    text = re.sub(r"\.\s*\.", ".", text)
    return re.sub(r"\s+", " ", text).strip()


def format_reply(channel: str, body: str) -> str:
    """Final text for the channel."""
    if channel == "sms":
        flat = re.sub(r"\s+", " ", body).strip()
        return flat if len(flat) <= SMS_MAX_CHARS else flat[: SMS_MAX_CHARS - 3].rstrip() + "..."
    if channel == "voice":
        return to_spoken(body)
    return body
