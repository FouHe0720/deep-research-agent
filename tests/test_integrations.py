import json

import httpx
import pytest

from researchos.adapters.integrations import (
    AgentOpsExporter,
    GatewayClient,
    IntegrationError,
    RAGAdapter,
)
from researchos.domain.models import ResearchRequest, Run

MESSAGES = [{"role": "user", "content": "Summarize this evidence: 中文"}]


def llm_client(handler, **kwargs):
    return GatewayClient(
        "http://127.0.0.1:9999/v1",
        "operator-model",
        input_usd_per_million=1,
        output_usd_per_million=2,
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


@pytest.mark.parametrize("kind", ["gateway", "openai"])
async def test_llm_wire_contract_and_observed_usage(kind):
    def handler(request):
        body = json.loads(request.content)
        assert body["model"] == "operator-model"
        assert body["messages"] == MESSAGES
        assert request.headers["Authorization"] == "Bearer synthetic-test-key"
        assert request.headers["Accept-Encoding"] == "identity"
        if kind == "gateway":
            assert request.url.path == "/v1/chat"
            assert body["max_tokens"] == 400
            assert "max_completion_tokens" not in body
            response = {"answer": "Grounded summary", "request_id": "req-1"}
        else:
            assert request.url.path == "/v1/chat/completions"
            assert body["max_completion_tokens"] == 400
            assert "max_tokens" not in body
            response = {"choices": [{"message": {"content": "Grounded summary"}}]}
        response["usage"] = {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25}
        return httpx.Response(200, json=response)

    client = llm_client(handler, kind=kind, api_key="synthetic-test-key")
    try:
        result = await client.generate(MESSAGES, max_cost_usd=1)
        assert result.text == "Grounded summary"
        assert result.usage.total_tokens == 25
        assert result.cost_usd == pytest.approx(0.00003)
        assert result.model == "operator-model"
    finally:
        await client.aclose()


@pytest.mark.parametrize("usage", [None, {}, {"total_tokens": None}])
async def test_keyless_missing_usage_stays_null(usage):
    def handler(request):
        assert "Authorization" not in request.headers
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "text"}}], "usage": usage}
        )

    client = llm_client(handler)
    try:
        result = await client.generate(MESSAGES, max_cost_usd=1)
        assert result.usage is None
        assert result.cost_usd is None
    finally:
        await client.aclose()


async def test_partial_usage_cannot_invent_cost():
    client = llm_client(
        lambda request: httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "text"}}],
                "usage": {"total_tokens": 20},
            },
        )
    )
    try:
        result = await client.generate(MESSAGES, max_cost_usd=1)
        assert result.usage.total_tokens == 20
        assert result.usage.prompt_tokens is None
        assert result.cost_usd is None
    finally:
        await client.aclose()


async def test_cost_preflight_denies_io_and_counts_utf8_bytes():
    calls = []
    client = llm_client(lambda request: calls.append(request))
    try:
        minimum = (256 + len(MESSAGES[0]["content"].encode()) + len("user") + 128 + 400 * 2) / 1e6
        assert client.estimate_cost(MESSAGES) >= minimum
        with pytest.raises(IntegrationError, match="^cost_budget_exhausted$"):
            await client.generate(MESSAGES, max_cost_usd=0)
        assert not calls
    finally:
        await client.aclose()

    unpriced = GatewayClient(
        "http://127.0.0.1:9999/v1",
        "explicit-model",
        transport=httpx.MockTransport(lambda r: calls.append(r)),
    )
    try:
        assert unpriced.estimate_cost(MESSAGES) is None
        with pytest.raises(IntegrationError, match="^pricing_required$"):
            await unpriced.generate(MESSAGES, max_cost_usd=1)
        assert not calls
    finally:
        await unpriced.aclose()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"base_url": "http://user:secret@example.test/v1"},
        {"base_url": "http://example.test/v1?api_key=secret"},
        {"base_url": "file:///secret"},
        {"model": ""},
        {"model": "model\nsecret"},
        {"input_usd_per_million": float("nan")},
        {"api_key": "secret\nheader"},
    ],
)
def test_configuration_errors_are_safe(kwargs):
    parameters = {"base_url": "http://127.0.0.1:9999/v1", "model": "model", **kwargs}
    with pytest.raises(IntegrationError, match="^invalid_configuration$"):
        GatewayClient(**parameters)


@pytest.mark.parametrize("max_tokens,budget", [(401, 1), (True, 1), (400, float("nan"))])
async def test_invalid_budget_and_token_cap_denied_before_send(max_tokens, budget):
    calls = []
    client = llm_client(lambda request: calls.append(request))
    try:
        with pytest.raises(IntegrationError):
            await client.generate(MESSAGES, max_cost_usd=budget, max_completion_tokens=max_tokens)
        assert not calls
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    "response,code",
    [
        (httpx.Response(302, headers={"location": "http://example.test/secret"}), "http_error"),
        (httpx.Response(500, text="Bearer secret upstream exception"), "http_error"),
        (httpx.Response(200, text="not-json secret"), "invalid_response"),
        (httpx.Response(200, content=b"x" * 65), "response_too_large"),
        (httpx.Response(200, json={"choices": []}), "invalid_response"),
    ],
)
async def test_http_failures_are_bounded_and_sanitized(response, code):
    client = llm_client(lambda request: response, max_bytes=64)
    try:
        with pytest.raises(IntegrationError) as raised:
            await client.generate(MESSAGES, max_cost_usd=1)
        assert str(raised.value) == code
        assert "secret" not in str(raised.value)
    finally:
        await client.aclose()


