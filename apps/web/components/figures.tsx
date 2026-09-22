"use client";

/**
 * The Figures view (Parts F and O).
 *
 * Three things ship together or the primitive is not finished: the chart, the
 * *reason* it was chosen, and a data table. The reason matters as much as the
 * picture — it is where a PhD student absorbs visualisation judgment as a side
 * effect of using the product, and it is what makes the recommendation feel
 * intelligent rather than automated.
 */

import { useCallback, useMemo, useRef, useState } from "react";
import { AnalysisRunRow, DatasetColumn, api } from "@/lib/api";
import { nameOf } from "./analyses";
import { ApiState, useApi } from "@/lib/useApi";
// Aliased: Matrix exports a `Cell` too, and its shape is row/column/value
// rather than x/y/count.
import { Binned, Cell as BinnedCell } from "./charts/Binned";
import { Surface } from "./charts/Surface";
import { Cartesian, CartesianMark, Datum } from "./charts/Cartesian";
import { Estimate, Interval } from "./charts/Interval";
import { Cell, Matrix } from "./charts/Matrix";
import { Density, DensityCurve } from "./charts/Density";
import {
  BoxSummary, HistogramBin, PreparedBoxPlot, PreparedHistogram,
} from "./charts/PreparedStatCharts";
import { Empty, Failure, Fold, Loading } from "./primitives";
import { SavedFigures } from "./savedfigures";
import { PublishFigure } from "./publish";
import { MapView, mappable } from "./mapview";

type Recommendation = {
  visual_type: string;
  reason: string;
  caption: string;
  interpretation?: string;
  alternatives?: Array<{ visual_type: string; reason: string }>;
  spec: {
    /*
      `scale` was sent by the API and absent from this type, so the axis could
      not say what it was drawn on even though the analysis had recorded it.
    */
    x?: { field: string; label?: string; unit?: string; scale?: string };
    y?: { field: string; label?: string; unit?: string; scale?: string };
    dataset_version_id?: string | null;
    category_labels?: Record<string, string>;
    title?: string;
  };
};

type Points = {
  x: number[];
  y: number[];
  group?: string[];
  ci_low?: number[];
  ci_high?: number[];
  categories?: string[];
  matrix?: number[][];
  series?: Array<Record<string, unknown>>;
  statistics?: Record<string, number | string | null>;
  sample_size?: number;
  sampling?: {
    sampled: boolean;
    rows_total: number;
    rows_drawn: number;
    method: string;
  };
  /** Counted server-side, and present only for a binned recommendation. */
  cells?: BinnedCell[] | null;
  bin_count?: number | null;
  bin_shape?: string;
  count_scale?: string;
  /**
   * Present only for a surface: the fitted response over a grid, and the
   * observations it was fitted to.
   *
   * Evaluated server-side from the recorded coefficients, like `cells`, so a
   * browser cannot compute a second model beside the one in the record.
   */
  grid?: Array<Array<number | null>> | null;
  grid_x?: number[];
  grid_y?: number[];
  observations?: Array<{ x: number; y: number; z: number }>;
  note?: string;
};

const MARK_FOR: Record<string, CartesianMark> = {
  scatter: "point", bubble: "point", strip: "point", beeswarm: "point",
  line: "line", multi_line: "line", step: "line", sparkline: "line",
  area: "area", stacked_area: "area",
  bar: "rect", column: "rect", histogram: "rect", grouped_bar: "rect",
};

type EstimatePayload = {
  estimates: Estimate[];
  estimate_name: string;
  note: string;
  /** Stated so the omission is visible rather than silent. */
  excluded_without_estimate?: number;
};

/**
 * Which two columns a figure is drawn against.
 *
 * Taken from the run's own recommendation, which is where it has always been:
 * the spec carries an encoding per axis, with the field it plots. This screen
 * read `connection.left_variable` instead, and so could only draw an analysis
 * that a discovery sweep had turned into a connection — the run-keyed routes
 * behind the figure (`/visual-recommendation`, `/points`) never needed one.
 *
 * A specified analysis belongs to no connection, so every figure on this screen
 * was unreachable for it, and the empty state said to run discovery as though
 * that were the only way to produce something plottable.
 */
export function axisFields(
  recommendation: Recommendation, run: AnalysisRunRow,
): { x: string; y: string } {
  const named = Object.values(run.variables ?? {})
    .flatMap((v) => (Array.isArray(v) ? v.map(String) : typeof v === "string" ? [v] : []));
  return {
    x: recommendation.spec.x?.field || run.left_variable || named[0] || "x",
    y: recommendation.spec.y?.field || run.right_variable || named[1] || "y",
  };
}

