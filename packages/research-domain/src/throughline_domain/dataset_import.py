"""
Bringing a dataset the repository search found into the project (D202).

Find data searches four repositories and leads with the three fields that
decide whether data can answer anything — licence, file formats, embargo — and
then offers nothing to do with a usable record but open it in a browser tab.
The capability inventory records the whole gap in one line: "**Has no
import/add control at all** — the only write is `api.post` to
`/api/datasets/search`" (`docs/audit/capability-inventory-2026-09-05.md`, §2
"Critic-added screens", Find data), and calls the screen a dead end. The
adjacent rail entry, Find papers, imports in a click.

This module is the server half of closing that. It answers one question —
*may these bytes be downloaded, and are they a dataset?* — and hands back the
bytes for the caller to register through the ordinary upload door, so a dataset
that arrives from Zenodo and one dragged off a desktop are the same kind of
object with the same audit trail and the same deduplication.

**It is a server fetching a URL a client chose, which is an SSRF hole unless
it is arranged not to be.** Three guards, and the order they run in is part of
the design:

1. **The host must be a repository this installation actually searches**, and
   that list is *derived* from the connectors behind `/api/datasets/search`
   rather than typed here. A second hand-typed list is how the connector SDK's
   own format list drifted until a test was written to hold it (see
   `throughline_connectors.datasets.TABULAR`), and a drifted allowlist either
   refuses a repository the product advertises or permits one it does not. The
   check runs *before* any request, so a refused address is not even a
   connection.
2. **Every redirect hop is checked again.** A public URL that 302s to
   somewhere else is the standard way a one-time guard is bypassed;
   `throughline_connectors.papers` reasons this out at length for PDFs and
   this follows it.
3. **The private-address guard lives in the fetcher**, next to the socket that
   would make the request, because that is the only place a hostname has been
   resolved. `fetch` is injectable for exactly this reason: the tests exercise
   every refusal above without a network, and the code that touches one is the
   part they replace.

What comes back must also be a *dataset*: a tabular file, under a stated cap,
with a name whose suffix the ingestion worker can dispatch on — it reads the
suffix off `files.filename`, so a file registered without one would be queued
and then permanently fail with "This file type is not supported", which is a
worse answer than refusing in place with a sentence (§104).
"""

from __future__ import annotations

import ast
import functools
import inspect
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Callable, Iterator

__all__ = [
    "DatasetImportError", "DatasetImportRefused", "DatasetImportUnreachable",
    "Fetched", "FetchedDataset", "MAX_BYTES", "MAX_REDIRECTS",
    "TABULAR_SUFFIXES", "fetch_dataset", "searched_repository_domains",
]


class DatasetImportError(Exception):
    """Anything that stopped an import, with a sentence for a person."""


class DatasetImportRefused(DatasetImportError):
    """This will not be imported, and the reason is the caller's to act on."""


class DatasetImportUnreachable(DatasetImportError):
    """The repository did not answer. Nothing here decided anything."""


def _tabular_suffixes() -> frozenset[str]:
    """The four formats this door promises, and only the ones it can open.

    Intersected with what this installation genuinely reads rather than
    asserted alone: `throughline_ingestion.datasets.readable_suffixes` is the
    single source of truth for "can you open this?" — its own comment says so —
    and accepting a file the profiler will then refuse turns a refusal a
    researcher could have acted on into a source stuck at `failed` carrying the
    same information an hour later. The soft import mirrors the connector SDK's,
    which reaches for the same constant from the other side and for the same
    reason.
    """
    promised = frozenset({".csv", ".tsv", ".xlsx", ".json"})
    try:
        from throughline_ingestion.datasets import readable_suffixes
    except ImportError:  # pragma: no cover - ingestion is installed everywhere
        return promised
    return promised & frozenset(readable_suffixes())


#: What an import will accept, named in the refusal when it will not.
TABULAR_SUFFIXES: frozenset[str] = _tabular_suffixes()

