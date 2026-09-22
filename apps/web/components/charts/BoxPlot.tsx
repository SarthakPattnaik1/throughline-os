"use client";

import { extent } from "d3-array";
import { scaleBand, scaleLinear } from "d3-scale";
import { ChartTable } from "./ChartTable";

export type BoxSummary = {
  group: string;
  q1: number;
  median: number;
  q3: number;
  low: number;
  high: number;
  n: number;
};

const M = { top: 16, right: 20, bottom: 58, left: 62 };

export function BoxPlot({
  summaries, xLabel, yLabel, title, caption, width = 620, height = 340,
}: {
  summaries: BoxSummary[];
  xLabel: string;
  yLabel: string;
  title?: string;
  caption?: string;
  width?: number;
  height?: number;
}) {
  const inner = { w: width - M.left - M.right, h: height - M.top - M.bottom };
  const x = scaleBand<string>()
    .domain(summaries.map((s) => s.group))
    .range([0, inner.w])
    .padding(0.3);
  const limits = summaries.flatMap((s) => [s.low, s.high]);
  const [lo = 0, hi = 1] = extent(limits) as [number, number];
  const pad = (hi - lo) * 0.06 || 1;
  const y = scaleLinear().domain([lo - pad, hi + pad]).range([inner.h, 0]).nice();
  const ticks = y.ticks(5);

  return (
    <figure className="chart">
      {title && <figcaption className="chart-title">{title}</figcaption>}
      <svg className="chart-svg" style={{ maxWidth: width }}
           viewBox={`0 0 ${width} ${height}`} width="100%" role="img"
           aria-label={`${title ?? "Box plot"}. ${summaries.length} groups of ${yLabel}. `
             + "Boxes show the interquartile range, the centre line is the median, "
             + "and whiskers stop at the most extreme value within 1.5 IQR."}>
        <g transform={`translate(${M.left},${M.top})`}>
          {ticks.map((t) => (
            <g key={t}>
              <line className="chart-grid" x1={0} x2={inner.w} y1={y(t)} y2={y(t)} />
              <text className="chart-tick numeric" x={-8} y={y(t)} dy="0.32em"
                    textAnchor="end">{t}</text>
            </g>
          ))}
          {summaries.map((s) => {
            const left = x(s.group) ?? 0;
            const band = x.bandwidth();
            const centre = left + band / 2;
            return (
              <g key={s.group}>
                <line className="chart-ci" x1={centre} x2={centre}
                      y1={y(s.low)} y2={y(s.high)} />
                <line className="chart-cap" x1={centre - band * 0.22}
                      x2={centre + band * 0.22} y1={y(s.low)} y2={y(s.low)} />
                <line className="chart-cap" x1={centre - band * 0.22}
                      x2={centre + band * 0.22} y1={y(s.high)} y2={y(s.high)} />
                <rect className="chart-rect" x={left} width={band}
                      y={y(s.q3)} height={Math.max(1, y(s.q1) - y(s.q3))}>
                  <title>{`${s.group}: median ${s.median}, IQR ${s.q1} to ${s.q3}, n=${s.n}`}</title>
                </rect>
                <line className="chart-axis" x1={left} x2={left + band}
                      y1={y(s.median)} y2={y(s.median)} />
                <text className="chart-tick" x={centre} y={inner.h + 22}
                      textAnchor="middle">{s.group}</text>
              </g>
            );
          })}
          <line className="chart-axis" x1={0} x2={inner.w} y1={inner.h} y2={inner.h} />
          <line className="chart-axis" x1={0} x2={0} y1={0} y2={inner.h} />
          <text className="chart-axis-label" x={inner.w / 2} y={inner.h + 48}
                textAnchor="middle">{xLabel}</text>
          <text className="chart-axis-label"
                transform={`translate(-48,${inner.h / 2}) rotate(-90)`}
                textAnchor="middle">{yLabel}</text>
        </g>
      </svg>
      {caption && <figcaption className="chart-caption">{caption}</figcaption>}
      <ChartTable
        label={title ?? "Box plot"}
        columns={[
          { key: "group", header: xLabel },
          { key: "low", header: "Low whisker", numeric: true },
          { key: "q1", header: "Q1", numeric: true },
          { key: "median", header: "Median", numeric: true },
          { key: "q3", header: "Q3", numeric: true },
          { key: "high", header: "High whisker", numeric: true },
          { key: "n", header: "n", numeric: true },
        ]}
        rows={summaries.map((s) => ({ id: s.group, ...s }))}
      />
    </figure>
  );
}
