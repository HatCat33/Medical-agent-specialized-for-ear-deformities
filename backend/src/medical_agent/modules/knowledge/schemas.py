"""Knowledge import and retrieval contracts."""

from dataclasses import dataclass

from pydantic import BaseModel, Field


@dataclass(frozen=True)
class FaqEntryInput:
    category: str
    question: str
    aliases: str
    answer: str
    effective_date: str
    source_sheet: str
    source_row: int


@dataclass(frozen=True)
class RetrievalHit:
    entry_id: str
    category: str
    question: str
    answer: str
    source_name: str
    source_sheet: str
    source_row: int
    score: float

    @property
    def citation_label(self) -> str:
        return f"{self.source_name} · {self.category} · {self.question}"


class KnowledgeReleaseStatus(BaseModel):
    id: str
    name: str
    source_name: str
    status: str
    entry_count: int
    vectorized_count: int
    pending_count: int
    embedding_configured: bool
    embedding_model: str


class EmbeddingRebuildRequest(BaseModel):
    batch_size: int | None = Field(default=None, ge=1, le=256)
    rebuild_all: bool = False


class EmbeddingRebuildResponse(BaseModel):
    release_id: str
    total_entries: int
    existing_entries: int
    processed_entries: int
    remaining_entries: int
    vectorized_count: int
    vector_dimension: int | None

