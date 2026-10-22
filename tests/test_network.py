import asyncio
import json
import socket

import httpx
import pytest

from researchos.adapters.http import NetworkError, SafeHTTP, public_ip, resolve_host
from researchos.adapters.live import LiveSourceProvider, ProviderError, WebReader
from researchos.domain.models import Subtask


async def public_dns(host, port):
    return ["93.184.216.34"]


def task(identifier="q1"):
    return Subtask(
        id=identifier,
        question="Agent memory systems?",
        keywords=["agent memory", "primary sources"],
        evidence_type="paper",
    )


def client(handler, **kwargs):
    return SafeHTTP(
        resolver=public_dns,
        transport=httpx.MockTransport(handler),
        min_interval=0,
        **kwargs,
    )


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://user:password@example.org/",
        "https://example.org:8080/",
        "http://localhost/",
        "http://example.org\\@127.0.0.1/",
        "http://[fe80::1%25eth0]/",
        "http://example.org/\nheader",
    ],
)
async def test_rejects_unsafe_url_before_network(url):
    def unexpected(request):
        pytest.fail("unsafe URL reached transport")

    http = client(unexpected)
    try:
        with pytest.raises(NetworkError, match="unsafe_url"):
            await http.get(url)
        assert http.request_count == 0
    finally:
        await http.aclose()


@pytest.mark.parametrize(
    "addresses",
    [
        ["127.0.0.1"],
        ["169.254.169.254"],
        ["10.0.0.1"],
        ["100.64.0.1"],
        ["::1"],
        ["::ffff:192.168.1.1"],
        ["64:ff9b::a9fe:a9fe"],
        ["64:ff9b:1::a9fe:a9fe"],
        ["2002:a9fe:a9fe::1"],
        ["::a9fe:a9fe"],
        ["192.0.0.8"],
        ["93.184.216.34", "fd00::1"],
        [],
    ],
)
async def test_all_dns_answers_must_be_public(addresses):
    async def resolver(host, port):
        return addresses

    http = SafeHTTP(resolver=resolver, transport=httpx.MockTransport(lambda _: None))
    try:
        with pytest.raises(NetworkError, match="unsafe_address"):
            await http.get("https://example.org/")
        assert http.request_count == 0
    finally:
        await http.aclose()


async def test_dns_pins_ip_and_preserves_host_and_tls_sni():
    calls = []

    async def resolver(host, port):
        calls.append((host, port))
        # A hypothetical second lookup would rebind to localhost. The transport
        # receives the first literal IP and does not resolve this hostname again.
        return ["93.184.216.34"] if len(calls) == 1 else ["127.0.0.1"]

    def handler(request):
        assert request.url.host == "93.184.216.34"
        assert request.headers["Host"] == "example.org"
        assert request.extensions["sni_hostname"] == "example.org"
        assert request.headers["User-Agent"].startswith("ResearchOS/")
        return httpx.Response(200, text="safe")

    http = SafeHTTP(resolver=resolver, transport=httpx.MockTransport(handler))
    try:
        response = await http.get("https://example.org/")
        assert response.text == "safe"
        assert str(response.url) == "https://example.org/"
        assert calls == [("example.org", 443)]
    finally:
        await http.aclose()


