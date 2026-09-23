"""Phase 1 ingestion (§24, §26, §27) against real files."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from throughline_domain import corpus, objects, storage, workflow
from throughline_domain.db import connection
from throughline_domain.ids import new_id
from throughline_ingestion import datasets as dataset_parser
from throughline_ingestion import documents as document_parser
from throughline_schemas.enums import IngestionStatus, SourceType
from throughline_workers.runner import Worker

# The three periodontal-disease PDFs from the previous product, used as real
# input rather than a synthetic fixture.


@pytest.fixture()
def committed_project():
    user_id, project_id = new_id("usr"), new_id("prj")
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users(id, email, display_name, password_hash, password_salt) "
            "VALUES (%s, %s, %s, 'x', 'y')",
            (user_id, f"{user_id}@test.local", "Ingest Test"),
        )
        cur.execute("INSERT INTO projects(id, owner_user_id, name) VALUES (%s, %s, 'Ingest')",
                    (project_id, user_id))
    yield project_id
    with connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM users WHERE id = %s", (user_id,))


def _upload(project_id: str, filename: str, payload: bytes) -> str:
    """Store a file and queue ingestion the way the API does."""
    with connection() as conn, conn.cursor() as cur:
        record = storage.register_file(cur, project_id=project_id, filename=filename,
                                       stream=io.BytesIO(payload),
                                       media_type="application/octet-stream")
        source_id = objects.create_source(
            cur, project_id=project_id, source_type=SourceType.UPLOAD,
            title=filename, actor="test", file_id=str(record["id"]),
            content_hash=str(record["content_hash"]),
        )
        # Keyed per source, mirroring the API. Keying on the content hash meant a
        # second source with identical bytes was handed the first one's finished
        # run and then never advanced past 'uploaded'.
        workflow.enqueue(cur, workflow_name="ingest.source", project_id=project_id,
                         payload={"source_id": source_id},
                         idempotency_key=f"ingest:{source_id}")
    return source_id


def _drain() -> None:
    while Worker(worker_id="test").run_once():
        pass


def _source(source_id: str) -> dict:
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM sources WHERE id = %s", (source_id,))
        return cur.fetchone()


# ---------------------------------------------------------------------------
# Pure parsing
# ---------------------------------------------------------------------------


def test_unsupported_format_is_refused_not_silently_empty():
    """§25 — do not show unsupported formats as functional."""
    with pytest.raises(document_parser.UnsupportedFormat) as exc:
        document_parser.parse_document(Path("/tmp/whatever.sav"))
    assert ".sav" in str(exc.value)


def test_plain_text_anchors_resolve_exactly():
    """LAW 1 — offsets must index the real text, verified at parse time."""
    path = Path("/tmp/tl-anchor-test.txt")
    path.write_text("Methods\n\nWe enrolled 240 patients.\n\nResults\n\nMortality was 12.5%.")
    parsed = document_parser.parse_document(path)
    assert parsed.verify_anchors() == []
    for passage in parsed.passages:
        assert parsed.text[passage.char_start : passage.char_end] == passage.content
    assert {p.section for p in parsed.passages} >= {"methods", "results"}


def test_real_pdf_parses_with_resolvable_anchors(paper_pdf):
    parsed = document_parser.parse_pdf(paper_pdf)
    assert parsed.passages and parsed.page_count > 0
    assert parsed.verify_anchors() == []
    # The column-aware reader must not splice unrelated sentences together.
    joined = " ".join(p.content for p in parsed.passages[:40])
    assert "kindout of" not in joined


# ---------------------------------------------------------------------------
# Dataset profiling
# ---------------------------------------------------------------------------


def test_dataset_profiling_types_columns_from_values_not_names(tmp_path):
    """§26 — a name raises a hypothesis; the values decide."""
    csv_path = tmp_path / "amr.csv"
    csv_path.write_text(
        "country,year,consumption_ddd,resistance_pct,patient_name,age\n"
        "IND,2019,12.4,31.2,Asha,54\n"
        "USA,2019,9.8,18.6,Bob,61\n"
        "GBR,2019,8.1,15.0,Cara,47\n"
        "FRA,2020,11.2,22.4,Dan,39\n"
    )
    profile = dataset_parser.profile_dataset(csv_path)
    by_name = {c.name: c for c in profile.columns}

    assert profile.row_count == 4 and profile.column_count == 6
    assert by_name["country"].semantic_type == "geography"
    # A bare year is stored as a number but means a date — typing it "continuous"
    # would let it be correlated against outcomes as if it were a measurement.
    assert by_name["year"].physical_type == "number"
    assert by_name["year"].semantic_type == "date"
    assert by_name["consumption_ddd"].semantic_type == "continuous"
    assert by_name["age"].semantic_type == "age"
    # §26 — personal fields are flagged, never dropped.
    assert by_name["patient_name"].sensitivity == "possibly_personal"
    assert "patient_name" in profile.quality_report["possibly_personal_columns"]


def test_profiling_flags_sentinels_and_missingness(tmp_path):
    csv_path = tmp_path / "d.csv"
    csv_path.write_text("value,note\n10\n-999,\n20,\nNA,\n")
    profile = dataset_parser.profile_dataset(csv_path)
    value = next(c for c in profile.columns if c.name == "value")
    assert value.missing_count >= 1
    assert -999 in value.statistics.get("possible_sentinel_values", [])


def test_profiling_never_mutates_the_source(tmp_path):
    """§26 — "Never mutate raw data.\""""
    csv_path = tmp_path / "raw.csv"
    original = "a,b\n1,NA\n2,3\n"
    csv_path.write_text(original)
    dataset_parser.profile_dataset(csv_path)
    assert csv_path.read_text() == original


