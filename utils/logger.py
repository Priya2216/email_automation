"""Logging framework: console + rotating file, with sensitive-data masking."""
import logging
from logging.handlers import RotatingFileHandler

from config.settings import get_settings
from utils.masking import MaskingFilter

_configured = False


def setup_logging() -> None:
    global _configured
    if _configured:
        return
    settings = get_settings()
    settings.log_dir.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)-28s | %(message)s")
    masking = MaskingFilter()

    file_handler = RotatingFileHandler(settings.log_dir / "app.log", maxBytes=5_000_000,
                                       backupCount=3, encoding="utf-8")
    file_handler.setFormatter(fmt)
    file_handler.addFilter(masking)

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(levelname)-7s %(name)s: %(message)s"))
    console.addFilter(masking)
    console.setLevel(logging.WARNING)

    root = logging.getLogger()
    root.setLevel(settings.log_level.upper())
    root.addHandler(file_handler)
    root.addHandler(console)
    for noisy in ("httpx", "httpcore", "mcp", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _configured = True