async def test_redirect_revalidates_dns_and_rejects_private_destination():
    async def resolver(host, port):
        return ["93.184.216.34"] if host == "example.org" else ["127.0.0.1"]

    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(302, headers={"Location": "http://internal.invalid/secret"})

    http = SafeHTTP(resolver=resolver, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(NetworkError, match="unsafe_address"):
            await http.get("http://example.org/")
        assert len(requests) == 1
    finally:
        await http.aclose()


async def test_same_hostname_redirect_rebinding_is_rejected():
    calls = 0

    async def resolver(host, port):
        nonlocal calls
        calls += 1
        return ["93.184.216.34"] if calls == 1 else ["127.0.0.1"]

    http = SafeHTTP(
        resolver=resolver,
        transport=httpx.MockTransport(lambda _: httpx.Response(302, headers={"Location": "/next"})),
        min_interval=0,
    )
    try:
        with pytest.raises(NetworkError, match="unsafe_address"):
            await http.get("https://example.org/")
        assert http.request_count == 1
    finally:
        await http.aclose()


async def test_cross_origin_redirect_drops_credentials():
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(302, headers={"Location": "https://other.example/page"})
        assert "authorization" not in request.headers
        assert "x-subscription-token" not in request.headers
        assert "cookie" not in request.headers
        return httpx.Response(200, text="done")

    http = client(handler)
    try:
        await http.get(
            "https://example.org/",
            headers={
                "Authorization": "secret",
                "X-Subscription-Token": "secret",
                "Cookie": "secret",
            },
        )
        assert len(requests) == 2
    finally:
        await http.aclose()


class Chunks(httpx.AsyncByteStream):
    async def __aiter__(self):
        for _ in range(5):
            yield b"0123456789"


@pytest.mark.parametrize("declared", [False, True])
async def test_response_byte_limit_for_streamed_and_declared_lengths(declared):
    headers = {"Content-Length": "50"} if declared else {}
    http = client(lambda _: httpx.Response(200, headers=headers, stream=Chunks()), max_bytes=20)
    try:
        with pytest.raises(NetworkError, match="response_too_large"):
            await http.get("https://size.example/")
    finally:
        await http.aclose()


async def test_429_retry_and_hard_request_budget(monkeypatch):
    sleeps = []

    async def sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr("researchos.adapters.http.asyncio.sleep", sleep)
    attempts = 0

    def handler(request):
        nonlocal attempts
        attempts += 1
        return (
            httpx.Response(429, headers={"Retry-After": "0"})
            if attempts == 1
            else httpx.Response(200)
        )

    http = client(handler, max_requests=2)
    try:
        assert (await http.get("https://rate.example/")).status_code == 200
        assert http.request_count == 2
        with pytest.raises(NetworkError, match="request_budget_exhausted"):
            await http.get("https://rate.example/")
        assert attempts == 2
    finally:
        await http.aclose()


async def test_long_retry_after_is_not_retried_early():
    http = client(lambda _: httpx.Response(429, headers={"Retry-After": "3600"}))
    try:
        assert (await http.get("https://rate.example/")).status_code == 429
        assert http.request_count == 1
    finally:
        await http.aclose()


async def test_timeout_and_bounded_network_retries(monkeypatch):
    async def sleep(seconds):
        pass

    monkeypatch.setattr("researchos.adapters.http.asyncio.sleep", sleep)

    def broken(request):
        raise httpx.ConnectError("may contain secret credentials", request=request)

    http = client(broken, retries=1)
    try:
        with pytest.raises(NetworkError) as error:
            await http.get("https://error.example/")
        assert str(error.value) == "network_failed"
        assert http.request_count == 2
    finally:
        await http.aclose()

    async def slow_resolver(host, port):
        await asyncio.Event().wait()

    http = SafeHTTP(resolver=slow_resolver, timeout=0.01)
    try:
        with pytest.raises(NetworkError, match="timeout"):
            await http.get("https://timeout.example/")
    finally:
        await http.aclose()


async def test_arxiv_gate_single_connection_three_second_interval():
    http = client(lambda _: httpx.Response(200))
    try:
        gate = http._gate("export.arxiv.org")
        assert gate is http._gate("arxiv.org")
        assert gate.concurrency == 1
        assert gate.interval >= 3
        assert http._gate("pages.example").concurrency <= 2
    finally:
        await http.aclose()


async def test_system_resolver_requests_all_address_families(monkeypatch):
    async def getaddrinfo(host, port, **kwargs):
        assert kwargs["family"] == socket.AF_UNSPEC
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (
                socket.AF_INET6,
                socket.SOCK_STREAM,
                6,
                "",
                ("2606:2800:220:1:248:1893:25c8:1946", 443, 0, 0),
            ),
        ]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", getaddrinfo)
    assert len(await resolve_host("dns.example", 443)) == 2
    assert not public_ip("::ffff:127.0.0.1")


async def test_github_parsing_cache_and_no_invented_publication_date():
    def handler(request):
        assert request.headers["Host"] == "api.github.com"
        assert request.url.params["q"] == "agent memory"
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "html_url": "https://github.com/example/memory",
                        "full_name": "example/memory",
                        "description": "Observed repository description",
                        "created_at": "2024-01-01T00:00:00Z",
                        "owner": {"login": "example"},
                    }
                ]
            },
        )

    http = client(handler)
    provider = LiveSourceProvider(["github"], http=http)
    try:
        first = (await provider.search(task(), 2))[0]
        second = (await provider.search(task("q2"), 2))[0]
        assert first.provenance == "network"
        assert first.excerpt == "Observed repository description"
        assert first.published_at is None
        assert first.fetched_at
        assert first.subtask_ids == ["q1"]
        assert second.subtask_ids == ["q2"]
        assert provider.tool_calls == 1
    finally:
        await http.aclose()


ATOM = b"""<feed xmlns="http://www.w3.org/2005/Atom">
<entry><id>http://arxiv.org/abs/2401.01234v1</id><title>Memory research</title>
<summary>Actual API abstract</summary><published>2024-01-02T00:00:00Z</published>
<author><name>Research Author</name></author></entry></feed>"""


