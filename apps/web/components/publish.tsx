"use client";

/**
 * Exporting a figure for publication (LAW 5, §96).
 *
 * The Figures screen could already save an SVG, and did it by cloning the live
 * `<svg>` out of the page. That produces a file, which is why nobody noticed
 * what it skips: the whole server-side visuals subsystem — four routes, a
 * critic, a publication renderer and a lineage edge — had no caller at all.
 *
 * What a DOM export loses:
 *
 * * **Lineage.** `create_visual` writes a `VISUALIZES` edge from the analysis
 *   run to the figure, which is what LAW 5 rests on — a figure on a slide
 *   resolves back to the computation and the dataset underneath. A file
 *   assembled in the browser is related to nothing.
 * * **The critic.** A figure with an unfixed blocking problem must not be
 *   published. Cloning the DOM asks nobody, so a figure the system would refuse
 *   left through the side door.
 * * **The formats journals actually ask for.** PDF, EPS and TIFF at a stated
 *   pixel height. The page could offer one format because that is the one the
 *   browser happened to be holding.
 * * **A traceable filename.** The server names the file by figure id and size,
 *   so a folder of downloads is still legible a month later.
 *
 * So the on-screen chart stays where it is — it is good for reading — and
 * *export* goes through the server, where the figure becomes a recorded object
 * with a critique attached.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api } from "@/lib/api";

/** What the critic reports about one check. */
export type Critique = {
  check: string;
  outcome: "passed" | "fixed" | "warned" | "violated";
  severity: string;
  detail: string;
  fix_applied?: string;
};

export type Created = {
  visual_id: string;
  publishable: boolean;
  critique: { publishable: boolean; critiques: Critique[] };
  /*
   * The stored spec, which is how this panel knows whether the figure has a
   * third axis. Asked for rather than guessed: offering a 3D export on a bar
   * chart would be a control that always fails, and hiding it on a surface
   * would be the capability going unreachable again.
   */
  spec?: { visual_type?: string };
  /**
   * Whether the publication renderer draws this kind of figure at all.
   *
   * It draws seven flat kinds, and a fitted surface is not one of them — yet
   * the format picker was shown for every figure, so a surface offered PDF,
   * SVG and PNG and a Download that failed every time it was pressed. Asked
   * of the server, which owns that list, rather than copied here to drift.
   */
  exportable: boolean;
};

/**
 * The formats the publication renderer supports, in the order a researcher
 * should prefer them.
 *
 * Vector first because that is what most journals ask for, and because a
 * vector figure does not have to be told a size. The lossy ones are last and
 * carry the server's own warning when chosen.
 */
export const FORMATS = ["pdf", "svg", "eps", "png", "tiff", "jpeg", "webp"] as const;
export type Format = (typeof FORMATS)[number];

const VECTOR: readonly string[] = ["svg", "pdf", "eps"];

/**
 * What a figure is drawn on. `-clear` paints nothing behind the marks, so the
 * figure takes the page it is placed on; the ink is chosen for the page it is
 * going onto, which is the one thing a transparent figure cannot work out.
 */
export const GROUNDS = [
  ["light", "White"],
  ["dark", "Dark"],
  ["light-clear", "Transparent, for a light page"],
  ["dark-clear", "Transparent, for a dark page"],
] as const;
export type Ground = (typeof GROUNDS)[number][0];

/** Formats with no alpha channel, for which a transparent ground is refused. */
export const NO_ALPHA: readonly string[] = ["eps", "jpeg", "jpg"];

/** The query that asks the server for a ground, and the filename suffix it names. */
export function groundQuery(ground: Ground): { query: string; suffix: string } {
  const dark = ground.startsWith("dark");
  const clear = ground.endsWith("-clear");
  return {
    query: `&ground=${dark ? "dark" : "light"}${clear ? "&transparent=true" : ""}`,
    suffix: `${dark ? "-dark" : ""}${clear ? "-transparent" : ""}`,
  };
}