def test_delimiter_is_detected(tmp_path):
    path = tmp_path / "semi.csv"
    path.write_text("a;b;c\n1;2;3\n4;5;6\n")
    profile = dataset_parser.profile_dataset(path)
    assert profile.column_count == 3


# ---------------------------------------------------------------------------
# End-to-end ingestion through the durable worker
# ---------------------------------------------------------------------------


def test_dataset_ingests_to_ready_with_a_version(committed_project):
    source_id = _upload(committed_project, "amr.csv",
                        b"country,year,rate\nIND,2019,31.2\nUSA,2019,18.6\nGBR,2020,15.0\n")
    _drain()

    source = _source(source_id)
    assert source["ingestion_status"] == str(IngestionStatus.READY)

    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT dv.version, dv.row_count, dv.column_count FROM dataset_versions dv "
            "JOIN datasets d ON d.id = dv.dataset_id WHERE d.source_id = %s",
            (source_id,),
        )
        version = cur.fetchone()
        assert version["version"] == 1 and version["row_count"] == 3


def test_unsupported_upload_fails_with_a_useful_reason(committed_project):
    """§104 — never a generic error.

    Previously used a `.sav` file, which was then unreadable. SPSS is now read,
    so the case moved to a format nothing here claims: the point of the test is
    the quality of the refusal, not the particular extension.
    """
    source_id = _upload(committed_project, "notes.rtf", b"{\\rtf1 not a dataset}")
    _drain()
    source = _source(source_id)
    assert source["ingestion_status"] == str(IngestionStatus.FAILED)
    detail = source["ingestion_detail"]
    assert ".rtf" in detail and "Supported" in detail
    # The refusal names something that would work, rather than only what did not.
    assert ".csv" in detail


def test_ingestion_refuses_bytes_that_no_longer_match_the_recorded_hash(
        committed_project):
    source_id = _upload(
        committed_project,
        "amr.csv",
        b"country,year,rate\nIND,2019,31.2\nUSA,2019,18.6\n",
    )

    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT f.storage_key FROM sources s "
            "JOIN files f ON f.id = s.file_id WHERE s.id = %s",
            (source_id,),
        )
        key = cur.fetchone()["storage_key"]

    # Simulate disk corruption after registration but before the worker reads it.
    storage.path_for(key).write_bytes(b"country,year,rate\nIND,2019,999.0\n")

    _drain()
    source = _source(source_id)
    assert source["ingestion_status"] == str(IngestionStatus.FAILED)
    assert "SHA-256" in source["ingestion_detail"]
    assert "altered or corrupted" in source["ingestion_detail"]


