"""Explicit manager with finite rounds, deterministic evidence and retained partial results."""

import asyncio
import time

from researchos.adapters.fixture import FixtureSourceProvider, SourceProvider
from researchos.domain.models import Run
from researchos.infra.config import Settings
from researchos.infra.store import Store
from researchos.infra.trace import Tracer
from researchos.services.evidence import EvidenceChecker
from researchos.services.memory import ResearchMemory
from researchos.services.planner import Planner
from researchos.services.roles import Researcher, Reviewer, Synthesizer
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
        trace = Tracer(self.store, run.id)
        run.status = "running"
        run.config = {
            "mode": run.request.mode,
            "writer": "extractive",
            "budget": run.request.budget.model_dump(),
            "tool_calls": 0,
            "rounds": 0,
        }
        self.store.save(run)
        with trace.span("research", "Orchestrator"):
            try:
                async with asyncio.timeout(run.request.budget.max_seconds):
                    with trace.span("plan", "Planner"):
                        run.plan = Planner().plan(run.request)
                        run.config["planner"] = run.plan.strategy
                        self.store.save(run)
                        self.store.event(run.id, "plan", plan=run.plan.model_dump())
                    provider = self._provider(run)
                    self._active_provider = provider
                    pending = run.plan.subtasks
                    for round_number in range(1, run.request.budget.max_rounds + 1):
                        run.config["rounds"] = round_number
                        with trace.span(f"research_round_{round_number}", "Researcher"):
                            await self._research(run, provider, pending, trace)
                        with trace.span("synthesize", "Synthesizer"):
                            # Only one model call per run, even if review requests a second round.
                            if round_number == run.request.budget.max_rounds:
                                await Synthesizer().synthesize(run, self.settings, self.store)
                            else:
                                run.report_md = Writer().write(run)
                        with trace.span("review", "Reviewer"):
                            review = Reviewer().review(run)
                            run.checks = review.checks
                            self.store.event(
                                run.id,
                                "review",
                                round=round_number,
                                checks=review.checks.model_dump(),
                                missing_subtasks=review.missing_subtasks,
                            )
                        if not review.request_more:
                            if round_number < run.request.budget.max_rounds:
                                with trace.span("auxiliary_synthesis", "Synthesizer"):
                                    await Synthesizer().synthesize(run, self.settings, self.store)
                            break
                        if self._calls(provider, run) >= run.request.budget.max_tool_calls:
                            run.reasons.append("Tool call budget exhausted")
                            break
                        pending = [s for s in run.plan.subtasks if s.id in review.missing_subtasks]
            except TimeoutError:
                run.reasons.append("Time budget exhausted; partial evidence retained")
            except asyncio.CancelledError:
                run.status = "cancelled"
                run.reasons.append("Cancelled by user or process shutdown")
                self._finish(run, started)
                raise
            except Exception as exc:
                run.reasons.append(
                    f"Research unavailable ({type(exc).__name__}); check configuration"
                )
            finally:
                if self._active_provider is not None:
                    run.config["tool_calls"] = self._calls(self._active_provider, run)
                    for warning in getattr(self._active_provider, "warnings", []):
                        run.reasons.append(warning)
                    if hasattr(self._active_provider, "aclose"):
                        await self._active_provider.aclose()
        return self._finish(run, started)

    @staticmethod
    def _calls(provider: SourceProvider, run: Run) -> int:
        return getattr(provider, "tool_calls", run.config["tool_calls"])

    async def _research(
        self, run: Run, provider: SourceProvider, pending: list, trace: Tracer
    ) -> None:
        memory = ResearchMemory()
        for subtask in pending:
            if self._calls(provider, run) >= run.request.budget.max_tool_calls:
                run.reasons.append("Tool call budget exhausted")
                break
            remaining = run.request.max_sources - len(run.sources)
            if remaining <= 0:
                run.reasons.append("Source limit reached before every subquestion was covered")
                break
            subtask.status = "searching"
            self.store.save(run)
            self.store.event(run.id, "subtask", subtask=subtask.model_dump())
            run.config["tool_calls"] += 1
            with trace.span(f"search_{subtask.id}", "Researcher") as span_id:
                self.store.event(
                    run.id,
                    "tool_call",
                    tool="source_search",
                    subtask_id=subtask.id,
                    span_id=span_id,
                )
                try:
                    result = await Researcher().search(provider, subtask, min(2, remaining))
                    for source in result.sources[:remaining]:
                        if not self._in_range(run, source):
                            continue
                        duplicate = memory.add(run.sources, source)
                        if duplicate:
                            self.store.event(
                                run.id,
                                "deduplicated",
                                source_id=source.id,
                                duplicate_of=duplicate.id,
                            )
                    self.store.event(
                        run.id,
                        "tool_result",
                        tool="source_search",
                        status="ok",
                        subtask_id=subtask.id,
                        candidates=len(result.sources),
                        span_id=span_id,
                    )
                except Exception as exc:
                    code = getattr(exc, "code", type(exc).__name__)
                    run.reasons.append(f"Source search for {subtask.id} failed ({code})")
                    self.store.event(
                        run.id,
                        "tool_result",
                        tool="source_search",
                        status="error",
                        subtask_id=subtask.id,
                        error=code,
                        span_id=span_id,
                    )
            subtask.status = (
                "complete" if any(subtask.id in s.subtask_ids for s in run.sources) else "missing"
            )
            self.store.save(run)
            await asyncio.sleep(0)

    @staticmethod
    def _in_range(run, source) -> bool:
        if not run.request.time_range:
            return True
        if not source.published_at:
            run.reasons.append("Undated source omitted under explicit year filter")
            return False
        start, end = map(int, run.request.time_range.split("-"))
        try:
            return start <= int(source.published_at[:4]) <= end
        except ValueError:
            run.reasons.append("Malformed source date omitted")
            return False

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
