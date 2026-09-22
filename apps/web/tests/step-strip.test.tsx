/**
 * The step strip: every screen names the loop's step and carries its action.
 *
 * The product knew the next step and said it in two places nobody could press,
 * on one screen out of twenty-three (D204, T135). The strip is the structural
 * answer — above the scroll region, on every section — and these tests pin the
 * three things it must get right: it names the step by number and label, it
 * offers the action as a real button except where the researcher already is,
 * and it tells the truth about work still running rather than claiming a
 * step while the machine is mid-pipeline.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { StepStrip } from "@/components/StepStrip";
import { Shell } from "@/components/Shell";
import { loopSteps } from "@/lib/loop";

afterEach(cleanup);

const MAP = {
  counts: { sources: 2, papers: 1, datasets: 1, analyses: 6, contradictions: 0,
            figures: 0, reports: 0, in_flight: 0 },
  connections: { candidate: 5, exploratory: 1 }, findings: {},
  top_connections: [], recommended_next_action: "Validate.",
};
const STEPS = loopSteps(MAP);
const VALIDATE = STEPS[3];
/* The strip carries the whole loop now, not only the step it is on: six ticks
   on the line it already spent, so a researcher can see the path rather than
   only their position on it. */
const SPINE = { steps: STEPS, onGo: vi.fn() };

describe("the step strip", () => {
  it("names the step by its place in the loop and offers its action", () => {
    const onAction = vi.fn();
    render(<StepStrip {...SPINE} step={VALIDATE} index={4} total={6} here={false}
                      actionLabel="Validate consumption × resistance"
                      onAction={onAction} onShowLoop={vi.fn()} working={0} />);
    expect(screen.getByText(/step 4 of 6/i)).toBeInTheDocument();
    expect(screen.getByText(/try to destroy what survived/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /validate consumption × resistance/i }));
    expect(onAction).toHaveBeenCalled();
  });

  it("offers no button where the researcher already is", () => {
    /** The real control is on the page; a second copy of it is the duplicate
     *  this codebase keeps having to remove. */
    render(<StepStrip {...SPINE} step={VALIDATE} index={4} total={6} here
                      actionLabel="Validate consumption × resistance"
                      onAction={vi.fn()} onShowLoop={vi.fn()} working={0} />);
    expect(screen.getByText(/you are here/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /validate/i })).toBeNull();
  });

  it("says the machine is working rather than naming a step mid-pipeline", () => {
    render(<StepStrip {...SPINE} step={VALIDATE} index={4} total={6} here={false}
                      actionLabel="Validate" onAction={vi.fn()} onShowLoop={vi.fn()}
                      working={2} />);
    expect(screen.getByRole("status")).toHaveTextContent(/2 steps are still running/i);
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("says when the loop is complete, and still leads somewhere", () => {
    const onShowLoop = vi.fn();
    render(<StepStrip {...SPINE} step={null} index={0} total={6} here={false} actionLabel={null}
                      onAction={vi.fn()} onShowLoop={onShowLoop} working={0} />);
    expect(screen.getByText(/every step is done/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /open the overview/i }));
    expect(onShowLoop).toHaveBeenCalled();
  });

  it("opens the loop card from its left half", () => {
    const onShowLoop = vi.fn();
    render(<StepStrip {...SPINE} step={VALIDATE} index={4} total={6} here={false}
                      actionLabel="Validate" onAction={vi.fn()} onShowLoop={onShowLoop}
                      working={0} />);
    fireEvent.click(screen.getByRole("button", { name: /step 4 of 6/i }));
    expect(onShowLoop).toHaveBeenCalled();
  });
});

describe("the shell carries the strip above the workspace", () => {
  it("renders the strip before the workspace's scroll region", () => {
    /** Above `<main>`, not inside it: inside, it would scroll away with the
     *  content, which is the defect it exists to end. */
    const { container } = render(
      <Shell section="findings" onSection={vi.fn()} map={null} inspector={null}
             projectName="P" crumbs={[]} onDropFiles={vi.fn()}
             strip={<div className="step-strip" data-testid="strip">strip</div>}>
        <p>content</p>
      </Shell>,
    );
    const strip = screen.getByTestId("strip");
    const main = container.querySelector("main.workspace")!;
    expect(strip.compareDocumentPosition(main) & Node.DOCUMENT_POSITION_FOLLOWING)
      .toBeTruthy();
    expect(main.contains(strip)).toBe(false);
  });

  it("keeps This machine a group of its own, not a research step", () => {
    const { container } = render(
      <Shell section="settings" onSection={vi.fn()} map={null} inspector={null}
             projectName="P" crumbs={[]} onDropFiles={vi.fn()}>
        <p>content</p>
      </Shell>,
    );
    /*
     * Settings is the current section, so This machine is the open group. It
     * used to be pinned in a nav of its own below the scrolling rail; now the
     * five groups are a row and it is the last of them. What has to stay true
     * is the separation, not the mechanism: the machine's entries are reachable
     * and none of the research steps is filed among them.
     */
    const sections = container.querySelector("nav.sectionbar")!;
    expect(sections.getAttribute("aria-label")).toBe("This machine");
    expect(sections.textContent).toContain("Settings");
    /* Chart primitives left this group for Figures: a catalogue of chart kinds
       answers "what could I draw this as", which is a question you have while
       making a figure rather than a property of the installation. */
    expect(sections.textContent).not.toContain("Chart primitives");
    for (const step of ["Findings", "Analyses", "Sources", "Reports"]) {
      expect(sections.textContent).not.toContain(step);
    }
    // Named and never numbered: only some sections are step destinations, so a
    // numbered heading would claim a sequence. The name is the tab's first
    // span; the second is the entry count (T139). Six groups now, around the
    // three master surfaces rather than around verbs.
    const names = [...container.querySelectorAll(".groupbar-tab")]
      .map((h) => h.querySelector("span")?.textContent?.trim());
    expect(names).toEqual(
      ["The project", "Evidence", "Analysis", "Lineage", "Communicate",
       "This machine"]);
    for (const label of names) expect(label).not.toMatch(/^\d/);
  });
});
