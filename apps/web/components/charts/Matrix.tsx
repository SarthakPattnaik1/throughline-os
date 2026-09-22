"use client";

/**
 * P5 — matrix grid (Part F).
 *
 * Correlation matrix, confusion matrix, adjacency matrix, clustered heatmap,
 * coding matrix. One primitive: a grid of cells whose fill encodes a value.
 *
 * For a discovery run this is the figure that shows the *whole* dataset at
 * once. A forest plot ranks pairs; a matrix shows the structure — which
 * variables cluster, which are independent of everything, and where a
 * confounder is sitting in plain sight because it correlates with both sides of
 * the relationship you care about.
 *
 * **Diverging, anchored at zero.** A correlation of −0.9 and one of +0.9 are
 * equally strong and opposite, so the scale must be symmetric around a
 * meaningful zero. A sequential ramp here would make negative relationships
 * look weak.
 *
 * **Interpolated in OKLCH, not RGB.** RGB interpolation passes through muddy
 * greys and makes equal steps look unequal — the perceptual distance between
 * two cells stops matching the difference between two numbers, which is the one
 * thing a heatmap must get right.
 */

import { useId, useMemo } from "react";
import { ChartTable } from "./ChartTable";
import { ChartTooltip, readable, useChartHover } from "./interaction";

export type Cell = {
  row: string;
  column: string;
  value: number | null;
};

const M = { top: 8, right: 8, bottom: 8, left: 8 };

/**
 * Diverging blue↔red through neutral, mixed in OKLCH.
 *
 * `color-mix` does the interpolation in the browser's own colour engine, which
 * keeps this honest without shipping a colour library.
 */
function fillFor(
  value: number | null, max: number, mode: "diverging" | "sequential",
): string {
  if (value === null) return "var(--n-100)";
  const t = Math.min(Math.abs(value) / (max || 1), 1);
  if (mode === "sequential") {
    return `color-mix(in oklch, #0072B2 ${Math.round(t * 88)}%, var(--n-50))`;
  }
  const hue = value < 0 ? "#0072B2" : "#D55E00";
  // Percentage of the hue mixed into the neutral: 0 at the anchor, 100 at the
  // extreme, so the anchor really does read as "no relationship".
  return `color-mix(in oklch, ${hue} ${Math.round(t * 88)}%, var(--n-50))`;
}

