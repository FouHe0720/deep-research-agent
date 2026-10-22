"""Optional service boundaries; no sibling installation or credentials are required.

Configured service URLs are operator-controlled and may point to local services.
They are separate from the public page reader's untrusted-URL boundary.
"""

import asyncio
import json
import math
import re
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from researchos.domain.models import Run


class IntegrationError(Exception):
    """Stable code only: remote bodies, URLs and exception text never escape."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class _Value(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False, frozen=True)


class LLMUsage(_Value):
    prompt_tokens: int | None = Field(None, ge=0)
    completion_tokens: int | None = Field(None, ge=0)
    total_tokens: int | None = Field(None, ge=0)


class LLMResult(_Value):
    text: str
    model: str
    usage: LLMUsage | None = None
    cost_usd: float | None = Field(None, ge=0)


class RetrievalHit(_Value):
    """Retrieved context, not evidence of a new public network observation."""

    chunk_id: str
    document_id: str
    text: str
    source: str
    file_name: str
    score: float
    rank: int = Field(ge=1)
    page: int | None = None
    section: str | None = None
    paragraph: int | None = None
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    provenance: Literal["rag_retrieval"] = "rag_retrieval"


class _Message(_Value):
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=32_000)


def _finite(value: object, *, minimum: float = 0) -> bool:
    try:
        return type(value) in {int, float} and math.isfinite(value) and value >= minimum
    except OverflowError:
        return False


def _base_url(value: str) -> str:
    try:
        if not isinstance(value, str) or any(ord(char) < 33 for char in value) or "\\" in value:
            raise ValueError
        url = httpx.URL(value)
        if url.scheme not in {"http", "https"} or not url.host:
            raise ValueError
        if url.userinfo or url.query or url.fragment:
            raise ValueError
        return str(url).rstrip("/")
    except (ValueError, TypeError, httpx.InvalidURL):
        raise IntegrationError("invalid_configuration") from None


class _JSONClient:
    def __init__(self, *, api_key, timeout, max_bytes, transport):
        if not _finite(timeout, minimum=0.01) or timeout > 120:
            raise IntegrationError("invalid_configuration")
        if type(max_bytes) is not int or not 1 <= max_bytes <= 4_194_304:
            raise IntegrationError("invalid_configuration")
        if api_key and (
            not isinstance(api_key, str)
            or not api_key.isascii()
            or any(ord(char) < 33 or ord(char) == 127 for char in api_key)
        ):
            raise IntegrationError("invalid_configuration")
        self._headers = {"Accept": "application/json", "Accept-Encoding": "identity"}
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"
        self._timeout = timeout
        self._max_bytes = max_bytes
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    async def _post(self, endpoint: str, payload: dict) -> object:
        try:
            async with asyncio.timeout(self._timeout):
                async with self._client.stream(
                    "POST", endpoint, json=payload, headers=self._headers
                ) as response:
                    if not 200 <= response.status_code < 300:
                        raise IntegrationError("http_error")
                    if response.headers.get("content-encoding", "identity").lower() != "identity":
                        raise IntegrationError("unsupported_encoding")
                    length = response.headers.get("content-length", "0")
                    if not length.isdigit() or int(length) > self._max_bytes:
                        raise IntegrationError("response_too_large")
                    body = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=65_536):
                        if len(body) + len(chunk) > self._max_bytes:
                            raise IntegrationError("response_too_large")
                        body.extend(chunk)
            return json.loads(body)
        except (TimeoutError, httpx.TimeoutException):
            raise IntegrationError("timeout") from None
        except httpx.TransportError:
            raise IntegrationError("transport_error") from None
        except (ValueError, UnicodeError):
            raise IntegrationError("invalid_response") from None

    async def aclose(self):
        await self._client.aclose()


class GatewayClient(_JSONClient):
    """One bounded text call; prices and a per-call budget are explicit.

    The UTF-8 byte count plus generous message framing is a conservative planning
    allowance for byte-based tokenizers. It is not a billing guarantee for arbitrary
    compatible services. Missing provider usage is never replaced by this estimate.
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        kind: Literal["openai", "gateway"] = "openai",
        api_key: str | None = None,
        timeout: float = 20,
        max_bytes: int = 1_048_576,
        input_usd_per_million: float | None = None,
        output_usd_per_million: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        base = _base_url(base_url)
        if (
            kind not in {"openai", "gateway"}
            or not isinstance(model, str)
            or not model.strip()
            or len(model) > 128
            or any(ord(char) < 33 or ord(char) == 127 for char in model)
        ):
            raise IntegrationError("invalid_configuration")
        for price in (input_usd_per_million, output_usd_per_million):
            if price is not None and not _finite(price):
                raise IntegrationError("invalid_configuration")
        self.model = model
        self.kind = kind
        self._input_price = input_usd_per_million
        self._output_price = output_usd_per_million
        if kind == "gateway":
            self._endpoint = base + ("/chat" if base.endswith("/v1") else "/v1/chat")
        else:
            self._endpoint = base + "/chat/completions"
        super().__init__(api_key=api_key, timeout=timeout, max_bytes=max_bytes, transport=transport)

    def _messages(self, messages: list[dict], max_completion_tokens: int) -> list[dict]:
        try:
            if type(max_completion_tokens) is not int or not 1 <= max_completion_tokens <= 400:
                raise ValueError
            if not isinstance(messages, list) or not 1 <= len(messages) <= 100:
                raise ValueError
            parsed = [_Message.model_validate(message) for message in messages]
            if any(not message.content.strip() for message in parsed):
                raise ValueError
            if self.kind == "gateway" and (
                any(len(message.content) > 16_000 for message in parsed)
                or sum(len(message.content) for message in parsed) > 32_000
            ):
                raise ValueError
            return [message.model_dump() for message in parsed]
        except (ValueError, TypeError):
            raise IntegrationError("invalid_request") from None

    def estimate_cost(self, messages: list[dict], max_completion_tokens: int = 400) -> float | None:
        parsed = self._messages(messages, max_completion_tokens)
        if self._input_price is None or self._output_price is None:
            return None
        try:
            prompt_bound = 256 + sum(
                len(message["content"].encode("utf-8")) + len(message["role"].encode("utf-8")) + 128
                for message in parsed
            )
        except UnicodeError:
            raise IntegrationError("invalid_request") from None
        estimate = (
            prompt_bound * self._input_price + max_completion_tokens * self._output_price
        ) / 1_000_000
        if not math.isfinite(estimate):
            raise IntegrationError("invalid_configuration")
        return estimate

    async def generate(
        self,
        messages: list[dict],
        *,
        max_cost_usd: float,
        max_completion_tokens: int = 400,
    ) -> LLMResult:
        parsed = self._messages(messages, max_completion_tokens)
        estimate = self.estimate_cost(parsed, max_completion_tokens)
        if not _finite(max_cost_usd):
            raise IntegrationError("invalid_budget")
        if estimate is None:
            raise IntegrationError("pricing_required")
        if estimate > max_cost_usd:
            raise IntegrationError("cost_budget_exhausted")
        payload = {"model": self.model, "messages": parsed}
        token_key = "max_tokens" if self.kind == "gateway" else "max_completion_tokens"
        payload[token_key] = max_completion_tokens
        response = await self._post(self._endpoint, payload)
        try:
            if not isinstance(response, dict):
                raise ValueError
            if self.kind == "gateway":
                text = response["answer"]
            else:
                message = response["choices"][0]["message"]
                if message.get("tool_calls") or message.get("function_call"):
                    raise ValueError
                text = message["content"]
            if not isinstance(text, str) or len(text) > 20_000:
                raise ValueError
            raw_usage = response.get("usage")
            usage = None
            cost = None
            if raw_usage is not None:
                if not isinstance(raw_usage, dict):
                    raise ValueError
                counters = {
                    key: raw_usage[key]
                    for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                    if key in raw_usage
                }
                if any(value is not None for value in counters.values()):
                    usage = LLMUsage.model_validate(counters)
                    if usage.prompt_tokens is not None and usage.completion_tokens is not None:
                        if usage.total_tokens is None:
                            usage = usage.model_copy(
                                update={
                                    "total_tokens": usage.prompt_tokens + usage.completion_tokens
                                }
                            )
                        cost = (
                            usage.prompt_tokens * self._input_price
                            + usage.completion_tokens * self._output_price
                        ) / 1_000_000
            return LLMResult(text=text, model=self.model, usage=usage, cost_usd=cost)
        except (KeyError, IndexError, TypeError, ValueError):
            raise IntegrationError("invalid_response") from None


