/**
 * The rail shows one stage of the loop at a time.
 *
 * Twenty-six entries under five headings were all on screen at once, and the
 * owner's reading of the result was the plainest kind of evidence there is:
 * "there are too many options on screen." A rail that lists everything a
 * product can do is a table of contents, not a place; the eye has to re-read it
 * on every glance because nothing in it is ever the answer.
 *
 * Nothing is removed, and that is the constraint these tests hold. Every one of
 * the twenty-six entries is still reachable, in the same group, under the same
 * label, in two presses at worst — and the five headings are always on screen
 * saying how many entries each of them holds, which is what makes a collapsed
 * group a fold rather than a menu.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { PAGES, SECTIONS, Section, Shell } from "@/components/Shell";

afterEach(cleanup);

const HEADINGS = [
  "The project", "Evidence", "Analysis", "Lineage", "Communicate", "This machine",
];

function shell(section: Section = "overview", onSection = vi.fn()) {
  const result = render(
    <Shell section={section} onSection={onSection} map={null} inspector={null}
           projectName="A project" crumbs={[]}
           onDropFiles={vi.fn()}>
      <p>content</p>
    </Shell>,
  );
  return { ...result, onSection };
}

/**
 * The heading button for a group, by the name it starts with.
 *
 * The name ends "3 screens" rather than "3" since T188: a bare digit beside a
 * group heading reads as a count of the things in the project — "Evidence 3"
 * as three sources — when it counts the screens behind the heading. The digit
 * is still what this matches on, because the point of the assertion is that
 * the count travels in the accessible name at all.
 */
function heading(label: string): HTMLElement {
  return screen.getByRole("button",
    { name: (name) => name.startsWith(label) && /\d( screens)?$/.test(name) });
}

/** The rows that are actually on screen, as their labels. */
function visibleEntries(container: HTMLElement): string[] {
  return [...container.querySelectorAll(".sectionbar-item")]
    .map((row) => row.querySelector("span:not(.sectionbar-icon)")?.firstChild?.textContent ?? "");
}

describe("one group is expanded, and it is the one holding the current section", () => {
  it("names every group whatever is open, with a count on each", () => {
    /** A collapsed group that did not say how many entries it held would be a
     *  menu, which is the thing this product does not do. */
    const { container } = shell("connections");
    const names = [...container.querySelectorAll(".groupbar-tab")]
      .map((h) => h.textContent);
    expect(names).toEqual(
      ["The project2", "Evidence3", "Analysis2", "Lineage3", "Communicate4",
       "This machine4"]);
    /*
     * Fifteen sections plus the three machine pages.
     *
     * It was twenty-three. Eight were absorbed into the screen that owns them
     * — searching the library, finding papers, finding data and digitising a
     * figure are views of Sources; the chart catalogue is a view of Figures;
     * the activity log is the second reading of the Record; the pattern sweep
     * and the embedding space are two more readings of this project's
     * analyses. Every one of them is still reachable, as a view rather than as
     * a peer of the thing it serves.
     */
    expect(SECTIONS.length + PAGES.length).toBe(18);
  });

  it("shows only the current section's group", () => {
    const { container } = shell("connections");
    expect(visibleEntries(container)).toEqual([
      "Connections", "Findings", "Research graph",
    ]);
    // And the entries of every other group are not merely hidden — they are
    // not built, so a screen reader walking the rail meets five headings.
    expect(screen.queryByRole("button", { name: /^Sources/ })).toBeNull();
    expect(screen.queryByRole("link", { name: /Draw in the air/ })).toBeNull();
  });

  it("follows the section, so choosing an entry leaves its group open", () => {
    /** The group holding the section *is* the open one; there is no state to
     *  fall out of step, which is what makes Back work too. */
    const { container } = shell("reports");
    expect(visibleEntries(container)).toContain("Reports");
    expect(heading("Communicate")).toHaveAttribute("aria-expanded", "true");
    expect(heading("Evidence")).toHaveAttribute("aria-expanded", "false");
  });

  it("marks the current entry, which is inside the open group", () => {
    const { container } = shell("findings");
    const current = container.querySelector(".sectionbar-item[aria-current='true']");
    expect(current?.textContent).toMatch(/^Findings/);
  });
});

