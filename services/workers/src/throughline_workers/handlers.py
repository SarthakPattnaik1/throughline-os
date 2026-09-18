"""Workflow handlers.

Per  nothing here is stubbed to look implemented. A handler either does the
work or is absent.
"""

from __future__ import annotations

import logging

from pathlib import Path
from typing import Any

from throughline_domain import corpus, objects, storage, trust, workflow
from throughline_ingestion import datasets as dataset_parser
from throughline_ingestion import documents as document_parser
from throughline_schemas.enums import IngestionStatus

log = logging.getLogger("throughline.workers")

from .runner import REGISTRY

SUPPORTED_SUFFIXES = (
    document_parser.SUPPORTED_DOCUMENT_SUFFIXES | dataset_parser.SUPPORTED_DATASET_SUFFIXES
)


class PermanentIngestionError(Exception):
    """A failure that retrying cannot fix — an unsupported format, an empty file.

    Distinguished from transient errors so the worker does not burn its retry
    budget re-attempting something that will fail identically every time.
    """


@REGISTRY.register("system.echo")
def echo(run: dict[str, Any], cur: Any) -> dict[str, Any]:
    """Return the payload. Used by tests and the dev script's smoke check."""
    return {"echo": run["input"]}


@REGISTRY.register("ingest.source")
def ingest_source(run: dict[str, Any], cur: Any) -> dict[str, Any]:
    """Walk a source through the  ingestion state machine.

    Each stage records its progress, so a failure partway through shows the
    researcher exactly how far it got and leaves the stored file in
    place for a retry.
    """
    source_id = run["input"]["source_id"]
    cur.execute(
        """
        SELECT s.id, s.project_id, s.title, s.content_hash,
               f.storage_key, f.filename, f.media_type
        FROM sources s LEFT JOIN files f ON f.id = s.file_id
        WHERE s.id = %s
        """,
        (source_id,),
    )
    source = cur.fetchone()
    if not source:
        raise ValueError(f"Source {source_id} no longer exists")

    project_id = source["project_id"]
    actor = "system:ingest"
    filename = source["filename"] or source["title"] or ""
    # Files are stored content-addressed, so the path on disk has no extension.
    # The format comes from the filename recorded at upload.
    suffix = Path(filename).suffix.lower()

    try:
        if not source["storage_key"]:
            raise PermanentIngestionError("No stored file is attached to this source.")
        if suffix not in SUPPORTED_SUFFIXES:
            raise PermanentIngestionError(
                f"{suffix or 'This file type'} is not supported. "
                f"Supported: {', '.join(sorted(SUPPORTED_SUFFIXES))}"
            )

        path = storage.path_for(source["storage_key"])
        objects.advance_ingestion(cur, source_id=source_id,
                                  to_status=IngestionStatus.VALIDATED,
                                  detail=f"Stored file located ({suffix})")
        #  lists malware scanning as architecture. It is not implemented here,
        # so this stage records that no scan ran rather than implying one did.
        objects.advance_ingestion(cur, source_id=source_id,
                                  to_status=IngestionStatus.SCANNED,
                                  detail="Local-first install: no malware scanner configured")
        objects.advance_ingestion(cur, source_id=source_id,
                                  to_status=IngestionStatus.EXTRACTING,
                                  detail="Reading file")

        if suffix in document_parser.SUPPORTED_DOCUMENT_SUFFIXES:
            result = _ingest_document(cur, project_id=project_id, source_id=source_id,
                                      path=path, suffix=suffix, actor=actor)
        else:
            result = _ingest_dataset(cur, project_id=project_id, source_id=source_id,
                                     path=path, suffix=suffix, name=filename,
                                     content_hash=source["content_hash"] or "",
                                     storage_key=source["storage_key"], actor=actor)

        objects.advance_ingestion(cur, source_id=source_id,
                                  to_status=IngestionStatus.READY, detail=result["summary"])
        return {"source_id": source_id, "status": "ready", **result}

    except PermanentIngestionError as exc:
        # Retrying cannot help. Record the failure in this transaction and return
        # normally so the run completes rather than looping through its retries.
        objects.advance_ingestion(cur, source_id=source_id,
                                  to_status=IngestionStatus.FAILED, detail=str(exc))
        return {"source_id": source_id, "status": "failed", "reason": str(exc)}

    # Anything else re-raises and is retried. There is no failure record here:
    # this used to write one on a second connection, which could never succeed —
    # by then this transaction holds the source row's lock, so the write waited
    # out its two-second lock timeout on every attempt and was logged and lost.
    # The source then read `uploaded`, with no reason, for ever after the run
    # had failed (T163). Between attempts it still reads as in progress, which
    # is true; once the run gives up, `_source_gave_up` records why.