export function Matrix({
  cells, rows, columns, title, caption, valueLabel = "correlation",
  cellSize = 34, symmetricAt = 1, scaleMode = "diverging",
  diagonalNeutral = true,
}: {
  cells: Cell[];
  /** Display names, in the order they should appear. */
  rows: string[];
  columns: string[];
  title?: string;
  caption?: string;
  valueLabel?: string;
  cellSize?: number;
  /** The value at which the scale saturates. 1 for correlations. */
  symmetricAt?: number;
  /** Correlations need a signed scale; counts and intensities do not. */
  scaleMode?: "diverging" | "sequential";
  /** Only a same-variable matrix has a special self-comparison diagonal. */
  diagonalNeutral?: boolean;
}) {
  const clipId = useId();
  const hover = useChartHover();
  const hovered = cells.find(
    (c) => `${c.row}\u0000${c.column}` === hover.hovered) ?? null;

  // A left gutter wide enough for the longest label, so nothing is clipped.
  const gutter = Math.min(
    200, Math.max(90, ...rows.map((r) => r.length * 6.2)));
  const headerHeight = Math.min(
    140, Math.max(70, ...columns.map((c) => c.length * 6.2)));

  const width = M.left + gutter + columns.length * cellSize + M.right;
  const height = M.top + headerHeight + rows.length * cellSize + M.bottom;

  const index = useMemo(() => {
    const map = new Map<string, Cell>();
    for (const cell of cells) map.set(`${cell.row}\u0000${cell.column}`, cell);
    return map;
  }, [cells]);

  const tableColumns = [
    { key: "row", header: "Row" },
    { key: "column", header: "Column" },
    { key: "value", header: valueLabel, numeric: true },
  ];
  // `id` is what ties a table row to its cell for the shared highlight, and
  // it is the same coordinate pair the grid looks cells up by.
  const tableRows = cells.map((c) => ({
    id: `${c.row}\u0000${c.column}`,
    row: c.row, column: c.column, value: c.value,
  }));

  return (
    <figure className="chart">
      {title && <figcaption className="chart-title">{title}</figcaption>}

      <svg
        className="chart-svg" style={{ maxWidth: width }}
        viewBox={`0 0 ${width} ${height}`}
        width="100%"
        role="img"
        aria-label={`${title ?? "Matrix"}. ${rows.length} by ${columns.length} grid of `
          + `${valueLabel} values. A table of the same values follows.`}
      >
        <defs>
          <clipPath id={clipId}>
            <rect x={0} y={0} width={columns.length * cellSize}
                  height={rows.length * cellSize} />
          </clipPath>
        </defs>

        {/* Column headers, rotated so long names fit without a wide chart. */}
        <g transform={`translate(${M.left + gutter},${M.top + headerHeight})`}>
          {columns.map((column, c) => (
            <text
              key={column}
              className="chart-tick"
              transform={`translate(${c * cellSize + cellSize / 2},-6) rotate(-45)`}
              textAnchor="start"
            >
              {column.length > 22 ? `${column.slice(0, 20)}…` : column}
            </text>
          ))}
        </g>

        <g transform={`translate(${M.left + gutter},${M.top + headerHeight})`}>
          <g clipPath={`url(#${clipId})`}>
            {rows.map((row, r) =>
              columns.map((column, c) => {
                const cell = index.get(`${row}\u0000${column}`)
                  ?? { row, column, value: null };
                const isDiagonal = diagonalNeutral && row === column;
                return (
                  // Keyed by its coordinates, so a reorder moves the cell rather
                  // than repainting the grid (Part D2).
                  <rect
                    key={`${row}\u0000${column}`}
                    className="chart-cell"
                    x={c * cellSize}
                    y={r * cellSize}
                    width={cellSize - 1}
                    height={cellSize - 1}
                    // P5's signature: a diagonal reveal, capped so a large matrix
                    // does not keep the reader waiting.
                    style={{
                      fill: isDiagonal ? "var(--n-200)" : fillFor(cell.value, symmetricAt, scaleMode),
                      opacity: hover.emphasis(`${row}\u0000${column}`),
                      animationDelay: `${Math.min((r + c) * 4, 400)}ms`,
                    }}
                    {...hover.markProps(`${row}\u0000${column}`)}
                  >
                    <title>
                      {isDiagonal
                        ? `${row} with itself`
                        : `${row} × ${column}: ${cell.value === null
                            ? "not computed" : cell.value.toFixed(3)}`}
                    </title>
                  </rect>
                );
              }),
            )}
          </g>

          {/* Row labels. */}
          {rows.map((row, r) => (
            <text key={row} className="chart-tick" x={-8}
                  y={r * cellSize + cellSize / 2 + 4} textAnchor="end">
              {row.length > 26 ? `${row.slice(0, 24)}…` : row}
            </text>
          ))}
        </g>
      </svg>

      <div className="matrix-key">
        {/* The scale, stated. A heatmap without a key is decoration. */}
        {scaleMode === "diverging" ? (
          <>
            <span className="matrix-swatch"
                  style={{ background: fillFor(-symmetricAt, symmetricAt, scaleMode) }} />
            <span>−{symmetricAt}</span>
            <span className="matrix-swatch"
                  style={{ background: fillFor(0, symmetricAt, scaleMode) }} />
            <span>0 — no relationship</span>
            <span className="matrix-swatch"
                  style={{ background: fillFor(symmetricAt, symmetricAt, scaleMode) }} />
            <span>+{symmetricAt}</span>
          </>
        ) : (
          <>
            <span className="matrix-swatch"
                  style={{ background: fillFor(0, symmetricAt, scaleMode) }} />
            <span>0</span>
            <span className="matrix-swatch"
                  style={{ background: fillFor(symmetricAt, symmetricAt, scaleMode) }} />
            <span>{symmetricAt} {valueLabel}</span>
          </>
        )}
        {hovered && hovered.value !== null && (
          <span className="matrix-readout numeric">
            {hovered.row} × {hovered.column}: {hovered.value.toFixed(3)}
          </span>
        )}
      </div>

      {caption && <figcaption className="chart-caption">{caption}</figcaption>}

      <ChartTable
        columns={tableColumns}
        rows={tableRows}
        label={title ?? `${rows.length} by ${columns.length} grid of ${valueLabel} values`}
        highlightId={hover.hovered}
        onHighlight={hover.setHovered}
      />

      <ChartTooltip
        pointer={hover.pointer}
        title={hovered ? `${hovered.row} × ${hovered.column}` : undefined}
        rows={hovered ? [{ label: valueLabel, value: readable(hovered.value) }] : []}
      />
    </figure>
  );
}
