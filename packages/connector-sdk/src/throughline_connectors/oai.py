"""
OAI-PMH: one implementation, thousands of repositories.

Every other connector here is a single service with its own API. This is not a
service — it is a protocol that institutional repositories, thesis archives,
museum catalogues and national libraries all speak. Writing it once reaches all
of them, which is why it earns its place over adding a fifth or sixth bespoke
source: the long tail of research output lives in exactly these places and is
indexed nowhere else.

Four things about the protocol shape this file, and three of them are places
where a naive client is quietly wrong rather than broken.

**There is no search.** OAI-PMH lists; it does not query. The verbs are
`ListRecords` and `GetRecord`, filtered by date range and by set. A client that
offers a search box over it is either fetching the entire repository and
filtering locally — slow, rude, and wrong on anything large — or pretending. So
`search` refuses and names `harvest` instead. Refusing is the honest answer to
"can you search this", and it is a better one than a search that silently misses
most of the corpus.

**Deleted records still arrive.** A record withdrawn from a repository is not
omitted from the feed; it comes back with `status="deleted"` and no metadata at
all. A harvester that does not check the header ingests it as a paper with an
empty title, and the withdrawal — which is a real and sometimes important fact
about a work — is lost.

**Results are paged by an opaque token, and the token expires.** Following
`resumptionToken` is the only way to see past the first few hundred records.
There is a cap here anyway, because a harvest with no bound will happily pull a
million records into a laptop database.

**Dublin Core is lossy, and pretending otherwise invents facts.** `dc:date` may
be a year, a full date, an ISO timestamp or a sentence. `dc:type` may say
anything at all. Nothing in it distinguishes a preprint from a version of
record, so `peer_reviewed` stays None rather than being guessed — the three
states are genuinely different and collapsing None into False labels indexed
work as unreviewed.

One security note that is not protocol trivia. This parses XML from an endpoint
the researcher typed in, which is untrusted input. `xml.etree` does not resolve
external entities, so document exfiltration is not the risk; entity *expansion*
is, and a hostile or broken repository can serve a few kilobytes that expand
into gigabytes. A declaration is refused outright rather than parsed, and the
response is size-capped before it reaches the parser.
"""

from __future__ import annotations

import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Iterator

from .base import (Connector, ConnectorError, SourceRecord, clean_doi,
                   clean_text, year_of)
from .papers import _PublicOnlyHTTP, _PublicOnlyHTTPS, _is_public

OAI = "{http://www.openarchives.org/OAI/2.0/}"
DC = "{http://purl.org/dc/elements/1.1/}"

#: Above this a single response is refused unparsed. Generous for a page of
#: Dublin Core records, and far below what an expansion attack needs to hurt.
MAX_RESPONSE_BYTES = 32 * 1024 * 1024

#: A harvest without a ceiling will pull an entire national repository into a
#: laptop. The caller can raise it; it may not be absent.
DEFAULT_MAX_RECORDS = 1000


class OAIError(ConnectorError):
    """The repository answered, and said no."""

    def __init__(self, message: str, *, code: str = "") -> None:
        super().__init__(message)
        self.code = code


def _parse(raw: bytes) -> ET.Element:
    """
    Parse a response from an endpoint the researcher named.

    The DOCTYPE check is the whole guard. `xml.etree` will not fetch an external
    entity, so this is not about exfiltration — it is that a handful of nested
    internal entities expand to gigabytes and take the process with them. A
    legitimate OAI-PMH response has no use for a DOCTYPE at all, so refusing one
    costs nothing and removes the class entirely.
    """
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ConnectorError(
            f"The repository returned more than {MAX_RESPONSE_BYTES // (1024 * 1024)}MB "
            "in one response, which no page of records needs. Nothing was parsed.")

    class NoDocumentType(ET.TreeBuilder):
        def doctype(self, name, pubid, system):
            # Let the XML parser handle encodings and the entire prolog. A
            # byte-prefix scan misses declarations after comments or in UTF-16.
            raise ConnectorError(
                "That response carries a document type declaration. A valid "
                "OAI-PMH response has no use for one. Nothing was parsed.")

    try:
        return ET.fromstring(raw, parser=ET.XMLParser(target=NoDocumentType()))
    except ET.ParseError as exc:
        raise ConnectorError(
            f"The repository returned XML that could not be parsed ({exc}). "
            "It may not be an OAI-PMH endpoint.") from exc


def _text(node: ET.Element | None) -> str:
    return clean_text(node.text) if node is not None and node.text else ""


def _refuse_unless_public(url: str) -> None:
    host = urllib.parse.urlsplit(url).hostname or ""
    if not _is_public(host):
        # The paper fetcher's message, for its reason: one sentence for "private"
        # and "does not resolve", so this cannot be used to map internal hosts.
        raise ConnectorError(f"{host} is not a public address this can fetch from.")