@workflow.on_give_up("ingest.source")
def _source_gave_up(cur: Any, payload: dict[str, Any], error: str) -> None:
    """The run is out of attempts: the source says it failed, and why."""
    objects.advance_ingestion(cur, source_id=payload["source_id"],
                              to_status=IngestionStatus.FAILED,
                              detail=error or "Ingestion stopped without finishing.")


@workflow.on_give_up("analysis.run")
def _analysis_gave_up(cur: Any, payload: dict[str, Any], error: str) -> None:
    """
    The run set this row to `running` inside its own transaction, so its failure
    rolled it back to `queued`. Only a row that has not reached an outcome is
    touched: a completed analysis is not rewritten by a later failure.
    """
    cur.execute(
        "UPDATE analysis_runs SET status = 'failed', error = %s, "
        "finished_at = COALESCE(finished_at, now()) "
        "WHERE id = %s AND status NOT IN ('completed', 'failed')",
        (error or "The analysis stopped without finishing.",
         payload["analysis_run_id"]))


@workflow.on_give_up("discovery.run")
def _discovery_gave_up(cur: Any, payload: dict[str, Any], error: str) -> None:
    cur.execute(
        "UPDATE discovery_runs SET status = 'failed', error = %s "
        "WHERE id = %s AND status NOT IN ('complete', 'failed')",
        (error or "The sweep stopped without finishing.",
         payload["discovery_run_id"]))


def _ingest_document(
    cur, *, project_id: str, source_id: str, path: Path, suffix: str, actor: str
) -> dict[str, Any]:
    try:
        parsed = document_parser.parse_document(path, suffix=suffix)
    except document_parser.UnsupportedFormat as exc:
        raise PermanentIngestionError(str(exc)) from exc

    if not parsed.passages:
        raise PermanentIngestionError("No readable text was found in this document.")

    broken = parsed.verify_anchors()
    if broken:
        # An anchor that does not resolve is not evidence.
        raise PermanentIngestionError(
            f"{len(broken)} passage anchors did not match the source text; "
            "refusing to index unverifiable spans."
        )

    objects.advance_ingestion(cur, source_id=source_id, to_status=IngestionStatus.PARSING,
                              detail=f"Parsed {len(parsed.passages)} passages")

    # Text in this document addressed to an AI system, recorded for the
    # researcher rather than acted on. Nothing is blocked or edited: the
    # content is already untrusted and already fenced when it reaches a model,
    # and a paper carrying hidden instructions is a fact about that paper
    # somebody should know before citing it.
    trust.note_injection_attempt(cur, project_id=project_id, source_id=source_id,
                                 text=parsed.text, actor=actor)
    count = corpus.store_passages(cur, project_id=project_id, source_id=source_id,
                                  passages=parsed.passages)

    objects.advance_ingestion(cur, source_id=source_id, to_status=IngestionStatus.STRUCTURING,
                              detail="Recording paper structure")
    paper = corpus.store_paper(cur, project_id=project_id, source_id=source_id,
                               parsed=parsed, actor=actor)

    objects.advance_ingestion(cur, source_id=source_id, to_status=IngestionStatus.INDEXING,
                              detail=f"Indexing {count} passages")
    embedding = corpus.embed_passages(cur, project_id=project_id, source_id=source_id)

    objects.advance_ingestion(cur, source_id=source_id, to_status=IngestionStatus.ENRICHING,
                              detail="Recording sections")
    sections = sorted({p.section for p in parsed.passages if p.section})
    return {
        "kind": "document", "passages": count, "pages": parsed.page_count,
        "sections": sections, "embedding": embedding, **paper,
        "summary": (f"{count} passages, {parsed.page_count} pages"
                    + (f", {embedding['embedded']} embedded" if embedding["embedded"]
                       else ", lexical index only")),
    }


