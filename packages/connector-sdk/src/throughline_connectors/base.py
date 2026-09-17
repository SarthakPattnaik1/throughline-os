"""
The connector contract.

Every literature source implements one interface, and the interface is shaped by
the failure modes rather than by the happy path — because with eight upstream
APIs, something is always broken, rate-limited or mid-outage, and how the system
behaves then is what a researcher actually experiences.

Four rules, each earned from a specific way this goes wrong:

**Politeness is not optional and not configurable per call.** Every one of these
APIs is a public good run on someone's budget. A token bucket sits in the base
class so a connector cannot forget it, and the arXiv limit in particular is a
published request-per-three-seconds figure that gets you blocked if you ignore
it.

**One source failing must not empty the page.** Search fans out; results arrive
per source with a status. A failed source renders as a calm note beside the
results that did arrive, never as an error page — a researcher searching four
databases should not lose three because one is down.

**Records are normalised and deduplicated on identifiers, not titles.** The same
paper arrives from arXiv, Crossref and OpenAlex with three different author
spellings. DOI, then arXiv id, then PMID, then a fuzzy title match as the last
resort.

**Field-level provenance survives.** When two sources disagree about a
publication year — and they do, constantly, because one records the preprint and
one the version of record — the record keeps both and says which was preferred.
Silently picking one is how a bibliography ends up with a date nobody can
defend.

All connector HTTP goes through a public-network-only transport. Most connector
URLs are assembled from literal API origins, but keeping the guard in the base
class means a future connector cannot accidentally turn a query, cursor or
configuration value into an SSRF primitive. Redirects are rechecked, environment
proxies are disabled for this server-side research fetch path, and the actual
connected peer must be globally routable so DNS rebinding cannot swap a public
answer for a private socket between validation and connect.
"""

from __future__ import annotations

import http.client
import ipaddress
import json
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Iterator

USER_AGENT = (
    "Throughline/0.1 (research workspace; +https://throughline.local; "
    "mailto:{mailto})"
)


class ConnectorError(RuntimeError):
    """A source could not be reached or understood."""


class RateLimited(ConnectorError):
    """The upstream asked us to slow down."""


@dataclass
class SourceRecord:
    """One work, normalised, with field-level provenance."""

    title: str
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    pmid: str | None = None
    pmcid: str | None = None
    openalex_id: str | None = None
    oai_identifier: str | None = None
    abstract: str = ""
    venue: str = ""
    url: str = ""
    pdf_url: str = ""
    open_access: bool | None = None
    cited_by: int | None = None
    source: str = ""
    peer_reviewed: bool | None = None
    has_full_text: bool = False
    superseded_by: str | None = None
    provenance: dict[str, str] = field(default_factory=dict)
    disagreements: dict[str, dict[str, Any]] = field(default_factory=dict)

    def identity(self) -> tuple[str, str] | None:
        for kind, value in (("doi", self.doi), ("arxiv", self.arxiv_id),
                            ("pmid", self.pmid), ("pmcid", self.pmcid),
                            ("openalex", self.openalex_id)):
            if value:
                return (kind, str(value).lower())
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title, "authors": self.authors, "year": self.year,
            "doi": self.doi, "arxiv_id": self.arxiv_id, "pmid": self.pmid,
            "pmcid": self.pmcid, "openalex_id": self.openalex_id,
            "abstract": self.abstract, "venue": self.venue, "url": self.url,
            "pdf_url": self.pdf_url, "open_access": self.open_access,
            "cited_by": self.cited_by, "source": self.source,
            "peer_reviewed": self.peer_reviewed,
            "has_full_text": self.has_full_text,
            "superseded_by": self.superseded_by,
            "provenance": self.provenance, "disagreements": self.disagreements,
        }


