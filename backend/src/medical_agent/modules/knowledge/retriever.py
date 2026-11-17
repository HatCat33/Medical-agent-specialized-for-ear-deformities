"""Hybrid lexical and optional vector retrieval for a published FAQ release."""

import asyncio
import json
import math
import re
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from time import perf_counter
from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from medical_agent.core.config import Settings
from medical_agent.core.logging import get_logger
from medical_agent.core.redis import stable_hash, store
from medical_agent.modules.knowledge import repository
from medical_agent.modules.knowledge.models import KnowledgeEntry, KnowledgeRelease
from medical_agent.modules.knowledge.schemas import RetrievalHit
from medical_agent.providers.embedding.client import (
    EmbeddingProviderError,
    build_embedding_provider,
    embedding_fingerprint,
)
from medical_agent.providers.rerank.client import (
    RerankerProviderError,
    build_reranker_provider,
)

logger = get_logger(__name__)
_CORPUS_CACHE_LIMIT = 4
_corpus_cache: OrderedDict[str, tuple["_CorpusEntry", ...]] = OrderedDict()
_corpus_lock = asyncio.Lock()


@dataclass(frozen=True)
class _CorpusEntry:
    id: str
    category: str
    question: str
    aliases: str
    answer: str
    source_sheet: str
    source_row: int
    question_compact: str
    title_tokens: frozenset[str]
    answer_tokens: frozenset[str]
    embedding: tuple[float, ...] | None
    embedding_norm: float
    embedding_invalid: bool


def _tokens(text: str) -> set[str]:
    compact = "".join(text.casefold().split())
    latin = set(re.findall(r"[a-z0-9]+", compact))
    chinese = "".join(re.findall(r"[\u4e00-\u9fff]", compact))
    grams = {chinese[index : index + 2] for index in range(max(0, len(chinese) - 1))}
    if len(chinese) == 1:
        grams.add(chinese)
    return latin | grams


def _lexical_score(
    query_compact: str,
    query_tokens: set[str],
    entry: _CorpusEntry,
) -> float:
    title_overlap = len(query_tokens & entry.title_tokens) / max(1, len(query_tokens))
    answer_overlap = len(query_tokens & entry.answer_tokens) / max(1, len(query_tokens))
    similarity = SequenceMatcher(None, query_compact, entry.question_compact).ratio()
    substring_bonus = (
        0.15
        if query_compact in entry.question_compact or entry.question_compact in query_compact
        else 0.0
    )
    return min(
        1.0, 0.55 * title_overlap + 0.2 * answer_overlap + 0.25 * similarity + substring_bonus
    )


def _cosine(
    left: Sequence[float],
    right: Sequence[float],
    *,
    left_norm: float,
    right_norm: float,
) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    if not left_norm or not right_norm:
        return 0.0
    return dot / (left_norm * right_norm)


def _corpus_entry(entry: KnowledgeEntry) -> _CorpusEntry:
    vector: tuple[float, ...] | None = None
    invalid = False
    if entry.embedding_json:
        try:
            raw = cast(object, json.loads(entry.embedding_json))
            if not isinstance(raw, list) or not raw:
                raise ValueError("invalid_vector")
            parsed = tuple(
                float(cast(int | float | str, value))
                for value in cast(list[object], raw)
            )
            if not all(math.isfinite(value) for value in parsed):
                raise ValueError("invalid_vector")
            vector = parsed
        except (TypeError, ValueError, json.JSONDecodeError):
            invalid = True
    return _CorpusEntry(
        id=entry.id,
        category=entry.category,
        question=entry.question,
        aliases=entry.aliases,
        answer=entry.answer,
        source_sheet=entry.source_sheet,
        source_row=entry.source_row,
        question_compact="".join(entry.question.casefold().split()),
        title_tokens=frozenset(_tokens(f"{entry.question} {entry.aliases}")),
        answer_tokens=frozenset(_tokens(entry.answer)),
        embedding=vector,
        embedding_norm=math.sqrt(sum(value * value for value in vector)) if vector else 0.0,
        embedding_invalid=invalid,
    )