def _ingest_dataset(
    cur, *, project_id: str, source_id: str, path: Path, suffix: str, name: str,
    content_hash: str, storage_key: str, actor: str,
) -> dict[str, Any]:
    objects.advance_ingestion(cur, source_id=source_id, to_status=IngestionStatus.PARSING,
                              detail="Reading tabular data")
    try:
        profile = dataset_parser.profile_dataset(path, suffix=suffix)
    except dataset_parser.UnsupportedDataset as exc:
        raise PermanentIngestionError(str(exc)) from exc
    if profile.column_count == 0:
        raise PermanentIngestionError("No columns were found in this dataset.")

    objects.advance_ingestion(cur, source_id=source_id, to_status=IngestionStatus.STRUCTURING,
                              detail=f"Profiling {profile.column_count} columns")
    stored = corpus.store_dataset(cur, project_id=project_id, source_id=source_id,
                                  name=name, profile=profile, content_hash=content_hash,
                                  storage_key=storage_key, actor=actor)

    # A dataset's searchable surface is its schema, not its rows: the system forbids
    # shipping millions of rows around, and a column description is what a
    # researcher actually searches for. Schema passages describe rather than
    # quote, so they deliberately carry no source span.
    objects.advance_ingestion(cur, source_id=source_id, to_status=IngestionStatus.INDEXING,
                              detail="Indexing column descriptions")
    schema_passages = [
        document_parser.Passage(
            ordinal=column.ordinal,
            # The file's own label leads where there is one. A researcher
            # searches for "antibiotic use", not for `q7a_rec`, and on an SPSS
            # or Stata import the file already told us which is which.
            content=(f"Column {column.name}"
                     + (f" — {label}" if (label := getattr(column, "label", "")) else "")
                     + f" ({column.physical_type}, {column.semantic_type})"
                     + (f" in {column.unit}" if column.unit else "")
                     + f". {column.unique_count} distinct values, "
                       f"{column.missing_count} missing."),
            locator=f"column {column.ordinal + 1}: {column.original_name}",
            kind="schema", section="schema",
            metadata={"semantic_type": column.semantic_type, "column": column.name},
        )
        for column in profile.columns
    ]
    corpus.store_passages(cur, project_id=project_id, source_id=source_id,
                          passages=schema_passages)
    embedding = corpus.embed_passages(cur, project_id=project_id, source_id=source_id)

    objects.advance_ingestion(cur, source_id=source_id, to_status=IngestionStatus.ENRICHING,
                              detail="Building data-quality report")

    return {
        "kind": "dataset", **stored,
        "rows": profile.row_count, "columns": profile.column_count,
        "quality_report": profile.quality_report, "embedding": embedding,
        "summary": f"{profile.row_count} rows, {profile.column_count} columns",
    }


@REGISTRY.register("analysis.run")
def analysis_run(run: dict[str, Any], cur: Any) -> dict[str, Any]:
    """Execute one analysis in the sandbox and commit its provenance.

    The dataset file is located here, in the parent, and handed to the executor
    as a path. The sandbox process is never told where the object store is, and
    never receives database credentials.
    """
    from throughline_domain import analysis, storage
    from throughline_runtime.executor import SandboxPolicy, SandboxTimeout, run_analysis

    run_id = run["input"]["analysis_run_id"]
    cur.execute("SELECT project_id, spec_id, status FROM analysis_runs WHERE id = %s", (run_id,))
    record = cur.fetchone()
    if not record:
        raise ValueError(f"Analysis run {run_id} no longer exists")
    if record["status"] in {"completed", "failed"}:
        #  — a retry must not recompute a terminal run.
        return {"analysis_run_id": run_id, "status": record["status"], "skipped": True}

    spec_row, spec_payload, input_path, input_suffix = _prepare_analysis(
        cur, record["spec_id"])
    del record  # everything needed is prepared above

    cur.execute("UPDATE analysis_runs SET status = 'running', started_at = now() "
                "WHERE id = %s", (run_id,))

    try:
        sandbox = run_analysis(
            spec=spec_payload, input_path=input_path,
            input_suffix=input_suffix, policy=SandboxPolicy(),
        )
    except SandboxTimeout as exc:
        from throughline_runtime.executor import SandboxResult, policy_report

        sandbox = SandboxResult(ok=False, payload={"error": str(exc)},
                                policy=policy_report())

    return analysis.record_result(cur, run_id=run_id, sandbox=sandbox,
                                  spec_row=spec_row, actor="system:analysis")


