"""Owned teaching notes, intentionally fixture:// rather than fake internet sources."""

import hashlib
import json
from importlib.resources import files
from typing import Protocol

from researchos.domain.models import SourceRecord, Subtask


class SourceProvider(Protocol):
    async def search(self, subtask: Subtask, limit: int) -> list[SourceRecord]: ...


class FixtureSourceProvider:
    async def search(self, subtask: Subtask, limit: int) -> list[SourceRecord]:
        items = json.loads(files("researchos").joinpath("data/fixtures.json").read_text())
        # Deterministic routing is intentional, and never represents topic relevance.
        index = (int(subtask.id[1:]) - 1) % 3
        selected = items[index::3][:limit]
        return [
            SourceRecord(
                id=item["id"],
                url=f"fixture://researchos/{item['id']}",
                title=item["title"],
                category="fixture",
                excerpt=item["excerpt"],
                provenance="fixture",
                content_hash=hashlib.sha256(item["excerpt"].encode()).hexdigest(),
                subtask_ids=[subtask.id],
            )
            for item in selected
        ]