async def test_arxiv_atom_metadata_and_provider_rotation():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, content=ATOM)

    http = client(handler)
    provider = LiveSourceProvider(["github", "arxiv"], http=http)
    try:
        source = (await provider.search(task("q2"), 1))[0]
        assert requests[0].headers["Host"] == "export.arxiv.org"
        assert source.url == "https://arxiv.org/abs/2401.01234v1"
        assert source.category == "paper"
        assert source.published_at == "2024-01-02T00:00:00Z"
        assert source.date_basis == "arXiv API published"
        assert source.authors == ["Research Author"]
    finally:
        await http.aclose()


async def test_missing_brave_key_is_explicit_and_never_fetches():
    http = client(lambda _: pytest.fail("missing key sent a request"))
    provider = LiveSourceProvider(["web"], http=http)
    try:
        with pytest.raises(ProviderError) as error:
            await provider.search(task(), 2)
        assert error.value.code == "missing_api_key"
        assert provider.tool_calls == 0
        assert provider.warnings == ["web: missing BRAVE_SEARCH_API_KEY"]
    finally:
        await http.aclose()


async def test_brave_reads_real_page_with_robots_and_strips_scripts():
    requests = []

    def handler(request):
        requests.append(request)
        if request.headers["Host"] == "api.search.brave.com":
            assert request.headers["X-Subscription-Token"] == "private-key"
            return httpx.Response(
                200, json={"web": {"results": [{"url": "https://page.example/read"}]}}
            )
        assert "X-Subscription-Token" not in request.headers
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html"},
            text="""
            <title>Actual title</title><main>Observed page text<script>malicious()</script></main>
        """,
        )

    http = client(handler)
    provider = LiveSourceProvider(["web"], web_api_key="private-key", http=http)
    try:
        source = (await provider.search(task(), 1))[0]
        assert source.title == "Actual title"
        assert source.excerpt == "Observed page text"
        assert source.published_at is None
        assert source.url == "https://page.example/read"
        assert len(requests) == 3
    finally:
        await http.aclose()


@pytest.mark.parametrize(
    "robots_status,robots_body", [(503, ""), (200, "User-agent: *\nDisallow: /")]
)
async def test_robots_unavailable_or_disallowed_fails_closed(robots_status, robots_body):
    def handler(request):
        assert request.url.path == "/robots.txt"
        return httpx.Response(robots_status, text=robots_body, headers={"Retry-After": "3600"})

    http = client(handler)
    reader = WebReader(http)
    try:
        with pytest.raises(ProviderError, match="robots_"):
            await reader.read("https://page.example/read", task())
        assert http.request_count == 1
    finally:
        await http.aclose()


async def test_page_redirect_checks_destination_robots():
    paths = []

    def handler(request):
        paths.append((request.headers["Host"], request.url.path))
        if request.url.path == "/robots.txt":
            body = (
                "User-agent: *\nAllow: /"
                if request.headers["Host"] == "first.example"
                else "User-agent: *\nDisallow: /"
            )
            return httpx.Response(200, text=body)
        return httpx.Response(302, headers={"Location": "https://second.example/read"})

    http = client(handler)
    try:
        with pytest.raises(ProviderError, match="robots_denied"):
            await WebReader(http).read("https://first.example/read", task())
        assert paths == [
            ("first.example", "/robots.txt"),
            ("first.example", "/read"),
            ("second.example", "/robots.txt"),
        ]
    finally:
        await http.aclose()


async def test_bad_provider_payload_and_rate_limit_never_include_secrets():
    secret = "private-key-do-not-log"
    http = client(
        lambda _: httpx.Response(403, headers={"X-RateLimit-Remaining": "0"}, text=secret)
    )
    provider = LiveSourceProvider(["github"], github_token=secret, http=http)
    try:
        with pytest.raises(ProviderError) as error:
            await provider.search(task(), 1)
        assert error.value.code == "rate_limited"
        assert secret not in json.dumps(error.value.as_dict())
        assert secret not in str(provider.warnings)
    finally:
        await http.aclose()


@pytest.mark.parametrize(
    "provider,payload",
    [
        ("github", {"items": "wrong type"}),
        ("github", {"items": [None]}),
        ("github", {"items": [{"owner": None, "full_name": "example/memory"}]}),
        ("github", {"items": [{"owner": [], "full_name": "example/memory"}]}),
        ("github", {"items": [{"owner": {}, "full_name": []}]}),
        ("web", {"web": []}),
        ("web", {"web": None}),
        ("web", {"web": {"results": [None]}}),
        ("web", {"web": {"results": [{"url": None}]}}),
    ],
)
async def test_malformed_nested_provider_payloads_are_structured_errors(provider, payload):
    http = client(lambda _: httpx.Response(200, json=payload))
    live = LiveSourceProvider([provider], web_api_key="test-key", http=http)
    try:
        with pytest.raises(ProviderError) as error:
            await live.search(task(), 1)
        assert error.value.code == "invalid_response"
        assert live.warnings == [f"{provider}: invalid_response"]
    finally:
        await http.aclose()
