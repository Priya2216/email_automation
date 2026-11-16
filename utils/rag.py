"""
RAG (retrieval-augmented generation) over the banking policy knowledge base.

    knowledge/*.md  --chunk by "## heading"-->  PolicyIndex  --search(query)-->  top-k passages

Two interchangeable retrievers (no extra dependency is needed for the default one):
  * BM25 (default)  - pure Python, works offline, supports English + Hindi/Marathi queries
                      (Devanagari terms are mapped to the English policy vocabulary).
  * Embeddings      - when RAG_EMBEDDING_MODEL is set (e.g. nomic-embed-text or the multilingual
                      bge-m3) and Ollama is reachable; otherwise BM25 is used automatically.

The retrieved passages are exposed to the agents through the MCP tool `search_policy`; the Response
Agent then writes a grounded answer from them (LLM when available, extractive otherwise) and always
cites the source document.
"""
from __future__ import annotations

import logging
import math
import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable, Optional

import httpx

from config.settings import get_settings

log = logging.getLogger(__name__)

_STOP = {
    "a", "an", "the", "is", "are", "was", "were", "be", "to", "of", "for", "on", "in", "at", "by", "and", "or",
    "my", "me", "i", "you", "your", "we", "our", "it", "its", "this", "that", "what", "which", "how", "do", "does",
    "can", "could", "would", "will", "please", "kindly", "tell", "about", "with", "from", "any", "if", "when", "there",
    "have", "has", "get", "need", "want", "know", "much", "many",
}

# Hindi/Marathi terms -> English policy vocabulary (cross-lingual fallback for the BM25 retriever)
_DEV_TO_EN = {
    "शुल्क": "fee charges", "चार्ज": "charges fee", "फी": "fee", "दंड": "penalty late", "विलंब": "late payment",
    "उशीर": "late payment", "देर": "late payment", "वार्षिक": "annual fee", "कार्ड": "card", "क्रेडिट": "credit card",
    "ब्लॉक": "block lost stolen card", "हरवले": "lost stolen card block", "हरवला": "lost stolen card block",
    "हरवल": "lost stolen card block", "खो गया": "lost stolen card block", "खो गई": "lost stolen card block",
    "चोरी": "stolen lost card block", "चेकबुक": "cheque book", "चेक बुक": "cheque book", "स्टेटमेंट": "statement",
    "खाते उतारा": "statement", "तक्रार": "complaint grievance", "शिकायत": "complaint grievance",
    "निवारण": "grievance redressal resolution", "फसवणूक": "unauthorized fraud dispute", "धोखाधड़ी": "unauthorized fraud dispute",
    "अनधिकृत": "unauthorized dispute", "विवाद": "dispute chargeback", "व्याज": "interest rate", "ब्याज": "interest rate",
    "कर्ज": "loan", "लोन": "loan", "केवायसी": "kyc documents", "कागदपत्र": "documents required kyc",
    "दस्तावेज": "documents required kyc", "पात्रता": "eligibility", "किमान शिल्लक": "minimum average balance",
    "मिनिमम": "minimum average balance", "न्यूनतम": "minimum average balance", "शिल्लक": "balance",
    "बैलेंस": "balance", "बॅलन्स": "balance", "पत्ता": "address", "पता": "address", "मोबाइल": "mobile number",
    "मोबाईल": "mobile number", "नामांकन": "nominee", "नॉमिनी": "nominee", "एटीएम": "atm", "सुरक्षा": "security",
    "पासवर्ड": "password", "ओटीपी": "otp pin", "किती दिवस": "how many days timeline", "कितने दिन": "how many days timeline",
    "प्रक्रिया": "procedure process", "नियम": "policy terms", "पॉलिसी": "policy", "धोरण": "policy",
}

_TOKEN = re.compile(r"[a-z0-9]+")