#: Content types that name a tabular format, for the repositories that serve a
#: download URL with no filename in it at all (Dataverse's
#: `/api/access/datafile/123` is the common one). Only consulted when neither
#: the Content-Disposition nor the path carries a suffix — a name that *has* a
#: suffix is believed over a header, because a repository serving a PDF as
#: `text/csv` is a mislabelled PDF and not a CSV.
MEDIA_TYPES: dict[str, str] = {
    "text/csv": ".csv",
    "application/csv": ".csv",
    "text/tab-separated-values": ".tsv",
    "application/json": ".json",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
}

#: What a suffix is served as when the repository did not say.
DEFAULT_MEDIA_TYPE: dict[str, str] = {
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    ".json": "application/json",
    ".xlsx": ("application/vnd.openxmlformats-officedocument"
              ".spreadsheetml.sheet"),
}

#: 200 MB. Large enough for a national panel with a decade of rows, small
#: enough that a mistyped link cannot fill the disk or hold a request open for
#: an hour. Named in the refusal, because "too large" without the cap tells a
#: researcher nothing about whether trimming the file would help.
MAX_BYTES = 200 * 1024 * 1024

#: Enough for the usual landing-page → file-server hop, few enough that a
#: redirect loop ends. Papers allow five for the doi.org → publisher → CDN
#: chain; a dataset file is fetched from a repository directly.
MAX_REDIRECTS = 3

_REDIRECTS = {301, 302, 303, 307, 308}

# Only these connectors currently surface direct file URLs that the UI can
# import. Dryad and Figshare search results are landing records with
# files_listed=False, so accepting arbitrary sibling subdomains for them would
# widen an SSRF boundary without enabling a real product path.
_IMPORT_FILE_HOSTS = frozenset({"zenodo.org", "dataverse.harvard.edu"})


@dataclass
class Fetched:
    """One HTTP response, one hop, no redirect followed.

    The seam the tests replace. It is deliberately a *single hop*: following
    redirects inside the fetcher would put the hops where the host check cannot
    see them, which is the bug guard 2 above exists to prevent.
    """

    status: int = 200
    #: Header names are matched case-insensitively by :meth:`header`.
    headers: dict[str, str] = field(default_factory=dict)
    #: At most ``MAX_BYTES + 1`` bytes: one over the cap is what proves the cap
    #: was exceeded without holding the whole file in memory.
    body: bytes = b""

    def header(self, name: str) -> str:
        for key, value in self.headers.items():
            if key.lower() == name.lower():
                return value
        return ""


@dataclass
class FetchedDataset:
    """Bytes that passed every check, named so ingestion can dispatch on them."""

    content: bytes
    #: Carries a suffix in :data:`TABULAR_SUFFIXES`. The ingestion worker reads
    #: the format off this name, not off the stored file, which is
    #: content-addressed and has no extension on disk.
    filename: str
    media_type: str
    #: Where the bytes actually came from, after redirects — which is not
    #: always where the researcher was sent from, and is the honest thing to
    #: record on the source.
    url: str

    @property
    def size_bytes(self) -> int:
        return len(self.content)


#: A single hop. Raises :class:`DatasetImportUnreachable` if the host does not
#: answer; returns the response, redirect or error status included, otherwise.
Fetcher = Callable[[str], Fetched]


# ---------------------------------------------------------------------------
# The allowlist, derived from the repositories the search already queries
# ---------------------------------------------------------------------------

_HOST_IN_URL = re.compile(r"https?://([A-Za-z0-9.\-]+)")


