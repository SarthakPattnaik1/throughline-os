/**
 * What a search says about itself (D203, plan §4.9).
 *
 * The header used to be one grey line of five machine facts ending in a bare
 * `ret_…` id — the one identifier printed beside every search, and the one
 * thing on the screen that could not be opened. The route that audits which
 * passages an answer was built from, `GET /api/retrievals/{event_id}`, had no
 * caller anywhere in `apps/web`: the inventory's sole dead end by omission
 * rather than by design.
 *
 * Two disclosures, not one, and that is the part this file pins. Folding the
 * strategy and the two candidate counts under a summary that named only the
 * event id would bury three currently-visible readouts behind a label that
 * does not mention them — `ChartTable`'s law (`ChartTable.tsx:1-21`), which
 * this plan makes principle 4. So each summary states its own contents while
 * closed, and the audit is fetched when it is asked for rather than on every
 * search.
 */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Search } from "@/components/views";

const RESULT = {
  query: "how many took part",
  strategy: "hybrid",
  retrieval_event_id: "ret_evt_1",
  lexical_candidates: 40,
  semantic_candidates: 32,
  results: [{
    passage_id: "psg_1", source_id: "src_9",
    content: "In total 120 countries took part in the surveillance programme.",
    locator: "p. 3", page: 3, section: "Methods",
    lexical_score: 0.8, semantic_score: 0.7, fused_score: 0.75, rank: 1,
  }],
};

const AUDIT = {
  id: "ret_evt_1", project_id: "prj_1", query: "how many took part",
  strategy: "hybrid", filters: {}, result_count: 1,
  created_at: "2026-09-05T10:00:00Z",
  results: [{
    rank: 1, passage_id: "psg_1", source_id: "src_9",
    source_title: "Global AMR surveillance 2024",
    content: "In total 120 countries took part in the surveillance programme.",
    locator: "p. 3", page: 3, section: "Methods",
    lexical_score: 0.8, semantic_score: 0.7, fused_score: 0.75,
    rerank_score: null, char_start: 100, char_end: 162,
  }],
};

/** Answers by path, and records which paths were asked for. */
function serve(routes: Record<string, unknown>) {
  const calls: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(
    async (input: RequestInfo | URL) => {
      const url = String(input).replace(/^https?:\/\/[^/]+/, "");
      const path = url.split("?")[0];
      calls.push(path);
      const answer = routes[path];
      if (answer === undefined) {
        return { ok: false, status: 404,
                 text: async () => JSON.stringify({ detail: `not stubbed: ${path}` }) } as Response;
      }
      return { ok: true, status: 200,
               text: async () => JSON.stringify(answer) } as Response;
    });
  return calls;
}

async function search(over: Record<string, unknown> = {}) {
  const calls = serve({
    "/api/projects/prj_1/search": RESULT,
    "/api/retrievals/ret_evt_1": AUDIT,
    ...over,
  });
  const view = render(<Search projectId="prj_1" onOpenSource={() => {}} />);
  fireEvent.change(screen.getByLabelText("Search the sources in this project"),
                   { target: { value: "how many took part" } });
  fireEvent.click(screen.getByRole("button", { name: "Search" }));
  await screen.findByText(/120 countries took part/);
  return { ...view, calls };
}