def _prepare_analysis(cur, spec_id: str):
    """Everything the sandbox needs for one spec, resolved in the parent.

    Split out of `analysis_run` so that a sweep prepares its candidates exactly
    the way a lone analysis does. A second copy of this — the column
    translation especially — would drift, and a discovery run that resolved
    names differently from a hand-specified one is the kind of difference
    nobody notices until the numbers disagree.
    """
    from throughline_domain import analysis, storage

    spec_row = dict(analysis.load_spec(cur, spec_id))
    version_ids = spec_row["dataset_version_ids"]
    cur.execute(
        """
        SELECT f.storage_key, f.filename, dv.content_hash, dv.row_count
        FROM dataset_versions dv
        JOIN datasets d ON d.id = dv.dataset_id
        JOIN sources s ON s.id = d.source_id
        JOIN files f ON f.id = s.file_id
        WHERE dv.id = %s
        """,
        (version_ids[0],),
    )
    location = cur.fetchone()
    if not location:
        raise ValueError("The dataset version has no stored file to analyse.")

    spec_row["_dataset"] = {"content_hash": location["content_hash"],
                            "row_count": location["row_count"]}
    # The sandbox reads the file, so it only knows the headers as written. A
    # spec may legitimately name the normalised form instead — discovery always
    # does — and translating here is what makes both spellings actually run.
    for_sandbox = analysis.to_file_columns(
        cur, dataset_version_id=version_ids[0], spec=spec_row)
    spec_payload = {
        "method": spec_row["method"], "variables": for_sandbox["variables"],
        "filters": for_sandbox["filters"],
        "confidence_level": spec_row["confidence_level"],
        "method_rationale": spec_row["method_rationale"],
        "random_seed": spec_row["random_seed"], "parameters": spec_row["parameters"],
    }
    return (
        spec_row, spec_payload,
        storage.path_for(location["storage_key"]),
        Path(location["filename"] or "").suffix.lower() or ".csv",
    )


def _execute_analyses(cur, run_ids: list[str]) -> None:
    """Run many prepared analyses in one sandbox, recording each on its own.

    This is discovery's path. Every candidate pair used to get its own
    subprocess — a fresh Python, a fresh pandas, and a fresh copy of the whole
    dataset — measured at about a second each, of which four fifths is startup.
    Forty columns is 820 pairs and a quarter of an hour; a hundred columns is
    4,950 pairs and most of two. The correlations themselves take microseconds.

    Nothing about what is recorded changes. Each run still gets its own
    `analysis_runs` row, its own result, its own seed and its own verdict,
    through the same `record_result` a lone analysis uses — so a connection is
    traceable to its computation exactly as before. What is shared is the
    process, and these specs are system-generated from a closed list of
    methods, so sharing one is the same isolation boundary as eight hundred.

    Runs are grouped by input file. Discovery sweeps one dataset version, so
    in practice there is one group; grouping is what keeps that an observation
    rather than an assumption.
    """
    from throughline_domain import analysis
    from throughline_runtime.executor import (
        SandboxPolicy, SandboxResult, SandboxTimeout, policy_report, run_many,
    )

    groups: dict[tuple[str, str], list[tuple[str, dict, dict]]] = {}
    for run_id in run_ids:
        cur.execute("SELECT spec_id, status FROM analysis_runs WHERE id = %s",
                    (run_id,))
        record = cur.fetchone()
        if not record or record["status"] in {"completed", "failed"}:
            # A retry must not recompute a terminal run, as in `analysis_run`.
            continue
        spec_row, spec_payload, input_path, input_suffix = _prepare_analysis(
            cur, record["spec_id"])
        groups.setdefault((str(input_path), input_suffix), []).append(
            (run_id, spec_row, spec_payload))

    for (input_path, input_suffix), prepared in groups.items():
        ids = [run_id for run_id, _, _ in prepared]
        cur.execute("UPDATE analysis_runs SET status = 'running', "
                    "started_at = now() WHERE id = ANY(%s)", (ids,))
        try:
            batch = run_many(
                specs=[payload for _, _, payload in prepared],
                input_path=Path(input_path), input_suffix=input_suffix,
                policy=SandboxPolicy(),
            )
        except SandboxTimeout as exc:
            batch = SandboxResult(ok=False, payload={"error": str(exc)},
                                  policy=policy_report())

        entries = batch.payload.get("results") or []
        for index, (run_id, spec_row, _) in enumerate(prepared):
            if index < len(entries):
                sandbox = SandboxResult(
                    ok=bool(entries[index].get("ok")), payload=entries[index],
                    stderr=batch.stderr, exit_code=batch.exit_code,
                    duration_ms=int(entries[index].get("duration_ms") or 0),
                    policy=batch.policy,
                )
            else:
                # The sweep itself failed, so every candidate in it failed —
                # and says the same thing, rather than being left `running`
                # for ever with no explanation.
                sandbox = SandboxResult(
                    ok=False,
                    payload={"error": batch.payload.get("error")
                             or "The sweep produced no result for this pair."},
                    stderr=batch.stderr, exit_code=batch.exit_code,
                    policy=batch.policy,
                )
            analysis.record_result(cur, run_id=run_id, sandbox=sandbox,
                                   spec_row=spec_row, actor="system:analysis")


