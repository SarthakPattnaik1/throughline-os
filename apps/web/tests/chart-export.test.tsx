/**
 * A spatial chart can be saved (§75).
 *
 * Two-dimensional figures could already be written out as SVG. **No
 * three-dimensional one could be saved at all** — every spatial chart is a
 * canvas and nothing offered `toBlob` — so the last step of the work this
 * product exists for, putting the figure in the paper, ended at a screenshot
 * of somebody's own screen.
 *
 * The tests worth having here are about the two ways an export lies: a JPEG
 * that encodes transparency as black, and a video button that produces a file
 * the reader's browser cannot play.
 */

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { createRef } from "react";
import { ChartExport } from "@/components/charts/ChartExport";
import { FORMATS, canRecord, recordingFormat } from "@/lib/charts/export";

describe("what the formats promise", () => {
  it("says what each one costs, on the control that chooses it", () => {
    /**
     * A chart is thin lines, small text and flat fills — where JPEG's blocks
     * show most. It is offered because it is asked for, and the cost is said
     * rather than discovered in a printed figure.
     */
    expect(FORMATS.png.note).toMatch(/lossless/i);
    expect(FORMATS.jpeg.note).toMatch(/transparency|thin/i);
    expect(FORMATS.png.mime).toBe("image/png");
    expect(FORMATS.jpeg.mime).toBe("image/jpeg");
  });
});

describe("the control offers only what the browser can do", () => {
  it("offers both still formats", () => {
    render(<ChartExport canvasRef={createRef<HTMLCanvasElement>()} name="Globe" />);
    expect(screen.getByRole("button", { name: "PNG" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "JPEG" })).toBeTruthy();
  });

  it("does not offer a recording of a chart that cannot turn", () => {
    /** A still of a fixed chart is the whole of what there is to save; an
     *  orbit button on it would record six seconds of nothing moving. */
    render(<ChartExport canvasRef={createRef<HTMLCanvasElement>()} name="Table" />);
    expect(screen.queryByRole("button", { name: /orbit/i })).toBeNull();
  });

  it("does not offer a recording this browser cannot make", () => {
    /**
     * The failure this guards is a button that yields an unplayable file.
     * Safari records H.264 in MP4 while Chrome and Firefox record VP8/VP9 in
     * WebM, so the container is asked for rather than assumed — and where
     * nothing is supported, a still image is still correct.
     */
    expect(canRecord()).toBe(false);   // no MediaRecorder in this environment
    expect(recordingFormat()).toBeNull();

    render(
      <ChartExport canvasRef={createRef<HTMLCanvasElement>()} name="Globe"
                   rotate={() => {}} />);
    expect(screen.queryByRole("button", { name: /orbit/i })).toBeNull();
  });
});

describe("the control is really rendered, not merely mentioned", () => {
  it("appears when a real chart mounts", async () => {
    /**
     * The behavioural half, and it exists because the source check below is
     * not enough on its own: wrapping the element in `{false && …}` leaves the
     * string in the file and passes it. That is the same hole as a hook that
     * is called and whose result is thrown away — found once already in
     * `spatial-charts-answer-keys`, and repeated here by me.
     *
     * One chart is mounted rather than seven: this proves the control renders
     * at all, and the source scan below proves every chart carries it. Neither
     * is sufficient alone.
     */
    const { Network3D } = await import("@/components/charts/Network3D");
    render(<Network3D graph={{
      nodes: [{ id: "a", label: "A" }, { id: "b", label: "B" }],
      edges: [{ source: "a", target: "b" }],
    }} />);

    expect(screen.getByRole("button", { name: "PNG" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "JPEG" })).toBeTruthy();
  });
});

describe("every rotatable chart offers it", () => {
  it("is reachable from all of them, not just the one it was written for", async () => {
    /**
     * `spatial-charts-answer-keys` makes the same argument for the keyboard:
     * a capability wired into one chart and forgotten in six is the
     * built-and-unreachable defect this codebase keeps naming.
     */
    const { readFileSync, readdirSync } = await import("node:fs");
    const { join } = await import("node:path");
    const directory = join(__dirname, "..", "components", "charts");

    const spatial = readdirSync(directory)
      .filter((file) => /3D\.tsx$|VoxelVolume\.tsx$/.test(file));
    expect(spatial.length).toBeGreaterThanOrEqual(6);

    const missing = spatial.filter((file) =>
      !readFileSync(join(directory, file), "utf8").includes("<ChartExport"));
    expect(missing, `cannot be saved: ${missing.join(", ")}`).toEqual([]);
  });
});