/**
 * An axis label: what a human approved it should be called, then what the
 * figure's own spec calls it, then the raw column name.
 *
 * The canonical label comes first because it is the one a person chose, and a
 * figure that disagreed with the rest of the project about a variable's name
 * would be the figure that goes into the paper.
 */
export function axisLabel(
  field: string,
  labels: Record<string, string>,
  encoding?: { label?: string; unit?: string },
): string {
  const base = labels[field] ?? (encoding?.label || field);
  const unit = encoding?.unit?.trim();
  if (!unit) return base;

  // The backend spec is the source of truth for the unit. A project-approved
  // label may replace the wording but not erase the measurement unit.
  // Avoid doubling a label that already includes it.
  const lowered = base.trim().toLowerCase();
  const suffixes = [` (${unit.toLowerCase()})`, ` [${unit.toLowerCase()}]`];
  return suffixes.some((suffix) => lowered.endsWith(suffix))
    ? base
    : `${base} (${unit})`;
}

/**
 * What to call a run in the picker, in the project's own words for its columns.
 *
 * `nameOf` already answers "what is this run" for the analyses list; this adds
 * the canonical labels a human approved, so the two screens cannot disagree
 * about what a variable is called.
 */
export function figureName(
  run: AnalysisRunRow, labels: Record<string, string>,
): string {
  if (run.left_variable && run.right_variable) {
    return `${labels[run.left_variable] ?? run.left_variable} × `
         + `${labels[run.right_variable] ?? run.right_variable}`;
  }
  return nameOf(run)
    .split(" · ")
    .map((name) => labels[name] ?? name)
    .join(" · ");
}

/**
 * The axis note for a recorded scale, or nothing.
 *
 * `linear` returns nothing on purpose: an axis that always carried a bracket
 * would teach a reader to skip it, and the bracket exists for the one case
 * that matters — a log axis read as linear is wrong by orders of magnitude at
 * one end and nearly right at the other.
 */
function transformOf(scale?: string): string | undefined {
  return scale && scale !== "linear" ? scale : undefined;
}


/** The five questions this screen can draw, as they appear in the address. */
export type FigureLens = "all" | "matrix" | "spread" | "one" | "map";