def _execute_analysis(cur, run_id: str) -> None:
    """Run one analysis to completion inside the caller's transaction.

    Discovery and validation generate many analyses whose results they need
    immediately, so they run inline rather than round-tripping through the queue.
    The sandbox boundary is identical either way — this is the same handler.
    """
    analysis_run({"input": {"analysis_run_id": run_id}}, cur)


@REGISTRY.register("discovery.run")
def discovery_run(run: dict[str, Any], cur: Any) -> dict[str, Any]:
    """/ — generate candidates, test them, correct, rank, record.

    Every candidate becomes a real sandboxed analysis run, so a discovered
    connection is traceable to the computation behind it (this rule, this rule).
    """
    from throughline_domain import analysis, discovery, workflow

    discovery_run_id = run["input"]["discovery_run_id"]
    cur.execute("SELECT * FROM discovery_runs WHERE id = %s", (discovery_run_id,))
    record = cur.fetchone()
    if not record:
        raise ValueError(f"Discovery run {discovery_run_id} no longer exists")
    if record["status"] in {"complete", "failed"}:
        return {"discovery_run_id": discovery_run_id, "status": record["status"],
                "skipped": True}

    project_id = record["project_id"]
    version_id = record["dataset_version_id"]
    cur.execute("UPDATE discovery_runs SET status = 'running', "
                "started_at = COALESCE(started_at, now()) WHERE id = %s",
                (discovery_run_id,))

    def sweep() -> dict[str, Any]:
        """Plan the candidates and test each one in the sandbox."""
        plan = discovery.plan_candidates(cur, dataset_version_id=version_id)
        run_ids = []
        for candidate in plan["candidates"]:
            created = analysis.create_spec(cur, project_id=project_id, spec={
                "method": candidate["method"],
                "dataset_version_ids": [version_id],
                "variables": candidate["variables"],
                "method_rationale": candidate["rationale"],
                "research_question": (f"Is {candidate['left_variable']} associated with "
                                      f"{candidate['right_variable']}?"),
            }, actor="system:discovery")
            analysis_run_id = analysis.create_run(cur, project_id=project_id,
                                                  spec_id=created["spec_id"])
            run_ids.append(analysis_run_id)
        # One sandbox for the sweep, not one per pair. See `_execute_analyses`.
        _execute_analyses(cur, run_ids)
        return {"plan": plan, "analysis_run_ids": run_ids}

    # Steps 4–6: one sandboxed analysis per surviving candidate.
    #
    # Wrapped so it happens once even if the run stops at the gate below and is
    # resumed after approval. Without that, approving would re-plan and re-test
    # every candidate — a second sandboxed analysis per pair, and a second set
    # of `analysis_runs` rows recording work that was already done.
    # `example.assemble` calls this handler inline as a subroutine, the same way
    # `_execute_analysis` does, and hands it a run dict with an input and no id.
    # There is then no workflow run to record steps against and none to resume,
    # so the sweep simply happens. Recording against the *caller's* run would be
    # worse than not recording: the steps would attach to a different workflow,
    # and a gate would halt the worked example on a fresh install.
    workflow_run_id = run.get("id")
    swept = (workflow.once(cur, run_id=workflow_run_id, name="test_candidates",
                           produce=sweep)
             if workflow_run_id else sweep())
    plan = swept["plan"]
    candidates = plan["candidates"]
    tested: list[dict[str, Any]] = [
        {"candidate": candidate, "analysis_run_id": analysis_run_id,
         "run": analysis.get_run(cur, analysis_run_id)}
        for candidate, analysis_run_id in zip(candidates,
                                              swept["analysis_run_ids"])
    ]

    # Everything above this line is reversible: it computed results and wrote
    # nothing into the project's own record of what is true. Everything below
    # records connections and promotes the survivors, which is the system
    # deciding on its own that something is a discovery worth presenting.
    #
    # Rule 10 — "AI does not secretly mutate important research state" — is the
    # reason a researcher can ask to stand between those two halves. It is
    # asked for per run rather than imposed on every one: gating every sweep by
    # default would stop the seeded worked example on a fresh install, and
    # whether an unattended sweep should be allowed to record is a decision for
    # whoever runs this, not a default worth choosing on their behalf.
    if workflow_run_id and run["input"].get("hold_before_recording"):
        completed_now = sum(1 for t in tested
                            if (t["run"] or {}).get("status") == "completed")
        workflow.gate(
            cur, run_id=workflow_run_id, name="record_connections",
            # The claimer: `claim_next` returns the row it leased (T160).
            worker_id=run.get("lease_owner"),
            describes=(
                f"Record {completed_now} tested "
                f"{'pair' if completed_now == 1 else 'pairs'} into this project "
                f"and promote whichever survive correction at FDR "
                f"{float(record['false_discovery_rate']):.2f}. Nothing has been "
                f"written to the project yet; the results exist and can be read "
                f"first."),
        )

    # Step 7: correct across the whole family that was actually run.
    completed = [t for t in tested if t["run"]["status"] == "completed"]
    corrections = discovery.benjamini_hochberg(
        [(t["run"]["result"] or {}).get("p_value") for t in completed],
        fdr=float(record["false_discovery_rate"]),
    )

    connection_ids: list[str] = []
    for item, correction in zip(completed, corrections):
        connection_id = discovery.record_connection(
            cur, project_id=project_id, discovery_run_id=discovery_run_id,
            candidate=item["candidate"], analysis_run_id=item["analysis_run_id"],
            result=item["run"]["result"] or {}, q_value=correction["q_value"],
            # Carried from the run so the sweep joins the researcher's session
            # rather than forming a family of its own. None when the run came
            # from a script or an older client, and then it is its own family —
            # which is the behaviour that already existed.
            enquiry_id=record.get("enquiry_id"),
        )
        connection_ids.append(connection_id)
        # Step 10: only survivors of the correction become exploratory. The rest
        # stay candidates — visible, but not presented as discoveries.
        if correction["survives"]:
            discovery.transition(
                cur, connection_id=connection_id, to_status="exploratory",
                reason=(f"Survived Benjamini-Hochberg correction at FDR "
                        f"{record['false_discovery_rate']:.2f} "
                        f"(q = {correction['q_value']:.4g})."),
                actor="system:discovery", checks={"multiple_comparison_correction": True},
            )

    cur.execute(
        """
        UPDATE discovery_runs SET status = 'complete', candidates_considered = %s,
            candidates_excluded = %s, tests_run = %s, exclusion_reasons = %s,
            finished_at = now()
        WHERE id = %s
        """,
        (len(candidates), len(plan["excluded_columns"]), len(completed),
         plan["excluded_columns"], discovery_run_id),
    )

    exploratory = sum(1 for c in corrections if c["survives"])
    return {
        "discovery_run_id": discovery_run_id, "status": "complete",
        "columns_usable": plan["columns_usable"],
        "excluded_columns": plan["excluded_columns"],
        "candidates": len(candidates), "tests_run": len(completed),
        "survived_correction": exploratory, "connection_ids": connection_ids,
    }



