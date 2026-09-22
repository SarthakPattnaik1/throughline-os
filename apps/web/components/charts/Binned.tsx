"use client";

/**
 * P5 — binned aggregation (Part F).
 *
 * Hexbin, 2-D histogram, raster density. One primitive: the plane divided into
 * cells, each shaded by how many observations fall inside it.
 *
 * **Why this exists at all.** A scatter plot degrades continuously and never
 * says so. Past a few thousand rows the dense region fills in, and a cell
 * holding fifty observations and one holding five thousand both render as solid
 * ink — so the reader sees the *outline* of the data and cannot see where most
 * of it actually is. Binning replaces "is there ink here" with "how much is
 * here", which is the question a dense scatter was silently failing to answer.
 *
 * **Hexagons, not squares.** A square grid produces horizontal and vertical
 * banding that the eye reads as structure in the data. Hexagons tile without
 * that artefact, and every point in a hexagon sits closer to its centre than in
 * a square of equal area, so a cell's count is a fairer summary of its
 * neighbourhood.
 *
 * **The bin count is stated, always.** Bin width is not a rendering detail:
 * widen it and two modes merge into one, narrow it and sampling noise reads as
 * structure. Both are defensible pictures of the same data and they disagree,
 * and nothing in the image tells them apart — so the number that produced the
 * shape is printed with the figure, exactly as a density plot states its
 * bandwidth. The critic refuses to create a binned figure without one.
 *
 * **Counts are computed server-side** (LAW 2). This component draws the cells it
 * is given and bins nothing itself.
 *
 * Motion signature: cells reveal in a wave from the densest outward, and morph
 * on bin-width change rather than being redrawn — so the reader watches the
 * *resolution* change instead of watching one chart replaced by another.
 */

import { useId, useMemo } from "react";
import { extent, max } from "d3-array";
import { scaleLinear } from "d3-scale";
import { interpolateViridis } from "d3-scale-chromatic";
import { ChartTable } from "./ChartTable";
import { ChartTooltip, readable, useChartHover } from "./interaction";

export type Cell = {
  /** Cell centre, in data space. */
  x: number;
  y: number;
  /** Observations in this cell. Never zero — empty cells are absent, not shaded. */
  count: number;
};

/**
 * Hexagons see density; squares read values.
 *
 * A square cell maps onto a legible x-range and y-range, and the marginal
 * distributions can be recovered by summing rows and columns — neither of which
 * a hexagonal lattice offers. What squares cost is the axis-aligned banding the
 * eye reads as structure, which is why hexagons are the default.
 */
export type BinShape = "hex" | "square";

/**
 * Binned counts are heavy-tailed often enough that linear is the wrong default:
 * a few central cells take the top of the range and the rest collapse into the
 * darkest shades, reproducing the overplotting this chart exists to cure.
 */
export type CountScale = "linear" | "log" | "sqrt";

const M = { top: 14, right: 74, bottom: 46, left: 60 };

/** Where a count sits in the colour ramp, 0–1, under the chosen scale. */
function transform(count: number, peak: number, scale: CountScale): number {
  if (peak <= 0) return 0;
  if (scale === "log") {
    // log1p so a single observation is distinguishable from an empty cell
    // rather than mapping to the very bottom of the ramp.
    return Math.log1p(count) / Math.log1p(peak);
  }
  if (scale === "sqrt") return Math.sqrt(count) / Math.sqrt(peak);
  return count / peak;
}

/** Ticks that sit at even *visual* intervals, so the ramp reads correctly. */
function legendTicks(peak: number, scale: CountScale): number[] {
  if (scale === "log") {
    const ticks = [1];
    for (let value = 10; value < peak; value *= 10) ticks.push(value);
    ticks.push(peak);
    return ticks;
  }
  return [1, Math.round(peak / 2), peak].filter((v, i, a) => a.indexOf(v) === i);
}

