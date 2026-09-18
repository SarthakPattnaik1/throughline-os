"use client";

/**
 * Several saved figures as one lettered figure (T192).
 *
 * A multi-panel figure was made outside Throughline: each chart exported on
 * its own and pasted together in a drawing program, where the type sizes
 * drifted and the numbers stayed in the caption. Here the panels are drawn by
 * the same renderer, on one ground and in one type scale, and each carries its
 * recorded numbers beneath it.
 *
 * **The disagreements are shown before the download.** The server names every
 * place the recorded numbers point different ways — a p-value and an interval
 * that disagree, a significant effect that is negligible, the same estimate
 * with opposite signs in two panels — and they are listed here while the
 * researcher is still choosing panels, not discovered in review.
 */

import { useEffect, useState } from "react";
import { ApiError, api } from "@/lib/api";
import { FORMATS, type Format, GROUNDS, type Ground, NO_ALPHA, save,
         takesAHeight } from "./publish";

type Checked = {
  panels: Array<{
    letter: string;
    visual_id: string;
    title: string | null;
    visual_type: string;
    metrics: string;
    drawable: boolean;
  }>;
  disagreements: string[];
};

export function ComposeFigure({ projectId, visualIds, onRemove, onClear }: {
  projectId: string;
  /** The panels, in the order they were chosen — which is the reading order. */
  visualIds: string[];
  onRemove: (visualId: string) => void;
  onClear: () => void;
}) {
  const [checked, setChecked] = useState<Checked | null>(null);
  const [format, setFormat] = useState<Format>("pdf");
  const [ground, setGround] = useState<Ground>("light");
  const [height, setHeight] = useState(1600);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const key = visualIds.join(",");
  useEffect(() => {
    let live = true;
    setError(null);
    api.post<Checked>(`/api/projects/${projectId}/figures/compose/check`,
                      { visual_ids: visualIds })
      .then((next) => { if (live) setChecked(next); })
      .catch((err) => {
        if (live) setError(err instanceof ApiError ? err.message : String(err));
      });
    return () => { live = false; };
    // `key` stands for the list: a new array with the same ids is no change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, key]);

  async function download() {
    setBusy(true);
    setError(null);
    const dark = ground.startsWith("dark");
    const clear = ground.endsWith("-clear");
    try {
      const bytes = await api.postForBytes(
        `/api/projects/${projectId}/figures/compose`, {
          visual_ids: visualIds, format, ground: dark ? "dark" : "light",
          transparent: clear, height: takesAHeight(format) ? height : null,
        });
      const look = `${dark ? "-dark" : ""}${clear ? "-transparent" : ""}`;
      save(bytes, `figure-${visualIds.length}-panels${look}.${format}`,
           format === "svg" ? "image/svg+xml" : "application/octet-stream");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card" aria-label="Compose a figure">
      <div className="row">
        <h3 style={{ margin: 0 }}>
          A figure of {visualIds.length} {visualIds.length === 1 ? "panel" : "panels"}
        </h3>
        <button className="btn" onClick={onClear}>Clear</button>
      </div>

      <ol className="compose-panels">
        {(checked?.panels ?? []).map((panel) => (
          <li key={panel.visual_id}>
            <b className="compose-letter">{panel.letter}</b>
            <div>
              <div>{panel.title || `Untitled ${panel.visual_type}`}</div>
              <div className="mono compose-metrics">
                {panel.metrics || "No recorded numbers to print under this panel."}
              </div>
            </div>
            <button className="btn" aria-label={`Remove panel ${panel.letter}`}
                    onClick={() => onRemove(panel.visual_id)}>
              Remove
            </button>
          </li>
        ))}
      </ol>

      {checked && checked.disagreements.length > 0 && (
        <div className="notice" role="status">
          <b>Where the numbers disagree</b> — printed under the figure too:
          <ul>
            {checked.disagreements.map((note) => <li key={note}>{note}</li>)}
          </ul>
        </div>
      )}

      <div className="row" style={{ gap: "0.75rem", alignItems: "center",
                                    justifyContent: "flex-start" }}>
        <label>
          Format{" "}
          <select value={format} onChange={(event) => {
            const next = event.target.value as Format;
            setFormat(next);
            if (NO_ALPHA.includes(next) && ground.endsWith("-clear")) {
              setGround(ground.startsWith("dark") ? "dark" : "light");
            }
          }}>
            {FORMATS.map((f) => <option key={f} value={f}>{f.toUpperCase()}</option>)}
          </select>
        </label>
        <label>
          Background{" "}
          <select value={ground} onChange={(event) => setGround(event.target.value as Ground)}>
            {GROUNDS.filter(([value]) => !(NO_ALPHA.includes(format)
                                          && value.endsWith("-clear")))
              .map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </label>
        {takesAHeight(format) && (
          <label>
            Height in pixels{" "}
            <input type="number" min={120} max={8000} value={height}
                   onChange={(event) => setHeight(Number(event.target.value))} />
          </label>
        )}
        <button className="btn btn-primary" disabled={busy || !checked}
                onClick={() => void download()}>
          {busy ? "Composing…" : "Download the figure"}
        </button>
      </div>

      {error && <div className="notice" role="alert">{error}</div>}
    </section>
  );
}
