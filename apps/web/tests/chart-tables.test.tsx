/**
 * Part P — every primitive offers its numbers as a table, and the keys that
 * build those numbers are unambiguous.
 *
 * Two separate claims, in one file because the second was found by breaking it
 * while adding the first.
 *
 * The tables: a chart with `role="img"` announces what it is, not what it says.
 * Thirteen primitives carried a comment promising a table and none rendered
 * one, so a screen-reader user could learn that a figure existed and nothing
 * more.
 *
 * The keys: `Matrix` and `SetRegions` join two strings with a NUL to make a map
 * key, because NUL is the one character that cannot appear in a label. Remove
 * it and `"ab" + "c"` collides with `"a" + "bc"` — and `split(NUL)` becomes
 * `split("")`, which splits a string into its characters. Both of those
 * happened, and the whole suite plus `tsc` stayed green, because nothing here
 * used a label that could collide. These tests use labels that can.
 */

import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Cartesian } from "@/components/charts/Cartesian";
import { Interval } from "@/components/charts/Interval";
import { Matrix } from "@/components/charts/Matrix";
import { SetRegions } from "@/components/charts/SetRegions";
import { ChartTable } from "@/components/charts/ChartTable";

/** Body rows only — the header row lives in <thead>. */
function bodyRows(container: HTMLElement): HTMLElement[] {
  const body = container.querySelector("tbody");
  return body ? Array.from(body.querySelectorAll("tr")) : [];
}

describe("the table beside a figure", () => {
  it("renders one row per observation, with the values in it", () => {
    const { container } = render(
      <Cartesian
        mark="point"
        xLabel="Antibiotic consumption"
        yLabel="Resistance prevalence"
        data={[
          { id: "a", x: 10, y: 1.5 },
          { id: "b", x: 20, y: 2.5 },
          { id: "c", x: 30, y: 3.5 },
        ]}
      />,
    );
    const table = container.querySelector("table");
    expect(table, "no table accompanies the figure").not.toBeNull();
    expect(bodyRows(container)).toHaveLength(3);
    // The axis labels name the columns, so the table is readable without the
    // chart — the point of having it.
    expect(within(table!).getByText("Antibiotic consumption")).toBeInTheDocument();
    expect(within(table!).getByText("2.5")).toBeInTheDocument();
  });

  it("keeps the estimate and its interval together", () => {
    const { container } = render(
      <Interval
        xLabel="correlation"
        estimates={[
          { id: "1", label: "consumption vs resistance", estimate: 0.85,
            lo: 0.7, hi: 0.94, significant: true },
          { id: "2", label: "GDP vs resistance", estimate: 0.02,
            lo: -0.2, hi: 0.24, significant: false },
        ]}
      />,
    );
    expect(bodyRows(container)).toHaveLength(2);
    const table = container.querySelector("table")!;
    expect(within(table).getByText("consumption vs resistance")).toBeInTheDocument();
  });

  it("says how many rows it is hiding rather than truncating in silence", () => {
    const rows = Array.from({ length: 60 }, (_, i) => ({ id: String(i), n: i }));
    const { container } = render(
      <ChartTable
        columns={[{ key: "id", header: "id" }, { key: "n", header: "n", numeric: true }]}
        rows={rows}
        label="Sixty rows"
        maxRows={10}
      />,
    );
    expect(bodyRows(container)).toHaveLength(10);
    // Said twice on purpose: once in the disclosure a sighted reader clicks,
    // once in the table's own caption for a screen reader that never sees it.
    expect(container.querySelector("summary")!.textContent).toMatch(/10 of 60 rows/);
    expect(container.querySelector("caption")!.textContent)
      .toMatch(/first 10 of 60 rows/);
    expect(screen.getByText(/50 further rows are not shown/)).toBeInTheDocument();
  });

  it("reports a total larger than the rows it was given", () => {
    // A binned figure aggregates 5,000 observations into a few hundred cells.
    // The table must describe the observations, not just the cells.
    const { container } = render(
      <ChartTable
        columns={[{ key: "id", header: "cell" }]}
        rows={[{ id: "one" }, { id: "two" }]}
        label="Binned"
        totalRows={5000}
      />,
    );
    expect(bodyRows(container)).toHaveLength(2);
    expect(screen.getByText(/2 of 5,000 rows/)).toBeInTheDocument();
  });

  it("shows a missing value as a dash, not as the word null", () => {
    const { container } = render(
      <ChartTable
        columns={[{ key: "id", header: "id" },
                  { key: "v", header: "value", numeric: true }]}
        rows={[{ id: "a", v: null }]}
        label="One missing value"
      />,
    );
    expect(container.textContent).not.toContain("null");
    expect(within(container.querySelector("tbody")!).getByText("—")).toBeInTheDocument();
  });
});