/** Whether a pixel height means anything for this format. */
export function takesAHeight(format: string): boolean {
  return !VECTOR.includes(format.toLowerCase());
}

/**
 * What is worth saying about a figure's critique, in one line.
 *
 * Only the problems. A list that also recites everything that passed buries
 * the one line that matters, and a researcher reads none of it.
 */
export function summarise(critiques: Critique[]): Critique[] {
  return critiques.filter((c) => c.outcome === "violated" || c.outcome === "warned"
                              || c.outcome === "fixed");
}

/** Save bytes the server has already named. */
export function save(bytes: Uint8Array, filename: string, type: string) {
  const url = URL.createObjectURL(new Blob([bytes as BlobPart], { type }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

export function PublishFigure({ projectId, analysisRunId, spec, findingId,
                                emphasis = "primary" }: {
  projectId: string;
  analysisRunId: string;
  /**
   * How much weight this control takes.
   *
   * §4.12 gave it `btn-primary` so it would not read as the equal of the DOM
   * save sitting under it on the Figures screen — the path that drops the
   * VISUALIZES edge, the critic and the journal formats. That argument holds
   * exactly where the pair is on screen together. On the finding's "Take it
   * further" card there is no save beside it and the step strip above already
   * carries the page's one primary, so the caller there asks for the plain
   * button (T139) and nothing it was distinguished from is on screen to be
   * confused with it.
   */
  emphasis?: "primary" | "secondary";
  /** The recommendation's spec, when the researcher is looking at one. */
  spec?: Record<string, unknown> | null;
  /**
   * The finding this figure illustrates, when there is one.
   *
   * Documented and passed to `POST /visuals` since this panel was written, and
   * for that whole time no caller supplied it — the Figures screen draws a
   * *run*, and knows no finding. A declared prop with nowhere to land is this
   * repository's own named recurring defect (`CardDetail.tsx:6-9`), and it was
   * recurring on the object researchers most want to communicate. §4.6.1's
   * "Take it further" card is the caller: `takeitfurther.tsx` passes the
   * finding it is mounted on, so the figure is recorded against it.
   *
   * Still optional, because the Figures screen is a legitimate caller that has
   * no finding to give.
   */
  findingId?: string | null;
}) {
  const [created, setCreated] = useState<Created | null>(null);
  const [format, setFormat] = useState<Format>("pdf");
  const [height, setHeight] = useState(1200);
  const [ground, setGround] = useState<Ground>("light");
  const [busy, setBusy] = useState(false);
  const [warning, setWarning] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function prepare() {
    setBusy(true);
    setError(null);
    try {
      setCreated(await api.post<Created>(`/api/projects/${projectId}/visuals`, {
        analysis_run_id: analysisRunId,
        spec: spec ?? null,
        finding_id: findingId ?? null,
      }));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  /**
   * The figure as geometry, for Blender and anything else that opens a mesh.
   *
   * Only for a fitted surface: every other figure here is flat, and a mesh of
   * a bar chart is a bar chart standing up, not a three-dimensional object.
   * The archive keeps the fitted model and the observations in separate named
   * files, because in a picture they look different and in a mesh they would
   * not.
   */
  async function downloadScene() {
    if (!created) return;
    setBusy(true);
    setError(null);
    try {
      const bytes = await api.getForBytes(
        `/api/visuals/${created.visual_id}/scene.zip`);
      save(bytes, `${created.visual_id}-scene.zip`, "application/zip");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function download() {
    if (!created) return;
    setBusy(true);
    setError(null);
    setWarning(null);
    const sized = takesAHeight(format) ? `&height=${height}` : "";
    const look = groundQuery(ground);
    try {
      /*
       * Asked for before the file is fetched, so a warning about the format
       * arrives while the researcher can still change it. A warning that comes
       * with the download has already lost.
       */
      const rendered = await api.post<{ warning?: string | null }>(
        `/api/visuals/${created.visual_id}/render?format=${format}${sized}${look.query}`);
      if (rendered.warning) setWarning(rendered.warning);

      const bytes = await api.getForBytes(
        `/api/visuals/${created.visual_id}/download?format=${format}${sized}${look.query}`);
      const size = takesAHeight(format) ? `-${height}px` : "";
      save(bytes, `${created.visual_id}${size}${look.suffix}.${format}`,
           format === "svg" ? "image/svg+xml" : "application/octet-stream");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  if (!created) {
    return (
      <div>
        {/* See `emphasis` above: primary where the DOM save is beside it. */}
        <button className={emphasis === "primary" ? "btn btn-primary" : "btn"}
                disabled={busy} onClick={() => void prepare()}>
          {busy ? "Checking the figure…" : "Export for publication"}
        </button>
        {error && <div className="notice" role="alert">{error}</div>}
      </div>
    );
  }

  const problems = summarise(created.critique.critiques);

  return (
    <section className="card" aria-label="Export for publication">
      <h3 style={{ marginTop: 0 }}>Export for publication</h3>
      <p className="mono" style={{ color: "var(--ink-faint)" }}>
        {created.visual_id} · recorded against this analysis, so the figure
        resolves back to the numbers behind it.
      </p>

      {problems.length > 0 && (
        <ul>
          {problems.map((c, i) => (
            <li key={`${c.check}:${i}`}>
              <strong>{c.outcome === "fixed" ? "Corrected" : c.check}:</strong>{" "}
              {c.detail}
              {c.fix_applied ? ` ${c.fix_applied}` : ""}
            </li>
          ))}
        </ul>
      )}

      {!created.publishable ? (
        /*
         * The server refuses to render an unpublishable figure, so a download
         * button here would be one that always fails. Saying why is the useful
         * thing — the problems are listed above, and they are fixable.
         */
        <div className="notice" role="alert">
          This figure has a problem that has to be fixed before it can be
          published. It has not been exported.
        </div>
      ) : (
        <>
          {created.exportable ? (
          <div className="row" style={{ gap: "0.75rem", alignItems: "center" }}>
            <label>
              Format{" "}
              <select
                value={format}
                onChange={(event) => {
                  const next = event.target.value as Format;
                  setFormat(next);
                  // A format with no alpha cannot keep a transparent ground.
                  if (NO_ALPHA.includes(next) && ground.endsWith("-clear")) {
                    setGround(ground.startsWith("dark") ? "dark" : "light");
                  }
                  setWarning(null);
                }}
              >
                {FORMATS.map((f) => (
                  <option key={f} value={f}>{f.toUpperCase()}</option>
                ))}
              </select>
            </label>

            <label>
              Background{" "}
              <select value={ground}
                      onChange={(event) => setGround(event.target.value as Ground)}>
                {GROUNDS.filter(([value]) => !(NO_ALPHA.includes(format)
                                              && value.endsWith("-clear")))
                  .map(([value, label]) => (
                    <option key={value} value={value}>{label}</option>
                  ))}
              </select>
            </label>

            {takesAHeight(format) && (
              <label>
                Height in pixels{" "}
                <input
                  type="number" min={120} max={8000} value={height}
                  onChange={(event) => setHeight(Number(event.target.value))}
                />
              </label>
            )}

            <button className="btn btn-primary" disabled={busy}
                    onClick={() => void download()}>
              {busy ? "Rendering…" : "Download"}
            </button>
          </div>
          ) : (
            /* No format exists for this kind of figure, so no format is
               offered — and the reason is said, because a missing button
               with no sentence reads as a page that failed to load. */
            <p className="note">
              {created.spec?.visual_type === "surface"
                ? "A fitted surface is three-dimensional and has no flat "
                  + "publication export. Render it with Blender below, or "
                  + "download the 3D scene for any other tool."
                : "Figures of this kind have no publication export yet, so "
                  + "there is no file to download here."}
            </p>
          )}
          {created.spec?.visual_type === "surface" && (
            <div className="scene">
              <button className="btn" disabled={busy}
                      onClick={() => void downloadScene()}>
                Download 3D scene
              </button>
              <p style={{ color: "var(--ink-faint)" }}>
                The fitted surface and the observations as a mesh and a point
                cloud, for Blender or another 3D tool. Each axis is scaled
                separately, so a slope measured on the mesh is not the slope in
                the data — the archive states the ranges that map it back.
              </p>
              <BlenderRender visualId={created.visual_id} />
            </div>
          )}
          {/* A vector format has no pixel size, and the server refuses a height
              for one rather than ignoring it. Saying so is better than hiding
              the field and leaving the researcher to wonder where it went. */}
          {created.exportable && !takesAHeight(format) && (
            <p style={{ color: "var(--ink-faint)" }}>
              {format.toUpperCase()} is a vector format — it has no pixel size
              and stays sharp at any scale.
            </p>
          )}
        </>
      )}

      {warning && <div className="notice" role="status">{warning}</div>}
      {error && <div className="notice" role="alert">{error}</div>}
    </section>
  );
}


/** What `/api/visuals/{id}/blender-render` reports. */
export type BlenderState = {
  visual_id: string;
  is_surface: boolean;
  /** What the colours stand for; null when the render is of an earlier figure. */
  colour_scale?: null | { low: number; high: number; label: string; text: string };
  available: boolean;
  version: string | null;
  withheld: string;
  install: string;
  render: null | {
    render_id: string;
    renderer_version: string | null;
    style?: string;
    deterministic: boolean;
    bytes: number;
    created_at: string;
    stale: boolean;
    note: string;
  };
  run: null | { run_id: string; state: string; error: string | null };
};

/** States after which a run will not change on its own. */
const FINISHED = ["completed", "partially_completed", "failed", "cancelled"];

/**
 * Rendering this figure through Blender, from inside Throughline.
 *
 * Blender's renderer was written, documented and tested, and nothing in the
 * product called it: Settings found Blender, named its version and promised
 * "a physically-based render for publication", and there was nowhere to ask
 * for one. This is where.
 *
 * It runs as a job on this machine rather than inside the request, because a
 * render can take minutes and a request held open that long is a timeout
 * reported as a failure while Blender is still working — so it is started and
 * then watched, the way a feature pack install is.
 *
 * The picture is labelled as a render wherever it appears. A Blender render
 * varies with the version, the build and the machine; the export beside it is
 * reproducible byte for byte, and the one must never pass for the other.
 */
export function BlenderRender({ visualId }: { visualId: string }) {
  const [state, setState] = useState<BlenderState | null>(null);
  const [image, setImage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [style, setStyle] = useState<"figure" | "hero">("figure");
  const [ground, setGround] = useState<"light" | "dark">("light");
  const poll = useRef<number | null>(null);
  const shown = useRef<string | null>(null);

  const load = useCallback(async () => {
    const next = await api.get<BlenderState>(
      `/api/visuals/${visualId}/blender-render`);
    setState(next);
    return next;
  }, [visualId]);

  /*
   * Keyed on the render *and* when it was made. Rendering again updates the
   * same row, so the id alone never changed and the panel went on showing
   * the first picture after every later one had finished.
   */
  const showImage = useCallback(async (renderId: string) => {
    if (shown.current === renderId) return;
    const bytes = await api.getForBytes(
      `/api/visuals/${visualId}/blender-render.png`);
    shown.current = renderId;
    setImage(URL.createObjectURL(
      new Blob([bytes as BlobPart], { type: "image/png" })));
  }, [visualId]);

  const stop = useCallback(() => {
    if (poll.current !== null) {
      window.clearInterval(poll.current);
      poll.current = null;
    }
  }, []);

  const watch = useCallback(() => {
    stop();
    poll.current = window.setInterval(async () => {
      try {
        const next = await load();
        if (!next.run || FINISHED.includes(next.run.state)) {
          stop();
          if (next.render) await showImage(`${next.render.render_id}@${next.render.created_at}`);
        }
      } catch (err) {
        stop();
        setError(err instanceof ApiError ? err.message : String(err));
      }
    }, 3000);
  }, [load, showImage, stop]);

  useEffect(() => {
    let live = true;
    load()
      .then(async (first) => {
        if (!live) return;
        if (first.run && !FINISHED.includes(first.run.state)) watch();
        else if (first.render) await showImage(`${first.render.render_id}@${first.render.created_at}`);
      })
      .catch((err) => {
        if (live) setError(err instanceof ApiError ? err.message : String(err));
      });
    return () => { live = false; stop(); };
  }, [load, showImage, stop, watch]);

  // Each object URL is released when it is replaced or the panel closes.
  useEffect(() => () => { if (image) URL.revokeObjectURL(image); }, [image]);

  async function start() {
    setStarting(true);
    setError(null);
    try {
      await api.post(
        `/api/visuals/${visualId}/blender-render?style=${style}&ground=${ground}`);
      await load();
      watch();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setStarting(false);
    }
  }

  async function saveRender() {
    try {
      const bytes = await api.getForBytes(
        `/api/visuals/${visualId}/blender-render.png`);
      save(bytes, `${visualId}-blender-render.png`, "image/png");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  if (!state) {
    return error ? <p className="notice" role="alert">{error}</p> : null;
  }
  if (!state.is_surface) return null;

  const running = Boolean(state.run && !FINISHED.includes(state.run.state));
  const failed = state.run?.state === "failed" ? state.run.error : null;

  return (
    <div className="blender-render">
      <h3 className="eyebrow">Render with Blender</h3>
      {!state.available ? (
        <>
          <p className="note">{state.withheld}</p>
          {state.install && <p className="note">{state.install}</p>}
        </>
      ) : (
        <>
          <p className="note">
            Blender {state.version} on this machine renders the fitted surface
            with light and material. It runs in the background, and the picture
            appears here when it is done.
          </p>
          <div className="row" style={{ gap: "0.75rem", alignItems: "center" }}>
            <label>
              Look{" "}
              <select value={style} disabled={running || starting}
                      onChange={(event) => setStyle(event.target.value as "figure" | "hero")}>
                <option value="figure">Figure — transparent, all in focus</option>
                <option value="hero">Cover — backdrop and shallow focus</option>
              </select>
            </label>
            <label>
              Ground{" "}
              <select value={ground} disabled={running || starting}
                      onChange={(event) => setGround(event.target.value as "light" | "dark")}>
                <option value="light">Light</option>
                <option value="dark">Dark</option>
              </select>
            </label>
          </div>
          <p className="note">
            The surface is coloured by its fitted value on the same scale as
            every heatmap here: dark purple lowest, yellow highest.
            {style === "hero" && " A cover render blurs what is out of focus — "
              + "use the figure look for anything a reader will take values from."}
          </p>
          <button className="btn" disabled={running || starting}
                  onClick={() => void start()}>
            {running ? "Rendering in Blender…"
              : starting ? "Starting…"
              : state.render ? "Render again" : "Render with Blender"}
          </button>
        </>
      )}
      {failed && <p className="notice" role="alert">{failed}</p>}
      {error && <p className="notice" role="alert">{error}</p>}
      {state.render && image && (
        <figure>
          {/* Generated Blender renders are blob/data URLs, so Next image optimization adds no value. */}
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={image} alt="The fitted surface, rendered through Blender" />
          <figcaption>
            {state.colour_scale && <>{state.colour_scale.text}{" "}</>}
            {state.render.note}
            {state.render.stale
              && " This is a render of an earlier version of the figure; render "
                 + "again to match it."}
          </figcaption>
          <button className="btn" onClick={() => void saveRender()}>
            Download the render
          </button>
        </figure>
      )}
    </div>
  );
}

