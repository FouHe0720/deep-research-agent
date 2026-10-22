import importlib.util
import json
from pathlib import Path

import pytest

from researchos.adapters.fixture import FixtureSourceProvider
from researchos.domain.models import CheckResult, ResearchRequest, Run
from researchos.infra.store import Store
from researchos.services.engine import Engine
from researchos.services.evaluation import (
    EvalQuestion,
    evaluate_dataset,
    evaluate_run,
    load_questions,
    offline_settings,
)

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "eval/research_questions.jsonl"


def expansion_module():
    spec = importlib.util.spec_from_file_location("expand_eval", ROOT / "scripts/expand_eval.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def completed_run(tmp_path):
    question = load_questions(DATASET)[0]
    store = Store(tmp_path / "evaluation.db")
    run = await Engine(store, FixtureSourceProvider(), offline_settings(tmp_path)).execute(
        Run(request=ResearchRequest(topic=question.topic, mode="offline"))
    )
    return run, store.events(run.id), question


def test_dataset_has_30_categorized_pending_questions_and_limited_annotations():
    questions = load_questions(DATASET)
    assert len(questions) >= 30
    assert len({question.category for question in questions}) >= 10
    assert all(not question.reviewed for question in questions)
    assert sum(bool(question.annotations) for question in questions) == 3
    assert all(
        question.category == "agent_memory" for question in questions if question.annotations
    )
    with pytest.raises(ValueError, match="human review pending"):
        load_questions(DATASET, require_reviewed=True)


async def test_metrics_measure_events_and_keep_unmeasured_quality_null(tmp_path):
    run, events, question = await completed_run(tmp_path)
    metrics = evaluate_run(run, events, question, 12.3456)
    assert metrics["task_completion"] is True
    assert metrics["source_validity"] is None
    assert metrics["faithfulness"] is None
    assert metrics["llm_tokens"] is metrics["cost_usd"] is None
    assert metrics["fixture_source_integrity"] == 1
    assert metrics["citation_link_integrity"] == 1
    assert metrics["coverage"] == 1
    assert metrics["annotated_keypoint_coverage"] == 1
    assert metrics["latency_ms"] == 12.346
    assert metrics["tool_success"] == 1

    events.append({"kind": "tool_result", "payload": {"status": "error"}})
    measured = evaluate_run(run, events, question, 14)
    assert measured["tool_success"] == pytest.approx(3 / 4)
    assert evaluate_run(run, [], question, 14)["tool_success"] is None
    assert evaluate_run(run, [], question, 14)["task_completion"] is False


async def test_metrics_detect_checker_failure_instead_of_trusting_cached_checks(tmp_path):
    run, events, question = await completed_run(tmp_path)
    run.report_md = run.report_md.replace("[1]", "[999]")
    run.checks = CheckResult(passed=True)
    metrics = evaluate_run(run, events, question, 1)
    assert metrics["citation_link_integrity"] == 0
    assert metrics["task_completion"] is False
    assert metrics["checks_passed"] is False
    assert metrics["check_errors"]


async def test_metrics_detect_fixture_tampering_binding_gaps_and_missing_keypoints(tmp_path):
    run, events, question = await completed_run(tmp_path)
    run.sources[0].excerpt = "Changed fixture passage"
    for source in run.sources:
        source.subtask_ids = [identifier for identifier in source.subtask_ids if identifier != "q3"]
    for claim in run.claims:
        claim.passage = "No annotated words"
    metrics = evaluate_run(run, events, question, 1)
    assert metrics["fixture_source_integrity"] == pytest.approx(5 / 6)
    assert metrics["coverage"] == pytest.approx(2 / 3)
    assert metrics["annotated_keypoint_coverage"] == 0
    assert metrics["task_completion"] is False
    unannotated = EvalQuestion(id="other", category="energy", topic="Compare energy storage")
    assert evaluate_run(run, events, unannotated, 1)["annotated_keypoint_coverage"] is None


def test_missing_plan_evidence_or_usage_does_not_receive_perfect_scores():
    run = Run(request=ResearchRequest(topic="Missing evidence"))
    question = EvalQuestion(id="empty", category="research", topic="Missing evidence")
    metrics = evaluate_run(run, [], question, 0.1)
    assert metrics["task_completion"] is False
    for name in (
        "fixture_source_integrity",
        "citation_link_integrity",
        "coverage",
        "tool_success",
        "llm_tokens",
        "cost_usd",
        "faithfulness",
        "source_validity",
    ):
        assert metrics[name] is None


def test_offline_settings_override_model_and_retrieval_environment(monkeypatch, tmp_path):
    for name in ("LLM_BASE_URL", "GATEWAY_URL", "RAG_URL"):
        monkeypatch.setenv(f"RESEARCH_{name}", "https://must-not-call.example")
    monkeypatch.setenv("RESEARCH_ALLOW_LIVE", "true")
    monkeypatch.setenv("RESEARCH_LLM_MODEL", "paid-default-model")
    settings = offline_settings(tmp_path)
    assert settings.allow_live is False
    assert settings.llm_base_url is settings.gateway_url is settings.rag_url is None
    assert settings.llm_model is settings.llm_api_key is None


def test_expansion_produces_100_unique_pending_candidates_and_never_approves(tmp_path):
    module = expansion_module()
    target = tmp_path / "candidates.jsonl"
    generated = module.generate_candidates(DATASET, target)
    assert len(generated) == 100
    assert len({item.topic for item in generated}) == 100
    assert all(not item.reviewed and item.reviewer is None for item in generated)
    assert target.read_text() == "".join(item.model_dump_json() + "\n" for item in generated)
    with pytest.raises(ValueError, match="human review pending"):
        load_questions(target, require_reviewed=True)
    # Only this explicitly selected item changes; no loop silently approves peers.
    module.mark_reviewed(target, "candidate-001", "test-human-action")
    records = load_questions(target)
    assert sum(item.reviewed for item in records) == 1
    assert records[0].reviewer == "test-human-action"
    assert records[0].reviewed_at
    with pytest.raises(ValueError, match="unknown question"):
        module.mark_reviewed(target, "missing-id", "test-human-action")


def test_review_flag_requires_human_record_and_duplicate_dataset_ids_fail(tmp_path):
    with pytest.raises(ValueError, match="reviewer"):
        EvalQuestion(id="unproven", category="test", topic="Some topic", reviewed=True)
    with pytest.raises(ValueError, match="agent_memory"):
        EvalQuestion.model_validate(
            {
                "id": "irrelevant",
                "category": "energy",
                "topic": "Some topic",
                "annotations": [{"id": "one", "any_of": ["word"]}],
            }
        )
    target = tmp_path / "duplicate.jsonl"
    target.write_text(DATASET.read_text() + DATASET.read_text().splitlines()[0] + "\n")
    with pytest.raises(ValueError, match="duplicate"):
        load_questions(target)


async def test_full_offline_evaluation_exports_actual_metrics_and_one_safe_sample(
    tmp_path, monkeypatch
):
    async def unexpected(*args, **kwargs):
        pytest.fail("offline evaluation attempted network I/O")

    monkeypatch.setattr("httpx.AsyncClient.send", unexpected)
    output = tmp_path / "results" / "offline.json"
    payload = await evaluate_dataset(DATASET, output)
    assert payload["question_count"] == 30
    assert payload["human_reviewed_count"] == 0
    assert payload["aggregate_means"]["source_validity"] is None
    assert payload["aggregate_means"]["faithfulness"] is None
    assert all(row["metrics"]["latency_ms"] > 0 for row in payload["rows"])
    assert all(row["tool_result_count"] == 3 for row in payload["rows"])
    assert (
        sum(row["metrics"]["annotated_keypoint_coverage"] is not None for row in payload["rows"])
        == 3
    )
    assert json.loads(output.read_text())["dataset_sha256"]
    assert "fixed Agent Memory" in " ".join(payload["constraints"])
    for filename in ("demo.md", "demo.html", "run.json", "trace.json"):
        value = (output.parent / filename).read_text()
        assert value
        assert str(tmp_path) not in value
    assert "DEMO / OFFLINE" in (output.parent / "demo.md").read_text()
    run = json.loads((output.parent / "run.json").read_text())
    assert all(source["provenance"] == "fixture" for source in run["sources"])
    trace = json.loads((output.parent / "trace.json").read_text())
    assert any(span["event_type"] == "tool_result" for span in trace["trace"])
