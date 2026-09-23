/**
 * The sign-in gate offers both doors as equal, labelled choices (T135, plan §4.2)
 * and leads with the one this installation needs (T199).
 *
 * The landing page's only button lands a visitor here, and they used to meet
 * "Welcome back" with "Sign in" as the primary and account creation as an
 * underlined link in body text — the walkthrough harness had to find and press
 * that link to get in. Both paths are buttons now. T135 stands.
 *
 * What changed is which one leads. It was read from `localStorage`: whether
 * *this browser* had signed in here before. That is the wrong question, and it
 * failed the ordinary case — a different browser, cleared site data or a
 * private window met "Create your account" on an installation already holding
 * the visitor's own projects, and typing the email they had answered "an
 * account with this email already exists". A dead end two clicks from the
 * front page.
 *
 * `needs_setup` is the right question, and it is already on the status object:
 * it asks the installation rather than the browser.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import Home from "@/app/workspace/page";

/** `needs_setup: false` — this installation already has at least one account. */
const HAS_ACCOUNTS = { needs_setup: false, authenticated: false, user: null };
/** `needs_setup: true` — nothing here yet; the first account is being made. */
const FRESH_INSTALL = { needs_setup: true, authenticated: false, user: null };

function gate(status: Record<string, unknown> = HAS_ACCOUNTS) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input: RequestInfo | URL) => {
    const path = String(input).split("?")[0];
    if (path.endsWith("/api/auth/status")) {
      return { ok: true, status: 200, text: async () => JSON.stringify(status) } as Response;
    }
    return { ok: true, status: 200, text: async () => "{}" } as Response;
  });
  return render(<Home />);
}

beforeEach(() => { window.localStorage.clear(); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("an installation that already has an account", () => {
  it("leads with signing in, whatever this browser remembers", async () => {
    // The browser is blank — the state that used to force "Create your
    // account" onto somebody who owns an account on this very machine.
    gate(HAS_ACCOUNTS);
    const existing = await screen.findByRole("button", { name: "I already have an account" });
    const create = screen.getByRole("button", { name: "Create an account" });
    expect(existing).toHaveAttribute("aria-pressed", "true");
    expect(existing.className).toContain("btn-primary");
    expect(create).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Welcome back");
    // Signing in asks for no display name; offering one is the create form.
    expect(screen.queryByPlaceholderText("Dr Chen")).toBeNull();
  });

  it("still offers creating one, as an equal labelled button", async () => {
    // A genuine second person on a shared machine is one click away, and T135
    // holds: never a link buried in a sentence.
    gate(HAS_ACCOUNTS);
    fireEvent.click(await screen.findByRole("button", { name: "Create an account" }));
    await waitFor(() => expect(screen.getByRole("heading", { level: 1 }))
      .toHaveTextContent("Create your account"));
    expect(screen.getByPlaceholderText("Dr Chen")).toBeInTheDocument();
    expect(screen.queryByText(/New here\?/)).toBeNull();
  });
});

describe("a fresh installation with no account at all", () => {
  it("sets the machine up, with nothing to choose between", async () => {
    // T135's original case, unchanged: there is no sign-in to lead with.
    gate(FRESH_INSTALL);
    expect(await screen.findByRole("heading", { level: 1 }))
      .toHaveTextContent("Set up this machine");
    expect(screen.queryByRole("group", { name: /Sign in or create an account/ })).toBeNull();
  });
});

describe("the gate on a browser that has signed in before", () => {
  it("leads with signing in", async () => {
    window.localStorage.setItem("throughline.account", "1");
    gate();
    const existing = await screen.findByRole("button", { name: "I already have an account" });
    expect(existing).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Welcome back");
  });
});
