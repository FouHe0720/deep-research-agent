import asyncio

import pytest

from researchos.adapters.fixture import FixtureSourceProvider
from researchos.domain.models import Budget, ResearchRequest, Run, SourceRecord
from researchos.infra.config import Settings
from researchos.infra.store import Store
from researchos.services.engine import Engine
from researchos.services.memory import ResearchMemory, canonical_url


def test_canonical_doi_hash_dedup_preserves_association():
    memory = ResearchMemory()
    sources = []
    a = SourceRecord(
        url="https://example.org/path?utm_source=x#ref",
        title="a",
        excerpt="evidence",
        category="web",
        provenance="network",
        subtask_ids=["q1"],
        doi="doi:10.1/A",
    )
    assert memory.add(sources, a) is None
    b = a.model_copy(update={"id": "b", "url": "https://other.org", "subtask_ids": ["q2"]})
    assert memory.add(sources, b) is a
    assert b.duplicate_of == a.id
    assert a.subtask_ids == ["q1", "q2"]
    assert canonical_url("https://EXAMPLE.org:443/path/?b=2&a=1&utm_source=3#x") == (
        "https://example.org/path?a=1&b=2"
    )


@pytest.mark.asyncio
async def test_source_and_tool_limits_return_partial(tmp_path):
    run = await Engine(Store(tmp_path / "r.db")).execute(
        Run(
            request=ResearchRequest(
                topic="Agent memory", max_sources=1, budget=Budget(max_tool_calls=1)
            )
        )
    )
    assert run.status == "partial"
    assert len(run.sources) == 1
    assert run.config["tool_calls"] == 1
    assert run.checks.passed


class SlowProvider:
    async def search(self, subtask, limit):
        await asyncio.sleep(5)
        return []


@pytest.mark.asyncio
async def test_timeout_does_not_claim_completion(tmp_path):
    run = await Engine(Store(tmp_path / "r.db"), SlowProvider()).execute(
        Run(request=ResearchRequest(topic="Agent memory", budget=Budget(max_seconds=0.05)))
    )
    assert run.status == "failed"
    assert "Time budget" in str(run.reasons)


@pytest.mark.asyncio
async def test_live_never_falls_back_to_fixture(tmp_path):
    run = await Engine(Store(tmp_path / "r.db"), settings=Settings(allow_live=False)).execute(
        Run(request=ResearchRequest(topic="Agent memory", mode="live"))
    )
    assert run.status == "failed" and not run.sources
    assert run.request.mode == "live"


@pytest.mark.asyncio
async def test_time_range_omits_undated_fixture(tmp_path):
    run = await Engine(Store(tmp_path / "r.db"), FixtureSourceProvider()).execute(
        Run(request=ResearchRequest(topic="Agent memory", time_range="2020-2025"))
    )
    assert not run.sources and run.status == "failed"
