"""Real, optional sources. Search failures never turn into offline evidence."""

import hashlib
import json
import time
import xml.etree.ElementTree as ET
from collections import OrderedDict
from collections.abc import Sequence
from datetime import datetime
from urllib.parse import urlencode
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from researchos.adapters.http import USER_AGENT, NetworkError, SafeHTTP, validate_url
from researchos.domain.models import SourceRecord, Subtask


class ProviderError(Exception):
    """Structured, credential-free failure suitable for traces and API responses."""

    def __init__(self, provider: str, code: str, message: str | None = None):
        self.provider = provider
        self.code = code
        self.message = message or f"{provider}: {code}"
        super().__init__(self.message)

    def as_dict(self) -> dict[str, str]:
        return {"provider": self.provider, "code": self.code, "message": self.message}


def _text(value, limit: int = 8000) -> str:
    return " ".join(str(value or "").split())[:limit]


def _date(value) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value
    except ValueError:
        return None


def _record(subtask: Subtask, *, url, title, excerpt, category, **extra) -> SourceRecord:
    clean = _text(excerpt)
    if not clean:
        raise ProviderError(category, "empty_content")
    normalized = str(validate_url(url))
    return SourceRecord(
        id=hashlib.sha256(normalized.encode()).hexdigest()[:24],
        url=normalized,
        title=_text(title) or normalized,
        excerpt=clean,
        category=category,
        provenance="network",
        content_hash=hashlib.sha256(clean.encode()).hexdigest(),
        subtask_ids=[subtask.id],
        **extra,
    )


class WebReader:
    """HTML text only. robots is checked on every page redirect and fails closed."""

    def __init__(self, http: SafeHTTP):
        self.http = http
        self._robots: OrderedDict[str, tuple[float, RobotFileParser]] = OrderedDict()

    async def _check_robots(self, value: str):
        url = validate_url(value)
        robots_url = str(url.copy_with(path="/robots.txt", query=None, fragment=None))
        cached = self._robots.get(robots_url)
        if cached and cached[0] > time.monotonic():
            parser = cached[1]
        else:
            response = await self.http.get(robots_url, max_bytes=128_000)
            if response.status_code != 200:
                raise ProviderError("web", "robots_unavailable")
            content_type = response.headers.get("content-type", "text/plain")
            if "html" in content_type.lower() or "<html" in response.text[:200].lower():
                raise ProviderError("web", "robots_invalid")
            parser = RobotFileParser(robots_url)
            parser.parse(response.text.splitlines())
            self._robots[robots_url] = (time.monotonic() + 300, parser)
            while len(self._robots) > 64:
                self._robots.popitem(last=False)
        if not parser.can_fetch(USER_AGENT, value):
            raise ProviderError("web", "robots_denied")
        # robots-specific slower crawl delays take precedence over default pacing.
        delay = parser.crawl_delay(USER_AGENT) or parser.crawl_delay("*")
        if delay:
            self.http._gate(url.host).interval = max(self.http._gate(url.host).interval, delay)

    async def read(self, url: str, subtask: Subtask) -> SourceRecord:
        try:
            response = await self.http.get(url, policy=self._check_robots)
            _check_status(response, "web")
            content_type = response.headers.get("content-type", "").lower()
            if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
                raise ProviderError("web", "unsupported_content_type")
            soup = BeautifulSoup(response.content, "html.parser")
            for element in soup.select("script,style,noscript,nav,footer,header,form,iframe"):
                element.decompose()
            title = soup.title.get_text(" ", strip=True) if soup.title else str(response.url)
            body = soup.find("main") or soup.find("article") or soup.body or soup
            return _record(
                subtask,
                url=str(response.url),
                title=title,
                excerpt=body.get_text(" ", strip=True),
                category="web",
            )
        except NetworkError as exc:
            raise ProviderError("web", exc.code) from None


