from __future__ import annotations

from html import unescape
import re
from typing import Iterable


TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")
WORD_RE = re.compile(r"[a-z0-9][a-z0-9'-]{1,}", re.IGNORECASE)
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
ENTITY_RE = re.compile(r"\b(?:[A-Z][A-Za-z0-9&'.-]+(?:\s+|$)){2,5}")

STOPWORDS = {
    "about",
    "after",
    "again",
    "against",
    "also",
    "before",
    "being",
    "between",
    "could",
    "from",
    "have",
    "into",
    "more",
    "over",
    "said",
    "that",
    "their",
    "there",
    "this",
    "under",
    "were",
    "with",
    "would",
}


def clean_html(value: str | None) -> str:
    if not value:
        return ""
    text = TAG_RE.sub(" ", value)
    return normalize_space(unescape(text))


def normalize_space(value: str | None) -> str:
    return SPACE_RE.sub(" ", value or "").strip()


def tokenize(value: str | None) -> set[str]:
    words = {match.group(0).lower().strip("'") for match in WORD_RE.finditer(value or "")}
    return {word for word in words if len(word) > 2 and word not in STOPWORDS}


def jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def phrase_hits(text: str, phrases: Iterable[str]) -> list[str]:
    lowered = f" {text.lower()} "
    hits: list[str] = []
    for phrase in phrases:
        clean = normalize_space(phrase).lower()
        if clean and clean in lowered:
            hits.append(phrase)
    return sorted(set(hits), key=lambda item: item.lower())


def first_sentences(text: str, max_sentences: int = 2, max_chars: int = 420) -> str:
    clean = normalize_space(text)
    if not clean:
        return ""
    sentences = SENTENCE_RE.split(clean)
    summary = " ".join(sentences[:max_sentences]).strip()
    if len(summary) <= max_chars:
        return summary
    clipped = summary[: max_chars - 3].rsplit(" ", 1)[0].strip()
    return f"{clipped}..."


def extract_named_entities(text: str, known_terms: Iterable[str], limit: int = 12) -> list[str]:
    known = phrase_hits(text, known_terms)
    candidates = []
    for match in ENTITY_RE.finditer(text):
        entity = normalize_space(match.group(0))
        if len(entity) < 4:
            continue
        if entity.lower() in {"signal stream"}:
            continue
        if entity not in candidates:
            candidates.append(entity)
    combined = known + [item for item in candidates if item not in known]
    return combined[:limit]
