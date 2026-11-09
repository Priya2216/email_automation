"""
Extract text from downloaded attachments.

  * PDF            -> pypdf text layer (falls back to OCR of nothing: scanned
                      PDFs need Tesseract + a rasteriser, see README)
  * Images (any)   -> OCR with Tesseract via pytesseract (optional dependency)
  * .txt/.csv/.eml -> read as text
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

log = logging.getLogger(__name__)

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".heic"}
TEXT_EXTS = {".txt", ".csv", ".log", ".md"}


def tesseract_available() -> bool:
    return shutil.which("tesseract") is not None


def extract_text(path: str | Path, content_type: str = "") -> tuple[str, str]:
    """Returns (text, method)."""
    path = Path(path)
    ext = path.suffix.lower()
    try:
        if ext == ".pdf" or content_type == "application/pdf":
            from pypdf import PdfReader

            text = "\n".join((page.extract_text() or "") for page in PdfReader(str(path)).pages)
            return text.strip(), "pdf-text"
        if ext in IMAGE_EXTS or content_type.startswith("image/"):
            if not tesseract_available():
                log.warning("OCR skipped for %s: Tesseract is not installed", path.name)
                return "", "ocr-unavailable"
            import pytesseract
            from PIL import Image

            with Image.open(path) as img:
                return pytesseract.image_to_string(img.convert("RGB")).strip(), "ocr"
        if ext in TEXT_EXTS or content_type.startswith("text/"):
            return path.read_text(encoding="utf-8", errors="ignore").strip(), "plain-text"
    except Exception as exc:  # noqa: BLE001 - a broken attachment must not stop the email
        log.warning("Could not extract text from %s: %s", path.name, exc)
        return "", "failed"
    return "", "unsupported"
