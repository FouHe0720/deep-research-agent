"""Local-first HTTP application. Run with one uvicorn worker."""

import asyncio
import json
import re
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field, field_validator

from researchos.adapters.integrations import AgentOpsExporter
from researchos.domain.models import Contract, ResearchRequest, Run
from researchos.infra.config import Settings
from researchos.infra.jobs import JobManager, QueueFull
from researchos.infra.store import Store
from researchos.services.writer import render_html

TERMINAL = {"completed", "partial", "failed", "cancelled"}
STATIC = Path(__file__).parents[1] / "static"


class AgentOpsConfig(Contract):
    planner: bool = True
    tool_descriptions: Literal["basic", "improved"] = "improved"
    tool_retries: int = Field(0, ge=0, le=1)


class AgentOpsRequest(Contract):
    task: str = Field(min_length=3, max_length=500)
    category: Literal["retrieval"] = "retrieval"
    fixtures: dict = Field(default_factory=dict)
    seed: int = Field(0, ge=0, le=2**31 - 1)
    config: AgentOpsConfig = Field(default_factory=AgentOpsConfig)

    @field_validator("task")
    @classmethod
    def valid_task(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 3:
            raise ValueError("task must contain at least 3 non-whitespace characters")
        return value


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store = Store(settings.database)
        jobs = JobManager(store, settings)
        await jobs.recover()
        app.state.store, app.state.jobs = store, jobs
        try:
            yield
        finally:
            await jobs.shutdown()

    app = FastAPI(title="ResearchOS", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings

    @app.middleware("http")
    async def boundaries(request: Request, call_next):
        if request.url.hostname not in settings.allowed_hosts:
            return JSONResponse(
                {"error": {"code": "invalid_host", "message": "Host is not allowed"}},
                status_code=400,
            )
        if request.method in {"POST", "PUT", "PATCH"}:
            body = bytearray()
            async for chunk in request.stream():
                if len(body) + len(chunk) > 32_768:
                    return JSONResponse(
                        {
                            "error": {
                                "code": "body_too_large",
                                "message": "Maximum request is 32768 bytes",
                            }
                        },
                        status_code=413,
                    )
                body.extend(chunk)
            # Starlette forwards cached request bodies from HTTP middleware.
            request._body = bytes(body)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "frame-src 'self' blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'self'"
        )
        return response

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return JSONResponse(
            {"error": {"code": str(exc.status_code), "message": str(exc.detail)}},
            status_code=exc.status_code,
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        details = [
            {"field": list(e["loc"]), "message": e["msg"], "type": e["type"]} for e in exc.errors()
        ]
        return JSONResponse(
            {
                "error": {
                    "code": "validation_error",
                    "message": "Invalid request",
                    "details": details,
                }
            },
            status_code=422,
        )

    async def authorize(authorization: Annotated[str | None, Header()] = None):
        if settings.api_key:
            expected = "Bearer " + settings.api_key.get_secret_value()
            if not secrets.compare_digest((authorization or "").encode(), expected.encode()):
                raise HTTPException(401, "Valid Bearer API key required")

    def get_run(run_id: str) -> Run:
        if not re.fullmatch(r"[a-f0-9]{32}", run_id):
            raise HTTPException(404, "Research run not found")
        run = app.state.store.get(run_id)
        if run is None:
            raise HTTPException(404, "Research run not found")
        return run

    @app.get("/health")
    async def health():
        with app.state.store.connect() as db:
            db.execute("SELECT 1").fetchone()
        return {
            "status": "ok",
            "service": "researchos",
            "active": app.state.jobs.active_count,
            "queued": app.state.jobs.queued_count,
        }

    @app.get("/api/config")
    async def config():
        return {"allow_live": settings.allow_live, "auth_required": settings.api_key is not None}

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    auth = [Depends(authorize)]

    @app.post("/api/research", status_code=202, response_model=Run, dependencies=auth)
    async def research(body: ResearchRequest):
        if body.mode == "live" and not settings.allow_live:
            raise HTTPException(403, "Live mode disabled; enable RESEARCH_ALLOW_LIVE explicitly")
        if body.mode == "live" and "web" in body.providers and not settings.web_api_key:
            raise HTTPException(422, "Web Search requires RESEARCH_WEB_API_KEY (Brave)")
        try:
            return await app.state.jobs.submit(body)
        except QueueFull:
            raise HTTPException(
                429,
                "Research queue is full; retry after a task finishes",
                headers={"Retry-After": "5"},
            ) from None

    @app.get("/api/research", dependencies=auth)
    async def history(limit: Annotated[int, Query(ge=1, le=100)] = 50):
        return [
            {
                "id": run.id,
                "topic": run.request.topic,
                "status": run.status,
                "mode": run.request.mode,
                "created_at": run.created_at,
            }
            for run in app.state.store.list(limit)
        ]

    @app.get("/api/research/{run_id}", response_model=Run, dependencies=auth)
    async def detail(run_id: str):
        return get_run(run_id)

    @app.post("/api/research/{run_id}/cancel", response_model=Run, dependencies=auth)
    async def cancel(run_id: str):
        get_run(run_id)
        return await app.state.jobs.cancel(run_id)

    @app.get("/api/research/{run_id}/sources", dependencies=auth)
    async def sources(run_id: str):
        return get_run(run_id).sources

    @app.get("/api/research/{run_id}/memory", dependencies=auth)
    async def memory(run_id: str, q: Annotated[str, Query(min_length=1, max_length=500)]):
        get_run(run_id)
        return app.state.store.search(run_id, q)

    @app.get("/api/research/{run_id}/report", dependencies=auth)
    async def report(run_id: str, format: Literal["md", "html"] = "md"):
        run = get_run(run_id)
        if not run.report_md:
            raise HTTPException(409, "Report not ready")
        headers = {"Content-Disposition": f'inline; filename="research-{run_id}.{format}"'}
        return (
            HTMLResponse(render_html(run.report_md), headers=headers)
            if format == "html"
            else Response(run.report_md, media_type="text/markdown", headers=headers)
        )

    @app.get("/api/research/{run_id}/trace", dependencies=auth)
    async def trace(run_id: str, format: Literal["json", "agentops"] = "json"):
        run = get_run(run_id)
        events = app.state.store.events(run_id)
        return AgentOpsExporter.from_events(run, events) if format == "agentops" else events

    @app.get("/api/research/{run_id}/events", dependencies=auth)
    async def events(
        run_id: str,
        request: Request,
        after: Annotated[int, Query(ge=0, le=2**63 - 1)] = 0,
        last_event_id: Annotated[str | None, Header()] = None,
    ):
        get_run(run_id)
        if last_event_id:
            try:
                value = int(last_event_id)
                if value < 0 or value > 2**63 - 1:
                    raise ValueError
                after = max(after, value)
            except ValueError:
                raise HTTPException(400, "Last-Event-ID must be an integer") from None

        async def stream():
            cursor = after
            idle = 0
            while True:
                if await request.is_disconnected():
                    break
                rows = app.state.store.events(run_id, cursor)
                for row in rows:
                    cursor = row["seq"]
                    data = json.dumps(row, ensure_ascii=False)
                    yield f"id: {cursor}\ndata: {data}\n\n"
                current = get_run(run_id)
                if current.status in TERMINAL and not app.state.jobs.is_pending(run_id):
                    # Yielding the earlier snapshot may let the worker append its final
                    # spans; drain those rows once the task itself has stopped.
                    for row in app.state.store.events(run_id, cursor):
                        cursor = row["seq"]
                        data = json.dumps(row, ensure_ascii=False)
                        yield f"id: {cursor}\ndata: {data}\n\n"
                    break
                idle += 1
                if idle % 20 == 0:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(0.25)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
        )

    @app.post("/api/agentops/research", dependencies=auth)
    async def agentops(body: AgentOpsRequest):
        if not body.config.planner or body.config.tool_descriptions != "improved" or body.fixtures:
            raise HTTPException(
                422,
                "Supports built-in offline fixtures and planner=true, "
                "tool_descriptions=improved only; supplied fixtures are unsupported",
            )
        request = ResearchRequest(
            topic=body.task, budget={"max_rounds": body.config.tool_retries + 1}
        )
        try:
            run = await app.state.jobs.submit(request)
        except QueueFull:
            raise HTTPException(429, "Research queue is full") from None
        while run.status not in TERMINAL:
            await asyncio.sleep(0.02)
            run = get_run(run.id)
        return AgentOpsExporter.from_events(run, app.state.store.events(run.id))

    return app


app = create_app()
