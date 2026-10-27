"""Bounded public-text search and source evidence for reconstruction.

Search snippets locate pages. They never count as factual evidence. Fetched page
text is untrusted input, and this module neither uploads media nor executes it.
"""

from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import re
import socket
import ssl
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Literal, Protocol
from urllib.parse import parse_qs, quote, urlencode, urlsplit, urlunsplit

Provider = Literal["duckduckgo", "wikipedia", "searxng"]
SearchStatus = Literal["ok", "disabled", "skipped_identity", "error"]
Applicability = Literal["model_mentioned", "series_mentioned", "unverified"]
ClaimStatus = Literal["candidate", "conflict", "unverified"]


@dataclass(frozen=True)
class ObjectIdentity:
    object_id: str
    category: str
    manufacturer: str = ""
    model: str = ""
    series: str = ""

    def __post_init__(self) -> None:
        for value in asdict(self).values():
            if not isinstance(value, str) or len(value) > 160 or any(ord(c) < 32 for c in value):
                raise ValueError("identity fields must be short, printable text")
        if not self.object_id.strip() or not self.category.strip():
            raise ValueError("object_id and category are required")

    @property
    def specificity(self) -> Literal["exact_model", "series", "category"]:
        if self.model.strip():
            return "exact_model"
        if self.series.strip():
            return "series"
        return "category"

    @property
    def query(self) -> str:
        words = (self.manufacturer, self.model or self.series, self.category)
        return " ".join(part.strip() for part in words if part.strip())


@dataclass(frozen=True)
class WebSearchPolicy:
    enabled: bool = True
    provider: Provider = "duckduckgo"
    # A public HTTPS SearXNG instance configured by the operator, for example
    # https://search.example.org/search. No implicit API token or account.
    searxng_endpoint: str = ""
    wikipedia_language: str = "en"
    dns_resolver: Literal["system", "google"] = "system"
    allow_category_search: bool = False
    official_domains: tuple[str, ...] = ()
    allowed_source_domains: tuple[str, ...] = ()
    max_results: int = 5
    max_pages: int = 3
    max_response_bytes: int = 1_000_000
    max_text_chars: int = 30_000
    timeout_seconds: float = 8.0
    cache_ttl_seconds: int = 7 * 24 * 3600

    def __post_init__(self) -> None:
        if self.dns_resolver not in {"system", "google"}:
            raise ValueError("unsupported public DNS resolver")
        if self.provider not in {"duckduckgo", "wikipedia", "searxng"}:
            raise ValueError("unsupported web search provider")
        if self.provider == "searxng":
            _validate_url_shape(self.searxng_endpoint)
        if not re.fullmatch(r"[a-z]{2,3}", self.wikipedia_language):
            raise ValueError("wikipedia_language must be a two- or three-letter code")
        if not (1 <= self.max_results <= 10 and 1 <= self.max_pages <= self.max_results):
            raise ValueError("web search result and page limits are invalid")
        if not (1_024 <= self.max_response_bytes <= 4_000_000):
            raise ValueError("web response byte limit is invalid")
        if not (1_000 <= self.max_text_chars <= 100_000):
            raise ValueError("web text character limit is invalid")
        if not (0.5 <= self.timeout_seconds <= 30.0):
            raise ValueError("web request timeout is invalid")
        if not (0 <= self.cache_ttl_seconds <= 30 * 24 * 3600):
            raise ValueError("web cache lifetime is invalid")
        for domain in (*self.official_domains, *self.allowed_source_domains):
            if not _valid_domain(domain):
                raise ValueError("source domains must be DNS names without scheme or path")


@dataclass(frozen=True)
class HTTPResult:
    body: bytes
    content_type: str


class TextTransport(Protocol):
    def get(self, url: str, *, timeout_seconds: float, max_bytes: int) -> HTTPResult: ...