describe("a heading press expands its group and collapses the rest", () => {
  it("opens the pressed group and closes the one that was open", () => {
    const { container } = shell("overview");
    expect(heading("The project")).toHaveAttribute("aria-expanded", "true");

    fireEvent.click(heading("Evidence"));

    expect(heading("Evidence")).toHaveAttribute("aria-expanded", "true");
    for (const other of HEADINGS.filter((h) => h !== "Evidence")) {
      expect(heading(other), other).toHaveAttribute("aria-expanded", "false");
    }
    expect(visibleEntries(container)).toEqual([
      // Three, not six: searching the library, finding papers, finding data
      // and digitising a figure are views of Sources now rather than peers of
      // it, and Compare joined what it compares.
      "Sources", "Variables", "Compare",
    ]);
  });

  it("is reachable and operable from the keyboard", () => {
    /** §30. A disclosure a keyboard cannot open is a disclosure that is not
     *  there; `Enter` and `Space` are the button contract, and the headings are
     *  real buttons so they get it without a handler of our own. */
    shell("overview");
    const gather = heading("Evidence");
    expect(gather.tagName).toBe("BUTTON");
    expect(gather.tabIndex).toBeGreaterThanOrEqual(0);
    gather.focus();
    fireEvent.keyDown(gather, { key: "Enter" });
    fireEvent.click(gather);  // what a browser dispatches after Enter
    expect(gather).toHaveAttribute("aria-expanded", "true");
  });

  it("names the region it opens", () => {
    const { container } = shell("overview");
    const controls = heading("Evidence").getAttribute("aria-controls")!;
    expect(container.querySelector(`#${controls}`)).not.toBeNull();
  });

  it("stays open when its own heading is pressed", () => {
    /** One group is open at a time, so closing the open one leaves five
     *  headings and no rows: the rail stops saying where you are, and no
     *  `aria-current` is on screen to say it. This is a choice among five, the
     *  way a radio group is — the only way to close a group is to open
     *  another, and a press on the open heading is not a way out of the rail. */
    const { container } = shell("overview");
    fireEvent.click(heading("The project"));
    expect(heading("The project")).toHaveAttribute("aria-expanded", "true");
    // Overview first: it is where a researcher lands and what the loop card
    // is on, and the workboard is where objects are arranged afterwards.
    expect(visibleEntries(container)).toEqual(["Overview", "Workboard"]);
    expect(container.querySelector('.sectionbar [aria-current="true"]')?.textContent)
      .toMatch(/^Overview/);
  });

  it("forgets a hand-made expansion as soon as the section changes", () => {
    /** The rule is "the group holding the current section", and a press is an
     *  override of it. An override that outlived the screen it was made on
     *  would leave the rail pointing somewhere the researcher no longer is. */
    const { rerender } = shell("overview");
    fireEvent.click(heading("Evidence"));
    expect(heading("Evidence")).toHaveAttribute("aria-expanded", "true");

    rerender(
      <Shell section="reports" onSection={vi.fn()} map={null} inspector={null}
             projectName="A project" crumbs={[]}
             onDropFiles={vi.fn()}>
        <p>content</p>
      </Shell>,
    );

    expect(heading("Communicate")).toHaveAttribute("aria-expanded", "true");
    expect(heading("Evidence")).toHaveAttribute("aria-expanded", "false");
  });
});

describe("every one of the twenty-six entries is still reachable", () => {
  it("reaches each section in two presses: its heading, then its row", () => {
    /**
     * The whole of the argument for collapsing anything. If one entry cannot be
     * got to under the same name in the same group, this is not a fold — it is
     * a removal with a nicer word on it.
     */
    const onSection = vi.fn();
    const { container } = shell("overview", onSection);

    for (const section of SECTIONS) {
      // Pressing the open heading is a no-op, so this is safe either way; the
      // press is skipped only to keep the count of presses honest at two.
      const head = heading(section.group);
      if (head.getAttribute("aria-expanded") === "false") fireEvent.click(head);
      const rail = container.querySelector(".sectionbar")!;
      const row = within(rail as HTMLElement).getByRole("button",
        { name: (name) => name.startsWith(section.label) });
      fireEvent.click(row);
      expect(onSection, section.label).toHaveBeenCalledWith(section.id);
      onSection.mockClear();
    }
  });

  it("reaches all three machine pages the same way, as real links", () => {
    shell("overview");
    fireEvent.click(heading("This machine"));
    for (const page of PAGES) {
      const link = screen.getByRole("link",
        { name: (name) => name.includes(page.label) });
      expect(link.tagName, page.label).toBe("A");
      expect(link).toHaveAttribute("href", page.href);
    }
  });
});
