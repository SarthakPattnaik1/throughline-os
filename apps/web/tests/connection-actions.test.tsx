/**
 * What a researcher can do with a connection, on arrival at the connection.
 *
 * This is the screen the Overview's loop sends somebody to in order to take
 * steps 4, 5 and 6 — try to destroy the result, record what it shows, then
 * communicate it. Two of those three could not be seen when the screen loaded:
 * "Record this as a finding" was the seventh block, roughly two screens below
 * the fold and beneath both the fragility panel and the report history, and
 * drafting a report from a connection existed only on the Reports screen,
 * which is not a place a researcher holding a result thinks to open.
 *
 * So two things are pinned here. **The order** — recording now sits directly
 * after the validation card, above the reading that supports it, because
 * recording is the act that *follows* validation rather than something
 * fragility gates. And **the actions band** under the result, whose contract is
 * the part that will decay first: each entry either performs the act or moves
 * to the real control already on the page, and where an act is impossible the
 * entry stays and says why. A band that silently drops its ineligible entries
 * teaches a reader the product cannot draft reports at all; a band that
 * renders its own second Validate button leaves two controls to keep in step.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ConnectionDetail } from "@/components/views";
import type { Connection } from "@/lib/api";

const CONNECTION: Connection = {
  id: "conn_1", left_variable: "consumption", right_variable: "resistance",
  method: "pearson_correlation", lifecycle_status: "exploratory",
  estimate: 0.81, p_value: 0.001, q_value: 0.01,
  survived_correction: true, false_discovery_rate: 0.05, effect_size: 0.81,
  effect_size_name: "r", sample_size: 120, evidence_quality: "moderate",
  rank_score: 0.7, analysis_run_id: "arun_1", analysis_object_id: null,
  dataset_version_id: "dsv_1",
};

const RUN = {
  id: "arun_1", status: "completed", method: "pearson_correlation",
  assumption_checks: [], result: {},
};

type Answer = unknown | ((url: string, init?: RequestInit) => unknown);

/**
 * An API that answers by path, as `workspace-flow` does.
 *
 * Anything not stubbed answers 404, which every panel here renders as its own
 * failure or refusal card — so the fragility panel this file only needs for
 * its position in the document does not have to be given a fixture, and a
 * request nobody thought of shows a message rather than blanking the screen.
 */
function serve(routes: Record<string, Answer>) {
  const calls: Array<{ method: string; path: string; body: unknown }> = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input).replace(/^https?:\/\/[^/]+/, "");
      const path = url.split("?")[0];
      calls.push({
        method: init?.method ?? "GET", path,
        body: typeof init?.body === "string" ? JSON.parse(init.body) : null,
      });
      const answer = routes[path];
      if (answer === undefined) {
        return {
          ok: false, status: 404,
          text: async () => JSON.stringify({ detail: `not stubbed: ${path}` }),
        } as Response;
      }
      const value = typeof answer === "function" ? answer(url, init) : answer;
      return {
        ok: true, status: 200,
        text: async () => JSON.stringify(value), json: async () => value,
      } as Response;
    });
  return calls;
}

function routesFor(connection: Connection, over: Record<string, Answer> = {}) {
  return {
    "/api/projects/prj_1/connections": [connection],
    "/api/projects/prj_1/variables": { labels: {} },
    "/api/connections/conn_1/validations": [],
    "/api/dataset-versions/dsv_1/columns": [],
    "/api/analyses/arun_1": RUN,
    "/api/analyses/arun_1/plain-summary": null,
    ...over,
  };
}

/** Render the screen and wait for the result card to arrive. */
async function open(connection: Connection = CONNECTION,
                    props: { onDraftedReport?: (id: string) => void } = {},
                    over: Record<string, Answer> = {}) {
  const calls = serve(routesFor(connection, over));
  const { container } = render(
    <ConnectionDetail connectionId="conn_1" projectId="prj_1" {...props} />);
  await screen.findByRole("heading", { name: /consumption and resistance/ });
  return { container, calls };
}

/** Where a node sits among the screen's top-level blocks. */
function order(container: HTMLElement, node: Element | null) {
  return [...container.children].indexOf(node as Element);
}

beforeEach(() => { vi.restoreAllMocks(); });
afterEach(() => { vi.restoreAllMocks(); });

describe("the band of things that can be done to this connection", () => {
  it("sits under the result and above the checks, not at the end of the scroll", async () => {
    // Under the thing it acts on, and before the first block a reader would
    // have to scroll past. Anywhere lower and it is not an answer to "what do
    // I do here" — it is another thing to find.
    const { container } = await open();

    const band = container.querySelector(".oa");
    expect(band).toBeTruthy();
    expect(order(container, band))
      .toBe(order(container, container.querySelector("article.rc")) + 1);
    expect(order(container, band))
      .toBeLessThan(order(container, document.getElementById("connection-validate")));
  });

  it("names itself, so the row of verbs is not an unlabelled toolbar", async () => {
    const { container } = await open();
    expect(container.querySelector(".oa")!.getAttribute("aria-label"))
      .toBe("What you can do with this");
  });
});

