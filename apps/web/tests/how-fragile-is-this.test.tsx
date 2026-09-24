/**
 * The fragility panel.
 *
 * The risk with a single number is that it becomes a score: quoted bare, an
 * E-value reads like a quality mark and invites comparison across unrelated
 * results. So these tests hold down that the number never appears without what
 * it means, what it rests on, and its refusal to be read as evidence of
 * causation — and that a method it cannot convert is explained rather than
 * shown as an error or hidden.
 */

import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Fragility } from "@/components/fragility";
import { ApiError, api } from "@/lib/api";

const REPORT = {
  variables: ["rainfall", "yield"],
  // Both sent by the route on every report. The risk ratio is computed by
  // `risk_ratio_from_correlation(0.4)` rather than written by hand — a fixture
  // number nobody derived is how a test comes to describe a payload the
  // product could not produce.
  method: "pearson_correlation",
  risk_ratio: 2.21292046485957,
  estimate: 0.4,
  e_value: 2.6,
  e_value_limit: 1.42,
  headline: 1.42,
  interval_note: "The confidence limit nearest the null is 0.1.",
  assumptions: [
    "The correlation was converted to a risk ratio by d = 2r / sqrt(1 - r^2).",
    "An E-value is conditional on the association being real.",
  ],
  sentence:
    "E-value (the confidence limit nearest the null): 1.42.\n" +
    "An unmeasured confounder would need to be associated with both variables " +
    "by a risk ratio of at least 1.42 each.\n" +
    "This is not evidence that one variable affects the other.",
};

