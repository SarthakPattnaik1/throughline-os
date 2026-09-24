/**
 * The project's analyses.
 *
 * This screen listed `connections` that carried an `analysis_run_id` — a list
 * of what discovery produced, which was the only thing that produced analyses.
 * Now that a researcher can specify one, a run they asked for would be queued,
 * executed, recorded, and never shown.
 *
 * The tests are about that invisibility, and about the distinction a flat list
 * would destroy: a swept run was corrected inside a family of tests and a
 * specified one was not, and the flattened version is the more flattering one.
 */

import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { AnalysisList, Headline, ORIGIN_NOTE, PlainReading, nameOf } from "@/components/analyses";
import type { AnalysisRunRow } from "@/lib/api";
import { ApiError, api } from "@/lib/api";

function run(over: Partial<AnalysisRunRow> = {}): AnalysisRunRow {
  return {
    id: "arun_1", status: "completed", error: null,
    created_at: "2026-01-01T00:00:00Z", origin: "discovery",
    method: "pearson_correlation", variables: { x: "consumption", y: "resistance" },
    research_question: "Does consumption track resistance?", fork_reason: "",
    forked_from_run_id: null, left_variable: "consumption",
    right_variable: "resistance", estimate: 0.81, estimate_name: "r",
    p_value: 0.001, sample_size: 120, ...over,
  };
}

function serve(runs: AnalysisRunRow[]) {
  return vi.spyOn(api, "get").mockImplementation(async (path: string) => {
    if (path.includes("/analyses")) return runs as never;
    // The specify-an-analysis form sits on this screen. Closed, it asks for
    // nothing — which is the point of the assertion below — but a payload
    // missing its `analysis` block must not take the screen down with it.
    return {} as never;
  });
}

beforeEach(() => { vi.restoreAllMocks(); });

describe("which runs appear", () => {
  it("asks for the project's runs rather than deriving them from connections", async () => {
    /*
     * The whole defect. A run that belongs to no connection — anything a
     * researcher specified — was invisible by construction.
     */
    const get = serve([run()]);
    render(<AnalysisList projectId="prj_1" onSelect={() => {}} />);
    await waitFor(() => expect(get)
      .toHaveBeenCalledWith("/api/projects/prj_1/analyses?limit=200"));
    expect(get.mock.calls.some(([p]) => String(p).includes("/connections")))
      .toBe(false);
  });

  it("asks for nothing on behalf of a form nobody has opened", async () => {
    // The specify-an-analysis form sits on this screen. Closed, it is a button,
    // and a button should not cost the capabilities payload and the source list
    // on every visit to a screen that is mostly read.
    const get = serve([run()]);
    render(<AnalysisList projectId="prj_1" onSelect={() => {}} />);
    await screen.findByText(/consumption × resistance/);
    expect(get.mock.calls.map(([p]) => String(p)))
      .toEqual(["/api/projects/prj_1/analyses?limit=200"]);
  });

  it("lists a run that belongs to no connection", async () => {
    serve([run({ id: "arun_2", origin: "specified", left_variable: null,
                 right_variable: null })]);
    render(<AnalysisList projectId="prj_1" onSelect={() => {}} />);
    expect(await screen.findByText(/consumption · resistance/)).toBeTruthy();
  });

  it("opens the run that was clicked", async () => {
    const selected = vi.fn();
    serve([run({ id: "arun_7" })]);
    render(<AnalysisList projectId="prj_1" onSelect={selected} />);
    await userEvent.click(await screen.findByText(/consumption × resistance/));
    expect(selected).toHaveBeenCalledWith("arun_7");
  });
});

describe("where a run came from", () => {
  it("says a swept run was corrected across the whole search", async () => {
    /*
     * The same p-value is worth less in a larger family. A list that showed the
     * estimate and not the origin would let a swept result read as a standalone
     * one, which is the flattering direction.
     */
    serve([run({ origin: "discovery" })]);
    render(<AnalysisList projectId="prj_1" onSelect={() => {}} />);
    expect(await screen.findByText(/corrected across every test in that search/))
      .toBeTruthy();
  });

  it("says a fork is a variant rather than an independent look", async () => {
    serve([run({ origin: "fork", fork_reason: "without the outlier",
                 forked_from_run_id: "arun_0" })]);
    render(<AnalysisList projectId="prj_1" onSelect={() => {}} />);
    expect(await screen.findByText(/not an independent look/)).toBeTruthy();
    expect(screen.getByText(/without the outlier/)).toBeTruthy();
  });

  it("distinguishes the three rather than describing them alike", () => {
    expect(new Set(Object.values(ORIGIN_NOTE)).size).toBe(3);
  });
});

