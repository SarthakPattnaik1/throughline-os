"use client";

/**
 * The figures this project has made.
 *
 * Exporting a figure creates a record: the spec, the critic's report, and a
 * lineage edge back to the analysis it draws. Nothing listed those records and
 * nothing opened one, so a figure was created and then reachable only by
 * somebody who had kept its id — the interface downloaded the file and moved
 * on. `GET /visuals/{id}` and `PATCH /visuals/{id}` both sat with no caller,
 * and the table has been indexed on `(project_id, created_at DESC)` since it
 * was written, for a query nobody had made.
 *
 * **The edit is deliberately narrow.** A title and a caption are how a figure
 * reads; the rows and variables are what it claims. The domain refuses the
 * second — *"a redraw would leave the old statistics on new data"* — so this
 * offers only the first, and reports the refusal in the server's own words if
 * one ever gets through.
 *
 * Editing re-runs the critic, which is the point of editing here rather than
 * in the file afterwards: a caption that overstates the result is exactly what
 * the critic blocks, and a caption fixed in an exported PNG is fixed nowhere.
 */

import { useState } from "react";
import { ApiError, api } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { ComposeFigure } from "./composefigure";
import { Empty, Failure, Loading } from "./primitives";

/** Kinds with no flat drawing, so no panel in a composed figure. */
const NO_PANEL = ["surface", "line"];

export type SavedFigure = {
  id: string;
  visual_type: string;
  publishable: boolean;
  created_at: string;
  analysis_run_id: string;
  finding_id: string | null;
  title: string | null;
  caption: string | null;
};

type Critique = {
  publishable: boolean;
  critiques: Array<{
    check: string;
    outcome: string;
    severity: string;
    detail: string;
    fix_applied?: string;
  }>;
};

/** What the critic blocked, as distinct from what it merely noted. */
export function blocking(critique: Critique | null): Critique["critiques"] {
  if (!critique) return [];
  return critique.critiques.filter(
    (c) => c.severity === "blocking" && c.outcome === "violated");
}

