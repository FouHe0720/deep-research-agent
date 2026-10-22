import ipaddress
import re
from datetime import datetime
from urllib.parse import urlsplit

from researchos.domain.models import CheckResult, Run
from researchos.services.writer import reference_line, safe_text


class EvidenceChecker:
    def check(self, run: Run) -> CheckResult:
        errors = []
        sources = {s.id: s for s in run.sources}
        if len(sources) != len(run.sources):
            errors.append("duplicate source identifiers")
        ids = []
        tasks = {s.id for s in run.plan.subtasks} if run.plan else set()
        for c in run.claims:
            ids.append(c.citation_id)
            s = sources.get(c.source_id)
            if not s:
                errors.append(f"missing source: {c.source_id}")
                continue
            if not c.passage.strip() or c.passage not in s.excerpt:
                errors.append(f"unbound passage: {c.id}")
            if c.subtask_id not in s.subtask_ids or c.subtask_id not in tasks:
                errors.append(f"invalid subquestion binding: {c.id}")
            if safe_text(c.text) not in run.report_md:
                errors.append(f"claim missing from report: {c.id}")
            if s.provenance == "network":
                parsed = urlsplit(s.url)
                if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username:
                    errors.append(f"invalid network URL: {s.id}")
                try:
                    if not ipaddress.ip_address(parsed.hostname or "").is_global:
                        errors.append(f"nonpublic network URL: {s.id}")
                except ValueError:
                    if parsed.hostname in {"localhost", "metadata.google.internal"}:
                        errors.append(f"nonpublic network URL: {s.id}")
            if s.provenance == "fixture" and not s.url.startswith("fixture://"):
                errors.append(f"fixture masquerades as network: {s.id}")
            if s.published_at:
                try:
                    datetime.fromisoformat(s.published_at.replace("Z", "+00:00"))
                except ValueError:
                    errors.append(f"invalid source date: {s.id}")
                if not s.date_basis:
                    errors.append(f"unattributed date: {s.id}")
            if reference_line(c.citation_id, s) not in run.report_md:
                errors.append(f"reference content mismatch: {c.citation_id}")
        if sorted(ids) != list(range(1, len(ids) + 1)):
            errors.append("citation IDs must be unique and contiguous")
        body = re.split(r"## (?:参考文献|References)", run.report_md)[0]
        cited = {int(n) for n in re.findall(r"(?<!\\)\[(\d+)\]", body)}
        if cited != set(ids):
            errors.append("orphan or missing inline citations")
        refs = [int(n) for n in re.findall(r"^\[(\d+)\]:", run.report_md, re.MULTILINE)]
        if sorted(refs) != sorted(ids):
            errors.append("reference numbering mismatch")
        return CheckResult(
            passed=not errors,
            errors=errors,
            warnings=[
                "Structure only; source reachability and semantic faithfulness are separate.",
                "Fixture evidence is owned demo material, never live source validity.",
            ],
        )
