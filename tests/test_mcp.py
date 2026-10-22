import pytest

pytest.importorskip("mcp")
from mcp import Client  # noqa: E402

from researchos.domain.models import ResearchRequest, Run  # noqa: E402
from researchos.infra.store import Store  # noqa: E402
from researchos.mcp_server import create_server  # noqa: E402
from researchos.services.engine import Engine  # noqa: E402


@pytest.mark.asyncio
async def test_real_sdk_tool_discovery_and_read_only_calls(tmp_path):
    store = Store(tmp_path / "mcp.db")
    run = await Engine(store).execute(Run(request=ResearchRequest(topic="Agent Memory")))
    server = create_server(store)
    async with Client(server) as client:
        tools = await client.list_tools()
        assert {t.name for t in tools.tools} == {"get_run", "list_sources", "search_memory"}
        result = await client.call_tool("get_run", {"run_id": run.id})
        assert not result.is_error
        result = await client.call_tool("search_memory", {"run_id": run.id, "query": "SQLite"})
        assert not result.is_error
        bad = await client.call_tool("get_run", {"run_id": "../escape"})
        assert bad.is_error


@pytest.mark.asyncio
async def test_stdio_subprocess_negotiates_and_reads_persisted_report(tmp_path):
    import sys

    from mcp import StdioServerParameters

    store = Store(tmp_path / "research.db")
    run = await Engine(store).execute(Run(request=ResearchRequest(topic="Agent Memory")))
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "researchos.mcp_server"],
        env={"RESEARCH_DATA_DIR": str(tmp_path)},
        cwd=str(tmp_path),
    )
    async with Client(parameters, read_timeout_seconds=10) as client:
        result = await client.call_tool("get_run", {"run_id": run.id})
        assert not result.is_error
        assert "completed" in str(result)
