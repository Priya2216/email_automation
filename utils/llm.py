"""
LLM integration: Llama 3 served locally by Ollama, through LangChain.

`get_llm()` returns a LangChain ChatOllama model when Ollama is reachable and
the model is pulled; otherwise None, and every agent falls back to its
rule-based NLP implementation. That keeps the system working (and testable)
on machines without a GPU / without Ollama.
"""
from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

import httpx

from config.settings import PROJECT_ROOT, get_settings

log = logging.getLogger(__name__)
PROMPTS_DIR = PROJECT_ROOT / "prompts"


@lru_cache
def ollama_available() -> bool:
    settings = get_settings()
    try:
        resp = httpx.get(f"{settings.ollama_base_url}/api/tags", timeout=2.0)
        models = [m.get("name", "") for m in resp.json().get("models", [])]
    except (httpx.HTTPError, ValueError):
        return False
    wanted = settings.ollama_model.split(":")[0]
    found = any(m.split(":")[0] == wanted for m in models)
    if not found:
        log.warning("Ollama is running but model '%s' is not pulled (run: ollama pull %s)",
                    settings.ollama_model, settings.ollama_model)
    return found


@lru_cache
def get_llm():
    settings = get_settings()
    if settings.llm_provider == "rules":
        return None
    if not ollama_available():
        if settings.llm_provider == "ollama":
            log.warning("LLM_PROVIDER=ollama but Ollama is not reachable; using rule-based NLP")
        return None
    from langchain_ollama import ChatOllama

    return ChatOllama(model=settings.ollama_model, base_url=settings.ollama_base_url,
                      temperature=0, format="json", timeout=settings.llm_timeout_seconds)


def llm_enabled() -> bool:
    return get_llm() is not None


def load_prompt(name: str) -> str:
    return Path(PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8")


def _extract_json(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}|\[.*\]", text, re.S)
        if match:
            return json.loads(match.group(0))
        raise


async def ask_json(prompt_name: str, **variables) -> Optional[Any]:
    """Render prompts/<prompt_name>.txt, call Llama 3, parse JSON. None on any failure."""
    llm = get_llm()
    if llm is None:
        return None
    from langchain_core.messages import HumanMessage, SystemMessage

    template = load_prompt(prompt_name)
    system, _, user = template.partition("### USER")
    try:
        user_text = user.format(**variables)
        reply = await llm.ainvoke([SystemMessage(content=system.replace("### SYSTEM", "").strip()),
                                   HumanMessage(content=user_text.strip())])
        return _extract_json(reply.content)
    except Exception as exc:  # noqa: BLE001
        log.warning("LLM call '%s' failed, falling back to rules: %s", prompt_name, exc)
        return None
