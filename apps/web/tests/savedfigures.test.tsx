/**
 * The figures a project has made.
 *
 * Exporting a figure creates a record — the spec, the critic's report, and a
 * lineage edge back to the analysis it draws — and nothing listed those
 * records or opened one. The interface downloaded the file and moved on, so a
 * saved figure was reachable only by somebody who had kept its id. `GET
 * /visuals/{id}` and `PATCH /visuals/{id}` both had no caller, and the table
 * had been indexed on `(project_id, created_at DESC)` since it was written for
 * a query nobody made.
 *
 * The tests turn on what an edit may change. A title and a caption are how a
 * figure reads; its rows and variables are what it claims. The domain refuses
 * the second, and the critic runs again on the first — which is the reason to
 * edit here rather than in an exported file, where a caption that overstates
 * the result is fixed nowhere.
 */

import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { SavedFigures, blocking } from "@/components/savedfigures";
import type { SavedFigure } from "@/components/savedfigures";
import { ApiError, api } from "@/lib/api";

const FIGURE: SavedFigure = {
  id: "vis_1",
  visual_type: "scatter",
  publishable: true,
  created_at: "2026-08-29T10:00:00Z",
  analysis_run_id: "arun_1",
  finding_id: null,
  title: "Consumption against resistance",
  caption: "Each point is one country.",
};

const CLEAN = { publishable: true, critiques: [] };

const BLOCKED = {
  publishable: false,
  critiques: [
    { check: "causal_language", outcome: "violated", severity: "blocking",
      detail: "The caption says 'causes'; this analysis shows an association." },
    { check: "axis_zero", outcome: "passed", severity: "advisory", detail: "" },
  ],
};

function serve(figures: SavedFigure[] = [FIGURE]) {
  vi.spyOn(api, "get").mockResolvedValue(figures as never);
}

beforeEach(() => { vi.restoreAllMocks(); });

describe("the figures made here", () => {
  it("lists them with what they are of", async () => {
    serve();
    render(<SavedFigures projectId="prj_1" />);
    expect(await screen.findByText("Consumption against resistance")).toBeTruthy();
    expect(screen.getByText("Each point is one country.")).toBeTruthy();
  });

  it("says which ones the critic blocked, on the row", async () => {
    /*
     * A blocked figure cannot be rendered, and somebody scanning the list needs
     * to know that before they reach for one — not after they try to use it.
     */
    serve([{ ...FIGURE, publishable: false }]);
    render(<SavedFigures projectId="prj_1" />);
    expect(await screen.findByText(/the critic blocked this one/)).toBeTruthy();
  });

  it("does not accuse a figure the critic passed", async () => {
    serve();
    render(<SavedFigures projectId="prj_1" />);
    await screen.findByText("Consumption against resistance");
    expect(screen.queryByText(/the critic blocked/)).toBeNull();
  });

  it("names an untitled figure by its kind rather than leaving it blank", async () => {
    serve([{ ...FIGURE, title: null, caption: null }]);
    render(<SavedFigures projectId="prj_1" />);
    expect(await screen.findByText("Untitled scatter")).toBeTruthy();
  });

  it("says where figures come from when there are none", async () => {
    serve([]);
    render(<SavedFigures projectId="prj_1" />);
    expect(await screen.findByText(/No figure has been saved yet/)).toBeTruthy();
  });
});

describe("editing the wording", () => {
  async function openEditor() {
    serve();
    render(<SavedFigures projectId="prj_1" />);
    await userEvent.click(await screen.findByRole("button", { name: "Edit wording" }));
  }

  it("sends only the title and the caption", async () => {
    /*
     * What the figure plots is what it claims. The domain refuses a
     * data-bearing change — "a redraw would leave the old statistics on new
     * data" — so the form must not offer one.
     */
    const patch = vi.spyOn(api, "patch").mockResolvedValue(
      { publishable: true, critique: CLEAN } as never);
    await openEditor();

    await userEvent.click(screen.getByRole("button", { name: "Save wording" }));
    await waitFor(() => expect(patch).toHaveBeenCalled());
    expect(patch.mock.calls[0][0]).toBe("/api/visuals/vis_1");
    expect(patch.mock.calls[0][1]).toEqual({
      changes: { title: "Consumption against resistance",
                 caption: "Each point is one country." },
    });
  });

  it("says that the wording is all it can change", async () => {
    await openEditor();
    expect(screen.getByText(/changing that is a new analysis rather than a redraw/))
      .toBeTruthy();
  });

  it("reports what the critic blocked, in its words", async () => {
    /*
     * The reason to edit here rather than in an exported file: a caption that
     * overstates the result is what the critic stops, and a caption fixed in a
     * PNG is fixed nowhere.
     */
    vi.spyOn(api, "patch").mockResolvedValue(
      { publishable: false, critique: BLOCKED } as never);
    await openEditor();
    await userEvent.click(screen.getByRole("button", { name: "Save wording" }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("shows an association");
  });

  it("shows only what blocked it, not every check that ran", async () => {
    // A list mixing a blocking violation with a passing advisory teaches a
    // reader to skim the one that matters.
    expect(blocking(BLOCKED).map((c) => c.check)).toEqual(["causal_language"]);
    expect(blocking(CLEAN)).toEqual([]);
    expect(blocking(null)).toEqual([]);
  });

  it("keeps the editor open while the critic is still refusing", async () => {
    // Closing it would leave the researcher looking at the row they failed to
    // change, with the refusal gone.
    vi.spyOn(api, "patch").mockResolvedValue(
      { publishable: false, critique: BLOCKED } as never);
    await openEditor();
    await userEvent.click(screen.getByRole("button", { name: "Save wording" }));

    await screen.findByRole("alert");
    expect(screen.getByRole("button", { name: "Save wording" })).toBeTruthy();
  });

  it("reports a refused edit in the server's own words", async () => {
    vi.spyOn(api, "patch").mockRejectedValue(new ApiError(
      409, "Changing x changes what the figure shows, not how it looks."));
    await openEditor();
    await userEvent.click(screen.getByRole("button", { name: "Save wording" }));

    expect(await screen.findByText(/changes what the figure shows/)).toBeTruthy();
  });
});