class ExampleNotReady(RuntimeError):
    """The worked example is waiting on a step that has not finished yet.

    Raised rather than polled: the workflow runner already knows how to retry
    with backoff, and a handler that sleeps holds a worker slot for no reason.
    """


def _advance_project(cur: Any, *, project_id: str, actor: str) -> dict[str, Any]:
    """Walk a project's newest profiled dataset through the loop.

    Discovery over the real profiled columns, then the strongest surviving
    relationship promoted to a finding that carries the connection it came
    from — so the finding has a route back to the analysis, and through that to
    the dataset.

    **Why this is not the example's private code any more.** It was, and the
    consequence was the whole product's shape: the seeded example arrived with
    connections and a finding already in it, and a researcher who uploaded
    their own dataset got a profile and a dead end. Six steps, each its own
    button, each able to fail on its own, and nobody — not a researcher, not
    the person who built it — will click through all six to find out whether
    their data says anything. The machine can do the work; the researcher's job
    is to judge it.

    Idempotent at both stages. A retry after a partial run must not produce a
    second discovery, which would double every connection on the first screen,
    and must not record a second finding for work already promoted.
    """
    from throughline_domain import discovery, findings
    from throughline_schemas.enums import CausalStatus, FindingType

    cur.execute(
        """
        SELECT dv.id
        FROM dataset_versions dv
        JOIN datasets d ON d.id = dv.dataset_id
        WHERE d.project_id = %s
        ORDER BY dv.created_at DESC
        LIMIT 1
        """,
        (project_id,),
    )
    row = cur.fetchone()
    if not row:
        return {"project_id": project_id, "discovery_run_id": None,
                "finding_id": None, "note": "no profiled dataset yet"}
    version_id = row["id"]

    cur.execute("SELECT count(*) AS n FROM dataset_columns WHERE dataset_version_id = %s",
                (version_id,))
    if not cur.fetchone()["n"]:
        return {"project_id": project_id, "discovery_run_id": None,
                "finding_id": None, "note": "the dataset has no profiled columns yet"}

    # Idempotent: a retry after a partial run must not produce a second
    # discovery, which would double every connection on the first screen.
    cur.execute("SELECT id FROM discovery_runs WHERE project_id = %s LIMIT 1",
                (project_id,))
    existing = cur.fetchone()
    if existing:
        discovery_run_id = existing["id"]
    else:
        discovery_run_id = discovery.create_run(
            cur, project_id=project_id, dataset_version_id=version_id)
        discovery_run({"input": {"discovery_run_id": discovery_run_id}}, cur)

    connections = discovery.list_connections(cur, project_id=project_id, limit=50)
    if not connections:
        return {"project_id": project_id, "discovery_run_id": discovery_run_id,
                "finding_id": None, "note": "discovery surfaced nothing to promote"}

    cur.execute("SELECT id FROM findings WHERE project_id = %s LIMIT 1", (project_id,))
    if cur.fetchone():
        return {"project_id": project_id, "discovery_run_id": discovery_run_id,
                "finding_id": None, "note": "already assembled"}

    # The strongest surviving relationship, which for this dataset is
    # consumption against resistance. Chosen by the recorded effect rather than
    # by name, so the example does not quietly assert an answer the computation
    # did not produce.
    best = max(connections, key=lambda c: abs(c.get("effect_size") or 0))
    finding_id = findings.create_finding(
        cur, project_id=project_id,
        title=f"{best['left_variable']} tracks {best['right_variable']}",
        finding_type=FindingType.STATISTICAL,
        # True of this project, not of the example's.
        #
        # This sentence was written for the seeded example and named its
        # confounder — "GDP per capita was tested as a confounder rather than
        # assumed away" — which was a statement of fact there and a fabrication
        # everywhere else. The first upload through the general path produced a
        # finding about crop yield that claimed GDP per capita had been tested.
        # What is true of every project is what discovery actually did: it
        # tested every pair and corrected for how many tests it ran.
        statement=(
            f"{best['left_variable']} and {best['right_variable']} move "
            f"together across this dataset. This is an association between "
            f"measured quantities: the design cannot establish direction, and "
            f"every pair in the sweep was tested and corrected for how many "
            f"tests ran. Nothing here has been validated."),
        summary="Promoted from this project's discovery run.",
        # Assessed, and the assessment is "association". Not NOT_ASSESSED:
        # discovery really did test GDP per capita as a confounder, and saying
        # nothing was looked at would understate what the run did. This is also
        # what stops the visual critic passing a caption that claims cause.
        causal_status=CausalStatus.ASSOCIATION_ONLY,
        # The connection this was promoted from, which is what joins the finding
        # to the analysis that produced it and, through that, to the dataset.
        # Without it the finding is an island: `evidence_graph` returns claims
        # only, and a researcher opening it has no route back to the evidence.
        from_connections=[best["id"]],
        actor=actor,
    )
    # The evidence, the claim and the finding's own object all come from
    # `create_finding` now, which records the analyses behind `from_connections`
    # for every finding rather than only this one. The example used to do it
    # here, which is why it was the only finding in the product that could ever
    # leave CANDIDATE.

    return {"project_id": project_id, "discovery_run_id": discovery_run_id,
            "finding_id": finding_id}


