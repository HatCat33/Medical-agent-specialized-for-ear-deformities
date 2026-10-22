"""Hybrid lexical and optional vector retrieval for a published FAQ release."""

import hashlib
import json
import math
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from time import perf_counter

from sqlalchemy.ext.asyncio import AsyncSession

from medical_agent.core.config import Settings
from medical_agent.modules.knowledge import repository
from medical_agent.modules.knowledge.models import KnowledgeEntry
from medical_agent.modules.knowledge.schemas import RetrievalHit
from medical_agent.providers.embedding.client import (
    EmbeddingProviderError,
    build_embedding_provider,
)


def _tokens(text: str) -> set[str]:
    compact = "".join(text.casefold().split())
    latin = set(re.findall(r"[a-z0-9]+", compact))
    chinese = "".join(re.findall(r"[\u4e00-\u9fff]", compact))
    grams = {chinese[index : index + 2] for index in range(max(0, len(chinese) - 1))}
    if len(chinese) == 1:
        grams.add(chinese)
    return latin | grams


def _lexical_score(query: str, entry: KnowledgeEntry) -> float:
    query_compact = "".join(query.casefold().split())
    question_compact = "".join(entry.question.casefold().split())
    query_tokens = _tokens(query)
    title_tokens = _tokens(f"{entry.question} {entry.aliases}")
    answer_tokens = _tokens(entry.answer)
    title_overlap = len(query_tokens & title_tokens) / max(1, len(query_tokens))
    answer_overlap = len(query_tokens & answer_tokens) / max(1, len(query_tokens))
    similarity = SequenceMatcher(None, query_compact, question_compact).ratio()
    substring_bonus = (
        0.15 if query_compact in question_compact or question_compact in query_compact else 0.0
    )
    return min(
        1.0, 0.55 * title_overlap + 0.2 * answer_overlap + 0.25 * similarity + substring_bonus
    )


def _cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return dot / (left_norm * right_norm)


async def retrieve(
    db: AsyncSession,
    *,
    query: str,
    settings: Settings,
) -> list[RetrievalHit]:
    result = await retrieve_detailed(db, query=query, settings=settings)
    return result.hits


@dataclass
class RetrievalResult:
    hits: list[RetrievalHit]
    scores: dict[str, dict[str, float | None]]
    mode: str
    warnings: list[str]
    latency_ms: float


async def retrieve_detailed(
    db: AsyncSession,
    *,
    query: str,
    settings: Settings,
    release_id: str | None = None,
    lexical_only: bool = False,
) -> RetrievalResult:
    started = perf_counter()
    release = (
        await repository.get_release_by_id(db, release_id=release_id)
        if release_id
        else await repository.get_published_release(db)
    )
    warnings: list[str] = []
    scores: dict[str, dict[str, float | None]] = {}
    if release is None:
        return RetrievalResult([], {}, "lexical", ["knowledge_release_not_found"], 0)
    query_vector: list[float] | None = None
    embedding_provider = build_embedding_provider(settings)
    report = json.loads(release.quality_report_json)
    current_fingerprint = hashlib.sha256(
        f"{settings.embedding_base_url.rstrip('/')}|{settings.embedding_model}".encode()
    ).hexdigest()
    if report.get("requires_full_rebuild") or (
        report.get("embedding_fingerprint")
        and report["embedding_fingerprint"] != current_fingerprint
    ):
        embedding_provider = None
        warnings.append("embedding_model_changed_using_lexical")
    if (
        not lexical_only
        and embedding_provider is not None
        and any(entry.embedding_json for entry in release.entries)
    ):
        try:
            query_vector = (await embedding_provider.embed([query]))[0]
        except EmbeddingProviderError:
            query_vector = None
            warnings.append("embedding_request_failed_using_lexical")
    if not lexical_only and query_vector is None and not warnings:
        warnings.append("no_usable_embeddings_using_lexical")

    scored: list[tuple[float, KnowledgeEntry]] = []
    for entry in release.entries:
        lexical = _lexical_score(query, entry)
        score = lexical
        semantic: float | None = None
        if query_vector is not None and entry.embedding_json:
            try:
                vector = [float(value) for value in json.loads(entry.embedding_json)]
                if len(vector) != len(query_vector) or not all(math.isfinite(v) for v in vector):
                    raise ValueError("invalid_vector")
                semantic = max(0.0, _cosine(query_vector, vector))
                score = 0.65 * lexical + 0.35 * semantic
            except (TypeError, ValueError, json.JSONDecodeError):
                if "invalid_stored_vectors_using_lexical" not in warnings:
                    warnings.append("invalid_stored_vectors_using_lexical")
        scores[entry.id] = {"lexical": round(lexical, 4), "semantic": semantic}
        if score >= settings.rag_min_score:
            scored.append((score, entry))
    scored.sort(key=lambda item: (-item[0], item[1].source_row))
    if scored:
        relative_floor = scored[0][0] * settings.rag_relative_score
        scored = [item for item in scored if item[0] >= relative_floor]
    hits = [
        RetrievalHit(
            entry_id=entry.id,
            category=entry.category,
            question=entry.question,
            answer=entry.answer,
            source_name=release.name,
            source_sheet=entry.source_sheet,
            source_row=entry.source_row,
            score=round(score, 4),
        )
        for score, entry in scored[: settings.rag_top_k]
    ]
    return RetrievalResult(
        hits,
        {hit.entry_id: scores[hit.entry_id] for hit in hits},
        "hybrid" if query_vector is not None else "lexical",
        warnings,
        round((perf_counter() - started) * 1000, 2),
    )
