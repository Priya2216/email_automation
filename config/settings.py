"""
Central configuration.

Every value can be overridden with an environment variable or a `.env` file
in the project root (see config/.env.example). Example:

    LLM_PROVIDER=ollama OLLAMA_MODEL=llama3 python main.py
"""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- LLM ---------------------------------------------------------------
    # auto   -> use Llama 3 via Ollama when reachable, otherwise rule-based NLP
    # ollama -> always use Ollama (falls back to rules per call if it errors)
    # rules  -> never call an LLM (deterministic, fastest, used by tests)
    llm_provider: str = "auto"
    
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3"
    llm_timeout_seconds: float = 60.0

    # --- Mock core banking API (FastAPI) -----------------------------------
    core_api_host: str = "127.0.0.1"
    core_api_port: int = 8001
    core_api_timeout_seconds: float = 5.0
    core_api_max_retries: int = 2
    # Chaos testing: probability (0..1) that any core API call returns HTTP 503
    core_api_failure_rate: float = 0.0
    # Start the core API automatically from main.py when it isn't running
    core_api_autostart: bool = True

    # --- Email in / out -----------------------------------------------------
    email_source: str = "folder"  # folder | imap
    inbox_dir: Path = PROJECT_ROOT / "sample_emails" / "inbox"
    outbox_dir: Path = PROJECT_ROOT / "outbox"
    attachments_dir: Path = PROJECT_ROOT / "outbox" / "downloaded_attachments"
    statements_dir: Path = PROJECT_ROOT / "outbox" / "statements"

    imap_host: str = ""
    imap_port: int = 993
    imap_user: str = ""
    imap_password: str = ""
    imap_folder: str = "INBOX"

    send_mode: str = "file"  # file | smtp
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    support_from_address: str = "customer.support@demobank.example"

    # --- Processing -----------------------------------------------------------
    max_parallel_emails: int = 4
    default_transaction_count: int = 5
    max_transaction_count: int = 20

    # --- Audit / logging --------------------------------------------------------
    database_url: str = f"sqlite:///{PROJECT_ROOT / 'logs' / 'audit.db'}"
    log_dir: Path = PROJECT_ROOT / "logs"
    log_level: str = "INFO"

    @property
    def core_api_url(self) -> str:
        return f"http://{self.core_api_host}:{self.core_api_port}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