describe("what a row says about a run", () => {
  it("names a swept run by the pair it tested", () => {
    expect(nameOf(run())).toBe("consumption × resistance");
  });

  it("names a specified run by the columns it used", () => {
    // It belongs to no pair, and `arun_8f21…` tells a reader nothing.
    expect(nameOf(run({ origin: "specified", left_variable: null,
                        right_variable: null,
                        variables: { outcome: "resistance",
                                     predictors: ["consumption", "gdp"] } })))
      .toBe("resistance · consumption · gdp");
  });

  it("falls back to the method when a run named no columns", () => {
    expect(nameOf(run({ left_variable: null, right_variable: null,
                        variables: {}, method: "kruskal_wallis" })))
      .toBe("kruskal wallis");
  });

  it("shows the p-value of a method that produces no point estimate", async () => {
    /*
     * Found by looking at a real project: half its runs were ANOVAs, which
     * store `estimate` as null and `estimate_name` as "". Keying the row off
     * the estimate showed a *completed* ANOVA carrying p = 0.81 as a status
     * badge reading "completed", which reads as still working.
     */
    serve([run({ method: "anova", estimate: null, estimate_name: "",
                 p_value: 0.8076 })]);
    render(<AnalysisList projectId="prj_1" onSelect={() => {}} />);

    expect(await screen.findByText(/0\.80/)).toBeTruthy();
    expect(screen.queryByText(/^completed$/)).toBeNull();
  });

  it("prefers the estimate when a method has one", () => {
    const { container } = render(<Headline run={run()} />);
    expect(container.textContent).toContain("r");
    expect(container.textContent).toContain("0.81");
  });

  it("does not label an unnamed estimate with an empty string", () => {
    // `estimate_name` is "" rather than null for a method that does not name
    // its estimate, which `??` would happily render as nothing at all.
    const { container } = render(
      <Headline run={run({ estimate: 2.5, estimate_name: "" })} />);
    expect(container.textContent).toContain("estimate");
  });

  it("shows where a run has got to only when it has no result at all", () => {
    const { container } = render(
      <Headline run={run({ status: "queued", estimate: null, p_value: null })} />);
    expect(container.textContent).toMatch(/queued/);
  });

  it("shows a status rather than an estimate while a run is still queued", async () => {
    // An absent estimate is not a zero one, and a zero estimate is a finding.
    //
    // No p-value either: `list_runs` reads both out of the run's result, which
    // is empty until it finishes, so a queued run carrying a p-value is a
    // shape the server never produces.
    serve([run({ status: "queued", estimate: null, estimate_name: null,
                 p_value: null })]);
    render(<AnalysisList projectId="prj_1" onSelect={() => {}} />);
    await screen.findByText(/consumption × resistance/);
    expect(screen.getByText(/queued/)).toBeTruthy();
  });

  it("shows the estimate of a finished run whatever the status is called", async () => {
    /*
     * `analysis_runs` finishes as "completed" and `discovery_runs` finishes as
     * "complete". A component that tests for one word is wrong about the other
     * and shows a status badge where the result belongs — which is what this
     * one did, for every finished run, until a round trip through the real
     * server said the word out loud.
     */
    serve([run({ status: "completed" })]);
    render(<AnalysisList projectId="prj_1" onSelect={() => {}} />);
    expect(await screen.findByText(/0\.81/)).toBeTruthy();
    expect(screen.queryByText(/^completed$/)).toBeNull();
  });

  it("shows a failed run with what stopped it", async () => {
    /*
     * Dropping failures would make the search look more successful than it was
     * — the same distortion the correction exists to prevent, one level up.
     */
    serve([run({ status: "failed", estimate: null,
                 error: "The dataset version disappeared." })]);
    render(<AnalysisList projectId="prj_1" onSelect={() => {}} />);
    expect(await screen.findByRole("alert"))
      .toHaveTextContent(/dataset version disappeared/);
  });
});

describe("an empty project", () => {
  it("offers both ways of producing an analysis", async () => {
    // The old empty state said "Run discovery to generate them", which was the
    // only way there was.
    serve([]);
    render(<AnalysisList projectId="prj_1" onSelect={() => {}} />);
    expect(await screen.findByText(/No analyses yet/)).toBeTruthy();
    expect(screen.getByText(/or specify one yourself/)).toBeTruthy();
    expect(screen.getByRole("button", { name: /Specify an analysis/ })).toBeTruthy();
  });
});


// ---------------------------------------------------------------------------
// The plain reading
// ---------------------------------------------------------------------------
//
// `GET /analyses/{id}/plain-summary` is keyed on the run, but the only caller
// was `ResultCard`, which needs a connection. The one feature whose job is to
// make a result legible to someone who does not read confidence intervals was
// unreachable for an analysis a researcher specified.

