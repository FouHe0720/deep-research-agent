import asyncio

import pytest

from researchos.domain.models import ResearchRequest, Run
from researchos.infra.config import Settings
from researchos.infra.jobs import JobManager, QueueFull
from researchos.infra.store import Store


def request(topic="Agent memory"):
    return ResearchRequest(topic=topic)


def setup(tmp_path, *, max_jobs=1, max_queue=2, engine_factory=None):
    store = Store(tmp_path / "jobs.db")
    settings = Settings(data_dir=tmp_path, max_jobs=max_jobs, max_queue=max_queue)
    manager = JobManager(store, settings, engine_factory=engine_factory)
    return store, manager


async def settle():
    for _ in range(5):
        await asyncio.sleep(0)


async def test_submission_returns_queued_snapshot_and_real_engine_finishes_durably(tmp_path):
    store, manager = setup(tmp_path)
    try:
        submitted = await manager.submit(request())
        assert submitted.status == "queued"
        while manager.tracked_count:
            await settle()
        completed = store.get(submitted.id)
        assert completed.status in {"completed", "partial"}
        assert completed.sources and completed.report_md
        assert submitted.status == "queued"
        assert Store(tmp_path / "jobs.db").get(submitted.id).report_md == completed.report_md
    finally:
        await manager.shutdown()


async def test_concurrency_and_total_admission_capacity_are_bounded(tmp_path):
    release = asyncio.Event()
    active = 0
    peak = 0
    instances = []

    class BlockingEngine:
        def __init__(self):
            instances.append(self)

        async def execute(self, run):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            try:
                await release.wait()
                run.status = "completed"
                return run
            finally:
                active -= 1

    store, manager = setup(tmp_path, max_jobs=2, max_queue=2, engine_factory=BlockingEngine)
    try:
        submitted = [await manager.submit(request()) for _ in range(4)]
        await settle()
        assert manager.active_count == 2 and manager.queued_count == 2
        assert manager.tracked_count == 4
        with pytest.raises(QueueFull):
            await manager.submit(request())
        assert len(store.list()) == 4
        release.set()
        while manager.tracked_count:
            await settle()
        assert peak == 2
        assert len(instances) == 4
        assert all(store.get(run.id).status == "completed" for run in submitted)
        assert manager.active_count == manager.queued_count == 0
    finally:
        await manager.shutdown()


async def test_cancel_queued_and_running_jobs_preserves_cancelled_state(tmp_path):
    entered = []

    class BlockingEngine:
        async def execute(self, run):
            entered.append(run.id)
            await asyncio.Event().wait()

    store, manager = setup(tmp_path, engine_factory=BlockingEngine)
    try:
        running = await manager.submit(request())
        queued = await manager.submit(request())
        await settle()
        assert entered == [running.id]
        cancelled_queued = await manager.cancel(queued.id)
        assert cancelled_queued.status == "cancelled"
        assert cancelled_queued.reasons == ["Cancelled by user"]
        assert entered == [running.id]
        cancelled_running = await manager.cancel(running.id)
        assert cancelled_running.status == "cancelled"
        assert "Cancelled by user" in cancelled_running.reasons
        await settle()
        assert store.get(running.id).status == store.get(queued.id).status == "cancelled"
        assert manager.tracked_count == 0
    finally:
        await manager.shutdown()


async def test_cancel_before_task_starts_and_idempotent_unknown_cancel(tmp_path):
    store, manager = setup(tmp_path)
    try:
        run = await manager.submit(request())
        assert (await manager.cancel(run.id)).status == "cancelled"
        assert (await manager.cancel(run.id)).status == "cancelled"
        assert await manager.cancel("unknown") is None
        assert manager.tracked_count == 0
        assert not store.get(run.id).sources
        assert any(event["kind"] == "cancelled" for event in store.events(run.id))
    finally:
        await manager.shutdown()


async def test_engine_suppressed_cancellation_cannot_overwrite_terminal_status(tmp_path):
    class ResistantEngine:
        async def execute(self, run):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                run.status = "completed"
                store.save(run)
                return run

    store, manager = setup(tmp_path, engine_factory=ResistantEngine)
    try:
        run = await manager.submit(request())
        await settle()
        cancelled = await manager.cancel(run.id)
        assert cancelled.status == "cancelled"
        assert "Cancelled by user" in cancelled.reasons
        await settle()
        assert store.get(run.id).status == "cancelled"
    finally:
        await manager.shutdown()


async def test_unhandled_engine_errors_are_safe_and_release_admission(tmp_path):
    class BrokenEngine:
        async def execute(self, run):
            raise RuntimeError("token=secret-private-key and secret response")

    store, manager = setup(tmp_path, engine_factory=BrokenEngine)
    try:
        run = await manager.submit(request())
        while manager.tracked_count:
            await settle()
        result = store.get(run.id)
        assert result.status == "failed"
        assert result.reasons == ["Job execution failed; check configuration"]
        assert "secret" not in str(store.events(run.id))
        another = await manager.submit(request())
        assert another.status == "queued"
    finally:
        await manager.shutdown()


async def test_restart_recovery_marks_all_interrupted_runs_and_keeps_results(tmp_path):
    store, manager = setup(tmp_path)
    # Exceeds Store.list's default page size to verify recovery is not truncated.
    interrupted = [
        Run(request=request(), status="running" if i % 2 else "queued") for i in range(55)
    ]
    completed = Run(request=request(), status="completed", report_md="Persisted report")
    cancelled = Run(request=request(), status="cancelled")
    for run in [*interrupted, completed, cancelled]:
        store.save(run)
    try:
        assert await manager.recover() == 55
        assert await manager.recover() == 0
        for run in interrupted:
            recovered = store.get(run.id)
            assert recovered.status == "failed"
            assert "Interrupted by process restart" in recovered.reasons[0]
            assert store.events(run.id)[0]["kind"] == "interrupted"
        assert store.get(completed.id).report_md == "Persisted report"
        assert store.get(completed.id).status == "completed"
        assert store.get(cancelled.id).status == "cancelled"
    finally:
        await manager.shutdown()


async def test_shutdown_cancels_and_awaits_every_task_then_rejects_new_jobs(tmp_path):
    cleaned = asyncio.Event()

    class BlockingEngine:
        async def execute(self, run):
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                cleaned.set()

    store, manager = setup(tmp_path, engine_factory=BlockingEngine)
    running = await manager.submit(request())
    queued = await manager.submit(request())
    await settle()
    with pytest.raises(RuntimeError, match="idle"):
        await manager.recover()
    await manager.shutdown()
    assert cleaned.is_set()
    assert manager.tracked_count == manager.active_count == 0
    assert store.get(running.id).status == store.get(queued.id).status == "cancelled"
    assert "Cancelled by process shutdown" in store.get(queued.id).reasons
    with pytest.raises(QueueFull, match="shutting down"):
        await manager.submit(request())
    await manager.shutdown()


async def test_shutdown_before_worker_starts_is_durable_and_emits_event(tmp_path):
    store, manager = setup(tmp_path)
    run = await manager.submit(request())
    await manager.shutdown()
    assert store.get(run.id).status == "cancelled"
    assert manager.tracked_count == 0
    assert any(event["kind"] == "cancelled" for event in store.events(run.id))