async def _load_corpus(db: AsyncSession, release: KnowledgeRelease) -> tuple[_CorpusEntry, ...]:
    if release.status != "published":
        rows = await db.scalars(
            select(KnowledgeEntry)
            .where(KnowledgeEntry.release_id == release.id)
            .order_by(KnowledgeEntry.source_row, KnowledgeEntry.id)
        )
        return tuple(_corpus_entry(entry) for entry in rows)

    cached = _corpus_cache.get(release.id)
    if cached is not None:
        _corpus_cache.move_to_end(release.id)
        return cached
    async with _corpus_lock:
        cached = _corpus_cache.get(release.id)
        if cached is not None:
            _corpus_cache.move_to_end(release.id)
            return cached
        rows = await db.scalars(
            select(KnowledgeEntry)
            .where(KnowledgeEntry.release_id == release.id)
            .order_by(KnowledgeEntry.source_row, KnowledgeEntry.id)
        )
        corpus = tuple(_corpus_entry(entry) for entry in rows)
        _corpus_cache[release.id] = corpus
        _corpus_cache.move_to_end(release.id)
        while len(_corpus_cache) > _CORPUS_CACHE_LIMIT:
            _corpus_cache.popitem(last=False)
        logger.info(
            "knowledge_corpus_cached",
            release_id=release.id,
            entry_count=len(corpus),
        )
        return corpus


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


def _retrieval_cache_key(
    *,
    release_id: str,
    query: str,
    settings: Settings,
    lexical_only: bool,
) -> str:
    configuration = stable_hash(
        embedding_fingerprint(settings),
        settings.rag_top_k,
        settings.rag_min_score,
        settings.rag_relative_score,
        settings.reranker_enabled,
        settings.reranker_base_url.rstrip("/"),
        settings.reranker_model,
        settings.reranker_candidate_k,
        lexical_only,
    )
    return store.key("retrieval", release_id, configuration, stable_hash(query))


def _cached_result(value: object, *, latency_ms: float) -> RetrievalResult | None:
    if not isinstance(value, dict):
        return None
    payload = cast(dict[str, object], value)
    try:
        raw_hits = payload["hits"]
        raw_scores = payload["scores"]
        raw_warnings = payload["warnings"]
        mode = str(payload["mode"])
        if (
            not isinstance(raw_hits, list)
            or not isinstance(raw_scores, dict)
            or not isinstance(raw_warnings, list)
        ):
            return None
        warnings = [str(item) for item in cast(list[object], raw_warnings)]
        hits: list[RetrievalHit] = []
        for raw_hit in cast(list[object], raw_hits):
            if not isinstance(raw_hit, dict):
                continue
            item = cast(dict[str, object], raw_hit)
            hits.append(
                RetrievalHit(
                    entry_id=str(item["entry_id"]),
                    category=str(item["category"]),
                    question=str(item["question"]),
                    answer=str(item["answer"]),
                    source_name=str(item["source_name"]),
                    source_sheet=str(item["source_sheet"]),
                    source_row=int(cast(int | float | str, item["source_row"])),
                    score=float(cast(int | float | str, item["score"])),
                )
            )
        scores: dict[str, dict[str, float | None]] = {}
        for entry_id, raw_item in cast(dict[object, object], raw_scores).items():
            if not isinstance(raw_item, dict):
                continue
            scores[str(entry_id)] = {
                str(name): (None if score is None else float(cast(int | float | str, score)))
                for name, score in cast(dict[object, object], raw_item).items()
            }
    except (KeyError, TypeError, ValueError):
        return None
    return RetrievalResult(hits, scores, mode, warnings, latency_ms)


