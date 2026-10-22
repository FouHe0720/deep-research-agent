"""Bounded single-process execution; failures retain already collected evidence."""

import asyncio
import time

from researchos.adapters.fixture import FixtureSourceProvider, SourceProvider
from researchos.domain.models import Run
from researchos.infra.config import Settings
from researchos.infra.store import Store
from researchos.services.evidence import EvidenceChecker
from researchos.services.memory import ResearchMemory
from researchos.services.planner import Planner
from researchos.services.writer import Writer


class Engine:
    def __init__(
        self, store: Store, provider: SourceProvider | None = None, settings: Settings | None = None
    ):
        self.store = store
        self.provider = provider
        self.settings = settings or Settings()
        self._active_provider = None

    def _provider(self, run: Run) -> SourceProvider:
        if self.provider:
            return self.provider
        if run.request.mode == "offline":
            return FixtureSourceProvider()
        if not self.settings.allow_live:
            raise ValueError("Live mode disabled; set RESEARCH_ALLOW_LIVE=true explicitly")
        from researchos.adapters.live import LiveSourceProvider

        return LiveSourceProvider(
            providers=run.request.providers,
            max_tool_calls=run.request.budget.max_tool_calls,
            web_api_key=(
                self.settings.web_api_key.get_secret_value() if self.settings.web_api_key else None
            ),
            github_token=(
                self.settings.github_token.get_secret_value()
                if self.settings.github_token
                else None
            ),
        )

    async def execute(self, run: Run) -> Run:
        started = time.perf_counter()
        run.status = "running"
        run.plan = Planner().plan(run.request)
        run.config = {
            "mode": run.request.mode,
            "planner": run.plan.strategy,
            "writer": "extractive",
            "budget": run.request.budget.model_dump(),
        }
        self.store.save(run)
        self.store.event(run.id, "plan", plan=run.plan.model_dump())
        try:
            async with asyncio.timeout(run.request.budget.max_seconds):
                await self._research(run)
        except TimeoutError:
            run.reasons.append("Time budget exhausted; partial evidence retained")
        except asyncio.CancelledError:
            run.status = "cancelled"
            run.reasons.append("Cancelled by user or process shutdown")
            self._finish(run, started)
            raise
        except Exception as exc:
            # Never put remote response bodies, URLs containing keys or config secrets in events.
            run.reasons.append(f"Research unavailable ({type(exc).__name__}); check configuration")
        finally:
            if self._active_provider is not None:
                run.config["tool_calls"] = getattr(
                    self._active_provider, "tool_calls", run.config.get("tool_calls", 0)
                )
                if hasattr(self._active_provider, "aclose"):
                    await self._active_provider.aclose()
        return self._finish(run, started)

    async def _research(self, run: Run) -> None:
        provider = self._provider(run)
        self._active_provider = provider
        memory = ResearchMemory()
        calls = 0
        for subtask in run.plan.subtasks:
            if calls >= run.request.budget.max_tool_calls:
                run.reasons.append("Tool call budget exhausted")
                break
            subtask.status = "searching"
            self.store.event(run.id, "subtask", subtask=subtask.model_dump())
            remaining = run.request.max_sources - len(run.sources)
            if remaining <= 0:
                run.reasons.append("Source limit reached before every subquestion was covered")
                break
            calls += 1
            self.store.event(run.id, "tool_call", tool="source_search", subtask_id=subtask.id)
            try:
                found = await provider.search(subtask, min(2, remaining))
                for source in found[:remaining]:
                    if run.request.time_range and source.published_at:
                        start, end = map(int, run.request.time_range.split("-"))
                        year = int(source.published_at[:4])
                        if not start <= year <= end:
                            continue
                    if run.request.time_range and not source.published_at:
                        run.reasons.append("Undated source omitted under explicit year filter")
                        continue
                    duplicate = memory.add(run.sources, source)
                    if duplicate:
                        self.store.event(
                            run.id, "deduplicated", source_id=source.id, duplicate_of=duplicate.id
                        )
                self.store.event(
                    run.id,
                    "tool_result",
                    tool="source_search",
                    status="ok",
                    subtask_id=subtask.id,
                    candidates=len(found),
                )
            except Exception as exc:
                code = getattr(exc, "code", type(exc).__name__)
                message = f"Source search for {subtask.id} failed ({code})"
                run.reasons.append(message)
                self.store.event(
                    run.id,
                    "tool_result",
                    tool="source_search",
                    status="error",
                    subtask_id=subtask.id,
                    error=code,
                )
            subtask.status = (
                "complete" if any(subtask.id in s.subtask_ids for s in run.sources) else "missing"
            )
            self.store.save(run)
            await asyncio.sleep(0)
        run.config["tool_calls"] = getattr(provider, "tool_calls", calls)
        for warning in getattr(provider, "warnings", []):
            if warning not in run.reasons:
                run.reasons.append(warning)

    def _finish(self, run: Run, started: float) -> Run:
        if run.plan:
            for subtask in run.plan.subtasks:
                if subtask.status in {"pending", "searching"}:
                    subtask.status = "missing"
            if any(s.status == "missing" for s in run.plan.subtasks):
                run.reasons.append(
                    "Some subquestions have no evidence; conclusions remain unverified"
                )
        run.reasons = list(dict.fromkeys(run.reasons))
        run.report_md = Writer().write(run)
        run.checks = EvidenceChecker().check(run)
        run.config["latency_ms"] = round((time.perf_counter() - started) * 1000, 3)
        if run.status != "cancelled":
            run.status = (
                "completed"
                if run.sources and run.checks.passed and not run.reasons
                else "partial"
                if run.sources
                else "failed"
            )
        self.store.save(run)
        self.store.event(run.id, "finished", status=run.status)
        return run