def _valid_domain(domain: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", domain))


def _validate_url_shape(url: str) -> tuple[str, str]:
    if not isinstance(url, str) or len(url) > 4096 or any(ord(c) < 32 for c in url):
        raise ValueError("web URL is invalid")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("web URL must be public HTTPS without embedded credentials")
    if parsed.port not in {None, 443} or parsed.fragment:
        raise ValueError("web URL cannot use a nonstandard port or fragment")
    try:
        host = parsed.hostname.encode("idna").decode("ascii").rstrip(".").lower()
    except UnicodeError as error:
        raise ValueError("web host is invalid") from error
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None and not literal.is_global:
        raise ValueError("web host is not public")
    if not _valid_domain(host) and literal is None:
        raise ValueError("web host must be a public DNS name")
    return host, parsed.path or "/"


def _resolve_public_address(host: str) -> str:
    try:
        addresses = [entry[4][0] for entry in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)]
    except OSError as error:
        raise ValueError("web host could not be resolved") from error
    if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise ValueError("web host resolves to a non-public address")
    return addresses[0]


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, pinned_ip: str, timeout_seconds: float):
        super().__init__(host, port=443, timeout=timeout_seconds, context=ssl.create_default_context())
        self.pinned_ip = pinned_ip

    def connect(self) -> None:
        # Connect to the address that passed the public-IP check; preserve host
        # for Host and TLS certificate verification to avoid a second DNS lookup.
        raw = socket.create_connection((self.pinned_ip, 443), timeout=self.timeout)
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


class PublicHTTPS:
    """Direct HTTPS GET with public-IP pinning, no proxy, redirect, or credentials."""

    def __init__(self, dns_resolver: Literal["system", "google"] = "system"):
        if dns_resolver not in {"system", "google"}:
            raise ValueError("unsupported public DNS resolver")
        self.dns_resolver = dns_resolver

    def _resolve(self, host: str, timeout_seconds: float) -> str:
        if self.dns_resolver == "system":
            return _resolve_public_address(host)
        # Fixed trusted bootstrap, never a caller-provided resolver or proxy.
        # TLS still verifies dns.google; both address families must be public.
        addresses = []
        for kind in ("A", "AAAA"):
            response = self._fetch(
                "https://dns.google/resolve?" + urlencode({"name": host, "type": kind}),
                "8.8.8.8", timeout_seconds, 64_000,
            )
            payload = json.loads(response.body)
            if payload.get("Status") != 0 or payload.get("TC"):
                raise ValueError("public DNS lookup failed or was truncated")
            for answer in payload.get("Answer", []):
                if answer.get("type") in {1, 28}:
                    address = ipaddress.ip_address(answer["data"])
                    if not address.is_global:
                        raise ValueError("web host resolves to a non-public address")
                    addresses.append(str(address))
        if not addresses:
            raise ValueError("public DNS lookup returned no addresses")
        return addresses[0]

    def get(self, url: str, *, timeout_seconds: float, max_bytes: int) -> HTTPResult:
        host, _ = _validate_url_shape(url)
        ip = self._resolve(host, timeout_seconds)
        return self._fetch(url, ip, timeout_seconds, max_bytes)

    @staticmethod
    def _fetch(url: str, ip: str, timeout_seconds: float, max_bytes: int) -> HTTPResult:
        host, path = _validate_url_shape(url)
        parsed = urlsplit(url)
        target = path + ("?" + parsed.query if parsed.query else "")
        connection = _PinnedHTTPSConnection(host, ip, timeout_seconds)
        try:
            connection.request(
                "GET",
                target,
                headers={"Accept": "application/json, text/html, text/plain", "User-Agent": "roboenv-evidence/1.0"},
            )
            response = connection.getresponse()
            if response.status != 200:
                raise RuntimeError(f"web request returned HTTP {response.status}")
            content_type = response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type not in {"application/json", "text/html", "text/plain", "application/xhtml+xml"}:
                raise ValueError("web response is not JSON or text")
            body = response.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise ValueError("web response exceeds configured byte limit")
            return HTTPResult(body, content_type)
        finally:
            connection.close()


