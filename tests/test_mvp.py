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
