"use client";

import { max } from "d3-array";
import { scaleLinear } from "d3-scale";
import { ChartTable } from "./ChartTable";

export type HistogramBin = {
  left: number;
  right: number;
  count: number;
};

const M = { top: 16, right: 18, bottom: 54, left: 58 };

export function Histogram({
  bins, xLabel, title, caption, width = 620, height = 330,
}: {
  bins: HistogramBin[];
  xLabel: string;
  title?: string;
  caption?: string;
  width?: number;
  height?: number;
}) {
  const inner = { w: width - M.left - M.right, h: height - M.top - M.bottom };
  const x0 = bins[0]?.left ?? 0;
  const x1 = bins[bins.length - 1]?.right ?? 1;
  const peak = max(bins, (b) => b.count) ?? 1;
  const x = scaleLinear().domain([x0, x1]).range([0, inner.w]).nice();
  const y = scaleLinear().domain([0, peak * 1.06 || 1]).range([inner.h, 0]).nice();

  return (
    <figure className="chart">
      {title && <figcaption className="chart-title">{title}</figcaption>}
      <svg className="chart-svg" style={{ maxWidth: width }}
           viewBox={`0 0 ${width} ${height}`} width="100%" role="img"
           aria-label={`${title ?? "Histogram"}. ${bins.length} prepared bins for ${xLabel}.`}>
        <g transform={`translate(${M.left},${M.top})`}>
          {y.ticks(5).map((t) => (
            <g key={t}>
              <line className="chart-grid" x1={0} x2={inner.w} y1={y(t)} y2={y(t)} />
              <text className="chart-tick numeric" x={-8} y={y(t)} dy="0.32em"
                    textAnchor="end">{t}</text>
            </g>
          ))}
          {bins.map((b) => {
            const left = x(b.left);
            const right = x(b.right);
            return (
              <rect key={`${b.left}:${b.right}`} className="chart-rect"
                    x={left} width={Math.max(1, right - left)}
                    y={y(b.count)} height={inner.h - y(b.count)}>
                <title>{`${b.left} to ${b.right}: ${b.count} observations`}</title>
              </rect>
            );
          })}
          <line className="chart-axis" x1={0} x2={inner.w} y1={inner.h} y2={inner.h} />
          <line className="chart-axis" x1={0} x2={0} y1={0} y2={inner.h} />
          {x.ticks(6).map((t) => (
            <text key={t} className="chart-tick numeric" x={x(t)} y={inner.h + 20}
                  textAnchor="middle">{t}</text>
          ))}
          <text className="chart-axis-label" x={inner.w / 2} y={inner.h + 46}
                textAnchor="middle">{xLabel}</text>
          <text className="chart-axis-label"
                transform={`translate(-44,${inner.h / 2}) rotate(-90)`}
                textAnchor="middle">count</text>
        </g>
      </svg>
      {caption && <figcaption className="chart-caption">{caption}</figcaption>}
      <ChartTable
        label={title ?? "Histogram"}
        columns={[
          { key: "left", header: "Bin start", numeric: true },
          { key: "right", header: "Bin end", numeric: true },
          { key: "count", header: "Count", numeric: true },
        ]}
        rows={bins.map((b, i) => ({ id: i, ...b }))}
      />
    </figure>
  );
}