class _PublicRedirects(urllib.request.HTTPRedirectHandler):
    """Follows redirects — repositories do redirect — re-checking every hop."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        _refuse_unless_public(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class OAIRepository(Connector):
    """
    One OAI-PMH endpoint.

    Deliberately not in the connector registry. The others are named services a
    researcher picks from a list; this is a protocol, and every instance needs
    the address of the repository it speaks to. Registering it under one name
    would imply a single source that does not exist.
    """

    name = "oai"
    #: Institutional repositories are usually small, often shared hosting, and
    #: frequently a single university's server. Conservative on purpose: being
    #: slower than necessary costs seconds, being faster than allowed costs
    #: access for everyone at that institution.
    rate_per_second = 1.0
    burst = 1

    def __init__(self, base_url: str, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if not base_url.lower().startswith(("http://", "https://")):
            raise ConnectorError(
                f"{base_url!r} is not an http(s) address. An OAI-PMH base URL "
                "is the endpoint itself, e.g. "
                "https://export.arxiv.org/oai2")
        self.base_url = base_url.rstrip("?&")

    def _open(self, request):
        """
        The base URL is whatever the caller typed, and it was fetched unchecked:
        only its scheme was looked at, and `urllib` followed redirects wherever
        they led, so a signed-in caller could harvest this machine's own
        services from the server's network position (T165). Checked here, at
        fetch time rather than in `__init__`, so building a repository does no
        network lookup — the name is resolved when it is used. The connection's
        actual peer is checked as well, for the reason `papers` gives.
        """
        _refuse_unless_public(request.full_url)
        opener = urllib.request.build_opener(_PublicRedirects, _PublicOnlyHTTP,
                                             _PublicOnlyHTTPS)
        return opener.open(request, timeout=self.timeout)

    # -- protocol ----------------------------------------------------------

    def _verb(self, verb: str, **params: str) -> ET.Element:
        query = {"verb": verb, **{k: v for k, v in params.items() if v}}
        root = _parse(self._get(
            f"{self.base_url}?{urllib.parse.urlencode(query)}",
            headers={"Accept": "text/xml"}))

        error = root.find(f"{OAI}error")
        if error is not None:
            code = error.get("code", "")
            # `noRecordsMatch` is an answer, not a failure: the date range or
            # set simply contains nothing. Callers distinguish on `code`.
            raise OAIError(
                f"{self.base_url} answered {code or 'an error'}: "
                f"{_text(error) or 'no detail given'}", code=code)
        return root

    def identify(self) -> dict[str, Any]:
        """
        Who this repository says it is.

        Worth calling before a harvest: it is the cheapest way to find out that
        a URL is not an OAI-PMH endpoint, and the name is what a researcher
        needs to see attributed on anything harvested from it.
        """
        root = self._verb("Identify")
        node = root.find(f"{OAI}Identify")
        if node is None:
            raise ConnectorError(
                f"{self.base_url} replied without an Identify block, so it is "
                "probably not an OAI-PMH endpoint.")
        return {
            "name": _text(node.find(f"{OAI}repositoryName")),
            "base_url": _text(node.find(f"{OAI}baseURL")) or self.base_url,
            "protocol": _text(node.find(f"{OAI}protocolVersion")),
            "admin_email": _text(node.find(f"{OAI}adminEmail")),
            "earliest": _text(node.find(f"{OAI}earliestDatestamp")),
            "deleted_record_policy": _text(node.find(f"{OAI}deletedRecord")),
        }

    def harvest(self, *, set_spec: str = "", since: str = "", until: str = "",
                prefix: str = "oai_dc", max_records: int = DEFAULT_MAX_RECORDS,
                max_pages: int = 1000
                ) -> dict[str, Any]:
        """
        List records, following pagination until the cap.

        Returns deletions separately rather than dropping them. A withdrawn
        record is a fact about a work — sometimes a retraction — and a harvester
        that silently omits it leaves a stale copy in the local corpus with
        nothing to say it should not be cited.
        """
        for name, value in (("max_records", max_records), ("max_pages", max_pages)):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ConnectorError(f"{name} must be a positive integer.")
        records: list[SourceRecord] = []
        deleted: list[str] = []
        token, pages, truncated = "", 0, False
        seen_tokens: set[str] = set()
        processed = 0

        while True:
            try:
                # Once a resumptionToken is in play it is the *only* legal
                # parameter — repeating the filters is a protocol error, and
                # several repositories enforce it strictly.
                root = (self._verb("ListRecords", resumptionToken=token) if token
                        else self._verb("ListRecords", metadataPrefix=prefix,
                                        set=set_spec, **{"from": since,
                                                         "until": until}))
            except OAIError as exc:
                if exc.code == "noRecordsMatch" and pages == 0:
                    return {"records": [], "deleted": [], "pages": 0,
                            "truncated": False,
                            "note": ("The repository has nothing matching that "
                                     "range or set. That is an answer, not a "
                                     "failure.")}
                raise

            pages += 1
            listing = root.find(f"{OAI}ListRecords")
            if listing is None:
                raise ConnectorError("The repository replied without a ListRecords block.")

            entries = listing.findall(f"{OAI}record")
            token_node = listing.find(f"{OAI}resumptionToken")
            next_token = token_node.text if token_node is not None and token_node.text else ""
            for index, record in enumerate(entries):
                processed += 1
                header = record.find(f"{OAI}header")
                identifier = _text(header.find(f"{OAI}identifier")) if header is not None else ""

                if header is not None and header.get("status") == "deleted":
                    deleted.append(identifier)
                else:
                    normalised = self._record(record, identifier)
                    if normalised:
                        records.append(normalised)
                if processed >= max_records:
                    truncated = index + 1 < len(entries) or bool(next_token)
                    break

            if processed >= max_records:
                break

            token = next_token
            if not token:
                break
            if token in seen_tokens:
                raise ConnectorError("The repository repeated a pagination token; harvest stopped.")
            seen_tokens.add(token)
            if pages >= max_pages:
                truncated = True
                break

        return {
            "records": records,
            "deleted": deleted,
            "pages": pages,
            "truncated": truncated,
            "note": self._note(len(records), len(deleted), truncated, max_records),
        }

    def _note(self, kept: int, deleted: int, truncated: bool, cap: int) -> str:
        parts = [f"{kept} record{'' if kept == 1 else 's'} harvested."]
        if deleted:
            parts.append(
                f"{deleted} arrived marked deleted and were not ingested. A "
                "withdrawal is a fact about the work — if any of these are "
                "already in this project, they should not be cited as current.")
        if truncated:
            parts.append(
                "Stopped at a harvest safety limit before completion. Deleted "
                "and unparseable records count toward the record limit too. "
                "Narrow the date range or raise the limit deliberately.")
        return " ".join(parts)

    # -- normalisation -----------------------------------------------------

    def _record(self, record: ET.Element, identifier: str) -> SourceRecord | None:
        metadata = record.find(f"{OAI}metadata")
        if metadata is None:
            return None
        dc = next(iter(metadata), None)
        if dc is None:
            return None

        title = clean_text(_first(dc, "title"))
        if not title:
            # Dublin Core makes almost everything optional. A record with no
            # title cannot be shown, cited or deduplicated, so it is dropped
            # rather than stored as an untitled row.
            return None

        identifiers = [_text(node) for node in dc.findall(f"{DC}identifier")]
        doi = next((clean_doi(value) for value in identifiers
                    if value and "10." in value and clean_doi(value)), None)
        url = next((value for value in identifiers
                    if value.lower().startswith(("http://", "https://"))), "")

        return SourceRecord(
            title=title,
            authors=[clean_text(_text(node)) for node in dc.findall(f"{DC}creator")][:40],
            # dc:date is famously anything at all. `year_of` takes the leading
            # four digits and returns None when they are not a plausible year,
            # which is the honest outcome for "Spring, 1998" and for "n.d.".
            year=year_of((_first(dc, "date") or "")[:4]),
            doi=doi,
            abstract=clean_text(_first(dc, "description")),
            venue=clean_text(_first(dc, "publisher")),
            url=url or (f"https://doi.org/{doi}" if doi else ""),
            source=f"{self.name}:{urllib.parse.urlparse(self.base_url).netloc}",
            # Dublin Core carries no notion of peer review. `dc:type` may say
            # "article" for a preprint, a thesis chapter or a version of record
            # alike, so this stays unknown rather than guessed.
            peer_reviewed=None,
            oai_identifier=identifier or None,
            provenance={field: self.name for field in
                        ("title", "authors", "year", "abstract", "venue")},
        )

    # -- interface ---------------------------------------------------------

    def search(self, query: str, *, limit: int = 20) -> list[SourceRecord]:
        raise ConnectorError(
            "OAI-PMH has no search. The protocol lists records by date range "
            "and set; it cannot be queried by keyword. Use harvest(since=..., "
            "set_spec=...) and search the results once they are indexed here. "
            "A search box over this would have to download the whole "
            "repository to answer, and would still miss anything it skipped.")

    def capability(self) -> dict[str, Any]:
        base = super().capability()
        base["ready"] = True
        base["searchable"] = False
        base["harvests"] = True
        base["note"] = (
            "A protocol, not a service: one implementation reaching any "
            "repository that speaks OAI-PMH. It cannot be searched by keyword "
            "— records are listed by date range and set, then indexed here. "
            "Dublin Core does not say whether a work was peer reviewed, so "
            "nothing harvested claims to have been.")
        return base


def _first(dc: ET.Element, field: str) -> str:
    node = dc.find(f"{DC}{field}")
    return _text(node)


__all__ = ["DEFAULT_MAX_RECORDS", "MAX_RESPONSE_BYTES", "OAIError",
           "OAIRepository"]
