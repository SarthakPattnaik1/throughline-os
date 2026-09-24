"use client";

/**
 * Searching dataset repositories.
 *
 * Kept separate from `literature.tsx` rather than added as more sources to it,
 * because a dataset and a paper are different objects and the difference is the
 * whole point of this screen. A paper is cited; a dataset is computed on. So
 * the row here leads with licence, file formats and embargo status rather than
 * with authors and venue — those are the fields that decide whether the data
 * can answer anything, and a repository search that returns titles and DOIs
 * looks helpful while telling the researcher nothing.
 *
 * Two display rules follow from that.
 *
 * **Unusable records are shown, not filtered out.** A researcher needs to know
 * that the promising-looking record is a PDF supplement, or is embargoed until
 * 2027 — filtering it would make the search look thinner while hiding the
 * reason. They are shown, dimmed, with the blocker stated.
 *
 * **"Not stated" is never drawn as "no".** Dryad does not list files in its
 * search response, so an empty file list there means the repository did not
 * say, not that the record has none. Those are rendered as an open question in
 * different words from a real blocker.
 *
 * **A usable record can be added, and an unusable one says why instead.** The
 * rail entry beside this one imports a paper in a click; this screen's only
 * onward control was a link out to the repository, so a researcher who found
 * the data left the product to fetch it (D202, plan §4.14.1). The refusal case
 * was already designed — the dimmed record above with its blocker named — so
 * the offer is made only where the record is usable, and the reason it is not
 * offered is the sentence already on the row.
 */

import { useEffect, useState } from "react";
import { ApiError, api } from "@/lib/api";
import { plainText } from "@/lib/plain-text";
import { Empty, Failure } from "./primitives";
import { SearchingSources, SourceChip, SourceMark } from "./SourceMark";

type Usability = {
  usable: boolean;
  blockers: string[];
  unknown: string[];
  readable_files: number;
};

type Dataset = {
  title: string;
  repository: string;
  authors: string[];
  year: number | null;
  doi: string | null;
  description: string;
  url: string;
  licence: string;
  /**
   * `url` is the file's own download address, where the repository gives one
   * (D411). The record's `url` is its landing page — HTML — and importing that
   * was refused as "not tabular". `readable` says whether this workspace can
   * open the format.
   */
  files: Array<{ name: string; format: string; bytes: number | null;
                 url: string | null; readable: boolean }>;
  files_listed: boolean;
  variables: string[];
  rows: number | null;
  embargoed: boolean;
  curated: boolean;
  related_paper_doi: string | null;
  usability: Usability;
};

type Results = {
  query: string;
  results: Dataset[];
  sources: { [name: string]: { ok: boolean; count: number; note: string | null } };
  found: number;
  usable: number;
  unchecked: number;
  note: string;
};

type Repository = { name: string; curated: boolean; note: string };

function bytes(value: number | null): string {
  if (!value) return "";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let n = value;
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i += 1; }
  return `${n < 10 && i > 0 ? n.toFixed(1) : Math.round(n)} ${units[i]}`;
}

/** What the import route returns — the same shape as an uploaded file. */
type Imported = {
  source_id: string;
  workflow_run_id: string;
  ingestion_status: string;
};

