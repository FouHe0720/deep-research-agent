"""Measured offline workflow regression; semantic quality remains unmeasured."""

from __future__ import annotations

import asyncio
import hashlib
import json
import statistics
import tempfile
import time
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from researchos.adapters.fixture import FixtureSourceProvider
from researchos.adapters.integrations import AgentOpsExporter
from researchos.domain.models import ResearchRequest, Run
from researchos.infra.config import Settings
from researchos.infra.store import Store
from researchos.services.engine import Engine
from researchos.services.evidence import EvidenceChecker
from researchos.services.writer import render_html


class Keypoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=80)
    any_of: list[str] = Field(min_length=1, max_length=10)


class EvalQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    category: str = Field(min_length=1, max_length=80)
    topic: str = Field(min_length=3, max_length=500)
    reviewed: bool = Field(False, strict=True)
    reviewer: str | None = None
    reviewed_at: str | None = None
    annotations: list[Keypoint] = Field(default_factory=list)

    @model_validator(mode="after")
    def review_consistency(self):
        if self.reviewed and (not self.reviewer or not self.reviewed_at):
            raise ValueError("reviewed questions require reviewer and reviewed_at")
        if self.annotations and self.category != "agent_memory":
            raise ValueError("fixture keypoint annotations apply only to agent_memory")
        if any(not token.strip() for point in self.annotations for token in point.any_of):
            raise ValueError("keypoint match tokens cannot be blank")
        if len({point.id for point in self.annotations}) != len(self.annotations):
            raise ValueError("duplicate keypoint ids")
        return self


