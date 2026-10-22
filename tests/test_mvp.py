import pytest
from pydantic import ValidationError

from researchos.domain.models import ResearchRequest, Run
from researchos.infra.store import Store
from researchos.services.engine import Engine
from researchos.services.evidence import EvidenceChecker
from researchos.services.writer import render_html


@pytest.mark.asyncio
async def test_offline_end_to_end_persists_and_retrieves(tmp_path):
    store = Store(tmp_path / "memory.db")
    run = await Engine(store).execute(Run(request=ResearchRequest(topic="Agent Memory")))
    assert run.status == "completed"
    assert len(run.plan.subtasks) == 3
    assert len(run.sources) == 6
    assert run.checks.passed
    assert store.get(run.id).report_md == run.report_md
    assert store.search(run.id, "SQLite")
    assert all(s.url.startswith("fixture://") for s in run.sources)
    assert "DEMO / OFFLINE" in render_html(run.report_md)
    run.claims[0].source_id = "nonexistent"
    assert not EvidenceChecker().check(run).passed


def test_input_constraints():
    for kwargs in (
        {"topic": "  "},
        {"topic": "abc", "max_sources": 100},
        {"topic": "abc", "time_range": "2025-2020"},
    ):
        with pytest.raises(ValidationError):
            ResearchRequest(**kwargs)


def test_html_does_not_execute_untrusted_instructions():
    result = render_html("<script>alert(1)</script> [danger](javascript:alert(1))")
    assert "<script>" not in result
    assert 'href="javascript:' not in result


@pytest.mark.asyncio
async def test_report_tables_links_and_tamper_detection(tmp_path):
    run = await Engine(Store(tmp_path / "r.db")).execute(
        Run(request=ResearchRequest(topic="A [42] topic"))
    )
    html = render_html(run.report_md)
    assert "<table>" in html
    assert 'href="#ref-1"' in html
    assert 'href="#ref-42"' not in html
    assert 'id="ref-1"' in html
    run.report_md = run.report_md.replace(run.sources[0].url, "https://wrong.example")
    assert not EvidenceChecker().check(run).passed


@pytest.mark.asyncio
async def test_invalid_date_and_subtask_binding_detected(tmp_path):
    run = await Engine(Store(tmp_path / "r.db")).execute(
        Run(request=ResearchRequest(topic="Agent memory"))
    )
    run.sources[0].published_at = "not-a-date"
    run.sources[0].date_basis = "made up"
    run.claims[0].subtask_id = "q99"
    assert not EvidenceChecker().check(run).passed
