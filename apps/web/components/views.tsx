"use client";

/**
 * The workspace views.
 *
 * These are presentation only: every number shown comes from the API, and
 * nothing is computed in the browser. §106 forbids shipping rows to React, and
 * more importantly a figure or a statistic recomputed here could disagree with
 * the analysis that produced it.
 */

import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import {
  AnalysisRun, AnalysisRunRow, ApiError, Connection, DatasetColumn, DiscoveryMap, EvidenceGraph,
  Finding, objectTypeName,
  INGESTION_STAGES, Provenance, SearchResult, Source, ValidationReport, api,
  ingestionStep, isIngesting,
} from "@/lib/api";
import { Tabs } from "./Tabs";
import { Cartesian, type CartesianMark } from "./charts/Cartesian";
import { columnNotices } from "@/lib/column-notices";
import { DatasetFormats, extrasNote, uploadAccept } from "@/lib/formats";
import { ApiState, useApi } from "@/lib/useApi";
import { ObjectKind, useObjectId } from "@/lib/useObjectId";
import { ObjectHistory } from "./objecthistory";
import { SECTIONS, Section } from "./Shell";
import { PlainSummary, ResultCard } from "./ResultCard";
import { Empty, Failure, Fold, Loading, Num, Stat, StateMark, Status, Totals } from "./primitives";
import { Fragility } from "./fragility";
import { DatabaseTables } from "./databasetables";
import { CohortTree } from "./cohorts";
import { RecordFinding } from "./recordfinding";
import { Approvals } from "./approvals";
import { WhatTheSweepDid } from "./sweep";
import { currentStep, loopSteps, stepTarget } from "@/lib/loop";
import { ObjectAction, ObjectActions } from "./objectactions";
import { Limitations } from "./limitations";
import { canDraftReport, draftReport } from "./reports";
import { Term, TermList, methodTerms } from "./term";
import { PlainReading, readingOf } from "./plainreading";

// ---------------------------------------------------------------------------
// Overview (§70)
// ---------------------------------------------------------------------------

