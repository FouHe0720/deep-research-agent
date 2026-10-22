import re

from researchos.domain.models import CheckResult, Run


class EvidenceChecker:
    def check(self, run: Run) -> CheckResult:
        errors = []
        sources = {s.id: s for s in run.sources}
        ids = []
        for c in run.claims:
            ids.append(c.citation_id)
            s = sources.get(c.source_id)
            if not s:
                errors.append(f"missing source: {c.source_id}")
                continue
            if not c.passage.strip() or c.passage not in s.excerpt:
                errors.append(f"unbound passage: {c.id}")
            if s.provenance == "network" and not s.url.startswith(("https://", "http://")):
                errors.append(f"invalid network URL: {s.id}")
            if s.provenance == "fixture" and not s.url.startswith("fixture://"):
                errors.append(f"fixture masquerades as network: {s.id}")
            if s.published_at and not s.date_basis:
                errors.append(f"unattributed date: {s.id}")
        if sorted(ids) != list(range(1, len(ids) + 1)):
            errors.append("citation IDs must be unique and contiguous")
        body = run.report_md.split("## 参考文献")[0]
        cited = {int(n) for n in re.findall(r"\[(\d+)\]", body)}
        if cited != set(ids):
            errors.append("orphan or missing inline citations")
        refs = {int(n) for n in re.findall(r"^\[(\d+)\]:", run.report_md, re.MULTILINE)}
        if refs != set(ids):
            errors.append("reference numbering mismatch")
        return CheckResult(
            passed=not errors,
            errors=errors,
            warnings=["Structural verification only; semantic faithfulness requires human review."],
        )
