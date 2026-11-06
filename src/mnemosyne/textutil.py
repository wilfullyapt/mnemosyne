"""Hashing and light text helpers for ingest."""

from __future__ import annotations

import hashlib
import re


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def estimate_tokens(text: str) -> int:
    """Rough whitespace token estimate — not a model tokenizer."""
    parts = text.split()
    return len(parts)


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'])")


def split_passages(text: str, *, max_chars: int = 1200) -> list[str]:
    """Split plain text into passage-sized chunks on paragraph/sentence boundaries."""
    cleaned = text.replace("\r\n", "\n").strip()
    if not cleaned:
        return []

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", cleaned) if p.strip()]
    chunks: list[str] = []
    buf = ""

    def flush() -> None:
        nonlocal buf
        if buf.strip():
            chunks.append(buf.strip())
        buf = ""

    for para in paragraphs:
        if len(para) <= max_chars:
            candidate = f"{buf}\n\n{para}".strip() if buf else para
            if len(candidate) <= max_chars:
                buf = candidate
            else:
                flush()
                buf = para
            continue

        # Long paragraph: sentence-split.
        sentences = _SENTENCE_SPLIT.split(para) if para else [para]
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            if len(sentence) > max_chars:
                flush()
                # Hard wrap pathological lines.
                for i in range(0, len(sentence), max_chars):
                    chunks.append(sentence[i : i + max_chars].strip())
                continue
            candidate = f"{buf} {sentence}".strip() if buf else sentence
            if len(candidate) <= max_chars:
                buf = candidate
            else:
                flush()
                buf = sentence
    flush()
    return chunks


def propose_claims_from_passage(body: str, *, max_claims: int = 3) -> list[str]:
    """
    Deterministic draft claim proposer for the golden path.

    Heuristic only — human review is mandatory. Prefers sentences containing
    strong assertion verbs; falls back to first non-trivial sentences.
    """
    body = body.strip()
    if not body:
        return []

    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(body) if s.strip()]
    if not sentences:
        sentences = [body]

    strong = re.compile(
        r"\b(is|are|was|were|means|implies|shows|demonstrates|requires|causes)\b",
        re.I,
    )
    scored: list[tuple[int, str]] = []
    for s in sentences:
        if len(s) < 24:
            continue
        score = 2 if strong.search(s) else 1
        if s[0].isupper() or s[0] in "\"'":
            score += 1
        scored.append((score, s))

    scored.sort(key=lambda t: (-t[0], -len(t[1])))
    out: list[str] = []
    seen: set[str] = set()
    for _, s in scored:
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        # Normalize trailing whitespace; keep original wording.
        out.append(s.rstrip())
        if len(out) >= max_claims:
            break
    return out
