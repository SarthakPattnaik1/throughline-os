/**
 * The always-on bar, and the trail it keeps (T195).
 *
 * A rail told you these places existed simply by staying on screen. A single
 * box does not, so it has to answer "where was I" as well as "what do I want
 * to do". These hold the trail to being useful and to being harmless: it
 * replays what it recorded, it collapses repeats, it is capped, and every path
 * through it works when `localStorage` is missing or throws — a private
 * window, blocked site data, or a preview frame.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { KEPT, clearHistory, readHistory, remember } from "@/components/askhistory";
import { OneBar } from "@/components/onebar";
import type { Command } from "@/components/commands";

const PROJECT = "prj_test";

const COMMANDS: Command[] = [
  { id: "src:1", label: "harvest.csv", group: "Source", run: vi.fn() },
];

beforeEach(() => { clearHistory(PROJECT); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); clearHistory(PROJECT); });

describe("the trail", () => {
  it("is empty before anything is asked", () => {
    expect(readHistory(PROJECT)).toEqual([]);
  });

  it("keeps what was asked, newest first", () => {
    remember(PROJECT, { kind: "verb", verbId: "find-papers", argument: "soil",
                        label: "Find papers — soil" });
    remember(PROJECT, { kind: "object", commandId: "src:1", label: "harvest.csv" });
    const trail = readHistory(PROJECT);
    expect(trail.map((e) => e.label)).toEqual(["harvest.csv", "Find papers — soil"]);
  });

  it("collapses the same ask repeated, because a path is not a tally", () => {
    for (let i = 0; i < 4; i++) {
      remember(PROJECT, { kind: "verb", verbId: "find-papers", argument: "soil",
                          label: "Find papers — soil" });
    }
    expect(readHistory(PROJECT)).toHaveLength(1);
  });

  it("treats the same verb with a different subject as a different place", () => {
    remember(PROJECT, { kind: "verb", verbId: "find-papers", argument: "soil", label: "a" });
    remember(PROJECT, { kind: "verb", verbId: "find-papers", argument: "maize", label: "b" });
    expect(readHistory(PROJECT)).toHaveLength(2);
  });

  it("is capped, so it stays scannable", () => {
    for (let i = 0; i < KEPT + 6; i++) {
      remember(PROJECT, { kind: "object", commandId: `c${i}`, label: `thing ${i}` });
    }
    expect(readHistory(PROJECT)).toHaveLength(KEPT);
  });

  it("keeps one project's trail out of another's", () => {
    remember(PROJECT, { kind: "object", commandId: "a", label: "mine" });
    expect(readHistory("prj_other")).toEqual([]);
    clearHistory("prj_other");
  });

  it("survives storage that throws, rather than taking the bar down with it", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    expect(readHistory(PROJECT)).toEqual([]);
    expect(() => remember(PROJECT, { kind: "object", commandId: "a", label: "x" })).not.toThrow();
  });

  it("ignores anything in storage that is not a trail", () => {
    vi.spyOn(Storage.prototype, "getItem").mockReturnValue('{"not":"an array"}');
    expect(readHistory(PROJECT)).toEqual([]);
    vi.spyOn(Storage.prototype, "getItem").mockReturnValue('[{"junk":1},null,"x"]');
    expect(readHistory(PROJECT)).toEqual([]);
  });
});

describe("the dock", () => {
  function dock(props: Partial<React.ComponentProps<typeof OneBar>> = {}) {
    const onVerb = vi.fn();
    render(<OneBar commands={COMMANDS} onVerb={onVerb} size="dock"
                   historyKey={PROJECT}
                   suggestions={[{ label: "Validate x × y", run: vi.fn(), primary: true }]}
                   {...props} />);
    return { input: screen.getByRole("combobox"), onVerb };
  }

  it("shows nothing over the screen until it is reached for", () => {
    dock();
    expect(screen.queryByText("Worth doing now")).toBeNull();
  });

  it("offers what is worth doing now once it is focused", () => {
    const { input } = dock();
    fireEvent.focus(input);
    expect(screen.getByText("Worth doing now")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Validate x × y" })).toBeInTheDocument();
  });

  it("opens on a click too, not only on focus", () => {
    // After running something the input keeps focus while the panel is shut,
    // so a focus-only bar looked dead to anyone reaching for it a second time.
    const { input } = dock();
    fireEvent.focus(input);
    fireEvent.keyDown(input, { key: "Escape" });
    expect(screen.queryByText("Worth doing now")).toBeNull();
    fireEvent.click(input);
    expect(screen.getByText("Worth doing now")).toBeInTheDocument();
  });

  it("puts what was asked into the trail, and goes back there when pressed", () => {
    const { input, onVerb } = dock();
    fireEvent.change(input, { target: { value: "find papers about soil" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onVerb).toHaveBeenCalledTimes(1);

    fireEvent.click(input);
    expect(screen.getByText("Where you have been")).toBeInTheDocument();
    const again = screen.getByRole("button", { name: /Find papers — soil/ });
    fireEvent.click(again);
    expect(onVerb).toHaveBeenCalledTimes(2);
    // Same place, same subject — replayed, not merely re-navigated.
    expect(onVerb.mock.calls[1][0]).toMatchObject({ section: "sources", view: "papers" });
    expect(onVerb.mock.calls[1][1]).toBe("soil");
  });

  it("keeps no trail when it is not given a project to keep one for", () => {
    render(<OneBar commands={COMMANDS} onVerb={vi.fn()} size="dock" />);
    const input = screen.getByRole("combobox");
    fireEvent.change(input, { target: { value: "find papers" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(readHistory(PROJECT)).toEqual([]);
  });

  it("hides the panel while something is being typed, so offers own the space", () => {
    const { input } = dock();
    fireEvent.focus(input);
    expect(screen.getByText("Worth doing now")).toBeInTheDocument();
    fireEvent.change(input, { target: { value: "find" } });
    expect(screen.queryByText("Worth doing now")).toBeNull();
  });
});
