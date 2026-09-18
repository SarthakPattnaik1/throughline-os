"use client";

/**
 * P3 — density paths (Part F).
 *
 * Density, violin, ridgeline, raincloud, 2D density, contour. One primitive: a
 * smoothed path over a continuous axis, anchored at a baseline.
 *
 * **The rug is not optional.** A density curve is a *smoothed estimate*, and a
 * smooth bump drawn over four observations looks identical to one drawn over
 * four hundred. Showing the observations beneath the curve is what stops the
 * smoother from making sparse data look like a distribution — which is the
 * specific way this chart type misleads.
 *
 * **The curve is computed server-side.** Bandwidth is an analytical choice that
 * changes the shape, so it is executed code with a stated rule rather than a
 * browser-side smoother nobody documented (LAW 2). This component draws what it
 * is given and never smooths anything itself.
 *
 * Motion signature: the path morphs on bandwidth change, anchored at the
 * median — so the peak stays put and the reader sees the *shape* change rather
 * than the whole curve sliding.
 */

import { useId, useMemo } from "react";
import { max } from "d3-array";
import { scaleLinear } from "d3-scale";
import { area as d3area, curveBasis } from "d3-shape";
import { categorical, seriesEdge, seriesStroke } from "@/lib/tokens";
import { ChartTable } from "./ChartTable";
import { ChartTooltip, readable, useChartHover } from "./interaction";

export type DensityCurve = {
  /** Stable identity, so a re-render moves the path rather than redrawing it. */
  id: string;
  label: string;
  x: number[];
  density: number[];
  observations?: number[];
  quartiles?: number[];
  n: number;
};

/** The x-value where a curve's density is highest — the mode, read directly
    off the arrays the curve was already given rather than re-estimated. */
function peakOf(curve: DensityCurve): number | undefined {
  if (!curve.x.length || !curve.density.length) return undefined;
  let bestIndex = 0;
  for (let i = 1; i < curve.density.length; i += 1) {
    if (curve.density[i] > curve.density[bestIndex]) bestIndex = i;
  }
  return curve.x[bestIndex];
}

const M = { top: 14, right: 18, bottom: 46, left: 56 };