export function Binned({
  cells, xLabel, yLabel, xUnit, yUnit, binCount, sampleSize,
  binShape = "hex", countScale = "log",
  title, caption, fit, sampled = false,
  width = 620, height = 340,
}: {
  cells: Cell[];
  xLabel: string;
  yLabel: string;
  xUnit?: string;
  yUnit?: string;
  /** Cells across the range. Required: the figure must state how it was binned. */
  binCount: number;
  sampleSize: number;
  binShape?: BinShape;
  /** Named on the colour bar, because it changes the apparent ratio between cells. */
  countScale?: CountScale;
  title?: string;
  caption?: string;
  /** Optional least-squares line, for orientation only. */
  fit?: { slope: number; intercept: number };
  /** Counts came from a bounded sample rather than every analysis row. */
  sampled?: boolean;
  width?: number;
  height?: number;
}) {
  const hover = useChartHover();
  // A cell has no id of its own: its coordinates are its identity, and they
  // are stable because the binning is deterministic.
  const hoveredCell = cells.find((c) => `${c.x}:${c.y}` === hover.hovered) ?? null;
  const clipId = useId();
  const plotWidth = width - M.left - M.right;
  const plotHeight = height - M.top - M.bottom;

  const { x, y, colour, radius, peak } = useMemo(() => {
    const [x0 = 0, x1 = 1] = extent(cells, (c) => c.x) as [number, number];
    const [y0 = 0, y1 = 1] = extent(cells, (c) => c.y) as [number, number];
    // A hair of padding so cells centred on the extremes are not clipped in half.
    const padX = (x1 - x0) / Math.max(binCount, 1) / 2;
    const padY = (y1 - y0) / Math.max(binCount, 1) / 2;

    const peakCount = max(cells, (c) => c.count) ?? 1;
    return {
      x: scaleLinear().domain([x0 - padX, x1 + padX]).nice().range([0, plotWidth]),
      y: scaleLinear().domain([y0 - padY, y1 + padY]).nice().range([plotHeight, 0]),
      // Sequential and perceptually uniform: the encoded quantity is a count —
      // ordered, single-ended, with a real zero. A diverging scale would invent
      // a midpoint that does not exist in the data.
      // Sequential and perceptually uniform, transformed by countScale. The
      // transform is applied to the *position* in the ramp, not to the tick
      // labels, so the legend still reads in observations.
      colour: (count: number) =>
        interpolateViridis(transform(count, peakCount, countScale)),
      radius: plotWidth / Math.max(binCount, 1) / 1.732,
      peak: peakCount,
    };
  }, [cells, binCount, plotWidth, plotHeight, countScale]);

  // The cell outline, drawn once and reused at every position.
  const cellPoints = useMemo(() => {
    if (binShape === "square") {
      // Edge length chosen so a square covers the same area as the hexagon it
      // replaces, keeping the two shapes visually comparable at one bin count.
      const half = radius * 0.9306;
      return [`${-half},${-half}`, `${half},${-half}`,
              `${half},${half}`, `${-half},${half}`].join(" ");
    }
    const points: string[] = [];
    for (let i = 0; i < 6; i += 1) {
      const angle = (Math.PI / 3) * i;
      points.push(`${(radius * Math.cos(angle)).toFixed(2)},${(radius * Math.sin(angle)).toFixed(2)}`);
    }
    return points.join(" ");
  }, [radius, binShape]);

  const xTicks = x.ticks(6);
  const yTicks = y.ticks(5);

  const tableColumns = [
    { key: "x", header: xLabel, numeric: true },
    { key: "y", header: yLabel, numeric: true },
    { key: "count", header: "Count", numeric: true },
  ];
  const tableRows = cells.map((c) => ({
    // Same identity the polygon and the tooltip use.
    id: `${c.x}:${c.y}`, x: c.x, y: c.y, count: c.count }));

  return (
    <figure className="chart chart-binned">
      {title && <figcaption className="chart-title">{title}</figcaption>}
      <svg
        /*
         * `viewBox` + `width="100%"`, the way every other chart in this folder
         * is drawn. This one alone carried a fixed `width={width}` and no
         * viewBox, so it rendered at a flat 620px regardless of the column it
         * was in — measured spilling 126px out of a 468px workspace, the only
         * one of thirteen charts on the primitives screen that did. Since the
         * shell's edges became draggable that column can be narrow at any
         * window size, so a fixed width is not a width, it is a guess.
         */
        className="chart-svg" style={{ maxWidth: width }}
        viewBox={`0 0 ${width} ${height}`}
        width="100%" height={height} role="img"
        aria-label={
          `Binned density of ${yLabel} against ${xLabel}. `
          + `${sampleSize.toLocaleString()} ${sampled ? "sampled " : ""}observations in ${cells.length} `
          + `occupied cells, ${binCount} cells across each axis. `
          + `The densest cell holds ${peak.toLocaleString()} observations.`
        }
      >
        <defs>
          <clipPath id={clipId}>
            <rect x={0} y={0} width={plotWidth} height={plotHeight} />
          </clipPath>
        </defs>
        <g transform={`translate(${M.left},${M.top})`}>
          {yTicks.map((t) => (
            <g key={t} transform={`translate(0,${y(t)})`}>
              <line x2={plotWidth} className="chart-grid" />
              <text x={-8} dy="0.32em" className="chart-tick numeric" textAnchor="end">{t}</text>
            </g>
          ))}
          {xTicks.map((t) => (
            <g key={t} transform={`translate(${x(t)},${plotHeight})`}>
              <line y2={6} className="chart-axis" />
              <text y={20} className="chart-tick numeric" textAnchor="middle">{t}</text>
            </g>
          ))}

          <g clipPath={`url(#${clipId})`}>
            {cells.map((cell) => (
              // Keyed on position so a bin-width change morphs the cells that
              // persist rather than fading the whole field out and back in.
              <polygon
                key={`${cell.x}:${cell.y}`}
                points={cellPoints}
                transform={`translate(${x(cell.x)},${y(cell.y)})`}
                fill={colour(cell.count)}
                stroke="var(--n-0)"
                strokeWidth={0.4}
                style={{ opacity: hover.emphasis(`${cell.x}:${cell.y}`) }}
                {...hover.markProps(`${cell.x}:${cell.y}`)}
              >
                <title>
                  {`${cell.count.toLocaleString()} observations near `
                   + `${xLabel} ${cell.x}, ${yLabel} ${cell.y}`}
                </title>
              </polygon>
            ))}

            {fit && (
              <line
                x1={x(x.domain()[0])}
                y1={y(fit.slope * x.domain()[0] + fit.intercept)}
                x2={x(x.domain()[1])}
                y2={y(fit.slope * x.domain()[1] + fit.intercept)}
                className="chart-fit"
                stroke="var(--negative)" strokeWidth={1.4} strokeDasharray="5 3"
              />
            )}
          </g>

          <text
            transform={`translate(${plotWidth / 2},${plotHeight + 40})`}
            className="chart-axis-label" textAnchor="middle"
          >
            {xLabel}{xUnit ? ` (${xUnit})` : ""}
          </text>
          <text
            transform={`translate(${-M.left + 14},${plotHeight / 2}) rotate(-90)`}
            className="chart-axis-label" textAnchor="middle"
          >
            {yLabel}{yUnit ? ` (${yUnit})` : ""}
          </text>

          <Legend peak={peak} countScale={countScale}
                  height={plotHeight} x={plotWidth + 16} />
        </g>
      </svg>

      <p className="chart-caption">
        {sampleSize.toLocaleString()} {sampled ? "sampled " : ""}observations,
        binned into {binCount}{" "}
        {binShape === "square" ? "square" : "hexagonal"} cells per axis. Shade
        shows {sampled ? "sampled " : ""}observations per cell
        {countScale !== "linear" && ` on a ${countScale} scale`}; empty cells are
        left blank rather than shaded, so no data and a little data stay
        distinguishable.
        {countScale === "log" && " A logarithmic scale is used because binned "
          + "counts are heavy-tailed: on a linear ramp the densest few cells "
          + "would take the whole range and everything else would read as one "
          + "shade."}
      </p>
      {caption && <p className="chart-caption">{caption}</p>}

      <ChartTooltip pointer={hover.pointer} rows={hoveredCell ? [
        { label: xLabel, value: readable(hoveredCell.x) },
        { label: yLabel, value: readable(hoveredCell.y) },
        { label: sampled ? "sampled observations" : "observations",
          value: readable(hoveredCell.count) },
      ] : []} />

      <ChartTable
        highlightId={hover.hovered}
        onHighlight={hover.setHovered}
        columns={tableColumns}
        rows={tableRows}
        label={title ?? `Binned counts of ${yLabel} against ${xLabel}`}
        maxRows={200}
        totalRows={sampleSize}
      />
    </figure>
  );
}

