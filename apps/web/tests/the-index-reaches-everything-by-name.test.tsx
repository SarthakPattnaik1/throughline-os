/**
 * Everything a project holds, reachable by name or by id (T197).
 *
 * These were the command palette's tests. The palette is gone — it searched
 * this same index through this same ranking and matched no verbs at all, so it
 * was a strict subset of the bar at the foot of every screen, behind a second
 * front door. What it was *for* survives, and so do the tests that hold it:
 * the index knew 23 sections and three kinds of object, and four more kinds a
 * project accumulates were unreachable — the standalone pages, every analysis
 * run, every report and every saved figure. Somebody holding a run id, out of
 * the address bar or an error or a colleague's message, had nowhere to type it.
 *
 * Written against `buildCommands` rather than a hand-made list, because the
 * defect was never in the ranking; it was in what was handed to it. Driven
 * through the bar, because that is what renders it now.
 *
 * The modal-only assertions went with the modal: the `<dialog>` element, its
 * focus trap, its cancel event, and clearing the query across a reopen. The
 * bar is not modal and clears itself after every run, which its own tests
 * cover.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Connection, Finding, Source } from "@/lib/api";
import { buildCommands } from "@/components/commands";
import { OneBar } from "@/components/onebar";
import { PAGES, SECTIONS } from "@/components/Shell";
import { VERBS } from "@/components/verbs";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const SOURCES: Source[] = [];
const CONNECTIONS: Connection[] = [];
const FINDINGS: Finding[] = [];

const ANALYSES = [
  { id: "arun_8f21c4", method: "pearson_correlation",
    left_variable: "consumption", right_variable: "resistance_pct" },
  { id: "arun_0b7d19", method: "kruskal_wallis",
    left_variable: null, right_variable: null },
];

const REPORTS = [
  { id: "art_31aa", title: "Resistance and consumption, 2019-2024",
    artifact_type: "research_report" },
];

const FIGURES = [
  { id: "vis_77b2", title: null, visual_type: "scatter" },
];

function built(open = vi.fn(), go = vi.fn()) {
  return buildCommands({
    sections: SECTIONS,
    pages: PAGES,
    sources: SOURCES,
    connections: CONNECTIONS,
    findings: FINDINGS,
    analyses: ANALYSES,
    reports: REPORTS,
    figures: FIGURES,
    labels: { resistance_pct: "Resistance (%)" },
    go,
    open,
  });
}

/**
 * Type into the bar and read back the rows it offers.
 *
 * Only the name matches: a query like "a" also matches verbs, and these tests
 * are about the index. The bar puts verbs first and names under them, which
 * `the-one-bar-does-what-it-says` covers from the other side.
 */
async function offers(query: string, list = built()) {
  render(<OneBar commands={list} onVerb={vi.fn()} size="dock" />);
  const input = screen.getByRole("combobox");
  await userEvent.type(input, query);
  return screen.queryAllByRole("option")
    .map((row) => row.textContent ?? "")
    .filter((text) => !VERB_LABELS.some((v) => text.startsWith(v)));
}

/** Every verb's heading, so an offer list can be filtered down to names. */
const VERB_LABELS = VERBS.map((v) => v.label);