export function DataSearch({ projectId, onImported, initialQuery }: {
  /**
   * What to search for, when the screen is opened from a claim (D413). Filled
   * in, not run: searching reaches four repositories, which is the
   * researcher's to start.
   */
  initialQuery?: string;
  /**
   * The project a record would be added to.
   *
   * Optional because this screen searches repositories whether or not a
   * project is open, and a control that cannot work is worse than an absent
   * one (§123). With no project id the search is unchanged and no "Add to this
   * project" appears — there is no project for it to name.
   */
  projectId?: string;
  /**
   * Called with the new source's id once a record has been imported.
   *
   * The screen says the record is in Sources; this is how the caller makes
   * that true — reload the source list, or open the source it just made.
   */
  onImported?: (sourceId: string) => void;
}) {
  const [query, setQuery] = useState(initialQuery ?? "");
  const [repositories, setRepositories] = useState<Repository[] | null>(null);
  const [results, setResults] = useState<Results | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  /*
   * What has been added, and what the server said when adding failed — both
   * keyed by the record's own key, because the refusal belongs on the row it
   * refused and a single screen-level error message would name no record at
   * all (§104).
   */
  const [imported, setImported] = useState<Map<string, Imported>>(new Map());
  const [refused, setRefused] = useState<Map<string, string>>(new Map());
  const [adding, setAdding] = useState<string | null>(null);

  /**
   * Add one record to the project.
   *
   * The body is the four things the importer needs and nothing else: where the
   * file is, what to call it, which repository it came from, and the licence
   * the repository stated. Everything after that — allowlist, file type, size
   * cap — is checked on the server, and its refusal sentence is what the row
   * shows, because this browser cannot know which hosts the installation
   * searches.
   */
  async function add(key: string, record: Dataset, file: Dataset["files"][number],
                     several: boolean) {
    if (!projectId) return;
    setAdding(key);
    setRefused((current) => {
      const next = new Map(current);
      next.delete(key);
      return next;
    });
    try {
      const result = await api.post<Imported>(
        `/api/projects/${projectId}/datasets/import`, {
          // The file, not the record's page (D411).
          url: file.url,
          // With several files in one record, each import needs its own name.
          title: several ? `${record.title} — ${file.name}` : record.title,
          repository: record.repository,
          // Not `|| null` on a falsy string only: "not stated" is a real fact
          // about the record, and the empty string the search returns for it
          // is not a licence.
          licence: record.licence ? record.licence : null,
        });
      setImported((current) => new Map(current).set(key, result));
      onImported?.(result.source_id);
    } catch (err) {
      // §104 — the server's own sentence names the reason: a host that is not
      // one of the repositories this installation searches, a file that is not
      // tabular, a file over the cap. Any of those is information; "could not
      // add" is not.
      setRefused((current) => new Map(current).set(
        key, err instanceof ApiError ? err.message : String(err)));
    } finally {
      setAdding(null);
    }
  }

  useEffect(() => {
    api.get<{ repositories: Repository[] }>("/api/datasets/repositories")
      .then((r) => setRepositories(r.repositories))
      .catch(() => setRepositories(null));
  }, []);

  async function search() {
    if (query.trim().length < 2) return;
    setBusy(true); setError(null); setResults(null);
    try {
      setResults(await api.post<Results>("/api/datasets/search",
                                         { query, limit: 20 }));
    } catch (err) { setError(err); } finally { setBusy(false); }
  }

  return (
    <>
      <h1>Find data</h1>
      <p className="lede">
        Searches Zenodo, Dryad, Dataverse and Figshare at once. Every result
        leads with its licence, its file formats and whether it is embargoed —
        because those decide whether the data can answer a question, and a title
        and a DOI do not.
      </p>

      <div className="lit-search">
        <label className="sr-only" htmlFor="ds-q">Search dataset repositories</label>
        <input
          id="ds-q"
          value={query}
          placeholder="antimicrobial resistance surveillance"
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => { if (event.key === "Enter") void search(); }}
        />
        {/* Plain, for the reason Find papers is: the filled control belongs to
            the step strip, which is on this screen too (T139). */}
        <button className="btn" disabled={busy} onClick={() => void search()}>
          {busy ? "Searching…" : "Search"}
        </button>
      </div>

      {repositories && (
        <p className="lit-caps">
          {repositories.map((r) => (
            <SourceChip key={r.name} name={r.name} note={r.note} />
          ))}
        </p>
      )}

      {error ? <Failure error={error} /> : null}
      {busy && (
        /*
         * The shared panel (T196): every repository named while it is being
         * waited on, and the count taken from the list rather than written in
         * a sentence. The literal this replaced was right when written and
         * one connector from being wrong — which is exactly how Find papers
         * came to say it asked four databases while asking ten.
         */
        <SearchingSources names={(repositories ?? []).map((r) => r.name)}
                          kind="repositories"
                          whenUnknown="the dataset repositories" />
      )}

      {results && (
        <>
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
              <p className="lit-summary">{results.note}</p>

              <ol className="lit-results">
                {results.results.map((record, index) => {
                  const use = record.usability;
                  const formats = [...new Set(record.files.map((f) => f.format)
                    .filter(Boolean))];
                  // The same key the list is drawn with, so what was added and
                  // what was refused stay attached to the row they belong to.
                  const key = `${record.repository}-${record.doi ?? index}`;
                  // What can actually be brought in: a file this workspace
                  // reads, at an address the repository gave for it.
                  const importable = record.files.filter((f) => f.readable && f.url);
                  const fileKey = (name: string) => `${key}::${name}`;
                  const addedFiles = importable
                    .map((f) => imported.get(fileKey(f.name)))
                    .filter((a): a is Imported => a !== undefined);
                  const added = addedFiles[0];
                  const refusals = importable
                    .map((f) => refused.get(fileKey(f.name)))
                    .filter((r): r is string => Boolean(r));
                  return (
                    <li key={key}>
                      {/* Unusable records stay visible and dimmed. Filtering
                          them would hide the reason a promising title is
                          not actually usable. */}
                      <article className="lit-record ds-record"
                               data-usable={use.usable}>
                        <h3>{record.title}</h3>
                        <p className="lit-meta">
                          <SourceMark name={record.repository} size={15} />
                          {record.repository}
                          {record.curated ? " · curated" : " · open deposit"}
                          {record.year && ` · ${record.year}`}
                          {record.authors.length > 0
                            && ` · ${record.authors.slice(0, 3).join(", ")}`}
                          {record.authors.length > 3 && " et al."}
                        </p>

                        <dl className="ds-facts">
                          <div>
                            <dt>Licence</dt>
                            <dd>
                              {record.licence || (
                                <em>not stated — not the same as permissive</em>
                              )}
                            </dd>
                          </div>
                          <div>
                            <dt>Files</dt>
                            <dd>
                              {!record.files_listed ? (
                                <em>not listed by this repository</em>
                              ) : record.files.length === 0 ? (
                                <em>none listed</em>
                              ) : (
                                <>
                                  {record.files.length} ({formats.join(", ")})
                                  {use.readable_files > 0 && (
                                    <> · <b>{use.readable_files} readable here</b></>
                                  )}
                                </>
                              )}
                            </dd>
                          </div>
                          {record.rows !== null && (
                            <div>
                              <dt>Rows</dt>
                              <dd className="numeric">{record.rows.toLocaleString()}</dd>
                            </div>
                          )}
                          {record.related_paper_doi && (
                            <div>
                              <dt>Paper</dt>
                              <dd>
                                <a href={`https://doi.org/${record.related_paper_doi}`}
                                   target="_blank" rel="noreferrer noopener">
                                  {record.related_paper_doi}
                                </a>
                              </dd>
                            </div>
                          )}
                        </dl>

                        {record.variables.length > 0 && (
                          <p className="ds-vars">
                            Variables: {record.variables.slice(0, 12).join(", ")}
                            {record.variables.length > 12
                              && ` … and ${record.variables.length - 12} more`}
                          </p>
                        )}

                        {use.blockers.length > 0 && (
                          <p className="ds-blocked">
                            <b>Not usable here:</b> {use.blockers.join("; ")}.
                          </p>
                        )}
                        {/* Stated separately from a blocker: something not
                            checked is not something ruled out. */}
                        {use.unknown.length > 0 && (
                          <p className="ds-unknown">{use.unknown.join("; ")}.</p>
                        )}

                        {record.description && (() => {
                          // Its words, not its markup: read through an inert
                          // parser and rendered as escaped text (CodeQL #29).
                          const words = plainText(record.description);
                          return words ? (
                            <p className="lit-abstract">
                              {words.slice(0, 300)}
                              {words.length > 300 && "…"}
                            </p>
                          ) : null;
                        })()}

                        <footer className="lit-actions">
                          {record.files.length > 0 && (
                            <span className="lit-found numeric">
                              {bytes(record.files.reduce(
                                (sum, f) => sum + (f.bytes ?? 0), 0))}
                            </span>
                          )}
                          {record.embargoed && (
                            <span className="ds-embargo">embargoed</span>
                          )}
                          {record.url && (
                            <a href={record.url} target="_blank"
                               rel="noreferrer noopener">Open record</a>
                          )}

                          {/*
                            §4.14.1 — the same affordance and the same word as
                            Find papers' Add (`literature.tsx:428-434`), because
                            it is the same act on the rail entry beside it. The
                            wording is longer here for one reason: a dataset row
                            already carries an "Open record" link out to the
                            repository, and "Add" beside it read as "add to a
                            list on this screen".
                          */}
                          {projectId && use.usable && importable.map((file) => {
                            const k = fileKey(file.name);
                            const done = imported.has(k);
                            const several = importable.length > 1;
                            return (
                              <button
                                key={k}
                                className="btn"
                                disabled={done || adding === k}
                                onClick={() => void add(k, record, file, several)}
                              >
                                {done ? (several ? `${file.name} is in this project`
                                                 : "In this project")
                                  : adding === k ? "Adding…"
                                  : several ? `Add ${file.name}`
                                  : "Add to this project"}
                              </button>
                            );
                          })}
                        </footer>

                        {/*
                          Where it went, in words. "Added" alone leaves a
                          researcher looking for it, and the answer — Sources,
                          the same door an uploaded file comes through — is the
                          one thing that makes this screen part of the loop
                          rather than a search box beside it.
                        */}
                        {added && (
                          <p className="note" role="status"
                             style={{ margin: "6px 0 0" }}>
                            In Sources now, being read like any file you
                            upload. Its ingestion state is{" "}
                            <span className="mono">{added.ingestion_status}</span>.
                          </p>
                        )}

                        {refusals.map((reason) => (
                          // The server's sentence, in place, as a sentence —
                          // never a disabled button and never a toast.
                          <p key={reason} className="ds-blocked" role="alert"
                             style={{ margin: "6px 0 0" }}>
                            Not added: {reason}
                          </p>
                        ))}

                        {projectId && use.usable && importable.length === 0 && (
                          // Said rather than offered and refused: nothing here
                          // gave a file address to import from.
                          <p className="ds-unknown" style={{ margin: "6px 0 0" }}>
                            {record.files_listed
                              ? "The repository gave no download address for its files. "
                              : "This repository does not list its files in search. "}
                            Open the record, download the file, and upload it to
                            Sources.
                          </p>
                        )}
                      </article>
                    </li>
                  );
                })}
              </ol>

              <p className="pat-foot">
                Nothing is downloaded by searching. Records are not merged
                across repositories — the same data deposited in two places is
                two records with different licences, files and versions, and
                collapsing them would hide the difference that matters.
              </p>
            </>
          )}
        </>
      )}
    </>
  );
}
