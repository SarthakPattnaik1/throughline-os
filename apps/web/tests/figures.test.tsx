/**
 * The Figures screen.
 *
 * It listed `connections` and kept the ones carrying an `analysis_run_id` —
 * a list of what discovery produced — and read `connection.left_variable` for
 * its axes. The routes behind the figure (`/visual-recommendation`, `/points`)
 * are keyed on the *run* and never needed a connection at all, so the only
 * thing the connection supplied was the axis names, which the run's own
 * recommendation already carries.
 *
 * The consequence was that an analysis a researcher specified could not be
 * drawn, and the empty state told them to run discovery as though that were
 * the only way to produce something plottable.
 *
 * The screen had no test file. These start with the parts that decide what a
 * figure is *of*, because an axis labelled from the wrong place is a figure
 * that disagrees with the analysis it came from.
 */

import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { Figures, axisFields, axisLabel, figureName } from "@/components/figures";
import type { AnalysisRunRow } from "@/lib/api";
import { api } from "@/lib/api";

function run(over: Partial<AnalysisRunRow> = {}): AnalysisRunRow {
  return {
    id: "arun_1", status: "completed", error: null,
    created_at: "2026-01-01T00:00:00Z", origin: "discovery",
    method: "pearson_correlation", variables: { x: "consumption", y: "resistance" },
    research_question: "", fork_reason: "", forked_from_run_id: null,
    left_variable: "consumption", right_variable: "resistance",
    estimate: 0.81, estimate_name: "r", p_value: 0.001, sample_size: 120, ...over,
  };
}

const RECOMMENDATION = {
  visual_type: "scatter",
  reason: "Two continuous variables with a linear relationship.",
  caption: "Consumption against resistance.",
  spec: {
    x: { field: "consumption", label: "Consumption (DDD)" },
    y: { field: "resistance", label: "Resistance (%)" },
  },
};

const POINTS = {
  x: [1, 2, 3], y: [2, 4, 6], statistics: { r: 0.81 }, sample_size: 3,
};

const state = <T,>(data: T) =>
  ({ data, error: null, loading: false, reload: () => {}, setData: () => {} });

function serve(over: Record<string, unknown> = {}) {
  return vi.spyOn(api, "get").mockImplementation(async (path: string) => {
    for (const [fragment, value] of Object.entries(over)) {
      if (path.includes(fragment)) return value as never;
    }
    if (path.includes("/visual-recommendation")) return RECOMMENDATION as never;
    if (path.includes("/points")) return POINTS as never;
    if (path.includes("/variables")) return { labels: {} } as never;
    if (path.includes("/correlation-matrix")) {
      return { cells: [], variables: [], note: "" } as never;
    }
    if (path.includes("/estimates")) return { estimates: [], note: "" } as never;
    // The saved-figures list sits under the live chart and reads its own
    // endpoint. A bare object here takes the whole screen down, which is how
    // this fixture first found out it was rendered at all.
    return [] as never;
  });
}

async function openOneRelationship(runs: AnalysisRunRow[]) {
  render(<Figures projectId="prj_1" runs={state(runs)} />);
  await userEvent.click(await screen.findByRole("button",
    { name: /One relationship/ }));
}

beforeEach(() => { vi.restoreAllMocks(); });

// ---------------------------------------------------------------------------
// What a figure is of
// ---------------------------------------------------------------------------

describe("the axes", () => {
  it("takes the fields from the run's own recommendation", () => {
    /*
     * Where they have always lived. Reading them off a connection is what tied
     * this screen to discovery.
     */
    expect(axisFields(RECOMMENDATION as never, run()))
      .toEqual({ x: "consumption", y: "resistance" });
  });

  it("still names the axes of a run that belongs to no connection", () => {
    const specified = run({
      origin: "specified", left_variable: null, right_variable: null });
    expect(axisFields(RECOMMENDATION as never, specified))
      .toEqual({ x: "consumption", y: "resistance" });
  });

  it("falls back to the run's own variables when the spec names no field", () => {
    const bare = { ...RECOMMENDATION, spec: {} };
    const specified = run({
      left_variable: null, right_variable: null,
      variables: { outcome: "resistance", predictors: ["consumption"] } });

    expect(axisFields(bare as never, specified))
      .toEqual({ x: "resistance", y: "consumption" });
  });

  it("prefers the name a human approved over the figure's own label", () => {
    /*
     * A figure that disagreed with the rest of the project about what a
     * variable is called would be the one that goes into the paper.
     */
    expect(axisLabel("consumption", { consumption: "Antibiotic use" },
                     { label: "Consumption", unit: "DDD" }))
      .toBe("Antibiotic use (DDD)");
  });

  it("uses the figure's label when the project has approved no name", () => {
    expect(axisLabel("consumption", {}, { label: "Consumption", unit: "DDD" }))
      .toBe("Consumption (DDD)");
  });


  it("does not print a unit twice when a label already carries it", () => {
    expect(axisLabel("consumption", {}, { label: "Consumption (DDD)", unit: "DDD" }))
      .toBe("Consumption (DDD)");
  });

  it("falls back to the raw column rather than showing nothing", () => {
    expect(axisLabel("consumption", {}, undefined)).toBe("consumption");
    expect(axisLabel("consumption", {}, { label: "" })).toBe("consumption");
  });
});

