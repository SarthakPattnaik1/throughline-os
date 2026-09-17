"""
The OAI-PMH harvester.

This reaches thousands of repositories through one implementation, which is the
argument for building it — and also the reason to be careful, because a bug here
is a bug against every institutional repository at once.

Three of the protocol's behaviours make a naive client quietly wrong rather than
broken, and those are what most of these tests are about: deleted records still
arrive in the feed, results are paged behind an opaque token, and Dublin Core
carries no notion of peer review. Each of those failures produces a corpus that
looks fine and is not.

The fourth thing under test is the refusal. OAI-PMH cannot be searched, and a
search box over it would have to download the whole repository to answer while
still missing whatever it skipped.

Nothing here touches the network — the transport is replaced with recorded XML.
"""

from __future__ import annotations

import pytest
from throughline_connectors.base import ConnectorError
from throughline_connectors.oai import MAX_RESPONSE_BYTES, OAIError, OAIRepository

HEAD = ('<?xml version="1.0" encoding="UTF-8"?>'
        '<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">')
TAIL = "</OAI-PMH>"


def record(title="A paper", identifier="oai:repo:1", creator="Ada Lovelace",
           date="1998", extra="") -> str:
    return (
        f"<record><header><identifier>{identifier}</identifier></header>"
        '<metadata><oai_dc xmlns="http://www.openarchives.org/OAI/2.0/oai_dc/"'
        ' xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f"<dc:title>{title}</dc:title>"
        f"<dc:creator>{creator}</dc:creator>"
        f"<dc:date>{date}</dc:date>{extra}"
        "</oai_dc></metadata></record>")


def deleted(identifier="oai:repo:gone") -> str:
    return (f'<record><header status="deleted">'
            f"<identifier>{identifier}</identifier></header></record>")


def listing(body: str, token: str = "") -> bytes:
    token_xml = f"<resumptionToken>{token}</resumptionToken>" if token else ""
    return f"{HEAD}<ListRecords>{body}{token_xml}</ListRecords>{TAIL}".encode()


class FakeRepo(OAIRepository):
    """An OAI endpoint whose responses are scripted."""

    def __init__(self, responses: list[bytes], **kwargs) -> None:
        super().__init__("https://repo.example.org/oai2", **kwargs)
        self.responses = list(responses)
        self.requested: list[str] = []

    def _get(self, url, *, headers=None, attempts=3) -> bytes:
        self.requested.append(url)
        return self.responses.pop(0)


# ---------------------------------------------------------------------------
# Deleted records
# ---------------------------------------------------------------------------

def test_a_deleted_record_is_not_ingested_as_a_paper():
    """
    Withdrawn records arrive in the feed with no metadata at all. A harvester
    that ignores the header stores a paper with an empty title.
    """
    repo = FakeRepo([listing(record() + deleted())])
    result = repo.harvest()

    assert len(result["records"]) == 1
    assert result["deleted"] == ["oai:repo:gone"]


def test_a_withdrawal_is_reported_rather_than_dropped():
    """
    The withdrawal is itself a fact about the work — sometimes a retraction.
    Silently omitting it leaves a stale copy locally with nothing saying it
    should not be cited.
    """
    repo = FakeRepo([listing(deleted())])
    result = repo.harvest()
    assert "should not be cited as current" in result["note"]


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

def test_pagination_is_followed_to_the_end():
    repo = FakeRepo([listing(record(identifier="oai:repo:1"), token="tok1"),
                     listing(record(identifier="oai:repo:2"))])
    result = repo.harvest()

    assert len(result["records"]) == 2
    assert result["pages"] == 2


def test_the_resumption_token_travels_alone():
    """
    Once a token is in play it is the only legal parameter. Repeating the
    original filters alongside it is a protocol error, and several repositories
    enforce it strictly — so the second request must not carry them.
    """
    repo = FakeRepo([listing(record(), token="tok1"), listing(record())])
    repo.harvest(since="2024-01-01", set_spec="theses")

    second = repo.requested[1]
    assert "resumptionToken=tok1" in second
    assert "from=" not in second and "set=" not in second


