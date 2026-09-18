/**
 * Several saved figures as one lettered figure (T192).
 *
 * Panels were composed outside Throughline, where the numbers stayed in the
 * caption and nobody saw two measures of one result disagree until review.
 * These pin the order (the order chosen is A, B, C), what can be a panel, and
 * that the disagreements are shown before the file is made.
 */

import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { SavedFigures } from "@/components/savedfigures";
import type { SavedFigure } from "@/components/savedfigures";
import { api } from "@/lib/api";

const base: SavedFigure = {
  id: "vis_1", visual_type: "scatter", publishable: true,
  created_at: "2026-09-18T10:00:00Z", analysis_run_id: "arun_1",
  finding_id: null, title: "Sleep and recall", caption: null,
};
const FIGURES: SavedFigure[] = [
  base,
  { ...base, id: "vis_2", visual_type: "bar", title: "Recall by arm" },
  { ...base, id: "vis_3", visual_type: "surface", title: "Fitted surface" },
  { ...base, id: "vis_4", visual_type: "box", title: "Blocked", publishable: false },
];

const CHECKED = {
  panels: [
    { letter: "A", visual_id: "vis_2", title: "Recall by arm", visual_type: "bar",
      metrics: "mean difference = 6.30 [-0.40, 12.90] (95% CI) · p = 0.030 · n = 120",
      drawable: true },
    { letter: "B", visual_id: "vis_1", title: "Sleep and recall", visual_type: "scatter",
      metrics: "pearson r = 0.61 [0.48, 0.71] (95% CI) · p < 0.001 · n = 120",
      drawable: true },
  ],
  disagreements: ["A: p is below 0.05, but the interval [-0.40, 12.90] includes 0, no effect."],
};

beforeEach(() => { vi.restoreAllMocks(); });

describe("composing a figure from saved ones", () => {
  it("offers a panel only for a figure that can be drawn flat and was not blocked", async () => {
    vi.spyOn(api, "get").mockResolvedValue(FIGURES as never);
    render(<SavedFigures projectId="prj_1" />);
    await screen.findByText("Sleep and recall");
    // Two of the four: the surface has no flat drawing, the blocked one no render.
    expect(screen.getAllByRole("checkbox", { name: /Add as a panel/ })).toHaveLength(2);
  });

  it("letters the panels in the order they were chosen, and shows the disagreement first", async () => {
    vi.spyOn(api, "get").mockResolvedValue(FIGURES as never);
    const post = vi.spyOn(api, "post").mockResolvedValue(CHECKED as never);
    const bytes = vi.spyOn(api, "postForBytes").mockResolvedValue(new Uint8Array([1]));
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    render(<SavedFigures projectId="prj_1" />);
    await screen.findByText("Sleep and recall");

    const [scatter, bar] = screen.getAllByRole("checkbox", { name: /Add as a panel/ });
    await userEvent.click(bar);
    await userEvent.click(scatter);

    await waitFor(() => expect(post).toHaveBeenLastCalledWith(
      "/api/projects/prj_1/figures/compose/check",
      { visual_ids: ["vis_2", "vis_1"] }));
    expect(screen.getByText("Panel A")).toBeTruthy();
    expect(screen.getByText("Panel B")).toBeTruthy();

    // Before any file: each panel's numbers, and where they disagree.
    expect(await screen.findByText(/mean difference = 6.30/)).toBeTruthy();
    expect(screen.getByText(/includes 0, no effect/)).toBeTruthy();

    await userEvent.selectOptions(screen.getByRole("combobox", { name: /Background/ }),
                                  "dark-clear");
    await userEvent.click(screen.getByRole("button", { name: "Download the figure" }));
    await waitFor(() => expect(bytes).toHaveBeenCalled());
    expect(bytes.mock.calls[0][0]).toBe("/api/projects/prj_1/figures/compose");
    expect(bytes.mock.calls[0][1]).toEqual({
      visual_ids: ["vis_2", "vis_1"], format: "pdf", ground: "dark",
      transparent: true, height: null,
    });
  });

  it("removes a panel and re-letters what is left", async () => {
    vi.spyOn(api, "get").mockResolvedValue(FIGURES as never);
    const post = vi.spyOn(api, "post").mockResolvedValue(CHECKED as never);
    render(<SavedFigures projectId="prj_1" />);
    await screen.findByText("Sleep and recall");
    const [scatter, bar] = screen.getAllByRole("checkbox", { name: /Add as a panel/ });
    await userEvent.click(bar);
    await userEvent.click(scatter);
    await userEvent.click(screen.getByRole("checkbox", { name: "Panel A" }));
    await waitFor(() => expect(post).toHaveBeenLastCalledWith(
      "/api/projects/prj_1/figures/compose/check", { visual_ids: ["vis_1"] }));
    expect(screen.getByText("Panel A")).toBeTruthy();
    expect(screen.queryByText("Panel B")).toBeNull();
  });
});