def load_questions(path: Path, *, minimum: int = 30, require_reviewed: bool = False):
    questions = [
        EvalQuestion.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(questions) < minimum:
        raise ValueError(f"dataset requires at least {minimum} questions")
    if len({question.id for question in questions}) != len(questions):
        raise ValueError("dataset contains duplicate question ids")
    if require_reviewed and any(not question.reviewed for question in questions):
        raise ValueError("human review pending: all questions must have reviewed=true")
    return questions


def _fixtures() -> dict[str, dict]:
    values = json.loads(files("researchos").joinpath("data/fixtures.json").read_text())
    return {value["id"]: value for value in values}


def evaluate_run(run: Run, events: list[dict], question: EvalQuestion, latency_ms: float):
    """Recompute checks, never trust cached run.checks or assume successful tools."""
    checks = EvidenceChecker().check(run)
    planned = {subtask.id for subtask in run.plan.subtasks} if run.plan else set()
    bound = {identifier for source in run.sources for identifier in source.subtask_ids}
    coverage = len(planned & bound) / len(planned) if planned else None
    tool_results = [
        event.get("payload", {}) for event in events if event.get("kind") == "tool_result"
    ]
    tool_success = (
        sum(result.get("status") == "ok" for result in tool_results) / len(tool_results)
        if tool_results
        else None
    )
    fixture_data = _fixtures()
    intact = 0
    for source in run.sources:
        expected = fixture_data.get(source.id)
        if expected and (
            source.provenance == "fixture"
            and source.category == "fixture"
            and source.url == f"fixture://researchos/{source.id}"
            and source.title == expected["title"]
            and source.excerpt == expected["excerpt"]
            and source.content_hash == hashlib.sha256(source.excerpt.encode()).hexdigest()
        ):
            intact += 1
    keypoint_coverage = None
    if question.category == "agent_memory" and question.annotations:
        passages = "\n".join(claim.passage for claim in run.claims).casefold()
        matched = sum(
            any(token.casefold() in passages for token in point.any_of)
            for point in question.annotations
        )
        keypoint_coverage = matched / len(question.annotations)
    return {
        "task_completion": bool(
            run.status == "completed"
            and planned
            and run.claims
            and checks.passed
            and coverage == 1
            and any(event.get("kind") == "finished" for event in events)
        ),
        "source_validity": None,
        "fixture_source_integrity": intact / len(run.sources) if run.sources else None,
        "citation_link_integrity": float(checks.passed) if run.claims else None,
        "coverage": coverage,
        "annotated_keypoint_coverage": keypoint_coverage,
        "latency_ms": round(latency_ms, 3),
        "tool_success": tool_success,
        "llm_tokens": run.usage.get("tokens"),
        "cost_usd": run.usage.get("cost_usd"),
        "faithfulness": None,
        "checks_passed": checks.passed,
        "check_errors": checks.errors,
    }


CONSTRAINTS = [
    "OFFLINE structural regression only; every topic uses the same fixed Agent Memory notes.",
    "Task completion and coverage measure workflow structure, not topic relevance or quality.",
    "Fixture integrity does not establish internet source validity; source_validity is null.",
    "Citation checks do not establish semantic faithfulness; faithfulness requires human review.",
    "Agent Memory keypoints use lexical passage matching, not semantic entailment.",
    "Question review flags are preserved; running evaluation never approves human review.",
    "No network, external RAG, default model, or paid API is used; unavailable usage remains null.",
]


def offline_settings(data_dir: Path) -> Settings:
    # _env_file=None disables .env; explicit None also overrides inherited endpoint
    # environment variables, so a developer's normal model settings cannot leak in.
    return Settings(
        _env_file=None,
        data_dir=data_dir,
        max_jobs=1,
        max_queue=1,
        allow_live=False,
        api_key=None,
        web_api_key=None,
        github_token=None,
        llm_base_url=None,
        llm_api_key=None,
        llm_model=None,
        llm_kind="openai",
        llm_input_usd_per_million=None,
        llm_output_usd_per_million=None,
        gateway_url=None,
        gateway_api_key=None,
        rag_url=None,
    )


def _mean(rows, name):
    values = [row["metrics"][name] for row in rows if row["metrics"][name] is not None]
    return round(statistics.mean(values), 6) if values else None


async def evaluate_dataset(dataset: Path, output: Path, *, artifacts_dir: Path | None = None):
    questions = load_questions(dataset)
    dataset_hash = hashlib.sha256(await asyncio.to_thread(dataset.read_bytes)).hexdigest()
    rows = []
    sample = None
    started_at = datetime.now(UTC).isoformat()
    with tempfile.TemporaryDirectory(prefix="researchos-evaluation-") as temporary:
        data_dir = Path(temporary)
        settings = offline_settings(data_dir)
        for question in questions:
            # Every question gets a fresh SQLite database and Engine instance.
            store = Store(data_dir / f"{question.id}.db")
            run = Run(request=ResearchRequest(topic=question.topic, mode="offline"))
            started = time.perf_counter()
            run = await Engine(store, FixtureSourceProvider(), settings).execute(run)
            elapsed_ms = (time.perf_counter() - started) * 1000
            events = store.events(run.id)
            rows.append(
                {
                    "question_id": question.id,
                    "category": question.category,
                    "topic": question.topic,
                    "reviewed": question.reviewed,
                    "status": run.status,
                    "source_count": len(run.sources),
                    "claim_count": len(run.claims),
                    "tool_result_count": sum(event["kind"] == "tool_result" for event in events),
                    "metrics": evaluate_run(run, events, question, elapsed_ms),
                }
            )
            if sample is None:
                sample = (run, events)
    metric_names = (
        "task_completion",
        "source_validity",
        "fixture_source_integrity",
        "citation_link_integrity",
        "coverage",
        "annotated_keypoint_coverage",
        "latency_ms",
        "tool_success",
        "llm_tokens",
        "cost_usd",
        "faithfulness",
    )
    payload = {
        "schema_version": "researchos.evaluation.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "started_at": started_at,
        "mode": "offline",
        "dataset": dataset.name,
        "dataset_sha256": dataset_hash,
        "question_count": len(rows),
        "human_reviewed_count": sum(question.reviewed for question in questions),
        "constraints": CONSTRAINTS,
        "aggregate_means": {name: _mean(rows, name) for name in metric_names},
        "rows": rows,
    }
    run, events = sample
    await asyncio.to_thread(_write_artifacts, payload, output, artifacts_dir, run, events)
    return payload


def _write_artifacts(payload, output, artifacts_dir, run, events):
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    destination = artifacts_dir or output.parent
    destination.mkdir(parents=True, exist_ok=True)
    # Only this runner's owned fixture run is exported. It contains no settings,
    # environment, downloaded content, private paths, or credentials.
    (destination / "demo.md").write_text(run.report_md, encoding="utf-8")
    (destination / "demo.html").write_text(render_html(run.report_md), encoding="utf-8")
    for name, value in (
        ("run.json", run.model_dump(mode="json")),
        ("trace.json", AgentOpsExporter.from_events(run, events)),
    ):
        (destination / name).write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