def test_a_harvest_stops_at_its_ceiling():
    """Without a cap this pulls an entire national repository onto a laptop."""
    repo = FakeRepo([listing("".join(record(identifier=f"oai:{i}")
                                     for i in range(10)), token="more")])
    result = repo.harvest(max_records=3)

    assert len(result["records"]) == 3
    assert result["truncated"] is True
    assert "harvest safety limit" in result["note"]


# ---------------------------------------------------------------------------
# Dublin Core is lossy
# ---------------------------------------------------------------------------

def test_nothing_harvested_claims_to_be_peer_reviewed():
    """
    Dublin Core has no field for it. Guessing False would label indexed work as
    unreviewed; guessing True would be worse.
    """
    repo = FakeRepo([listing(record())])
    assert repo.harvest()["records"][0].peer_reviewed is None


def test_an_unparseable_date_becomes_no_year_rather_than_a_wrong_one():
    repo = FakeRepo([listing(record(date="Spring, n.d."))])
    assert repo.harvest()["records"][0].year is None


def test_a_doi_is_recovered_from_among_several_identifiers():
    extra = ("<dc:identifier>https://repo.example.org/handle/1</dc:identifier>"
             "<dc:identifier>https://doi.org/10.1234/abc</dc:identifier>")
    repo = FakeRepo([listing(record(extra=extra))])
    assert repo.harvest()["records"][0].doi == "10.1234/abc"


def test_a_record_with_no_title_is_dropped_not_stored_untitled():
    """Dublin Core makes almost everything optional, including the title."""
    untitled = record(title="").replace("<dc:title></dc:title>", "")
    repo = FakeRepo([listing(untitled + record())])
    assert len(repo.harvest()["records"]) == 1


def test_the_repository_identifier_travels_with_the_record():
    """The stable key a re-harvest matches on; without it every run re-adds."""
    repo = FakeRepo([listing(record(identifier="oai:repo:42"))])
    assert repo.harvest()["records"][0].oai_identifier == "oai:repo:42"


# ---------------------------------------------------------------------------
# Answers that are not failures, and failures that are
# ---------------------------------------------------------------------------

def test_an_empty_range_is_an_answer_not_an_error():
    repo = FakeRepo([f'{HEAD}<error code="noRecordsMatch">nothing</error>{TAIL}'.encode()])
    result = repo.harvest(since="2099-01-01")

    assert result["records"] == []
    assert "not a failure" in result["note"]


def test_a_real_protocol_error_is_raised_with_its_code():
    repo = FakeRepo([f'{HEAD}<error code="badArgument">bad from</error>{TAIL}'.encode()])
    with pytest.raises(OAIError) as caught:
        repo.harvest(since="not-a-date")
    assert caught.value.code == "badArgument"


def test_a_non_oai_endpoint_is_named_as_such():
    repo = FakeRepo([b"<html><body>Not an API</body></html>"])
    with pytest.raises(ConnectorError, match="not an OAI-PMH endpoint"):
        repo.identify()


# ---------------------------------------------------------------------------
# Untrusted XML
# ---------------------------------------------------------------------------

def test_a_document_type_declaration_is_refused_unparsed():
    """
    The researcher types this URL, so the XML is untrusted. `xml.etree` will not
    fetch an external entity, but a handful of nested internal ones expand to
    gigabytes — and a legitimate OAI-PMH response has no use for a DOCTYPE, so
    refusing costs nothing.
    """
    bomb = (b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
            b'<!ENTITY lol2 "&lol;&lol;&lol;">]><OAI-PMH>&lol2;</OAI-PMH>')
    repo = FakeRepo([bomb])
    with pytest.raises(ConnectorError, match="document type declaration"):
        repo.harvest()