export function Figures({ projectId, runs, focusId = null,
                          lens = null, onLens }: {
  projectId: string;
  runs: ApiState<AnalysisRunRow[]>;
  /** A saved figure to land on — the one the palette or the address named. */
  focusId?: string | null;
  /**
   * Which question to draw, when the address names one.
   *
   * It was `useState` alone, which is D196's argument one level down: "How it
   * all relates" could not be linked to and did not survive a reload. What
   * forced the change is that it could not be *arrived at* either — the chart
   * catalogue lists fourteen primitives and could not offer "draw my data this
   * way", because the lens that would draw it had no address to send anybody
   * to. Null means this screen is uncontrolled and keeps its own.
   */
  lens?: FigureLens | null;
  /** Where a lens change goes, when the address is carrying it. */
  onLens?: (next: FigureLens) => void;
}) {
  const [chosen, setChosen] = useState<string | null>(null);
  const [ownView, setOwnView] = useState<FigureLens>("all");
  // Controlled by the address where there is one, uncontrolled otherwise, so
  // the component is still renderable on its own in a test or a story.
  const view = lens ?? ownView;
  const setView = (next: FigureLens) => {
    if (onLens) onLens(next);
    else setOwnView(next);
  };
  const [column, setColumn] = useState<string | null>(null);
  const matrix = useApi<{ cells: Cell[]; variables: string[]; note: string }>(
    `/api/projects/${projectId}/correlation-matrix`);
  const estimates = useApi<EstimatePayload>(
    `/api/projects/${projectId}/estimates?limit=30`);
  const variables = useApi<{ labels: Record<string, string> }>(
    `/api/projects/${projectId}/variables`);

  /*
   * The profiled schema of the project's dataset, so the screen can tell
   * whether there is anywhere to draw before offering to draw it.
   */
  const sources = useApi<Array<{ dataset?: { dataset_version_id: string } | null }>>(
    `/api/projects/${projectId}/sources`);
  const versionId = (sources.data ?? []).find((s) => s.dataset)
    ?.dataset?.dataset_version_id ?? null;
  const columns = useApi<DatasetColumn[]>(
    versionId ? `/api/dataset-versions/${versionId}/columns` : null, [versionId]);
  const placeColumn = mappable(columns.data ?? []).place;

  /*
   * A run that has not finished has no estimate and no points, and a picker
   * entry that can only ever say "no plottable values" is worse than no entry.
   */
  const plottable = (runs.data ?? []).filter((r) => r.estimate !== null);
  const active = chosen ?? plottable[0]?.id ?? null;
  const run = plottable.find((r) => r.id === active) ?? null;

  const recommendation = useApi<Recommendation>(
    run ? `/api/analyses/${run.id}/visual-recommendation` : null, [run?.id]);

  if (runs.loading) return <Loading rows={4} label="Reading analyses" />;
  if (!plottable.length) {
    return (
      <>
        <h1>Figures</h1>
        <Empty title="Nothing to plot yet"
               hint={"Run discovery to search for relationships, or specify "
                     + "an analysis yourself — either one can be drawn."} />
      </>
    );
  }

  const labels = variables.data?.labels ?? {};

  return (
    <>
      <h1>Figures</h1>
      <p className="lede">
        The chart is chosen from the shape of the data and the question.
      </p>
      <Fold summary="How a figure is chosen and what comes with it" count={2}>
        <p className="note" style={{ marginTop: 0 }}>
          The reason for the chart is shown with it, so a figure is never a
          choice somebody has to take on trust.
        </p>
        <p className="note">
          Every figure exports as a vector, and the numbers behind it are one
          click away.
        </p>
      </Fold>

      {/* Two lenses on the same run: everything that was tested, or one
          relationship in detail. The overview is the default because the
          honest summary of a discovery run is how much of it was noise. */}
      {/* The five questions as one segmented control, like every other set of
          readings in the product; it was loose pills with a gold edge, the one
          place a choice between views still looked like a row of buttons. */}
      <div className="fig-picker fig-lenses" style={{ marginBottom: 10 }}>
        <button className="btn" aria-current={view === "all"}
                onClick={() => setView("all")}>
          Everything tested
        </button>
        <button className="btn" aria-current={view === "matrix"}
                onClick={() => setView("matrix")}>
          How it all relates
        </button>
        <button className="btn" aria-current={view === "spread"}
                onClick={() => setView("spread")}>
          How one variable is spread
        </button>
        <button className="btn" aria-current={view === "one"}
                onClick={() => setView("one")}>
          One relationship
        </button>
        {/*
          Offered only when there is a column of places. A map button on a
          dataset with nowhere to draw is a control that can only disappoint.
        */}
        {placeColumn && (
          <button className="btn" aria-current={view === "map"}
                  onClick={() => setView("map")}>
            Where it was measured
          </button>
        )}
      </div>

      {view === "all" && (
        <ForestView state={estimates} />
      )}

      {view === "matrix" && <MatrixView state={matrix} />}

      {view === "spread" && (
        <SpreadView projectId={projectId} column={column} onColumn={setColumn} />
      )}

      {view === "map" && (
        placeColumn
          ? <MapView versionId={versionId} columns={columns.data ?? []} />
          : (
            /*
             * The lens is in the address now, so it can be arrived at from
             * somewhere that could not know whether this dataset has anywhere
             * to draw — the chart catalogue's own door, for one. The button
             * above is still hidden in that case; a lens that renders an empty
             * map instead of saying why would be the same dead end the
             * catalogue's door was built to remove.
             */
            <p className="note">
              Nothing in this dataset was profiled as a place, so there is
              nowhere to draw. A column of countries, regions or coordinates is
              what this lens needs.
            </p>
          )
      )}

      {view === "one" && (
      <>
      <div className="fig-picker">
        {plottable.slice(0, 8).map((r) => (
          <button key={r.id} className="btn" aria-current={r.id === active}
                  onClick={() => setChosen(r.id)}>
            {figureName(r, labels)}
          </button>
        ))}
      </div>

      {recommendation.error && (
        <Failure error={recommendation.error} retry={recommendation.reload} />
      )}
      {recommendation.loading && <Loading rows={4} label="Choosing the figure" />}
      {run && recommendation.data && (
        <Figure run={run} recommendation={recommendation.data}
                labels={labels} projectId={projectId}
                versionId={recommendation.data.spec.dataset_version_id ?? null} />
      )}
      </>
      )}

      {/*
        Under the live chart, because the list is a record of what has already
        been made rather than the thing a researcher came to this screen to do.
      */}
      <SavedFigures projectId={projectId} focusId={focusId} />
    </>
  );
}

