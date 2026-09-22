/**
 * One control at the right of the topbar, not two.
 *
 * A three-button segmented theme toggle sat beside an avatar that opened a
 * menu, so every screen in the product carried two permanent controls for the
 * two things a researcher touches about once a session — and the toggle spent
 * that permanence on the least consequential choice on screen. The owner read
 * the result as "there are too many options on screen", and this is the part of
 * that which is chrome rather than content.
 *
 * Nothing is taken away: all three theme choices are still one press from
 * anywhere, they are simply a row inside the control that already names who is
 * signed in. These tests hold the two halves of that — that there is exactly
 * one such control, and that everything the two used to offer is still in it.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Shell } from "@/components/Shell";
import { THEME_STORAGE_KEY } from "@/components/Theme";

const USER = { id: "usr_1", email: "chen@lab.local", display_name: "Dr Chen" };

beforeEach(() => {
  document.documentElement.removeAttribute("data-theme");
  try { window.localStorage.clear(); } catch { /* storage may be refused */ }
});
afterEach(cleanup);

function shell() {
  return render(
    <Shell section="overview" onSection={vi.fn()} map={null} inspector={null}
           projectName="A project" crumbs={[]}
           onDropFiles={vi.fn()} account={USER}>
      <p>content</p>
    </Shell>,
  );
}

/** Radix opens on a real key press; a synthetic click on the trigger does not
 *  carry the pointer state it listens for. */
async function openMenu(container: HTMLElement) {
  const trigger = container.querySelector<HTMLElement>(".topbar .acct-trigger")!;
  trigger.focus();
  await userEvent.keyboard("{Enter}");
  return trigger;
}

describe("the topbar carries one control for the account and the theme", () => {
  it("has exactly one control at the right, and no separate theme toggle", () => {
    /** Counted as "everything after the breadcrumb", because that is what the
     *  right-hand side of the topbar is. It used to be counted from the command
     *  bar, which sat between them; that button is gone (T197) — the bar at the
     *  foot of every screen does what it did and more, and two front doors onto
     *  one index is the thing the single box set out to remove. The project
     *  switcher is not in the count: it sits in the breadcrumb, on the object
     *  the crumb is already naming. */
    const { container } = shell();
    const topbar = container.querySelector(".topbar")!;
    const crumbs = topbar.querySelector("[aria-label='Breadcrumb']")!;
    const right = [...topbar.children].filter((element) =>
      crumbs.compareDocumentPosition(element) & Node.DOCUMENT_POSITION_FOLLOWING);

    expect(right).toHaveLength(1);
    expect(right[0].querySelectorAll("button")).toHaveLength(1);
    expect(right[0].querySelector("[aria-haspopup='menu']")).not.toBeNull();
    expect(topbar.querySelector(".theme-toggle")).toBeNull();
  });

  it("wears the signed-in name, so the control says whose session it is", () => {
    const { container } = shell();
    const trigger = container.querySelector(".topbar .acct-trigger")!;
    expect(trigger.textContent).toContain("Dr Chen");
    // The initials as well, which is what survives when the topbar is narrow.
    expect(trigger.querySelector(".am-avatar")?.textContent).toBe("DC");
  });

  it("keeps the breadcrumb beside it, and no longer carries a command bar", () => {
    /** The two controls that were merged were the ones a person does not aim
     *  at. The breadcrumb is one they do, and it stays where it was.
     *
     *  The command bar that used to sit here is gone (T197). It opened a modal
     *  palette that searched the same index through the same ranking and
     *  matched no verbs at all — a strict subset of the bar docked at the foot
     *  of every screen, which now owns ⌘K too. */
    shell();
    expect(screen.getByRole("navigation", { name: "Breadcrumb" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /command bar/i })).toBeNull();
  });
});

describe("opened, it offers everything the two controls used to", () => {
  it("reads out the identity, all three themes, and sign out", async () => {
    const { container } = shell();
    await openMenu(container);

    expect(await screen.findByText("chen@lab.local")).toBeInTheDocument();
    for (const label of ["Dark", "Light", "Match this machine"]) {
      expect(screen.getByRole("menuitemradio", { name: label }), label)
        .toBeInTheDocument();
    }
    expect(screen.getByRole("menuitem", { name: /sign out/i })).toBeInTheDocument();
    // The reassurance is one line, and it is the hairline the sign-out sits
    // under rather than a section of its own.
    expect(screen.getByText(/stays on\s+this machine/i)).toBeInTheDocument();
  });

  it("shows the default as the held choice before anything is stored", async () => {
    /** Dark is the product's own look, and a control that showed no choice at
     *  all would read as a control that has not been used yet. */
    const { container } = shell();
    await openMenu(container);
    expect(await screen.findByRole("menuitemradio", { name: "Dark" }))
      .toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("menuitemradio", { name: "Light" }))
      .toHaveAttribute("aria-checked", "false");
  });

  it("stamps the document and remembers it when Light is chosen", async () => {
    /** Light has to stay one press away because exports always render light,
     *  so a figure composed in dark is one its author cannot see as it will
     *  appear. This is that press. */
    const { container } = shell();
    await openMenu(container);
    await userEvent.click(await screen.findByRole("menuitemradio", { name: "Light" }));

    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");
  });

  it("stays open after a theme is chosen", async () => {
    /** The one thing in this menu somebody might do twice — dark, look, light,
     *  look — so it is the one item that does not close on select. */
    const { container } = shell();
    await openMenu(container);
    await userEvent.click(await screen.findByRole("menuitemradio", { name: "Light" }));
    expect(screen.getByRole("menuitemradio", { name: "Dark" })).toBeInTheDocument();
  });

  it("gives the machine back its own choice", async () => {
    const { container } = shell();
    await openMenu(container);
    await userEvent.click(
      await screen.findByRole("menuitemradio", { name: "Match this machine" }));

    // Not the same state as choosing dark: with no stamp the media query
    // decides, which is the whole point of keeping the option.
    expect(document.documentElement.getAttribute("data-theme")).toBeNull();
  });
});

describe("no account yet", () => {
  it("renders no control at all rather than an empty one", () => {
    /** The shell paints before `/api/auth/status` answers. An avatar with no
     *  name that opens a menu with no identity in it says less than nothing. */
    const { container } = render(
      <Shell section="overview" onSection={vi.fn()} map={null} inspector={null}
             projectName="A project" crumbs={[]}
             onDropFiles={vi.fn()}>
        <p>content</p>
      </Shell>,
    );
    expect(container.querySelector(".acct-trigger")).toBeNull();
  });
});
