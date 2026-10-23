"""Public typed contracts. Evidence is data, never executable instructions."""

from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


def uid() -> str:
    return uuid4().hex


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Budget(Contract):
    max_seconds: float = Field(60, ge=0.05, le=300)
    max_tool_calls: int = Field(12, ge=1, le=40)
    max_rounds: int = Field(1, ge=1, le=2)
    max_cost_usd: float = Field(0, ge=0, le=5)


class ResearchRequest(Contract):
    topic: str = Field(min_length=3, max_length=500)
    time_range: str | None = Field(None, pattern=r"^\d{4}-\d{4}$")
    max_sources: int = Field(6, ge=1, le=20)
    depth: Literal["quick", "standard", "deep"] = "standard"
    budget: Budget = Field(default_factory=Budget)
    language: Literal["zh", "en"] = "zh"
    mode: Literal["offline", "live"] = "offline"
    corpus: Literal["teaching", "campus"] = "teaching"
    campus: Literal["威海", "济南", "青岛"] | None = None
    document_year: int | None = Field(None, ge=1900, le=2100)
    cohort: int | None = Field(None, ge=1900, le=2100)
    providers: list[Literal["github", "arxiv", "web"]] = Field(
        default_factory=lambda: ["github", "arxiv"], min_length=1, max_length=3
    )

    @model_validator(mode="after")
    def validate_topic(self) -> "ResearchRequest":
        self.topic = self.topic.strip()
        if len(self.topic) < 3:
            raise ValueError("topic must contain at least 3 non-whitespace characters")
        if self.mode == "live" and self.corpus == "campus":
            raise ValueError("campus corpus is an offline historical snapshot; use offline mode")
        if self.time_range:
            start, end = map(int, self.time_range.split("-"))
            if start > end or start < 1900 or end > 2100:
                raise ValueError("time_range must be ascending years in 1900..2100")
        return self


class Subtask(Contract):
    id: str
    question: str
    keywords: list[str]
    evidence_type: str
    status: Literal["pending", "searching", "complete", "missing"] = "pending"


class Plan(Contract):
    subtasks: list[Subtask]
    max_rounds: int
    strategy: str = "bounded deterministic planner"


class DocumentOrigin(Contract):
    dataset_id: str
    source_path: str
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    excerpt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    paragraph_numbers: list[int] = Field(min_length=1)
    campus: str
    document_year: int
    cohort: int | None = None


class SourceRecord(Contract):
    id: str = Field(default_factory=uid)
    url: str
    title: str
    authors: list[str] = Field(default_factory=list)
    published_at: str | None = None
    date_basis: str | None = None
    fetched_at: str = Field(default_factory=utcnow)
    category: Literal["fixture", "paper", "github", "web", "document"]
    excerpt: str = Field(min_length=1, max_length=8000)
    content_hash: str = ""
    doi: str | None = None
    duplicate_of: str | None = None
    subtask_ids: list[str] = Field(default_factory=list)
    provenance: Literal["fixture", "network", "local"]
    document: DocumentOrigin | None = None


class Claim(Contract):
    id: str = Field(default_factory=uid)
    text: str
    source_id: str
    passage: str
    citation_id: int
    subtask_id: str


class CheckResult(Contract):
    passed: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class Run(Contract):
    id: str = Field(default_factory=uid)
    request: ResearchRequest
    status: Literal["queued", "running", "completed", "partial", "failed", "cancelled"] = "queued"
    created_at: str = Field(default_factory=utcnow)
    updated_at: str = Field(default_factory=utcnow)
    plan: Plan | None = None
    sources: list[SourceRecord] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    report_md: str = ""
    checks: CheckResult | None = None
    reasons: list[str] = Field(default_factory=list)
    config: dict = Field(default_factory=dict)
    usage: dict = Field(default_factory=lambda: {"tokens": None, "cost_usd": None})
