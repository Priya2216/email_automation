"""
Voice channel: speech-to-text with faster-whisper (optional dependency).

    pip install faster-whisper          # downloads the Whisper model on first use (WHISPER_MODEL, default 'base')

Without it the voice endpoint still works when the caller sends a ready transcript (e.g. from a telephony
platform that already does speech-to-text). Text-to-speech is left to the telephony platform: the API returns
`reply_text` in a spoken style for exactly that purpose.
"""
from __future__ import annotations

from functools import lru_cache

from config.settings import get_settings


class SpeechUnavailable(RuntimeError):
    pass


def stt_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


@lru_cache
def _model():
    from faster_whisper import WhisperModel

    return WhisperModel(get_settings().whisper_model, compute_type="int8")


def transcribe(path: str) -> tuple[str, str]:
    """Returns (text, detected language code)."""
    if not stt_available():
        raise SpeechUnavailable("faster-whisper is not installed (pip install faster-whisper)")
    segments, info = _model().transcribe(path, task="transcribe")
    return " ".join(seg.text.strip() for seg in segments).strip(), info.language
