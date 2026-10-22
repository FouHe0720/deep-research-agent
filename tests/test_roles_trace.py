import asyncio

import pytest

from researchos.adapters.fixture import FixtureSourceProvider
from researchos.domain.models import Budget, ResearchRequest, Run
from researchos.infra.store import Store
from researchos.services.engine import Engine


@pytest.mark.asyncio
async def test_spans_have_parent_durations_and_roles(tmp_path):
    store = Store(tmp_path / "r.db")
    run = await Engine(store).execute(Run(request=ResearchRequest(topic="Agent Memory")))
    events = store.events(run.id)
    starts = [e["payload"] for e in events if e["kind"] == "span_start"]
    ends = [e["payload"] for e in events if e["kind"] == "span_end"]
    assert len(starts) == len(ends)
    assert len([s for s in starts if s["parent_span_id"] is None]) == 1
    assert {s["role"] for s in starts} >= {"Researcher", "Synthesizer", "Reviewer", "Orchestrator"}
    assert all(e["duration_ms"] >= 0 for e in ends)
    assert store.get(run.id).checks.passed


class RetryProvider:
    def __init__(self):
        self.calls = 0

    async def search(self, task, limit):
        self.calls += 1
        if self.calls <= 3:
            return []
        return await FixtureSourceProvider().search(task, limit)


@pytest.mark.asyncio
async def test_reviewer_requests_only_bounded_second_round(tmp_path):
    p = RetryProvider()
    run = await Engine(Store(tmp_path / "r.db"), p).execute(
        Run(request=ResearchRequest(topic="Agent Memory", budget=Budget(max_rounds=2)))
    )
    assert run.status == "completed"
    assert run.config["rounds"] == 2 and p.calls == 6
    assert len(run.sources) == 6


@pytest.mark.asyncio
async def test_cancellation_saves_evidence_and_trace(tmp_path):
    class Slow:
        async def search(self, task, limit):
            await asyncio.sleep(10)

    store = Store(tmp_path / "r.db")
    run = Run(request=ResearchRequest(topic="Agent Memory"))
    task = asyncio.create_task(Engine(store, Slow()).execute(run))
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert store.get(run.id).status == "cancelled"
    assert any(e["kind"] == "span_end" for e in store.events(run.id))


@pytest.mark.asyncio
async def test_english_report_preserves_original_evidence(tmp_path):
    run = await Engine(Store(tmp_path / "r.db")).execute(
        Run(request=ResearchRequest(topic="Agent Memory", language="en"))
    )
    assert run.checks.passed
    assert "## Findings" in run.report_md
    assert "## References" in run.report_md
    assert run.sources[0].excerpt in run.claims[0].passage


@pytest.mark.asyncio
async def test_optional_llm_analysis_cannot_create_trusted_citations(tmp_path, monkeypatch):
    from researchos.adapters.integrations import GatewayClient, LLMResult
    from researchos.infra.config import Settings

    async def generate(self, messages, **kwargs):
        assert kwargs["max_cost_usd"] == 0.01
        return LLMResult(text="Unverified model claim [999] <script>bad</script>", model="test")

    monkeypatch.setattr(GatewayClient, "generate", generate)
    settings = Settings(llm_base_url="http://127.0.0.1:9999/v1", llm_model="test")
    run = await Engine(Store(tmp_path / "r.db"), settings=settings).execute(
        Run(request=ResearchRequest(topic="Agent Memory", budget=Budget(max_cost_usd=0.01)))
    )
    assert run.status == "completed" and run.checks.passed
    assert len(run.claims) == 6
    assert "待人工核验" in run.report_md
    assert run.usage == {"tokens": None, "cost_usd": None}


@pytest.mark.asyncio
async def test_configured_llm_zero_budget_never_calls_model(tmp_path, monkeypatch):
    from researchos.adapters.integrations import GatewayClient
    from researchos.infra.config import Settings

    called = False

    async def generate(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("unexpected network")

    monkeypatch.setattr(GatewayClient, "generate", generate)
    settings = Settings(llm_base_url="http://127.0.0.1:9999/v1", llm_model="test")
    run = await Engine(Store(tmp_path / "r.db"), settings=settings).execute(
        Run(request=ResearchRequest(topic="Agent Memory"))
    )
    assert run.status == "completed" and not called
    assert "budget" in run.config["llm_fallback"]
