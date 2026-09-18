"use client";

/**
 * P15 — the specification curve.
 *
 * The chart is the argument. A single number tells a researcher nothing about
 * whether it would survive a different covariate set; a column of dots, one per
 * specification, with the zero line drawn through them, tells them immediately.
 *
 * Two rules govern this component and neither is cosmetic.
 *
 * **The dots are ordered by covariate count, never by effect size.** Sorting
 * this chart by magnitude would draw a smooth curve from smallest to largest
 * and invite the reader's eye straight to the end they prefer. The taxonomy is
 * blunt about the risk and so is the API: there is no "best" specification in
 * the payload to render even if someone wanted to.
 *
 * **The covariates come from the researcher.** The picker lists columns; it
 * makes no suggestion about which belong in the model, because deciding what to
 * adjust for is a causal judgement and the data cannot make it (LAW 6).
 */

import { useState } from "react";
import { scaleLinear } from "d3-scale";
import { api } from "@/lib/api";
import { Empty, Failure, Loading } from "./primitives";
import { VerdictBody, VerdictCard } from "./Verdict";

type Specification = {
  covariates: string[];
  n_covariates: number;
  estimate: number;
  p_value: number | null;
  ci_low: number | null;
  ci_high: number | null;
  sample_size: number | null;
  significant: boolean;
};

type Curve = {
  specifications: Specification[];
  failed: Array<{ covariates: string[]; reason: string }>;
  total: number;
  outcome: string;
  exposure: string;
  median_estimate: number;
  min_estimate: number;
  max_estimate: number;
  share_positive: number;
  share_significant: number;
  sign_stable: boolean;
  significance_stable: boolean;
  headline: string;
  ordering: string;
  verdict: VerdictBody;
};