def _registrable(host: str) -> str:
    """
    The domain a repository's own file server would share with its API.

    Not a cosmetic reduction: Figshare searches `api.figshare.com` and serves
    records from `figshare.com`, Zenodo serves files from a sibling host, and
    an allowlist of exactly the hosts the search calls would refuse every real
    download while looking correct.

    The reduction stops at three labels wherever the last one is short, because
    under a country-code suffix ("co.uk", "gov.au") the last two labels are the
    *suffix* and reducing to them would admit an entire country. There the host
    is kept as the connector wrote it — strict rather than sorry, and the worst
    case is a refusal a person can read.
    """
    labels = host.lower().strip(".").split(".")
    if len(labels) < 3 or len(labels[-1]) <= 2:
        return ".".join(labels)
    return ".".join(labels[-2:])


def _hosts_in(source: str) -> Iterator[str]:
    """Hosts in the string literals a connector uses, docstrings excluded.

    Parsed rather than grepped so a URL in prose — a comment pointing at an API
    document, a docstring naming a paper — cannot widen the allowlist. Only
    strings the code actually holds count.
    """
    tree = ast.parse(source)
    prose = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef))
        and node.body and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in prose):
            yield from _HOST_IN_URL.findall(node.value)


@functools.cache
def searched_repository_domains() -> frozenset[str]:
    """
    The repositories `/api/datasets/search` queries, as domains.

    Read off the connector classes themselves. A hand-written copy would be a
    second list to keep in step with `DATASET_CONNECTORS`, and the failure mode
    is silent in both directions: a repository added to the search that cannot
    be imported from, or a host still permitted here after the connector that
    justified it was removed.
    """
    from throughline_connectors.datasets import DATASET_CONNECTORS

    domains: set[str] = set()
    for connector in DATASET_CONNECTORS.values():
        try:
            source = inspect.getsource(connector)
        except (OSError, TypeError):  # pragma: no cover - source is on disk
            continue
        for host in _hosts_in(source):
            domains.add(_registrable(host))
    return frozenset(domains)


def _repositories() -> frozenset[str]:
    """The allowlist, or a refusal saying why there is not one.

    Fails closed. If the connectors cannot be imported or name no host, this
    installation cannot say what a repository is, and permitting anything on
    that basis would be the whole guard gone in an ImportError.
    """
    try:
        domains = searched_repository_domains()
    except ImportError as exc:
        raise DatasetImportRefused(
            "This installation has no dataset repositories installed, so "
            "there is nothing an import could be checked against."
        ) from exc
    if not domains:
        raise DatasetImportRefused(
            "This installation could not determine which dataset repositories "
            "it searches, so it will not download from any of them."
        )
    return domains