class RAGAdapter(_JSONClient):
    def __init__(
        self,
        base_url: str,
        *,
        api_key: str | None = None,
        timeout: float = 15,
        max_bytes: int = 1_048_576,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._endpoint = _base_url(base_url) + "/query"
        super().__init__(api_key=api_key, timeout=timeout, max_bytes=max_bytes, transport=transport)

    async def query(self, question: str, top_k: int = 5) -> list[RetrievalHit]:
        if (
            not isinstance(question, str)
            or not question.strip()
            or len(question) > 2000
            or type(top_k) is not int
            or not 1 <= top_k <= 100
        ):
            raise IntegrationError("invalid_request")
        payload = await self._post(
            self._endpoint,
            {
                "question": question,
                "strategy": "hybrid",
                "top_k": top_k,
                "rerank": "noop",
                "rewrite": False,
            },
        )
        try:
            candidates = payload["candidates"]
            if not isinstance(candidates, list) or len(candidates) > 100:
                raise ValueError
            hits = []
            for candidate in candidates:
                chunk = candidate["chunk"]
                hit = RetrievalHit.model_validate(
                    {
                        **{
                            key: chunk[key]
                            for key in (
                                "chunk_id",
                                "document_id",
                                "text",
                                "source",
                                "file_name",
                                "start",
                                "end",
                            )
                        },
                        **{key: chunk.get(key) for key in ("page", "section", "paragraph")},
                        "score": candidate["score"],
                        "rank": candidate["rank"],
                    }
                )
                if not hit.text.strip() or len(hit.text) > 8000 or hit.end < hit.start:
                    raise ValueError
                hits.append(hit)
            return sorted(hits, key=lambda hit: hit.rank)[:top_k]
        except (KeyError, IndexError, TypeError, ValueError):
            raise IntegrationError("invalid_response") from None


_SAFE_NAME = re.compile(r"^[a-zA-Z0-9_.:-]{1,80}$")


class AgentOpsExporter:
    """Sanitized ResearchAgentAdapter response; full local events stay local."""

    @staticmethod
    def from_events(run: Run, events: list[dict]) -> dict:
        status = "error" if run.status in {"failed", "cancelled"} else "ok"
        root = {"span_id": "research-root", "event_type": "agent", "status": status}
        latency = run.config.get("latency_ms")
        if _finite(latency):
            root["duration_ms"] = latency
        trace = [root]
        pending: dict[tuple[str, str], list[dict]] = {}
        for index, event in enumerate(events):
            if not isinstance(event, dict):
                continue
            kind = event.get("kind")
            if kind not in {"tool_call", "tool_result", "model_call", "retry"}:
                continue
            payload = event.get("payload", {})
            if not isinstance(payload, dict):
                continue
            tool = payload.get("tool", payload.get("tool_name"))
            if kind in {"tool_call", "tool_result"} and (
                not isinstance(tool, str) or not _SAFE_NAME.fullmatch(tool)
            ):
                continue
            if not isinstance(tool, str) or not _SAFE_NAME.fullmatch(tool):
                tool = None
            span = {
                "span_id": f"research-event-{index}",
                "parent_span_id": "research-root",
                "event_type": kind,
                "status": payload.get("status")
                if payload.get("status") in {"ok", "error", "timeout"}
                else "ok",
            }
            if isinstance(tool, str) and _SAFE_NAME.fullmatch(tool):
                span["tool_name"] = tool
            subtask = payload.get("subtask_id")
            key = (tool or "", subtask if isinstance(subtask, str) else "")
            if kind == "tool_result" and pending.get(key):
                parent = pending[key].pop(0)
                span["parent_span_id"] = parent["span_id"]
                parent["status"] = span["status"]
            elif kind == "tool_call":
                pending.setdefault(key, []).append(span)
            duration = payload.get("duration_ms")
            if _finite(duration):
                span["duration_ms"] = duration
            if kind == "model_call":
                tokens, cost = payload.get("token_usage"), payload.get("cost")
                span["token_usage"] = tokens if type(tokens) is int and tokens >= 0 else None
                span["cost"] = cost if _finite(cost) else None
            trace.append(span)
            if len(trace) >= 999:
                break
        trace.append(
            {
                "span_id": "research-final",
                "parent_span_id": "research-root",
                "event_type": "final",
                "status": status,
            }
        )
        answer = run.report_md
        if len(answer) > 20_000:
            note = "\n[Truncated for AgentOps transport; full report retained locally.]"
            answer = answer[: 20_000 - len(note)] + note
        return {"answer": answer, "trace": trace}