def _stem(word: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def tokenize(text: str) -> list[str]:
    text = text.lower()
    extra = [en for dev, en in _DEV_TO_EN.items() if dev in text]
    tokens = _TOKEN.findall(text) + _TOKEN.findall(" ".join(extra))
    return [_stem(t) for t in tokens if t not in _STOP and len(t) > 1]


@dataclass
class Chunk:
    id: str
    source: str     # file name
    title: str      # document title
    heading: str    # section heading
    text: str


def _strip_md(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[#>*`]", "", text)).strip()


def load_chunks(kb_dir: Path) -> list[Chunk]:
    chunks: list[Chunk] = []
    for path in sorted(Path(kb_dir).glob("*.md")):
        title, heading, buf = path.stem.replace("_", " ").title(), "Overview", []

        def flush():
            body = _strip_md(" ".join(buf))
            if body:
                chunks.append(Chunk(f"{path.stem}#{len(chunks)}", path.name, title, heading, body))

        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
            elif line.startswith("## "):
                flush()
                heading, buf = line[3:].strip(), []
            elif not line.startswith(">"):
                buf.append(line)
        flush()
    return chunks


class PolicyIndex:
    """BM25 index with an optional embedding re-ranker."""

    K1, B = 1.5, 0.75

    def __init__(self, chunks: list[Chunk], embed_fn: Optional[Callable[[list[str]], list[list[float]]]] = None):
        self.chunks = chunks
        # the heading and title are repeated so that they weigh more than the body text
        self._docs = [tokenize(f"{c.title} {c.heading} {c.heading} {c.text}") for c in chunks]
        self._tf = [Counter(d) for d in self._docs]
        self._len = [len(d) for d in self._docs]
        self._avg = (sum(self._len) / len(self._len)) if self._len else 1.0
        df: Counter = Counter()
        for d in self._docs:
            df.update(set(d))
        n = len(self._docs)
        self._idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self._embed_fn = embed_fn
        self._vectors: list[list[float]] = []
        self.method = "bm25"
        if embed_fn and chunks:
            try:
                self._vectors = embed_fn([f"{c.title}. {c.heading}. {c.text}" for c in chunks])
                self.method = "embeddings"
            except Exception as exc:  # noqa: BLE001 - never let retrieval setup break the pipeline
                log.warning("Embedding index failed (%s); using BM25", exc)

    # ------------------------------------------------------------------ scoring
    def _bm25(self, query: str) -> list[float]:
        all_terms = list(dict.fromkeys(tokenize(query)))
        terms = [t for t in all_terms if t in self._idf]
        if not terms:
            return [0.0] * len(self.chunks)
        best = sum(self._idf[t] * (self.K1 + 1) for t in terms)  # score of a "perfect" document
        # words the knowledge base has never seen ("cryptocurrency") lower the relevance: the question is
        # probably about something the policies do not cover
        unknown = len(all_terms) - len(terms)
        best += unknown * 0.35 * max(self._idf.values()) * (self.K1 + 1)
        scores = []
        for tf, ln in zip(self._tf, self._len):
            s = 0.0
            for t in terms:
                f = tf.get(t, 0)
                if f:
                    s += self._idf[t] * f * (self.K1 + 1) / (f + self.K1 * (1 - self.B + self.B * ln / self._avg))
            scores.append(min(1.0, s / best))
        return scores

    def _cosine(self, query: str) -> list[float]:
        qv = self._embed_fn([query])[0]
        qn = math.sqrt(sum(x * x for x in qv)) or 1.0
        out = []
        for v in self._vectors:
            vn = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append(max(0.0, sum(a * b for a, b in zip(qv, v)) / (qn * vn)))
        return out

    def search(self, query: str, top_k: int = 3, min_score: float = 0.30) -> list[dict]:
        if not self.chunks or not query.strip():
            return []
        scores = self._bm25(query)
        if self.method == "embeddings":
            try:
                emb = self._cosine(query)
                # hybrid: embeddings carry the meaning, BM25 keeps exact terms (fees, limits, ...) on top
                scores = [0.7 * e + 0.3 * b for e, b in zip(emb, scores)]
            except Exception as exc:  # noqa: BLE001
                log.warning("Embedding query failed (%s); BM25 only", exc)
        ranked = sorted(zip(scores, self.chunks), key=lambda x: x[0], reverse=True)[:top_k]
        return [{"source": c.source, "title": c.title, "heading": c.heading, "text": c.text, "score": round(s, 3)}
                for s, c in ranked if s >= min_score]


# --------------------------------------------------------------------------- #
# Ollama embeddings (optional)
# --------------------------------------------------------------------------- #
def _ollama_embed(texts: list[str]) -> list[list[float]]:
    s = get_settings()
    resp = httpx.post(f"{s.ollama_base_url}/api/embed", json={"model": s.rag_embedding_model, "input": texts},
                      timeout=60.0)
    resp.raise_for_status()
    return resp.json()["embeddings"]


@lru_cache
def get_index() -> PolicyIndex:
    s = get_settings()
    chunks = load_chunks(s.kb_dir)
    embed = None
    if s.rag_embedding_model and s.llm_provider != "rules":
        from utils.llm import ollama_available
        if ollama_available():
            embed = _ollama_embed
    index = PolicyIndex(chunks, embed)
    log.info("Policy index: %d chunks from %s (%s)", len(chunks), s.kb_dir, index.method)
    return index


def search_policy(query: str, top_k: Optional[int] = None) -> dict:
    """Tool payload: relevant passages (possibly none) with their scores and the retrieval method."""
    s = get_settings()
    index = get_index()
    hits = index.search(query, top_k or s.rag_top_k, s.rag_min_score)
    return {"query": query, "method": index.method, "hits": hits,
            "best_score": hits[0]["score"] if hits else 0.0, "documents": len(index.chunks)}