describe("an entry moves to the real control, and never duplicates it", () => {
  it("puts the keyboard on the Validate control, not only the scroll", async () => {
    /*
     * A "jump to" that only scrolls strands a keyboard user where they were:
     * the next Tab carries on from the top of the document, past everything
     * the scroll skipped. And there is exactly one Validate button on the
     * screen — the band moves to it rather than rendering a second one that
     * would not know which confounders had been ticked.
     */
    await open();
    fireEvent.click(screen.getByRole("button", { name: "Validate ↓" }));

    const card = document.getElementById("connection-validate")!;
    expect(card.contains(document.activeElement)).toBe(true);
    expect(screen.getAllByRole("button", { name: "Validate" })).toHaveLength(1);
  });

  it("puts the keyboard on the record card, which used to be two screens down", async () => {
    await open();
    fireEvent.click(screen.getByRole("button", { name: "Record this as a finding ↓" }));

    const card = document.getElementById("connection-record")!;
    expect(card.contains(document.activeElement)).toBe(true);
  });
});

describe("drafting a report from the connection", () => {
  it("posts to the same route the Reports screen posts to, and opens what it made",
     async () => {
    /*
     * The loop's last step, from the object the researcher is holding. The
     * request is the Reports screen's request — one capability with a second
     * door, not a second implementation that could drift from the first.
     */
    const opened = vi.fn();
    const { calls } = await open(CONNECTION, { onDraftedReport: opened },
      { "/api/projects/prj_1/artifacts/draft": { artifact_id: "art_9" },
        "/api/artifacts/art_9/check-citations": {} });

    fireEvent.click(screen.getByRole("button", { name: "Draft a report from this" }));

    await waitFor(() => expect(opened).toHaveBeenCalledWith("art_9"));
    expect(calls).toContainEqual({
      method: "POST", path: "/api/projects/prj_1/artifacts/draft",
      body: { connection_id: "conn_1" },
    });
    // And the citations are checked, as the Reports screen checks them: a
    // report drafted here must not be a report with no integrity verdict.
    expect(calls).toContainEqual({
      method: "POST", path: "/api/artifacts/art_9/check-citations", body: null,
    });
  });

  it("offers the way back to the draft rather than drafting a second one", async () => {
    // Pressing again would assemble another document from the same result and
    // leave the researcher on neither.
    const opened = vi.fn();
    await open(CONNECTION, { onDraftedReport: opened },
      { "/api/projects/prj_1/artifacts/draft": { artifact_id: "art_9" },
        "/api/artifacts/art_9/check-citations": {} });

    fireEvent.click(screen.getByRole("button", { name: "Draft a report from this" }));
    const again = await screen.findByRole("button", { name: "Drafted — open it" });
    fireEvent.click(again);

    expect(opened).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole("button", { name: "Draft a report from this" })).toBeNull();
  });

  it("says what the server said when the draft fails", async () => {
    // §104 — the server's own words, not "something went wrong". The draft
    // route is left unstubbed, so it answers as a failure does.
    await open();
    fireEvent.click(screen.getByRole("button", { name: "Draft a report from this" }));

    // Other panels on this screen may independently surface their own
    // "not stubbed" fixture failures. Assert on the draft action's alert and
    // exact route instead of a page-global substring whose match count races
    // with those background requests.
    const alert = await screen.findByRole("alert");
    expect(alert.textContent)
      .toContain("not stubbed: /api/projects/prj_1/artifacts/draft");
  });

  it("keeps the control and states the reason where a report cannot start",
     async () => {
    /*
     * §80: a report is written from something that was tested. Where there is
     * no recorded analysis run the entry stays and says so — dropping it would
     * leave a researcher unable to tell a capability that does not exist from
     * one they have failed to find, and they would go looking.
     */
    await open({ ...CONNECTION, analysis_run_id: null });

    expect(screen.getByText(/no recorded analysis run/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Draft a report/ })).toBeNull();
  });
});

describe("the order of the blocks down the screen", () => {
  it("puts recording above the fragility reading it does not depend on", async () => {
    /*
     * Recording is what follows a validation; fragility and the report history
     * are the reading around it, not a gate before it. Measured at 1440×900,
     * the fold ended inside "How fragile is this?" with a bare number as the
     * last visible thing, and the record card was below it.
     */
    const { container } = await open();
    const fragility = await waitFor(() => {
      const section = container.querySelector("section.fragility");
      expect(section).toBeTruthy();
      return section!;
    });

    expect(fragility.textContent).toContain("How fragile is this?");
    expect(order(container, document.getElementById("connection-record")))
      .toBeLessThan(order(container, fragility));
  });
});
