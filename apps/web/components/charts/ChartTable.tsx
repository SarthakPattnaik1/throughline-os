"use client";

/**
 * Part P — the same figure, as numbers.
 *
 * A chart with `role="img"` and an `aria-label` announces what it *is*. It
 * does not let anyone read what it *says*. A screen-reader user, anyone with
 * low vision, and anyone who simply wants the value rather than the position
 * of a mark all need the underlying rows, and every one of the thirteen
 * primitives had a comment promising a table while none of them rendered one.
 *
 * Shared rather than written per chart, because thirteen hand-rolled tables
 * would drift in exactly the way the primitives were built to prevent. Each
 * chart supplies the rows it already has; the presentation, the truncation
 * rule and the disclosure behaviour are decided once, here.
 *
 * **Truncation is disclosed, never silent.** A binned figure can stand behind
 * five thousand observations, and a five-thousand-row table in the DOM is its
 * own accessibility problem. So a cap exists — and when it bites, the summary
 * says how many rows are shown out of how many exist. A table that quietly
 * shows the first two hundred rows is a table that lies about the data.
 */

import { useId } from "react";

export type ChartTableColumn = {
  key: string;
  header: string;
  /** Right-aligned and tabular-figured. Set for anything read as a quantity. */
  numeric?: boolean;
};

export type ChartTableRow = Record<string, string | number | null | undefined>;

export type ChartTableProps = {
  columns: ChartTableColumn[];
  rows: ChartTableRow[];
  /** What the figure shows, used in the disclosure and the caption. */
  label: string;
  /** Anything the numbers alone would not tell a reader. */
  note?: string;
  /** Rows beyond this are not rendered, and the summary says so. */
  maxRows?: number;
  /** Total behind the figure, when the chart aggregated before it got here. */
  totalRows?: number;
  /** Row id currently emphasised in the chart, so the table can match it. */
  highlightId?: string | null;
  /** Called as the pointer moves over a row, so the chart can match it back. */
  onHighlight?: (id: string | null) => void;
};

const DEFAULT_MAX_ROWS = 200;

/** Nulls become a dash rather than "null", and floats stop at 4 significant. */
function cell(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return "—";
    if (Number.isInteger(value)) return value.toLocaleString();
    return Number(value.toPrecision(4)).toLocaleString();
  }
  return String(value);
}

export function ChartTable({
  columns, rows, label, note, maxRows = DEFAULT_MAX_ROWS, totalRows,
  highlightId = null, onHighlight,
}: ChartTableProps) {
  const captionId = useId();
  const total = totalRows ?? rows.length;
  const shown = rows.slice(0, maxRows);
  const truncated = total > shown.length;

  // The count is text, not a CSS `attr()`: "10 of 60 rows" is a fact about
  // how complete the table is, and generated content can be neither found
  // with find-in-page nor copied. Styled as every fold's count is (D216).
  const count = truncated
    ? `${shown.length.toLocaleString()} of ${total.toLocaleString()} rows`
    : `${total.toLocaleString()} ${total === 1 ? "row" : "rows"}`;

  return (
    <details className="fold chart-table">
      <summary>
        The numbers behind this figure<span className="fold-count">· {count}</span>
      </summary>
      <table aria-describedby={captionId}>
        <caption id={captionId} className="sr-only">
          {label}
          {truncated
            ? `. Showing the first ${shown.length} of ${total} rows.`
            : ""}
          {note ? ` ${note}` : ""}
        </caption>
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column.key}
                  scope="col"
                  style={column.numeric ? { textAlign: "right" } : undefined}>
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {shown.map((row, index) => {
            const id = String(row.id ?? index);
            // The table and the figure share one highlight, in both
            // directions: hovering a mark lights its row, and hovering a row
            // lights its mark. That is what turns the table from an appendix
            // into an index of the picture beside it.
            const lit = highlightId !== null && highlightId === id;
            return (
            <tr key={id}
                className={lit ? "is-highlighted" : undefined}
                onMouseEnter={onHighlight ? () => onHighlight(id) : undefined}
                onMouseLeave={onHighlight ? () => onHighlight(null) : undefined}>
              {columns.map((column, columnIndex) => {
                const text = cell(row[column.key]);
                // The first column names the row, so it is a header for it —
                // which is what lets a screen reader say "Denmark, 24.1"
                // instead of reading a wall of unattached numbers.
                return columnIndex === 0 ? (
                  <th key={column.key} scope="row">{text}</th>
                ) : (
                  <td key={column.key}
                      className={column.numeric ? "numeric" : undefined}
                      style={column.numeric ? { textAlign: "right" } : undefined}>
                    {text}
                  </td>
                );
              })}
            </tr>
            );
          })}
        </tbody>
      </table>
      {truncated && (
        <p className="note">
          {(total - shown.length).toLocaleString()} further rows are not shown.
          Export the figure&rsquo;s data to read all of them.
        </p>
      )}
      {note && <p className="note">{note}</p>}
    </details>
  );
}
