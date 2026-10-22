import asyncio

from researchos.adapters.fixture import FixtureSourceProvider, SourceProvider
from researchos.domain.models import Run
from researchos.infra.store import Store
from researchos.services.evidence import EvidenceChecker
from researchos.services.planner import Planner
from researchos.services.writer import Writer


class Engine:
    def __init__(self, store: Store, provider: SourceProvider | None = None):
        self.store = store
        self.provider = provider or FixtureSourceProvider()

    async def execute(self, run: Run) -> Run:
        run.status = "running"
        run.plan = Planner().plan(run.request)
        self.store.save(run)
        self.store.event(run.id, "plan", plan=run.plan.model_dump())
        for subtask in run.plan.subtasks:
            subtask.status = "searching"
            self.store.event(run.id, "subtask", subtask=subtask.model_dump())
            remaining = run.request.max_sources - len(run.sources)
            if remaining > 0:
                found = await self.provider.search(subtask, min(2, remaining))
                run.sources.extend(s for s in found if s.id not in {v.id for v in run.sources})
            subtask.status = (
                "complete" if any(subtask.id in s.subtask_ids for s in run.sources) else "missing"
            )
            self.store.save(run)
            await asyncio.sleep(0)
        run.report_md = Writer().write(run)
        run.checks = EvidenceChecker().check(run)
        run.status = "completed" if run.checks.passed else "partial"
        self.store.save(run)
        self.store.event(run.id, "finished", status=run.status)
        return run
