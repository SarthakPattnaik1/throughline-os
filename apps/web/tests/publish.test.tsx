/**
 * Exporting a figure for publication (LAW 5, §96).
 *
 * The screen could already save an SVG by cloning the live `<svg>` out of the
 * page, which is why the server-side visuals subsystem — four routes, a critic,
 * a publication renderer and a lineage edge — sat with no caller at all. A file
 * came out either way; what the DOM route skipped was everything that makes the
 * file accountable.
 *
 * So these tests are about the parts a DOM export cannot have: that the critic
 * is consulted and believed, that a figure it refuses is not exported anyway,
 * and that the format warning arrives before the download rather than with it.
 */

import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { Critique, PublishFigure, summarise, takesAHeight } from "@/components/publish";
import { ApiError, api } from "@/lib/api";

const clean = {
  visual_id: "vis_1",
  publishable: true,
  exportable: true,
  critique: { publishable: true, critiques: [] as Critique[] },
};

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:x");
  vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
});

describe("the figure becomes something the system can account for", () => {
  it("records the figure against the analysis it came from", async () => {
    // LAW 5: the edge from the analysis run is what lets a figure on a slide
    // resolve back to the dataset underneath. A DOM clone is related to nothing.
    const post = vi.spyOn(api, "post").mockResolvedValue(clean);
    render(<PublishFigure projectId="prj_1" analysisRunId="arun_1" />);

    await userEvent.click(await screen.findByRole("button", { name: /Export for/ }));
    expect(post).toHaveBeenCalledWith("/api/projects/prj_1/visuals",
      expect.objectContaining({ analysis_run_id: "arun_1" }));
  });

  it("shows the figure id, so a download can be traced back", async () => {
    vi.spyOn(api, "post").mockResolvedValue(clean);
    render(<PublishFigure projectId="prj_1" analysisRunId="arun_1" />);
    await userEvent.click(await screen.findByRole("button", { name: /Export for/ }));
    expect(await screen.findByText(/vis_1/)).toBeTruthy();
  });
});

describe("the critic is consulted and believed", () => {
  const refused = {
    visual_id: "vis_2",
    publishable: false,
    exportable: true,
    critique: {
      publishable: false,
      critiques: [{
        check: "truncated_axis", outcome: "violated" as const, severity: "blocking",
        detail: "The y axis starts at 40, which exaggerates the difference.",
      }],
    },
  };

  it("does not offer a download for a figure the critic refused", async () => {
    /*
     * The server refuses to render an unpublishable figure, so a download
     * button here would be one that always fails. The problem is what the
     * researcher needs, and it is fixable.
     */
    vi.spyOn(api, "post").mockResolvedValue(refused);
    render(<PublishFigure projectId="prj_1" analysisRunId="arun_1" />);
    await userEvent.click(await screen.findByRole("button", { name: /Export for/ }));

    expect(await screen.findByText(/has to be fixed before it can be published/))
      .toBeTruthy();
    expect(screen.queryByRole("button", { name: /Download/ })).toBeNull();
  });

  it("says what the problem is, not merely that there is one", async () => {
    vi.spyOn(api, "post").mockResolvedValue(refused);
    render(<PublishFigure projectId="prj_1" analysisRunId="arun_1" />);
    await userEvent.click(await screen.findByRole("button", { name: /Export for/ }));
    expect(await screen.findByText(/exaggerates the difference/)).toBeTruthy();
  });

  it("reports a correction the critic made rather than hiding it", async () => {
    // A figure that was silently altered before export is one the researcher
    // no longer recognises as theirs.
    vi.spyOn(api, "post").mockResolvedValue({
      visual_id: "vis_3", publishable: true, exportable: true,
      critique: { publishable: true, critiques: [{
        check: "causal_language", outcome: "fixed", severity: "blocking",
        detail: "The caption said 'causes'.",
        fix_applied: "Rewritten to 'is associated with'.",
      }] },
    });
    render(<PublishFigure projectId="prj_1" analysisRunId="arun_1" />);
    await userEvent.click(await screen.findByRole("button", { name: /Export for/ }));

    expect(await screen.findByText(/Corrected/)).toBeTruthy();
    expect(screen.getByText(/is associated with/)).toBeTruthy();
  });

  it("keeps only what a researcher has to act on", () => {
    // A list that recites every passing check buries the one line that matters.
    const critiques: Critique[] = [
      { check: "a", outcome: "passed", severity: "advisory", detail: "fine" },
      { check: "b", outcome: "violated", severity: "blocking", detail: "bad" },
      { check: "c", outcome: "warned", severity: "advisory", detail: "hm" },
      { check: "d", outcome: "fixed", severity: "blocking", detail: "was bad" },
    ];
    expect(summarise(critiques).map((c) => c.check)).toEqual(["b", "c", "d"]);
  });
});

