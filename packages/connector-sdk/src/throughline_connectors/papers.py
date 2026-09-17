"""Fetching the PDF behind a search result (§204, §205).

The literature search already returns a `pdf_url` for open-access records, and
the reader already renders bytes. This is the missing middle — and it is a
server-side fetch rather than a browser one for a boring reason: arXiv, Crossref
and the rest do not send CORS headers, so a browser cannot read the response
even when it is allowed to make the request.

**The guard that matters is SSRF, and it is the whole reason this file is not
four lines.** An endpoint that takes a URL from the client and fetches it from
the server will, given a malicious or careless caller, happily fetch
`http://127.0.0.1:8080/api/...`, `http://[::1]/`, or a cloud metadata address —
using the server's own network position, which is exactly the position an
attacker does not have. This installation is local, which lowers the stakes and
does not change the shape of the bug: the API also binds a database and an
admin surface on this machine, and "it's only localhost" is what makes localhost
worth reaching.

So the destination is resolved and checked *before* the request is made, and
rejected if it lands anywhere private. Redirects are followed manually so each
hop is checked the same way — a public URL that 302s to `169.254.169.254` is
the standard way this guard gets bypassed when it is applied only once.

**And the address actually connected to is checked as well.** Resolving a name
to decide and letting the connection resolve it again is the other standard
bypass: a name whose DNS answers public for the check and loopback for the
connection (rebinding) passed the first guard and was fetched from this machine
(T165). See `_connection_to_public_peers`.

**Proxies are deliberately disabled for this security-sensitive fetcher.** If
urllib honors `HTTP_PROXY`/`HTTPS_PROXY`, the socket peer is the proxy rather
than the destination. A proxy could then resolve a destination differently from
this process and turn a URL we checked as public into a request to an internal
address. Ordinary connectors may support proxies; this endpoint accepts a URL
from a client and therefore does not.

**A PDF is not trusted here either.** The bytes are handed to the browser, which
hands them to PDF.js with scripting off; nothing on this side parses them. What
this does enforce is that they are plausibly a PDF and not, say, a 900MB file
or an HTML login page — a reader that spent a minute downloading a captcha page
and then said "that file could not be opened" would be telling the truth and
helping nobody.
"""

from __future__ import annotations

import http.client
import ipaddress
import socket
import urllib.error
import urllib.parse
import urllib.request

from .base import USER_AGENT, ConnectorError

#: Papers are large but not unbounded. A 60MB PDF is a big supplementary-heavy
#: article; a 600MB one is not a paper and would sit in browser memory.
MAX_BYTES = 60 * 1024 * 1024

#: Long enough for a slow repository, short enough that a hung host does not
#: hold a request open until the researcher gives up on the whole page.
TIMEOUT = 30.0

#: Enough hops for the usual doi.org -> publisher -> CDN chain, few enough that
#: a redirect loop ends.
MAX_REDIRECTS = 5


class PaperFetchError(ConnectorError):
    """A PDF that will not be fetched, with a reason for a person."""


def _is_public(host: str) -> bool:
    """Whether a hostname resolves only to globally routable addresses."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    if not infos:
        return False

    for info in infos:
        address = ipaddress.ip_address(info[4][0].split("%", 1)[0])
        if not address.is_global:
            return False
    return True


def _checked(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise PaperFetchError("Only http and https addresses can be fetched.")
    if parsed.username is not None or parsed.password is not None:
        raise PaperFetchError("Addresses containing credentials cannot be fetched.")
    if not parsed.hostname:
        raise PaperFetchError("That address names no host.")
    if not _is_public(parsed.hostname):
        # Deliberately the same message for "private address" and "does not
        # resolve": distinguishing them turns this endpoint into a scanner that
        # reports which internal hosts exist.
        raise PaperFetchError(
            f"{parsed.hostname} is not a public address this can fetch from.")
    return urllib.parse.urlunsplit(parsed)


def _safe_opener() -> urllib.request.OpenerDirector:
    """An opener that neither uses environment proxies nor follows redirects."""
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _NoRedirects,
        _PublicOnlyHTTP,
        _PublicOnlyHTTPS,
    )


def fetch_pdf(url: str, *, mailto: str | None = None) -> bytes:
    """Fetch one public PDF, validating every redirect and actual socket peer."""
    current = _checked(url)
    headers = {
        "User-Agent": USER_AGENT.format(mailto=mailto or "unknown"),
        "Accept": "application/pdf,*/*",
    }
    opener = _safe_opener()

    for _ in range(MAX_REDIRECTS):
        # `current` has been returned by `_checked` for the initial URL and for
        # every redirect. Keeping validation immediately beside the sink makes
        # the security invariant obvious to both reviewers and static analysis.
        current = _checked(current)
        request = urllib.request.Request(current, headers=headers)
        try:
            with opener.open(request, timeout=TIMEOUT) as response:
                location = response.headers.get("Location")
                if response.status in {301, 302, 303, 307, 308} and location:
                    current = _checked(urllib.parse.urljoin(current, location))
                    continue
                return _body(response)
        except urllib.error.HTTPError as exc:
            if exc.code in {301, 302, 303, 307, 308}:
                location = exc.headers.get("Location")
                if not location:
                    raise PaperFetchError("That address redirected to nowhere.") from exc
                current = _checked(urllib.parse.urljoin(current, location))
                continue
            raise PaperFetchError(
                f"That paper could not be downloaded ({exc.code}). It may be "
                "behind a paywall.") from exc
        except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
            raise PaperFetchError(
                f"That paper could not be downloaded ({exc}).") from exc

    raise PaperFetchError("That address redirects in a loop.")


def _body(response: object) -> bytes:
    read = getattr(response, "read")
    headers = getattr(response, "headers")

    declared = headers.get("Content-Length")
    if declared is not None:
        try:
            if int(declared) > MAX_BYTES:
                raise PaperFetchError(
                    f"That file is {int(declared) // (1024 * 1024)}MB, which is "
                    "larger than this will download.")
        except ValueError:
            pass

    data = read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise PaperFetchError("That file is larger than this will download.")
    if not data.startswith(b"%PDF-"):
        raise PaperFetchError(
            "That address did not return a PDF. It may be a landing page "
            "rather than the paper itself.")
    return data


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """Hands redirects back rather than following them (see `fetch_pdf`)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


def _connection_to_public_peers(base: type, *, host: str):
    """Build a connection class that validates the actual connected socket."""
    def make(connect_to: str, **kwargs):
        connection = base(connect_to, **kwargs)
        create = connection._create_connection

        def checked(address, *args, **kw):
            sock = create(address, *args, **kw)
            peer = sock.getpeername()[0].split("%", 1)[0]
            if not ipaddress.ip_address(peer).is_global:
                sock.close()
                raise PaperFetchError(
                    f"{host} is not a public address this can fetch from.")
            return sock

        connection._create_connection = checked
        return connection
    return make


class _PublicOnlyHTTP(urllib.request.HTTPHandler):
    def http_open(self, req):  # noqa: D102
        host = urllib.parse.urlsplit(req.full_url).hostname or ""
        return self.do_open(
            _connection_to_public_peers(http.client.HTTPConnection, host=host), req)


class _PublicOnlyHTTPS(urllib.request.HTTPSHandler):
    def https_open(self, req):  # noqa: D102
        host = urllib.parse.urlsplit(req.full_url).hostname or ""
        return self.do_open(
            _connection_to_public_peers(http.client.HTTPSConnection, host=host),
            req,
            context=self._context,
        )