describe("what a run is called in the picker", () => {
  it("names a swept run by its pair, in the project's words", () => {
    expect(figureName(run(), { consumption: "Antibiotic use" }))
      .toBe("Antibiotic use × resistance");
  });

  it("names a specified run by the columns it used", () => {
    expect(figureName(run({
      left_variable: null, right_variable: null,
      variables: { x: "consumption", y: "resistance" } }),
      { consumption: "Antibiotic use" }))
      .toBe("Antibiotic use · resistance");
  });
});

// ---------------------------------------------------------------------------
// Which runs can be drawn
// ---------------------------------------------------------------------------

describe("the picker", () => {
  it("offers a run that belongs to no connection", async () => {
    // The defect. A specified analysis could not be drawn at all.
    serve();
    await openOneRelationship([run({
      id: "arun_2", origin: "specified",
      left_variable: null, right_variable: null })]);

    expect(await screen.findByRole("button", { name: /consumption · resistance/ }))
      .toBeTruthy();
  });

  it("asks the server for the chosen run's figure", async () => {
    const get = serve();
    await openOneRelationship([run({ id: "arun_9" })]);
    await waitFor(() => expect(get)
      .toHaveBeenCalledWith("/api/analyses/arun_9/visual-recommendation"));
    await waitFor(() => expect(get)
      .toHaveBeenCalledWith("/api/analyses/arun_9/points"));
  });

  it("does not offer a run with no estimate to plot", async () => {
    /*
     * A queued run has no points, and a picker entry that can only ever say
     * "no plottable values" is worse than no entry.
     */
    serve();
    await openOneRelationship([
      run({ id: "arun_1" }),
      run({ id: "arun_2", status: "queued", estimate: null,
            left_variable: "gdp", right_variable: "resistance" }),
    ]);

    expect(await screen.findByRole("button", { name: /consumption × resistance/ }))
      .toBeTruthy();
    expect(screen.queryByRole("button", { name: /gdp × resistance/ })).toBeNull();
  });

  it("draws the run that was picked", async () => {
    const get = serve();
    await openOneRelationship([
      run({ id: "arun_1" }),
      run({ id: "arun_2", left_variable: "gdp", right_variable: "resistance" }),
    ]);
    await userEvent.click(await screen.findByRole("button",
      { name: /gdp × resistance/ }));

    await waitFor(() => expect(get)
      .toHaveBeenCalledWith("/api/analyses/arun_2/visual-recommendation"));
  });
});

describe("sampling disclosure", () => {
  it("tells the reader when the plotted marks are only a sample", async () => {
    serve({
      "/points": {
        ...POINTS,
        sampling: {
          sampled: true,
          rows_total: 2_000_000,
          rows_drawn: 500,
          method: "uniform random without replacement, fixed seed",
        },
      },
    });
    await openOneRelationship([run()]);

    expect(await screen.findByText(/Showing 500 of 2,000,000 rows/)).toBeTruthy();
    expect(screen.getByText(/Statistics shown with the figure come from the full analysis run/))
      .toBeTruthy();
  });
});

describe("a project with nothing to plot", () => {
  it("offers both ways of producing something drawable", async () => {
    // The old hint said "Run discovery", which was the only way there was.
    serve();
    render(<Figures projectId="prj_1" runs={state([])} />);

    expect(await screen.findByText(/Nothing to plot yet/)).toBeTruthy();
    expect(screen.getByText(/specify an analysis yourself/)).toBeTruthy();
  });

  it("says nothing is plottable when every run is still queued", async () => {
    serve();
    render(<Figures projectId="prj_1"
                    runs={state([run({ status: "queued", estimate: null })])} />);

    expect(await screen.findByText(/Nothing to plot yet/)).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// Which export looks like the default (plan §4.12, Slice 3 item 3.2)
// ---------------------------------------------------------------------------

describe("the two ways a figure leaves the screen", () => {
  it("puts the accountable export first, and gives it the weight", async () => {
    /*
     * `publish.tsx:5-28` lists what the DOM save drops: the VISUALIZES edge
     * LAW 5 rests on, the critic that must refuse an unpublishable figure, the
     * formats journals ask for, and a filename that is still legible a month
     * later. For as long as the two controls sat one above the other at the
     * same weight, with "Save this view" on top, the path that loses all four
     * was the one that read as the default — and it produced a file, which is
     * why nobody noticed.
     */
    serve();
    await openOneRelationship([run()]);

    const publish = await screen.findByRole("button",
      { name: /Export for publication/ });
    const save = screen.getByRole("button", { name: /Save this view/ });

    // DOCUMENT_POSITION_FOLLOWING: the save strip comes after the export.
    expect(publish.compareDocumentPosition(save)
      & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(publish.className).toContain("btn-primary");
    expect(save.className).not.toContain("btn-primary");
  });

  it("keeps the honest sentence about what the quick save is, pointing the right way", async () => {
    /*
     * Nothing was removed — the SVG save is still one press. Its sentence had
     * to change one word, because it said "export it below" and the export
     * moved above it; a sentence that names a place is a control that does not
     * do what it says once the place moves (§123).
     */
    serve();
    await openOneRelationship([run()]);

    expect(await screen.findByText(/Quick, and related to nothing/)).toBeTruthy();
    expect(screen.queryByText(/export it below/)).toBeNull();
  });
});