/** P3 — the distribution of one variable, smoothed server-side. */
function SpreadView({ projectId, column, onColumn }: {
  projectId: string;
  column: string | null;
  onColumn: (name: string) => void;
}) {
  const sources = useApi<Array<{ dataset?: { dataset_version_id: string } | null }>>(
    `/api/projects/${projectId}/sources`);
  const versionId = (sources.data ?? []).find((s) => s.dataset)?.dataset?.dataset_version_id;
  const columns = useApi<Array<{ name: string; semantic_type: string }>>(
    versionId ? `/api/dataset-versions/${versionId}/columns` : null);
  const variables = useApi<{ labels: Record<string, string> }>(
    `/api/projects/${projectId}/variables`);

  const continuous = (columns.data ?? []).filter((c) => c.semantic_type === "continuous");
  const active = column ?? continuous[0]?.name ?? null;

  const density = useApi<{
    label: string; x: number[]; density: number[]; observations: number[];
    quartiles: number[]; n: number; bandwidth: number; bandwidth_rule: string;
    note: string;
  }>(versionId && active
    ? `/api/dataset-versions/${versionId}/density?column=${encodeURIComponent(active)}`
    : null);

  if (!versionId) {
    return <Empty title="No dataset yet"
                  hint="Add tabular data — every continuous column can be plotted." />;
  }
  if (columns.loading) return <Loading rows={4} label="Reading the schema" />;

  const labels = variables.data?.labels ?? {};

  return (
    <>
      <div className="fig-picker">
        {continuous.slice(0, 10).map((c) => (
          <button key={c.name} className="btn" aria-current={c.name === active}
                  onClick={() => onColumn(c.name)}>
            {labels[c.name] ?? c.name}
          </button>
        ))}
      </div>

      {density.error && <Failure error={density.error} retry={density.reload} />}
      {density.loading && <Loading rows={4} label="Estimating the distribution" />}
      {density.data && (
        <>
          <div className="card">
            <Density
              curves={[{
                id: active ?? "curve",
                label: density.data.label,
                x: density.data.x,
                density: density.data.density,
                observations: density.data.observations,
                quartiles: density.data.quartiles,
                n: density.data.n,
              } as DensityCurve]}
              xLabel={density.data.label}
              title={`How ${density.data.label} is distributed`}
              bandwidthNote={`Smoothed by ${density.data.bandwidth_rule}'s rule `
                + `(bandwidth ${density.data.bandwidth.toFixed(3)}). The ticks beneath `
                + `the curve are the observations themselves.`}
              caption={density.data.note}
            />
          </div>

          <details className="fold kg-table">
            <summary>The values behind this figure<span className="fold-count">· {density.data.n} observations</span></summary>
            <table>
              <thead><tr><th>Quartile</th><th style={{ textAlign: "right" }}>Value</th></tr></thead>
              <tbody>
                {["25th percentile", "Median", "75th percentile"].map((name, i) => (
                  <tr key={name}>
                    <td>{name}</td>
                    <td className="numeric" style={{ textAlign: "right" }}>
                      {density.data!.quartiles[i].toFixed(3)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
        </>
      )}
    </>
  );
}

/**
 * P5 — the whole set of tested pairs at once.
 *
 * The forest plot ranks relationships; this shows the structure. It is where a
 * confounder becomes visible: a variable that correlates with both sides of the
 * relationship you care about is sitting in plain sight in its row.
 */
function MatrixView({ state }: {
  state: ApiState<{ cells: Cell[]; variables: string[]; note: string }>;
}) {
  if (state.error) return <Failure error={state.error} retry={state.reload} />;
  if (state.loading || !state.data) {
    return <Loading rows={5} label="Assembling the matrix" />;
  }
  const { cells, variables, note } = state.data;
  if (!variables.length) {
    return <Empty title="Nothing tested yet"
                  hint="Run discovery — every tested pair appears here." />;
  }

  return (
    <>
      <div className="card">
        <Matrix
          cells={cells}
          rows={variables}
          columns={variables}
          title="How every variable relates to every other"
          caption={"Blue is negative, orange positive, and the neutral centre is no "
            + "relationship. Blank cells were never tested. Reading down a row shows "
            + "what a variable moves with — which is how a confounder becomes visible."}
        />
      </div>
      <p className="note">{note}</p>

      <details className="fold kg-table">
        <summary>The numbers behind this figure<span className="fold-count">· {cells.length / 2} tested pairs</span></summary>
        <table>
          <thead><tr><th>Row</th><th>Column</th>
                     <th style={{ textAlign: "right" }}>Value</th></tr></thead>
          <tbody>
            {cells.filter((c, i) => i % 2 === 0).map((c) => (
              <tr key={`${c.row}-${c.column}`}>
                <td>{c.row}</td><td>{c.column}</td>
                <td className="numeric" style={{ textAlign: "right" }}>
                  {c.value === null ? "—" : c.value.toFixed(3)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </>
  );
}

/** P2 — every estimate in the correction family, with its interval. */
function ForestView({ state }: { state: ApiState<EstimatePayload> }) {
  if (state.error) return <Failure error={state.error} retry={state.reload} />;
  if (state.loading || !state.data) {
    return <Loading rows={5} label="Reading every tested estimate" />;
  }
  const { estimates, estimate_name, note } = state.data;
  if (!estimates.length) {
    return <Empty title="Nothing with an interval yet"
                  hint="Run discovery — every tested pair records a confidence interval." />;
  }

  const surviving = estimates.filter((e) => e.significant).length;

  return (
    <>
      <div className="card">
        <Interval
          estimates={estimates}
          xLabel={estimate_name.replace(/_/g, " ")}
          title="Every relationship tested in this run"
          caption={`${estimates.length} tested · ${surviving} exclude zero after `
            + `Benjamini–Hochberg correction. Intervals crossing the line are `
            + `consistent with no relationship.`}
        />
      </div>
      {/* The caption under the chart already says how many were tested and
          how many survived; this is the paragraph about what a crossing
          interval means, which is the same paragraph on every visit. */}
      <Fold summary="What an interval crossing the line means" count={1}>
        <p className="note" style={{ margin: 0 }}>{note}</p>
      </Fold>

      {/* Part P — the same figure as a table. */}
      <details className="fold kg-table">
        <summary>The numbers behind this figure<span className="fold-count">· {estimates.length} rows</span></summary>
        <table>
          <thead>
            <tr><th style={{ width: "46%" }}>Relationship</th>
                <th style={{ textAlign: "right" }}>Estimate</th>
                <th style={{ textAlign: "right" }}>Interval</th>
                <th>After correction</th></tr>
          </thead>
          <tbody>
            {estimates.map((e) => (
              <tr key={e.id}>
                <td>{e.label}</td>
                {/* A missing value degrades to a dash. One null coefficient
                    from a categorical pair crashed this whole screen once; a
                    figure must never be the thing that takes the workspace
                    down. */}
                <td className="numeric" style={{ textAlign: "right" }}>
                  {e.estimate == null ? "—" : e.estimate.toFixed(3)}
                </td>
                <td className="numeric" style={{ textAlign: "right" }}>
                  {e.lo == null || e.hi == null
                    ? "—"
                    : `${e.lo.toFixed(3)} to ${e.hi.toFixed(3)}`}
                </td>
                {/* A word, not only a colour (Part A). */}
                <td>{e.significant ? "excludes zero" : "consistent with none"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </>
  );
}

function Figure({ run, recommendation, labels, projectId, versionId }: {
  run: AnalysisRunRow;
  recommendation: Recommendation;
  labels: Record<string, string>;
  projectId: string;
  /** Which dataset a recorded subset would belong to, when there is one. */
  versionId: string | null;
}) {
  const svgHost = useRef<HTMLDivElement>(null);
  const points = useApi<Points>(`/api/analyses/${run.id}/points`, [run.id]);
  const fields = axisFields(recommendation, run);

  /*
   * Recording a region as a subset.
   *
   * The name is asked for rather than generated: a subset called "region 3"
   * is one nobody can quote, and naming it is the moment the researcher says
   * what they think it is. The bounds come from the chart in data units and
   * the column from the recorded spec — never from the axis label, which is
   * what a reader should call it and frequently not what the data does.
   */
  const [recording, setRecording] =
    useState<{ from: number; to: number } | null>(null);
  const [subsetName, setSubsetName] = useState("");
  const [recordError, setRecordError] = useState<unknown>(null);
  const [recorded, setRecorded] = useState<string | null>(null);

  const data: Datum[] = useMemo(() => {
    const p = points.data;
    if (!p?.x?.length) return [];
    return p.x.map((x, i) => ({ id: String(i), x, y: p.y[i] }));
  }, [points.data]);

  /**
   * Export the live SVG.
   *
   * The element on screen *is* the vector, so there is no second renderer to
   * drift from it. Chrome-free by construction: the figure element contains no
   * interface furniture.
   */
  const exportSvg = useCallback(() => {
    const svg = svgHost.current?.querySelector("svg");
    if (!svg) return;
    const clone = svg.cloneNode(true) as SVGSVGElement;
    // Publication figures are light. A figure built in dark mode and dropped
    // into a manuscript must not arrive as a black rectangle, so the export
    // pins light values regardless of the app&apos;s theme (Part A).
    clone.setAttribute("style", "background:#FFFFFF");
    clone.querySelectorAll<SVGElement>(".chart-tick,.chart-axis-label")
      .forEach((el) => el.setAttribute("fill", "#2A2A28"));
    clone.querySelectorAll<SVGElement>(".chart-axis")
      .forEach((el) => el.setAttribute("stroke", "#161615"));
    clone.querySelectorAll<SVGElement>(".chart-grid")
      .forEach((el) => el.setAttribute("stroke", "#E8E8E5"));

    const blob = new Blob(
      [`<?xml version="1.0" encoding="UTF-8"?>\n${clone.outerHTML}`],
      { type: "image/svg+xml" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${run.id}-${recommendation.visual_type}.svg`;
    link.click();
    URL.revokeObjectURL(url);
  }, [run.id, recommendation.visual_type]);

  const mark = MARK_FOR[recommendation.visual_type] ?? "point";
  const xLabel = axisLabel(fields.x, labels, recommendation.spec.x);
  const yLabel = axisLabel(fields.y, labels, recommendation.spec.y);

  /**
   * A surface recommendation must draw a surface, for the same reason the
   * binned one must draw bins.
   *
   * This is the first spatial chart a researcher's own analysis can produce.
   * Six of the eight renderers had never seen anything but generated data,
   * because the recommender had no three-dimensional option in its vocabulary
   * — so they were unreachable by construction rather than misfiled.
   *
   * The grid arrives already evaluated from the recorded coefficients. A
   * browser that computed the fitted response itself could disagree with the
   * analysis that produced it, which is the fidelity rule every figure here
   * follows.
   */
  const grid = points.data?.grid;
  const surface = recommendation.visual_type === "surface"
    && grid?.length
    && (points.data?.grid_x?.length ?? 0) > 1
    && (points.data?.grid_y?.length ?? 0) > 1;

  /*
   * Both of these were built inline in the JSX, so every render of this figure
   * handed `Surface` a new grid object and a new observations array. `Surface`
   * projects the fit and the observations into one shared cube in a memo keyed
   * on exactly those two references, so a fresh pair on every render meant the
   * whole projection — every cell spanned, every observation placed, the
   * colour ramp rebuilt — ran again for a figure that had not changed. This is
   * the path a real fitted surface takes, at real sizes, not the small
   * examples the catalogue draws.
   */
  const surfaceGrid = useMemo(
    () => (surface
      ? { x: points.data!.grid_x!, y: points.data!.grid_y!, z: grid! }
      : null),
    [surface, points.data, grid]);

  const surfaceObservations = useMemo(
    () => (points.data?.observations ?? []).map((o, index) => ({
      id: String(index), label: `observation ${index + 1}`,
      x: o.x, y: o.y, z: o.z,
    })),
    [points.data]);

  if (points.error) return <Failure error={points.error} retry={points.reload} />;
  if (points.loading) return <Loading rows={4} label="Reading the plotted values" />;

  const forestEstimates: Estimate[] = recommendation.visual_type === "forest"
    ? (points.data?.categories ?? []).map((category, index) => ({
        id: category,
        label: recommendation.spec.category_labels?.[category] ?? category.replace(/_/g, " "),
        estimate: points.data?.y?.[index] ?? 0,
        lo: points.data?.ci_low?.[index] ?? 0,
        hi: points.data?.ci_high?.[index] ?? 0,
      }))
    : [];

  const heatmapRows = points.data?.group ?? [];
  const heatmapColumns = points.data?.categories ?? [];
  const heatmapCells: Cell[] = recommendation.visual_type === "heatmap"
    ? heatmapRows.flatMap((row, rowIndex) =>
        heatmapColumns.map((column, columnIndex) => ({
          row,
          column,
          value: points.data?.matrix?.[rowIndex]?.[columnIndex] ?? null,
        })))
    : [];

  const boxSummaries = recommendation.visual_type === "box"
    ? (points.data?.series ?? []) as unknown as BoxSummary[]
    : [];
  const histogramBins = recommendation.visual_type === "histogram"
    ? (points.data?.series ?? []) as unknown as HistogramBin[]
    : [];

  const hasRenderableData =
    (recommendation.visual_type === "forest" && forestEstimates.length > 0)
    || (recommendation.visual_type === "heatmap" && heatmapCells.length > 0)
    || (recommendation.visual_type === "box" && boxSummaries.length > 0)
    || (recommendation.visual_type === "histogram" && histogramBins.length > 0)
    || Boolean(surface)
    || Boolean(recommendation.visual_type === "hexbin" && points.data?.cells?.length)
    || data.length > 0;

  if (!hasRenderableData) {
    return <Empty title="No plottable values recorded"
                  hint="The analysis completed, but its prepared figure data is empty." />;
  }

  /**
   * A binned recommendation must draw a binned figure.
   *
   * `MARK_FOR` has no entry for it and falls back to a point mark, so this used
   * to render a scatter — at the sample size that triggers the recommendation,
   * exactly the overplotted blob the primitive exists to replace. The
   * recommender said one thing and the screen showed another.
   *
   * Cells arrive already counted from the same endpoint as the points, because
   * binning is aggregation and a browser that re-aggregated could disagree with
   * the analysis (LAW 2).
   */
  const cells = points.data?.cells;
  const binned = recommendation.visual_type === "hexbin" && cells?.length;


  async function recordSubset() {
    if (!recording || !versionId) return;
    setRecordError(null);
    try {
      const made = await api.post<{ sentence: string }>(
        `/api/projects/${projectId}/cohorts`, {
          dataset_version_id: versionId,
          name: subsetName,
          definition: [{ column: fields.x, min: recording.from,
                         max: recording.to }],
        });
      setRecorded(made.sentence);
      setRecording(null);
      setSubsetName("");
    } catch (failure) {
      setRecordError(failure);
    }
  }

  return (
    <>
      <div className="card" ref={svgHost}>
        {recommendation.visual_type === "forest" ? (
          <Interval
            estimates={forestEstimates}
            xLabel={axisLabel("estimate", labels, recommendation.spec.x)}
            title={recommendation.spec?.title}
            caption={recommendation.caption}
          />
        ) : recommendation.visual_type === "heatmap" ? (
          <Matrix
            cells={heatmapCells}
            rows={heatmapRows}
            columns={heatmapColumns}
            title={recommendation.spec?.title}
            caption={recommendation.caption}
            valueLabel="count"
            symmetricAt={Math.max(1, ...heatmapCells.map((cell) => cell.value ?? 0))}
          />
        ) : recommendation.visual_type === "box" ? (
          <PreparedBoxPlot
            summaries={boxSummaries}
            xLabel={xLabel}
            yLabel={yLabel}
            title={recommendation.spec?.title}
            caption={recommendation.caption}
          />
        ) : recommendation.visual_type === "histogram" ? (
          <PreparedHistogram
            bins={histogramBins}
            xLabel={xLabel}
            title={recommendation.spec?.title}
            caption={recommendation.caption}
          />
        ) : surface ? (
          <Surface
            grid={surfaceGrid!}
            observations={surfaceObservations}
            xLabel={xLabel}
            yLabel={yLabel}
            zLabel={axisLabel(fields.y, labels, recommendation.spec.y)}
            caption={points.data?.note ?? ""}
          />
        ) : binned ? (
          <Binned
            cells={cells}
            xLabel={xLabel}
            yLabel={yLabel}
            binCount={points.data?.bin_count ?? 30}
            binShape={points.data?.bin_shape === "square" ? "square" : "hex"}
            countScale={
              points.data?.count_scale === "linear" ? "linear"
              : points.data?.count_scale === "sqrt" ? "sqrt" : "log"
            }
            sampleSize={points.data?.sample_size ?? data.length}
            title={recommendation.spec?.title}
            caption={recommendation.caption}
          />
        ) : (
          <Cartesian
            data={data}
            mark={mark}
            xLabel={xLabel}
            yLabel={yLabel}
            /*
              What the analysis recorded, not what the renderer guesses. The
              spec carries a scale per encoding; a linear one prints nothing,
              because an axis reading "[linear]" everywhere trains people to
              stop reading the brackets.
            */
            xTransform={transformOf(recommendation.spec?.x?.scale)}
            yTransform={transformOf(recommendation.spec?.y?.scale)}
            /*
              A scatter is where overplotting hides the shape, so it is
              coloured by density. The component declines where the points
              carry a group — colour cannot say what a point is and how
              crowded it is at once.
            */
            densityColour={mark === "point"}
            totalPoints={points.data?.sampling?.rows_total}
            fit={
              points.data?.statistics?.fit_slope != null
              && points.data?.statistics?.fit_intercept != null
                ? {
                    slope: Number(points.data.statistics.fit_slope),
                    intercept: Number(points.data.statistics.fit_intercept),
                  }
                : null
            }
            /*
              Only where there is a dataset to define a subset against.
              Offering it without one would be a control that cannot work,
              which teaches a researcher the feature is broken rather than
              inapplicable.
            */
            onRecordRegion={versionId ? setRecording : undefined}
            title={recommendation.spec?.title}
            caption={recommendation.caption}
          />
        )}
      </div>

      {points.data?.sampling?.sampled && (
        <p className="note" role="note">
          Showing {points.data.sampling.rows_drawn.toLocaleString()} of{" "}
          {points.data.sampling.rows_total.toLocaleString()} rows using{" "}
          {points.data.sampling.method}. Statistics shown with the figure come
          from the full analysis run.
        </p>
      )}

      {recording && (
        <div className="card card-tight record-subset">
          <p className="note" style={{ marginTop: 0 }}>
            Recording {fields.x} between {recording.from.toPrecision(4)} and{" "}
            {recording.to.toPrecision(4)}. It will be counted against the
            dataset&rsquo;s own rows, not against what is drawn here.
          </p>
          <label>
            Name{" "}
            <input value={subsetName} aria-label="Subset name"
                   onChange={(event) => setSubsetName(event.target.value)} />
          </label>
          <button className="btn btn-primary" disabled={!subsetName.trim()}
                  onClick={() => void recordSubset()}>
            Record subset
          </button>
          <button className="btn" onClick={() => setRecording(null)}>
            Cancel
          </button>
          {recordError != null && <Failure error={recordError} />}
        </div>
      )}

      {recorded && (
        <p className="note" role="status">
          Recorded. {recorded}
        </p>
      )}

      {/* The reason, stated. This is where visualisation judgment transfers. */}
      <div className="card card-tight">
        <h3 className="eyebrow">Why this figure</h3>
        <p style={{ margin: "6px 0 0", color: "var(--ink)" }}>{recommendation.reason}</p>
        {recommendation.alternatives?.length ? (
          <p className="note">
            Also considered:{" "}
            {recommendation.alternatives.map((a) => a.visual_type.replace(/_/g, " "))
              .join(", ")}.
          </p>
        ) : null}
      </div>

      {/*
        * §4.12 — the emphasis swapped, and nothing removed.
        *
        * The same figure, but through the server: critiqued, recorded against
        * the analysis it came from, and rendered in the formats journals ask
        * for. It renders first and carries the screen's primary weight because
        * `publish.tsx:5-28` lists exactly what the other path drops — the
        * VISUALIZES edge, the critic, PDF/EPS/TIFF, a traceable filename — and
        * until now the path that loses all four was the one that looked like
        * the default, sitting directly above this one at the same weight. The
        * layering now matches the stated cost.
        */}
      <PublishFigure
        projectId={projectId}
        analysisRunId={run.id}
        spec={recommendation.spec as unknown as Record<string, unknown>}
      />

      {/*
        * Still here, still one press, and now at the weight of the thing it
        * is: a copy of what the browser is holding. `.chart-export` is the
        * save strip that belongs to a figure rather than to the prose about
        * it — 11px, quiet until hovered — which is what "demoted to text
        * weight" means in the vocabulary this stylesheet already has.
        */}
      <div className="chart-export" style={{ marginBottom: 14 }}>
        <button className="btn" onClick={exportSvg}>Save this view</button>
        <span className="note" style={{ margin: 0 }}>
          The SVG on screen, as it is. Quick, and related to nothing — for a
          figure that has to be traceable back to its analysis, export it
          {/* "below" until §4.12 moved the export above this row. A sentence
              that names a place has to be re-read when the place moves, or it
              is a control that does not do what it says (§123). */}
          {" "}above.
        </span>
      </div>

      {/* Part P — an always-available table alternative. */}
      <details className="fold kg-table">
        <summary>The numbers behind this figure<span className="fold-count">· {data.length} rows</span></summary>
        <table>
          <thead><tr><th>{xLabel}</th><th>{yLabel}</th></tr></thead>
          <tbody>
            {data.slice(0, 200).map((d) => (
              <tr key={d.id}>
                <td className="numeric">{typeof d.x === "number" ? d.x.toFixed(3) : d.x}</td>
                <td className="numeric">{d.y.toFixed(3)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {data.length > 200 && (
          <p className="note">Showing the first 200 of {data.length} rows.</p>
        )}
      </details>
    </>
  );
}
