/**
 * The approval screen (§36, Rule 10).
 *
 * The gate this releases existed in the engine from the first migration and
 * could not be given by anybody: the approve route needed a run id nothing
 * handed out. So the tests that matter here are about whether the screen tells
 * the truth — what is waiting, what releasing it will do, and what happened
 * when a release did not take.
 */

import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { Approvals, waitedFor } from "@/components/approvals";
import { ApiError, api } from "@/lib/api";

const waiting = {
  run_id: "wfr_1",
  workflow_name: "discovery.run",
  node_name: "record_connections",
  describes: "Record 4 tested pairs into this project and promote whichever "
           + "survive correction at FDR 0.05.",
  waiting_since: new Date().toISOString(),
};

beforeEach(() => { vi.restoreAllMocks(); });

describe("what the screen shows", () => {
  it("shows the worker's own description of what will happen", async () => {
    /*
     * The control is the sentence, not the button. A screen that said "approve
     * record_connections" would be asking for a signature on an unread
     * document, which is what an approval gate is supposed to prevent.
     */
    vi.spyOn(api, "get").mockResolvedValue([waiting]);
    render(<Approvals projectId="prj_1" />);

    expect(await screen.findByText(/Record 4 tested pairs/)).toBeTruthy();
    expect(screen.getByText(/FDR 0.05/)).toBeTruthy();
  });

  it("renders nothing at all when nothing is waiting", async () => {
    // A permanent "no approvals pending" panel is clutter on every screen it
    // sits above.
    vi.spyOn(api, "get").mockResolvedValue([]);
    const { container } = render(<Approvals projectId="prj_1" />);
    await waitFor(() => expect(container.textContent).toBe(""));
  });

  it("says the work has stopped, not that it is running", async () => {
    vi.spyOn(api, "get").mockResolvedValue([waiting]);
    render(<Approvals projectId="prj_1" />);
    expect(await screen.findByText(/stopped before changing anything/)).toBeTruthy();
  });
});

describe("releasing a step", () => {
  it("approves the step that is waiting, by name", async () => {
    vi.spyOn(api, "get").mockResolvedValue([waiting]);
    const post = vi.spyOn(api, "post").mockResolvedValue({});
    render(<Approvals projectId="prj_1" />);

    await userEvent.click(await screen.findByRole("button", { name: /Release/ }));
    expect(post).toHaveBeenCalledWith(
      "/api/workflows/wfr_1/nodes/record_connections/approve");
  });

  it("tells the view showing the results to refresh", async () => {
    // Otherwise the researcher releases the step and watches an empty table.
    vi.spyOn(api, "get").mockResolvedValue([waiting]);
    vi.spyOn(api, "post").mockResolvedValue({});
    const released = vi.fn();
    render(<Approvals projectId="prj_1" onReleased={released} />);

    await userEvent.click(await screen.findByRole("button", { name: /Release/ }));
    await waitFor(() => expect(released).toHaveBeenCalled());
  });

  it("does not claim an approval that the server refused", async () => {
    /*
     * A 409 is the honest answer to a second click, or to a step someone else
     * released while this list was on screen. Reporting it as approved would
     * be reporting an approval that did not happen — which is the one thing
     * an approval screen must never do.
     */
    vi.spyOn(api, "get").mockResolvedValue([waiting]);
    vi.spyOn(api, "post").mockRejectedValue(
      new ApiError(409, "There is no step named 'record_connections' waiting."));
    const released = vi.fn();
    render(<Approvals projectId="prj_1" onReleased={released} />);

    await userEvent.click(await screen.findByRole("button", { name: /Release/ }));
    expect(await screen.findByText(/no longer waiting/)).toBeTruthy();
    expect(released).not.toHaveBeenCalled();
  });

  it("refetches after a refusal, so the list stops showing a dead row", async () => {
    const get = vi.spyOn(api, "get")
      .mockResolvedValueOnce([waiting])
      .mockResolvedValue([]);
    vi.spyOn(api, "post").mockRejectedValue(new ApiError(409, "gone"));
    render(<Approvals projectId="prj_1" />);

    await userEvent.click(await screen.findByRole("button", { name: /Release/ }));
    await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /Release/ })).toBeNull());
  });

  it("surfaces what the server said for any other failure", async () => {
    vi.spyOn(api, "get").mockResolvedValue([waiting]);
    vi.spyOn(api, "post").mockRejectedValue(new ApiError(500, "the database is down"));
    render(<Approvals projectId="prj_1" />);

    await userEvent.click(await screen.findByRole("button", { name: /Release/ }));
    expect(await screen.findByText(/the database is down/)).toBeTruthy();
  });
});

describe("how long it has waited", () => {
  it("reads as elapsed time rather than a timestamp", () => {
    const now = new Date("2026-08-28T12:00:00Z");
    const ago = (iso: string) => waitedFor(iso, now);
    expect(ago("2026-08-28T11:59:30Z")).toBe("just now");
    expect(ago("2026-08-28T11:45:00Z")).toBe("15 minutes ago");
    expect(ago("2026-08-28T09:00:00Z")).toBe("3 hours ago");
    expect(ago("2026-08-26T12:00:00Z")).toBe("2 days ago");
  });

  it("says nothing rather than 'Invalid Date' for a time it cannot read", () => {
    expect(waitedFor("not a date")).toBe("");
  });

  it("does not report a negative wait when the clocks disagree", () => {
    /*
     * The server's clock and the browser's are not the same clock, and
     * "-2 minutes ago" is how that difference would reach the researcher.
     *
     * This holds because a negative age falls under the first threshold, not
     * because it is clamped — a `Math.max(0, …)` here looked like the guard
     * and was dead code, since every negative value is already below 60. What
     * is asserted is the outcome, which is what would break if the thresholds
     * were ever rewritten.
     */
    const now = new Date("2026-08-28T12:00:00Z");
    expect(waitedFor("2026-08-28T12:00:30Z", now)).toBe("just now");
    expect(waitedFor("2026-08-28T14:00:00Z", now)).toBe("just now");
  });
});
