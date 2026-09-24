/**
 * The project's journal.
 *
 * `GET /projects/{id}/journal` had no view: a note could be read beside the
 * thing it was about and nowhere else, so a researcher returning after a
 * fortnight could check one object at a time and had to remember which ones to
 * check.
 *
 * The property worth holding is the one the per-object journal already holds —
 * a model's note never looks like a person's note. It is the same table, and
 * two screens that rendered it differently would teach a reader that the
 * distinction is decorative.
 */

import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { Journal, JournalEntry, byDay, dayOf } from "@/components/journal";
import { api } from "@/lib/api";

/**
 * A timestamp on a given local day and hour.
 *
 * Built from local components rather than written as a UTC string, because the
 * grouping is by the reader's own day: `2026-08-27T22:00Z` is the 28th in
 * India and the 27th in London, so a fixture written in UTC makes this suite
 * pass or fail depending on where it runs. An earlier version of these tests
 * did exactly that.
 */
const at = (day: number, hour: number): string =>
  new Date(2026, 7, day, hour, 0, 0).toISOString();

const entry = (over: Partial<JournalEntry> = {}): JournalEntry => ({
  id: "note_1",
  object_id: "obj_1",
  object_type: "analysis",
  object_title: "Sleep and reaction time",
  body: "The outlier is one hospital, not one patient.",
  author_kind: "human",
  author: "usr_1",
  model: null,
  created_at: at(28, 14),
  ...over,
});

function serve(entries: JournalEntry[]) {
  vi.spyOn(api, "get").mockResolvedValue(entries as never);
}

beforeEach(() => { vi.restoreAllMocks(); });

describe("a model's note is never a person's note", () => {
  it("marks who wrote each one", async () => {
    serve([
      entry({ id: "n1" }),
      entry({ id: "n2", author_kind: "model", model: "claude-3",
              body: "The association weakens once site is adjusted for." }),
    ]);
    render(<Journal projectId="prj_1" />);

    expect(await screen.findByText("you")).toBeTruthy();
    expect(screen.getByText(/claude-3 — written by a model/)).toBeTruthy();
  });

  it("carries the same mark the per-object journal uses", async () => {
    /*
     * Same rows, same appearance. A second styling for one table teaches a
     * reader that the human/model distinction is a decoration of this screen
     * rather than a fact about the note.
     */
    serve([entry({ id: "n2", author_kind: "model", model: "claude-3" })]);
    const { container } = render(<Journal projectId="prj_1" />);

    // The author line specifically: the lede above also contains the phrase,
    // and matching either of them would not prove the note was marked.
    await screen.findByText(/^claude-3 — written by a model$/);
    expect(container.querySelector('.nj-note[data-kind="model"]')).toBeTruthy();
    expect(container.querySelector('.nj-note[data-kind="human"]')).toBeNull();
    // Including the mark itself, which is the part a reader sees before they
    // read anything: the attribute alone is invisible.
    expect(container.querySelector(".nj-mark")!.textContent).toBe("◇");
  });

  it("marks a person's note differently from a model's", async () => {
    serve([entry({ id: "n1" })]);
    const { container } = render(<Journal projectId="prj_1" />);
    await screen.findByText("you");
    expect(container.querySelector(".nj-mark")!.textContent).toBe("◆");
  });

  it("names the model as one even when the server did not say which", async () => {
    // "written by a model" matters more than which model it was.
    serve([entry({ author_kind: "model", model: null })]);
    render(<Journal projectId="prj_1" />);
    expect(await screen.findByText(/model — written by a model/)).toBeTruthy();
  });
});

describe("what a note was about", () => {
  it("names the object, and opens it", async () => {
    serve([entry()]);
    const opened = vi.fn();
    render(<Journal projectId="prj_1" onOpenObject={opened} />);

    await userEvent.click(
      await screen.findByRole("button", { name: "Sleep and reaction time" }));
    expect(opened).toHaveBeenCalledWith("obj_1");
  });

  it("says a note is about the project rather than leaving the line blank", async () => {
    // A blank line reads as a missing value; this note simply is not about a
    // particular thing.
    serve([entry({ object_id: null, object_type: null, object_title: null })]);
    render(<Journal projectId="prj_1" />);
    expect(await screen.findByText("about the project")).toBeTruthy();
  });

  it("falls back to the type when an object has no title", async () => {
    serve([entry({ object_title: null })]);
    render(<Journal projectId="prj_1" onOpenObject={() => {}} />);
    expect(await screen.findByRole("button", { name: "analysis" })).toBeTruthy();
  });

  it("does not offer to open anything when the host cannot", async () => {
    // Rather than a button that does nothing, which is the dead surface this
    // codebase keeps producing.
    serve([entry()]);
    render(<Journal projectId="prj_1" />);
    await screen.findByText("Sleep and reaction time");
    expect(screen.queryByRole("button")).toBeNull();
  });
});