describe("the format", () => {
  it("warns about a lossy format before the file arrives, not with it", async () => {
    const post = vi.spyOn(api, "post")
      .mockResolvedValueOnce(clean)
      .mockResolvedValue({ warning: "JPEG is lossy. This figure is line art." });
    const bytes = vi.spyOn(api, "getForBytes")
      .mockResolvedValue(new Uint8Array([1, 2, 3]));
    render(<PublishFigure projectId="prj_1" analysisRunId="arun_1" />);
    await userEvent.click(await screen.findByRole("button", { name: /Export for/ }));

    await userEvent.selectOptions(screen.getByRole("combobox", { name: /Format/ }), "jpeg");
    await userEvent.click(screen.getByRole("button", { name: /Download/ }));

    expect(await screen.findByText(/JPEG is lossy/)).toBeTruthy();
    // Rendered first, then fetched — the warning cannot arrive after the file.
    expect(post.mock.invocationCallOrder[1])
      .toBeLessThan(bytes.mock.invocationCallOrder[0]);
  });

  it("asks for a pixel height only where one means something", () => {
    // The server refuses a height for a vector format rather than ignoring it.
    expect(takesAHeight("pdf")).toBe(false);
    expect(takesAHeight("svg")).toBe(false);
    expect(takesAHeight("eps")).toBe(false);
    expect(takesAHeight("png")).toBe(true);
    expect(takesAHeight("TIFF")).toBe(true);
  });

  it("sends the height for a raster format and omits it for a vector one", async () => {
    vi.spyOn(api, "post")
      .mockResolvedValueOnce(clean)
      .mockResolvedValue({ warning: null });
    const bytes = vi.spyOn(api, "getForBytes")
      .mockResolvedValue(new Uint8Array([1]));
    render(<PublishFigure projectId="prj_1" analysisRunId="arun_1" />);
    await userEvent.click(await screen.findByRole("button", { name: /Export for/ }));

    // PDF is the default, and it is a vector.
    await userEvent.click(screen.getByRole("button", { name: /Download/ }));
    await waitFor(() => expect(bytes).toHaveBeenCalled());
    expect(bytes.mock.calls[0][0]).not.toContain("height");

    await userEvent.selectOptions(screen.getByRole("combobox", { name: /Format/ }), "png");
    await userEvent.click(screen.getByRole("button", { name: /Download/ }));
    await waitFor(() => expect(bytes).toHaveBeenCalledTimes(2));
    expect(bytes.mock.calls[1][0]).toContain("height=1200");
  });

  it("draws the figure on the ground asked for, and offers no transparency a format cannot hold (T191)", async () => {
    vi.spyOn(api, "post")
      .mockResolvedValueOnce(clean)
      .mockResolvedValue({ warning: null });
    const bytes = vi.spyOn(api, "getForBytes")
      .mockResolvedValue(new Uint8Array([1]));
    render(<PublishFigure projectId="prj_1" analysisRunId="arun_1" />);
    await userEvent.click(await screen.findByRole("button", { name: /Export for/ }));

    const background = screen.getByRole("combobox", { name: /Background/ });
    await userEvent.selectOptions(background, "dark-clear");
    await userEvent.click(screen.getByRole("button", { name: /Download/ }));
    await waitFor(() => expect(bytes).toHaveBeenCalled());
    expect(bytes.mock.calls[0][0]).toContain("ground=dark&transparent=true");

    // EPS has no alpha: the transparent grounds go, and the choice falls back
    // to the opaque one with the same ink rather than failing on Download.
    await userEvent.selectOptions(screen.getByRole("combobox", { name: /Format/ }), "eps");
    const options = [...screen.getByRole("combobox", { name: /Background/ })
      .querySelectorAll("option")].map((o) => o.value);
    expect(options).toEqual(["light", "dark"]);
    expect((screen.getByRole("combobox", { name: /Background/ }) as HTMLSelectElement)
      .value).toBe("dark");
  });

  it("names the saved file by the figure id, not by the variables", async () => {
    /*
     * The point at which a figure leaves the system. A file called
     * `consumption-resistance.svg` is one nobody can trace back a month later.
     */
    vi.spyOn(api, "post")
      .mockResolvedValueOnce(clean)
      .mockResolvedValue({ warning: null });
    vi.spyOn(api, "getForBytes").mockResolvedValue(new Uint8Array([1]));
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => {});
    render(<PublishFigure projectId="prj_1" analysisRunId="arun_1" />);
    await userEvent.click(await screen.findByRole("button", { name: /Export for/ }));
    await userEvent.click(screen.getByRole("button", { name: /Download/ }));

    await waitFor(() => expect(click).toHaveBeenCalled());
    const anchor = click.mock.contexts[0] as HTMLAnchorElement;
    expect(anchor.download).toBe("vis_1.pdf");
  });
});