export function Density({
  curves, xLabel, xUnit, title, caption, bandwidthNote,
  width = 620, height = 320,
}: {
  curves: DensityCurve[];
  xLabel: string;
  xUnit?: string;
  title?: string;
  caption?: string;
  bandwidthNote?: string;
  width?: number;
  height?: number;
}) {
  const hover = useChartHover();
  const hoveredCurve = curves.find((c) => c.id === hover.hovered) ?? null;
  const clipId = useId();
  const inner = { w: width - M.left - M.right, h: height - M.top - M.bottom };

  const xScale = useMemo(() => {
    const all = curves.flatMap((c) => c.x);
    return scaleLinear()
      .domain([Math.min(...all), Math.max(...all)])
      .range([0, inner.w]).nice();
  }, [curves, inner.w]);

  const yScale = useMemo(() => {
    const peak = max(curves.flatMap((c) => c.density)) ?? 1;
    return scaleLinear().domain([0, peak * 1.08]).range([inner.h, 0]);
  }, [curves, inner.h]);

  const shape = useMemo(
    () => d3area<[number, number]>()
      .x((d) => xScale(d[0]))
      .y0(inner.h)
      .y1((d) => yScale(d[1]))
      .curve(curveBasis),
    [xScale, yScale, inner.h]);

  const ticks = xScale.ticks(6);

  // The median column only appears when at least one curve actually carries
  // quartiles — peak is always trivially available from the curve's own arrays.
  const hasMedian = curves.some((c) => c.quartiles?.[1] !== undefined);
  const tableColumns = [
    { key: "label", header: "Group" },
    { key: "n", header: "n", numeric: true },
    { key: "peak", header: "Peak", numeric: true },
    ...(hasMedian ? [{ key: "median", header: "Median", numeric: true }] : []),
  ];
  const tableRows = curves.map((c) => ({
    id: c.id, label: c.label, n: c.n, peak: peakOf(c), median: c.quartiles?.[1],
  }));

  return (
    <figure className="chart">
      {title && <figcaption className="chart-title">{title}</figcaption>}

      <svg
        className="chart-svg" style={{ maxWidth: width }}
        viewBox={`0 0 ${width} ${height}`}
        width="100%"
        role="img"
        aria-label={`${title ?? "Distribution"}. Estimated distribution of ${xLabel}`
          + `${curves.length > 1 ? ` across ${curves.length} groups` : ""}. `
          + `${curves[0]?.n ?? 0} observations. A table of the values follows.`}
      >
        <defs>
          <clipPath id={clipId}>
            <rect x={0} y={-6} width={inner.w} height={inner.h + 6} />
          </clipPath>
        </defs>

        <g transform={`translate(${M.left},${M.top})`}>
          {yScale.ticks(4).map((t) => (
            <line key={t} className="chart-grid"
                  x1={0} x2={inner.w} y1={yScale(t)} y2={yScale(t)} />
          ))}

          <g clipPath={`url(#${clipId})`}>
            {curves.map((curve, index) => {
              const colour = categorical[index % categorical.length];
              const points = curve.x.map((x, i) =>
                [x, curve.density[i]] as [number, number]);
              return (
                // Keyed by curve id, so a bandwidth change morphs the path
                // instead of destroying and recreating it.
                <g key={curve.id}
                   style={{ opacity: hover.emphasis(curve.id) }}
                   {...hover.markProps(curve.id)}>
                  <path className="chart-density" d={shape(points) ?? undefined}
                        style={{ fill: colour, stroke: seriesStroke(colour) }} />
                  {/* The median, so the eye has an anchor the smoother cannot move. */}
                  {curve.quartiles?.[1] !== undefined && (
                    <line className="chart-median"
                          x1={xScale(curve.quartiles[1])} x2={xScale(curve.quartiles[1])}
                          y1={inner.h} y2={yScale(max(curve.density) ?? 0)}
                          style={{ stroke: seriesStroke(colour) }} />
                  )}
                </g>
              );
            })}
          </g>

          {/* The rug: every observation, so a bump over four points cannot
              masquerade as a distribution. */}
          {curves.map((curve, index) => (
            <g key={`rug-${curve.id}`}>
              {(curve.observations ?? []).map((value, i) => (
                <line key={i} className="chart-rug"
                      x1={xScale(value)} x2={xScale(value)}
                      y1={inner.h} y2={inner.h + 6}
                      style={{ stroke: seriesStroke(categorical[index % categorical.length]) }} />
              ))}
            </g>
          ))}

          <line className="chart-axis" x1={0} x2={inner.w} y1={inner.h} y2={inner.h} />
          {ticks.map((t) => (
            <text key={t} className="chart-tick" x={xScale(t)} y={inner.h + 22}
                  textAnchor="middle">{t}</text>
          ))}
          <text className="chart-axis-label" x={inner.w / 2} y={inner.h + 40}
                textAnchor="middle">
            {xUnit ? `${xLabel} (${xUnit})` : xLabel}
          </text>
          <text className="chart-axis-label"
                transform={`translate(${-M.left + 14},${inner.h / 2}) rotate(-90)`}
                textAnchor="middle">estimated density</text>
        </g>
      </svg>

      {curves.length > 1 && (
        <div className="chart-legend">
          {curves.map((curve, index) => (
            <span key={curve.id}>
              <i style={{ background: categorical[index % categorical.length],
                          boxShadow: seriesEdge(categorical[index % categorical.length])
                            ? `inset 0 0 0 1px ${seriesEdge(categorical[index % categorical.length])}`
                            : undefined }} aria-hidden />
              {curve.label}
            </span>
          ))}
        </div>
      )}

      {bandwidthNote && <p className="chart-caption">{bandwidthNote}</p>}
      {caption && <figcaption className="chart-caption">{caption}</figcaption>}

      <ChartTooltip pointer={hover.pointer} title={hoveredCurve?.label}
        rows={hoveredCurve ? [
          { label: "observations", value: readable(hoveredCurve.n) },
        ] : []} />

      <ChartTable
        highlightId={hover.hovered}
        onHighlight={hover.setHovered}
        columns={tableColumns}
        rows={tableRows}
        label={title ?? `Distribution of ${xLabel}`}
      />
    </figure>
  );
}
