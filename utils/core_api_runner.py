"""Start the mock core banking API in the background when it isn't running."""
import atexit
import logging
import subprocess
import sys
import time

import httpx

from config.settings import PROJECT_ROOT, get_settings

log = logging.getLogger(__name__)
_process: subprocess.Popen | None = None


def core_api_is_up() -> bool:
    try:
        return httpx.get(f"{get_settings().core_api_url}/health", timeout=1.0).status_code == 200
    except httpx.HTTPError:
        return False


def ensure_core_api_running(timeout_seconds: float = 60.0) -> bool:
    """Returns True when the API is reachable (started here or already up)."""
    global _process
    settings = get_settings()
    if core_api_is_up():
        return True
    if not settings.core_api_autostart:
        log.error("Core API is not running at %s and autostart is disabled", settings.core_api_url)
        return False

    log.info("Starting mock core banking API on %s", settings.core_api_url)
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    log_file = open(settings.log_dir / "core_api.log", "a", encoding="utf-8")  # noqa: SIM115
    _process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "apis.core_banking_api:app",
         "--host", settings.core_api_host, "--port", str(settings.core_api_port), "--log-level", "warning"],
        cwd=PROJECT_ROOT, stdout=log_file, stderr=subprocess.STDOUT,
    )
    atexit.register(stop_core_api)

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if core_api_is_up():
            return True
        if _process.poll() is not None:
            log.error("Core API process exited early; see logs/core_api.log")
            return False
        time.sleep(0.3)
    return False


def stop_core_api() -> None:
    global _process
    if _process and _process.poll() is None:
        _process.terminate()
        try:
            _process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _process.kill()
    _process = None