describe("the index reaches everything the project has", () => {
  it("offers a standalone page by name", async () => {
    /**
     * `/air-ink` is a route, not a section, so it was in neither the palette's
     * section list nor any object list — reachable only from a rail row that
     * falls below the fold on a 900px laptop, which is the same defect the
     * pinned footer was written to fix from the other end.
     */
    const rows = await offers("air");
    expect(rows.join(" | ")).toMatch(/Draw in the air/);
  });

  it("navigates to a page rather than changing section", async () => {
    /**
     * §123 — a control does what it appears to do. The workspace navigates by
     * `pushState` within one page, so calling `go` for `/air-ink` would close
     * the palette and leave the researcher where they were. This is the one
     * command in the list that must be a real page load.
     */
    const assign = vi.fn();
    const go = vi.fn();
    // Restored by hand rather than by `restoreAllMocks`: `window.location` is
    // not a mock, it is a redefined property, and leaving a stub behind would
    // break whatever ran next in this file.
    const original = Object.getOwnPropertyDescriptor(window, "location");
    Object.defineProperty(window, "location", {
      configurable: true, value: { assign },
    });
    try {
      const page = built(vi.fn(), go).find((c) => c.label === "Draw in the air");
      page!.run();

      expect(assign).toHaveBeenCalledWith("/air-ink");
      expect(go).not.toHaveBeenCalled();
    } finally {
      if (original) Object.defineProperty(window, "location", original);
    }
  });

  it("finds an analysis by the id somebody is holding", async () => {
    /**
     * The failure this closes: `arun_8f21c4` appears in the address bar and in
     * every server error about that run, and pasting it into the palette
     * matched nothing at all, because only labels were searched and no label
     * contains an id.
     */
    const rows = await offers("arun_8f21c4");
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatch(/pearson correlation/);
  });

  it("opens the analysis it matched, in the section that shows a run", async () => {
    const open = vi.fn();
    render(<OneBar commands={built(open)} onVerb={vi.fn()} size="dock" />);
    const input = screen.getByRole("combobox");

    await userEvent.type(input, "arun_8f21c4{Enter}");

    expect(open).toHaveBeenCalledWith("analyses", "analysis", "arun_8f21c4");
  });

  it("matches an id from its front, and never in the middle", async () => {
    /**
     * Ids are hex. The subsequence rule that makes `rsp` find `resistance_pct`
     * would, applied to `arun_8f21c4`, match on almost any query — so an id is
     * matched literally and by prefix only, which are the two shapes that
     * happen: pasted whole, or typed from the start.
     */
    expect((await offers("arun_")).length).toBe(2);
    cleanup();
    // A fragment out of the middle of an id is not a search anybody performs,
    // and treating it as one is what buries the labels.
    expect(await offers("8f21c4")).toHaveLength(0);
  });

  it("does not let a one-letter query become a list of ids", async () => {
    /**
     * Every analysis id starts `arun_`, so matching ids on one character would
     * put the whole run table above the section somebody was typing towards.
     * The list is capped at 40; a flood is a hide.
     */
    const rows = await offers("a");
    expect(rows[0]).toMatch(/Analyses|Activity/);
  });

  it("names an analysis by what it tested, not by its method alone", async () => {
    /**
     * The rule `nameOf` follows in the analyses list: a swept run is known by
     * its pair. Approved labels are used, because a palette full of raw column
     * names is unsearchable — which is why `labels` existed here already.
     */
    const rows = await offers("consumption");
    expect(rows.join(" | ")).toMatch(/consumption × Resistance \(%\)/);
  });

  it("falls back to the method when a run belongs to no pair", async () => {
    // A specified run has no left/right pair, and `kruskal_wallis` with its
    // underscores is a database value, not a name.
    const rows = await offers("kruskal");
    expect(rows.join(" | ")).toMatch(/kruskal wallis/);
  });

  it("offers reports and figures, which nothing indexed before", async () => {
    const open = vi.fn();
    const list = built(open);
    expect(list.find((c) => c.id === "art:art_31aa")?.group).toBe("Report");
    list.find((c) => c.id === "art:art_31aa")!.run();
    expect(open).toHaveBeenCalledWith("reports", "artifact", "art_31aa");

    list.find((c) => c.id === "fig:vis_77b2")!.run();
    expect(open).toHaveBeenCalledWith("figures", "artifact", "vis_77b2");
  });

  it("gives an untitled figure the name the figures list gives it", async () => {
    // `savedfigures.tsx` prints "Untitled scatter"; a palette row reading only
    // "" is one nobody can pick, and two names for one figure is worse.
    const rows = await offers("untitled");
    expect(rows.join(" | ")).toMatch(/Untitled scatter/);
  });

  it("says what it indexes, where somebody typing into it will read it", () => {
    /**
     * A control that under-describes itself is the mirror of §123: nobody
     * types an analysis id into a box that never claimed to know one. The
     * palette said this in its placeholder, which listed four of the seven
     * kinds it searched. The bar's placeholder is an invitation rather than an
     * inventory, so the inventory moved to "What can I ask?" — where it has
     * room to be complete, and is checked here for being so.
     */
    render(<OneBar commands={built()} onVerb={vi.fn()} size="dock" />);
    fireEvent.focus(screen.getByRole("combobox"));
    fireEvent.click(screen.getByRole("button", { name: "What can I ask?" }));
    const said = screen.getByText(/things to ask for/).textContent ?? "";

    for (const kind of
         ["section", "source", "connection", "finding", "analysis", "report",
          "figure", "page"]) {
      expect(said, kind).toContain(kind);
    }
  });
});
