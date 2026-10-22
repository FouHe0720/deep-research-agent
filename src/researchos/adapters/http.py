"""Bounded HTTP with DNS pinning and redirect-by-redirect SSRF checks.

TLS keeps the original Host/SNI while connecting only to a validated literal IP:
https://www.encode.io/httpcore/extensions/#sni_hostname
The environment's proxy settings are deliberately ignored.
"""

import asyncio
import ipaddress
import socket
import time
import weakref
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit

import httpx

USER_AGENT = "ResearchOS/0.1 (+https://github.com/FouHe0720/deep-research-agent)"
Resolver = Callable[[str, int], Awaitable[list[str]]]
HopPolicy = Callable[[str], Awaitable[None]]


class NetworkError(Exception):
    """Safe error code: never includes URLs, response bodies, or credentials."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def validate_url(value: str) -> httpx.URL:
    """Validate syntax before any DNS or network I/O, including unusual IP forms."""
    try:
        if not value or any(ord(c) < 33 or ord(c) == 127 for c in value) or "\\" in value:
            raise ValueError
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            raise ValueError
        if parsed.username is not None or parsed.password is not None:
            raise ValueError
        if parsed.port not in {None, 80, 443} or "%" in parsed.hostname:
            raise ValueError
        url = httpx.URL(value).copy_with(fragment=None)
        if not url.host or url.host.rstrip(".").lower() in {"localhost", "localhost.localdomain"}:
            raise ValueError
        return url
    except (ValueError, httpx.InvalidURL):
        raise NetworkError("unsafe_url") from None


def public_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        elif isinstance(address, ipaddress.IPv6Address):
            # Translation/transition prefixes can route an apparently global IPv6
            # literal to an embedded private IPv4 destination (e.g. metadata).
            blocked = ("64:ff9b::/96", "64:ff9b:1::/48", "::/96", "3fff::/20")
            if (
                address.sixtofour
                or address.teredo
                or any(address in ipaddress.ip_network(prefix) for prefix in blocked)
            ):
                return False
        if isinstance(address, ipaddress.IPv4Address) and any(
            address in ipaddress.ip_network(prefix) for prefix in ("192.0.0.0/24", "192.88.99.0/24")
        ):
            return False
        return address.is_global and not address.is_multicast
    except ValueError:
        return False


async def resolve_host(host: str, port: int) -> list[str]:
    try:
        return [str(ipaddress.ip_address(host))]
    except ValueError:
        records = await asyncio.get_running_loop().getaddrinfo(
            host, port, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM
        )
        return list(dict.fromkeys(record[4][0] for record in records))


@dataclass
class _HostGate:
    concurrency: int
    interval: float
    semaphore: asyncio.Semaphore = field(init=False)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    next_start: float = 0.0

    def __post_init__(self):
        self.semaphore = asyncio.Semaphore(self.concurrency)

    async def pace(self):
        async with self.lock:
            delay = self.next_start - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            self.next_start = time.monotonic() + self.interval


# Shared across runs in this process/event loop: separate clients cannot bypass pacing.
_GATES: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


class SafeHTTP:
    def __init__(
        self,
        *,
        timeout: float = 15,
        max_bytes: int = 1_000_000,
        max_redirects: int = 4,
        retries: int = 2,
        max_requests: int = 12,
        resolver: Resolver = resolve_host,
        transport: httpx.AsyncBaseTransport | None = None,
        min_interval: float = 0.5,
    ):
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.max_redirects = max_redirects
        self.retries = retries
        self.max_requests = max_requests
        self.request_count = 0
        self.resolver = resolver
        self.min_interval = min_interval
        self._budget_lock = asyncio.Lock()
        # Avoid reusing an IP-keyed TLS connection for a different virtual host.
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            follow_redirects=False,
            trust_env=False,
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=0),
            transport=transport,
        )

    def _gate(self, host: str) -> _HostGate:
        gates = _GATES.setdefault(asyncio.get_running_loop(), {})
        host = host.rstrip(".").lower()
        if host == "arxiv.org" or host.endswith(".arxiv.org"):
            key, concurrency, interval = "arxiv.org", 1, 3.0
        elif host == "api.github.com":
            key, concurrency, interval = host, 2, 6.1  # unauthenticated search: 10/min
        else:
            key, concurrency, interval = host, 2, self.min_interval
        gate = gates.setdefault(key, _HostGate(concurrency, interval))
        gate.interval = max(gate.interval, interval)
        return gate

    async def _reserve(self):
        async with self._budget_lock:
            if self.request_count >= self.max_requests:
                raise NetworkError("request_budget_exhausted")
            self.request_count += 1

    async def _once(
        self, url: httpx.URL, headers: dict[str, str], max_bytes: int
    ) -> httpx.Response:
        try:
            addresses = await self.resolver(
                url.host, url.port or (443 if url.scheme == "https" else 80)
            )
        except (OSError, ValueError):
            raise NetworkError("dns_failed") from None
        if not addresses or not all(public_ip(address) for address in addresses):
            raise NetworkError("unsafe_address")
        pinned = url.copy_with(host=addresses[0])
        outgoing = httpx.Headers(headers)
        outgoing["Host"] = url.netloc.decode("ascii")
        outgoing["User-Agent"] = USER_AGENT
        outgoing["Accept-Encoding"] = "identity"
        gate = self._gate(url.host)
        async with gate.semaphore:
            await gate.pace()
            await self._reserve()
            async with self.client.stream(
                "GET", pinned, headers=outgoing, extensions={"sni_hostname": url.host}
            ) as response:
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise NetworkError("unsupported_encoding")
                try:
                    declared = int(response.headers.get("content-length", "0"))
                except ValueError:
                    declared = 0
                if declared > max_bytes:
                    raise NetworkError("response_too_large")
                body = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                    if len(body) + len(chunk) > max_bytes:
                        raise NetworkError("response_too_large")
                    body.extend(chunk)
                # Public response has the logical URL; the pinned IP remains transport-only.
                return httpx.Response(
                    response.status_code,
                    headers=response.headers,
                    content=bytes(body),
                    request=httpx.Request("GET", url),
                )

    @staticmethod
    def _retry_delay(response: httpx.Response, attempt: int) -> float | None:
        value = response.headers.get("retry-after")
        if value:
            try:
                delay = float(value)
            except ValueError:
                try:
                    when = parsedate_to_datetime(value)
                    delay = (when - datetime.now(UTC)).total_seconds()
                except (ValueError, TypeError):
                    return None
            # Never retry before a long Retry-After; let the caller report rate limiting.
            return max(0, delay) if delay <= 5 else None
        return 0.25 * 2**attempt

    async def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        max_bytes: int | None = None,
        policy: HopPolicy | None = None,
    ) -> httpx.Response:
        try:
            async with asyncio.timeout(self.timeout):
                return await self._get(url, headers or {}, max_bytes, policy)
        except TimeoutError:
            raise NetworkError("timeout") from None

    async def _get(self, value, headers, max_bytes, policy):
        current = validate_url(value)
        maximum = min(max_bytes or self.max_bytes, self.max_bytes)
        for hop in range(self.max_redirects + 1):
            if policy is not None:
                await policy(str(current))
            for attempt in range(self.retries + 1):
                try:
                    response = await self._once(current, headers, maximum)
                except (httpx.TransportError, OSError):
                    if attempt == self.retries:
                        raise NetworkError("network_failed") from None
                    await asyncio.sleep(0.25 * 2**attempt)
                    continue
                if response.status_code not in {429, 503} or attempt == self.retries:
                    break
                delay = self._retry_delay(response, attempt)
                if delay is None:
                    break
                await asyncio.sleep(delay)
            if response.status_code not in {301, 302, 303, 307, 308}:
                return response
            location = response.headers.get("location")
            if not location or hop == self.max_redirects:
                raise NetworkError("redirect_limit")
            destination = validate_url(urljoin(str(current), location))
            if current.scheme == "https" and destination.scheme != "https":
                raise NetworkError("redirect_downgrade")
            if (current.scheme, current.host, current.port) != (
                destination.scheme,
                destination.host,
                destination.port,
            ):
                headers = {
                    key: value
                    for key, value in headers.items()
                    if key.lower() not in {"authorization", "cookie", "x-subscription-token"}
                }
            current = destination
        raise NetworkError("redirect_limit")

    async def aclose(self):
        await self.client.aclose()
