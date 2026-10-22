"""Bounded asyncio jobs for one server process, with durable SQLite snapshots.

The admitted capacity is max_jobs execution slots plus max_queue waiting slots.
Each accepted request owns one task and one fresh Engine. SQLite stores results;
only unfinished tasks occupy memory. Multiple application workers are unsupported.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Protocol

from researchos.domain.models import ResearchRequest, Run
from researchos.infra.config import Settings
from researchos.infra.store import Store
from researchos.services.engine import Engine


class QueueFull(Exception):
    """Admission rejected before creating a run or task."""


class _Engine(Protocol):
    async def execute(self, run: Run) -> Run: ...


class JobManager:
    def __init__(
        self,
        store: Store,
        settings: Settings,
        *,
        engine_factory: Callable[[], _Engine] | None = None,
    ):
        self.store = store
        self.settings = settings
        self._engine_factory = engine_factory or (lambda: Engine(store, settings=settings))
        self._semaphore = asyncio.Semaphore(settings.max_jobs)
        self._tasks: dict[str, asyncio.Task] = {}
        self._running: set[str] = set()
        self._cancel_reasons: dict[str, str] = {}
        self._closed = False

    def is_pending(self, run_id: str) -> bool:
        task = self._tasks.get(run_id)
        return task is not None and not task.done()

    @property
    def active_count(self) -> int:
        return len(self._running)

    @property
    def tracked_count(self) -> int:
        return len(self._tasks)

    @property
    def queued_count(self) -> int:
        # Execution slots are reserved at admission, before their tasks get CPU.
        return max(0, len(self._tasks) - self.settings.max_jobs)

    async def submit(self, request: ResearchRequest) -> Run:
        if self._closed:
            raise QueueFull("Job manager is shutting down")
        if len(self._tasks) >= self.settings.max_jobs + self.settings.max_queue:
            raise QueueFull("Research queue is full")
        run = Run(request=request.model_copy(deep=True))
        self.store.save(run)
        self.store.event(run.id, "queued", status="queued")
        task = asyncio.create_task(self._execute(run), name=f"research:{run.id}")
        self._tasks[run.id] = task
        task.add_done_callback(lambda finished, run_id=run.id: self._done(run_id, finished))
        # The worker's mutable Run is never exposed to API callers.
        return run.model_copy(deep=True)

    def _done(self, run_id: str, task: asyncio.Task):
        if self._tasks.get(run_id) is task:
            self._tasks.pop(run_id, None)
            self._cancel_reasons.pop(run_id, None)
        if not task.cancelled():
            task.exception()  # retrieve failures even if persistence itself became unavailable

    def _cancelled(self, run: Run, reason: str):
        previous = self.store.get(run.id)
        run.status = "cancelled"
        if reason not in run.reasons:
            run.reasons.append(reason)
        self.store.save(run)
        if previous is None or previous.status != "cancelled":
            self.store.event(run.id, "cancelled", status="cancelled", reason=reason)

    async def _execute(self, run: Run):
        result = run
        cancelled = False
        try:
            async with self._semaphore:
                self._running.add(run.id)
                run.status = "running"
                self.store.save(run)
                self.store.event(run.id, "started", status="running")
                # Never share provider/client state between concurrently executing jobs.
                engine = self._engine_factory()
                executed = await engine.execute(run)
                if not isinstance(executed, Run) or executed.id != run.id:
                    raise TypeError("Engine must return the submitted run")
                result = executed
                if result.status in {"queued", "running"}:
                    result.status = "failed"
                    result.reasons.append("Job execution ended without a terminal result")
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            result.status = "failed"
            result.reasons.append("Job execution failed; check configuration")
            self.store.event(run.id, "job_error", error="engine_failed")
        finally:
            self._running.discard(run.id)
            reason = self._cancel_reasons.get(run.id)
            if cancelled or reason:
                self._cancelled(result, reason or "Cancelled by process shutdown")
            else:
                self.store.save(result)
            self.store.event(run.id, "job_finished", status=result.status)

    async def cancel(self, run_id: str) -> Run | None:
        run = self.store.get(run_id)
        if run is None or run.status not in {"queued", "running"}:
            return run
        reason = "Cancelled by user"
        self._cancel_reasons[run_id] = reason
        self._cancelled(run, reason)
        task = self._tasks.get(run_id)
        if task is not None:
            task.cancel()
            # Await engine cleanup before returning the final snapshot. Even an
            # engine that suppresses CancelledError cannot overwrite cancellation.
            await asyncio.gather(task, return_exceptions=True)
        else:
            self._cancel_reasons.pop(run_id, None)
        return self.store.get(run_id)

    async def recover(self) -> int:
        """Call once at startup, before admitting jobs; never silently resume work."""
        if self._tasks:
            raise RuntimeError("Recovery requires an idle job manager")
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT body FROM runs WHERE json_extract(body, '$.status') IN ('queued','running')"
            ).fetchall()
        for row in rows:
            run = Run.model_validate_json(row[0])
            previous = run.status
            run.status = "failed"
            run.reasons.append("Interrupted by process restart; previous execution cannot resume")
            self.store.save(run)
            self.store.event(run.id, "interrupted", previous_status=previous, status="failed")
        return len(rows)

    async def shutdown(self):
        self._closed = True
        tasks = list(self._tasks.items())
        for run_id, task in tasks:
            reason = "Cancelled by process shutdown"
            self._cancel_reasons.setdefault(run_id, reason)
            run = self.store.get(run_id)
            if run is not None and run.status in {"queued", "running"}:
                self._cancelled(run, self._cancel_reasons[run_id])
            task.cancel()
        if tasks:
            await asyncio.gather(*(task for _, task in tasks), return_exceptions=True)
        # Includes tasks cancelled before their coroutine could start/finalize.
        for run_id, task in tasks:
            self._done(run_id, task)