class Chunks(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b"x" * 32
        yield b"x" * 33


async def test_streamed_response_limit_without_content_length():
    client = llm_client(lambda request: httpx.Response(200, stream=Chunks()), max_bytes=64)
    try:
        with pytest.raises(IntegrationError, match="^response_too_large$"):
            await client.generate(MESSAGES, max_cost_usd=1)
    finally:
        await client.aclose()


async def test_http_timeout_does_not_expose_request():
    def handler(request):
        raise httpx.ReadTimeout("secret in URL and headers", request=request)

    client = llm_client(handler)
    try:
        with pytest.raises(IntegrationError, match="^timeout$"):
            await client.generate(MESSAGES, max_cost_usd=1)
    finally:
        await client.aclose()


def rag_payload():
    return {
        "answer": "Untrusted generated answer is not retrieval evidence",
        "candidates": [
            {
                "chunk": {
                    "chunk_id": "chunk-1",
                    "document_id": "document-1",
                    "text": "Local excerpt",
                    "source": "notes/memory.md",
                    "file_name": "memory.md",
                    "start": 0,
                    "end": 13,
                },
                "score": 0.8,
                "rank": 1,
            }
        ],
    }


async def test_rag_uses_query_candidates_and_preserves_retrieval_provenance():
    def handler(request):
        assert request.url.path == "/query"
        assert json.loads(request.content) == {
            "question": "Agent memory?",
            "strategy": "hybrid",
            "top_k": 5,
            "rerank": "noop",
            "rewrite": False,
        }
        return httpx.Response(200, json=rag_payload())

    adapter = RAGAdapter("http://127.0.0.1:9998", transport=httpx.MockTransport(handler))
    try:
        hits = await adapter.query("Agent memory?")
        assert len(hits) == 1
        assert hits[0].text == "Local excerpt"
        assert hits[0].source == "notes/memory.md"
        assert hits[0].provenance == "rag_retrieval"
        assert "answer" not in hits[0].model_dump()
    finally:
        await adapter.aclose()


@pytest.mark.parametrize("bad_value", [float("nan"), "0.8", True])
async def test_invalid_rag_score_is_rejected(bad_value):
    payload = rag_payload()
    payload["candidates"][0]["score"] = bad_value
    # json.dumps deliberately permits NaN so the protocol validator, not serializer, rejects it.
    adapter = RAGAdapter(
        "http://127.0.0.1:9998",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=json.dumps(payload))),
    )
    try:
        with pytest.raises(IntegrationError, match="^invalid_response$"):
            await adapter.query("Agent memory?")
    finally:
        await adapter.aclose()


def test_agentops_export_is_one_tree_with_only_allowed_public_fields():
    run = Run(
        request=ResearchRequest(topic="Agent memory"), status="partial", report_md="Answer [1]"
    )
    run.config["latency_ms"] = 12.5
    events = [
        {"kind": "plan", "payload": {"secret": "must not be exported"}},
        {
            "kind": "tool_call",
            "payload": {"tool": "source_search", "subtask_id": "q1", "secret": "redact"},
        },
        {
            "kind": "tool_result",
            "payload": {
                "tool": "source_search",
                "subtask_id": "q1",
                "status": "error",
                "body": "secret",
            },
        },
        {
            "kind": "model_call",
            "payload": {"token_usage": 12, "cost": 0.01, "duration_ms": 1.5, "prompt": "secret"},
        },
    ]
    exported = AgentOpsExporter.from_events(run, events)
    assert set(exported) == {"answer", "trace"}
    assert exported["answer"] == run.report_md
    allowed = {
        "span_id",
        "parent_span_id",
        "run_id",
        "event_type",
        "duration_ms",
        "tool_name",
        "status",
        "token_usage",
        "cost",
    }
    seen = set()
    roots = 0
    for span in exported["trace"]:
        assert not set(span) - allowed
        assert span["span_id"] not in seen
        if span.get("parent_span_id"):
            assert span["parent_span_id"] in seen
        else:
            roots += 1
        seen.add(span["span_id"])
    assert roots == 1
    call, result, model = exported["trace"][1:4]
    assert result["parent_span_id"] == call["span_id"]
    assert call["status"] == "error"
    assert model["token_usage"] == 12
    assert model["cost"] == 0.01
    assert "secret" not in json.dumps(exported)


def test_agentops_limits_missing_metrics_and_unsafe_events():
    run = Run(request=ResearchRequest(topic="Agent memory"), report_md="a" * 25_000)
    events = [
        {"kind": "tool_call", "payload": {"tool": "unsafe tool\nname"}},
        {
            "kind": "model_call",
            "payload": {"token_usage": True, "cost": float("nan"), "tool": {"unsafe": True}},
        },
    ] + [{"kind": "model_call", "payload": {}}] * 1100
    result = AgentOpsExporter.from_events(run, events)
    assert len(result["answer"]) == 20_000
    assert "Truncated" in result["answer"]
    assert len(result["trace"]) == 1000
    assert result["trace"][1]["token_usage"] is None
    assert result["trace"][1]["cost"] is None
    assert result["trace"][-1]["event_type"] == "final"
