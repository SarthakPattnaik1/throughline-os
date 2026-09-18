/**
 * Rendering a figure through Blender, from inside Throughline.
 *
 * Blender's renderer was written, documented and tested — and called by
 * nothing but its own tests. Settings found Blender, named its version and
 * promised "a physically-based render for publication"; there was nowhere to
 * ask for one. This panel is where.
 *
 * What these tests hold: it is offered only where it can work, it says why
 * when it cannot, it watches a render rather than holding a request open, and
 * the picture it shows is labelled as a render — never as the export, which
 * is reproducible byte for byte and a render is not.
 */

import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BlenderRender, type BlenderState } from "@/components/publish";
import { api } from "@/lib/api";

const BASE: BlenderState = {
  visual_id: "vis_1", is_surface: true, available: true, version: "5.2.1",
  withheld: "", install: "", render: null, run: null,
};

const RENDER: NonNullable<BlenderState["render"]> = {
  render_id: "vren_1", renderer_version: "5.2.1", deterministic: false,
  bytes: 48213, created_at: "2026-09-11T10:00:00+00:00", stale: false,
  note: "Rendered with Blender 5.2.1. A render varies with the version, the "
    + "build and the machine, so it is kept beside the reproducible export, "
    + "never in place of it.",
};

/** Answer successive state requests in order; the last one repeats. */
function states(...sequence: BlenderState[]) {
  let call = 0;
  return vi.spyOn(api, "get").mockImplementation(async () =>
    sequence[Math.min(call++, sequence.length - 1)] as never);
}

beforeEach(() => {
  vi.restoreAllMocks();
  // happy-dom has no object URLs; the panel only needs one to exist.
  Object.assign(URL, {
    createObjectURL: vi.fn(() => "blob:render"),
    revokeObjectURL: vi.fn(),
  });
  vi.spyOn(api, "getForBytes").mockResolvedValue(new Uint8Array([137, 80]) as never);
});

afterEach(() => { vi.useRealTimers(); });

describe("where it is offered", () => {
  it("offers the render on a surface when Blender is on this machine", async () => {
    states(BASE);
    render(<BlenderRender visualId="vis_1" />);

    expect(await screen.findByRole("button", { name: "Render with Blender" }))
      .toBeVisible();
    expect(screen.getByText(/Blender 5\.2\.1 on this machine/)).toBeVisible();
  });

  it("says what is withheld and how to get it, and offers no button", async () => {
    states({ ...BASE, available: false, version: null,
             withheld: "Figures cannot be rendered through Blender.",
             install: "Install Blender to render figures through it." });
    render(<BlenderRender visualId="vis_1" />);

    expect(await screen.findByText(/cannot be rendered through Blender/))
      .toBeVisible();
    expect(screen.getByText(/Install Blender/)).toBeVisible();
    expect(screen.queryByRole("button", { name: /Render/ })).toBeNull();
  });

  it("is not offered on a figure with no third axis", async () => {
    const get = states({ ...BASE, is_surface: false });
    render(<BlenderRender visualId="vis_1" />);

    await waitFor(() => expect(get).toHaveBeenCalled());
    expect(screen.queryByText("Render with Blender")).toBeNull();
  });
});

describe("a render, from the click to the picture", () => {
  it("starts it, watches it, and labels the picture as a render", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    states(
      BASE,
      { ...BASE, run: { run_id: "wfr_1", state: "queued", error: null } },
      { ...BASE, run: { run_id: "wfr_1", state: "running", error: null } },
      { ...BASE, render: RENDER,
        run: { run_id: "wfr_1", state: "completed", error: null } },
    );
    const post = vi.spyOn(api, "post").mockResolvedValue(
      { run_id: "wfr_1", state: "queued", reused: false } as never);
    render(<BlenderRender visualId="vis_1" />);

    fireEvent.click(await screen.findByRole("button", { name: "Render with Blender" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith(
      "/api/visuals/vis_1/blender-render?style=figure&ground=light"));
    // Watched, not awaited: the request came back at once and the button says
    // the work is still going.
    expect(await screen.findByRole("button", { name: "Rendering in Blender…" }))
      .toBeDisabled();

    await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
    await act(async () => { await vi.advanceTimersByTimeAsync(3100); });

    const picture = await screen.findByAltText(
      "The fitted surface, rendered through Blender");
    expect(picture.getAttribute("src")).toBe("blob:render");
    expect(screen.getByText(/never in place of it/)).toBeVisible();
    expect(screen.getByRole("button", { name: "Render again" })).toBeEnabled();
  });

  it("says why a render failed, in words", async () => {
    states({ ...BASE, run: { run_id: "wfr_1", state: "failed",
                             error: "Blender ran and produced no image." } });
    render(<BlenderRender visualId="vis_1" />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Blender ran and produced no image.");
  });

  it("picks up a render that was already running when the panel opened", async () => {
    states({ ...BASE, run: { run_id: "wfr_1", state: "running", error: null } });
    render(<BlenderRender visualId="vis_1" />);

    expect(await screen.findByRole("button", { name: "Rendering in Blender…" }))
      .toBeDisabled();
  });

  it("states what the colours stand for, in the figure's own numbers", async () => {
    // A colour ramp with no numbers is decoration that looks like data.
    states({ ...BASE, render: RENDER,
             colour_scale: { low: 12.5, high: 48.25, label: "yield",
                             text: "Colour is the fitted yield: dark purple is 12.50, "
                                   + "the lowest fitted value, and yellow 48.25, the highest." },
             run: { run_id: "wfr_1", state: "completed", error: null } });
    render(<BlenderRender visualId="vis_1" />);
    expect(await screen.findByText(/dark purple is 12\.50, the lowest fitted value/))
      .toBeVisible();
  });

  it("says when a render is of an earlier version of the figure", async () => {
    // A picture of a figure that no longer exists in that form must not be
    // shown as current.
    states({ ...BASE, render: { ...RENDER, stale: true },
             run: { run_id: "wfr_1", state: "completed", error: null } });
    render(<BlenderRender visualId="vis_1" />);

    expect(await screen.findByText(/render of an earlier version of the figure/))
      .toBeVisible();
  });
});
