import argparse
import asyncio
from pathlib import Path

from researchos.domain.models import ResearchRequest, Run
from researchos.infra.store import Store
from researchos.services.engine import Engine
from researchos.services.writer import render_html


def main() -> None:
    parser = argparse.ArgumentParser(description="ResearchOS reproducible offline demo")
    parser.add_argument("topic", nargs="?", default="Agent Memory 有哪些主要路线和优缺点")
    parser.add_argument("--output", default="artifacts/demo")
    args = parser.parse_args()
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    run = asyncio.run(
        Engine(Store(out / "research.db")).execute(Run(request=ResearchRequest(topic=args.topic)))
    )
    (out / "report.md").write_text(run.report_md)
    (out / "report.html").write_text(render_html(run.report_md))
    (out / "run.json").write_text(run.model_dump_json(indent=2))
    print(f"DEMO/OFFLINE run={run.id} status={run.status} sources={len(run.sources)}")
    print(f"Citation checks: {run.checks.passed}; artifacts: {out}")


if __name__ == "__main__":
    main()