def _check_status(response: httpx.Response, provider: str):
    status = response.status_code
    if status in {401, 403}:
        code = (
            "rate_limited"
            if response.headers.get("x-ratelimit-remaining") == "0"
            else "access_denied"
        )
        raise ProviderError(provider, code)
    if status == 429:
        raise ProviderError(provider, "rate_limited")
    if status != 200:
        raise ProviderError(provider, f"http_{status}")


class LiveSourceProvider:
    def __init__(
        self,
        providers: Sequence[str] = ("github", "arxiv"),
        web_api_key: str | None = None,
        github_token: str | None = None,
        http: SafeHTTP | None = None,
        *,
        max_tool_calls: int = 12,
        cache_ttl: float = 300,
    ):
        if not providers or any(p not in {"github", "arxiv", "web"} for p in providers):
            raise ValueError("providers must contain github, arxiv, or web")
        self.providers = list(dict.fromkeys(providers))
        self.web_api_key = web_api_key
        self.github_token = github_token
        self.http = http or SafeHTTP(max_requests=max_tool_calls)
        self.http.max_requests = min(self.http.max_requests, max_tool_calls)
        self._owned_http = http is None
        self.reader = WebReader(self.http)
        self.warnings: list[str] = []
        self.errors: list[ProviderError] = []
        self.cache_ttl = cache_ttl
        self._cache: OrderedDict[tuple, tuple[float, list[SourceRecord]]] = OrderedDict()

    @property
    def tool_calls(self) -> int:
        return self.http.request_count

    def _warn(self, error: ProviderError):
        self.errors.append(error)
        self.errors = self.errors[-64:]
        if error.message not in self.warnings:
            self.warnings.append(error.message)
            self.warnings = self.warnings[-64:]

    async def search(self, subtask: Subtask, limit: int) -> list[SourceRecord]:
        if limit <= 0:
            return []
        limit = min(limit, 20)
        # Planner's first keyword is the topic; labels like 'primary sources' are
        # planning hints and must not accidentally narrow official API searches.
        query = _text(subtask.keywords[0] if subtask.keywords else subtask.question, 240)
        found: list[SourceRecord] = []
        last_error = None
        digits = "".join(char for char in subtask.id if char.isdigit())
        offset = (max(1, int(digits)) - 1) % len(self.providers) if digits else 0
        ordered = self.providers[offset:] + self.providers[:offset]
        for provider in ordered:
            count = limit - len(found)
            if count <= 0:
                break
            key = (provider, query, count)
            cached = self._cache.get(key)
            if cached and cached[0] > time.monotonic():
                found.extend(
                    source.model_copy(deep=True, update={"subtask_ids": [subtask.id]})
                    for source in cached[1]
                )
                continue
            try:
                sources = await getattr(self, f"_{provider}")(subtask, query, count)
                self._cache[key] = (time.monotonic() + self.cache_ttl, sources)
                while len(self._cache) > 64:
                    self._cache.popitem(last=False)
                found.extend(sources)
            except NetworkError as exc:
                last_error = ProviderError(provider, exc.code)
                self._warn(last_error)
            except ProviderError as exc:
                last_error = exc
                self._warn(exc)
            except (ValueError, TypeError, KeyError, ET.ParseError):
                last_error = ProviderError(provider, "invalid_response")
                self._warn(last_error)
        if not found and last_error:
            raise last_error
        return found[:limit]

    async def _json(self, url: str, provider: str, headers=None):
        response = await self.http.get(url, headers=headers)
        _check_status(response, provider)
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError):
            raise ProviderError(provider, "invalid_response") from None
        if not isinstance(payload, dict):
            raise ProviderError(provider, "invalid_response")
        return payload

    async def _github(self, subtask: Subtask, query: str, limit: int):
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if self.github_token:
            headers["Authorization"] = f"Bearer {self.github_token}"
        url = "https://api.github.com/search/repositories?" + urlencode(
            {"q": query, "per_page": limit}
        )
        data = await self._json(url, "github", headers)
        if not isinstance(data.get("items"), list):
            raise ProviderError("github", "invalid_response")
        if data.get("incomplete_results"):
            self._warn(ProviderError("github", "incomplete_results"))
        sources = []
        for item in data["items"][:limit]:
            if not isinstance(item, dict):
                raise ProviderError("github", "invalid_response")
            owner = item.get("owner", {})
            if not isinstance(owner, dict) or not isinstance(item.get("full_name"), str):
                raise ProviderError("github", "invalid_response")
            if not isinstance(item.get("html_url"), str):
                raise ProviderError("github", "invalid_response")
            description = item.get("description")
            if description is not None and not isinstance(description, str):
                raise ProviderError("github", "invalid_response")
            url = item.get("html_url", "")
            parsed = validate_url(url)
            if parsed.scheme != "https" or parsed.host != "github.com" or item.get("private"):
                continue
            sources.append(
                _record(
                    subtask,
                    url=url,
                    title=item["full_name"],
                    excerpt=item.get("description") or item["full_name"],
                    category="github",
                    authors=[_text(owner["login"])] if owner.get("login") else [],
                )
            )
        return sources

    async def _arxiv(self, subtask: Subtask, query: str, limit: int):
        # Quoted literal keywords prevent user input becoming arXiv query operators.
        terms = [term.replace('"', "") for term in query.split()[:12]]
        url = "https://export.arxiv.org/api/query?" + urlencode(
            {"search_query": " AND ".join(f'all:"{term}"' for term in terms), "max_results": limit}
        )
        response = await self.http.get(url)
        _check_status(response, "arxiv")
        if b"<!DOCTYPE" in response.content.upper() or b"<!ENTITY" in response.content.upper():
            raise ProviderError("arxiv", "invalid_xml")
        root = ET.fromstring(response.content)
        ns = {"a": "http://www.w3.org/2005/Atom", "ar": "http://arxiv.org/schemas/atom"}
        if root.tag != "{http://www.w3.org/2005/Atom}feed":
            raise ProviderError("arxiv", "invalid_response")
        sources = []
        for entry in root.findall("a:entry", ns)[:limit]:
            identifier = entry.findtext("a:id", "", ns)
            parsed = validate_url(identifier)
            if parsed.host not in {"arxiv.org", "export.arxiv.org"} or not parsed.path.startswith(
                "/abs/"
            ):
                continue
            published = _date(entry.findtext("a:published", None, ns))
            sources.append(
                _record(
                    subtask,
                    url=str(parsed.copy_with(scheme="https", host="arxiv.org")),
                    title=entry.findtext("a:title", "", ns),
                    excerpt=entry.findtext("a:summary", "", ns),
                    category="paper",
                    published_at=published,
                    date_basis="arXiv API published" if published else None,
                    authors=[
                        _text(a.findtext("a:name", "", ns)) for a in entry.findall("a:author", ns)
                    ],
                    doi=entry.findtext("ar:doi", None, ns),
                )
            )
        return sources

    async def _web(self, subtask: Subtask, query: str, limit: int):
        if not self.web_api_key:
            raise ProviderError("web", "missing_api_key", "web: missing BRAVE_SEARCH_API_KEY")
        url = "https://api.search.brave.com/res/v1/web/search?" + urlencode(
            {"q": query, "count": min(limit, 20)}
        )
        data = await self._json(url, "web", {"X-Subscription-Token": self.web_api_key})
        web = data.get("web", {})
        if not isinstance(web, dict):
            raise ProviderError("web", "invalid_response")
        entries = web.get("results", [])
        if not isinstance(entries, list):
            raise ProviderError("web", "invalid_response")
        sources = []
        last_error = None
        for item in entries[:limit]:
            if not isinstance(item, dict) or not isinstance(item.get("url"), str):
                raise ProviderError("web", "invalid_response")
            try:
                sources.append(await self.reader.read(item["url"], subtask))
            except (ProviderError, NetworkError) as exc:
                last_error = (
                    exc if isinstance(exc, ProviderError) else ProviderError("web", exc.code)
                )
                self._warn(last_error)
        if not sources and last_error:
            raise last_error
        return sources

    async def aclose(self):
        if self._owned_http:
            await self.http.aclose()