class TokenBucket:
    """Politeness, enforced rather than documented."""

    def __init__(self, rate_per_second: float, burst: int = 1) -> None:
        self.rate = rate_per_second
        self.burst = max(1, burst)
        self._tokens = float(self.burst)
        self._checked = time.monotonic()
        self._lock = threading.Lock()

    def take(self) -> None:
        with self._lock:
            now = time.monotonic()
            self._tokens = min(self.burst,
                               self._tokens + (now - self._checked) * self.rate)
            self._checked = now
            if self._tokens < 1:
                wait = (1 - self._tokens) / self.rate
                time.sleep(wait)
                self._tokens = 0
                self._checked = time.monotonic()
            else:
                self._tokens -= 1


# ---------------------------------------------------------------------------
# Safe HTTP transport
# ---------------------------------------------------------------------------


def _public_url(url: str) -> str:
    """Return a normalized HTTP(S) URL only when every DNS answer is public."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ConnectorError("A connector tried to fetch a non-http(s) address.")
    if parsed.username is not None or parsed.password is not None:
        raise ConnectorError("Connector addresses may not contain credentials.")
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ConnectorError("The connector host could not be resolved.") from exc
    if not infos:
        raise ConnectorError("The connector host could not be resolved.")
    for info in infos:
        peer = info[4][0].split("%", 1)[0]
        if not ipaddress.ip_address(peer).is_global:
            raise ConnectorError("The connector host is not a public address.")
    return urllib.parse.urlunsplit(parsed)


class _PublicRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return super().redirect_request(
            req, fp, code, msg, headers, _public_url(newurl))


def _peer_checked_connection(base: type, *, host: str):
    """Connection factory that validates the socket peer after connect."""
    def make(connect_to: str, **kwargs):
        connection = base(connect_to, **kwargs)
        create = connection._create_connection

        def checked(address, *args, **kw):
            sock = create(address, *args, **kw)
            peer = sock.getpeername()[0].split("%", 1)[0]
            if not ipaddress.ip_address(peer).is_global:
                sock.close()
                raise ConnectorError(
                    f"{host or 'That host'} is not a public address.")
            return sock

        connection._create_connection = checked
        return connection
    return make


class _PublicHTTP(urllib.request.HTTPHandler):
    def http_open(self, req):  # noqa: D102
        host = urllib.parse.urlsplit(req.full_url).hostname or ""
        return self.do_open(
            _peer_checked_connection(http.client.HTTPConnection, host=host), req)


class _PublicHTTPS(urllib.request.HTTPSHandler):
    def https_open(self, req):  # noqa: D102
        host = urllib.parse.urlsplit(req.full_url).hostname or ""
        return self.do_open(
            _peer_checked_connection(http.client.HTTPSConnection, host=host),
            req,
            context=self._context,
        )


def _public_opener() -> urllib.request.OpenerDirector:
    # Explicit empty proxy map prevents HTTP_PROXY/HTTPS_PROXY from moving DNS
    # resolution and destination choice outside the guard above.
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _PublicRedirects,
        _PublicHTTP,
        _PublicHTTPS,
    )


class Connector:
    """A literature source. Subclasses implement `search` and `normalise`."""

    name: str = "connector"
    rate_per_second: float = 1.0
    burst: int = 1
    needs_contact: bool = False

    def __init__(self, *, mailto: str = "", api_key: str = "",
                 timeout: int = 20) -> None:
        self.mailto = mailto
        self.api_key = api_key
        self.timeout = timeout
        self._bucket = TokenBucket(self.rate_per_second, self.burst)

    # -- transport ---------------------------------------------------------

    def _open(self, request: urllib.request.Request):
        """Send one request through the public-network-only transport."""
        safe_url = _public_url(request.full_url)
        safe_request = urllib.request.Request(
            safe_url,
            data=request.data,
            headers=dict(request.header_items()),
            method=request.get_method(),
        )
        return _public_opener().open(safe_request, timeout=self.timeout)

    def _get(self, url: str, *, headers: dict[str, str] | None = None,
             attempts: int = 3) -> bytes:
        """Fetch, with backoff and jitter."""
        import random

        request_headers = {
            "User-Agent": USER_AGENT.format(mailto=self.mailto or "unknown"),
            "Accept": "application/json",
            **(headers or {}),
        }

        last: Exception | None = None
        for attempt in range(attempts):
            self._bucket.take()
            try:
                request = urllib.request.Request(
                    _public_url(url), headers=request_headers)
                with self._open(request) as r:
                    return r.read()
            except urllib.error.HTTPError as exc:
                last = exc
                if exc.code == 429 or 500 <= exc.code < 600:
                    if attempt == attempts - 1:
                        break
                    time.sleep((2 ** attempt) + random.random())
                    continue
                raise ConnectorError(
                    f"{self.name} returned {exc.code} for that query.") from exc
            except ConnectorError:
                # Security-policy failures are deterministic, not transient
                # network errors. Retrying would only hide the reason the URL
                # was refused and could turn an SSRF guard into a generic error.
                raise
            except (urllib.error.URLError, TimeoutError) as exc:
                last = exc
                if attempt == attempts - 1:
                    break
                time.sleep((2 ** attempt) + random.random())

        if isinstance(last, urllib.error.HTTPError) and last.code == 429:
            raise RateLimited(
                f"{self.name} is rate-limiting this request"
                + (" — adding an API key raises the limit considerably."
                   if not self.api_key else ".")
                + " Other sources are unaffected.") from last
        raise ConnectorError(
            f"{self.name} could not be reached. Other sources are unaffected.") from last

    def _send(self, url: str, *, method: str, payload: Any,
              headers: dict[str, str] | None = None) -> tuple[int, Any]:
        """A write, deliberately without automatic retry."""
        body = json.dumps(payload).encode("utf-8")
        request_headers = {
            "User-Agent": USER_AGENT.format(mailto=self.mailto or "unknown"),
            "Accept": "application/json",
            "Content-Type": "application/json",
            **(headers or {}),
        }

        self._bucket.take()
        request = urllib.request.Request(
            _public_url(url), data=body, method=method, headers=request_headers)
        try:
            with self._open(request) as r:
                raw = r.read()
                return r.status, (json.loads(raw.decode("utf-8")) if raw else None)
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                return exc.code, json.loads(raw.decode("utf-8")) if raw else None
            except (UnicodeDecodeError, json.JSONDecodeError):
                return exc.code, None
        except (urllib.error.URLError, TimeoutError, ConnectorError) as exc:
            raise ConnectorError(
                f"{self.name} could not be reached, and this was a write: it is "
                "not known whether it took effect. Check the library before trying "
                "again — retrying automatically could write it twice.") from exc

    def _json(self, url: str, **kwargs: Any) -> Any:
        raw = self._get(url, **kwargs)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ConnectorError(
                f"{self.name} returned something that was not JSON.") from exc

    # -- interface ---------------------------------------------------------

    def search(self, query: str, *, limit: int = 20) -> list[SourceRecord]:
        raise NotImplementedError

    def capability(self) -> dict[str, Any]:
        ready = not self.needs_contact or bool(self.mailto or self.api_key)
        return {
            "name": self.name,
            "ready": True,
            "polite": ready,
            "rate_per_second": self.rate_per_second,
            "note": None if ready else (
                f"{self.name} gives faster, more reliable service when you "
                "identify yourself. Set a contact email in settings."),
        }


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

_DOI = re.compile(r"(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)")


def clean_doi(value: Any) -> str | None:
    if not value:
        return None
    match = _DOI.search(str(value))
    return match.group(1).lower().rstrip(".") if match else None


_TAG = re.compile(r"<[^>]{1,80}>")
_ENTITY = {"&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"',
           "&apos;": "'", "&#39;": "'", "&nbsp;": " "}


def clean_text(value: Any) -> str:
    text = str(value or "")
    text = _TAG.sub(" ", text)
    for entity, char in _ENTITY.items():
        text = text.replace(entity, char)
    return re.sub(r"\s+", " ", text).strip()


def year_of(value: Any) -> int | None:
    match = re.search(r"(1[6-9]\d{2}|20\d{2})", str(value or ""))
    return int(match.group(1)) if match else None


__all__ = [
    "Connector", "ConnectorError", "RateLimited", "SourceRecord", "TokenBucket",
    "clean_doi", "clean_text", "year_of",
]