def test_an_oversized_response_is_refused_before_parsing():
    repo = FakeRepo([b"<OAI-PMH>" + b"x" * (MAX_RESPONSE_BYTES + 1)])
    with pytest.raises(ConnectorError, match="Nothing was parsed"):
        repo.harvest()


# ---------------------------------------------------------------------------
# The refusal
# ---------------------------------------------------------------------------

def test_search_refuses_and_says_what_to_use_instead():
    repo = FakeRepo([])
    with pytest.raises(ConnectorError, match="harvest"):
        repo.search("antimicrobial resistance")


def test_capability_does_not_advertise_a_search_it_cannot_do():
    repo = FakeRepo([])
    capability = repo.capability()
    assert capability["searchable"] is False
    assert capability["harvests"] is True


def test_a_base_url_that_is_not_http_is_refused_at_construction():
    with pytest.raises(ConnectorError, match="http"):
        OAIRepository("repo.example.org/oai2")


# ---------------------------------------------------------------------------
# A harvest address that points inside this machine (T165)
# ---------------------------------------------------------------------------

class TestAHarvestCannotReachThisMachine:
    """
    `POST /projects/{id}/harvest` takes `base_url` from the request, and the
    connector checked only that it began with http(s) — no private-address
    guard at all, and `urllib` following redirects wherever they led. A signed-in
    caller could harvest `http://127.0.0.1:8080/...` from the server's own
    network position. These run a real server on loopback and count what
    reaches it: the refusal has to come before the request, not after.
    """

    @staticmethod
    def _loopback_repository():
        import http.server
        import threading

        hits: list[str] = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                hits.append(self.path)
                body = (b'<?xml version="1.0"?><OAI-PMH xmlns="http://www.openarchives.org/'
                        b'OAI/2.0/"><Identify><repositoryName>inside</repositoryName>'
                        b'<baseURL>http://127.0.0.1/oai</baseURL></Identify></OAI-PMH>')
                self.send_response(200)
                self.send_header("Content-Type", "text/xml")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server, hits

    def test_a_loopback_base_url_is_refused_before_anything_is_sent(self):
        server, hits = self._loopback_repository()
        try:
            repo = OAIRepository(f"http://127.0.0.1:{server.server_port}/oai")
            with pytest.raises(ConnectorError, match="not a public address"):
                repo.identify()
        finally:
            server.shutdown()
        assert hits == [], f"the harvest reached this machine: {hits}"

    def test_an_internal_address_is_refused_without_opening_a_connection(self, monkeypatch):
        """
        The name is checked before connecting, not only the peer after. A check
        made only once connected would open a TCP connection to the internal
        host first — and "refused" for a closed port against "not a public
        address" for an open one would make this endpoint a port scanner for the
        machine it runs on.
        """
        import socket

        def no_connections(*args, **kwargs):
            raise AssertionError("a connection was opened to an internal address")

        monkeypatch.setattr(socket, "create_connection", no_connections)
        repo = OAIRepository("http://127.0.0.1:9/oai")
        with pytest.raises(ConnectorError, match="not a public address"):
            repo.identify()

    def test_a_redirect_into_this_machine_is_refused(self):
        """
        Redirects are still followed — repositories do redirect — but each new
        address is checked before the hop is taken. `urllib`'s own handler
        followed them unchecked.
        """
        from throughline_connectors import oai

        with pytest.raises(ConnectorError, match="not a public address"):
            oai._PublicRedirects().redirect_request(
                None, None, 302, "Found", {}, "http://127.0.0.1:8080/api/projects")

    def test_building_a_repository_does_no_network_lookup(self, monkeypatch):
        """The name is resolved when it is used, so scripted repositories stay offline."""
        import socket

        def no_lookups(*args, **kwargs):
            raise AssertionError("OAIRepository() resolved a name")

        monkeypatch.setattr(socket, "getaddrinfo", no_lookups)
        OAIRepository("https://repo.example.org/oai2")
