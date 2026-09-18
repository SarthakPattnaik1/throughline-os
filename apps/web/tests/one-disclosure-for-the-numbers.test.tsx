/**
 * The numbers behind a figure open the same way everywhere (D216).
 *
 * Figures, graphs and the spec curve wrote their counts into a label styled
 * by a block of its own (`.kg-table`), while every other disclosure was a
 * `details.fold`. They are one idiom now — and the count stays *text*, because
 * "10 of 60 rows" says how complete a table is, and a count drawn by CSS
 * cannot be found with find-in-page or copied.
 */

import { readFileSync } from "node:fs";
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ChartTable } from "@/components/charts/ChartTable";

const SOURCES = ["components/figures.tsx", "components/graphview.tsx",
                 "components/notegraph.tsx", "components/speccurve.tsx",
                 "components/charts/ChartTable.tsx"];

describe("the numbers behind a figure", () => {
  it("are a fold in every place they appear", () => {
    for (const file of SOURCES) {
      const source = readFileSync(file, "utf8");
      const tables = [...source.matchAll(/<details className="([^"]*)"/g)]
        .map((m) => m[1]).filter((c) => /kg-table|chart-table/.test(c));
      expect(tables.length, file).toBeGreaterThan(0);
      for (const cls of tables) expect(cls.split(" "), file).toContain("fold");
    }
  });

  it("state their count as text a reader can find and copy", () => {
    const rows = Array.from({ length: 60 }, (_, i) => ({ id: `r${i}`, cells: { v: i } }));
    const { container } = render(
      <ChartTable label="Values" columns={[{ key: "v", header: "Value", numeric: true }]}
                  rows={rows as never} maxRows={10} />);
    const details = container.querySelector("details")!;
    expect(details.classList.contains("fold")).toBe(true);
    const summary = details.querySelector("summary")!;
    expect(summary.textContent).toMatch(/10 of 60 rows/);
    expect(summary.hasAttribute("data-count")).toBe(false);
  });
});