def _cache_payload(result: RetrievalResult) -> dict[str, object]:
    return {
        "hits": [
            {
                "entry_id": hit.entry_id,
                "category": hit.category,
                "question": hit.question,
                "answer": hit.answer,
                "source_name": hit.source_name,
                "source_sheet": hit.source_sheet,
                "source_row": hit.source_row,
                "score": hit.score,
            }
            for hit in result.hits
        ],
        "scores": result.scores,
        "mode": result.mode,
        "warnings": result.warnings,
    }


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
        await repository.get_release_by_id_metadata(db, release_id=release_id)
        if release_id
        else await repository.get_published_release_metadata(db)
    )
    warnings: list[str] = []
    scores: dict[str, dict[str, float | None]] = {}
    if release is None:
        return RetrievalResult([], {}, "lexical", ["knowledge_release_not_found"], 0)
    cache_key: str | None = None
    if (
        settings.redis_enabled
        and settings.retrieval_cache_ttl_seconds > 0
        and release.status == "published"
    ):
        cache_key = _retrieval_cache_key(
            release_id=release.id,
            query=query,
            settings=settings,
            lexical_only=lexical_only,
        )
        cached = _cached_result(
            await store.get_json(cache_key),
            latency_ms=round((perf_counter() - started) * 1000, 2),
        )
        if cached is not None:
            logger.info(
                "knowledge_retrieval_completed",
                release_id=release.id,
                cache_hit=True,
                mode=cached.mode,
                hit_count=len(cached.hits),
                latency_ms=cached.latency_ms,
            )
            return cached
    query_vector: list[float] | None = None
    embedding_provider = build_embedding_provider(settings)
    report = json.loads(release.quality_report_json)
    current_fingerprint = embedding_fingerprint(settings)
    if report.get("requires_full_rebuild") or (
        report.get("embedding_fingerprint")
        and report["embedding_fingerprint"] != current_fingerprint
    ):
        embedding_provider = None
        warnings.append("embedding_model_changed_using_lexical")
    corpus = await _load_corpus(db, release)
    if (
        not lexical_only
        and embedding_provider is not None
        and any(entry.embedding is not None for entry in corpus)
    ):
        try:
            query_vector = (await embedding_provider.embed([query]))[0]
        except EmbeddingProviderError:
            query_vector = None
            warnings.append("embedding_request_failed_using_lexical")
    if not lexical_only and query_vector is None and not warnings:
        warnings.append("no_usable_embeddings_using_lexical")

    query_compact = "".join(query.casefold().split())
    query_tokens = _tokens(query)
    query_vector_norm = (
        math.sqrt(sum(value * value for value in query_vector)) if query_vector else 0.0
    )
    scored: list[tuple[float, _CorpusEntry]] = []
    for entry in corpus:
        lexical = _lexical_score(query_compact, query_tokens, entry)
        score = lexical
        semantic: float | None = None
        if query_vector is not None and entry.embedding is not None:
            if len(entry.embedding) == len(query_vector):
                semantic = max(
                    0.0,
                    _cosine(
                        query_vector,
                        entry.embedding,
                        left_norm=query_vector_norm,
                        right_norm=entry.embedding_norm,
                    ),
                )
                score = 0.65 * lexical + 0.35 * semantic
            else:
                if "invalid_stored_vectors_using_lexical" not in warnings:
                    warnings.append("invalid_stored_vectors_using_lexical")
        elif (
            query_vector is not None
            and entry.embedding_invalid
            and "invalid_stored_vectors_using_lexical" not in warnings
        ):
            warnings.append("invalid_stored_vectors_using_lexical")
        scores[entry.id] = {
            "lexical": round(lexical, 4),
            "semantic": semantic,
            "rerank": None,
        }
        if score >= settings.rag_min_score:
            scored.append((score, entry))
    scored.sort(key=lambda item: (-item[0], item[1].source_row))
    if scored:
        relative_floor = scored[0][0] * settings.rag_relative_score
        scored = [item for item in scored if item[0] >= relative_floor]
    candidate_limit = max(
        settings.rag_top_k,
        settings.reranker_candidate_k if settings.reranker_enabled and not lexical_only else 0,
    )
    candidates = scored[:candidate_limit]
    mode = "hybrid" if query_vector is not None else "lexical"
    reranker = build_reranker_provider(settings) if not lexical_only else None
    if reranker is not None and candidates:
        documents = [
            f"标准问题：{entry.question}\n其他问法：{entry.aliases}\n答案：{entry.answer}"
            for _, entry in candidates
        ]
        try:
            reranked = await reranker.rerank(
                query=query,
                documents=documents,
                top_n=settings.rag_top_k,
            )
            candidates = [
                (item.score, candidates[item.index][1])
                for item in reranked
            ]
            for item in reranked:
                entry = scored[item.index][1] if item.index < len(scored) else None
                if entry is not None:
                    scores[entry.id]["rerank"] = round(item.score, 4)
            mode = "hybrid_reranked" if query_vector is not None else "lexical_reranked"
        except RerankerProviderError:
            warnings.append("reranker_request_failed_using_base_ranking")
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
        for score, entry in candidates[: settings.rag_top_k]
    ]
    result = RetrievalResult(
        hits,
        {hit.entry_id: scores[hit.entry_id] for hit in hits},
        mode,
        warnings,
        round((perf_counter() - started) * 1000, 2),
    )
    if cache_key is not None:
        await store.set_json(
            cache_key,
            _cache_payload(result),
            ttl_seconds=settings.retrieval_cache_ttl_seconds,
        )
    logger.info(
        "knowledge_retrieval_completed",
        release_id=release.id,
        cache_hit=False,
        mode=result.mode,
        hit_count=len(result.hits),
        latency_ms=result.latency_ms,
        warnings=result.warnings,
    )
    return result
