"use client";

/**
 * The cockpit's right column: what this run is joined to, and what state it is in.
 *
 * §09 asks UI_02's right column for "Context/Notes, linked connection/source/
 * data, validation controls and full provenance", and it held one sentence and
 * a fold about the installation. So a researcher looking at a result could not
 * see the connection it produced, whether anything had tried to destroy it, or
 * what it was computed from — all three of which lived in other sections. That
 * is the shape of the whole product's problem in one column.
 *
 * **What it does not do is validate.** Choosing which columns to adjust for is
 * a claim about the science, made against the dataset's own schema, and the
 * Connections screen already has that control with the profile beside each
 * candidate. A second copy here would be a second implementation of a decision,
 * which is worse than a link — so this says the state, says what adjustment
 * would mean, and opens the place where the columns are listed. One
 * capability, two doors.
 */

import { useApi } from "@/lib/useApi";
import type { Connection } from "@/lib/api";
import { Fold, Loading } from "./primitives";
import { StateMark } from "./primitives";
import { Term } from "./term";
import {
  IconChevronRight, IconConnections, IconDataset, IconLink, IconNote,
} from "./icons";

type Note = {
  id: string;
  body: string;
  author_kind: "human" | "model";
  author: string;
  created_at: string;
};

export function AnalysisContext({ projectId, runId, onOpenConnection,
                                  onOpenLineage }: {
  projectId: string;
  runId: string;
  /** The connection this run produced, where validation is decided. */
  onOpenConnection?: (connectionId: string) => void;
  /** The dataset this run was computed from. */
  onOpenSource?: (sourceId: string) => void;
  /** The project's lineage, focused on this run. */
  onOpenLineage?: (objectId: string) => void;
}) {
  const connections = useApi<Connection[]>(
    `/api/projects/${projectId}/connections?limit=200`);
  const link = connections.data?.find((c) => c.analysis_run_id === runId) ?? null;

  /*
   * The run's own notes, read from the object the analysis has in the graph.
   *
   * Only fetched once the connection has named that object: the journal is
   * addressed by research-object id, and asking for it with a run id returns a
   * 404 that would read on screen as "this run has no notes".
   */
  const journal = useApi<{ notes: Note[] }>(
    link?.analysis_object_id
      ? `/api/projects/${projectId}/objects/${link.analysis_object_id}/journal`
      : null,
    [link?.analysis_object_id],
  );

  return (
    <>
      <h3 className="eyebrow">Context</h3>
      <p className="note one-line">
        An analysis is one run, with its seed and its assumption checks.
      </p>

      {connections.loading && <Loading rows={2} />}

      {/* ---- what this run is joined to ------------------------------- */}
      {link && (
        <section className="ax-block">
          <h4 className="ax-name">Linked objects</h4>
          {/*
            * Rows, not a bulleted list of links.
            *
            * UI_02 draws each linked object as a bordered row: a mark for what
            * kind of thing it is, its name, what it is, and a chevron where it
            * opens. Ours was a link and a grey line under it, so the column
            * that is supposed to say "here is everything this run touches"
            * read as two sentences — and nothing about it said which of them
            * you could press.
            */}
          <ul className="ax-objects">
            <li>
              <button className="ax-object" type="button"
                      onClick={() => onOpenConnection?.(link.id)}>
                <span className="ax-object-icon" aria-hidden>
                  <IconConnections size={15} />
                </span>
                <span className="ax-object-body">
                  <span className="ax-object-name">
                    {link.left_variable} ↔ {link.right_variable}
                  </span>
                  {/* The compact mark: the pill's phrase, "Checked — survived
                      the robustness checks", was longer than the row it sat in
                      and wrapped the row to four lines. */}
                  <span className="ax-meta">
                    Connection · <StateMark value={link.lifecycle_status} />
                  </span>
                </span>
                <span className="ax-object-go" aria-hidden>
                  <IconChevronRight size={14} />
                </span>
              </button>
            </li>
            {link.dataset_name && (
              /* No chevron and no button: this row names the input and there
                 is nowhere for it to go from here. A chevron on a row that
                 does not open is the dead-end door again. */
              <li className="ax-object ax-object-flat">
                <span className="ax-object-icon" aria-hidden>
                  <IconDataset size={15} />
                </span>
                <span className="ax-object-body">
                  <span className="ax-object-name">{link.dataset_name}</span>
                  {/* The version is on the connection as an id, not a number:
                      saying "exact input" is what is true without inventing a
                      version label the payload does not carry. */}
                  <span className="ax-meta">Dataset · exact input</span>
                </span>
              </li>
            )}
          </ul>
        </section>
      )}

      {/* ---- whether anything has tried to destroy it ------------------ */}
      {link && (
        <section className="ax-block">
          <h4 className="ax-name">Validation</h4>
          <p className="note" style={{ margin: "0 0 8px" }}>
            {link.lifecycle_status === "validated"
              ? "This connection survived the checks it was put through."
              : "Not run. A result is a candidate until something has tried to "
                + "destroy it."}
          </p>
          {/*
            * The control is a door, not a copy.
            *
            * Which columns to adjust for is a claim about the science, made
            * against the dataset's own schema — the Connections screen lists
            * every candidate column with its type, spread and missingness
            * beside it, which is what makes one a plausible confounder. A
            * compact second picker here would be a second implementation of a
            * decision, and the two would drift.
            */}
          <button className="btn" type="button"
                  onClick={() => onOpenConnection?.(link.id)}>
            {link.lifecycle_status === "validated"
              ? "Open the connection" : "Validate this connection →"}
          </button>
          <p className="note" style={{ marginTop: 8 }}>
            Adjusting for a <Term id="confounder" /> is chosen against the
            dataset&rsquo;s columns, which are listed with the connection.
          </p>
        </section>
      )}

      {/* ---- what it was computed from --------------------------------- */}
      {link && (
        <section className="ax-block">
          <h4 className="ax-name">Provenance</h4>
          <p className="ax-chain">
            <span className="ax-object-icon" aria-hidden><IconLink size={14} /></span>
            <span>
              {link.dataset_name ?? "dataset"}
              <span aria-hidden> → </span>
              this run
              <span aria-hidden> → </span>
              {link.left_variable} ↔ {link.right_variable}
            </span>
          </p>
          {link.analysis_object_id && onOpenLineage && (
            <button className="btn-text" type="button"
                    onClick={() => onOpenLineage(link.analysis_object_id!)}>
              Open the complete lineage →
            </button>
          )}
        </section>
      )}

      {!connections.loading && !link && (
        <p className="note">
          This run is not linked to a connection, so there is nothing recorded
          about what it was promoted into. Runs specified by hand stay this way
          until one is.
        </p>
      )}

      {/* ---- what was written about it --------------------------------- */}
      {(journal.data?.notes.length ?? 0) > 0 && (
        <Fold summary="Notes" count={journal.data!.notes.length}>
          {journal.data!.notes.map((note) => (
            <p key={note.id} className="ax-note">
              <span className="ax-object-icon" aria-hidden><IconNote size={14} /></span>
              {note.body}
              <span className="ax-meta">
                {note.author_kind === "model"
                  ? "written by a model" : note.author}
              </span>
            </p>
          ))}
        </Fold>
      )}
    </>
  );
}
