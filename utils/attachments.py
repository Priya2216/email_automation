"""
Extract text from downloaded attachments (OCR for scans and photos).

  * PDF with a text layer -> pypdf
  * Scanned PDF           -> pages rendered to images (pdf2image/poppler, or PyMuPDF) + Tesseract OCR
  * Images (any)          -> Tesseract OCR via pytesseract, after pre-processing
                             (grayscale, up-scaling, contrast) and in English + Hindi + Marathi when the
                             language packs are installed
  * .txt/.csv/.md/.log    -> read as text

OCR also returns the mean word confidence (0-100). It flows into the confidence score: a blurry photo of a
request form sends the reply to a human reviewer instead of acting on a mis-read account number.
Everything here is optional - a missing OCR component never stops the email from being processed.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Optional

from config.settings import get_settings
from utils.text_utils import normalize_digits

log = logging.getLogger(__name__)

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".heic"}
TEXT_EXTS = {".txt", ".csv", ".log", ".md"}
MIN_PDF_TEXT_CHARS = 20  # fewer characters than this -> the PDF is a scan


def tesseract_available() -> bool:
    return shutil.which("tesseract") is not None


def ocr_languages() -> str:
    """Configured OCR languages that are really installed (always falls back to English)."""
    wanted = [lang for lang in get_settings().ocr_languages.split("+") if lang]
    try:
        import pytesseract

        installed = set(pytesseract.get_languages(config=""))
    except Exception:  # noqa: BLE001
        installed = {"eng"}
    return "+".join(lang for lang in wanted if lang in installed) or "eng"


def _preprocess(img):
    from PIL import ImageOps

    img = img.convert("L")
    if img.width < 1200:  # small photos / screenshots OCR much better when enlarged
        scale = min(3.0, 1200 / max(img.width, 1))
        img = img.resize((int(img.width * scale), int(img.height * scale)))
    return ImageOps.autocontrast(img)


def ocr_image(img) -> tuple[str, float]:
    """(text, mean word confidence 0-100) of a PIL image."""
    import pytesseract

    img, lang = _preprocess(img), ocr_languages()
    text = pytesseract.image_to_string(img, lang=lang).strip()
    data = pytesseract.image_to_data(img, lang=lang, output_type=pytesseract.Output.DICT)
    confs = []
    for word, conf in zip(data["text"], data["conf"]):
        try:
            conf = float(conf)
        except (TypeError, ValueError):
            continue
        if word.strip() and conf >= 0:
            confs.append(conf)
    return normalize_digits(text), round(sum(confs) / len(confs), 1) if confs else 0.0


def _render_pdf_pages(path: Path, max_pages: int) -> list:
    """Rasterise PDF pages to PIL images (pdf2image if poppler is installed, else PyMuPDF)."""
    if shutil.which("pdftoppm"):
        try:
            from pdf2image import convert_from_path

            return convert_from_path(str(path), dpi=200, first_page=1, last_page=max_pages)
        except ImportError:
            pass
    try:
        import pymupdf
    except ImportError:
        try:
            import fitz as pymupdf  # older PyMuPDF
        except ImportError:
            return []
    from PIL import Image

    pages = []
    with pymupdf.open(str(path)) as doc:
        for page in list(doc)[:max_pages]:
            pix = page.get_pixmap(dpi=200)
            pages.append(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
    return pages


def _ocr_pdf(path: Path) -> tuple[str, Optional[float]]:
    pages = _render_pdf_pages(path, get_settings().ocr_max_pdf_pages)
    if not pages:
        log.warning("Scanned PDF %s: no PDF rasteriser (pdf2image+poppler or pymupdf) installed", path.name)
        return "", None
    texts, confs = [], []
    for img in pages:
        text, conf = ocr_image(img)
        texts.append(text)
        confs.append(conf)
    return "\n".join(texts).strip(), round(sum(confs) / len(confs), 1)


def extract_text_with_confidence(path: str | Path, content_type: str = "") -> tuple[str, str, Optional[float]]:
    """Returns (text, method, ocr_confidence). ocr_confidence is None unless OCR was used."""
    path = Path(path)
    ext = path.suffix.lower()
    try:
        if ext == ".pdf" or content_type == "application/pdf":
            from pypdf import PdfReader

            text = "\n".join((page.extract_text() or "") for page in PdfReader(str(path)).pages).strip()
            if len(text) >= MIN_PDF_TEXT_CHARS:
                return normalize_digits(text), "pdf-text", None
            if not tesseract_available():
                log.warning("Scanned PDF %s skipped: Tesseract is not installed", path.name)
                return text, "ocr-unavailable", None
            ocr_text, conf = _ocr_pdf(path)
            if ocr_text:
                return ocr_text, "pdf-ocr", conf
            return text, "ocr-unavailable", None
        if ext in IMAGE_EXTS or content_type.startswith("image/"):
            if not tesseract_available():
                log.warning("OCR skipped for %s: Tesseract is not installed", path.name)
                return "", "ocr-unavailable", None
            from PIL import Image

            with Image.open(path) as img:
                text, conf = ocr_image(img)
            return text, "ocr", conf
        if ext in TEXT_EXTS or content_type.startswith("text/"):
            return normalize_digits(path.read_text(encoding="utf-8", errors="ignore").strip()), "plain-text", None
    except Exception as exc:  # noqa: BLE001 - a broken attachment must not stop the email
        log.warning("Could not extract text from %s: %s", path.name, exc)
        return "", "failed", None
    return "", "unsupported", None


def extract_text(path: str | Path, content_type: str = "") -> tuple[str, str]:
    """Backwards compatible: (text, method)."""
    text, method, _ = extract_text_with_confidence(path, content_type)
    return text, method
