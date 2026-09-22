/**
 * The pages a researcher can actually reach (§1, §109).
 *
 * `/gesture-check` and `/air-ink` existed for a long time and nothing linked to
 * them — not the rail, not the landing page, not the workspace. Between them
 * they are the largest and most carefully engineered part of this codebase, and
 * a researcher could only arrive by typing the URL, which nobody does.
 *
 * That is what these tests are guarding: not that the pages work, but that a
 * person can get to them. A capability nobody can find is indistinguishable
 * from one that was never built, and the way this regressed the first time was
 * silence — no test failed, because no test asked.
 */

import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { PAGES, SECTIONS, Shell } from "@/components/Shell";

/**
 * The rail shows one stage at a time (T139), so a group that does not hold the
 * current section renders as its heading and its count and nothing else. Every
 * assertion below about a row therefore expands its group first — which is
 * also the shape of the click a person makes, and the shape
 * `scripts/walk-the-flow.mjs` has to make.
 */
function openMachine() {
  fireEvent.click(screen.getByRole("button", { name: /^This machine/ }));
}

function shell() {
  return render(
    <Shell
      section="overview"
      onSection={vi.fn()}
      map={null}
      inspector={null}
     
      projectName="A project"
      crumbs={[]}
      onDropFiles={vi.fn()}
    >
      <p>content</p>
    </Shell>,
  );
}

describe("the hand-tracking pages are reachable from the product", () => {
  it("links to the tracking check", () => {
    shell();
    openMachine();
    const link = screen.getByRole("link", { name: /check hand tracking/i });
    expect(link).toHaveAttribute("href", "/gesture-check");
  });

  it("links to drawing in the air", () => {
    shell();
    openMachine();
    expect(screen.getByRole("link", { name: /draw in the air/i }))
      .toHaveAttribute("href", "/air-ink");
  });

  it("uses real links, so they can be opened in a new tab", () => {
    /*
     * A button that navigated would look identical and would quietly take away
     * middle-click, cmd-click and "copy link address" — the three things
     * somebody does when they want to keep a diagnostic page open beside their
     * work, which is exactly how these pages get used.
     */
    shell();
    openMachine();
    for (const name of [/check hand tracking/i, /draw in the air/i]) {
      expect(screen.getByRole("link", { name }).tagName).toBe("A");
    }
  });

  it("files them under the machine rather than among the research steps", () => {
    /*
     * They answer "does this camera see my hands", which is a question about
     * the machine. Listing them between Findings and Reports would say they
     * were a step in doing research, and they are not.
     */
    shell();
    openMachine();
    const link = screen.getByRole("link", { name: /check hand tracking/i });
    // The group's name is in the row above its entries now rather than wrapping
    // them, so the row states which group it belongs to and the link sits in it
    // beside Settings.
    const sections = link.closest("nav.sectionbar");
    expect(sections?.getAttribute("aria-label")).toBe("This machine");
    expect(sections?.textContent).toContain("Settings");
  });

  it("says what each one is for", () => {
    // A rail item whose label is a noun tells somebody what it is called, not
    // whether it is the thing they want.
    shell();
    openMachine();
    expect(screen.getByRole("link", { name: /check hand tracking/i }))
      .toHaveAttribute("title", expect.stringMatching(/camera/i));
  });
});

describe("the standalone pages are offered to the palette as well as the rail", () => {
  /**
   * The rail shows one stage at a time, so a page that is only in the rail is
   * a page somebody has to open a group to find, and ⌘K is what a researcher
   * reaches for instead — so `PAGES` exists to give the palette the same three
   * destinations (plan §4.3.6). It was written when all 26 rows were on screen
   * at once and the bottom of them scrolled off a 900px laptop; the accordion
   * changed the reason and not the requirement.
   */
  it("exports all three, with where they live", () => {
    expect(PAGES.map((p) => p.href).sort())
      .toEqual(["/air-ink", "/charts-3d", "/gesture-check"]);
    for (const page of PAGES) {
      expect(page.group, page.href).toBe("This machine");
      expect(page.label.length, page.href).toBeGreaterThan(3);
    }
  });

  it("keeps them out of SECTIONS, which is the rail's own order", () => {
    /**
     * Not a tidiness rule. A section is reached by calling `onSection`; a page
     * is reached by loading a URL, and `rail-follows-the-work.test.ts` reads
     * `SECTIONS` as the order of the research steps and asserts no id in it is
     * `charts-3d`. Merging the two lists would hand the palette entries that
     * need two different mechanisms with no way to tell them apart.
     */
    const ids = new Set(SECTIONS.map((section) => section.id as string));
    for (const page of PAGES) {
      expect(ids.has(page.href), page.href).toBe(false);
      expect(ids.has(page.href.replace("/", "")), page.href).toBe(false);
    }
  });

  it("names the same three pages the rail links to", () => {
    // Two doors onto one room. If a page is added to one list and not the
    // other, one of the two surfaces is silently missing it.
    shell();
    openMachine();
    for (const page of PAGES) {
      // A substring, because the rail row's accessible name is the label plus
      // the one-clause note beside it — the palette shows the label alone.
      expect(screen.getByRole("link", { name: (name) => name.includes(page.label) }))
        .toHaveAttribute("href", page.href);
    }
  });
});
