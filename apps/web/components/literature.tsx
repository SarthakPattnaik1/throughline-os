"use client";

/**
 * Literature search across several databases at once.
 *
 * The design problem here is not the search box, it is what to do when four
 * upstream APIs disagree — which they do constantly. arXiv has the preprint
 * year, Crossref the version of record; OpenAlex expands author initials,
 * PubMed does not.
 *
 * Two decisions follow from that.
 *
 * **A disagreement is shown, not resolved.** Where the databases differ, the
 * preferred value is displayed with the others underneath it and the source
 * named. Silently picking one is how a bibliography ends up with a date nobody
 * can defend to a reviewer.
 *
 * **A source that fails is reported beside the results that arrived**, never as
 * an error page. A researcher searching four databases should not lose three
 * because one is having an outage — and they need to know which one was
 * missing, because "no results in PubMed" and "PubMed did not answer" are
 * different facts.
 */

import { useEffect, useState } from "react";
import { ApiError, api } from "@/lib/api";
import { Empty, Failure, Loading } from "./primitives";
import { SearchingSources, SourceChip, SourceMark } from "./SourceMark";
import { PaperReader } from "./literature/PaperReader";
import type { Excerpt, PaperSource } from "@/lib/literature/excerpt";

type Disagreement = {
  preferred: { value: unknown; source: string };
  also_reported: Array<{ value: unknown; source: string }>;
};

type Paper = {
  title: string;
  authors: string[];
  year: number | null;
  doi: string | null;
  arxiv_id: string | null;
  pmid: string | null;
  venue: string;
  abstract: string;
  url: string;
  pdf_url: string;
  open_access: boolean | null;
  cited_by: number | null;
  source: string;
  provenance: { [field: string]: string };
  disagreements: { [field: string]: Disagreement };
};

type Results = {
  query: string;
  results: Paper[];
  sources: { [name: string]: { ok: boolean; count: number; note: string | null } };
  found: number;
  returned_by_sources: number;
  note: string;
};

/** An excerpt as the server returns it (§205). */
type KeptExcerpt = {
  id: string;
  source_id: string;
  page: number;
  citation: string;
  context: string | null;
  source_title: string;
};

type Capability = {
  name: string; ready: boolean; polite: boolean; rate_per_second: number;
  note: string | null;
};