export function SavedFigures({ projectId, focusId = null }: {
  projectId: string;
  /**
   * The figure to land on. The palette can jump to a saved figure by title
   * or id, and a jump that lands on the list with nothing marked is a control
   * that half does what it says (§123).
   */
  focusId?: string | null;
}) {
  const figures = useApi<SavedFigure[]>(`/api/projects/${projectId}/visuals`);
  const [editing, setEditing] = useState<SavedFigure | null>(null);
  /** Chosen panels, in the order chosen: that order is A, B, C. */
  const [panels, setPanels] = useState<string[]>([]);

  if (figures.error) {
    return <Failure error={figures.error} retry={figures.reload} />;
  }
  if (figures.loading || !figures.data) {
    return <Loading rows={3} label="Reading the figures made here" />;
  }
  if (figures.data.length === 0) {
    return (
      <Empty
        title="No figure has been saved yet"
        hint="Exporting a figure from a result keeps its spec, what the critic
              said about it, and a link back to the analysis it draws."
      />
    );
  }

  return (
    <section aria-labelledby="saved-heading">
      <h2 id="saved-heading">Figures made here</h2>
      <p className="note">
        Each one records what the critic said and which analysis it draws, so a
        figure in a paper can be traced back to the run behind it. Tick several
        to make them the lettered panels of one figure, with each panel&rsquo;s
        numbers printed beneath it.
      </p>

      {panels.length > 0 && (
        <ComposeFigure
          projectId={projectId}
          visualIds={panels}
          onRemove={(id) => setPanels((now) => now.filter((p) => p !== id))}
          onClear={() => setPanels([])}
        />
      )}

      {figures.data.map((figure) => (
        <div className="card" key={figure.id}
             data-focus={figure.id === focusId || undefined}
             aria-current={figure.id === focusId ? "true" : undefined}
             ref={figure.id === focusId ? (el) => {
               if (el && typeof el.scrollIntoView === "function") el.scrollIntoView({ block: "center" });
             } : undefined}>
          <div className="row">
            <div>
              <div style={{ fontWeight: 560 }}>
                {figure.title || `Untitled ${figure.visual_type}`}
              </div>
              {figure.caption && (
                <p style={{ margin: "4px 0 0" }}>{figure.caption}</p>
              )}
              <div className="mono" style={{ color: "var(--ink-faint)" }}>
                {figure.visual_type} · {new Date(figure.created_at).toLocaleString()}
                {/*
                  Said on the row rather than only inside the editor: a figure
                  the critic blocked cannot be rendered, and a researcher
                  scanning this list needs to know which ones those are before
                  they reach for one.
                */}
                {!figure.publishable && " · the critic blocked this one"}
              </div>
            </div>
            <div className="row" style={{ gap: "0.75rem" }}>
              {figure.publishable && !NO_PANEL.includes(figure.visual_type) && (
                <label>
                  <input type="checkbox" checked={panels.includes(figure.id)}
                         disabled={!panels.includes(figure.id) && panels.length >= 9}
                         onChange={(event) => setPanels((now) => event.target.checked
                           ? [...now, figure.id]
                           : now.filter((p) => p !== figure.id))} />{" "}
                  {panels.includes(figure.id)
                    ? `Panel ${String.fromCharCode(65 + panels.indexOf(figure.id))}`
                    : "Add as a panel"}
                </label>
              )}
              <button className="btn"
                      onClick={() => setEditing(
                        editing?.id === figure.id ? null : figure)}>
                {editing?.id === figure.id ? "Done" : "Edit wording"}
              </button>
            </div>
          </div>

          {editing?.id === figure.id && (
            <EditWording
              figure={figure}
              onSaved={() => { setEditing(null); figures.reload(); }}
            />
          )}
        </div>
      ))}
    </section>
  );
}

function EditWording({ figure, onSaved }: {
  figure: SavedFigure;
  onSaved: () => void;
}) {
  const [title, setTitle] = useState(figure.title ?? "");
  const [caption, setCaption] = useState(figure.caption ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [critique, setCritique] = useState<Critique | null>(null);

  async function save(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setCritique(null);
    try {
      const result = await api.patch<{ publishable: boolean; critique: Critique }>(
        `/api/visuals/${figure.id}`,
        { changes: { title, caption } });
      /*
       * The critic runs again on every edit, and its answer is shown here
       * rather than after the next export: a caption that overstates the
       * result is the thing it blocks, and finding that out at export time
       * means finding it out after the wording felt finished.
       */
      setCritique(result.critique);
      if (result.publishable) onSaved();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={save} aria-label={`Edit the wording of ${figure.title ?? figure.id}`}
          style={{ marginTop: 10 }}>
      <label style={{ display: "block", marginBottom: 8 }}>
        Title
        <input value={title} style={{ width: "100%" }}
               onChange={(e) => setTitle(e.target.value)} />
      </label>
      <label style={{ display: "block", marginBottom: 8 }}>
        Caption
        <textarea rows={2} value={caption} style={{ width: "100%" }}
                  onChange={(e) => setCaption(e.target.value)} />
      </label>

      <p className="note" style={{ marginTop: 0 }}>
        Wording only. What the figure plots — its rows, its variables — is what
        it claims, and changing that is a new analysis rather than a redraw.
      </p>

      {error && <div className="notice" role="alert">{error}</div>}

      {critique && blocking(critique).length > 0 && (
        <div className="notice" role="alert">
          <b>The critic blocked this wording.</b>
          <ul>
            {blocking(critique).map((c) => (
              <li key={c.check}>{c.detail}</li>
            ))}
          </ul>
        </div>
      )}

      <button className="btn btn-primary" type="submit" disabled={busy}>
        {busy ? "Saving…" : "Save wording"}
      </button>
    </form>
  );
}