describe("how fragile is this", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("shows the number, labelled, under the sentence", async () => {
    vi.spyOn(api, "get").mockResolvedValue(REPORT as never);

    render(<Fragility connectionId="con_1" />);

    expect(await screen.findByText("1.42")).toBeTruthy();
    // Labelled in words, and glossed on first use (D207): a bare 1.42 is a
    // quantity with no claim attached.
    // The label above the figure, not the server's own sentence lower down.
    expect(screen.getByText("E-value")).toBeTruthy();
    expect(screen.getByText(
      /how much stronger than everything measured an unmeasured cause/))
      .toBeTruthy();
  });

  it("answers the heading with a sentence before it shows a number", async () => {
    /**
     * Item 2.11, and ResultCard's law applied here: the panel answered "how
     * fragile is this?" with a 2.4rem "1.42" and put the sentence that made
     * it mean something underneath, where it reached a reader who had already
     * formed an impression of the number.
     */
    vi.spyOn(api, "get").mockResolvedValue(REPORT as never);

    const { container } = render(<Fragility connectionId="con_1" />);
    await screen.findByText("1.42");

    const first = container.querySelector("section.fragility h2 + p");
    const text = first?.textContent?.trim() ?? "";
    expect(text).not.toMatch(/^[\d.]/);
    expect(text.split(/\s+/).length).toBeGreaterThanOrEqual(8);
    // The number the sentence carries is the server's, said in words rather
    // than recomputed.
    expect(text).toMatch(/1.42 times stronger/);
  });

  it("says a fragile result would not take much, in the same sentence", async () => {
    vi.spyOn(api, "get").mockResolvedValue(
      { ...REPORT, headline: 1.05 } as never);

    render(<Fragility connectionId="con_1" />);

    expect(await screen.findByText(/would be enough to explain this away/))
      .toBeTruthy();
  });

  it("keeps the assumptions closed, behind a summary that says what they are",
     async () => {
    /**
     * Principle 4 — depth may be layered, but a closed summary has to state
     * what is inside it, or the layering is a hide.
     *
     * The summary used to spell the count into its own sentence ("— 2
     * assumptions behind the conversion"). It is the workspace's `Fold` now
     * (T139), so the noun phrase is the label and the count is on
     * `data-count`, drawn by the one CSS rule that draws every count in the
     * product. The behaviour held here is unchanged — closed, and saying how
     * much is inside — only the place the number is written has moved.
     */
    vi.spyOn(api, "get").mockResolvedValue(REPORT as never);

    const { container } = render(<Fragility connectionId="con_1" />);
    await screen.findByText("1.42");

    const details = container.querySelector("details.fold");
    expect(details?.hasAttribute("open")).toBe(false);
    const summary = details?.querySelector("summary");
    expect(summary?.textContent).toMatch(/What this number rests on/);
    expect(summary?.getAttribute("data-count")).toBe("2");
  });

  it("names both variables the confounder would have to touch", async () => {
    vi.spyOn(api, "get").mockResolvedValue(REPORT as never);

    render(<Fragility connectionId="con_1" />);

    expect(await screen.findByText(/rainfall/)).toBeTruthy();
    expect(screen.getByText(/yield/)).toBeTruthy();
  });

  it("never shows the number without refusing a causal reading", async () => {
    vi.spyOn(api, "get").mockResolvedValue(REPORT as never);

    render(<Fragility connectionId="con_1" />);

    await screen.findByText("1.42");
    expect(screen.getByText(/not evidence that one variable affects the other/))
      .toBeTruthy();
  });

  it("carries the assumptions the conversion rests on", async () => {
    vi.spyOn(api, "get").mockResolvedValue(REPORT as never);

    render(<Fragility connectionId="con_1" />);

    expect(await screen.findByText(/converted to a risk ratio/)).toBeTruthy();
    expect(screen.getByText(/conditional on the association being real/))
      .toBeTruthy();
  });

  it("explains a method it cannot convert, rather than showing an error", async () => {
    /**
     * Not every connection is a correlation. A red failure banner would say
     * something is broken, and silence would leave a researcher unsure whether
     * the number was withheld or never existed.
     */
    /*
     * An `ApiError` with the status the endpoint actually raises, which is
     * 422 — "an estimate this cannot convert honestly is a fact about the
     * method", in `app.py`'s own words. The fixture used to be a bare `Error`,
     * which no code path in the product can produce: `api.get` throws
     * `ApiError` for every response it gets and only something like a dropped
     * connection reaches the caller as anything else. That mattered once the
     * panel started telling those two apart.
     */
    vi.spyOn(api, "get").mockRejectedValue(
      new ApiError(422, "not defined for linear_regression"));

    render(<Fragility connectionId="con_1" />);

    await waitFor(() =>
      expect(screen.getByText(/no number is shown rather than a confident one/))
        .toBeTruthy());
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("does not blame the method when the analysis simply has not finished",
     async () => {
    /**
     * The endpoint answers 409 here, with its own sentence: this connection
     * has no completed analysis, so there is no estimate to test the fragility
     * of. Every non-2xx arrived as one `error` and produced the 422 sentence,
     * so a run that was still going told the researcher their *method* could
     * not be converted to a risk ratio — a definite claim about their work
     * that nothing had established. Waiting and being refused are different
     * answers.
     */
    vi.spyOn(api, "get").mockRejectedValue(new ApiError(
      409, "This connection has no completed analysis, so there is no "
           + "estimate to test the fragility of."));

    render(<Fragility connectionId="con_1" />);

    await waitFor(() =>
      expect(screen.getByText(/no completed analysis/)).toBeTruthy());
    expect(screen.queryByText(/not one it can convert to a risk ratio/))
      .toBeNull();
    // A refusal, so no retry: the button would never come back with a number.
    expect(screen.queryByRole("button", { name: /try again|retry/i })).toBeNull();
  });

  it("treats a server fault as a fault, not as a fact about the method",
     async () => {
    // 500 is the one status in this family that means "this failed", and the
    // researcher can act on it by trying again.
    vi.spyOn(api, "get").mockRejectedValue(
      new ApiError(500, "Internal server error"));

    render(<Fragility connectionId="con_1" />);

    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
    expect(screen.queryByText(/not one it can convert to a risk ratio/))
      .toBeNull();
    expect(screen.getByRole("button", { name: /try again|retry/i })).toBeTruthy();
  });

  it("marks a fragile result without turning the number into a pass mark",
     async () => {
    vi.spyOn(api, "get").mockResolvedValue(
      { ...REPORT, headline: 1.05, e_value_limit: 1.05 } as never);

    render(<Fragility connectionId="con_1" />);

    const value = await screen.findByText("1.05");
    expect(value.getAttribute("data-fragile")).toBe("yes");
  });

  it("does not mark a robust result as fragile", async () => {
    vi.spyOn(api, "get").mockResolvedValue(
      { ...REPORT, headline: 4.2, e_value_limit: 4.2 } as never);

    render(<Fragility connectionId="con_1" />);

    const value = await screen.findByText("4.20");
    expect(value.getAttribute("data-fragile")).toBe("no");
  });

  it("says a request failed rather than blaming the method", async () => {
    /**
     * The failure and the refusal were one branch, so a server that was down,
     * a deleted connection or a dropped network all produced "this
     * connection's method is not one it can convert to a risk ratio honestly"
     * — a definite statement about the researcher's own analysis that this
     * screen had never established.
     *
     * The two are different in what a reader should do next, which is the
     * whole reason to tell them apart: a failure is worth retrying and a
     * method that cannot be converted is not.
     */
    vi.spyOn(api, "get").mockRejectedValue(new Error("gateway timed out"));

    render(<Fragility connectionId="con_1" />);

    await waitFor(() => expect(screen.getByText(/gateway timed out/)).toBeTruthy());
    expect(screen.queryByText(/not one it can convert to a risk ratio/))
      .toBeNull();
    // Recoverable, and the panel has to say so.
    expect(screen.getByRole("button", { name: /try again|retry/i })).toBeTruthy();
  });

  it("blames the method rather than a request when nothing failed", async () => {
    // The other half of the same split: a refusal must not be dressed as an
    // error, which would invite a retry that can never succeed.
    vi.spyOn(api, "get").mockResolvedValue({ id: "con_1", method: "x" } as never);

    render(<Fragility connectionId="con_1" />);

    await waitFor(() =>
      expect(screen.getByText(/not one it can convert to a risk ratio/))
        .toBeTruthy());
    expect(screen.queryByRole("button", { name: /try again|retry/i })).toBeNull();
  });

  it("says no number rather than crashing on a body without one", async () => {
    /**
     * Found by the connection screen's own tests, whose fixtures answer every
     * `api.get` with connection data: this panel read the number off one of
     * them and took the whole screen down. In the product the same shape
     * arrives from a response that changed underneath a running client.
     */
    vi.spyOn(api, "get").mockResolvedValue({ id: "con_1", method: "x" } as never);

    render(<Fragility connectionId="con_1" />);

    await waitFor(() =>
      expect(screen.getByText(/no number is shown rather than a confident one/))
        .toBeTruthy());
  });
});


describe("the step between the formula and the number", () => {
  it("shows the risk ratio the E-value is computed from", async () => {
    /*
     * The assumptions name the conversion — d = 2r / sqrt(1 - r^2), then
     * RR = exp(0.91 d) — and the headline is its consequence. The ratio in
     * between was sent and never shown, so a reader could see the formula and
     * the answer and could check neither.
     */
    vi.spyOn(api, "get").mockResolvedValue(REPORT as never);
    render(<Fragility connectionId="conn_1" />);

    expect(await screen.findByText(/which is a risk ratio of/)).toBeTruthy();
    expect(screen.getByText(/pearson correlation estimate of 0\.400/))
      .toBeTruthy();
  });

  it("shows less, not nothing, when a report lacks the ratio", async () => {
    // Guarded like the rest of the screen: one missing field must not take
    // the whole panel down, which is what an unguarded `.toPrecision` did.
    const without = { ...REPORT, risk_ratio: undefined };
    vi.spyOn(api, "get").mockResolvedValue(without as never);
    render(<Fragility connectionId="conn_1" />);

    expect(await screen.findByText(/What this number rests on/)).toBeTruthy();
    expect(screen.queryByText(/which is a risk ratio of/)).toBeNull();
  });
});
