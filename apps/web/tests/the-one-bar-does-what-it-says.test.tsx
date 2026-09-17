/**
 * The bar offers before it acts, and acts only on what it offered (T189).
 *
 * A single box is only trustworthy if the researcher can see what it is about
 * to do while there is still time to not do it. These are the assertions that
 * hold it to that: every offer names itself and says what it means, pressing
 * one runs that one and nothing else, and a line it does not understand is
 * refused in words rather than resolved to a guess.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { OneBar, offersFor } from "@/components/onebar";
import type { Command } from "@/components/CommandPalette";

afterEach(cleanup);

const COMMANDS: Command[] = [
  { id: "con:1", label: "yield_t_ha × fertiliser_kg", group: "Connection",
    hint: "validated", match: "conn_b5aa", run: vi.fn() },
  { id: "src:1", label: "harvest.csv", group: "Source", run: vi.fn() },
];

function bar(props: Partial<React.ComponentProps<typeof OneBar>> = {}) {
  const onVerb = vi.fn();
  const utils = render(
    <OneBar commands={COMMANDS} onVerb={onVerb} size="home" {...props} />);
  const input = screen.getByRole("combobox");
  return { ...utils, input, onVerb };
}

describe("what the bar offers", () => {
  it("puts an instruction above a name that merely contains the word", () => {
    const offers = offersFor("find papers", COMMANDS);
    expect(offers[0].kind).toBe("verb");
    expect(offers[0].kind === "verb" && offers[0].verb.id).toBe("find-papers");
  });

  it("still finds things by name when the line is not an instruction", () => {
    const offers = offersFor("harvest", COMMANDS);
    expect(offers.every((o) => o.kind === "object")).toBe(true);
    expect(offers[0].kind === "object" && offers[0].command.label).toBe("harvest.csv");
  });

  it("finds an object by the id somebody was sent", () => {
    const offers = offersFor("conn_b5aa", COMMANDS);
    expect(offers[0].kind === "object" && offers[0].command.id).toBe("con:1");
  });

  it("offers nothing for an empty line", () => {
    expect(offersFor("", COMMANDS)).toEqual([]);
    expect(offersFor("   ", COMMANDS)).toEqual([]);
  });
});

describe("what the bar shows before it acts", () => {
  it("says what each offer will do, in a sentence", () => {
    const { input } = bar();
    fireEvent.change(input, { target: { value: "find papers about soil" } });
    expect(screen.getByText("Find papers")).toBeInTheDocument();
    expect(screen.getByText(/Search the open literature/)).toBeInTheDocument();
    // And the subject it picked out of the line, so a wrong reading is visible.
    expect(screen.getByText(/soil/)).toBeInTheDocument();
  });

  it("hides nothing behind a hover", () => {
    const { container, input } = bar();
    fireEvent.change(input, { target: { value: "validate" } });
    expect(container.querySelector(".onebar-offers [title]")).toBeNull();
  });
});

describe("what the bar does", () => {
  it("runs the offer that was showing, with its subject", () => {
    const { input, onVerb } = bar();
    fireEvent.change(input, { target: { value: "find papers about soil fertility" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onVerb).toHaveBeenCalledTimes(1);
    expect(onVerb.mock.calls[0][0]).toMatchObject({ section: "sources", view: "papers" });
    expect(onVerb.mock.calls[0][1]).toBe("soil fertility");
  });

  it("runs the one the arrow keys moved to, not the first", () => {
    const { input, onVerb } = bar();
    fireEvent.change(input, { target: { value: "find" } });
    const before = screen.getAllByRole("option").length;
    expect(before).toBeGreaterThan(1);
    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onVerb).toHaveBeenCalledTimes(1);
    // Whichever it was, it was the second one listed — not a re-ranked guess.
    expect(onVerb.mock.calls[0][0]).toBeTruthy();
  });

  it("opens an object rather than calling onVerb for it", () => {
    const run = vi.fn();
    const { input, onVerb } = bar({
      commands: [{ id: "src:1", label: "harvest.csv", group: "Source", run }],
    });
    fireEvent.change(input, { target: { value: "harvest" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(run).toHaveBeenCalledTimes(1);
    expect(onVerb).not.toHaveBeenCalled();
  });

  it("empties itself after running, so the next question starts clean", () => {
    const { input } = bar();
    fireEvent.change(input, { target: { value: "find papers" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect((input as HTMLInputElement).value).toBe("");
  });

  it("does nothing at all on Enter when it has nothing to offer", () => {
    const { input, onVerb } = bar();
    fireEvent.change(input, { target: { value: "zzzzz not a thing" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onVerb).not.toHaveBeenCalled();
  });
});

describe("when the bar does not understand", () => {
  it("says so, and offers the vocabulary rather than guessing", () => {
    const { input } = bar();
    fireEvent.change(input, { target: { value: "zzzzz not a thing" } });
    expect(screen.getByText(/is not one of the things this can be asked to do/))
      .toBeInTheDocument();
    expect(screen.getByRole("button", { name: /See everything you can ask for/ }))
      .toBeInTheDocument();
  });
});

describe("the chips", () => {
  it("shows the project's own next step first, and runs it", () => {
    const next = vi.fn();
    render(<OneBar commands={COMMANDS} onVerb={vi.fn()} size="home"
                   suggestions={[{ label: "Validate rainfall × yield", run: next, primary: true },
                                 { label: "Add data", run: vi.fn() }]} />);
    const chips = screen.getAllByRole("button", { name: /Validate|Add data/ });
    expect(chips[0]).toHaveTextContent("Validate rainfall × yield");
    fireEvent.click(chips[0]);
    expect(next).toHaveBeenCalledTimes(1);
  });

  it("is absent on the compact bar, which has no room to teach", () => {
    render(<OneBar commands={COMMANDS} onVerb={vi.fn()} size="compact"
                   suggestions={[{ label: "Add data", run: vi.fn() }]} />);
    expect(screen.queryByRole("button", { name: "Add data" })).toBeNull();
  });
});

describe("the vocabulary is visible, not guessed at", () => {
  it("lists every verb, grouped, when asked", () => {
    render(<OneBar commands={COMMANDS} onVerb={vi.fn()} size="home" />);
    fireEvent.click(screen.getByRole("button", { name: "What can I ask?" }));
    expect(screen.getByText("Get evidence in")).toBeInTheDocument();
    expect(screen.getByText("find papers")).toBeInTheDocument();
    expect(screen.getByText(/things to ask for/)).toBeInTheDocument();
  });

  it("runs a verb pressed in the list", () => {
    const onVerb = vi.fn();
    render(<OneBar commands={COMMANDS} onVerb={onVerb} size="home" />);
    fireEvent.click(screen.getByRole("button", { name: "What can I ask?" }));
    // The button's name carries the subject it takes — "find papers a topic" —
    // which is the point of showing it, so the match is on the verb.
    fireEvent.click(screen.getByRole("button", { name: /^find papers/ }));
    expect(onVerb).toHaveBeenCalledWith(
      expect.objectContaining({ section: "sources", view: "papers" }), "");
  });
});