beforeEach(() => { vi.restoreAllMocks(); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("the sentence above the results", () => {
  it("says what was searched before it says how", async () => {
    /*
     * The scope is the fact a reader needs first, and it is exact: the view
     * sends no source filter, so the search really did run over every passage
     * in the project.
     */
    await search();
    expect(screen.getByText("Searched every passage in this project.")).toBeTruthy();
  });

  it("prints no bare retrieval id outside a control", async () => {
    /*
     * The id was the screen's only unopenable identifier. It stays — a methods
     * section cites it — but inside the disclosure that opens it, not loose in
     * a header where it reads as noise.
     */
    const { container } = await search();
    // Outside a disclosure, that is: the id lives inside the one that opens
    // the record, where it can be quoted.
    const loose = [...container.querySelectorAll("p, span, div")]
      .filter((node) => node.children.length === 0
                     && !node.closest("details")
                     && (node.textContent ?? "").includes("ret_evt_"));
    expect(loose).toEqual([]);
  });
});

describe("two disclosures, each naming its own contents", () => {
  it("names the passages one and the strategy one while both are closed", async () => {
    const { container } = await search();
    const summaries = [...container.querySelectorAll("summary")]
      .map((node) => node.textContent ?? "");

    expect(summaries.some((text) => /which passages this search was built from/i.test(text)))
      .toBe(true);
    // The counts stay visible while closed: folding three readouts behind a
    // label that does not mention them is the thing principle 4 forbids.
    expect(summaries.some((text) =>
      /hybrid/.test(text) && /40 lexical/.test(text) && /32 semantic/.test(text)))
      .toBe(true);
    for (const details of container.querySelectorAll("details")) {
      expect(details.hasAttribute("open")).toBe(false);
    }
  });

  it("asks for the retrieval record only when the disclosure is opened", async () => {
    /*
     * Every search would otherwise pay for an audit almost nobody opens. The
     * request follows the gesture.
     */
    const { calls } = await search();
    expect(calls).not.toContain("/api/retrievals/ret_evt_1");

    fireEvent.click(screen.getByText(/which passages this search was built from/i));
    await waitFor(() => expect(calls).toContain("/api/retrievals/ret_evt_1"));
  });

  it("names the document each passage came from, which the hits cannot", async () => {
    /*
     * The hit list shows rank, locator and two scores and never says which
     * paper the words are from. The record does, and that is what makes it a
     * citation rather than a quotation.
     */
    await search();
    fireEvent.click(screen.getByText(/which passages this search was built from/i));
    // The record is fetched on the click. One second — the default — was
    // exceeded by 26 ms on a loaded CI runner (D414), so the wait is sized
    // for a busy machine rather than an idle one; the assertion is unchanged.
    expect(await screen.findByText("Global AMR surveillance 2024", undefined,
                                   { timeout: 5000 })).toBeTruthy();
    expect(screen.getByText(/ret_evt_1/)).toBeTruthy();
  });

  it("says what the server said when the record cannot be read", async () => {
    /** §104 — the server's words, not "something went wrong". */
    await search({ "/api/retrievals/ret_evt_1": undefined });
    fireEvent.click(screen.getByText(/which passages this search was built from/i));
    expect(await screen.findByText(/not stubbed/)).toBeTruthy();
  });

  it("names the strategy that actually ran, not the one that was intended", async () => {
    /*
     * `hybrid_search` reports "lexical" when no embedding model is installed,
     * and a screen that said "hybrid" anyway would be claiming a search half
     * of which never happened.
     */
    await search({
      "/api/projects/prj_1/search": {
        ...RESULT, strategy: "lexical", semantic_candidates: 0,
      },
    });
    fireEvent.click(screen.getByText(/how this search ran/i));
    expect(await screen.findByText(/only the keyword index answered/i)).toBeTruthy();
  });
});

describe("what a hit hands back", () => {
  it("keeps the route to the document", async () => {
    /** The forward edge D203 bought; nothing here removes it. */
    await search();
    expect(screen.getByRole("button", { name: /open the source/i })).toBeTruthy();
  });

  it("states why a passage cannot go on the excerpt board rather than offering a control that would", async () => {
    /*
     * §205 requires an excerpt to carry the region circled on the page, and
     * `ExcerptRequest` makes `region` required for exactly that reason. A
     * search hit is matched text: `SearchResult.results` has no region and the
     * `passages` table stores none. An excerpt with an invented region is the
     * orphan §205 exists to keep off the board, so the screen says so in place
     * (principle 7) instead of shipping a control that would fail or lie.
     */
    await search();
    const said = screen.getByText(/Taking a passage/).closest("p")!;
    expect(said.textContent).toMatch(/no circled region/);
    expect(screen.queryByRole("button", { name: /take this passage/i })).toBeNull();
  });
});