def test_a_corrupt_file_in_a_supported_format_says_so(committed_project):
    """A readable format and a readable file are different claims.

    `.sav` is supported now, so a broken one must fail as a broken file — not as
    an unsupported format, which would send a researcher off to convert a file
    that was already the right kind.
    """
    source_id = _upload(committed_project, "survey.sav", b"\x00not really spss")
    _drain()
    source = _source(source_id)
    assert source["ingestion_status"] == str(IngestionStatus.FAILED)
    detail = source["ingestion_detail"]
    assert ".sav" in detail
    assert "could not be read" in detail
    # Nothing suggesting the format itself is the problem.
    assert "Supported" not in detail


def test_failed_ingestion_preserves_the_stages_it_completed(committed_project):
    """§24 — "Failure state must preserve completed work where possible.\""""
    source_id = _upload(committed_project, "empty.txt", b"   \n  \n ")
    _drain()
    source = _source(source_id)
    assert source["ingestion_status"] == str(IngestionStatus.FAILED)
    # The stored file survives, so a retry does not need a re-upload.
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT f.storage_key FROM sources s JOIN files f ON f.id = s.file_id "
                    "WHERE s.id = %s", (source_id,))
        assert storage.path_for(cur.fetchone()["storage_key"]).exists()


def test_no_source_is_left_without_a_run_to_finish_it(committed_project):
    """
    Every source row must reach a terminal ingestion state.

    The failure this covers: two sources carrying identical bytes shared one
    idempotency key, so the second was handed the first one's already-completed
    run. Nothing was left to advance it, and it stayed at 'uploaded' permanently
    while the interface reported it as waiting for a worker.

    Written against the queue rather than the HTTP endpoint on purpose. The API
    now refuses to create the second source at all, but the invariant being
    protected is the queue's — a source without a live run of its own is stranded
    no matter which caller created it.
    """
    payload = b"country,year,rate\nIND,2019,31.2\nUSA,2019,18.6\nGBR,2020,15.0\n"
    first = _upload(committed_project, "one.csv", payload)
    second = _upload(committed_project, "two.csv", payload)
    assert first != second
    _drain()

    for source_id in (first, second):
        status = _source(source_id)["ingestion_status"]
        assert status != str(IngestionStatus.UPLOADED), (
            f"{source_id} never left 'uploaded' — it has no run to finish it")
        assert status == str(IngestionStatus.READY), _source(source_id)


def test_real_pdf_ingests_end_to_end_with_lineage(committed_project, paper_pdf):
    source_id = _upload(committed_project, paper_pdf.name, paper_pdf.read_bytes())
    _drain()

    source = _source(source_id)
    assert source["ingestion_status"] == str(IngestionStatus.READY), source["ingestion_detail"]

    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) n FROM passages WHERE source_id = %s", (source_id,))
        assert cur.fetchone()["n"] > 0

        # LAW 1 — the paper object traces back to the raw source object.
        from throughline_domain import lineage

        cur.execute("SELECT object_id FROM papers WHERE source_id = %s", (source_id,))
        paper_object = cur.fetchone()["object_id"]
        assert lineage.ancestors(cur, paper_object)


# ---------------------------------------------------------------------------
# Titles
# ---------------------------------------------------------------------------