export function Literature({ projectId, initialQuery }: {
  projectId: string;
  /**
   * A topic the caller already knows, so "find papers about soil" arrives with
   * the box filled in (T189). `DataSearch` has taken one since D413; this is
   * the same door on the other search, and the bar reaches both.
   */
  initialQuery?: string;
}) {
  const [query, setQuery] = useState(initialQuery ?? "");
  const [capabilities, setCapabilities] = useState<Capability[] | null>(null);
  const [results, setResults] = useState<Results | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [imported, setImported] = useState<Set<string>>(new Set());
  /*
   * What happened when the full text was asked for, per record (D410): the
   * server's own sentence, whether it is "being read" or a refusal.
   */
  const [fullText, setFullText] = useState<Map<string, { ok: boolean; text: string }>>(
    new Map());
  /*
   * The paper currently being read, and what has been taken from papers.
   *
   * Reading lives here rather than on a route of its own, which was the first
   * mistake: a reader a researcher has to navigate away to is a second
   * Literature feature, not a continuation of this one. Finding a paper and
   * marking it are one activity.
   */
  const [reading, setReading] = useState<Paper | null>(null);
  /*
   * The source the paper being read corresponds to, once it is in the project.
   *
   * Marks attach to a source, and a paper is only a source after import — so
   * this is filled in when the reader opens, by the same idempotent import that
   * taking an excerpt uses.
   */
  const [readingSource, setReadingSource] = useState<string | null>(null);
  /*
   * Reading a PDF the researcher already has, which the search cannot reach.
   *
   * Kept in the same section rather than on a route of its own: "the paper I
   * downloaded last week" and "the paper I just found" are the same activity,
   * and separating them was what produced two Literature features.
   */
  const [readingOwn, setReadingOwn] = useState(false);
  /** Excerpts as the server has them — the board proper. */
  const [kept, setKept] = useState<KeptExcerpt[]>([]);

  /*
   * What is already on the board.
   *
   * Loaded rather than started empty, because the board was previously React
   * state and therefore emptied by navigating away — which made §205 a
   * demonstration rather than a place to put anything.
   */
  /*
   * A request that failed is not an empty board.
   *
   * `.catch(() => setKept([]))` made those two the same thing, and the board
   * is rendered only when it has entries — so a researcher who had taken
   * excerpts saw the section disappear, with nothing saying why. The comment
   * above records that this load exists precisely because an empty board made
   * §205 a demonstration rather than a place to put anything; showing an
   * empty one after a failed request re-creates that, and invites the same
   * excerpt to be taken twice.
   */
  const [keptError, setKeptError] = useState<unknown>(null);
  const [keptNonce, setKeptNonce] = useState(0);

  useEffect(() => {
    let live = true;
    api.get<{ excerpts: KeptExcerpt[] }>(`/api/projects/${projectId}/excerpts`)
      .then((r) => { if (live) { setKept(r.excerpts); setKeptError(null); } })
      .catch((err) => { if (live) setKeptError(err); });
    return () => { live = false; };
  }, [projectId, keptNonce]);

  useEffect(() => {
    api.get<{ sources: Capability[] }>("/api/literature/sources")
      .then((r) => setCapabilities(r.sources))
      .catch(() => setCapabilities(null));
  }, []);

  async function search() {
    if (query.trim().length < 2) return;
    setBusy(true); setError(null); setResults(null);
    try {
      setResults(await api.post<Results>("/api/literature/search",
                                         { query, limit: 20 }));
    } catch (err) { setError(err); } finally { setBusy(false); }
  }

  /**
   * A search record as the reader's idea of a paper.
   *
   * Worth doing explicitly rather than passing the record through: the reader
   * needs exactly five things to build a citation, and every one of them here
   * has been reconciled across four databases — which is why a paper opened
   * this way has a year and one opened from disk usually does not.
   */
  function sourceFor(record: Paper): PaperSource {
    return {
      id: record.doi || record.arxiv_id || record.pmid || record.title,
      title: record.title,
      authors: record.authors,
      year: record.year ?? undefined,
      doi: record.doi ?? undefined,
    };
  }

  /**
   * Make sure the paper is in the project, and say which source it is.
   *
   * An excerpt has to attach to a source row, and a paper is only a source once
   * it has been imported — so taking a piece of a paper the project does not
   * have yet would otherwise fail on an ordering rule the researcher cannot
   * see. The import is idempotent on the record's identifier, so doing it here
   * costs nothing when the paper is already present.
   */
  async function ensureImported(record: Paper): Promise<string> {
    const key = record.doi || record.arxiv_id || record.pmid || record.title;
    const result = await api.post<{ source_id: string }>(
      `/api/projects/${projectId}/literature/import`, record);
    setImported((current) => new Set(current).add(key));
    return result.source_id;
  }

  async function add(record: Paper) {
    try {
      await ensureImported(record);
    } catch (err) { setError(err); }
  }

  /**
   * Add the paper and read its open-access text into it (D410).
   *
   * An added paper was a citation and nothing more — no passages, so its
   * claims could not be located. The server fetches the address the import
   * recorded, through the guarded fetcher, into the same source.
   */
  async function addWithText(record: Paper, key: string) {
    const say = (ok: boolean, text: string) =>
      setFullText((current) => new Map(current).set(key, { ok, text }));
    try {
      const sourceId = await ensureImported(record);
      const answer = await api.post<{ note: string }>(
        `/api/projects/${projectId}/sources/${sourceId}/full-text`, {});
      say(true, answer.note);
    } catch (err) {
      say(false, err instanceof ApiError ? err.message : String(err));
    }
  }

  /**
   * Put a circled region on the board, and keep it there.
   *
   * The server re-checks everything §205 requires rather than trusting what
   * arrives — the reader already refuses to build an incomplete excerpt, and
   * this is the same guarantee held against a caller that is not the reader.
   */
  /**
   * Open a paper for reading, and give its ink somewhere to live.
   *
   * The import happens here rather than at the first mark, so that annotations
   * made in the first few seconds are kept like all the others. It is
   * idempotent, so a paper already in the project costs nothing.
   */
  async function read(record: Paper) {
    setReading(record);
    setReadingSource(null);
    try {
      setReadingSource(await ensureImported(record));
    } catch (err) {
      // Readable either way: without a source the marks stay in memory and the
      // reader says so, which is better than refusing to open the paper.
      setError(err);
    }
  }

  async function keepExcerpt(record: Paper, excerpt: Excerpt) {
    try {
      const sourceId = await ensureImported(record);
      const stored = await api.post<KeptExcerpt>(
        `/api/projects/${projectId}/excerpts`, {
          source_id: sourceId,
          page: excerpt.page,
          region: excerpt.region,
          citation: excerpt.citation,
          context: excerpt.context,
          title: excerpt.source.title,
        });
      setKept((current) => [stored, ...current]);
    } catch (err) { setError(err); }
  }

  if (readingOwn) {
    return (
      <>
        <div className="lit-reading-bar">
          <button className="btn" onClick={() => setReadingOwn(false)}>
            Back to search
          </button>
        </div>
        {/*
          * A PDF the researcher already has. Excerpts from it are not kept:
          * the board attaches to a source in this project, and a file off the
          * desktop is not one. Offering to keep it would either invent a
          * source with no provenance or fail at the last step, and both are
          * worse than the reader plainly being a reader here.
          */}
        <PaperReader />
      </>
    );
  }

  if (reading) {
    return (
      <>
        <div className="lit-reading-bar">
          <button className="btn"
                  onClick={() => { setReading(null); setReadingSource(null); }}>
            Back to results
          </button>
          <span className="lit-reading-title">{reading.title}</span>
        </div>
        <PaperReader
          opening={{ source: sourceFor(reading), pdfUrl: reading.pdf_url }}
          marks={readingSource
            ? { sourceId: readingSource, projectId }
            : undefined}
          onExcerpt={(excerpt) => void keepExcerpt(reading, excerpt)}
        />
      </>
    );
  }

  return (
    <>
      <h1>Find papers</h1>
      {/*
        * The lede names no sources, and that is the fix rather than an
        * omission. It used to read "Searches OpenAlex, Crossref, arXiv and
        * PubMed at once" — true when it was written and wrong by six sources by
        * the time anybody noticed, because bioRxiv, Europe PMC, Semantic
        * Scholar, DOAJ, OpenAIRE and Zotero were added underneath it. A
        * researcher reading it would conclude their preprints and their own
        * library were not being searched, when they were.
        *
        * The count comes from the same capabilities the chips below are built
        * from, so the sentence cannot drift from what actually runs: adding a
        * connector changes both, or neither.
        */}
      <p className="lede">
        {/* "all 10 source" — the count was made structural and the noun was
            left singular beside it (T196). */}
        Searches {capabilities ? `all ${capabilities.length} sources` : "every source"}
        {" "}below at once. Records that appear in more than one are merged — and
        where the databases disagree, every version is kept rather than quietly
        resolved.
      </p>

      <div className="lit-search">
        <label className="sr-only" htmlFor="lit-q">Search literature</label>
        <input
          id="lit-q"
          /*
           * A search field, not a text box. Without this the browser gives it
           * none of the affordances a search input has — no clear control, the
           * wrong on-screen keyboard and Enter key on a phone, and none of the
           * semantics assistive technology uses to announce it as a search.
           */
          type="search"
          value={query}
          placeholder="antibiotic consumption and resistance"
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => { if (event.key === "Enter") void search(); }}
        />
        {/* Plain: the strip above carries the loop's one filled act, and this
            screen is not where a step is taken (T139). */}
        <button className="btn" disabled={busy} onClick={() => void search()}>
          {busy ? "Searching…" : "Search"}
        </button>
        <button className="btn" onClick={() => setReadingOwn(true)}>
          Read a PDF you have
        </button>
      </div>

      {capabilities && (
        <p className="lit-caps">
          {capabilities.map((c) => (
            <SourceChip key={c.name} name={c.name} polite={c.polite}
                        note={c.polite ? null
                              : "No contact address, so this source gives us a "
                                + "slower rate limit. Add one in Settings."} />
          ))}
        </p>
      )}

      {error ? <Failure error={error} /> : null}
      {/* Named, not counted, and counted from the list rather than from a
          literal: this said "Asking four databases" while asking ten (T196). */}
      {busy && (
        <SearchingSources names={(capabilities ?? []).map((c) => c.name)}
                          kind="databases" />
      )}

      {results && (
        <>
          {/* Per-source status, always. "No results in PubMed" and "PubMed did
              not answer" are different facts and must not look alike. */}
          <div className="lit-status">
            {Object.entries(results.sources).map(([name, status]) => (
              <SourceChip key={name} name={name} ok={status.ok}
                          count={status.count} note={status.note} />
            ))}
          </div>

          {results.results.length === 0 ? (
            <Empty title="Nothing found"
                   hint="Try fewer or more general terms." />
          ) : (
            <>
              <p className="lit-summary">
                {results.found} papers from {results.returned_by_sources} records.{" "}
                {results.note}
              </p>

              <ol className="lit-results">
                {results.results.map((record, index) => {
                  const key = record.doi || record.arxiv_id
                    || record.pmid || record.title;
                  const disagreements = Object.entries(record.disagreements);
                  return (
                    <li key={index}>
                      <article className="lit-record">
                        <h3>{record.title}</h3>
                        <p className="lit-meta">
                          {record.authors.slice(0, 4).join(", ")}
                          {record.authors.length > 4 && " et al."}
                          {record.year && ` · ${record.year}`}
                          {record.venue && ` · ${record.venue}`}
                          {record.cited_by !== null && (
                            <span className="numeric">
                              {" "}· cited {record.cited_by.toLocaleString()}
                            </span>
                          )}
                        </p>

                        {record.abstract && (
                          <p className="lit-abstract">
                            {record.abstract.slice(0, 340)}
                            {record.abstract.length > 340 && "…"}
                          </p>
                        )}

                        {disagreements.length > 0 && (
                          // The half a citation manager throws away.
                          <details className="lit-disagree">
                            <summary>
                              The databases disagree on{" "}
                              {disagreements.map(([f]) => f).join(", ")}
                            </summary>
                            {disagreements.map(([field, value]) => (
                              <p key={field}>
                                <b>{field}</b>: using{" "}
                                <q>{String(value.preferred.value)}</q> from{" "}
                                {value.preferred.source}
                                {value.also_reported.map((other, i) => (
                                  <span key={i}>
                                    ; {other.source} says{" "}
                                    <q>{String(other.value)}</q>
                                  </span>
                                ))}
                              </p>
                            ))}
                          </details>
                        )}

                        <footer className="lit-actions">
                          <span className="lit-found">
                            {/* The mark of every database that returned this
                                record, so a merge is visible at a glance. */}
                            {record.source.split("+").map((name) => (
                              <SourceMark key={name} name={name.trim()} size={15} />
                            ))}
                            found via {record.source.replace(/\+/g, " + ")}
                          </span>
                          {record.open_access && (
                            <span className="lit-oa">open access</span>
                          )}
                          {record.url && (
                            <a href={record.url} target="_blank"
                               rel="noreferrer noopener">Open</a>
                          )}
                          {/*
                            * Offered only where the paper is open access and
                            * names a PDF. This does not route around a paywall
                            * and does not fetch anything until it is pressed —
                            * the note below the results says so, and this is
                            * the deliberate act it describes.
                            */}
                          {record.open_access && record.pdf_url && (
                            <button className="btn"
                                    onClick={() => void read(record)}>
                              Read and mark
                            </button>
                          )}
                          <button
                            className="btn"
                            disabled={imported.has(key)}
                            onClick={() => void add(record)}
                          >
                            {imported.has(key) ? "In this project" : "Add"}
                          </button>
                          {/* The same deliberate act as Read, with the text
                              kept in the project rather than only shown. */}
                          {record.open_access && record.pdf_url && (
                            <button
                              className="btn"
                              disabled={fullText.get(key)?.ok === true}
                              onClick={() => void addWithText(record, key)}
                            >
                              {fullText.get(key)?.ok ? "Being read"
                                : "Add and read the full text"}
                            </button>
                          )}
                        </footer>
                        {fullText.get(key) && (
                          <p className={fullText.get(key)!.ok ? "note" : "ds-blocked"}
                             role={fullText.get(key)!.ok ? "status" : "alert"}
                             style={{ margin: "6px 0 0" }}>
                            {fullText.get(key)!.ok ? "" : "Not read: "}
                            {fullText.get(key)!.text}
                          </p>
                        )}
                      </article>
                    </li>
                  );
                })}
              </ol>

              {keptError != null && (
                /*
                 * Said where the board would be, because that is where a
                 * reader looks for what they have taken.
                 */
                <section className="lit-board">
                  <h2>Taken from papers</h2>
                  <Failure error={keptError}
                           retry={() => setKeptNonce((n) => n + 1)} />
                </section>
              )}

              {keptError == null && kept.length > 0 && (
                /*
                 * Kept here rather than inside the reader so that going back to
                 * the results does not discard what was taken. This is the
                 * board in its smallest honest form — every entry carries the
                 * citation §205 requires, and nothing appears here that could
                 * not state where it came from.
                 */
                <section className="lit-board">
                  <h2>Taken from papers</h2>
                  <ul>
                    {kept.map((excerpt) => (
                      <li key={excerpt.id}>
                        <strong>{excerpt.citation}</strong>
                        {/* The page, because a citation without one sends a
                            reader to the whole paper. */}
                        <span className="numeric"> · p. {excerpt.page}</span>
                        {excerpt.context && (
                          <span> — {excerpt.context.slice(0, 160)}…</span>
                        )}
                      </li>
                    ))}
                  </ul>
                </section>
              )}

              <p className="pat-foot">
                Add imports the citation only. Fetching a PDF is a separate,
                deliberate act — this never routes around a paywall — and where a
                paper is open access, <em>Add and read the full text</em> is that
                act: its text is read into the project, so its claims can be found.
              </p>
            </>
          )}
        </>
      )}
    </>
  );
}
