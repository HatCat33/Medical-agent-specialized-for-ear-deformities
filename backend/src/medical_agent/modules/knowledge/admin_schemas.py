"""Contracts for versioned knowledge administration and retrieval evaluation."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EntryEdit(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    category: str = Field(min_length=1, max_length=120)
    question: str = Field(min_length=1, max_length=4000)
    aliases: str = Field(default="", max_length=8000)
    answer: str = Field(min_length=1, max_length=50000)
    effective_date: str = Field(default="", max_length=80)


class DraftRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=120)
    base_release_id: str | None = None


class SearchRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    release_id: str
    query: str = Field(min_length=1, max_length=4000)
    mode: Literal["hybrid", "lexical"] = "hybrid"
    top_k: int = Field(default=4, ge=1, le=20)
    min_score: float = Field(default=0.16, ge=0, le=1)
    relative_score: float = Field(default=0.62, ge=0, le=1)


class EvaluationCase(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    query: str = Field(min_length=1, max_length=4000)
    expected_entry_ids: list[str] = Field(default_factory=list, max_length=50)
    expect_no_results: bool = False

    @model_validator(mode="after")
    def require_label(self) -> "EvaluationCase":
        if self.expect_no_results == bool(self.expected_entry_ids):
            raise ValueError("provide_expected_ids_or_expect_no_results")
        return self


class EvaluationRequest(SearchRequest):
    query: str = "batch"
    cases: list[EvaluationCase] = Field(min_length=1, max_length=100)


class EvaluationCaseGenerateRequest(BaseModel):
    release_id: str
    limit: int = Field(default=50, ge=1, le=100)
    source: Literal["mixed", "aliases", "standard"] = "standard"