@REGISTRY.register("example.assemble")
def example_assemble(run: dict[str, Any], cur: Any) -> dict[str, Any]:
    """The seeded example, walked through the loop like any other project.

    It used to carry its own copy of this, which is how the example ended up
    being the one project in the product that arrived with anything in it. The
    only thing left that is particular to the example is that its dataset must
    be there: the example promises a project with work in it, so not being
    ready is a reason to retry rather than a result.
    """
    project_id = run["input"]["project_id"]
    out = _advance_project(cur, project_id=project_id, actor="system:example")
    if out.get("discovery_run_id") is None:
        raise ExampleNotReady(out.get("note") or "the example is not ready yet")
    return out


@REGISTRY.register("project.advance")
def project_advance(run: dict[str, Any], cur: Any) -> dict[str, Any]:
    """Take a researcher's own dataset as far as the machine honestly can.

    Queued by one press rather than by ingestion, and that is a decision worth
    recording. Running it automatically when a dataset finishes profiling is
    what "it should just work" would mean, and it was tried: the project then
    has a discovery run the researcher did not ask for, so their own press of
    *Discover connections* either doubles every connection or is refused as a
    repeat of something they never started. Compute spent on somebody's data
    without asking is also a real objection in research software.

    So: one act, not six. Discovery runs, every pair is tested and corrected
    for how many tests ran, and the strongest survivor is recorded as a finding
    that keeps the line back to the analysis behind it — all from a single
    press, instead of six buttons found in order, each able to fail alone.

    What it does not do is decide anything. Nothing here promotes past
    candidate, nothing validates, and the finding it writes says in its own
    statement that the design cannot establish direction. The machine does the
    work; the researcher does the judging, which is the whole argument of the
    product and the reason this can be automatic at all.

    Not an error when there is nothing to do. A project whose dataset has no
    profiled columns yet, or one that has already been advanced, returns a note
    saying so — a retry loop over an empty project would be a worker spinning
    on a project that is simply finished.
    """
    return _advance_project(
        cur, project_id=run["input"]["project_id"], actor="system:loop")


