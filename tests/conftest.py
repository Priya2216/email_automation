"""
Test setup: isolated output folders, a dedicated core API port, rule-based
NLP (deterministic) and one shared MCP session for the whole test run.
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="email-automation-tests-"))
os.environ.update({
    "LLM_PROVIDER": "rules",
    "CORE_API_PORT": "8011",
    "CORE_API_TIMEOUT_SECONDS": "6",
    "OUTBOX_DIR": str(_TMP / "outbox"),
    "ATTACHMENTS_DIR": str(_TMP / "outbox" / "attachments"),
    "STATEMENTS_DIR": str(_TMP / "outbox" / "statements"),
    "LOG_DIR": str(_TMP / "logs"),
    "DATABASE_URL": f"sqlite:///{_TMP / 'logs' / 'audit.db'}",
})
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models.schemas import IncomingEmail  # noqa: E402
from utils.core_api_runner import ensure_core_api_running, stop_core_api  # noqa: E402
from utils.mcp_client import BankingMCPClient  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def core_api():
    assert ensure_core_api_running(), "mock core banking API did not start"
    yield
    stop_core_api()


async def run(email: IncomingEmail):
    """Process one email through the full workflow with its own MCP session.

    (anyio-based MCP sessions must be opened and closed in the same task, so
    a session-wide async fixture is not used.)
    """
    from workflows.email_workflow import process_email

    async with BankingMCPClient() as client:
        return await process_email(email, client)


def make_email(body: str, sender: str = "john.doe@example.com", email_id: str = "test") -> IncomingEmail:
    return IncomingEmail(email_id=email_id, sender=sender, subject="Test", body=body)
