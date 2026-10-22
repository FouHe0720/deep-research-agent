# Implementation plan

Only this independent project directory is modified. Default mode is fixture/offline, without keys, paid services or local models.

1. **V0 — deterministic MVP**: typed request/plan/source/claim contracts; 3 subquestions; owned fixture corpus; SQLite memory + FTS; deterministic cited Markdown/HTML; offline CLI and tests. Pass tests, commit.
2. **V1 — bounded research**: SSRF-safe HTTP reader, public arXiv/GitHub and configurable Web Search; canonical/DOI/content deduplication; deterministic citation verification; timeout, source/time/tool/cost limits; live smoke explicitly separated. Pass tests, commit.
3. **V2 — orchestration**: explicit Researcher/Synthesizer/Reviewer contracts, bounded review loop, durable events and spans, cancellation/restart recovery; optional OpenAI-compatible/Gateway, RAG and AgentOps adapters with local fallbacks. Pass tests, commit.
4. **V3 — product and delivery**: FastAPI asynchronous jobs + SSE, usable local Web UI, exports/history/citation detail/trace; 30 categorized eval cases and reviewed expansion to 100; real generated artifacts; CI, Docker, Chinese documentation; local quickstart/browser/API verification; privacy scan; public GitHub publication.

## Layout
`src/researchos/{domain,services,adapters,infra,api,static}`, `tests`, `fixtures`, `eval`, `scripts`, `docs`, `.github/workflows`.

## Acceptance
Offline install/demo/test/lint must actually pass. Each stage has a commit. Metrics come from generated runs, fixture results never imply internet research quality. Live tests report exact providers and failures. Docker and CI status are recorded only after execution. No credentials, runtime databases or personal paths are committed. Publish only under verified FouHe0720/deep-research-agent, without force pushes.

## Executed milestones
V0: 3 passing tests; V1: 58; V2: 103; V3 local acceptance: 120, plus actual browser, Docker and live GitHub/arXiv checks. Stage commits retain separate histories. Public release and remote CI results are recorded in docs/VALIDATION.md after execution.