describe("keys built from two labels", () => {
  it("does not confuse two cells whose labels concatenate the same way", () => {
    // "ab"+"c" and "a"+"bc" both flatten to "abc" without a separator, so one
    // cell silently takes the other's value.
    const { container } = render(
      <Matrix
        rows={["ab", "a"]}
        columns={["c", "bc"]}
        cells={[
          { row: "ab", column: "c", value: 0.11 },
          { row: "ab", column: "bc", value: 0.22 },
          { row: "a", column: "c", value: 0.33 },
          { row: "a", column: "bc", value: 0.44 },
        ]}
        valueLabel="correlation"
      />,
    );
    // Assert on the grid, not on the table: the table is built straight from
    // `cells` and renders all four values whether or not the lookup is sound.
    // The collision only shows up where a cell is fetched *by* its key — which
    // is why an earlier version of this test passed against the broken code.
    const titles = Array.from(container.querySelectorAll("svg title"))
      .map((node) => node.textContent?.replace(/\s+/g, " ").trim());

    expect(titles).toContain("ab × c: 0.110");
    expect(titles).toContain("ab × bc: 0.220");
    expect(titles).toContain("a × c: 0.330");
    expect(titles).toContain("a × bc: 0.440");
    // Without the separator, "ab"+"c" and "a"+"bc" are the same key, so the
    // first cell reports the last cell's value.
    expect(titles).not.toContain("ab × c: 0.440");
    expect(bodyRows(container)).toHaveLength(4);
  });

  it("does not suppress same-named contingency categories as a diagonal", () => {
    const { container } = render(
      <Matrix
        rows={["yes", "no"]}
        columns={["yes", "no"]}
        cells={[
          { row: "yes", column: "yes", value: 12 },
          { row: "yes", column: "no", value: 3 },
          { row: "no", column: "yes", value: 4 },
          { row: "no", column: "no", value: 9 },
        ]}
        valueLabel="count"
        symmetricAt={12}
        scaleMode="sequential"
        diagonalNeutral={false}
      />,
    );

    const titles = Array.from(container.querySelectorAll("svg title"))
      .map((node) => node.textContent?.replace(/\s+/g, " ").trim());
    expect(titles).toContain("yes × yes: 12.000");
    expect(titles).toContain("no × no: 9.000");
    expect(container.textContent).not.toContain("no relationship");
    expect(container.textContent).not.toContain("−12");
  });

  it("decodes a set combination back to the sets it came from", () => {
    // `split("")` would turn a combination key into single characters, so a
    // two-set intersection would report a nonsense membership.
    const { container } = render(
      <SetRegions
        sets={[{ id: "screened", label: "Screened" },
               { id: "included", label: "Included" }]}
        members={[
          { id: "m1", label: "Karim 2019", sets: ["screened"] },
          { id: "m2", label: "Ali 2021", sets: ["screened", "included"] },
          { id: "m3", label: "Chen 2020", sets: ["screened", "included"] },
        ]}
        itemLabel="papers"
      />,
    );
    const table = container.querySelector("table")!;
    // The two-set combination is named by both of its sets, not by fragments
    // of their ids.
    expect(within(table).getByText(/Screened \+ Included/)).toBeInTheDocument();
    expect(container.textContent).not.toMatch(/\bs \+ c\b/);
  });
});