def _checked(url: str, *, redirected: bool = False) -> str:
    """Return a normalized URL only for an exact, approved import host."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise DatasetImportRefused(
            "Only http and https addresses can be imported; "
            f"{parsed.scheme or 'that address'} cannot.")
    if parsed.username is not None or parsed.password is not None:
        raise DatasetImportRefused(
            "Repository download addresses may not contain credentials.")
    if not parsed.hostname:
        raise DatasetImportRefused("That address names no host.")
    if parsed.port not in (None, 80, 443):
        raise DatasetImportRefused(
            "Repository download addresses may not select a custom network port.")

    host = parsed.hostname.lower().rstrip(".")
    approved = next((candidate for candidate in _IMPORT_FILE_HOSTS
                     if host == candidate), None)
    if approved is None:
        named = ", ".join(sorted(_IMPORT_FILE_HOSTS))
        raise DatasetImportRefused(
            (f"That address redirected to {host}, which is not an approved "
             if redirected else
             f"{host} is not an approved ")
            + f"dataset file host. Approved hosts are: {named}.")

    # Rebuild the network target from the approved literal host. User input can
    # still choose the path/query for a file on that repository, but cannot
    # choose the authority that urllib connects to.
    netloc = approved
    if parsed.port == 80 and parsed.scheme == "http":
        netloc += ":80"
    elif parsed.port == 443 and parsed.scheme == "https":
        netloc += ":443"
    return urllib.parse.urlunsplit(
        (parsed.scheme, netloc, parsed.path or "/", parsed.query, ""))


# ---------------------------------------------------------------------------
# What came back, and whether it is a dataset
# ---------------------------------------------------------------------------

_FILENAME = re.compile(r"filename\*?=(?:UTF-8''|\"|')?([^\";']+)")


def _human(size: int) -> str:
    """A size a person can compare against a link they were about to click."""
    if size >= 1024 * 1024:
        megabytes = size / (1024 * 1024)
        return f"{megabytes:.0f} MB" if megabytes >= 10 else f"{megabytes:.1f} MB"
    return f"{size} bytes"


def _offered_name(url: str, response: Fetched) -> str:
    """What the repository calls this file.

    Content-Disposition first: it is the only thing an opaque download URL
    (`/api/access/datafile/123`) says about the file at all, and where both are
    present it is the repository stating the name rather than us guessing one
    out of a path.
    """
    disposition = response.header("content-disposition")
    match = _FILENAME.search(disposition)
    if match:
        return _basename(urllib.parse.unquote(match.group(1)))
    path = urllib.parse.urlparse(url).path
    return _basename(urllib.parse.unquote(path))


def _basename(name: str) -> str:
    """The last path segment of a name a remote server chose.

    A header is not a promise: `filename="../../etc/passwd"` is a thing a
    repository can send, and this name is written to a database row and shown
    on the Sources list. Stored bytes are content-addressed and the name never
    reaches the filesystem, so this is belt to that braces — and it is one line.
    """
    trimmed = name.replace("\\", "/").strip().strip("/")
    return PurePosixPath(trimmed).name[:120] if trimmed else ""


def _named_for_ingestion(url: str, response: Fetched) -> tuple[str, str]:
    """`(filename, media_type)` for a tabular file, or a refusal saying why."""
    allowed = ", ".join(sorted(TABULAR_SUFFIXES))
    name = _offered_name(url, response)
    suffix = PurePosixPath(name).suffix.lower()
    declared = response.header("content-type").split(";")[0].strip().lower()

    if suffix:
        if suffix not in TABULAR_SUFFIXES:
            raise DatasetImportRefused(
                f"{name} is not tabular data. This imports {allowed} files, "
                f"and that address offers a {suffix} file.")
        return name, declared or DEFAULT_MEDIA_TYPE[suffix]

    # No suffix anywhere, so the content type is the only statement about the
    # format there is. A file registered without a suffix would queue and then
    # fail in the worker, so one is put on the name here rather than there.
    if declared not in MEDIA_TYPES or MEDIA_TYPES[declared] not in TABULAR_SUFFIXES:
        raise DatasetImportRefused(
            f"That address does not offer tabular data. This imports {allowed} "
            f"files, and that address is served as "
            f"{declared or 'no stated content type'}.")
    supplied = MEDIA_TYPES[declared]
    return f"{name or 'dataset'}{supplied}", declared


def _within_the_cap(response: Fetched) -> bytes:
    """The body, if it is under the cap and is not empty."""
    declared = response.header("content-length")
    if declared.isdigit() and int(declared) > MAX_BYTES:
        # Refused on the header before reading the body: the point of a cap is
        # not to download the file it refuses.
        raise DatasetImportRefused(
            f"That file is {_human(int(declared))}, which is larger than the "
            f"{_human(MAX_BYTES)} this will import.")
    if len(response.body) > MAX_BYTES:
        # The fetcher reads one byte past the cap, so a server that understates
        # Content-Length — or omits it, which is common — is caught by what
        # actually arrived rather than by what it claimed.
        raise DatasetImportRefused(
            f"That file is larger than the {_human(MAX_BYTES)} this will "
            "import.")
    if not response.body:
        raise DatasetImportRefused(
            "That address returned an empty file, so there is nothing to "
            "profile.")
    return response.body


def fetch_dataset(url: str, *, fetch: Fetcher | None = None) -> FetchedDataset:
    """
    Download a dataset file from a repository this installation searches.

    Every refusal is a :class:`DatasetImportRefused` carrying one sentence
    naming what stopped it — the host, the format, or the cap — because that is
    what a caller can put on the screen in place of the control (§104).
    """
    fetch = fetch or DEFAULT_FETCHER
    current = _checked(url)

    for _ in range(MAX_REDIRECTS + 1):
        response = fetch(current)
        if response.status in _REDIRECTS:
            location = response.header("location")
            if not location:
                raise DatasetImportRefused(
                    "That address redirected to nowhere.")
            current = _checked(
                urllib.parse.urljoin(current, location), redirected=True)
            continue
        if response.status >= 400:
            raise DatasetImportUnreachable(
                f"The repository answered {response.status} for that file. It "
                "may have been withdrawn, or need an account.")
        filename, media_type = _named_for_ingestion(current, response)
        return FetchedDataset(content=_within_the_cap(response),
                              filename=filename, media_type=media_type,
                              url=current)

    raise DatasetImportRefused(
        f"That address redirected more than {MAX_REDIRECTS} times, which is a "
        "loop rather than a file.")


# ---------------------------------------------------------------------------
# The one part that touches the network
# ---------------------------------------------------------------------------


def _fetch_over_http(url: str) -> Fetched:
    """One hop with `urllib`, redirects handed back rather than followed.

    The private-address guard runs here because here is where a hostname has
    been resolved: an allowlisted name that resolves to `127.0.0.1` or
    `169.254.169.254` passes the host check and must still not be fetched.
    `throughline_connectors.papers` already resolves every address a client
    supplies and checks every family it resolves to; a second implementation of
    that would be a second place to get it wrong, so this calls it.
    """
    from throughline_connectors.papers import _is_public
    from throughline_connectors.base import USER_AGENT

    parsed = urllib.parse.urlparse(url)
    if not _is_public(parsed.hostname or ""):
        # One message for "private address" and "does not resolve", for the
        # reason `papers` gives: telling them apart makes this a scanner that
        # reports which internal hosts exist.
        raise DatasetImportRefused(
            f"{parsed.hostname} is not a public address this can fetch from.")

    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT.format(mailto="unknown"),
        "Accept": "text/csv,application/json,*/*",
    })
    # With the paper fetcher's peer check as well as its name check: the name is
    # resolved again to connect, and the address actually reached is the one
    # that has to be public (T165).
    from throughline_connectors.papers import (
        PaperFetchError, _PublicOnlyHTTP, _PublicOnlyHTTPS)
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _NoRedirects,
        _PublicOnlyHTTP,
        _PublicOnlyHTTPS,
    )
    try:
        with opener.open(request, timeout=TIMEOUT) as response:
            return Fetched(status=response.status,
                           headers=dict(response.headers.items()),
                           # One past the cap; see `_within_the_cap`.
                           body=response.read(MAX_BYTES + 1))
    except urllib.error.HTTPError as exc:
        # A redirect arrives here once `_NoRedirects` declines to follow it, so
        # it is a response to hand back rather than a failure.
        return Fetched(status=exc.code, headers=dict(exc.headers.items()))
    except PaperFetchError as exc:
        # The peer check's refusal, in this module's own terms, so the route
        # answers it as the refusal it is rather than as a server error.
        raise DatasetImportRefused(str(exc)) from exc
    except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
        raise DatasetImportUnreachable(
            f"That file could not be downloaded ({exc}).") from exc


#: Long enough for a repository under load, short enough that a hung host does
#: not hold the request open until the researcher gives up on the screen.
TIMEOUT = 60.0


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """Hands redirects back rather than following them (see `fetch_dataset`).

    `urllib` would otherwise follow a redirect without consulting the host
    check, which is the one bypass this file is arranged to prevent.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


#: Read at call time by :func:`fetch_dataset`, so a test replaces the network
#: by replacing this name.
DEFAULT_FETCHER: Fetcher = _fetch_over_http