const READING = {
  headline: "Antibiotic use tracks resistance across these countries.",
  what_it_means: "Where more antibiotics are used, more resistance is carried.",
  how_confident: "Strong, and it survived the checks that were run.",
  // The fourth sentence the model is asked for. It was missing from this
  // fixture for as long as it was missing from `PlainSummary` — the type
  // stopped at three, so the field was dropped on arrival and a fixture
  // without it still looked like the real payload.
  what_would_change_it:
    "Data from countries with very different prescribing rules.",
  causal_reading: "This is an observational comparison between countries.",
  design: { description: "Cross-sectional, one year.",
            permits_causal_language: false },
  // Who wrote it. Sent on every reading, and absent from this fixture for as
  // long as the screen ignored it.
  model: "qwen2.5:7b-instruct",
  prompt: "plain_summary v3",
  cached: false,
  operational_summary: "Read the recorded result, its assumption checks and "
    + "its validation report, and restated them in plain language without "
    + "figures.",
};

describe("what a plain reading owes the reader", () => {
  it("says what would change it", async () => {
    // The fourth sentence the model is asked for, and the one the type
    // dropped. It is the reading's own account of how it could be wrong.
    vi.spyOn(api, "get").mockResolvedValue(READING as never);
    render(<PlainReading runId="arun_1" />);

    expect(await screen.findByText("What would change this")).toBeTruthy();
    expect(screen.getByText(/very different prescribing rules/)).toBeTruthy();
  });

  it("names the model and prompt that wrote it", async () => {
    /*
     * Every sentence here is a model's. Two readings of one result can
     * differ — another model, or the same one at a later prompt — and without
     * attribution the difference is a matter of which one was seen last.
     */
    vi.spyOn(api, "get").mockResolvedValue(READING as never);
    render(<PlainReading runId="arun_1" />);

    expect(await screen.findByText(/Written by qwen2\.5:7b-instruct · plain_summary v3/))
      .toBeTruthy();
  });

  it("says when the reading was kept from an earlier call", async () => {
    // The cached branch sends no `operational_summary`, which is why that
    // field is optional in the type.
    const cached = { ...READING, operational_summary: undefined };
    vi.spyOn(api, "get").mockResolvedValue(
      { ...cached, cached: true } as never);
    render(<PlainReading runId="arun_1" />);

    expect(await screen.findByText(/kept from an earlier reading of this result/))
      .toBeTruthy();
  });
});

describe("reading a result in plain words", () => {
  it("asks for the run it was given, with no connection involved", async () => {
    const get = vi.spyOn(api, "get").mockResolvedValue(READING as never);
    render(<PlainReading runId="arun_7" />);
    await waitFor(() => expect(get)
      .toHaveBeenCalledWith("/api/analyses/arun_7/plain-summary"));
  });

  it("shows the reading and what it means", async () => {
    vi.spyOn(api, "get").mockResolvedValue(READING as never);
    render(<PlainReading runId="arun_1" />);
    expect(await screen.findByText(/tracks resistance across these countries/))
      .toBeTruthy();
    expect(screen.getByText(/more resistance is carried/)).toBeTruthy();
  });

  it("says whether the design permits causal language", async () => {
    /*
     * The sentence a summary is most likely to be quoted out of is the causal
     * one, so whether the design supports it is not a detail below the fold.
     */
    vi.spyOn(api, "get").mockResolvedValue(READING as never);
    render(<PlainReading runId="arun_1" />);
    expect(await screen.findByText(/observational comparison/)).toBeTruthy();
    expect(screen.getByText(/does not support causal language/)).toBeTruthy();
  });

  it("does not warn about causal language when the design permits it", async () => {
    vi.spyOn(api, "get").mockResolvedValue({
      ...READING,
      design: { description: "A randomised trial.",
                permits_causal_language: true } } as never);
    render(<PlainReading runId="arun_1" />);
    await screen.findByText(/A randomised trial/);
    expect(screen.queryByText(/does not support causal language/)).toBeNull();
  });

  it("reports a run that cannot be summarised in the server's own words", async () => {
    /*
     * 409 covers two states a researcher can act on — no model configured on
     * this machine, and a run that has not finished. Both are facts, and
     * "That did not work" is not what either of them means.
     */
    vi.spyOn(api, "get").mockRejectedValue(
      new ApiError(409, "No model is configured, so nothing can be written."));
    render(<PlainReading runId="arun_1" />);

    expect(await screen.findByText(/No model is configured/)).toBeTruthy();
    expect(screen.queryByText(/That did not work/)).toBeNull();
  });

  it("still reports a real failure as one", async () => {
    vi.spyOn(api, "get").mockRejectedValue(new ApiError(500, "boom"));
    render(<PlainReading runId="arun_1" />);
    expect(await screen.findByText(/That did not work/)).toBeTruthy();
  });
});