export function Overview({ project, map, onGo, onOpen, onAddSources, onLineage,
                           onAdvance, advancing = false, labels }: {
  project: { name: string; research_question: string };
  map: DiscoveryMap | null;
  onGo: (section: Section) => void;
  /**
   * Open one object rather than the list it lives in.
   *
   * Steps 4 and 5 both act on a single connection, and the server already
   * ranks them. Without this the researcher is sent to a six-row table with
   * nothing saying which row the recommendation meant. Optional, and the
   * control falls back to the section when it is absent, so the button is
   * never dead — it just lands one screen short.
   */
  onOpen?: (kind: "connection", id: string) => void;
  /**
   * The Sources screen's own upload, so the first act of a new project can be
   * taken from the screen that asks for it rather than after a rail hop.
   */
  onAddSources?: (files: FileList | null) => void;
  /**
   * Open the project's lineage — the river — from here.
   *
   * §08 gives the river a contextual entrance from Overview and Research
   * graph and refuses it a place in the navigation, on the grounds that it is
   * a way of reading the project rather than another room in it. This is that
   * entrance. Optional, so the Overview still renders wherever no navigator
   * has been wired up.
   */
  onLineage?: () => void;
  /** Run the whole loop on this project's data, in one act. */
  onAdvance?: () => void;
  /** Whether that run is in flight, so the control can say so. */
  advancing?: boolean;
  /** Approved display names by raw column, so the control names a connection
   *  the way the strip above it does (Part C: no raw names outside Variables). */
  labels?: Record<string, string>;
}) {
  if (!map) return <Loading rows={4} label="Reading the project" />;

  /*
   * The research loop as a checklist against real state (§70).
   *
   * A dashboard of six zeroes tells a new researcher nothing about what to do.
   * Each step here is ticked from the project's actual counts, so the list is
   * both an explanation of the method and the place you start the next step —
   * and it can never claim progress the database does not have. The steps
   * themselves live in `lib/loop.ts`, so the shell and the inspector read the
   * same list and cannot disagree with this card about what comes next.
   */
  const steps = loopSteps(map);
  /*
   * The step the project is on, which is the server's recommendation when it
   * names one and the earliest gap otherwise — not `the first unticked row`.
   * The two differ, and the difference is visible: a finding may legitimately
   * be recorded from a connection nothing has validated yet, so step 5 ticks
   * above an unticked step 4 and a numbered checklist ends up contradicting
   * its own order. Marking the row the *project* is on keeps the numbers
   * describing the method rather than a sequence the work did not follow.
   */
  const current = currentStep(map);
  /*
   * A project with no sources cannot take its first step from here unless the
   * file picker is here. Sources still owns uploading; this is the same
   * handler, offered at the moment the list first says to use it.
   */
  const empty = (map.counts.sources ?? 0) === 0;
  // The meters below want totals across lifecycle states.
  const connections = Object.values(map.connections).reduce((a, b) => a + b, 0);
  const findings = Object.values(map.findings).reduce((a, b) => a + b, 0);

  return (
    <>
      <h1>{project.name}</h1>
      <p className="lede serif" style={{ fontSize: 15 }}>
        {project.research_question || "No research question has been stated yet."}
      </p>

      {/*
        One line rather than six bordered cells (T139). The cells were already
        a compression of six cards, and they were still six boxes a reader
        scanned past; the same six numbers set as a sentence are read as a
        sentence. Nothing is dropped — a zero still prints, because
        "0 contradictions" is a claim about the project and a missing cell is
        not.
      */}
      <Totals parts={[
        [map.counts.sources, "sources", "source"],
        [map.counts.datasets, "datasets", "dataset"],
        [map.counts.analyses, "analyses", "analysis"],
        [connections, "connections", "connection"],
        [findings, "findings", "finding"],
        [map.counts.contradictions, "contradictions", "contradiction"],
      ]} />

      {/*
        * One press instead of six screens.
        *
        * A project with a profiled dataset and nothing promoted is a project
        * whose whole loop the machine can run: discovery over the real
        * columns, every pair corrected for how many tests ran, the strongest
        * survivor written down with the analysis behind it. Six buttons found
        * in order, each able to fail alone, is a marathon nobody walks — the
        * seeded example was the only project in this product that ever arrived
        * with work in it.
        *
        * Offered rather than done on upload. Running it unasked leaves a
        * discovery the researcher did not start, so their own press either
        * doubles every connection or is refused as a repeat of something they
        * never began; and compute spent on somebody's data without asking is
        * its own objection. It says what it will do before it does it.
        */}
      {onAdvance && (map.counts.datasets ?? 0) > 0 && findings === 0 && (
        <div className="card ov-advance">
          <h2>Take it from here</h2>
          <p>
            {advancing
              ? "Working. Discovery is running over the profiled columns; this "
                + "screen updates as each step finishes."
              : "Your dataset is profiled. Throughline can run the rest of the "
                + "loop on it: test every pair, correct for how many tests ran, "
                + "and write down the strongest survivor with the analysis "
                + "behind it."}
          </p>
          <button className="btn btn-primary" type="button"
                  disabled={advancing} onClick={onAdvance}>
            {advancing ? "Working…" : "Run the loop →"}
          </button>
          <p className="note">
            Nothing is promoted past candidate and nothing is validated. The
            machine does the work; the judging stays yours.
          </p>
        </div>
      )}

      <div className="ov-grid">
      <div className="card">
        <h2>The loop</h2>
        <ol className="steps">
          {steps.map((step) => {
            /*
             * By id, not by identity. `currentStep` derives its own list from
             * the map, so the step it returns is a different object from the
             * one in this list — comparing the two by reference marks nothing
             * as current and quietly returns the card to a state where no row
             * is next and no control is offered.
             */
            const here = step.id === current?.id;
            const target = stepTarget(step, map, labels);
            return (
              <li key={step.id} data-done={step.done} data-next={here}>
                <button onClick={() => onGo(step.go)}>
                  <span className="step-tick" aria-hidden />
                  {/*
                    One line per row, and the sentence only where it is needed
                    (T139). Six hints stacked was sixty words of method on the
                    first screen of the product, five-sixths of it about steps
                    the reader is not taking. The row the project is on keeps
                    its hint; the others are a label and a state, and the hint
                    they lost is one press away in the fold below.
                  */}
                  <span>
                    <b>{step.label}</b>
                    {here && <em>{step.hint}</em>}
                  </span>
                  {/*
                    Where the row goes, said before it is pressed. Every row
                    here is a button and none of them looked like one, so the
                    only way to learn where a step led was to take it and read
                    the rail afterwards. The name comes from the rail's own
                    list, so a screen that is renamed is renamed here too.
                  */}
                  <span className="step-go">→ {sectionLabel(step.go)}</span>
                  {/* Never colour alone (§118): the state is also a word. */}
                  <span className="step-state">
                    {step.done ? "done" : here ? "next" : "waiting"}
                  </span>
                </button>

                {/*
                  The one real control on the card, and a sibling of the row
                  rather than a child of it: a button inside a button is not
                  valid HTML, and the two would fight over the same press.
                */}
                {here && (
                  <span className="step-action">
                    {/*
                      Plain, not primary (T139). This control and the step
                      strip's button perform the same act, and two gold buttons
                      on one screen for one action is the duplication the strip
                      was built to end — the strip is the one that cannot be
                      scrolled away, so the strip keeps the emphasis.
                    */}
                    {empty && onAddSources ? (
                      <label className="btn" style={{ display: "inline-block" }}>
                        Add sources
                        <input
                          type="file" multiple hidden
                          accept=".pdf,.docx,.txt,.md,.csv,.tsv,.xlsx,.json"
                          onChange={(e) => onAddSources(e.target.files)}
                        />
                      </label>
                    ) : (
                      <button
                        className="btn"
                        onClick={() => (target.item && onOpen
                          ? onOpen("connection", target.item)
                          : onGo(target.section))}
                      >
                        {target.label} →
                      </button>
                    )}
                  </span>
                )}
              </li>
            );
          })}
        </ol>
        {/* §70 — the server's own recommendation, which knows things the
            checklist does not, such as which connection ranks highest. It is
            the one sentence on this card that changes as the project moves,
            so it is the one sentence that stays open. */}
        <p className="note" style={{ marginBottom: 0 }}>{map.recommended_next_action}</p>

        {/*
          What every step is for, and why the order is not an order — kept, and
          closed. A numbered list reads as a sequence and this one is not one;
          a first-timer needs to be told that once, and does not need to be
          told it on every visit.
        */}
        <Fold summary="What each step is for" count={steps.length}>
          <p className="note steps-note">
            The steps may be taken out of order — this is where the project is now.
          </p>
          <dl className="kv">
            {steps.map((step) => (
              <Fragment key={step.id}>
                <dt>{step.label}</dt>
                <dd>{step.hint}</dd>
              </Fragment>
            ))}
          </dl>
        </Fold>
      </div>

      {/*
        * The project's actual state, beside the loop rather than under it.
        *
        * §09 asks the Overview for "current question, actual project state,
        * recent work, unresolved items, next supported actions" and warns off
        * a radial dashboard. Stacked in one column these read as an appendix
        * to the checklist; beside it they are what the checklist is about, and
        * the screen stops being 40% content in a 1586px frame.
        */}
      <div className="ov-state">
        {/*
          * What the project has actually found, on the screen that opens it.
          *
          * §09 asks the Overview for "current question, actual project state,
          * **recent work**, unresolved items, next supported actions", and
          * recent work was the one of those five that was missing entirely.
          * The column held two collapsed folds of counts, so two thirds of the
          * product's front door was empty and a researcher coming back after a
          * week was told how many connections there were and not one of them.
          *
          * Ranked by the server, strongest first, which is the same order
          * `stepTarget` reads — so the thing the loop is about to act on is
          * visible here rather than only discoverable by pressing.
          *
          * Four, not ten. §09 warns off a radial dashboard, and a front door
          * that lists everything is a list screen wearing a summary's name;
          * the rest are one press away in Connections.
          */}
        {(map.top_connections?.length ?? 0) > 0 && (
          <section className="card">
            <h2>
              What this project has found
              <span className="note">
                strongest first · {map.top_connections.length} ranked
              </span>
            </h2>
            <ul className="ov-found">
              {map.top_connections.slice(0, 4).map((c) => (
                <li key={c.id}>
                  <button type="button" className="ov-found-row"
                          onClick={() => onOpen?.("connection", c.id)}>
                    <span className="ov-found-pair">
                      {(labels?.[c.left_variable] ?? c.left_variable)}
                      <span aria-hidden> ↔ </span>
                      {(labels?.[c.right_variable] ?? c.right_variable)}
                    </span>
                    <span className="ov-found-meta">
                      {/* The mark, not the pill: `Status` says "Checked —
                          survived the robustness checks", which in a four-row
                          summary is longer than the pair it describes and
                          pushed that pair into an ellipsis. */}
                      <StateMark value={c.lifecycle_status} />
                      {/* The effect, because a list of pairs with no size is a
                          list of names. Absent rather than zero where the
                          method records none. */}
                      {/* Named from the method, and only where that name is
                          certain. This read `effect_size_name`, which the
                          ranked list has never carried — `main`'s tightened
                          `TopConnection` type is what caught it — so every row
                          printed the fallback word "effect". A correlation's
                          effect is its coefficient; anything else keeps the
                          plain word rather than a symbol that might be wrong. */}
                      {c.effect_size != null && (
                        <span className="numeric">
                          {/^(pearson|spearman|kendall)/.test(c.method)
                            ? estimateSymbol(c.method) : "effect"}{" "}
                          {c.effect_size.toFixed(2)}
                        </span>
                      )}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
            {/* The overview is the first screen of the product, and until now
                the first number on it was a bare `r 0.60` beside a word like
                "exploratory" (T188). Both are named here, once, under the
                list they label. */}
            <TermList ids={["r", "lifecycle state"]} />
            {map.top_connections.length > 4 && (
              <button className="btn-text" type="button"
                      onClick={() => onGo("connections")}>
                All {map.top_connections.length} connections &rarr;
              </button>
            )}
          </section>
        )}
        <LifecycleBreakdown title="Connections" counts={map.connections} />
        <LifecycleBreakdown title="Findings" counts={map.findings} />
      </div>
      </div>

      {/*
        * The way into the lineage, offered where a reader has just been shown
        * counts and might reasonably ask how any of it was arrived at. One
        * sentence and one control: this is the Overview §09 asks for, "a
        * restrained overview, not a radial dashboard".
        */}
      {onLineage && (
        <p className="note">
          To see what was derived from what, and the branches that were tried and
          set aside,{" "}
          <button className="btn-text" type="button" onClick={onLineage}>
            follow the project&rsquo;s lineage
          </button>.
        </p>
      )}
    </>
  );
}

/**
 * What the rail calls a section.
 *
 * Read from `SECTIONS` rather than written out here, so a step's destination
 * cannot come to name a screen the rail no longer has — the failure being
 * avoided is a row promising "→ Findings" long after the entry was renamed,
 * which is unfalsifiable by eye and looks right in review.
 */
function sectionLabel(section: Section): string {
  return SECTIONS.find((entry) => entry.id === section)?.label ?? section;
}

/**
 * How many of a kind sit in each lifecycle state.
 *
 * A breakdown is a detail about a total that has already been stated on the
 * line above, so it opens in place rather than standing as a card of its own
 * (T139). The summary carries the number of states, which is the thing a
 * reader wants before deciding to look.
 */
function LifecycleBreakdown({ title, counts }: { title: string; counts: Record<string, number> }) {
  const entries = Object.entries(counts);
  if (!entries.length) return null;
  return (
    <Fold summary={`${title} by lifecycle state`} count={entries.length}>
      {/* `Term` prints its own gloss inline, so the sentence around it must
          not repeat the definition — it read "a lifecycle state — how far a
          result has got through validation is how far a result has got
          through validation". */}
      <p className="note" style={{ marginTop: 0 }}>
        Counted by <Term id="lifecycle state" />. A candidate is not a discovery:
        promotion is earned by the robustness checks, never asserted.
      </p>
      <div style={{ display: "flex", gap: 20, flexWrap: "wrap" }}>
        {entries.map(([state, count]) => (
          <div key={state}>
            <Status value={state} />
            <div className="numeric" style={{ fontSize: 17, fontWeight: 620 }}>{count}</div>
          </div>
        ))}
      </div>
    </Fold>
  );
}

// ---------------------------------------------------------------------------
// Sources (§24, §26)
// ---------------------------------------------------------------------------

/**
 * Whether a failed ingestion is a database somebody still has to choose from.
 *
 * Read out of the server's own refusal rather than invented here: a multi-table
 * file is refused with "This database holds N tables and a dataset is one
 * table, so which one to read is not something to guess: …"
 * (`packages/ingestion/src/throughline_ingestion/datasets.py:439-443`), and the
 * worker records that sentence verbatim as `ingestion_detail`
 * (`services/workers/src/throughline_workers/handlers.py:107-112`). The list
 * endpoint sends the field (`app.py:663`), so no second request is needed.
 *
 * Deliberately narrow. If that sentence is ever reworded the match fails and
 * the row shows one word less — it never shows the *wrong* word, and the full
 * refusal is still printed in the cell beside it, which is the failure mode to
 * choose when a client is reading prose the server wrote.
 */
function hasImportableTables(source: Source): boolean {
  return source.ingestion_status === "failed"
    && /database holds \d+ tables/i.test(source.ingestion_detail ?? "");
}

export function Sources({ sources, onSelect, upload, uploading, uploadError,
                         formats }: {
  /*
   * The list is owned by the workspace, not fetched here.
   *
   * This view used to call useApi for the same path the workspace already
   * fetched, which meant two independent copies of the same list and two
   * independent refresh paths. After an upload the workspace refreshed its copy
   * and this one kept showing the old rows: files landed on disk, were ingested,
   * and never appeared on screen. One owner, one refresh.
   */
  sources: ApiState<Source[]>;
  onSelect: (id: string) => void;
  upload: (files: FileList | null) => void;
  uploading: boolean;
  uploadError: unknown;
  /**
   * Which dataset formats this installation can read, from
   * `/api/system/capabilities`.
   *
   * Passed in rather than fetched: the workspace already asks for capabilities,
   * and a second fetch of the same thing is a second copy to keep in step —
   * the mistake this component's own header records making with the source
   * list.
   */
  formats?: DatasetFormats | null;
}) {
  const { data, error, loading, reload } = sources;

  /*
   * Ingestion is asynchronous, so the list has to move on its own or a source
   * sits at "queued" until the researcher thinks to refresh. Polling stops once
   * nothing is in flight — a workspace left open overnight should not keep
   * hitting the API.
   */
  const settling = (data ?? []).some((s) => isIngesting(s.ingestion_status));

  useEffect(() => {
    if (!settling && !uploading) return;
    const timer = setInterval(reload, 1500);
    return () => clearInterval(timer);
  }, [settling, uploading, reload]);

  return (
    <>
      <div className="row" style={{ marginBottom: 14 }}>
        <div>
          <h1>Sources</h1>
          {/* "treated as untrusted until parsed" is a security property said
              in the vocabulary of the people who built it (T188). What it
              means for the reader is that a file cannot do anything to this
              machine, which is worth saying in those words. */}
          <p style={{ margin: 0 }}>
            Papers and datasets. A file you add is opened in a sealed reader
            first: nothing inside it runs, and nothing it says is believed until
            it has been read out into the project.
          </p>
        </div>
        <label className={`btn${(sources.data?.length ?? 0) === 0 ? " btn-primary" : ""}`}
               style={{ display: "inline-block" }}>
          {uploading ? "Uploading…" : "Add sources"}
          <input
            /* Named so the one bar's "add a file" verb can press it: the bar
               navigates here and opens the chooser, rather than landing the
               researcher on a screen and leaving them to find the control
               (T189). */
            id="add-sources-input"
            type="file" multiple hidden disabled={uploading}
            /* Asked of the server rather than written down. The literal that
               was here offered eight formats while ingestion read twenty-two,
               so Stata, SPSS and SAS files were greyed out by a product that
               reads them. Undefined until the answer arrives, because a wrong
               filter hides a researcher's own data with no error to read. */
            accept={uploadAccept(formats)}
            onChange={(e) => upload(e.target.files)}
          />
        </label>
      </div>

      {/*
        * Named, not offered. A format that needs a package installed cannot be
        * read yet, so putting it in `accept` would produce a selection that
        * fails at ingestion with nothing to read. Saying it exists is the
        * honest middle, and it uses the server's own count rather than one
        * written here.
        */}
      {extrasNote(formats) !== null && (
        <p className="set-note" style={{ marginTop: 0 }}>{extrasNote(formats)}</p>
      )}

      {uploadError ? <Failure error={uploadError} /> : null}
      {error ? <Failure error={error} retry={reload} /> : null}
      {loading && !data && <Loading rows={4} label="Reading sources" />}

      {data && data.length === 0 && (
        <Empty
          title="No sources yet"
          hint="Drop files anywhere in this window, or use Add sources. A dataset is what discovery needs; a paper is what gives it context."
        />
      )}

      {data && data.length > 0 && (
        <>
        <table>
          <thead>
            <tr>
              <th style={{ width: "55%" }}>Source</th>
              <th style={{ width: "14%" }}>State</th>
              <th>What was extracted</th>
            </tr>
          </thead>
          <tbody>
            {data.map((source) => (
              <tr key={source.id} style={{ cursor: "pointer" }} onClick={() => onSelect(source.id)}>
                <td>
                  <button type="button" className="pick"
                          style={{ fontWeight: 540, wordBreak: "break-word" }}
                          onClick={() => onSelect(source.id)}>
                    {source.title}
                  </button>
                  {/* §35 — trust level travels with the source, not in its own column. */}
                  <span className="mono" style={{ color: "var(--ink-faint)" }}>
                    {source.source_type} · {source.trust_level}
                  </span>
                  {/*
                    Where an imported dataset came from (D211). The import has
                    always recorded the repository, the record's own URL and the
                    licence, and the one screen that lists sources showed none of
                    it — so provenance the product stores was invisible exactly
                    where a researcher decides whether to trust a row.

                    A link, because that is what a record at its origin is: the
                    claim "from Zenodo" is checkable only if it can be opened.
                    An uploaded file gets no line at all rather than "from —",
                    which would be a sentence about nothing.
                  */}
                  {source.repository && (
                    <span style={{ display: "block", fontSize: 11, color: "var(--ink-faint)" }}>
                      from{" "}
                      {/* The row opens the source and this link leaves for the
                          repository, so the click has to stop here: without
                          that, a reader who asked for the record at its origin
                          would also be moved to another screen — one press,
                          two answers (§123). Where there is no URL the name is
                          words rather than a control that goes nowhere. */}
                      {source.original_uri ? (
                        <a href={source.original_uri} target="_blank" rel="noreferrer"
                           onClick={(event) => event.stopPropagation()}>
                          {source.repository}
                        </a>
                      ) : source.repository}
                      {source.licence ? ` · ${source.licence}` : ""}
                    </span>
                  )}
                </td>
                <td>
                  <Status value={source.ingestion_status} />
                  {/* The bar shows position in the §24 pipeline, which the worker
                      genuinely reports. It is not a time estimate, and it never
                      invents a percentage from one. */}
                  {isIngesting(source.ingestion_status) && (
                    <Ingesting status={source.ingestion_status} />
                  )}
                  {/*
                    A failed ingestion that is really a choice waiting to be
                    made. Importing a table is reachable only from the detail
                    of a failed source, and the list gave no signal at all —
                    so the capability was discoverable only by opening the
                    failures one at a time (plan §4.8.2). One word, beside the
                    state it qualifies, and the refusal's own sentence is
                    already in the cell to the right of it.
                  */}
                  {hasImportableTables(source) && <Status value="importable" />}
                </td>
                <td style={{ color: "var(--ink-soft)" }}>
                  {isIngesting(source.ingestion_status) && (
                    <span style={{ color: "var(--ink-faint)" }}>
                      {source.ingestion_detail || "Waiting for a worker to pick it up…"}
                    </span>
                  )}
                  {source.dataset && (
                    <span className="numeric">
                      {source.dataset.row_count} rows · {source.dataset.column_count} columns
                    </span>
                  )}
                  {source.paper && (
                    <span className="numeric">
                      {source.passage_count ?? "?"} passages · {source.paper.page_count} pages
                    </span>
                  )}
                  {!source.dataset && !source.paper && (source.ingestion_detail || "—")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
          {/* "upload · untrusted" is a value in this table, not a warning
              (T188): every source starts untrusted and stays so until it has
              been parsed, which is the security property said in the reader's
              vocabulary rather than the builder's. */}
          <TermList ids={["untrusted"]} />
        </>
      )}
    </>
  );
}

/** What to call the thing on screen, so the sentence below is about it. */
const NOUN: Record<ObjectKind, string> = {
  finding: "finding",
  source: "source",
  analysis_run: "analysis",
};

/**
 * The history of the object a finding, a source or a run is recorded as
 * (D213).
 *
 * `<ObjectHistory>` takes an `obj_…` id and these three screens hold a finding
 * id, a source id and a run id, so Slice 2 mounted the history on the board's
 * card and left it off the details rather than mounting something that 404s.
 * The lookup closes that gap; this is the one place that joins it to the
 * panel, because three screens writing the same four lines is three places for
 * the absent case to be got wrong.
 *
 * Three states, and only one of them renders a heading:
 *
 *  - resolving: nothing. A heading over a spinner is a promise the lookup has
 *    not yet made good on.
 *  - resolved: the panel, headed and findable.
 *  - no such object: one quiet sentence saying so and why. The section is
 *    *not* rendered empty — an empty heading is a claim of absence dressed as
 *    a claim of presence, and a reader who scrolls to "History and versions"
 *    and finds nothing under it learns nothing about which of the two it is.
 */
export function ObjectHistoryFor({ projectId, kind, id, onOpenObject }: {
  projectId: string | null;
  kind: ObjectKind;
  id: string | null;
  /** Follow a lineage link, or land on what a restore just made. */
  onOpenObject?: (objectId: string) => void;
}) {
  const { objectId, loading, missing } = useObjectId(projectId, kind, id);

  if (loading) return null;
  if (missing) {
    return (
      <div style={{ marginTop: 20 }}>
        <p className="note one-line" style={{ margin: 0 }}>
          This {NOUN[kind]} has no history yet.
        </p>
        <Fold summary="Why there is no history" count={1}>
          <p className="note" style={{ marginTop: 0 }}>
            Notes and versions are kept on the research object a {NOUN[kind]}
            becomes, and anything recorded before this project kept those objects
            never had one — so there is nothing to show here rather than
            something lost.
          </p>
        </Fold>
      </div>
    );
  }
  if (!projectId || !objectId) return null;

  return (
    <ObjectHistory projectId={projectId} objectId={objectId}
                   onOpenObject={onOpenObject} />
  );
}

/**
 * What ingestion actually made of a file (§24, §26).
 *
 * Clicking a source used to do nothing, which taught the researcher that the
 * row was the whole truth. It is not: a dataset row hides a profiled schema, and
 * that profile is what every later method choice depends on.
 */
export function SourceDetail({ projectId, sourceId, onDiscover, onOpenSource,
                               onGo, labels, onOpenObject, onImported }: {
  projectId: string;
  sourceId: string;
  onDiscover: (datasetVersionId: string) => void;
  /**
   * Open the source an imported table became (D205, plan §4.8.1).
   *
   * `DatabaseTables` has always returned the new source's id through
   * `onImported` and nothing ever supplied the handler, so importing a table
   * left the researcher standing on a *failed* source with no route to the
   * dataset they had just made — the third instance of the defect
   * `board/CardDetail.tsx:6-9` names by hand.
   *
   * Optional, because a host with nowhere to send a click is better off with
   * the plain "Imported" the table row already shows than with a jump that
   * goes nowhere.
   */
  onOpenSource?: (sourceId: string) => void;
  /**
   * Open a section of the workspace — only ever `"variables"` from here.
   *
   * Typed to the one section this screen links to rather than to `Section`, so
   * the bridge below cannot quietly become a second navigation surface.
   */
  onGo?: (section: "variables") => void;
  /**
   * Approved display names by raw column name, exactly as
   * `GET /api/projects/{id}/variables` sends them — and the route is explicit
   * that only approved labels appear there ("Only approved labels are used
   * anywhere. An unreviewed suggestion changes nothing on screen").
   *
   * Passed in rather than fetched here because the workspace already holds
   * this request (`page.tsx:435-436`), and two hooks on one path are two
   * copies that drift — the note on `Sources` above is the same rule.
   */
  labels?: Record<string, string>;
  /**
   * Open a research object — a lineage link in this source's history, or the
   * object a restore just brought forward (D213).
   *
   * Optional for the reason `onOpenSource` above is: the history is worth
   * reading on a host with nowhere to send a click, and `NodeJournal` renders
   * a lineage name as plain text rather than a dead control where the handler
   * is absent.
   */
  onOpenObject?: (objectId: string) => void;
  /**
   * A table was imported, which creates a *new* source.
   *
   * `DatabaseTables` has always offered this and nothing had ever passed it,
   * so the list of sources the researcher is looking at went stale the moment
   * they imported one: the dataset they just made was not in it. Uploading a
   * file creates a source the same way and the workspace already reloads for
   * that — this is the same gesture reaching the same handler.
   */
  onImported?: (newSourceId: string) => void;
}) {
  const { data, error, loading, reload } =
    useApi<Source>(`/api/projects/${projectId}/sources/${sourceId}`);
  const columns = useApi<DatasetColumn[]>(
    data?.dataset ? `/api/dataset-versions/${data.dataset.dataset_version_id}/columns` : null,
  );

  /*
   * Which profiled column a subset sent us to, and what to say when it sent us
   * nowhere.
   *
   * The highlight is a word as well as a background (§118): a row lit only by
   * colour tells a reader who cannot see the colour nothing at all, so the
   * sentence under the table names the column and the subset that pointed at
   * it.
   */
  const [jumpedTo, setJumpedTo] = useState<
    { column: string; subset: string } | null>(null);
  const [jumpFailed, setJumpFailed] = useState<string | null>(null);
  const jumpedRow = useRef<HTMLTableRowElement | null>(null);

  /*
   * Take the reader to the highlighted row once it exists.
   *
   * After the state change rather than inside the handler, because the row is
   * only marked — and therefore only `tabIndex={-1}` and focusable — on the
   * render that follows. `scrollIntoView` is checked rather than called, for
   * the reason `reveal` below records: the test environments have no layout
   * engine and a missing method would take the whole screen down.
   */
  useEffect(() => {
    const row = jumpedRow.current;
    if (!jumpedTo || !row) return;
    if (typeof row.scrollIntoView === "function") row.scrollIntoView({ block: "center" });
    // §30 — the keyboard goes where the pointer went, or the next Tab
    // continues from the top of the document past everything just skipped.
    row.focus();
  }, [jumpedTo]);

  if (error) return <Failure error={error} retry={reload} />;
  if (loading || !data) return <Loading rows={5} label="Reading the source" />;

  /*
   * How many profiled columns still have no approved label, and how many there
   * are — a count of what is on this screen, not a statistic.
   *
   * "Nothing is computed in the browser" is about numbers that describe the
   * data: an average, a share, a q-value. This is a count of rows in a table
   * the reader is looking at against a map the server sent, and computing it
   * on the server would mean a request whose answer is already here twice
   * over. Nothing about the dataset is being measured.
   */
  const profiled = columns.data ?? [];
  const unlabelled = labels
    ? profiled.filter((column) => !labels[column.name]).length
    : null;

  return (
    <>
      <h1 style={{ wordBreak: "break-word" }}>{data.title}</h1>
      {/*
        * Withdrawn upstream, said first.
        *
        * Above the ingestion status and above the injection signals, because it
        * outranks both: a source that has been retracted changes what every
        * finding resting on it is worth, and a reader who has opened this page
        * is deciding whether to rest something on it now. Harvesting marks a
        * source withdrawn rather than deleting it — the reference and the
        * evidence of withdrawal both have to survive — and the harvest report
        * says so once, at harvest time, to whoever happened to be looking.
        * This is the same fact where it is needed.
        */}
      {data.withdrawn_at != null && (
        <div className="withdrawn" role="alert">
          <strong>This source has been withdrawn upstream.</strong>
          <p>
            {data.withdrawn_reason
              ? `Reason given: ${data.withdrawn_reason}`
              : "No reason was given."}{" "}
            It is kept rather than deleted, so that anything resting on it can
            still be found — but nothing new should.
          </p>
        </div>
      )}

      <div className="row" style={{ marginBottom: 16 }}>
        <Status value={data.ingestion_status} />
        <span className="mono" style={{ color: "var(--ink-faint)" }}>
          {data.source_type} · trust: {data.trust_level}
        </span>
      </div>

      {data.ingestion_status === "failed" && (
        <div className="error">{data.ingestion_detail || "Ingestion failed with no detail recorded."}</div>
      )}

      {/*
        Text in this document addressed to an AI system.
        
        Shown near the top, because it changes how a reader should treat the
        whole source, and stated as a fact about the paper rather than as an
        alarm about the platform: nothing was blocked, nothing was edited, and
        the content was already fenced before any model saw it. The phrases are
        quoted so the researcher can judge them — a hidden instruction in a
        preprint is often the most interesting thing about it.
      */}
      {(data.metadata?.injection_signals?.length ?? 0) > 0 && (
        <section className="talkstomachine">
          <h2>This document contains text addressed to an AI system</h2>
          <p className="lede">
            Found while reading it. Nothing was blocked or removed, and no model
            has acted on it — retrieved content is fenced as data before it
            reaches one. It is shown because it is a fact about this source.
          </p>
          <ul>
            {data.metadata!.injection_signals!.map((phrase) => (
              <li key={phrase}><q>{phrase}</q></li>
            ))}
          </ul>
          <p className="note">
            Text like this in a paper is usually aimed at automated review or
            summarisation. Worth knowing before citing it.
          </p>
        </section>
      )}

      {data.paper?.metadata?.columns === "spliced" && (
        /*
         * Said for the same reason the injection notice above is: it is a fact
         * about this source that bears on whether a quotation from it can be
         * trusted, and the researcher is the only one who can judge what to do
         * about it.
         *
         * The parser reads a two-column paper column by column precisely
         * because a whole-page read interleaves them and splices unrelated
         * sentences together. On these pages it could not find the split, so
         * it fell back to the whole-page read — and a spliced sentence looks
         * exactly like a real one. The pages are named because "somewhere in
         * this paper" is not something anybody can act on.
         */
        <section className="talkstomachine">
          <h2>Some pages of this paper were read straight across</h2>
          <p className="lede">
            This paper is read column by column, because reading a two-column
            page straight across interleaves the columns and joins the end of
            one sentence to the middle of another. On{" "}
            {data.paper.metadata.spliced_pages?.length === 1 ? "page" : "pages"}{" "}
            {(data.paper.metadata.spliced_pages ?? []).join(", ")} the columns
            could not be told apart, so those pages were read straight across.
          </p>
          <p className="note">
            A quotation taken from{" "}
            {data.paper.metadata.spliced_pages?.length === 1
              ? "that page" : "those pages"}{" "}
            may join text that is not next to itself in the paper. Worth
            checking against the original before citing it. Every other page is
            unaffected.
          </p>
        </section>
      )}

      {/*
        A database that could not be ingested as one dataset is the case this
        answers: the message above says which tables it holds, and this is how
        one of them is chosen. It renders nothing for a source that is not a
        database, so it costs an ordinary failed ingestion nothing.
      */}
      {data.ingestion_status === "failed" && (
        /*
          The handler that was declared and never passed (D205). Importing a
          table creates a source of its own and returns its id: the list beside
          this one is stale until it is re-read, and the researcher was left on
          the failed database with nothing naming where the rows had gone. Both
          are answered here, in that order — reload, then follow.
        */
        <DatabaseTables projectId={projectId} sourceId={sourceId}
                        onImported={(newSourceId) => {
                          onImported?.(newSourceId);
                          onOpenSource?.(newSourceId);
                        }} />
      )}

      {data.paper && (
        <div className="grid-2" style={{ marginBottom: 16 }}>
          <Stat label="pages" one="page" value={data.paper.page_count} />
          <Stat label="passages indexed" one="passage indexed" value={data.passage_count ?? 0} />
        </div>
      )}

      {data.dataset && (
        <>
          <div className="grid-2" style={{ marginBottom: 16 }}>
            <Stat label="rows" one="row" value={data.dataset.row_count} />
            <Stat label="columns" one="column" value={data.dataset.column_count} />
            <Stat label="version" value={data.dataset.version} />
          </div>

          <div className="card">
            <div className="row" style={{ marginBottom: 8 }}>
              <h2 style={{ margin: 0 }}>
                Profiled <Term id="schema" />
              </h2>
              <button
                className="btn btn-primary"
                onClick={() => onDiscover(data.dataset!.dataset_version_id)}
              >
                Discover connections
              </button>
            </div>
            <p style={{ marginTop: 0 }}>
              Every candidate relationship, and every method chosen to test one, follows
              from these types.
            </p>

            {/*
              The subsetting decisions behind any number computed from this
              dataset. Above the schema because the chain determines which rows
              every column statistic below is about — a reader who meets the
              profile first has already been told a number without being told
              what it counted.
            */}
            <CohortTree
              projectId={projectId}
              datasetVersionId={data.dataset.dataset_version_id}
              /*
                The other handler D205 names. A subset is a range on a column,
                so the thing to show a reader who presses one is the profiled
                column it was drawn on — which is on this same screen, a few
                hundred pixels down.

                Supplied only once the profile has arrived: with no schema
                below there is nowhere to jump to, and the tree renders the
                names as text rather than as controls that do nothing.
              */
              onSelect={profiled.length === 0 ? undefined : (subset) => {
                const named = subset.columns.find(
                  (name) => profiled.some((column) => column.name === name));
                if (!named) {
                  // Stated, not swallowed. A subset drawn on a column this
                  // version no longer profiles is a real fact about the
                  // chain, and silence would read as a broken control.
                  setJumpedTo(null);
                  setJumpFailed(
                    subset.columns.length === 0
                      ? `“${subset.name}” does not record which column it was drawn on, so there is no row here to show you.`
                      : `“${subset.name}” was drawn on ${subset.columns.join(", ")}, which this profile does not have.`);
                  return;
                }
                setJumpFailed(null);
                setJumpedTo({ column: named, subset: subset.name });
              }}
            />

            {columns.loading && <Loading rows={4} label="Reading the profile" />}
            {columns.error ? <Failure error={columns.error} retry={columns.reload} /> : null}
            {columns.data && (
              <table>
                <thead>
                  <tr>
                    <th>Column</th><th>Type</th><th style={{ textAlign: "right" }}>Missing</th>
                    <th style={{ textAlign: "right" }}>Distinct</th><th>Sensitivity</th>
                  </tr>
                </thead>
                <tbody>
                  {columns.data.map((column) => {
                    /* What the profiler noticed, which it has always recorded
                       and never shown. A -999 standing for "missing" is in
                       every average until somebody is told about it. */
                    const notices = columnNotices(
                      column.statistics, column.physical_type);
                    /* Lit because a subset above points at it. */
                    const lit = jumpedTo?.column === column.name;
                    return (
                    <Fragment key={column.name}>
                    <tr
                      ref={lit ? jumpedRow : undefined}
                      tabIndex={lit ? -1 : undefined}
                      style={lit ? { background: "var(--hover)" } : undefined}
                    >
                      <td className="mono" style={{ color: "var(--ink)" }}>{column.name}</td>
                      <td style={{ color: "var(--ink-soft)" }}>
                        {column.semantic_type || column.physical_type}
                        {column.unit ? ` · ${column.unit}` : ""}
                      </td>
                      <td className="numeric" style={{ textAlign: "right" }}>{column.missing_count}</td>
                      <td className="numeric" style={{ textAlign: "right" }}>{column.unique_count}</td>
                      <td style={{ color: "var(--ink-soft)" }}>{column.sensitivity}</td>
                    </tr>
                    {notices.map((notice, index) => (
                      <tr key={`${column.name}-notice-${index}`}>
                        <td colSpan={5} style={{ paddingTop: 0 }}>
                          <p
                            className="note"
                            style={{
                              margin: 0, fontSize: 12,
                              color: notice.level === "warn"
                                ? "var(--caution)" : "var(--ink-faint)",
                            }}
                          >
                            {notice.text}
                          </p>
                        </td>
                      </tr>
                    ))}
                    </Fragment>
                    );
                  })}
                </tbody>
              </table>
            )}

            {/*
              What the jump did, in words. §118 — the row above is lit, and a
              highlight that is only a background says nothing to a reader who
              cannot see it.
            */}
            {jumpedTo && (
              <p className="note" style={{ marginTop: 8 }}>
                Showing <span className="mono">{jumpedTo.column}</span>, the
                column “{jumpedTo.subset}” is drawn on.
              </p>
            )}
            {jumpFailed && <p className="note" style={{ marginTop: 8 }}>{jumpFailed}</p>}

            {/*
              The bridge to Variables, from the schema that needs it (plan
              §4.8.3). Variables is the reason a chart stops being titled
              `resistance_pct` (`Shell.tsx:56-63`), and nothing anywhere linked
              forward to it from the data it describes.

              A route, not a duplicated control: the approve/reject cards stay
              on Variables, where the alias dropdown is built from the
              variables the project actually has (`variables.tsx:317-320`).
            */}
            {unlabelled !== null && profiled.length > 0 && (
              <p className="note" style={{ marginTop: 10 }}>
                {unlabelled === 0
                  ? `Every one of these ${profiled.length} columns has an approved label.`
                  : `${unlabelled} of ${profiled.length} columns have no approved label.`}
                {" "}
                {/* §123 — offered as a control only where there is somewhere
                    to send it. Where there is not, the count still stands and
                    the sentence names the screen instead. */}
                {onGo ? (
                  <button type="button" className="pick"
                          style={{ display: "inline", width: "auto" }}
                          onClick={() => onGo("variables")}>
                    {unlabelled === 0 ? "See them on Variables →" : "Review them →"}
                  </button>
                ) : "They are reviewed on the Variables screen."}
              </p>
            )}
          </div>
        </>
      )}

      {!data.paper && !data.dataset && data.ingestion_status === "ready" && (
        <Empty
          title="Nothing structured was extracted"
          hint="The file was read, but it produced neither a paper nor a dataset."
        />
      )}

      {/*
        Last, because it is the record of the screen above it rather than part
        of it: what was written about this source and what it used to say
        (D213, plan §4.6.2).
      */}
      <ObjectHistoryFor projectId={projectId} kind="source" id={sourceId}
                        onOpenObject={onOpenObject} />
    </>
  );
}

/** Where a source has got to in the §24 pipeline, stated rather than spun. */
function Ingesting({ status }: { status: string }) {
  const step = ingestionStep(status);
  const total = INGESTION_STAGES.length;
  return (
    <span
      className="progress"
      role="progressbar"
      aria-valuemin={1}
      aria-valuemax={total}
      aria-valuenow={step ?? undefined}
      aria-label={`Ingesting: ${status}`}
      style={step ? { ["--at" as string]: `${(step / total) * 100}%` } : undefined}
      data-known={step !== null}
      title={step ? `${status} — step ${step} of ${total}` : status}
    />
  );
}

// ---------------------------------------------------------------------------
// Search (§29, §30)
// ---------------------------------------------------------------------------

/**
 * One retrieval event, and the passages the answer was built from.
 *
 * Typed here rather than in `lib/api.ts` because this is the only caller.
 * `retrieval.retrieval_provenance` is the authority: it returns the
 * `retrieval_events` row itself plus a `results` list joined to the passages
 * and their sources
 * (`packages/research-domain/src/throughline_domain/retrieval.py:170-189`).
 */
type RetrievalAudit = {
  id: string;
  project_id: string;
  query: string;
  strategy: string;
  filters: Record<string, unknown>;
  result_count: number;
  created_at: string;
  results: Array<{
    rank: number;
    passage_id: string;
    source_id: string;
    /** The document's title — the one thing the hit list above cannot show. */
    source_title: string;
    content: string;
    locator: string;
    page: number | null;
    section: string;
    lexical_score: number | null;
    semantic_score: number | null;
    fused_score: number;
    rerank_score: number | null;
    char_start: number | null;
    char_end: number | null;
  }>;
};

export function Search({ projectId, onOpenSource, initialQuery }: {
  projectId: string;
  /** A phrase the bar carried in — "search my library for maize" (T198). */
  initialQuery?: string;
  /**
   * Open the source a passage came from. Without this a hit was a dead end:
   * rank, locator and scores, and no way to the document (D203). A search
   * that cannot be followed back to its passage is a citation nobody can
   * check.
   */
  onOpenSource?: (sourceId: string) => void;
}) {
  const [query, setQuery] = useState(initialQuery ?? "");
  const [submitted, setSubmitted] = useState<string | null>(null);
  const path = submitted ? `/api/projects/${projectId}/search?q=${encodeURIComponent(submitted)}&limit=12` : null;
  const { data, error, loading, reload } = useApi<SearchResult>(path);

  /*
   * Whether the passage audit has been asked for (D203, plan §4.9.1).
   *
   * `GET /api/retrievals/{event_id}` is the route that answers "which passages
   * was this built from", and it had no caller anywhere in `apps/web` — the
   * inventory's one dead end by omission rather than by design. It is fetched
   * on opening rather than with the search, because every search would
   * otherwise pay for an audit almost nobody opens.
   *
   * Set and never unset: closing the disclosure is not a reason to throw the
   * answer away and fetch it again on the next open.
   */
  const [auditing, setAuditing] = useState(false);
  const eventId = data?.retrieval_event_id ?? null;
  const audit = useApi<RetrievalAudit>(
    auditing && eventId ? `/api/retrievals/${eventId}` : null);

  /*
   * A new search is a new event, so the panel goes back to unasked.
   *
   * Without this the previous search's passages would sit under this search's
   * summary — a provenance panel showing the provenance of something else,
   * which is worse than showing none.
   */
  useEffect(() => { setAuditing(false); }, [eventId]);

  return (
    <>
      <h1>Search sources</h1>
      <p className="lede">
        Searches the sources already in this project — keyword and meaning
        together. Every search is recorded, so an answer built on one can be
        traced back to the passages it came from. To bring in something the
        project does not have yet, use Find papers or Find data.
      </p>

      <form
        onSubmit={(e) => { e.preventDefault(); setSubmitted(query.trim() || null); }}
        style={{ display: "flex", gap: 8, marginBottom: 16 }}
      >
        <input
          type="text" value={query} onChange={(e) => setQuery(e.target.value)}
          placeholder="e.g. how many people took part in the trial"
          aria-label="Search the sources in this project"
        />
        {/* Plain: Search is not a loop destination, so the strip above always
            holds the filled control (T139). */}
        <button className="btn" type="submit" disabled={!query.trim()}>Search</button>
      </form>

      {error ? <Failure error={error} retry={reload} /> : null}
      {loading && <Loading rows={3} label="Retrieving passages" />}

      {data && (
        <>
          {/*
            One sentence, then two disclosures that each name their own
            contents (D203, plan §4.9.1).

            What was here before was a single grey line of five machine facts
            ending in a bare `ret_…` id — the one identifier printed beside
            every search, and the one thing on the screen that could not be
            opened. The scope sentence is what a reader actually needs first;
            the strategy and the two candidate counts are still visible in a
            summary rather than folded away under a label that does not
            mention them, which is `ChartTable`'s law (`ChartTable.tsx:1-21`)
            and this plan's principle 4.

            The claim is exact: the view sends no `source_id` filter, so
            `hybrid_search` runs over every passage in the project
            (`app.py:931-943`).
          */}
          <p style={{ margin: "0 0 10px" }}>Searched every passage in this project.</p>

          {/* `onToggle` on the details, not a click on the summary: the
              audit is fetched however the disclosure is opened — pointer,
              keyboard or assistive technology — and the summary stays the
              native control it already is (§30). */}
          <details className="disclosure" style={{ marginBottom: 6 }}
                   onToggle={(e) => { if (e.currentTarget.open) setAuditing(true); }}>
            <summary>
              Which passages this search was built from
            </summary>

            <div style={{ margin: "8px 0 0 4px" }}>
              {/* The raw id, kept where it can be quoted. It is what a
                  methods section cites when it says an answer was built from
                  a recorded retrieval. */}
              <p className="note" style={{ margin: "0 0 8px" }}>
                Recorded as retrieval event{" "}
                <span className="mono">{eventId ?? "—"}</span>. Every passage
                below was stored with its rank and both scores, so this list is
                the record rather than a repeat of the search.
              </p>

              {audit.error ? <Failure error={audit.error} retry={audit.reload} /> : null}
              {audit.loading && <Loading rows={3} label="Reading the retrieval record" />}
              {/*
                Guarded on the event id: `useApi` keeps the last body when its
                path goes null, and rendering the previous search's passages
                here would be exactly the mislabelling this panel exists to
                prevent.
              */}
              {audit.data && audit.data.id === eventId && (
                audit.data.results.length === 0 ? (
                  <Empty
                    title="This retrieval recorded no passages"
                    hint="The search ran and matched nothing, which is a different fact from the record being missing."
                  />
                ) : (
                  <ol style={{ margin: 0, paddingLeft: "1.2rem" }}>
                    {audit.data.results.map((row) => (
                      <li key={row.passage_id} style={{ marginBottom: 8 }}>
                        {/* The document's title, which the hit list above
                            does not carry at all — a passage nobody can name
                            the source of is not a citation. */}
                        <div style={{ fontWeight: 540 }}>{row.source_title}</div>
                        <div className="mono" style={{ color: "var(--ink-faint)" }}>
                          #{row.rank} · {row.locator}
                          {row.section ? ` · ${row.section}` : ""} ·{" "}
                          <span className="numeric">{row.passage_id}</span>
                        </div>
                        <div style={{ color: "var(--ink-soft)", fontSize: 12.5 }}>
                          {row.content.slice(0, 240)}
                        </div>
                      </li>
                    ))}
                  </ol>
                )
              )}
            </div>
          </details>

          <details style={{ marginBottom: 14 }}>
            <summary className="pick"
                     style={{ display: "list-item", width: "auto", cursor: "pointer" }}>
              How this search ran: {data.strategy}, {data.lexical_candidates} lexical
              and {data.semantic_candidates} semantic candidates
            </summary>
            <div className="note" style={{ margin: "8px 0 0 4px" }}>
              {/*
                Every sentence here is a fact about `retrieval.hybrid_search`
                (`retrieval.py:92-146`) and its module header, not a
                description of search in general.
              */}
              <p style={{ marginTop: 0 }}>
                {data.strategy === "hybrid"
                  ? "Hybrid: the keyword index and the meaning index were both asked, and the two rankings were fused."
                  : `Strategy “${data.strategy}”: only the keyword index answered. Semantic search returns nothing when no embedding model is installed, and the strategy names what actually ran rather than what was intended.`}
              </p>
              <p>
                {data.lexical_candidates} passages came back from the keyword
                index and {data.semantic_candidates} from the meaning index.
                A passage can be in both counts. Those are the candidates
                considered; {data.results.length} are listed below.
              </p>
              <p style={{ marginBottom: 0 }}>
                The two are combined by rank, not by score: a keyword rank and
                a cosine similarity are not on a common scale, and normalising
                one against the other would invent a comparability that does
                not exist.
              </p>
            </div>
          </details>

          {data.results.length === 0 && <Empty title="Nothing matched" hint="Try different words, or add more sources." />}

          {/*
            Why there is no "Take this passage" here, stated rather than left
            as an absence (plan §4.9.3, principle 7).

            The board Find papers keeps is the §205 excerpt board, and the
            server refuses an excerpt that cannot fill §205's list — source
            paper, page, **bounding region**, citation, original context
            (`throughline_domain/excerpts.py:44-77`, and `region` is required
            by `ExcerptRequest` at `app.py:1427-1441`). A search hit is text
            the index matched: `SearchResult.results` carries no region and
            the `passages` table stores none, so a passage taken from here
            could only be stored by inventing coordinates. An excerpt on the
            board with an invented region is exactly the orphan §205 exists to
            keep off it. So this says so, in place, rather than offering a
            control that would either fail or lie.
          */}
          {data.results.length > 0 && (
            <p className="note" style={{ marginBottom: 12 }}>
              <b>Taking a passage.</b> The board on Find papers keeps a piece of
              a paper with everything a citation needs — the document, the page,
              the region circled on it, the citation and the words around it. A
              passage found here is matched text with no circled region, and an
              excerpt stored without one would sit on the board looking exactly
              like one that can be traced. So nothing here writes to that board;
              open the source to read the passage in place.
            </p>
          )}

          {data.results.map((hit) => (
            <div className="card card-tight" key={hit.passage_id}>
              <div className="row" style={{ marginBottom: 5 }}>
                <span className="mono" style={{ color: "var(--ink-faint)" }}>
                  #{hit.rank} · {hit.locator}{hit.section ? ` · ${hit.section}` : ""}
                </span>
                <span className="mono" style={{ color: "var(--ink-faint)" }}>
                  lex <Num value={hit.lexical_score} digits={3} /> · sem <Num value={hit.semantic_score} digits={3} />
                </span>
              </div>
              <div className="serif" style={{ fontSize: 13.5 }}>{hit.content.slice(0, 420)}</div>
              {/* A real control rather than a clickable card: the card holds
                  a paragraph of the passage, and a paragraph that is also a
                  button is a trap for the keyboard and the screen reader. */}
              {onOpenSource && (
                <button type="button" className="pick" style={{ marginTop: 8 }}
                        onClick={() => onOpenSource(hit.source_id)}>
                  Open the source →
                </button>
              )}
            </div>
          ))}
        </>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Discovery (§48, §49)
// ---------------------------------------------------------------------------

export function Discover({ projectId, sources, onSelectConnection, startWith,
                          onStarted, connectionTotal }: {
  projectId: string;
  /** Owned by the workspace — see the note on Sources. */
  sources: ApiState<Source[]>;
  onSelectConnection: (id: string) => void;
  /** Set when the researcher pressed "Discover connections" on a source. */
  startWith?: string | null;
  onStarted?: () => void;
  /**
   * How many connections this project has, for the table's truncation line.
   *
   * This screen's list is capped at 100 and said so nowhere (D201). The count
   * comes from the workspace's discovery map rather than from a second
   * request, and it is optional so a caller that does not have it makes the
   * table say nothing rather than guess.
   */
  connectionTotal?: number;
}) {
  const connections = useApi<Connection[]>(`/api/projects/${projectId}/connections?limit=100`);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [reused, setReused] = useState<string | null>(null);
  /*
   * Whether to stop the sweep before it writes anything into the project.
   *
   * Rule 10 — "AI does not secretly mutate important research state." The
   * tests run either way; what waits is the half that records connections and
   * promotes the survivors, so the results can be read before they become part
   * of the project's record rather than after.
   *
   * Off by default, and that is a decision rather than an oversight: a sweep
   * that stops on a fresh install leaves a new researcher looking at an empty
   * table wondering what went wrong.
   */
  const [hold, setHold] = useState(false);
  const [approvals, setApprovals] = useState(0);
  /** The sweep just run, so it can account for itself. */
  const [sweep, setSweep] = useState<string | null>(null);

  const datasets = (sources.data ?? []).filter((s) => s.dataset);

  /*
   * Carry the intent across the navigation, so pressing the button on a source
   * starts the run instead of landing on a screen with the same button again.
   *
   * The ref is load-bearing, not defensive noise: React's development StrictMode
   * mounts every component twice, which fires this effect twice and submitted
   * two discovery runs. The server now refuses the duplicate as well, but the
   * client should not be sending it.
   */
  const started = useRef<string | null>(null);
  useEffect(() => {
    if (!startWith || started.current === startWith) return;
    started.current = startWith;
    onStarted?.();
    void discover(startWith);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [startWith]);

  async function discover(versionId: string, force = false) {
    setRunning(true);
    setError(null);
    setReused(null);
    try {
      const started = await api.post<{ reused: boolean; note?: string;
                                       discovery_run_id: string }>(
        `/api/projects/${projectId}/discoveries`,
        // The session travels with the request so the sweep joins the family
        // of everything else looked at in this sitting. Null in a private
        // window, where storage is refused — the run is then its own family,
        // which is what happened before any of this existed.
        // No family is sent. The server resolves the project's open line of
        // enquiry, which is the same answer this used to compute from a UUID in
        // `sessionStorage` — except that it survives the tab and the researcher
        // can see what it is.
        { dataset_version_id: versionId, force,
          hold_before_recording: hold },
      );
      // §123 — if the server declined to start a second run, say so. A button
      // that appears to work and quietly does nothing is worse than an error.
      if (started.reused) {
        setReused(started.note ?? "A run already exists for this dataset version.");
        setSweep(started.discovery_run_id);
        connections.reload();
        return;
      }
      /*
       * Kept, where it used to be discarded.
       *
       * The run records what the sweep actually did — how many pairs it
       * considered, how many it dropped and why, how many it tested — and none
       * of that was reachable without the id. It is the denominator: a q-value
       * means nothing without the number of tests it was corrected across.
       */
      setSweep(started.discovery_run_id);
      for (let i = 0; i < 16; i++) {
        await new Promise((r) => setTimeout(r, 2000));
        connections.reload();
        // A held run records nothing, so the connections table stays empty on
        // purpose. Without this the sweep would look like one that found
        // nothing, and the thing actually waiting would be off screen.
        if (hold) setApprovals((n) => n + 1);
      }
    } catch (err) {
      setError(err);
    } finally {
      setRunning(false);
    }
  }

  return (
    <>
      <h1>Discovery</h1>
      {/* The one sentence naming this screen used two words a first-timer has
          not met — "profiled schema" and "sandbox" — to explain a third,
          "candidate" (T188). Every pair of columns is tried, which is the part
          that makes the correction below necessary, so it is said here. */}
      <p className="lede">
        Every pair of columns in the dataset is tried against every other, and
        each pair that is worth testing is tested in the{" "}
        <Term id="sandbox" />.
      </p>
      <Fold summary="What happens to a candidate here" count={2}>
        <p className="note" style={{ marginTop: 0 }}>
          Every test is corrected for how many tests ran, so a q-value on this
          screen already knows the size of the family it came from.
        </p>
        <p className="note">
          Rejected candidates stay visible — they were tested, they are simply
          not discoveries.
        </p>
      </Fold>

      {error ? <Failure error={error} /> : null}

      <Approvals
        key={approvals}
        projectId={projectId}
        onReleased={() => connections.reload()}
      />

      {reused && (
        <div className="notice" role="status">
          <span>{reused}</span>
          <button
            className="btn"
            onClick={() => {
              const only = datasets[0];
              if (only) void discover(only.dataset!.dataset_version_id, true);
            }}
          >
            Run it again anyway
          </button>
        </div>
      )}

      {datasets.length === 0 && (
        <Empty title="No dataset to search" hint="Discovery needs tabular data. Add a CSV or spreadsheet." />
      )}

      {/* Label beside its box. `.row` spreads its children to either end,
          which put the checkbox at the left edge of the column and the
          sentence that names it at the right — a control and its label a
          page apart. */}
      {datasets.length > 0 && (
        <label className="row" style={{ gap: "0.5rem", alignItems: "center", justifyContent: "flex-start" }}>
          <input
            type="checkbox"
            checked={hold}
            onChange={(event) => setHold(event.target.checked)}
          />
          {/* The choice, in one clause. What it does not change — the tests
              still run — is the reassurance, and it folds beneath. */}
          <span>Show me the results before anything is recorded.</span>
        </label>
      )}
      {datasets.length > 0 && (
        <Fold summary="What holding the results back does not stop" count={1}>
          <p className="note" style={{ margin: 0 }}>
            The tests still run; nothing enters the project until you release it.
          </p>
        </Fold>
      )}

      {datasets.map((source) => (
        <div className="card row" key={source.id}>
          <div>
            <div style={{ fontWeight: 540 }}>{source.title}</div>
            <div className="mono" style={{ color: "var(--ink-faint)" }}>
              {source.dataset!.row_count} rows · {source.dataset!.column_count} columns
            </div>
          </div>
          {/* Primary only while nothing has been discovered yet: after that
              the strip above carries the loop's action, and two gold buttons
              are two claims about what to do next. */}
          <button
            className={`btn${(connections.data?.length ?? 0) === 0 ? " btn-primary" : ""}`}
            disabled={running}
            onClick={() => discover(source.dataset!.dataset_version_id)}
          >
            {running ? "Testing candidates…" : "Discover connections"}
          </button>
        </div>
      ))}

      {running && <Loading rows={2} label="Generating candidates, running tests, correcting for multiple testing" />}

      {sweep && <WhatTheSweepDid runId={sweep} />}

      <ConnectionsTable
        connections={connections.data} error={connections.error}
        loading={connections.loading} reload={connections.reload}
        onSelect={onSelectConnection}
        total={connectionTotal}
      />
    </>
  );
}

export function ConnectionsTable({ connections, error, loading, reload, onSelect,
                                  total }: {
  connections: Connection[] | null;
  error: unknown; loading: boolean; reload: () => void;
  onSelect: (id: string) => void;
  /**
   * How many connections this project has, against however many are shown.
   *
   * Both lists that draw this table are capped — 100 on Discovery, 200 on
   * Connections — and neither said so, while `ChartTable` one directory away
   * discloses truncation in its *closed* summary (`ChartTable.tsx:74-78`).
   * D201: a table that quietly shows the first 200 rows is a table that lies
   * about the data.
   *
   * The total comes from the caller because only the caller has it: the
   * discovery map already counts every connection by lifecycle state
   * (`DiscoveryMap.connections`), so no new request is needed and this
   * component still computes nothing. Optional, because a caller that does not
   * know the total must not be made to invent one — and where it is absent the
   * table says nothing rather than guessing.
   */
  total?: number;
}) {
  if (error) return <Failure error={error} retry={reload} />;
  if (loading && !connections) return <Loading rows={4} label="Reading connections" />;
  if (!connections?.length) {
    return <Empty title="No connections yet" hint="Run discovery on a dataset to generate candidates." />;
  }

  return (
    <div style={{ marginTop: 18 }}>
      {/*
        Truncation, disclosed above the rows rather than discovered by
        counting them (D201).
      */}
      {total !== undefined && connections.length < total && (
        <p className="note" style={{ margin: "0 0 8px" }}>
          Showing the first {connections.length} of {total}.
        </p>
      )}

      {/*
        The correction, as the table's caption rather than as a footnote
        (plan §4.10.1).

        A first-timer reading top to bottom met 7.44e-39 in the q-value column
        before anything on the screen said what a q-value is or what it was
        corrected across — three separate files state that a q-value means
        nothing without the number of tests it was corrected over
        (`sweep.tsx:12-13`, `ledger.tsx:6-9`), and this one stated it *after*
        the table. The gloss carries the meaning ("corrected for how many tests
        ran") before the method is named, which is the order a reader needs
        them in (D207).
      */}
      <Fold summary="What the q-value column is corrected across" count={1}>
        <p className="note" style={{ margin: 0 }}>
          Every <Term id="q-value" /> in this table is corrected by{" "}
          <Term id="Benjamini–Hochberg" />, across every test in the discovery
          run. An uncorrected p-value would call roughly one in twenty of these
          significant by chance.
        </p>
      </Fold>

      <table>
        <thead>
          {/*
            Eight cells per row, so eight headers. This row declared seven —
            the dataset cell had no heading — and every value from the method
            rightward sat one column left of its label: the correlation
            coefficient under "q-value", the q-value under "n", the state past
            the last header. On the one screen whose stated purpose is the
            corrected q-value, the uncorrected effect size was shown in its
            place (D209). The test now counts cells against headers.
          */}
          <tr>
            <th style={{ width: "30%" }}>Relationship</th>
            <th>Dataset</th>
            <th>Method</th>
            <th style={{ textAlign: "right" }}>Estimate</th>
            <th style={{ textAlign: "right" }}>q-value</th>
            <th style={{ textAlign: "right" }}>n</th>
            <th>Evidence</th><th>State</th>
          </tr>
        </thead>
        <tbody>
          {connections.map((c) => (
            <tr key={c.id} style={{ cursor: "pointer" }} onClick={() => onSelect(c.id)}>
              <td style={{ fontWeight: 530 }}>
                <button type="button" className="pick"
                        onClick={() => onSelect(c.id)}>
                  {c.left_variable} <span style={{ color: "var(--ink-faint)" }}>×</span> {c.right_variable}
                </button>
              </td>
              <td style={{ color: "var(--ink-soft)" }}>
                {c.dataset_name ?? "—"}
              </td>
              <td>{humanMethod(c.method)}</td>
              <td className="numeric" style={{ textAlign: "right" }}><Num value={c.estimate} /></td>
              <td className="numeric" style={{ textAlign: "right" }}><Num value={c.q_value} digits={3} /></td>
              <td className="numeric" style={{ textAlign: "right" }}>{c.sample_size ?? "—"}</td>
              <td style={{ color: "var(--ink-soft)" }}>{c.evidence_quality ? sentenceCase(c.evidence_quality) : "—"}</td>
              <td><Status value={c.lifecycle_status} compact /></td>
            </tr>
          ))}
        </tbody>
      </table>
      {/* Eight headings, four of which are two or three characters wide, and a
          reader arriving on this screen has not necessarily met any of them.
          A clause inside a heading would set the column width, so the words go
          underneath in the order the columns run (T188). */}
      <TermList
        lead="What the columns say"
        ids={[
          "estimate", "q-value", "n",
          ["evidence quality", "Evidence"], ["lifecycle state", "State"],
          // Only the methods this table actually used: naming ANOVA under a
          // table with no ANOVA on it is one more thing to read.
          ...methodTerms(connections.map((c) => c.method)),
        ]}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Findings (§13) and the evidence graph (§62)
// ---------------------------------------------------------------------------


/**
 * Make a non-button element behave like one, for a keyboard as well as a mouse.
 *
 * `cursor: pointer` and an `onClick` make something clickable and nothing more:
 * there is no tab stop, no Enter or Space handling, and a screen reader
 * announces a div. Measuring the click depth from a finding back to its rows is
 * what surfaced this — the one click that mattered on that path was reachable
 * only with a mouse, which makes the depth not five but unreachable.
 *
 * Deliberately not applied to the clickable table rows in this file. A row is
 * not a button, and `role="button"` on a `<tr>` trades one broken semantic for
 * another; those need a real control inside the row instead, which is a change
 * to the table markup rather than a prop spread.
 */
function activatable(onActivate: () => void) {
  return {
    role: "button",
    tabIndex: 0,
    style: { cursor: "pointer" },
    onClick: onActivate,
    onKeyDown: (event: React.KeyboardEvent) => {
      // Space scrolls the page by default, so it has to be prevented — the
      // omission is why "it works with Enter" is usually where this stops.
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        onActivate();
      }
    },
  };
}

export function Findings({ projectId, onSelect }: {
  projectId: string; onSelect: (id: string) => void;
}) {
  const { data, error, reload } = useApi<DiscoveryMap>(`/api/projects/${projectId}/discovery-map`);
  const findings = useApi<Finding[]>(`/api/projects/${projectId}/findings`);
  /**
   * Which lifecycle states are on screen.
   *
   * §09 asks this screen for "lifecycle filters and finding detail", and it
   * had neither — every finding the project has ever held, in one list, with
   * no way to separate what still stands from what was set aside. Derived from
   * what is actually recorded rather than from the enum, so a state the
   * project has none of does not offer a filter that empties the screen.
   */
  const [standing, setStanding] = useState<string>("all");

  if (error) return <Failure error={error} retry={reload} />;

  const all = findings.data ?? [];
  const states = [...new Set(all.map((f) => f.lifecycle_status))].sort();
  const shown = standing === "all"
    ? all : all.filter((f) => f.lifecycle_status === standing);

  return (
    <>
      <h1>Findings</h1>
      <p className="lede">
        {/* "and contradicting" overstated the rule and disagreed with the
            server, which says "supporting or contradicting" when it refuses:
            `transition` requires the evidence total to be more than zero, not
            evidence in both directions. A researcher reading the stricter
            version would go looking for a contradiction to manufacture. */}
        A finding must link to evidence — supporting, contradicting, or both —
        before it can be promoted past <Term id="candidate" />.
      </p>
      {findings.loading && <Loading rows={3} label="Reading findings" />}
      {findings.error && <Failure error={findings.error} retry={findings.reload} />}
      {all.length === 0 && !findings.loading && (
        <Empty title="No findings recorded" hint="Validate a connection, then record what it shows as a finding." />
      )}

      {states.length > 1 && (
        <div className="fd-filters" role="group" aria-labelledby="fd-standing">
          {/* Named in the open, for the reason the chart's forms are: the
              label was `aria-label` and nothing else, so the only reader told
              what these buttons do was the one who could not see them. */}
          <span className="ckpt-form-label" id="fd-standing">Show</span>
          <button className="btn" type="button" aria-pressed={standing === "all"}
                  onClick={() => setStanding("all")}>
            All ({all.length})
          </button>
          {states.map((state) => (
            <button key={state} className="btn" type="button"
                    aria-pressed={standing === state}
                    onClick={() => setStanding(state)}>
              {state.replace(/_/g, " ")}{" "}
              ({all.filter((f) => f.lifecycle_status === state).length})
            </button>
          ))}
        </div>
      )}

      {/* A grid, so a project with a dozen findings reads as a collection
          rather than as a column of full-width banners. */}
      <div className="fd-grid">
      {shown.map((finding) => (
        <div className="card" key={finding.id} {...activatable(() => onSelect(finding.id))}>
          <div className="row">
            <div style={{ fontWeight: 560 }}>{finding.title}</div>
            <Status value={finding.lifecycle_status} />
          </div>
          {finding.statement && <p style={{ margin: "6px 0 0" }}>{finding.statement}</p>}
          <div className="fd-meta">
            {sentenceCase(finding.finding_type.replace(/_/g, " "))} · Causal status: {finding.causal_status.replace(/_/g, " ")}
            {/*
              * That the claim carries caveats, where the claims are scanned.
              *
              * The count, not the text: a list is for choosing which finding to
              * open, and three sentences of qualification in a row would bury
              * the titles. What it must not do is present a finding with three
              * stated limits identically to one with none, which is what it did
              * — the caveats appeared only after the reader had opened the very
              * finding they were deciding about.
              */}
            {(finding.limitations?.length ?? 0) > 0 && (
              <> · {finding.limitations?.length} stated{" "}
                {finding.limitations?.length === 1 ? "limit" : "limits"}</>
            )}
          </div>
        </div>
      ))}
      </div>
      {data && <LifecycleBreakdown title="Findings" counts={data.findings} />}
    </>
  );
}

/**
 * The two lists the finding detail needs from the evidence graph.
 *
 * Deliberately narrower than `EvidenceGraph` itself: the callback promises
 * exactly the fields a "Take it further" card reads — the run id a figure is
 * published against, and enough of each connection to ask
 * `canDraftReport` and to name the pair — so the finding detail cannot quietly
 * grow a dependency on the rest of the payload and then break when that
 * payload changes shape. Structurally assignable from `EvidenceGraph`, which
 * is what makes the hand-off free.
 */
export type EvidenceGraphSummary = {
  analyses: Array<{ id: string; method: string }>;
  connections: Array<{
    id: string;
    analysis_run_id: string | null;
    left_variable: string;
    right_variable: string;
  }>;
};

export function EvidenceGraphView({ findingId, onOpenAnalysis, onLoaded }: {
  findingId: string;
  /**
   * Open the analysis a finding rests on.
   *
   * Without this the finding was a dead end. Measuring the provenance depth
   * found that its detail screen offered exactly one action — previewing a
   * library note — and no route to the computation, the dataset or the paper.
   * The chain was in the database; nothing on screen walked it.
   *
   * Optional so the panel still renders in contexts with nowhere to navigate
   * to, where a button that did nothing would be worse than a plain row.
   */
  onOpenAnalysis?: (runId: string) => void;
  /**
   * Hand the evidence graph up once it has arrived (plan §4.6.1).
   *
   * The finding detail's "Take it further" card needs two ids that are already
   * on this wire: the run a figure is published against
   * (`evidence.analyses[0].id`) and the connection a report can start from
   * (`evidence.connections.find(canDraftReport)`). Lifting them through a
   * callback rather than letting `page.tsx` fetch
   * `/api/findings/{id}/evidence-graph` a second time keeps one request and
   * one answer: two fetches of the same path are two copies that drift, which
   * is the rule the note on `Sources` above states for the source list.
   *
   * Called once per graph, from an effect rather than during render — calling
   * a parent's setter while rendering a child is the React warning that turns
   * into an update loop.
   */
  onLoaded?: (graph: EvidenceGraphSummary) => void;
}) {
  const { data, error, loading, reload } = useApi<EvidenceGraph>(
    `/api/findings/${findingId}/evidence-graph`,
  );

  /*
   * Once per arrival, and not once per render.
   *
   * Keyed on `data` so a re-render with the same body does not fire again, and
   * so a *different* finding's graph does fire — the panel is remounted by key
   * on some screens and reused on others, and only the payload identity is
   * true in both cases.
   */
  useEffect(() => {
    if (!data) return;
    onLoaded?.({ analyses: data.analyses, connections: data.connections });
    // `onLoaded` is deliberately not a dependency: a caller that passes an
    // inline arrow would otherwise re-fire this on every parent render, which
    // is the loop this effect exists to avoid.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data]);

  if (error) return <Failure error={error} retry={reload} />;
  if (loading || !data) return <Loading rows={5} label="Assembling the evidence" />;

  return (
    <>
      <h1>{data.finding.title}</h1>
      <div className="row" style={{ marginBottom: 14 }}>
        <Status value={data.finding.lifecycle_status} />
        <span className="mono" style={{ color: "var(--ink-faint)" }}>
          {data.balance.supporting} supporting · {data.balance.contradicting} contradicting
        </span>
      </div>

      {data.note && <p className="note">{data.note}</p>}

      {/*
        * The finding's own caveats, beside the note rather than beneath the
        * evidence. `evidence_graph` has always sent these — next to the
        * sentence about absent contradicting evidence, because the response is
        * shaped around what a reader should not conclude — and the type never
        * named the field, so it arrived on every request and was shown to
        * nobody. A claim's limitations belong with the claim, not after the
        * reader has finished weighing it.
        */}
      {/*
        * The causal reading, above the limitations and below the note.
        *
        * `causal_status` was on the finding from the beginning and read by no
        * screen: the list printed it as a bare token — "causal status:
        * possible causal" — and the finding a researcher opens to decide what
        * it establishes said nothing at all. The exported library note, which
        * goes to somebody else's reference manager, carried a full sentence
        * about it. The person receiving the citation was told more about
        * causality than the person who made the finding.
        *
        * The sentence is the server's, not this component's, for the reason
        * given on `causal_reading` in `api.ts`: the vocabulary lives in one
        * place and had already drifted once.
        *
        * Placed above the limitations because it is the strongest single
        * qualification on a finding, and always shown — including
        * "not assessed", which is the case a reader is most likely to assume
        * away if the screen is silent.
        */}
      {data.causal_reading && (
        <div className="finding-causal">
          <h2>What this finding says about cause</h2>
          <p>{data.causal_reading.note}</p>
        </div>
      )}

      {/*
        Always rendered, never folded away when empty: nothing wrote this
        column, so the section never appeared, and a finding whose caveats
        nobody had written looked exactly like one with nothing left to
        caveat. The editor inside it is the "somewhere for them to do it"
        that the absence needed (T154).
      */}
      <Limitations
        findingId={findingId}
        limitations={data.limitations ?? []}
        onRecorded={() => void reload()}
      />

      <h2>Claims and their evidence</h2>
      {data.claims.length === 0 && <Empty
          title="No claims attached"
          hint="A finding recorded from a connection carries the analysis
                behind it as its evidence. This one was written by hand, so
                there is nothing yet for the balance above to weigh."
        />}
      {data.claims.map((claim) => (
        <div className="card" key={claim.id}>
          <div className="mono" style={{ color: "var(--ink-faint)", marginBottom: 4 }}>
            {claim.claim_type.replace(/_/g, " ")}
          </div>
          <div style={{ fontWeight: 540, marginBottom: 8 }}>{claim.statement}</div>
          {claim.supporting.map((e, i) => (
            <div key={i} className="mono" style={{ color: "var(--validated)" }}>
              ↑ supports · {e.evidence_type} {e.source_document ? `· ${e.source_document}` : ""}
            </div>
          ))}
          {claim.contradicting.map((e, i) => (
            <div key={i} className="mono" style={{ color: "var(--conflicted)" }}>
              ↓ contradicts · {e.evidence_type} {e.source_document ? `· ${e.source_document}` : ""}
            </div>
          ))}
        </div>
      ))}

      {data.analyses.length > 0 && (
        <>
          <h2>Computations behind it</h2>
          {data.analyses.map((a) => (
            <div className="card card-tight" key={a.id}>
              <div className="row">
                {/*
                  The step that makes the chain walkable. From here the analysis
                  names its dataset, which names its source — so "why do we
                  believe this?" is answerable by clicking rather than by
                  knowing where to look.
                */}
                {onOpenAnalysis ? (
                  /* `.pick`, the shared in-list opener, rather than the inline
                     text-button this used to hand-roll: the same rank of action
                     looks the same on every screen (§4.6.4). */
                  <button type="button" className="pick"
                          onClick={() => onOpenAnalysis(a.id)}>
                    <span className="mono">{a.method}</span>
                  </button>
                ) : <span className="mono">{a.method}</span>}
                <span className="mono" style={{ color: "var(--ink-faint)" }}>{a.id}</span>
              </div>
              {a.result?.interpretation ? (
                <div style={{ marginTop: 5, color: "var(--ink-soft)" }}>{a.result.interpretation}</div>
              ) : null}
            </div>
          ))}
        </>
      )}

      {data.challenges.length > 0 && (
        <>
          <h2>Challenges</h2>
          {data.challenges.map((c) => (
            <div className="card card-tight" key={c.id}>
              {/* `raw`: these are the pill's colours borrowed for a challenge
                  verdict, not lifecycle states, so the lifecycle phrases
                  must not be printed for them. */}
              <Status raw value={c.verdict === "holds" ? "validated" : "conflicted"} />
              <div style={{ marginTop: 5 }}>{c.summary}</div>
            </div>
          ))}
        </>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Analyses (§44, §47)
// ---------------------------------------------------------------------------

/**
 * What the run warned about, which is not the same as what it was limited by.
 *
 * A limitation qualifies a result that stands — "this is observational, so
 * causal language is not available". A warning questions whether it stands at
 * all: the normality assumption failed and a different test was the right one,
 * or the optimiser never converged and the estimate is not to be trusted.
 * Showing the first and silently dropping the second is the inversion this
 * product exists to prevent, and it is what this screen did.
 *
 * Read from both places the same content arrives by: the run's own column and
 * the copy inside the result. They are written from one value and are expected
 * to agree, so this unions them rather than trusting either — an older run
 * where only one was populated still says what it knew.
 */
/**
 * What the Assumptions tab says before anybody opens it.
 *
 * A count alone would answer the wrong question. What decides whether that tab
 * is worth opening is whether anything FAILED, and a run with eight passing
 * checks and one violation is the case that matters — the number 9 hides it.
 */
export function assumptionNote(checks: AnalysisRun["assumption_checks"]): string | undefined {
  if (checks.length === 0) return "none";
  const failed = checks.filter((c) => /violat|fail/i.test(c.outcome)).length;
  return failed > 0 ? `${failed} of ${checks.length} failed` : String(checks.length);
}

function ResultWarnings({ run }: { run: AnalysisRun }) {
  const fromRun = (run as { warnings?: string[] }).warnings ?? [];
  const fromResult = run.result?.warnings ?? [];
  const said = [...new Set([...fromRun, ...fromResult])].filter(
    (line) => line.trim().length > 0);
  if (said.length === 0) return null;

  return (
    <div className="run-warnings" role="alert">
      <h2>
        {said.length === 1 ? "This run warned about something"
                           : `This run warned about ${said.length} things`}
      </h2>
      <ul>{said.map((line) => <li key={line}>{line}</li>)}</ul>
      <p>
        A warning is not a limitation. It says the number below may not be the
        one you wanted — because the method’s assumptions did not hold, or
        because the fit did not settle.
      </p>
    </div>
  );
}

/**
 * Assumption checks, with a family of per-group checks reported as one.
 *
 * An ANOVA over 120 groups records `normality[group0]` … `normality[group8]`,
 * and printing each as its own row gave the cockpit nine near-identical lines
 * saying "Consistent with a normal distribution" — the panel that is supposed
 * to tell a reader what to worry about instead buried the one group that
 * failed among eight that did not.
 *
 * So a family collapses to its name, and what it reports is the count and the
 * exceptions: "8 of 9 passed · group6 violated". Nothing is hidden — the
 * Assumptions tab still lists every check individually — and the summary
 * cannot claim a clean sweep it did not have, because the failures are named.
 */
function assumptionFamilies(checks: Array<{
  name: string; outcome: string; detail: string; severity: string;
}>) {
  const families = new Map<string, {
    name: string; members: typeof checks; failed: typeof checks;
  }>();
  for (const check of checks) {
    // `normality[group6]` is a member of the `normality` family; a name with
    // no bracket is its own family of one.
    const family = check.name.replace(/\[.*\]$/, "");
    const entry = families.get(family)
      ?? { name: family, members: [], failed: [] };
    entry.members.push(check);
    if (check.outcome !== "passed") entry.failed.push(check);
    families.set(family, entry);
  }
  return [...families.values()].map((f) => ({
    name: f.name,
    /* The state the mark is coloured by. `outcome` can be a sentence — "0 of 2
       passed" — and a sentence has no tone, so the family rendered hollow, the
       mark for *nothing recorded*, on exactly the row that failed. */
    tone: f.failed.length === 0 ? "passed" : f.failed[0].outcome,
    outcome: f.failed.length === 0 ? "passed"
      : f.members.length === 1 ? f.failed[0].outcome
      : `${f.members.length - f.failed.length} of ${f.members.length} passed`,
    severity: f.failed.length
      ? f.failed[0].severity : f.members[0].severity,
    detail: f.members.length === 1
      ? f.members[0].detail
      : f.failed.length === 0
        ? f.members[0].detail
        : `${f.failed.map((c) => c.name.replace(/^.*\[(.*)\]$/, "$1")).join(", ")}`
          + ` — ${f.failed[0].detail}`,
  }));
}

/**
 * The run's own scatter, in the cockpit where a reader looks for it.
 *
 * UI_02 puts "Observed association" beside the recorded result, and a reader
 * judging an estimate looks at the cloud before they read the number: a
 * correlation of 0.24 means one thing over a straight band and another over
 * two clusters. The endpoint (`/analyses/{id}/points`) and the renderer
 * (`Cartesian`) both already existed and were wired only into Figures, so the
 * cockpit showed every number about a relationship and no picture of it.
 *
 * Deliberately plain here: no export button, no region recording, no
 * recommendation prose. Figures owns all of that, and a second full figure
 * builder inside the cockpit would be the duplicate this codebase keeps
 * removing. This is the reading, and Figures is where a figure is made.
 */
/**
 * The forms this panel can draw the same values in.
 *
 * Not every form the product has — Figures owns the builder and the catalogue,
 * and a second full one here would be the duplicate this codebase keeps
 * removing. These are the four a paired x and y can honestly take: the cloud,
 * the trend, the filled trend, and the binned version. Choosing one changes
 * how the same numbers are drawn and nothing about the numbers.
 */
const FORMS: ReadonlyArray<[CartesianMark, string]> = [
  ["point", "Points"],
  ["line", "Line"],
  ["area", "Area"],
  ["rect", "Bars"],
];

function ObservedAssociation({ runId, onOpenFigures, estimate = null,
                               estimateName = null, variables = {} }: {
  runId: string;
  /** The full builder, where a figure is made rather than read. */
  onOpenFigures?: () => void;
  /** The run's own estimate, set on the plot the way the master sets it. */
  estimate?: number | null;
  estimateName?: string | null;
  /**
   * The run's recorded variables, which is where the axes get their names.
   *
   * The points endpoint carries the values and not the column names, so the
   * axes were titled "x" and "y" — a scatter of two unnamed quantities, on the
   * screen whose whole job is saying what was measured against what.
   */
  variables?: Record<string, unknown>;
}) {
  /*
   * The plot is drawn at the width of its panel, measured.
   *
   * The renderer draws into a viewBox, and at its default 620 units in a
   * panel some 430px wide every tick label was scaled down to about eight
   * pixels — below anything §08 allows. Measuring the panel lets text render
   * at the size the stylesheet gives it.
   */
  const frame = useRef<HTMLDivElement | null>(null);
  const [frameWidth, setFrameWidth] = useState(460);
  useEffect(() => {
    const el = frame.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => {
      const w = Math.round(entry.contentRect.width);
      if (w > 200) setFrameWidth(w);
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const named = (keys: string[], fallback: number): string | null => {
    for (const k of keys) if (variables[k] != null) return String(variables[k]);
    const values = Object.values(variables);
    return values[fallback] != null ? String(values[fallback]) : null;
  };
  const xName = named(["x", "exposure", "predictor", "factor", "group"], 0);
  const yName = named(["y", "outcome", "response", "measure"], 1);
  /*
   * The form is the reader's, and it is remembered for as long as they are on
   * the run. A researcher who switches to the line to see the trend and then
   * opens a tab should not have to switch back.
   */
  const [mark, setMark] = useState<CartesianMark>("point");
  const points = useApi<{
    x: number[]; y: number[]; x_label?: string; y_label?: string;
    sample_size?: number; note?: string | null;
  }>(`/api/analyses/${runId}/points`, [runId]);

  const data = useMemo(() => {
    const p = points.data;
    if (!p?.x?.length) return [];
    return p.x.map((x, i) => ({ id: String(i), x, y: p.y[i] }));
  }, [points.data]);

  if (points.loading) return <Loading rows={3} label="Reading the points" />;
  /*
   * A method with nothing to plot says so rather than leaving a hole. Not
   * every analysis has a two-column cloud behind it, and an empty frame reads
   * as a chart that failed to draw.
   */
  if (points.error != null || data.length === 0) {
    return (
      <p className="note">
        {points.data?.note
          || "This method records no paired values to plot."}
      </p>
    );
  }

  const symbol = estimateName ? estimateSymbol(estimateName) : null;

  return (
    <>
      <div className="ckpt-plot" ref={frame}>
        {estimate != null && symbol && (
          /* The estimate on the plot, where the eye already is. Not a fitted
             line: a correlation fits no model, and this renderer draws a line
             only when one was fitted (Law 2) — the master's trend line on a
             Pearson correlation is the one thing here not copied from it. */
          <span className="ckpt-plot-estimate numeric">
            {symbol} = {estimate.toFixed(2)}
          </span>
        )}
        <Cartesian
          data={data}
          mark={mark}
          xLabel={points.data?.x_label ?? xName ?? "x"}
          yLabel={points.data?.y_label ?? yName ?? "y"}
          /*
            Density colour belongs to the cloud. On a line or a filled area it
            would colour a shape by a crowding that shape has already hidden.
          */
          densityColour={mark === "point"}
          densityRamp="ink"
          width={frameWidth}
          height={250}
        />
      </div>
      {/*
        * The figure's own controls, beside the figure.
        *
        * This panel used to draw one form and stop, and changing how a result
        * was drawn meant leaving the analysis for the Figures section — which
        * is the shape of the whole product's problem: twenty-three sections,
        * and the thing you want is always in another one. The forms are here;
        * the builder is still Figures, and the way there is a sentence rather
        * than a second builder.
        */}
      <div className="ckpt-forms">
        {/*
          * The label was `aria-label` and nothing else, so a screen reader was
          * told what these four buttons are for and a sighted reader was not —
          * the wrong way round, and the shape of the worry that "someone will
          * never know what is being done". Four bare words under a chart are
          * only obviously a chart control once you already know. It is said in
          * the open now, and the accessible name comes from the same words
          * rather than from a second string that can drift from them.
          */}
        <span className="ckpt-form-label" id="ckpt-draw-as">Draw as</span>
        <div className="ckpt-form-set" role="group" aria-labelledby="ckpt-draw-as">
          {FORMS.map(([id, label]) => (
            <button key={id} className="btn" type="button"
                    aria-pressed={mark === id} onClick={() => setMark(id)}>
              {label}
            </button>
          ))}
        </div>
        {onOpenFigures && (
          <button className="btn-text" type="button" onClick={onOpenFigures}>
            Build a figure from this run →
          </button>
        )}
      </div>
    </>
  );
}

/**
 * A p-value as a reader reports it.
 *
 * Below a thousandth the convention is `<0.001`, and the cockpit printed
 * `4.46e-19` — exact, and unreadable at a glance. Above it, three decimals.
 * The exact value is kept on the element's title, so nothing is rounded away.
 */
export function formatP(p: number | null | undefined): string {
  if (p == null || Number.isNaN(p)) return "—";
  if (p < 0.001) return "<0.001";
  return p.toFixed(3);
}

/**
 * An estimate's conventional symbol: `pearson_r` is r, `spearman_rho` is ρ.
 *
 * Stripping everything after the underscore turned `pearson_r` into "pearson",
 * which names the method and not the quantity — the one word in the
 * measurement row that is not a measurement.
 */
export function estimateSymbol(name: string | null | undefined): string {
  const n = (name ?? "").toLowerCase();
  if (n.startsWith("pearson")) return "r";
  if (n.startsWith("spearman")) return "ρ";
  if (n.startsWith("kendall")) return "τ";
  if (n === "r_squared" || n === "r2") return "R²";
  if (n.includes("cohen")) return "Cohen’s d";
  // Before η²: "beta" contains "eta", which labelled every regression
  // coefficient as an effect size it is not.
  const coefficient = /^beta\[(.+)\]$/.exec(n);
  if (coefficient) return `β(${coefficient[1]})`;
  if (n.startsWith("beta") || n.includes("slope") || n.includes("coefficient")) return "β";
  if (/^eta(_squared|2|²)?$|^partial_eta/.test(n)) return "η²";
  if (n.includes("odds")) return "Odds ratio";
  return n ? sentenceCase(n.replace(/_/g, " ")) : "Estimate";
}

/** `pearson_correlation` → "Pearson correlation". Names, not identifiers. */
export function humanMethod(method: string): string {
  const words = method.replace(/_/g, " ").trim();
  // The methods that are named by their initials are written that way.
  const acronyms: Record<string, string> = {
    anova: "ANOVA", ancova: "ANCOVA", manova: "MANOVA", ols: "OLS", glm: "GLM",
    pca: "PCA", "t test": "t-test", "chi square": "Chi-square",
  };
  const lowered = words.toLowerCase();
  for (const [key, value] of Object.entries(acronyms)) {
    if (lowered === key) return value;
    if (lowered.startsWith(key + " ")) return value + words.slice(key.length);
  }
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** The first letter up, the rest untouched — "large" → "Large", "p-value" stays. */
export function sentenceCase(text: string): string {
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : text;
}

/**
 * A recorded variable role, named for a reader.
 *
 * Only relabelled where the run's own vocabulary is a symbol: a correlation
 * records `x` and `y`, which are positions and not roles, so they read as the
 * first and second variable. Every other role — factor, measure, exposure,
 * outcome — is already a word and is printed as the run recorded it, because
 * relabelling a factor "exposure" would be a small lie about what was run.
 */
export function roleLabel(role: string): string {
  if (role === "x") return "First variable";
  if (role === "y") return "Second variable";
  return sentenceCase(role.replace(/_/g, " "));
}

/**
 * The same question asked other ways: the run family, as UI_02's table.
 *
 * This panel was one sentence explaining what a sensitivity family would be.
 * The project already holds one for any pair that has been validated — the
 * robustness suite re-runs the pair as a bootstrap and as a regression with the
 * other measured columns — and those runs sat in the list with nothing saying
 * they were the same question. The family is the runs whose variables are this
 * run's pair: a correlation of the same two columns in either order, or a
 * regression of one on the other with anything else beside it.
 *
 * **The estimates are not made comparable, because they are not.** A
 * correlation is r and a regression coefficient is in the outcome's units, so
 * each is printed with its own name and no column pretends they line up.
 */
export function RunFamily({ projectId, run, onOpenRun }: {
  /** Optional because the detail can be rendered without a project, in which
   *  case there is no list of runs to find a family in. */
  projectId?: string;
  run: AnalysisRun;
  onOpenRun?: (runId: string) => void;
}) {
  const runs = useApi<AnalysisRunRow[]>(
    projectId ? `/api/projects/${projectId}/analyses` : null, [projectId]);
  const pair = pairOf(run.variables);
  if (!pair) {
    return (
      <p className="note" style={{ margin: 0 }}>
        This method does not name a pair of variables, so there is no family of
        runs asking the same question.
      </p>
    );
  }
  const family = (Array.isArray(runs.data) ? runs.data : [])
    .map((r) => ({ r, change: changeFrom(r, pair, run.id) }))
    .filter((x): x is { r: AnalysisRunRow; change: string } => x.change !== null)
    // The run on screen leads, as the master's table does; the rest follow.
    .sort((x, y) => Number(y.r.id === run.id) - Number(x.r.id === run.id));

  if (runs.loading && !runs.data) return <Loading rows={2} label="Reading the run family" />;
  if (family.length <= 1) {
    return (
      <p className="note" style={{ margin: 0 }}>
        Only this run asks this question so far. Validating the connection it
        produced re-runs it resampled and adjusted, and those runs appear here.
      </p>
    );
  }
  return (
    <>
    <table className="ckpt-family">
      <thead>
        <tr><th>Run</th><th>Change</th><th>Status</th><th className="num">Estimate</th></tr>
      </thead>
      <tbody>
        {family.map(({ r, change }) => (
          <tr key={r.id} data-current={r.id === run.id || undefined}>
            <td>
              {r.id === run.id || !onOpenRun
                ? <span className="mono">{runHandle(r.id)}</span>
                : <button type="button" className="btn-text mono" onClick={() => onOpenRun(r.id)}>
                    {runHandle(r.id)}
                  </button>}
            </td>
            <td>{change}</td>
            <td><StateMark value={r.status} label={sentenceCase(r.status)} /></td>
            <td className="num">
              {r.estimate != null
                ? <>{r.estimate.toFixed(2)} <span className="ckpt-family-name">{estimateSymbol(r.estimate_name)}</span></>
                : "—"}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
    {/* The Estimate column is a number and a symbol, and the symbol changes
        per row: a correlation's r beside a regression's β (T188). */}
    <TermList ids={["r", "β"]} />
    </>
  );
}

/** A run's short handle, the way the river names it. */
function runHandle(id: string): string {
  return `AN-${id.replace(/^[a-z]+_/, "").slice(0, 4)}`;
}

/** The two variables a run is about, if it names exactly a pair. */
function pairOf(variables: Record<string, unknown>): [string, string] | null {
  const x = variables?.x, y = variables?.y;
  if (typeof x === "string" && typeof y === "string") return [x, y];
  return null;
}

/**
 * How a run differs from the question, or null when it is not the same question.
 *
 * A regression belongs to the family only when the coefficient it reports is
 * the other variable of the pair. The robustness suite regresses the outcome on
 * the focal variable *and* the confounders, so the same regression names every
 * column as a predictor — and matching on "is it among the predictors" filed a
 * rainfall regression in the fertiliser family, where its rainfall coefficient
 * of 0.00 read as "fertiliser has no effect once adjusted". The estimate's own
 * name (`beta[rainfall_mm]`) says which coefficient it is, so that decides.
 */
function changeFrom(r: AnalysisRunRow, [a, b]: [string, string], currentId: string): string | null {
  const v = r.variables ?? {};
  const sameCorrelation = typeof v.x === "string" && typeof v.y === "string"
    && new Set([v.x, v.y, a, b]).size === 2;
  if (sameCorrelation) {
    if (r.id === currentId) return "This run";
    if (r.method.startsWith("bootstrap")) return "Resampled (bootstrap)";
    if (r.method.startsWith("spearman")) return "Rank-based (Spearman)";
    if (r.method.startsWith("kendall")) return "Rank-based (Kendall)";
    // Nothing recorded why it was run; saying so beats inventing a reason.
    return r.fork_reason ? sentenceCase(r.fork_reason)
      : `${humanMethod(r.method)} · no reason recorded`;
  }
  const outcome = v.outcome;
  const predictors = Array.isArray(v.predictors) ? v.predictors.map(String) : [];
  const focal = /^beta\[(.+)\]$/.exec(r.estimate_name ?? "")?.[1];
  if (typeof outcome === "string" && [a, b].includes(outcome) && focal) {
    const other = outcome === a ? b : a;
    if (focal === other) {
      const rest = predictors.filter((p) => p !== other);
      return rest.length ? `Adjusted for ${rest.join(", ")}` : "Regression";
    }
  }
  return null;
}

export function AnalysisDetail({ runId, projectId, onMethod, onVariables,
                                onOpenObject, onOpenFigures, onOpenRun }: {
  /** Open another run of the family, from the sensitivity table. */
  onOpenRun?: (runId: string) => void;
  runId: string;
  /**
   * Which project this run belongs to (D213).
   *
   * Passed in, because `GET /api/analyses/{id}` does not carry it and the
   * history routes are all scoped to a project. Optional: a host that does not
   * know the project cannot ask the lookup, and the run itself reads perfectly
   * well without its history — better than a section that asks the server a
   * question with a blank in it.
   */
  projectId?: string;
  /*
   * Reported upward rather than fetched twice. The panel below this one offers
   * a branch that swaps the method for its rank-based counterpart, and it needs
   * to know which method that is — a second hook on `/api/analyses/{id}` would
   * be a second copy of this run that can drift from the one on screen.
   */
  onMethod?: (method: string) => void;
  /*
   * The recorded variable roles, reported upward for the same reason the method
   * is: the cockpit's left column shows them beside the result, and a second
   * hook on `/api/analyses/{id}` would be a second copy of this run that can
   * drift from the one on screen.
   */
  onVariables?: (variables: Record<string, unknown>) => void;
  /** Follow a lineage link in this run's history. See `SourceDetail`. */
  onOpenObject?: (objectId: string) => void;
  /**
   * Open the figure builder on this run.
   *
   * The cockpit draws the run's own figure and offers the forms it can take;
   * Figures is where a figure is configured, captioned and published. Handing
   * the way there rather than growing a second builder is what keeps the two
   * from becoming the same screen twice.
   */
  onOpenFigures?: () => void;
}) {
  const { data, error, loading, reload } = useApi<AnalysisRun>(`/api/analyses/${runId}`);
  const method = data?.method;
  const variables = data?.variables;
  useEffect(() => { if (method) onMethod?.(method); }, [method, onMethod]);
  useEffect(() => { if (variables) onVariables?.(variables); }, [variables, onVariables]);
  if (error) return <Failure error={error} retry={reload} />;
  if (loading || !data) return <Loading rows={5} label="Reading the analysis" />;

  // A completed run always carries a result; the guard above proves it.
  const r = data.result;
  return (
    <>
      {/*
        * The relationship is the title; the method is provenance.
        *
        * UI_02 heads this screen with what was examined — "Night-time heat ↔
        * anxiety symptoms" — and puts "Pearson correlation · researcher-
        * specified · dataset v2" under it in small type. Ours led with the bare
        * method name, so the largest word on a screen about a relationship was
        * "anova", and the relationship itself was the subtitle.
        */}
      <header className="ckpt-head">
        <div>
          <h1 className="ckpt-title">
            {data.research_question || data.method.replace(/_/g, " ")}
          </h1>
          <p className="ckpt-sub">
            <span>{humanMethod(data.method)}</span>
            <span aria-hidden> · </span>
            <span>{data.method_rationale ? "Researcher-specified" : "Recorded"}</span>
            {data.duration_ms != null && (
              <>
                <span aria-hidden> · </span>
                <span className="numeric">{data.duration_ms} ms</span>
              </>
            )}
          </p>
        </div>
        <div className="ckpt-head-side">
          {/* The run's state, as a state rather than as an absence of error.
              A mark with a tick in it, as the master sets it, so "completed"
              is a thing the eye finds and not a word it reads. */}
          <p className="ckpt-state" data-state={data.status}>
            <span className="ckpt-state-dot" aria-hidden>
              {data.status === "completed" ? "✓" : data.status === "failed" ? "!" : ""}
            </span>
            {data.status === "completed" ? "Run completed" : `Run ${data.status}`}
          </p>
        </div>
      </header>
      {data.status !== "completed" && (
        <>
          <div className="error">{data.error ?? `This run is ${data.status}.`}</div>

          {/*
            * What the sandbox actually said.
            *
            * `error` is often "Analysis failed" — the runtime's fallback when
            * it has nothing better — while the traceback that explains it was
            * captured to `logs` and displayed nowhere. A failure a researcher
            * cannot diagnose is one they retry blindly or abandon.
            *
            * Folded away rather than printed: a stack trace above the fold
            * makes an ordinary failure look like a crash in the product, and
            * the first thing most readers need is the sentence above.
            */}
          {(data.logs ?? "").trim().length > 0 && (
            <details className="run-logs">
              <summary>What the sandbox reported</summary>
              <pre>{data.logs}</pre>
            </details>
          )}
        </>
      )}

      {data.status === "completed" && r && (
        /*
         * Five readings of one run, which is what the cockpit master is.
         *
         * Nothing here is new: this is the same content the screen already
         * carried, in the order §09 names — Result, Specification, Assumptions,
         * Sensitivity, Reproducibility. What changes is that it stops being one
         * long scroll. A researcher comparing two runs reads the specification
         * of both, then the assumptions of both; scrolling past an interpretation
         * to reach a seed is the friction the master removes.
         *
         * The warnings stay OUTSIDE the tabs, above everything. They say the
         * estimate may not stand at all, and a reader must not have to open a
         * panel to find that out.
         */
        <>
          <ResultWarnings run={data} />

          <Tabs
            label="Readings of this run"
            tabs={[
              {
                id: "result",
                label: "Result",
                /*
                 * The Result view CO-LOCATES; it is not a summary card.
                 *
                 * §09: "The Result view co-locates specification summary,
                 * method-appropriate output, observed chart, assumption checks,
                 * interpretation, reproducibility and sensitivity family." The
                 * other four tabs are deeper readings of the same run, not the
                 * only place those things live — a researcher judging a result
                 * needs the specification it came from and the assumptions it
                 * rests on in the same glance, and this screen made them click
                 * through five tabs to assemble one judgement.
                 *
                 * 07_ACCEPTANCE names the two failures this fixes as blocking:
                 * "cockpit reduced to oversized tiles" and "hiding assumptions,
                 * provenance or context at reference width". It was both. Four
                 * tiles at 30px each is a dashboard, and a dashboard is what
                 * §08 calls the wrong answer for work.
                 */
                panel: () => (
                  <div className="ckpt-grid">
                    {/*
                      * Paired as UI_02 pairs them, row by row: what was asked
                      * beside what came back; the picture beside the checks
                      * that qualify it; the reading beside what it would take
                      * to reproduce it. The previous order put Interpretation
                      * beside the chart and left Assumption checks alone on a
                      * row, so the two things a reader compares — the scatter
                      * and whether its assumptions held — were a screen apart.
                      */}
                    <section className="ckpt-panel">
                      <h2 className="ckpt-panel-name">Recorded specification</h2>
                      <dl className="ckpt-kv">
                        <dt>Method</dt><dd>{humanMethod(data.method)}</dd>
                        {Object.entries(data.variables ?? {}).map(([role, name]) => (
                          <Fragment key={role}>
                            <dt>{roleLabel(role)}</dt>
                            <dd>{String(name)}</dd>
                          </Fragment>
                        ))}
                        <dt>Rationale</dt>
                        <dd>{data.method_rationale || "Not recorded."}</dd>
                      </dl>
                      {/* Method plus variables is exactly the estimand, and
                          the panel that holds both never said the word (T188). */}
                      <p className="note" style={{ marginBottom: 0 }}>
                        Taken together these are the <Term id="estimand" />: fix
                        them before the run, and the result is an answer to a
                        question rather than the best of several.
                      </p>
                    </section>

                    <section className="ckpt-panel">
                      <h2 className="ckpt-panel-name">Recorded result</h2>
                      {/*
                        * A measurement row, as the master sets it: the name
                        * above, the number below, a rule between each. The
                        * previous row put a 10px uppercase caption under each
                        * figure and printed p as `4.46e-19` — correct, and
                        * unreadable at a glance; reporting convention is `<0.001`
                        * below a thousandth, with the exact value one hover away.
                        */}
                      <div className="ckpt-measures">
                        <span>
                          <em>{estimateSymbol(r.effect_size?.name ?? r.estimate_name)}</em>
                          {/* Two decimals to read, as reporting convention has it;
                              the full value on the element, so nothing is lost. */}
                          <b className="numeric"
                             title={r.estimate != null ? String(r.estimate) : undefined}
                             data-exact={r.estimate != null ? r.estimate.toFixed(4) : undefined}>
                            {r.estimate != null ? r.estimate.toFixed(2) : "—"}
                          </b>
                        </span>
                        {r.ci_low != null && r.ci_high != null && (
                          <span>
                            <em>{Math.round((r.confidence_level ?? 0.95) * 100)}% CI</em>
                            <b className="numeric">[{r.ci_low.toFixed(2)}, {r.ci_high.toFixed(2)}]</b>
                          </span>
                        )}
                        <span title={r.p_value != null ? `p = ${r.p_value.toExponential(3)}` : undefined}>
                          <em>p-value</em>
                          <b className="numeric">{formatP(r.p_value)}</b>
                        </span>
                        <span>
                          {/* n, the conventional name, rather than "Observations":
                              the label was wider than every number in the row and
                              pushed the sample size onto a line of its own. */}
                          <em>n</em>
                          <b className="numeric">{r.sample_size != null ? r.sample_size.toLocaleString() : "—"}</b>
                        </span>
                      </div>
                      <dl className="ckpt-kv">
                        <dt>Statistical significance</dt>
                        {/*
                          * `String(null)` is "null", and that is what this
                          * printed — the literal word, on the cockpit, beside
                          * a real result. §08 asks for an absent value shown
                          * honestly, which is a sentence and not a JavaScript
                          * primitive leaking onto the screen.
                          */}
                        <dd>
                          <StateMark
                            value={r.statistically_significant}
                            label={r.statistically_significant == null
                              ? "not recorded"
                              : r.statistically_significant ? "Yes" : "No"}
                          />
                        </dd>
                        <dt>Practical significance</dt>
                        <dd>{r.practical_significance ? sentenceCase(r.practical_significance) : <span className="note">not recorded</span>}</dd>
                        <dt>Evidence quality</dt>
                        <dd>{r.evidence_quality ? sentenceCase(r.evidence_quality) : <span className="note">not recorded</span>}</dd>
                      </dl>
                    </section>

                    {/*
                      * The same four numbers in words, built from the result
                      * itself (T188). The row above is the densest thing on the
                      * screen and, to a reader who has not met r, the least
                      * legible; this is a reading of it, not a summary.
                      *
                      * Full width and directly beneath, rather than inside the
                      * result panel: in a half-width column the sentences ran
                      * to eight lines and left the specification panel beside
                      * them half empty.
                      */}
                    {readingOf({
                      method: data.method,
                      estimateName: r.effect_size?.name ?? r.estimate_name,
                      estimate: r.estimate, pValue: r.p_value,
                      sampleSize: r.sample_size, variables: data.variables,
                    }).length > 0 && (
                    <section className="ckpt-panel ckpt-wide">
                      <h2 className="ckpt-panel-name">What that says</h2>
                      <PlainReading
                        lead="In plain words"
                        method={data.method}
                        estimateName={r.effect_size?.name ?? r.estimate_name}
                        estimate={r.estimate}
                        ciLow={r.ci_low}
                        ciHigh={r.ci_high}
                        confidenceLevel={r.confidence_level}
                        pValue={r.p_value}
                        sampleSize={r.sample_size}
                        practicalSignificance={r.practical_significance}
                        variables={data.variables}
                      />
                      {/* The three graded words in the panel above, which are
                          easy to read as three names for one thing (T188).
                          They are here rather than under that panel because a
                          half-width column turned three clauses into nine
                          lines. */}
                      <TermList
                        lead="The three grades above"
                        ids={["statistical significance", "practical significance", "evidence quality"]}
                      />
                    </section>
                    )}

                    {/*
                      * The observed association, in the cockpit rather than
                      * only on Figures. The endpoint and the renderer both
                      * already existed; the master's centre had no picture in
                      * it, which is the one thing a reader looks at first.
                      */}
                    <section className="ckpt-panel">
                      <h2 className="ckpt-panel-name">Observed association</h2>
                      <ObservedAssociation runId={runId} onOpenFigures={onOpenFigures}
                                           estimate={r.estimate}
                                           estimateName={r.effect_size?.name ?? r.estimate_name}
                                           variables={data.variables ?? {}} />
                    </section>

                    <section className="ckpt-panel">
                      <h2 className="ckpt-panel-name">Assumption checks</h2>
                      {data.assumption_checks.length === 0 ? (
                        <p className="note">This method declared no assumptions to check.</p>
                      ) : (
                        <>
                          <table className="ckpt-checks">
                            <thead>
                              <tr><th>Check</th><th>Outcome</th><th>Detail</th></tr>
                            </thead>
                            <tbody>
                              {assumptionFamilies(data.assumption_checks).map((c) => (
                                <tr key={c.name} data-severity={c.severity}>
                                  <td>{sentenceCase(c.name.replace(/_/g, " "))}</td>
                                  <td><StateMark value={c.tone} label={sentenceCase(c.outcome)} /></td>
                                  <td>{c.detail}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                          {/* "2 points beyond 1.5×IQR" is the detail column's
                              own wording, and IQR appears nowhere else in the
                              product (T188). */}
                          <TermList ids={["assumption check", "IQR"]} />
                          {/*
                            * The consequence, stated where the failure is.
                            * UI_02 closes this panel with "Review assumptions
                            * before promoting this result" when a check needs
                            * review, and a table of outcomes with no sentence
                            * after it leaves the reader to decide whether a
                            * violated check matters. Said only when one did
                            * not pass — a warning on every run is wallpaper.
                            */}
                          {data.assumption_checks.some((c) => c.outcome !== "passed") && (
                            <p className="ckpt-caution" role="note">
                              <span className="ckpt-caution-mark" aria-hidden>!</span>
                              Review these assumptions before promoting this result.
                            </p>
                          )}
                        </>
                      )}
                    </section>

                    <section className="ckpt-panel">
                      <h2 className="ckpt-panel-name">Interpretation</h2>
                      <p className="ckpt-read">{r.interpretation}</p>
                      {r.limitations.length > 0 && (
                        <ul className="ckpt-limits">
                          {r.limitations.map((l, i) => <li key={i}>{l}</li>)}
                        </ul>
                      )}
                    </section>

                    <section className="ckpt-panel">
                      <h2 className="ckpt-panel-name">Reproducibility</h2>
                      <dl className="ckpt-kv">
                        <dt>Random seed</dt><dd className="numeric">{data.random_seed}</dd>
                        <dt>Duration</dt>
                        <dd className="numeric">
                          {data.duration_ms != null ? `${data.duration_ms} ms` : "—"}
                        </dd>
                        {/* Hashes are identifiers, so these two keep the
                            monospace face every other value gave up. */}
                        <dt>Dataset hash</dt>
                        <dd className="mono ckpt-hash">
                          {data.input_hashes.dataset_content_hash?.slice(0, 10) ?? "—"}…
                        </dd>
                        <dt>Spec hash</dt>
                        <dd className="mono ckpt-hash">
                          {data.input_hashes.spec_content_hash?.slice(0, 10) ?? "—"}…
                        </dd>
                      </dl>
                      {/* Four values with no statement of what having them is
                          worth (T188). The seed and the two hashes are the
                          whole reproducibility claim, and the claim was the
                          one thing the panel did not make. */}
                      <p className="note" style={{ marginBottom: 0 }}>
                        Together these are enough to get this exact number
                        again: the <Term id="random seed" />, and a fingerprint
                        each of the data that went in and of the settings it was
                        run with. If either fingerprint differs from a re-run,
                        the inputs were not the same.
                      </p>
                    </section>

                    <section className="ckpt-panel ckpt-wide">
                      <h2 className="ckpt-panel-name">Sensitivity · run family</h2>
                      {/*
                        * Full width and last, as the master has it: the family
                        * is a table of runs and a two-column cell would wrap
                        * every row.
                        */}
                      <RunFamily projectId={projectId} run={data} onOpenRun={onOpenRun} />
                    </section>
                  </div>
                ),
              },
              {
                id: "specification",
                label: "Specification",
                panel: () => (
                  <div className="card">
                    <h2>What was asked for</h2>
                    <div className="kv">
                      <dt>Method</dt><dd className="mono">{data.method}</dd>
                      <dt>Question</dt><dd>{data.research_question || "—"}</dd>
                      <dt>Method chosen because</dt><dd>{data.method_rationale || "—"}</dd>
                    </div>
                    {/*
                      * The variables as the run recorded them, not as a fixed
                      * exposure/outcome pair. §09: do not force every method
                      * into one schema — an ANOVA has a factor and a measure,
                      * a correlation has two continuous columns, and printing
                      * "exposure" over a factor would be a small lie about
                      * what was run.
                      */}
                    <h3 className="eyebrow" style={{ marginTop: 14 }}>Variables</h3>
                    {Object.keys(data.variables ?? {}).length === 0 ? (
                      <p className="note">This run recorded no variable roles.</p>
                    ) : (
                      <div className="kv">
                        {Object.entries(data.variables).map(([role, value]) => (
                          <Fragment key={role}>
                            <dt>{role.replace(/_/g, " ")}</dt>
                            <dd className="mono">{String(value)}</dd>
                          </Fragment>
                        ))}
                      </div>
                    )}
                  </div>
                ),
              },
              {
                id: "assumptions",
                label: "Assumptions",
                note: assumptionNote(data.assumption_checks),
                panel: () => (
                  <div className="card">
                    <h2>Assumption checks</h2>
                    {data.assumption_checks.length === 0 ? (
                      <p className="note">This method declared no assumptions to check.</p>
                    ) : (
                      <table>
                        <thead><tr><th>Check</th><th>Outcome</th><th>Detail</th></tr></thead>
                        <tbody>
                          {data.assumption_checks.map((c) => (
                            <tr key={c.name}>
                              <td className="mono">{c.name}</td>
                              <td><Status value={c.outcome} /></td>
                              <td style={{ color: "var(--ink-soft)" }}>{c.detail}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    )}
                  </div>
                ),
              },
              {
                id: "sensitivity",
                label: "Sensitivity",
                panel: () => (
                  <div className="card">
                    <h2>Does it survive another approach</h2>
                    {/*
                      * Stated rather than faked. The run family — forking this
                      * specification and comparing the branches — is a real
                      * capability of this product, and it is offered by the
                      * panel below this object rather than from inside this
                      * tab. §09 forbids retaining a control with no backing
                      * contract, so this says where the capability is instead
                      * of growing a button that would not work.
                      */}
                    <p className="note">
                      A sensitivity family is built by forking this run’s recorded
                      specification and comparing the branches. This run’s forks and
                      their lineage are listed with the run below.
                    </p>
                  </div>
                ),
              },
              {
                id: "reproducibility",
                label: "Reproducibility",
                panel: () => (
                  /* §44 — everything needed to reproduce the number. */
                  <div className="card">
                    <h2>Reproducibility</h2>
                    <div className="kv">
                      <dt>Random seed</dt><dd>{data.random_seed}</dd>
                      <dt>Duration</dt><dd>{data.duration_ms} ms</dd>
                      <dt>Dependencies</dt>
                      <dd className="mono">
                        {Object.entries(data.dependency_versions).map(([k, v]) => `${k} ${v}`).join(" · ")}
                      </dd>
                      <dt>Dataset hash</dt><dd className="mono">{data.input_hashes.dataset_content_hash?.slice(0, 16)}…</dd>
                      <dt>Spec hash</dt><dd className="mono">{data.input_hashes.spec_content_hash?.slice(0, 16)}…</dd>
                      <dt>Isolation</dt>
                      <dd className="mono">
                        {data.sandbox_policy.enforced?.separate_process ? "separate process" : "—"};
                        network {data.sandbox_policy.best_effort?.network_egress_disabled ?? "—"}
                      </dd>
                    </div>
                  </div>
                ),
              },
            ]}
          />
        </>
      )}

      {/*
        Outside the completed branch, deliberately: a run that failed still has
        whatever was written about it, and that is usually the reason somebody
        opened a failure (D213).
      */}
      <ObjectHistoryFor projectId={projectId ?? null} kind="analysis_run"
                        id={runId} onOpenObject={onOpenObject} />
    </>
  );
}

// ---------------------------------------------------------------------------
// Validation (§51)
// ---------------------------------------------------------------------------

/**
 * Whether the validation just requested has finished.
 *
 * This asked `latest.some((r) => r.status !== "running")` — is *any* report
 * not running — and the list holds every report a connection has ever had,
 * newest first. So the second time anybody validated a connection, the old
 * completed report satisfied it on the first poll: the wait ended after two
 * seconds, the spinner stopped, and the screen reloaded showing the *previous*
 * verdict while the new run was still going. A researcher re-checking a result
 * they had already validated would be shown the earlier answer as the current
 * one — and if the new run went the other way, they would never see it unless
 * they reloaded by hand.
 *
 * Three cases, all of them ordinary:
 *
 *  - a run is going, so a report is still `running` — keep waiting;
 *  - a report that was not there before has finished — that is the answer;
 *  - nothing new was queued at all, because the server deduplicates a repeat
 *    of the same request by idempotency key. Then no new report will ever
 *    appear, and waiting for one would spin until the timeout. Two polls with
 *    nothing running is enough to tell that apart from the brief window before
 *    the worker has written the row.
 */
export function settled(
  before: string[], latest: ValidationReport[], poll: number,
): boolean {
  if (latest.some((r) => r.status === "running")) return false;
  return latest.some((r) => !before.includes(r.id)) || poll >= 2;
}

/**
 * Take the reader to a block on this page, and take the keyboard with them.
 *
 * A "jump to" that only scrolls leaves a keyboard user exactly where they
 * were: the next Tab continues from the top of the document, past everything
 * the scroll just skipped. So focus moves too — onto the real control where
 * there is one, and onto the block itself otherwise, which is why the blocks
 * this points at carry `tabIndex={-1}`.
 *
 * `scrollIntoView` is checked rather than called: happy-dom and jsdom have no
 * layout engine, and a missing method here would take the whole screen down in
 * the test that is meant to be proving this works.
 */
function reveal(section: HTMLElement | null, control?: HTMLElement | null) {
  if (!section) return;
  /*
   * A block that folds is opened before it is jumped to (T139). Panels that
   * used to be open now rest closed, and a jump that lands on a closed summary
   * puts the reader in front of a control the browser will not let them focus
   * — the band's whole contract is that it moves to the real control.
   */
  section.querySelectorAll?.("details").forEach((fold) => { fold.open = true; });
  for (let node = section.parentElement; node; node = node.parentElement) {
    if (node instanceof HTMLDetailsElement) node.open = true;
  }
  if (typeof section.scrollIntoView === "function") {
    section.scrollIntoView({ block: "start" });
  }
  (control ?? section.querySelector<HTMLElement>("button") ?? section).focus();
}

/**
 * The shape `graph_projection.shortest_path` answers with
 * (`packages/research-domain/src/throughline_domain/graph_projection.py:279-305`).
 *
 * Local rather than in `lib/api.ts` because this is the only caller. Two
 * different bodies come back from one route: an unconnected pair carries a
 * `note` and an empty path, a connected one carries the nodes and the relation
 * kinds between them. Both are real answers, and neither is an error.
 */
type GraphPath = {
  connected: boolean;
  path: Array<{ id: string; title: string | null; object_type: string }>;
  relations?: string[];
  length?: number;
  /** Whether the projection has caught up with the record, in its own words. */
  staleness: { current: boolean; note: string } | null;
  store?: string;
  /** Present when nothing was found within the depth the route bounded. */
  note?: string;
};

/**
 * How two objects in this project are related at all (plan §4.5.4, slice 3.4).
 *
 * `GET /api/projects/{id}/graph/path` is one of the orphan routes the inventory
 * lists in §3: built, bounded, and reachable from nowhere. It sits beside Trace
 * with the distinction stated once, because this is the product's *fourth*
 * provenance surface and the inventory already records provenance being
 * duplicated across three unrelated mechanisms (§7) — an unexplained fourth
 * would compound exactly that confusion. Trace answers how this number was
 * made; this answers how two objects are related at all, which is a different
 * question with a different answer shape.
 *
 * **The other object is chosen from what this project already has.** The
 * connections list is already on the connection detail, and each row carries
 * the object id of the run that produced it, so the picker costs no request
 * and offers nothing that does not exist — the same rule that builds the alias
 * dropdown from the project's own variables (`variables.tsx:317-320`).
 *
 * **No projection is a reduced feature set, not a failure** (ADR 0002). The
 * route answers 503 with the sentence `ProjectionUnavailable` carries, which
 * already says what still works, and that sentence is printed rather than
 * paraphrased (§104) — in the same shape as the Settings readout
 * (`settings.tsx:980-1000`) so the two cannot read as different situations.
 */
function ConnectedHow({ projectId, sourceObjectId, others }: {
  projectId: string;
  /** The object for the run behind this connection, or null if it had none. */
  sourceObjectId: string | null;
  /** Every other result in this project that has an object to path to. */
  others: Array<{ objectId: string; name: string }>;
}) {
  const [chosen, setChosen] = useState("");
  const [asked, setAsked] = useState<string | null>(null);
  /** Pressed with nothing chosen. A refusal is a sentence, never a grey button. */
  const [needsTarget, setNeedsTarget] = useState(false);
  const answer = useApi<GraphPath>(
    asked && sourceObjectId
      ? `/api/projects/${projectId}/graph/path`
        + `?source_id=${encodeURIComponent(sourceObjectId)}`
        + `&target_id=${encodeURIComponent(asked)}`
      : null);

  // 503 is the one status these routes raise for absence, and its detail is the
  // sentence that says what still works. Any other failure is a real failure:
  // flattening the two would tell a researcher with a broken session that their
  // graph store is merely unconfigured.
  const reduced = answer.error instanceof ApiError && answer.error.status === 503
    ? answer.error.message : null;

  return (
    <div className="card card-tight" id="connection-graph-path" tabIndex={-1}>
      {/*
        Closed at rest (T139). The panel is a question a reader asks
        occasionally and the screen asked it of everyone, twice — once in the
        heading and once in the paragraph under it. The summary is the
        question; its count is how many other results there are to path to,
        which is the fact that decides whether opening it is worth anything.
      */}
      <Fold summary="How are these connected?" count={others.length}>
      <p className="note" style={{ marginTop: 0 }}>
        Trace, on the result above, answers how this number was made. This
        answers how two objects in the project are related at all — through
        whatever chain of recorded relationships joins them, or not at all.
      </p>

      {sourceObjectId === null ? (
        // Principle 7: the control stays and says why, because a missing one
        // teaches that the product cannot do this.
        <p className="note" style={{ marginBottom: 0 }}>
          There is no object in the graph for this connection to start from.
          That object is created by the run that computes a result, and this
          connection has none — a path needs two recorded objects.
        </p>
      ) : others.length === 0 ? (
        <p className="note" style={{ marginBottom: 0 }}>
          Nothing else in this project has a recorded object to path to yet.
          Another result computed from a discovery run is the second object this
          needs.
        </p>
      ) : (
        <>
          <div className="row" style={{ marginTop: 10 }}>
            <label style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <span className="eyebrow" style={{ margin: 0 }}>Related to</span>
              <select value={chosen} aria-label="Another result in this project"
                      onChange={(event) => setChosen(event.target.value)}>
                <option value="">choose a result…</option>
                {others.map((other) => (
                  <option key={other.objectId} value={other.objectId}>{other.name}</option>
                ))}
              </select>
            </label>
            {/*
              Not disabled while nothing is chosen. A greyed control states
              nothing and cannot even take the keyboard, which is how the
              band's jump lands on the block and stops — the refusal is a
              sentence in place instead.
            */}
            <button type="button" className="btn"
                    onClick={() => {
                      if (!chosen) { setNeedsTarget(true); return; }
                      setNeedsTarget(false);
                      setAsked(chosen);
                    }}>
              How are these connected?
            </button>
          </div>

          {needsTarget && !chosen && (
            <p className="note">
              Choose the other result first. A path runs between two recorded
              objects, and only one of them is this screen.
            </p>
          )}

          {reduced !== null && <p className="note">{reduced}</p>}
          {reduced === null && answer.error
            ? <Failure error={answer.error} retry={answer.reload} /> : null}
          {answer.loading && <Loading rows={2} label="Following the recorded relationships" />}

          {answer.data && (
            answer.data.connected ? (
              <>
                <ol className="chain" style={{ marginTop: 10 }}>
                  {answer.data.path.map((node, index) => (
                    <li key={`${node.id}-${index}`}>
                      <span style={{ color: "var(--ink)", fontWeight: 540 }}>
                        {node.title || node.id}
                      </span>{" "}
                      <span className="mono" style={{ color: "var(--ink-faint)" }}>
                        {objectTypeName(node.object_type)}
                        {/* The relation that got here, named as the graph
                            records it — not summarised into "related to". */}
                        {index > 0 && answer.data!.relations?.[index - 1]
                          ? ` · ${answer.data!.relations![index - 1].replace(/_/g, " ")}`
                          : ""}
                      </span>
                    </li>
                  ))}
                </ol>
                <p className="note" style={{ marginBottom: 0 }}>
                  {answer.data.length} step{answer.data.length === 1 ? "" : "s"} apart.
                  A path says the two are related through recorded work. It does
                  not say either one is evidence for the other.
                </p>
              </>
            ) : (
              // The server's own sentence for "no path within the bound",
              // which is careful to say what it does not know.
              <p className="note" style={{ marginBottom: 0 }}>{answer.data.note}</p>
            )
          )}

          {answer.data?.staleness && !answer.data.staleness.current && (
            <p className="note" style={{ marginBottom: 0 }}>
              {answer.data.staleness.note}
            </p>
          )}
        </>
      )}
      </Fold>
    </div>
  );
}

export function ConnectionDetail({ connectionId, projectId, onRecordFinding,
                                  onDraftedReport }: {
  connectionId: string;
  projectId: string;
  /** Open the finding once it is recorded, so the researcher lands on it. */
  onRecordFinding?: (findingId: string) => void;
  /**
   * Open the report once it is drafted, for the same reason.
   *
   * Optional, and where it is absent the band says where the draft went
   * rather than offering a button that opens nothing.
   */
  onDraftedReport?: (artifactId: string) => void;
}) {
  const connections = useApi<Connection[]>(`/api/projects/${projectId}/connections?limit=200`);
  const connection = connections.data?.find((c) => c.id === connectionId);
  const reports = useApi<ValidationReport[]>(`/api/connections/${connectionId}/validations`);
  // Approved display names, so no raw column name reaches the card.
  const variables = useApi<{ labels: Record<string, string> }>(
    `/api/projects/${projectId}/variables`);
  // The stored reading. GET never generates — a card that fired a model call on
  // render would make every list of results cost inferences to look at.
  const summary = useApi<PlainSummary>(
    connection?.analysis_run_id
      ? `/api/analyses/${connection.analysis_run_id}/plain-summary` : null);

  const [chosen, setChosen] = useState<string[]>([]);
  const [validating, setValidating] = useState(false);
  const [error, setError] = useState<unknown>(null);
  /** Whether the derivation chain is open beneath the result. */
  const [tracing, setTracing] = useState(false);

  /*
   * The two blocks the actions band points at, and the control inside the
   * first of them. Held as refs rather than looked up by id at press time,
   * because the band must move focus to the control that is really there —
   * not to whatever happens to answer a selector after the page has changed
   * shape around a failed fetch.
   */
  const validateCard = useRef<HTMLDivElement | null>(null);
  const validateButton = useRef<HTMLButtonElement | null>(null);
  const recordCard = useRef<HTMLDivElement | null>(null);
  /** The path panel, so the band can move to it rather than repeat it. */
  const pathCard = useRef<HTMLDivElement | null>(null);
  /** The report history, so the outcome line can move a reader to the evidence. */
  const reportsCard = useRef<HTMLDivElement | null>(null);

  /*
   * The newest validation, which is the one the Validate button just produced.
   * Newest-first from the server (`list_validations` orders by `created_at`
   * descending), so this is the head and not a scan.
   */
  const latestReport = (reports.data ?? [])[0] ?? null;

  /** Move to the evidence already on this page rather than repeating it. */
  function onSeeReport() {
    const card = reportsCard.current;
    if (!card) return;
    card.scrollIntoView({ behavior: "smooth", block: "start" });
    card.focus({ preventScroll: true });
  }

  /** The report drafted from this connection, if one has been drafted here. */
  const [drafted, setDrafted] = useState<string | null>(null);
  const [drafting, setDrafting] = useState(false);
  const [draftError, setDraftError] = useState<unknown>(null);

  /**
   * Draft a report from this connection.
   *
   * The same route the Reports screen posts to, with the same body: this is a
   * second door onto one capability, not a second implementation of it. The
   * eligibility rule is imported for the same reason.
   */
  // Named apart from the shared `draftReport` it calls: a local function of
  // the same name shadowed the import and called itself until the stack ran
  // out — found by the test that stubs the route.
  async function startDraft() {
    setDrafting(true);
    setDraftError(null);
    try {
      // The same routine the Reports screen uses, so a report drafted from
      // here has its citations checked exactly as one drafted from there.
      const created = await draftReport(projectId, connectionId);
      setDrafted(created.artifact_id);
      onDraftedReport?.(created.artifact_id);
    } catch (err) {
      setDraftError(err);
    } finally {
      setDrafting(false);
    }
  }

  // The profiled schema, so confounders are picked rather than typed.
  const columns = useApi<DatasetColumn[]>(
    connection?.dataset_version_id
      ? `/api/dataset-versions/${connection.dataset_version_id}/columns`
      : null,
  );

  async function validate() {
    setValidating(true); setError(null);
    try {
      await api.post(`/api/connections/${connectionId}/validate`, { confounders: chosen });
      // The suite runs several sandboxed analyses. Poll for the report rather
      // than for the lifecycle state: a connection that fails validation keeps
      // its state, and watching the state would hang until the timeout.
      //
      // Which reports existed *before* this request, because the list keeps
      // every one of them. Read from the server rather than from `reports.data`
      // so a stale render cannot make an old report look new.
      const before = (await api.get<ValidationReport[]>(
        `/api/connections/${connectionId}/validations`)).map((r) => r.id);
      for (let i = 0; i < 24; i++) {
        await new Promise((r) => setTimeout(r, 2000));
        const latest = await api.get<ValidationReport[]>(`/api/connections/${connectionId}/validations`);
        if (settled(before, latest, i)) break;
      }
      connections.reload();
      reports.reload();
    } catch (err) {
      setError(err);
    } finally {
      setValidating(false);
    }
  }

  if (connections.loading && !connection) return <Loading rows={4} label="Reading the connection" />;
  if (!connection) return <Empty
      title="Connection not found"
      hint="It may have been deleted, or belong to another project."
    />;

  // Adjusting a variable for itself is not a confounder test; it is a mistake
  // the interface should make impossible rather than report afterwards.
  const others = (columns.data ?? []).filter(
    (c) => c.name !== connection.left_variable && c.name !== connection.right_variable,
  );
  /*
   * Only the columns the adjusted model can actually use.
   *
   * Adjustment fits a linear regression with the confounders as predictors,
   * and a text column coerces to nothing — every row is dropped and the fit
   * raises "0 complete rows cannot fit 2 predictors". Measured, not reasoned
   * about: on a real dataset the picker offered the site column, a researcher
   * chose the most natural confounder there is, and the run came back
   * *violated* — which reads as "the association did not survive" when nothing
   * had been fitted at all. It was the step that gates the rest of the loop.
   *
   * Offered by what the model needs rather than hidden, because a column that
   * silently vanishes from a list is indistinguishable from a column the
   * dataset does not have.
   */
  const numeric = (c: DatasetColumn) =>
    c.physical_type === "number" || c.physical_type === "integer"
    || c.physical_type === "float" || c.semantic_type === "continuous";
  const candidates = others.filter(numeric);
  const notAdjustable = others.filter((c) => !numeric(c));

  function toggle(name: string) {
    setChosen((current) =>
      current.includes(name) ? current.filter((n) => n !== name) : [...current, name]);
  }

  const labels = variables.data?.labels ?? {};
  const left = labels[connection.left_variable] ?? connection.left_variable;
  const right = labels[connection.right_variable] ?? connection.right_variable;

  /*
   * §80's rule, asked rather than restated: a report starts from a result that
   * was computed. Where it cannot start, the control stays and says why —
   * removing it would leave a researcher unable to tell a capability that does
   * not exist from one they have failed to find.
   */
  const reportAction: ObjectAction = !canDraftReport(connection)
    ? {
        label: "Draft a report from this", kind: "note",
        note: "A report starts from a result that was computed. This connection"
          + " has no recorded analysis run, so there is nothing yet for a report"
          + " to reference.",
      }
    // Drafted here already: offered as the way back to it, never as a second
    // silent draft of the same result. Where nothing can open it, the band
    // says where it went instead of carrying a button that opens nothing.
    : drafted
      ? (onDraftedReport
          ? { label: "Drafted — open it", kind: "action",
              onSelect: () => onDraftedReport(drafted) }
          : { label: "Drafted", kind: "note",
              note: `it is on the Reports screen, as ${drafted}.` })
      : {
          label: drafting ? "Drafting…" : "Draft a report from this",
          kind: "action", busy: drafting, onSelect: () => void startDraft(),
        };

  /*
   * What can be done with this connection, listed where the reader arrives.
   *
   * The two ↓ entries move to the real controls further down rather than
   * repeating them — a second Validate button would be a second thing to keep
   * in step with the first, and the two would eventually disagree about what
   * had been selected.
   *
   * Validate leads. Fixed, not chosen from the connection's state: the band
   * would otherwise change rank as the work progressed, which turns one
   * control into two and is the §123 problem this band exists to remove. It is
   * also the honest order — a result nothing has tried to destroy is not one
   * to record first, and this screen is the easiest place in the product to
   * overclaim.
   */
  const actions: ObjectAction[] = [
    {
      label: "Validate ↓", kind: "scroll", primary: true,
      onSelect: () => reveal(validateCard.current, validateButton.current),
    },
    {
      label: "Record this as a finding ↓", kind: "scroll",
      onSelect: () => reveal(recordCard.current),
    },
    reportAction,
    /*
     * The fourth provenance question, named in the band and answered once, in
     * the panel below. A `scroll` rather than an `action` for the same reason
     * Validate is: the panel owns which other object was chosen, and a second
     * control here would be a second thing to keep in step with it.
     */
    {
      label: "How are these connected? ↓", kind: "scroll",
      onSelect: () => reveal(pathCard.current),
    },
  ];

  return (
    <>
      {/* Canonical names, never raw columns (Part C). */}
      <h1>{left} and {right}</h1>

      {/* The result card carries the sentence, the labelled numbers and the
          permanent provenance strip. It replaced four bare stat tiles, which
          made a reader decode `q = 5.17e-66` before learning what was found. */}
      <ResultCard
        connection={connection}
        labels={labels}
        summary={summary.data ?? null}
        sourceCount={{ sources: 0, datasets: connection.dataset_version_id ? 1 : 0 }}
        /*
         * "Where did this come from", finally answerable.
         *
         * The card has always rendered a Trace control and no caller ever
         * supplied the handler, so the button could not appear — while
         * `ProvenanceChain`, which answers exactly that question, was written
         * and imported by nobody. Two halves of one feature, each complete,
         * never joined. Provenance is the claim this product rests on, so this
         * was the most valuable disconnected wire in the codebase.
         *
         * Offered only when there is an object to walk. A Trace button that
         * opened an empty chain would be worse than none: it would suggest the
         * lineage was checked and found empty.
         */
        onTrace={connection.analysis_object_id
          ? () => setTracing((open) => !open)
          : undefined}
      />

      {tracing && connection.analysis_object_id && (
        <div className="card">
          <ProvenanceChain objectId={connection.analysis_object_id} />
        </div>
      )}

      {/*
        Directly under the result, because this is the screen the Overview's
        loop sends a researcher to in order to take steps 4, 5 and 6, and on
        arrival two of the three were below the fold — recording a finding was
        the seventh block down.
      */}
      <ObjectActions items={actions} />
      {draftError ? <Failure error={draftError} /> : null}

      {/*
        Beside Trace, under one heading with the distinction between them
        stated (plan §4.5.4). The list of other results is built from the
        connections this screen already fetched, so the picker costs no
        request: each row carries `analysis_object_id`, the addressable object
        for the run that produced it (`discovery.py:477-482`).
      */}
      {/* `tabIndex={-1}` so the band can put the keyboard here even in the two
          cases where the panel states a reason instead of offering a control
          — `reveal` focuses the block when there is no button inside it. */}
      <div ref={pathCard} tabIndex={-1}>
        <ConnectedHow
          projectId={projectId}
          sourceObjectId={connection.analysis_object_id ?? null}
          others={(connections.data ?? [])
            .filter((other) => other.id !== connectionId && other.analysis_object_id)
            .map((other) => ({
              objectId: other.analysis_object_id as string,
              // Named the way every other surface names it — approved labels,
              // never a raw column (Part C).
              name: `${labels[other.left_variable] ?? other.left_variable}`
                + ` and ${labels[other.right_variable] ?? other.right_variable}`,
            }))}
        />
      </div>

      <EvidenceGrade
        runId={connection.analysis_run_id}
        quality={connection.evidence_quality}
      />

      {/* `tabIndex={-1}` so the band above can put the keyboard here even
          while the Validate control is still loading its schema. */}
      <div className="card" id="connection-validate" tabIndex={-1} ref={validateCard}>
        <h2>Try to destroy it</h2>
        <p className="note" style={{ marginTop: 0 }}>
          This is <Term id="validation" />.
        </p>
        <Fold summary="What the robustness suite runs" count={4}>
          {/* "Naming no confounders is recorded as not tested — not as
              clean" used to close this paragraph. It is now the line beside
              the button, said out loud instead of folded away, because it is
              what decides the outcome of the press a reader is about to make.
              Kept in one place: this screen is at its word cap, and the cap is
              what noticed the duplication. */}
          <p style={{ marginTop: 0 }}>
            Bootstrap stability, sensitivity to outliers, missingness, and
            adjustment for confounders.
          </p>
        </Fold>

        {/*
          The columns fold (T139). A dataset with a dozen candidates put a
          dozen checkboxes between the result and the button that tests it, and
          the answer for most connections is "none" — which the line under the
          fold still says out loud. The count is how many columns could be
          adjusted for, and the selection rides on the summary while it is
          closed, so nothing chosen can be forgotten behind it.
        */}
        <Fold summary="Adjust for" count={candidates.length}>

        {columns.loading && <Loading rows={2} label="Reading the dataset schema" />}
        {columns.error ? <Failure error={columns.error} retry={columns.reload} /> : null}

        {!connection.dataset_version_id && (
          <p className="note">
            This connection is not linked to a discovery run, so its dataset schema
            cannot be resolved. Validation can still run without adjustment.
          </p>
        )}

        {/*
          * The columns that exist and cannot be used, named.
          *
          * Silently dropping them would leave a researcher looking for the
          * site column and concluding the profile had missed it. Adjusting for
          * a categorical variable is a real thing to want — it needs the model
          * to code it as indicator columns, which it does not do yet — so this
          * says what is missing rather than implying the column is unsuitable.
          */}
        {notAdjustable.length > 0 && (
          <p className="note">
            {notAdjustable.length === 1 ? "One column is" : `${notAdjustable.length} columns are`}
            {" "}not offered here
            {" ("}{notAdjustable.map((c) =>
              (variables.data?.labels ?? {})[c.name] ?? c.name).join(", ")}
            {"): "}
            adjustment fits a regression, and a categorical column would have to
            be coded as indicators first, which this model does not do yet.
          </p>
        )}

        {candidates.length > 0 && (
          <div className="picker">
            {candidates.map((column) => (
              <label className="pick-option" key={column.name} data-on={chosen.includes(column.name)}>
                <input
                  type="checkbox"
                  checked={chosen.includes(column.name)}
                  onChange={() => toggle(column.name)}
                />
                <span className="pick-option-name">
                  {(variables.data?.labels ?? {})[column.name] ?? column.name}
                </span>
                {/* The profile is shown because it is what makes a column a
                    plausible confounder — type, spread, and how much is missing. */}
                <span className="pick-option-meta">
                  {column.semantic_type || column.physical_type}
                  {column.unit ? ` · ${column.unit}` : ""}
                  {column.missing_count > 0 ? ` · ${column.missing_count} missing` : ""}
                </span>
              </label>
            ))}
          </div>
        )}

        </Fold>

        <div className="row" style={{ marginTop: 14 }}>
          <span className="note one-line" style={{ margin: 0 }}>
            {/*
              * What the press will do, not a hint that it might matter.
              *
              * This read "No adjustment — the report will say so", which is
              * true and understates it to the point of being misleading. The
              * suite's fourth check is `confounder_adjustment`, and naming no
              * columns records it as **not tested**, which is a fail: the run
              * cannot pass, and the researcher who pressed the product's own
              * recommended control learns that only from a summary line a
              * screen further down. The outcome of the default press is
              * knowable before the press, so it is said before the press.
              */}
            {chosen.length === 0
              ? "No adjustment is untested, so this cannot pass."
              : `Adjusting for ${chosen.join(", ")}.`}
          </span>
          <button className="btn btn-primary" onClick={validate} disabled={validating}
                  ref={validateButton}>
            {validating ? "Running checks…" : "Validate"}
          </button>
        </div>

        {error ? <div style={{ marginTop: 10 }}><Failure error={error} /></div> : null}
        {validating && (
          <div style={{ marginTop: 12 }}>
            <Loading rows={2} label="Running robustness checks in the sandbox" />
          </div>
        )}

        {/*
          * The outcome, beside the control that produced it.
          *
          * The full report is rendered at the bottom of this screen, which is
          * about a thousand pixels below this button — so pressing Validate
          * changed nothing a reader could see, and the card immediately under
          * it went on saying "this connection has not survived a validation
          * run yet" with no sign that one had been run at all. A control whose
          * result appears off-screen is indistinguishable from one that does
          * nothing, which is the specific way a person decides a product is
          * broken and stops.
          *
          * One line, not a second copy of the report: the verdict, the check
          * that decided it, and a way down to the evidence that is already on
          * the page. `reports` is newest-first from the server.
          */}
        {!validating && latestReport && (
          <p className="note vr-latest" data-passed={latestReport.passed === true}
             style={{ marginTop: 12 }}>
            {/* The same two words the report card below uses, and not
                "validated": a validation report that passed is not the same
                fact as the connection being validated, and this file already
                calls the space beside this button the easiest place in the
                product to overclaim. `passed` is null while a run is still
                going, which is the report's own status, not a verdict. */}
            <Status value={latestReport.passed === null ? latestReport.status
                           : latestReport.passed ? "passed" : "violated"} />
            {" "}
            {/* The pill already says the verdict; the server's summary opens
                by repeating it ("Did not pass: confounder_adjustment"). What
                the reader does not have is which check decided it, so that is
                what is left after the prefix the pill has already covered. */}
            {latestReport.summary.replace(/^Did not pass:\s*/, "")}
            {" "}
            <button className="btn-text" type="button" onClick={onSeeReport}>
              What each check found &darr;
            </button>
          </p>
        )}
      </div>

      {/*
        Recording comes before the reading that supports it. This was the
        seventh block on the screen, roughly two screens below the fold, under
        both the fragility panel and the report history — so the act that
        *follows* a validation sat beneath two panels that are the reading
        around it rather than a gate before it. Nothing about the rule moved:
        the form still belongs to this result, and the connection still travels
        with it.
      */}
      {/*
        The last step of the §137 workflow, and the one that was missing. After
        validation the overview said "Record a finding — NEXT" while the
        interface offered no way to record one: the capability existed in the
        API and was exercised by the suite, and no `api.post` to `/findings`
        existed anywhere in this app. It belongs here rather than on the
        Findings list because a finding is recorded *from* a result, and the
        connection travels with it — which is what makes it checkable later.
      */}
      {/* Wrapped only so the band above has something to move the keyboard
          onto; the card itself is unchanged. */}
      <div id="connection-record" tabIndex={-1} ref={recordCard}>
        <RecordFinding
          projectId={projectId}
          connectionId={connectionId}
          defaultTitle={`${left} tracks ${right}`}
          /*
           * Whether a validation *passed*, not whether one finished.
           *
           * This read `status === "complete"`, which a report gets whichever way
           * it went: `validation.py` writes `status='complete'` for both
           * outcomes and records the verdict in `passed`, with a summary that
           * begins "Did not pass: " when it failed. So a connection whose
           * robustness checks *failed* was reported here as validated, and the
           * caveat below — "this connection has not survived a validation run
           * yet" — was suppressed for exactly the results that most need it.
           *
           * The same screen already prints "violated" for that report a few
           * lines down, so the two halves disagreed with each other, and the
           * half that disagreed in the flattering direction was the one sitting
           * next to the record button — which this file calls the single easiest
           * place in the product to overclaim.
           *
           * `=== true` because `passed` is null while a run is still going, and
           * a validation in flight has not survived anything yet.
           */
          validated={(reports.data ?? []).some((r) => r.passed === true)}
          onRecorded={onRecordFinding}
        />
      </div>

      {/*
        Above the validation reports, because it answers the question a reader
        arrives with. The reports say what was tried; this says what it would
        take for none of it to matter.
      */}
      <Fragility connectionId={connectionId} />
      {/* `tabIndex={-1}` so the outcome line above can put the keyboard here,
          the same way the actions band reaches the Validate control. */}
      <div id="connection-reports" tabIndex={-1} ref={reportsCard}>
        <ValidationReports reports={reports} />
      </div>
    </>
  );
}

/**
 * Why the evidence grade is what it is (§47).
 *
 * This is the screen's most confusing moment and its most important one. An
 * association can read r = 0.90 at q = 5e-66 and still be graded *weak*, because
 * §47 grades evidence from the assumptions the method needed — not from the
 * p-value. Shown as a bare word next to a huge correlation, that looks like a
 * bug; a researcher's first instinct is to distrust the tool rather than the
 * result. So the grade is never shown without the checks that produced it.
 */
function EvidenceGrade({ runId, quality }: { runId: string | null; quality: string }) {
  const { data, error, loading, reload } =
    useApi<AnalysisRun>(runId ? `/api/analyses/${runId}` : null);

  if (!runId) return null;
  if (loading) return <Loading rows={2} label="Reading the assumption checks" />;

  /*
   * "Could not be read" and "nothing was violated" are the same picture — an
   * empty space — and they are opposite answers.
   *
   * This returned `null` for both, so a request that failed left the screen
   * looking exactly as it does when every assumption held. The section above
   * says this explanation is the reason a grade is trustworthy at all; its
   * absence therefore reads as "nothing to explain", which is a claim about
   * the analysis that nothing here established. Silence is the one answer
   * this screen cannot give, for the same reason the fragility panel refuses
   * to give it (D229).
   *
   * `!data` without an error stays silent: that is the state before a request
   * has been made for a run that does not exist, and there is nothing to
   * report about it.
   */
  if (error) {
    return (
      <div className="card card-tight">
        <h3 className="eyebrow">Why the evidence is graded {quality}</h3>
        <Failure error={error} retry={reload} />
      </div>
    );
  }
  if (!data) return null;

  const violated = data.assumption_checks.filter((c) => c.outcome === "violated");
  if (violated.length === 0) return null;

  return (
    <div className="card card-tight">
      {/*
        The grade itself is on the result card above; this is the reasoning
        behind it, and reasoning is what a fold is for (T139). The count is the
        number of violated assumptions, so a reader learns how much is behind
        the word before deciding to read it.
      */}
      <Fold summary={`Why the evidence is graded ${quality}`} count={violated.length}>
      <p style={{ margin: "0 0 8px", color: "var(--ink-soft)", fontSize: 12.5 }}>
        The grade comes from the assumptions the method required, not from the
        q-value. A very small q-value with violated assumptions is still weak
        evidence — which are four separate judgements and are kept separate here.
      </p>
      <ul style={{ margin: 0, paddingLeft: 0, listStyle: "none", display: "grid", gap: 5 }}>
        {violated.map((check) => (
          <li key={check.name} style={{ display: "flex", gap: 9, alignItems: "baseline" }}>
            <Status value={check.outcome} />
            <span className="mono" style={{ fontSize: 11.5 }}>{check.name}</span>
            <span style={{ color: "var(--ink-soft)", fontSize: 12 }}>{check.detail}</span>
          </li>
        ))}
      </ul>
      </Fold>
    </div>
  );
}

/**
 * One validation report, read from its own route (plan §4.5.5, slice 3.4).
 *
 * `GET /api/validations/{report_id}` was built, tested and called by nothing in
 * `apps/web` (inventory §3). It answers with the whole
 * `validation_reports` row plus every check's `evidence` — the numbers the
 * check was decided on, which `validation.py` records for all three checks
 * (`q_value`/`p_value`, `dropped_rows`/`rows_used`/`fraction`, and the columns
 * `flagged`) and which the list above renders nowhere. That is what this
 * opens: not a repeat of the summary, but the arithmetic under each verdict.
 *
 * Fetched on opening. A connection can carry several reports and almost nobody
 * opens all of them, so the request follows the gesture.
 */
type ValidationEvidence = Record<string, unknown>;

type ValidationReportDetail = {
  id: string;
  status: string;
  passed: boolean | null;
  summary: string;
  created_at: string;
  /** Null while the run is still going. */
  finished_at: string | null;
  check_details: Array<{
    name: string;
    outcome: string;
    detail: string;
    analysis_run_id: string | null;
    evidence: ValidationEvidence;
  }>;
};

/** A recorded value, as words. Never rounded, never recomputed — printed. */
function evidenceValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (Array.isArray(value)) return value.length ? value.map(String).join(", ") : "none";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function ValidationReportEvidence({ reportId }: { reportId: string }) {
  const [opened, setOpened] = useState(false);
  const { data, error, loading, reload } = useApi<ValidationReportDetail>(
    opened ? `/api/validations/${reportId}` : null);

  return (
    <details className="disclosure" style={{ marginTop: 10 }}
             onToggle={(e) => { if (e.currentTarget.open) setOpened(true); }}>
      {/* The closed summary states what is inside it (principle 4,
          `ChartTable.tsx:1-21`) — never "more", never a chevron alone. The
          fetch hangs on the toggle, so opening from the keyboard fetches too. */}
      <summary>
        What each check measured, and when this run finished
      </summary>

      <div style={{ marginTop: 8 }}>
        {error ? <Failure error={error} retry={reload} /> : null}
        {loading && <Loading rows={2} label="Reading the validation report" />}
        {data && (
          <>
            <dl className="kv" style={{ marginBottom: 10 }}>
              <dt>Report</dt><dd className="mono">{data.id}</dd>
              <dt>Started</dt><dd>{data.created_at}</dd>
              <dt>Finished</dt>
              <dd>
                {/* Null is a state, not a blank: a run still going has not
                    finished, and an em dash would read as "not recorded". */}
                {data.finished_at ?? "still running"}
              </dd>
            </dl>

            {data.check_details.length === 0 ? (
              <p className="note" style={{ margin: 0 }}>
                This report recorded no checks. That is not a pass — nothing was
                supplied for it to test.
              </p>
            ) : (
              data.check_details.map((check) => (
                <div key={check.name} style={{ marginBottom: 10 }}>
                  <div className="row">
                    <span className="mono">{check.name.replace(/_/g, " ")}</span>
                    <Status value={check.outcome} />
                  </div>
                  {Object.keys(check.evidence ?? {}).length === 0 ? (
                    <p className="note" style={{ margin: "4px 0 0" }}>
                      No figures were recorded for this check.
                    </p>
                  ) : (
                    <dl className="kv" style={{ marginTop: 4 }}>
                      {Object.entries(check.evidence).map(([key, value]) => (
                        <Fragment key={key}>
                          <dt>{key.replace(/_/g, " ")}</dt>
                          <dd className="numeric">{evidenceValue(value)}</dd>
                        </Fragment>
                      ))}
                    </dl>
                  )}
                </div>
              ))
            )}
          </>
        )}
      </div>
    </details>
  );
}

/**
 * The §51 report, which until now the interface ran but never showed.
 *
 * A lifecycle state that changes with no visible reasoning is an unaccountable
 * verdict. Each check cites the analysis run that produced it, so the claim
 * "this survived outlier exclusion" is itself traceable (LAW 1).
 */
function ValidationReports({ reports }: {
  reports: { data: ValidationReport[] | null; error: unknown; loading: boolean; reload: () => void };
}) {
  if (reports.error) return <Failure error={reports.error} retry={reports.reload} />;
  if (reports.loading && !reports.data) return <Loading rows={3} label="Reading validation reports" />;
  if (!reports.data?.length) {
    // Not folded. A disclosure whose body is one empty state is a control that
    // opens onto nothing, and the sentence it would hide is two lines that
    // already say what the summary would have said.
    return (
      <Empty
        title="Not validated yet"
        hint="Nothing has tried to break this connection. Until something does, it stays a candidate."
      />
    );
  }

  return (
    <>
      {reports.data.map((report) => (
        <div className="card" key={report.id}>
          <div className="row" style={{ marginBottom: 8 }}>
            <h2 style={{ margin: 0 }}>Validation report</h2>
            <Status value={report.passed === null ? report.status : report.passed ? "passed" : "violated"} />
          </div>
          {report.summary && <p style={{ color: "var(--ink)" }}>{report.summary}</p>}

          {/*
            The verdict and its sentence stay open; the per-check table is the
            working behind them (T139). The count is how many checks ran, which
            is the number that decides whether a pass means anything.
          */}
          <Fold summary="What each check found" count={report.check_details.length}>
          {report.check_details.length > 0 && (
            <table>
              <thead>
                <tr><th style={{ width: "22%" }}>Check</th><th style={{ width: "14%" }}>Outcome</th><th>Detail</th><th style={{ width: "18%" }}>Computed by</th></tr>
              </thead>
              <tbody>
                {report.check_details.map((check) => (
                  <tr key={check.name}>
                    <td className="mono">{check.name.replace(/_/g, " ")}</td>
                    <td><Status value={check.outcome} /></td>
                    <td style={{ color: "var(--ink-soft)" }}>{check.detail}</td>
                    <td className="mono" style={{ color: "var(--ink-faint)" }}>
                      {check.analysis_run_id ?? "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <p className="note">
            A check recorded as <b>not tested</b> is not a pass. It means nothing was
            supplied for it to test.
          </p>
          <ValidationReportEvidence reportId={report.id} />
          </Fold>
        </div>
      ))}
    </>
  );
}

// ---------------------------------------------------------------------------
// Provenance inspector (§93)
// ---------------------------------------------------------------------------

export function ProvenanceChain({ objectId }: { objectId: string }) {
  const { data, error, loading } = useApi<Provenance>(`/api/objects/${objectId}/provenance`);
  if (error) return <Failure error={error} />;
  if (loading || !data) return <Loading rows={3} />;

  return (
    <div>
      <h3 className="eyebrow">How was this made?</h3>
      <ul className="chain">
        <li style={{ color: "var(--ink)", fontWeight: 540 }}>
          {objectTypeName(data.artifact.object_type)} · {data.artifact.title}
        </li>
        {data.ancestors.map((a) => (
          <li key={a.artifact_id}>
            <span className="mono" style={{ color: "var(--ink-faint)" }}>depth {a.depth}</span>{" "}
            {objectTypeName(a.object_type)} · {a.title}
          </li>
        ))}
      </ul>
      {data.ancestors.length === 0 && <EmptyChain origin={data.origin} />}
    </div>
  );
}

/**
 * What an empty chain means — which is not one thing.
 *
 * This said "This is a source artifact — nothing was derived to make it" for
 * *any* empty ancestor list. An analysis whose lineage edges were never written
 * looks exactly the same from here, and that sentence reports the record as
 * complete rather than missing: it is a provenance claim the screen had no
 * basis for, in the flattering direction, on the one screen whose whole job is
 * not to flatter.
 *
 * The distinction comes from the server, which owns the list of object types
 * that enter a project from outside.
 */
export function EmptyChain({ origin }: { origin?: string }) {
  if (origin === "uploaded") {
    return (
      <p className="note">
        The chain starts here — this came into the project from outside rather
        than being made from something in it.
      </p>
    );
  }
  if (origin === "unrecorded") {
    return (
      <p className="notice" role="status">
        {/*
          A gap, and it reads as one. Something made this, and what made it was
          not written down — so this is a question about the record, not an
          answer about the artifact.
        */}
        Nothing is recorded as having made this. Something did: an artifact of
        this kind is derived from something else, so the chain was not written
        down rather than being empty.
      </p>
    );
  }
  // An older server sends no origin. Saying which of the two this is would be
  // a guess, and guessing wrong is how the original sentence got here.
  return (
    <p className="note">
      No derivation is recorded for this artifact.
    </p>
  );
}
