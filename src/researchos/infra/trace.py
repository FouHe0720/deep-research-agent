"""Persisted spans; use contexts around awaited work to measure actual durations."""

import time
from collections.abc import Iterator
from contextlib import contextmanager

from researchos.domain.models import uid
from researchos.infra.store import Store


class Tracer:
    def __init__(self, store: Store, run_id: str):
        self.store, self.run_id = store, run_id
        self.stack: list[str] = []

    @contextmanager
    def span(self, name: str, role: str) -> Iterator[str]:
        span_id = uid()
        parent_id = self.stack[-1] if self.stack else None
        self.stack.append(span_id)
        self.store.event(
            self.run_id,
            "span_start",
            span_id=span_id,
            parent_span_id=parent_id,
            name=name,
            role=role,
        )
        started = time.perf_counter()
        status = "ok"
        try:
            yield span_id
        except BaseException:
            status = "error"
            raise
        finally:
            self.stack.pop()
            self.store.event(
                self.run_id,
                "span_end",
                span_id=span_id,
                parent_span_id=parent_id,
                name=name,
                role=role,
                status=status,
                duration_ms=round((time.perf_counter() - started) * 1000, 3),
            )