class _VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "nav", "footer", "aside", "svg"}:
            self.hidden += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "nav", "footer", "aside", "svg"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data: str) -> None:
        if not self.hidden and data.strip():
            self.parts.append(data)


def _plain_text(body: bytes, content_type: str, max_chars: int) -> str:
    decoded = body.decode("utf-8", errors="replace")
    if content_type in {"text/html", "application/xhtml+xml"}:
        parser = _VisibleText()
        parser.feed(decoded)
        decoded = " ".join(parser.parts)
    return re.sub(r"\s+", " ", decoded).strip()[:max_chars]


class _DuckDuckGoHits(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hits: list[tuple[str, str, str]] = []
        self.active_href: str | None = None
        self.title_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        if tag == "a" and "result__a" in classes:
            self.active_href = attributes.get("href") or ""
            self.title_parts = []

    def handle_data(self, data: str) -> None:
        if self.active_href is not None:
            self.title_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.active_href is not None:
            href = self.active_href
            parsed = urlsplit(href)
            if parsed.hostname in {"duckduckgo.com", "www.duckduckgo.com"}:
                href = parse_qs(parsed.query).get("uddg", [""])[0]
            if href.startswith("https://"):
                title = re.sub(r"\s+", " ", " ".join(self.title_parts)).strip()
                self.hits.append((href, title, ""))
            self.active_href = None
            self.title_parts = []


def _domain_matches(host: str, domain: str) -> bool:
    domain = domain.lower().rstrip(".")
    return host == domain or host.endswith("." + domain)


def _mentions(text: str, name: str) -> bool:
    if not name.strip():
        return False
    def compact(s: str) -> str:
        return re.sub(r"[^\w]+", "", s.casefold())

    return compact(name) in compact(text)


def _quote_present(text: str, quote: str) -> bool:
    if not quote.strip():
        return False

    def normalize(value: str) -> str:
        return re.sub(r"\s+", " ", value).strip().casefold()

    return normalize(quote) in normalize(text)


@dataclass(frozen=True)
class SourceRecord:
    source_id: str
    url: str
    title: str
    discovery_snippet: str
    text_excerpt: str
    content_sha256: str
    retrieved_at: str
    source_kind: Literal["official", "other"]
    applicability: Applicability
    retrieval_status: Literal["fetched", "unverified"]
    note: str = ""


@dataclass(frozen=True)
class WebSearchResult:
    identity: ObjectIdentity
    query: str
    provider: Provider
    status: SearchStatus
    sources: tuple[SourceRecord, ...] = ()
    errors: tuple[str, ...] = ()
    searched_at: str = ""
    cache_hit: bool = False


@dataclass(frozen=True)
class ClaimCandidate:
    key: str
    value: str
    source_id: str
    quote: str


@dataclass(frozen=True)
class ClaimAssessment:
    key: str
    value: str
    source_id: str
    status: ClaimStatus
    reason: str


def assess_claims(result: WebSearchResult, claims: list[ClaimCandidate]) -> list[ClaimAssessment]:
    """Verify exact source quotations and flag disagreements among proposed claims.

    A candidate is only a quoted, potentially applicable source statement. Its
    technical interpretation still belongs to the design/review stages.
    """
    sources = {source.source_id: source for source in result.sources}
    preliminary: list[ClaimAssessment] = []
    for claim in claims:
        source = sources.get(claim.source_id)
        if source is None or source.retrieval_status != "fetched":
            reason = "source page was not fetched"
        elif source.applicability == "unverified":
            reason = "source applicability to the identified object is unverified"
        elif not _quote_present(source.text_excerpt, claim.quote):
            reason = "quotation is absent from the fetched page excerpt"
        else:
            preliminary.append(
                ClaimAssessment(claim.key, claim.value, claim.source_id, "candidate", "quoted source text found")
            )
            continue
        preliminary.append(ClaimAssessment(claim.key, claim.value, claim.source_id, "unverified", reason))
    values: dict[str, set[str]] = {}
    for item in preliminary:
        if item.status == "candidate":
            values.setdefault(item.key, set()).add(re.sub(r"\s+", " ", item.value).strip().casefold())
    return [
        ClaimAssessment(item.key, item.value, item.source_id, "conflict", "applicable sources disagree")
        if item.status == "candidate" and len(values[item.key]) > 1
        else item
        for item in preliminary
    ]


class WebEvidenceClient:
    def __init__(self, policy: WebSearchPolicy, transport: TextTransport | None = None):
        self.policy = policy
        self.transport = transport or PublicHTTPS(policy.dns_resolver)

    def _search_url(self, query: str) -> str:
        if self.policy.provider == "duckduckgo":
            base = "https://html.duckduckgo.com/html/"
            params = {"q": query}
        elif self.policy.provider == "wikipedia":
            base = f"https://{self.policy.wikipedia_language}.wikipedia.org/w/api.php"
            params = {
                "action": "query", "list": "search", "format": "json", "srnamespace": "0",
                "srlimit": str(self.policy.max_results), "srsearch": query,
            }
        else:
            base = self.policy.searxng_endpoint
            params = {"q": query, "format": "json", "categories": "general"}
        parsed = urlsplit(base)
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(params), ""))

    def _hits(self, raw: dict) -> list[tuple[str, str, str]]:
        if self.policy.provider == "wikipedia":
            items = raw.get("query", {}).get("search", [])
            if not isinstance(items, list):
                raise ValueError("Wikipedia search response has no result list")
            return [
                (
                    f"https://{self.policy.wikipedia_language}.wikipedia.org/wiki/"
                    + quote(str(item["title"]).replace(" ", "_")),
                    str(item["title"]),
                    _plain_text(str(item.get("snippet", "")).encode(), "text/html", 500),
                )
                for item in items[: self.policy.max_results]
                if isinstance(item, dict) and item.get("title")
            ]
        items = raw.get("results", [])
        if not isinstance(items, list):
            raise ValueError("SearXNG search response has no result list")
        return [
            (str(item["url"]), str(item.get("title", "")), str(item.get("content", ""))[:500])
            for item in items[: self.policy.max_results]
            if isinstance(item, dict) and item.get("url")
        ]

    def _duckduckgo_hits(self, body: bytes) -> list[tuple[str, str, str]]:
        decoded = body.decode("utf-8", errors="replace")
        parser = _DuckDuckGoHits()
        parser.feed(decoded)
        if not parser.hits and "no results found" not in decoded.casefold():
            raise RuntimeError("DuckDuckGo returned no parseable results; search may be unavailable")
        return parser.hits[: self.policy.max_results]

    def _source(self, identity: ObjectIdentity, hit: tuple[str, str, str]) -> SourceRecord:
        url, title, snippet = hit
        source_id = "web:" + hashlib.sha256(url.encode()).hexdigest()[:16]
        now = datetime.now(timezone.utc).isoformat()
        source_kind: Literal["official", "other"] = "other"
        try:
            host, _ = _validate_url_shape(url)
            if self.policy.allowed_source_domains and not any(
                _domain_matches(host, domain) for domain in self.policy.allowed_source_domains
            ):
                raise ValueError("source domain is not allowed by policy")
            if any(_domain_matches(host, domain) for domain in self.policy.official_domains):
                source_kind = "official"
            response = self.transport.get(
                url,
                timeout_seconds=self.policy.timeout_seconds,
                max_bytes=self.policy.max_response_bytes,
            )
            if response.content_type not in {"text/html", "text/plain", "application/xhtml+xml"}:
                raise ValueError("source page is not text")
            excerpt = _plain_text(response.body, response.content_type, self.policy.max_text_chars)
            applicability: Applicability = "unverified"
            if identity.model and _mentions(excerpt, identity.model):
                applicability = "model_mentioned"
            elif identity.series and _mentions(excerpt, identity.series):
                applicability = "series_mentioned"
            return SourceRecord(
                source_id, url, title, snippet, excerpt,
                hashlib.sha256(response.body).hexdigest(), now, source_kind,
                applicability, "fetched",
                "Model/series mention indicates a candidate source, not proven product revision or part compatibility.",
            )
        except (ValueError, RuntimeError, OSError, ssl.SSLError) as error:
            return SourceRecord(
                source_id, url, title, snippet, "", "", now, source_kind,
                "unverified", "unverified", str(error),
            )

    def search(self, identity: ObjectIdentity, cache_dir: Path) -> WebSearchResult:
        policy = self.policy
        now = datetime.now(timezone.utc)
        if not policy.enabled:
            return WebSearchResult(identity, identity.query, policy.provider, "disabled", searched_at=now.isoformat())
        if identity.specificity == "category" and not policy.allow_category_search:
            return WebSearchResult(
                identity, identity.query, policy.provider, "skipped_identity",
                errors=("search requires at least a product series or model",), searched_at=now.isoformat(),
            )
        key_data = json.dumps(
            {"identity": asdict(identity), "policy": asdict(policy)}, sort_keys=True, ensure_ascii=False
        )
        key = hashlib.sha256(key_data.encode()).hexdigest()
        cache_file = cache_dir / f"web-{key}.json"
        if policy.cache_ttl_seconds and cache_file.is_file():
            try:
                saved = json.loads(cache_file.read_text(encoding="utf-8"))
                searched_at = datetime.fromisoformat(saved["searched_at"])
                if now - searched_at < timedelta(seconds=policy.cache_ttl_seconds):
                    return WebSearchResult(
                        identity, identity.query, policy.provider, saved["status"],
                        tuple(SourceRecord(**item) for item in saved["sources"]),
                        tuple(saved["errors"]), saved["searched_at"], True,
                    )
            except (ValueError, KeyError, TypeError, OSError):
                pass
        try:
            response = self.transport.get(
                self._search_url(identity.query),
                timeout_seconds=policy.timeout_seconds,
                max_bytes=policy.max_response_bytes,
            )
            if policy.provider == "duckduckgo":
                if response.content_type != "text/html":
                    raise ValueError("DuckDuckGo did not return HTML")
                hits = self._duckduckgo_hits(response.body)
            else:
                if response.content_type != "application/json":
                    raise ValueError("search endpoint did not return JSON")
                raw = json.loads(response.body)
                if not isinstance(raw, dict):
                    raise ValueError("search response must be a JSON object")
                hits = self._hits(raw)
            # Duplicate URLs do not consume additional page budget.
            seen_urls: set[str] = set()
            unique_hits: list[tuple[str, str, str]] = []
            for hit in hits:
                if hit[0] not in seen_urls:
                    seen_urls.add(hit[0])
                    unique_hits.append(hit)
                if len(unique_hits) == policy.max_pages:
                    break
            sources = tuple(self._source(identity, hit) for hit in unique_hits)
            result = WebSearchResult(
                identity, identity.query, policy.provider, "ok", sources,
                searched_at=now.isoformat(),
            )
        except (ValueError, RuntimeError, OSError, json.JSONDecodeError) as error:
            result = WebSearchResult(
                identity, identity.query, policy.provider, "error",
                errors=(f"web search failed: {error}",), searched_at=now.isoformat(),
            )
        if result.status == "ok" and all(source.retrieval_status == "fetched" for source in result.sources):
            cache_dir.mkdir(parents=True, exist_ok=True)
            payload = {
                "status": result.status, "sources": [asdict(item) for item in result.sources],
                "errors": list(result.errors), "searched_at": result.searched_at,
            }
            pending = cache_file.with_suffix(".tmp")
            pending.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            pending.replace(cache_file)
        return result