describe("when it goes wrong", () => {
  it("says what the server said", async () => {
    vi.spyOn(api, "post").mockRejectedValue(
      new ApiError(409, "Analysis run arun_1 is running; only a completed run "
                      + "has results to visualise."));
    render(<PublishFigure projectId="prj_1" analysisRunId="arun_1" />);
    await userEvent.click(await screen.findByRole("button", { name: /Export for/ }));
    expect(await screen.findByText(/only a completed run/)).toBeTruthy();
  });

  it("does not leave the button saying it is still working", async () => {
    vi.spyOn(api, "post").mockRejectedValue(new ApiError(500, "boom"));
    render(<PublishFigure projectId="prj_1" analysisRunId="arun_1" />);
    await userEvent.click(await screen.findByRole("button", { name: /Export for/ }));
    expect(await screen.findByRole("button", { name: /Export for publication/ }))
      .toBeTruthy();
  });
});

/**
 * A figure with no publication format at all.
 *
 * The publication renderer draws seven flat kinds of figure. Line, linear and
 * surface figures are not among them, and the format picker was shown for
 * every figure regardless — so each of those offered PDF, SVG and PNG and a
 * Download that failed every time it was pressed. Whether a format exists is
 * now the server's answer, carried as `exportable`.
 */
describe("a figure with no publication export", () => {
  const SURFACE = {
    visual_id: "vis_s", publishable: true, exportable: false,
    critique: { publishable: true, critiques: [] as Critique[] },
    spec: { visual_type: "surface" },
  };

  beforeEach(() => { vi.restoreAllMocks(); });

  it("offers no format that would fail, and points a surface elsewhere", async () => {
    vi.spyOn(api, "post").mockResolvedValue(SURFACE as never);
    // The Blender panel asks for its state as soon as it appears.
    vi.spyOn(api, "get").mockResolvedValue({
      visual_id: "vis_s", is_surface: true, available: true, version: "5.2.1",
      withheld: "", install: "", render: null, run: null,
    } as never);
    render(<PublishFigure projectId="prj_1" analysisRunId="arun_1" />);

    await userEvent.click(screen.getByRole("button", { name: /Export for publication/ }));

    expect(await screen.findByText(/has no flat publication export/)).toBeVisible();
    expect(screen.queryByRole("button", { name: /^Download$/ })).toBeNull();
    expect(screen.queryByRole("combobox", { name: /Format/ })).toBeNull();
    // What a surface *can* leave as is still offered.
    expect(screen.getByRole("button", { name: /Download 3D scene/ })).toBeVisible();
    expect(await screen.findByRole("button", { name: "Render with Blender" }))
      .toBeVisible();
  });

  it("says plainly that another kind has no export, and offers nothing 3D", async () => {
    vi.spyOn(api, "post").mockResolvedValue(
      { ...SURFACE, spec: { visual_type: "line" } } as never);
    render(<PublishFigure projectId="prj_1" analysisRunId="arun_1" />);

    await userEvent.click(screen.getByRole("button", { name: /Export for publication/ }));

    expect(await screen.findByText(/no publication export yet/)).toBeVisible();
    expect(screen.queryByRole("button", { name: /^Download$/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Download 3D scene/ })).toBeNull();
  });
});