describe("grouped by day", () => {
  it("keeps the server's order rather than sorting again", async () => {
    /*
     * The stream arrives newest first. Re-sorting here would be a second
     * opinion about ordering, and the two would disagree the moment the
     * server's changed.
     */
    const grouped = byDay([
      entry({ id: "a", created_at: at(28, 14) }),
      entry({ id: "b", created_at: at(28, 9) }),
      entry({ id: "c", created_at: at(27, 22) }),
    ]);
    expect(grouped).toHaveLength(2);
    expect(grouped[0][1].map((e) => e.id)).toEqual(["a", "b"]);
    expect(grouped[1][1].map((e) => e.id)).toEqual(["c"]);
  });

  it("does not merge two days that are not adjacent in the stream", () => {
    // Grouping walks the list rather than bucketing it, so an out-of-order
    // stream produces two headings for one day instead of silently reordering.
    const grouped = byDay([
      entry({ id: "a", created_at: at(28, 14) }),
      entry({ id: "b", created_at: at(27, 14) }),
      entry({ id: "c", created_at: at(28, 8) }),
    ]);
    expect(grouped.map(([, e]) => e.map((x) => x.id))).toEqual([["a"], ["b"], ["c"]]);
  });

  it("groups on the reader's own day, not on UTC", () => {
    /*
     * A note written at eleven at night is one a researcher looks for under
     * that evening. Grouping on the stored timestamp would file half of it
     * under tomorrow for anyone east of UTC.
     */
    const evening = at(28, 23);
    const local = new Date(evening).toLocaleDateString(
      undefined, { weekday: "long", day: "numeric", month: "long", year: "numeric" });
    expect(dayOf(evening)).toBe(local);
    // And it is the 28th wherever this runs, which is the point.
    expect(dayOf(evening)).toContain("28");
  });

  it("says nothing rather than 'Invalid Date' for a time it cannot read", () => {
    expect(dayOf("not a date")).toBe("");
  });

  it("shows a heading for each day", async () => {
    serve([
      entry({ id: "a", created_at: at(28, 14) }),
      entry({ id: "c", created_at: at(27, 22) }),
    ]);
    const { container } = render(<Journal projectId="prj_1" />);
    await screen.findByText(/newest first/);
    expect(container.querySelectorAll("section")).toHaveLength(2);
  });
});

describe("when there is nothing", () => {
  it("says where notes come from", async () => {
    // This screen only reads them; a reader who sees an empty page needs to
    // know they are written elsewhere.
    serve([]);
    render(<Journal projectId="prj_1" />);
    expect(await screen.findByText(/Nothing written yet/)).toBeTruthy();
    expect(screen.getByText(/beside an analysis, a figure or a finding/))
      .toBeTruthy();
  });
});

/**
 * The Record reads; the Notebook writes.
 *
 * They are the same `notes` table — `journal.recent` filters on the project and
 * not on `note_kind`, so a notebook page, a daily page and a note left beside
 * an analysis all arrive on this screen. What was missing was the way back: a
 * researcher who had written nothing read a screen describing a feature it
 * gave them no way to reach, which is an empty state that explains instead of
 * helping.
 *
 * A second composer here was the other option and is the wrong one, for the
 * reason `AnalysisContext` gives about validation: two implementations of
 * writing would drift over links, titles and daily pages.
 */
describe("where writing happens", () => {
  it("offers the Notebook when there is nothing written", async () => {
    serve([]);
    const onWrite = vi.fn();
    render(<Journal projectId="prj_1" onWrite={onWrite} />);
    await userEvent.click(await screen.findByRole("button",
      { name: /Open the Notebook/ }));
    expect(onWrite).toHaveBeenCalled();
  });

  it("still offers it once there is something to read", async () => {
    // "Where do I write" is not a question that stops being asked once the
    // first note exists.
    serve([entry()]);
    const onWrite = vi.fn();
    render(<Journal projectId="prj_1" onWrite={onWrite} />);
    await userEvent.click(await screen.findByRole("button",
      { name: /Write in the Notebook/ }));
    expect(onWrite).toHaveBeenCalled();
  });

  it("says nothing about writing when there is nowhere to go", async () => {
    // The door is optional, and a button that leads nowhere is worse than no
    // button: it reads as a broken feature rather than as an absent one.
    serve([]);
    render(<Journal projectId="prj_1" />);
    await screen.findByText(/Nothing written yet/);
    expect(screen.queryByRole("button", { name: /Notebook/ })).toBeNull();
  });
});
