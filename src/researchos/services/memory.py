"""Deterministic deduplication; preserve associations and audit rejected candidates."""

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from researchos.domain.models import SourceRecord


def canonical_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme == "fixture":
        return url
    host = (parts.hostname or "").lower().rstrip(".")
    port = parts.port
    authority = host if not port or port in (80, 443) else f"{host}:{port}"
    query = urlencode(
        sorted(
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid"}
        )
    )
    return urlunsplit((parts.scheme.lower(), authority, parts.path.rstrip("/") or "/", query, ""))


def normalize_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    return re.sub(r"^(?:https?://(?:dx\.)?doi.org/|doi:\s*)", "", doi.strip().lower())


class ResearchMemory:
    def add(self, sources: list[SourceRecord], candidate: SourceRecord) -> SourceRecord | None:
        candidate.url = canonical_url(candidate.url)
        candidate.doi = normalize_doi(candidate.doi)
        candidate.content_hash = hashlib.sha256(candidate.excerpt.strip().encode()).hexdigest()
        for source in sources:
            duplicate = (
                source.url == candidate.url
                or (candidate.doi and normalize_doi(source.doi) == candidate.doi)
                or source.content_hash == candidate.content_hash
            )
            if duplicate:
                candidate.duplicate_of = source.id
                source.subtask_ids = list(dict.fromkeys(source.subtask_ids + candidate.subtask_ids))
                return source
        sources.append(candidate)
        return None