def test_a_paper_is_never_titled_with_its_content_hash(cur, project):
    """
    Regression, found by opening a node in the graph and trying to read it.

    Uploads are stored under their content hash, and the plain-text and DOCX
    parsers fall back to the storage filename when a document declares no title.
    Every paper in the knowledge graph was therefore named `b9590d6e361c…` —
    technically a string, useless to a reader, and invisible until someone
    looked at a node rather than a list.
    """
    from types import SimpleNamespace

    from throughline_domain import corpus
    from throughline_domain.ids import new_id

    source_id = new_id("src")
    cur.execute(
        "INSERT INTO sources(id, project_id, source_type, title, ingestion_status) "
        "VALUES (%s, %s, 'upload', 'consumption_resistance.md', 'ready')",
        (source_id, project))

    parsed = SimpleNamespace(
        title="b9590d6e361c2d39db01571f09034abe2cbe815fee35b4196e242bb34f20949f",
        page_count=1, metadata={"parser": "plain-text"})
    stored = corpus.store_paper(cur, project_id=project, source_id=source_id,
                                parsed=parsed, actor="usr_1")

    cur.execute("SELECT title FROM research_objects WHERE id = %s",
                (stored["object_id"],))
    assert cur.fetchone()["title"] == "consumption_resistance.md"


def test_a_real_parsed_title_is_preferred_over_the_filename(cur, project):
    from types import SimpleNamespace

    from throughline_domain import corpus
    from throughline_domain.ids import new_id

    source_id = new_id("src")
    cur.execute(
        "INSERT INTO sources(id, project_id, source_type, title, ingestion_status) "
        "VALUES (%s, %s, 'upload', 'download (3).pdf', 'ready')",
        (source_id, project))

    parsed = SimpleNamespace(
        title="Antibiotic consumption and resistance in European hospitals",
        page_count=8, metadata={"parser": "pymupdf"})
    stored = corpus.store_paper(cur, project_id=project, source_id=source_id,
                                parsed=parsed, actor="usr_1")

    cur.execute("SELECT title FROM research_objects WHERE id = %s",
                (stored["object_id"],))
    assert cur.fetchone()["title"].startswith("Antibiotic consumption")


def _retry_now(source_id: str) -> None:
    """The backoff is the only thing between a failure and its retry."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute("UPDATE workflow_runs SET run_after = now() "
                    "WHERE input->>'source_id' = %s", (source_id,))


def _ingest_run(source_id: str) -> dict:
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT state, attempts, error FROM workflow_runs "
                    "WHERE input->>'source_id' = %s", (source_id,))
        return cur.fetchone()


def test_a_transient_ingestion_failure_is_retried_to_ready(committed_project, monkeypatch):
    calls = {"n": 0}
    real = dataset_parser.profile_dataset

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("the disk blinked")
        return real(*args, **kwargs)

    monkeypatch.setattr(dataset_parser, "profile_dataset", flaky)
    source_id = _upload(committed_project, "amr.csv",
                        b"country,year,rate\nIND,2019,31.2\nUSA,2019,18.6\nGBR,2020,15.0\n")

    Worker(worker_id="test").run_once()
    _retry_now(source_id)
    _drain()

    assert calls["n"] == 2, "the retry never reached the parser"
    assert _source(source_id)["ingestion_status"] == str(IngestionStatus.READY)


def test_a_source_whose_ingestion_keeps_failing_says_it_failed(committed_project, monkeypatch):
    """
    The run gave up after three attempts and said why; the source kept reading
    `uploaded`, with no reason, for ever. Its fallback wrote the failure on a
    second connection while the job's transaction held that row's lock, so the
    write timed out every time — the warning was logged on each attempt and the
    status a researcher reads was never set (T163).
    """
    def broken(*args, **kwargs):
        raise RuntimeError("the disk blinked")

    monkeypatch.setattr(dataset_parser, "profile_dataset", broken)
    source_id = _upload(committed_project, "amr.csv",
                        b"country,year,rate\nIND,2019,31.2\nUSA,2019,18.6\n")

    for _ in range(6):
        Worker(worker_id="test").run_once()
        _retry_now(source_id)

    run, source = _ingest_run(source_id), _source(source_id)
    assert run["state"] == "failed"
    assert source["ingestion_status"] == str(IngestionStatus.FAILED), source["ingestion_status"]
    assert "the disk blinked" in (source["ingestion_detail"] or "")