@REGISTRY.register("connection.validate")
def connection_validate(run: dict[str, Any], cur: Any) -> dict[str, Any]:
    """ — try to destroy a connection; promote it only if it survives."""
    from throughline_domain import discovery, validation

    connection_id = run["input"]["connection_id"]
    confounders = list(run["input"].get("confounders") or [])

    outcome = validation.validate_connection(
        cur, connection_id=connection_id,
        runner=lambda analysis_run_id: _execute_analysis(cur, analysis_run_id),
        confounders=confounders,
    )

    cur.execute("SELECT lifecycle_status FROM connections WHERE id = %s", (connection_id,))
    current = cur.fetchone()["lifecycle_status"]
    if current == "exploratory":
        if outcome["passed"]:
            discovery.transition(cur, connection_id=connection_id, to_status="validated",
                                 reason=outcome["summary"], actor="system:validation",
                                 checks=outcome["checks"])
        else:
            # Failing validation does not reject the connection — it leaves it
            # exploratory, which is exactly what it still is.
            outcome["note"] = ("The connection remains exploratory. Failing a "
                               "robustness check is information, not a verdict.")
    return {"connection_id": connection_id, **outcome}


@REGISTRY.register("finding.challenge")
def finding_challenge(run: dict[str, Any], cur: Any) -> dict[str, Any]:
    """ — run the Scientific Critic against a finding."""
    from throughline_domain import critic

    return critic.challenge_finding(
        cur, finding_id=run["input"]["finding_id"],
        runner=lambda analysis_run_id: _execute_analysis(cur, analysis_run_id),
        actor=run["input"].get("actor") or "system:critic",
        confounders=tuple(run["input"].get("confounders") or ()),
    )


@REGISTRY.register("visual.render_blender")
def render_blender(run: dict[str, Any], cur: Any) -> dict[str, Any]:
    """
    Render one figure through Blender, on this machine.

    A job rather than a request because a render can take minutes. Raises on
    failure — returning normally would mark the run completed, and the screen
    would show success for a render that never happened. The route queues it
    with a single attempt, so a missing Blender is reported once, at once.
    """
    from throughline_domain import visuals

    given = run["input"]
    # Runs queued before the look was chosen carry only the figure's id.
    return visuals.render_through_blender(
        cur, visual_id=given["visual_id"], style=given.get("style", "figure"),
        ground=given.get("ground", "light"))
