import argparse
import asyncio
from pathlib import Path

from researchos.adapters.campus import ADMISSION_TOPIC
from researchos.adapters.integrations import AgentOpsExporter
from researchos.domain.models import Budget, ResearchRequest, Run
from researchos.infra.config import Settings
from researchos.infra.store import Store
from researchos.services.engine import Engine
from researchos.services.writer import render_html


def main() -> None:
    parser = argparse.ArgumentParser(description="ResearchOS bounded research, offline by default")
    parser.add_argument("topic", nargs="?", default=None)
    parser.add_argument("--output", default="artifacts/demo")
    parser.add_argument("--mode", choices=["offline", "live"], default="offline")
    parser.add_argument("--corpus", choices=["teaching", "campus"], default="teaching")
    parser.add_argument("--campus", choices=["威海", "济南", "青岛"])
    parser.add_argument("--document-year", type=int)
    parser.add_argument("--cohort", type=int)
    parser.add_argument(
        "--providers", nargs="+", choices=["github", "arxiv", "web"], default=["github", "arxiv"]
    )
    parser.add_argument("--max-sources", type=int, default=6)
    parser.add_argument("--max-seconds", type=float, default=60)
    parser.add_argument("--max-cost-usd", type=float, default=0)
    parser.add_argument("--language", choices=["zh", "en"], default="zh")
    parser.add_argument("--time-range", default=None)
    args = parser.parse_args()
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    store = Store(out / "research.db")
    request = ResearchRequest(
        topic=args.topic
        or (ADMISSION_TOPIC if args.corpus == "campus" else "Agent Memory 有哪些主要路线和优缺点"),
        mode=args.mode,
        corpus=args.corpus,
        campus=args.campus,
        document_year=args.document_year,
        cohort=args.cohort,
        providers=args.providers,
        max_sources=args.max_sources,
        language=args.language,
        time_range=args.time_range,
        budget=Budget(max_seconds=args.max_seconds, max_cost_usd=args.max_cost_usd),
    )
    run = asyncio.run(Engine(store, settings=Settings()).execute(Run(request=request)))
    (out / "report.md").write_text(run.report_md)
    (out / "report.html").write_text(render_html(run.report_md))
    (out / "run.json").write_text(run.model_dump_json(indent=2))
    import json

    (out / "trace.json").write_text(json.dumps(store.events(run.id), ensure_ascii=False, indent=2))
    (out / "agentops.json").write_text(
        json.dumps(
            AgentOpsExporter.from_events(run, store.events(run.id)), ensure_ascii=False, indent=2
        )
    )
    label = (
        ("CAMPUS/LOCAL" if args.corpus == "campus" else "DEMO/OFFLINE")
        if args.mode == "offline"
        else "LIVE"
    )
    print(f"{label} run={run.id} status={run.status} sources={len(run.sources)}")
    print(f"Citation checks: {run.checks.passed}; artifacts: {out}")
    for reason in run.reasons:
        print(f"  - {reason}")
    if run.status == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
