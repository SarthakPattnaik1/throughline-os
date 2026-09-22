/**
 * A search that asks ten databases says who, while it waits (T196).
 *
 * Both fan-out searches wait up to 25 seconds. That is long enough that a
 * generic skeleton reads as a hang, and the one on Find papers was captioned
 * "Asking four databases" while asking ten — a literal that was true when it
 * was written. So the count comes from the list of sources, never from a
 * number in a sentence, and the sources are named rather than counted.
 *
 * The other thing these hold: `waiting` must not look like `down`. "PubMed has
 * not answered yet" and "PubMed did not answer" are different facts, and a
 * researcher who reads the second when the first is true concludes the search
 * is broken.
 */

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { SearchingSources, SourceChip } from "@/components/SourceMark";

afterEach(cleanup);

const TEN = ["arxiv", "biorxiv", "crossref", "doaj", "europepmc",
             "openaire", "openalex", "pubmed", "semanticscholar", "zotero"];

describe("while a fan-out search runs", () => {
  it("counts the sources it was given rather than a number in a sentence", () => {
    render(<SearchingSources names={TEN} />);
    expect(screen.getByText(/Asking 10 databases at once/)).toBeInTheDocument();
    // The defect this replaces: a literal that outlived its truth.
    expect(screen.queryByText(/four databases/)).toBeNull();
  });

  it("recounts when a connector is added or removed", () => {
    const { rerender } = render(<SearchingSources names={TEN} />);
    rerender(<SearchingSources names={[...TEN, "eleventh"]} />);
    expect(screen.getByText(/Asking 11 databases at once/)).toBeInTheDocument();
  });

  it("names every source, so the wait says what is covered", () => {
    render(<SearchingSources names={TEN} />);
    for (const name of TEN) expect(screen.getByText(name)).toBeInTheDocument();
  });

  it("says what happens to whichever is slow", () => {
    render(<SearchingSources names={TEN} deadline={25} />);
    expect(screen.getByText(/answered within 25 seconds/)).toBeInTheDocument();
    expect(screen.getByText(/rather than quietly dropped/)).toBeInTheDocument();
  });

  it("takes the deadline as given rather than hard-coding one", () => {
    render(<SearchingSources names={TEN} deadline={40} />);
    expect(screen.getByText(/within 40 seconds/)).toBeInTheDocument();
  });

  it("calls them repositories when that is what they are", () => {
    render(<SearchingSources names={["zenodo", "dryad"]} kind="repositories" />);
    expect(screen.getByText(/Asking 2 repositories at once/)).toBeInTheDocument();
  });

  it("says something honest when the source list could not be read", () => {
    // The capabilities request can fail; the search still runs.
    render(<SearchingSources names={[]} />);
    expect(screen.getByText(/Asking the databases/)).toBeInTheDocument();
    expect(screen.queryByText(/Asking 0/)).toBeNull();
  });

  it("uses the plural it was given, rather than singularising it", () => {
    // "repositories" minus a trailing s is "repositorie", and this is the
    // branch nobody looks at — the source list's own request failing.
    render(<SearchingSources names={[]} kind="repositories"
                             whenUnknown="the dataset repositories" />);
    expect(screen.getByText(/Asking the dataset repositories/)).toBeInTheDocument();
    expect(screen.queryByText(/repositorie[^s]/)).toBeNull();
  });

  it("is announced to a screen reader, which cannot see a spinner", () => {
    render(<SearchingSources names={TEN} />);
    const status = screen.getByRole("status");
    expect(status).toHaveAttribute("aria-live", "polite");
  });
});

describe("a source that has not answered yet", () => {
  it("does not look like one that failed", () => {
    const { container: waiting } = render(<SourceChip name="pubmed" waiting />);
    expect(waiting.querySelector(".schip")).toHaveAttribute("data-state", "waiting");
    expect(screen.getByText("asking…")).toBeInTheDocument();
    expect(screen.queryByText("did not answer")).toBeNull();
    cleanup();

    const { container: down } = render(<SourceChip name="pubmed" ok={false} />);
    expect(down.querySelector(".schip")).toHaveAttribute("data-state", "down");
    expect(screen.getByText("did not answer")).toBeInTheDocument();
  });

  it("shows no count, because it has not returned one", () => {
    const { container } = render(<SourceChip name="pubmed" waiting count={0} />);
    expect(container.querySelector(".schip-count")).toBeNull();
  });

  it("does not also claim to be rate-limited while it is still being asked", () => {
    render(<SourceChip name="semanticscholar" waiting polite={false} />);
    expect(screen.queryByText("limited")).toBeNull();
  });
});