export function SpecificationCurve({ projectId, datasetVersionId, columns }: {
  projectId: string;
  datasetVersionId: string | null;
  columns: string[];
}) {
  const [outcome, setOutcome] = useState("");
  const [exposure, setExposure] = useState("");
  const [candidates, setCandidates] = useState<string[]>([]);
  const [curve, setCurve] = useState<Curve | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function run() {
    if (!datasetVersionId || !outcome || !exposure) return;
    setBusy(true); setError(null); setCurve(null);
    try {
      setCurve(await api.post<Curve>(
        `/api/projects/${projectId}/specification-curve`,
        { dataset_version_id: datasetVersionId, outcome, exposure,
          candidates }));
    } catch (err) { setError(err); } finally { setBusy(false); }
  }

  if (!datasetVersionId) {
    return (
      <Empty
        title="A dataset is needed"
        hint="The specification curve refits one relationship across every combination of the covariates you choose, and shows the spread rather than a single number."
      />
    );
  }

  const available = columns.filter((c) => c !== outcome && c !== exposure);

  return (
    <>
      <p className="lede">
        Pick a relationship and the covariates you have a reason to adjust for.
        Every combination is fitted, and the spread is shown — because if the
        answer changes with the covariate set, the number in a paper is a choice
        rather than a finding.
      </p>

      <div className="sc-picker">
        <label>
          <span>Outcome</span>
          <select value={outcome} onChange={(e) => setOutcome(e.target.value)}>
            <option value="">choose…</option>
            {columns.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </label>
        <label>
          <span>Exposure</span>
          <select value={exposure} onChange={(e) => setExposure(e.target.value)}>
            <option value="">choose…</option>
            {columns.filter((c) => c !== outcome).map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
        </label>
      </div>

      {outcome && exposure && (
        <div className="sc-covariates">
          <h3 className="eyebrow">Covariates to try</h3>
          {/* Deliberately no recommendation. Which variables belong in the
              model is a causal judgement and the data cannot make it. */}
          <p className="sc-hint">
            Choose the ones you have a reason to adjust for. The system does not
            suggest any: what belongs in a model is a judgement about cause, and
            the data cannot make it.
          </p>
          <div className="sc-chips">
            {available.map((column) => (
              <button
                key={column}
                className="sc-chip"
                data-on={candidates.includes(column)}
                onClick={() => setCandidates((current) =>
                  current.includes(column)
                    ? current.filter((c) => c !== column)
                    : [...current, column])}
              >
                {column}
              </button>
            ))}
          </div>
          <button className="btn btn-primary" onClick={() => void run()}
                  disabled={busy}>
            {busy ? "Fitting…" : `Fit ${2 ** candidates.length} specifications`}
          </button>
        </div>
      )}

      {error ? <Failure error={error} /> : null}
      {busy && <Loading rows={3} label="Fitting every combination" />}

      {curve && (
        <>
          <p className="sc-headline">{curve.headline}</p>
          <CurveChart curve={curve} />
          <VerdictCard verdict={curve.verdict} />
          <p className="pat-foot">{curve.ordering}</p>
        </>
      )}
    </>
  );
}

const M = { top: 16, right: 20, bottom: 40, left: 70 };
const ROW = 22;

function CurveChart({ curve }: { curve: Curve }) {
  const specs = curve.specifications;
  const height = M.top + M.bottom + specs.length * ROW;
  const width = 640;
  const inner = { w: width - M.left - M.right, h: specs.length * ROW };

  const lows = specs.map((s) => s.ci_low ?? s.estimate);
  const highs = specs.map((s) => s.ci_high ?? s.estimate);
  const x = scaleLinear()
    .domain([Math.min(0, ...lows), Math.max(0, ...highs)])
    .range([0, inner.w]).nice();

  return (
    <figure className="chart sc-chart">
      <svg className="chart-svg" viewBox={`0 0 ${width} ${height}`} width="100%"
           role="img"
           aria-label={`Specification curve. ${specs.length} specifications, `
             + `estimate from ${curve.min_estimate} to ${curve.max_estimate}. `
             + "A table of every specification follows."}>
        <g transform={`translate(${M.left},${M.top})`}>
          {/* Zero, drawn first and heavier than anything else: whether the
              intervals cross it is the entire question. */}
          <line className="chart-null" x1={x(0)} x2={x(0)} y1={0} y2={inner.h} />

          {specs.map((spec, index) => {
            const y = index * ROW + ROW / 2;
            return (
              <g key={index}>
                {spec.ci_low !== null && spec.ci_high !== null && (
                  <line className="sc-interval"
                        x1={x(spec.ci_low)} x2={x(spec.ci_high)} y1={y} y2={y} />
                )}
                <circle
                  className="sc-dot"
                  data-significant={spec.significant}
                  cx={x(spec.estimate)} cy={y} r={4}
                />
                <text className="chart-tick" x={-8} y={y + 4} textAnchor="end">
                  {spec.covariates.length
                    ? `+ ${spec.covariates.join(", ")}`
                    : "unadjusted"}
                </text>
              </g>
            );
          })}

          <line className="chart-axis" x1={0} x2={inner.w}
                y1={inner.h} y2={inner.h} />
          {x.ticks(6).map((t) => (
            <text key={t} className="chart-tick" x={x(t)} y={inner.h + 20}
                  textAnchor="middle">{t}</text>
          ))}
          <text className="chart-axis-label" x={inner.w / 2} y={inner.h + 36}
                textAnchor="middle">
            coefficient on {curve.exposure}
          </text>
        </g>
      </svg>

      {/* Part P — the figure as a table, in the same fixed order. */}
      <details className="fold kg-table">
        <summary>Every specification<span className="fold-count">· {specs.length}</span></summary>
        <table>
          <thead>
            <tr><th>Adjusted for</th>
                <th style={{ textAlign: "right" }}>Estimate</th>
                <th style={{ textAlign: "right" }}>Interval</th>
                <th>After correction</th></tr>
          </thead>
          <tbody>
            {specs.map((spec, index) => (
              <tr key={index}>
                <td>{spec.covariates.join(", ") || "nothing"}</td>
                <td className="numeric" style={{ textAlign: "right" }}>
                  {spec.estimate.toFixed(4)}
                </td>
                <td className="numeric" style={{ textAlign: "right" }}>
                  {spec.ci_low == null || spec.ci_high == null
                    ? "—"
                    : `${spec.ci_low.toFixed(3)} to ${spec.ci_high.toFixed(3)}`}
                </td>
                <td>{spec.significant ? "excludes zero" : "consistent with none"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>

      {curve.failed.length > 0 && (
        // Listed, not dropped: the covariate set that could not be fitted is
        // exactly the one a reader should know about.
        <div className="sc-failed">
          <h4 className="eyebrow">Could not be fitted</h4>
          <ul>
            {curve.failed.map((f, i) => (
              <li key={i}>
                <b>{f.covariates.join(", ") || "nothing"}</b> — {f.reason}
              </li>
            ))}
          </ul>
        </div>
      )}
    </figure>
  );
}