/**
 * The colour bar.
 *
 * It takes no `colour` function, and that is deliberate rather than an
 * oversight: the bar is painted in *ramp space* — thirty-two equal bands walking
 * the ramp from end to end — while the tick labels are placed through the same
 * `transform` the cells use. So a value's label sits at exactly the ramp
 * position its cell is painted with, under any of the three scales, and the bar
 * shows the transform itself rather than a linear gradient that would
 * misdescribe the mapping. Passing the cells' `colour` closure in and ignoring
 * it read as a legend that had forgotten to use its own palette.
 */
function Legend({ peak, countScale, height, x }: {
  peak: number;
  countScale: CountScale;
  height: number;
  x: number;
}) {
  const steps = 32;
  const barHeight = Math.min(height, 160);
  const band = barHeight / steps;
  const ticks = legendTicks(peak, countScale);

  return (
    <g transform={`translate(${x},0)`} aria-hidden>
      {Array.from({ length: steps }, (_, i) => {
        // The bar is painted in ramp space, so it shows the transform itself
        // rather than a linear gradient that would misdescribe the mapping.
        const position = (i + 0.5) / steps;
        return (
          <rect
            key={i}
            x={0}
            y={barHeight - (i + 1) * band}
            width={12}
            height={band + 0.5}
            fill={interpolateViridis(position)}
          />
        );
      })}
      {ticks.map((value) => {
        const position = transform(value, peak, countScale);
        return (
          <text
            key={value}
            x={17}
            y={barHeight - position * barHeight}
            dy="0.32em"
            className="chart-tick numeric"
          >
            {value.toLocaleString()}
          </text>
        );
      })}
      <text
        transform={`translate(${-4},${barHeight + 22})`}
        className="chart-tick" textAnchor="start"
      >
        per cell{countScale !== "linear" ? ` (${countScale})` : ""}
      </text>
    </g>
  );
}
