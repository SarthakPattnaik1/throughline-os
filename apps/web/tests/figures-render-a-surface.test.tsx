/**
 * A surface recommendation draws a surface.
 *
 * `figures.tsx` already carries this lesson for the binned case, in its own
 * words: `MARK_FOR` had no entry for it and fell back to a point mark, "so
 * this used to render a scatter — at the sample size that triggers the
 * recommendation, exactly the overplotted blob the primitive exists to
 * replace. The recommender said one thing and the screen showed another."
 *
 * Adding a spatial type to the recommender without adding it here would repeat
 * that failure exactly, and worse: a fitted response over two predictors would
 * fall through to a two-dimensional scatter of one predictor against the
 * outcome, which is a different claim about the model.
 */

import { describe, expect, it } from "vitest";

import { join } from "node:path";

const SOURCE = join(__dirname, "..", "components", "figures.tsx");

describe("the screen agrees with the recommendation", () => {
  it("has a branch for every recommendation that needs its own primitive", async () => {
    const { readFileSync } = await import("node:fs");
    const source = readFileSync(SOURCE, "utf8");

    // Both are recommendations whose primitive is not a Cartesian mark, so
    // neither can be reached through MARK_FOR.
    expect(source).toContain('recommendation.visual_type === "hexbin"');
    expect(source).toContain('recommendation.visual_type === "surface"');
    expect(source).toContain("<Surface");
  });

  it("does not draw a surface without a grid to draw", async () => {
    /**
     * The guard against the opposite failure: a surface recommendation whose
     * preparation refused — three predictors, or a result with no recorded
     * coefficients — must fall through to something, not render an empty
     * shape that reads as a flat model.
     */
    const { readFileSync } = await import("node:fs");
    const source = readFileSync(SOURCE, "utf8");
    const guard = source.slice(source.indexOf("const surface ="));

    expect(guard.slice(0, 300)).toContain("grid?.length");
    expect(guard.slice(0, 300)).toContain("grid_x");
    expect(guard.slice(0, 300)).toContain("grid_y");
  });

  it("takes the grid from the server rather than computing one", async () => {
    /**
     * The fidelity rule this codebase applies to binning applies here too: a
     * browser that evaluated the fitted response itself could disagree with
     * the analysis that recorded the coefficients.
     */
    const { readFileSync } = await import("node:fs");
    const source = readFileSync(SOURCE, "utf8");
    /*
     * The window is the memo that builds the grid, not the JSX branch. The
     * construction used to sit inline in the branch and was moved into a memo
     * — handing `Surface` a fresh grid object on every render re-ran its whole
     * projection — so reading the branch would now find only `surfaceGrid` and
     * pass whatever the memo did. Both halves are checked below: the memo is
     * where the grid comes from, and the branch must pass that memo through
     * rather than build its own.
     */
    const built = source.slice(source.indexOf("const surfaceGrid ="),
                               source.indexOf("const surfaceObservations ="));
    expect(built).toContain("points.data!.grid_x");
    expect(built).not.toMatch(/Math\.|=>\s*\{[^}]*\*/);

    const surfaceAt = source.indexOf('recommendation.visual_type === "surface"')
    const branch = source.slice(source.indexOf(") : surface ? (", surfaceAt),
                                source.indexOf(") : binned ? (", surfaceAt));
    expect(branch).toContain("grid={surfaceGrid");
    expect(branch).not.toMatch(/Math\.|=>\s*\{[^}]*\*/);

    /*
     * Neither may be rebuilt in the branch. `Surface` keys its projection memo
     * on these two references, so an object or an array constructed here — an
     * inline `{ x: ..., y: ... }`, or a `.map` over the observations — is a
     * new reference on every render and re-runs the projection every time,
     * which is precisely what these two memos were introduced to stop.
     */
    expect(branch).toContain("observations={surfaceObservations");
    expect(branch).not.toMatch(/\.map\(|grid=\{\{/);
  });
});
