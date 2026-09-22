/**
 * The edges of the shell belong to the researcher.
 *
 * The inspector was fixed at 360px. A dense evidence panel in it had nowhere to
 * go, and there was no control anywhere in the product to give it room.
 *
 * The rail was the other resizable edge until it became a header row, which is
 * why only two shares are left below.
 *
 * What these guard is the part that is easy to get wrong rather than the part
 * that is easy to see. A divider that only answers a mouse is a §30/Rule 5
 * violation of exactly the kind the project switcher was just fixed for. A
 * stored width that survives across versions of the app is untrusted input, and
 * a bad one could restore a panel of zero width and leave part of the shell
 * unreachable with nothing to drag. And the 1101px threshold now lives in two
 * places — `Shell.tsx` and `globals.css` — which have to agree.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { Shell } from "@/components/Shell";
import { INSPECTOR, WORKSPACE, readLayout, writeLayout } from "@/lib/layout";

/**
 * What the library actually stores: percentage shares that sum to 100.
 *
 * This constant is the whole point of the rewrite below. The first version of
 * these tests invented pixel layouts, wrote them with `writeLayout` and read
 * them back — which round-trips perfectly and agrees with nothing the library
 * produces. `onLayoutChanged` hands back shares like these, the validator was
 * checking them against pixel bounds, every real layout failed, and the panels
 * silently reset to their defaults on every reload. The suite was green
 * throughout; it took opening the page in a browser.
 */
const REAL = { [WORKSPACE]: 74.286, [INSPECTOR]: 25.714 };

/**
 * A panel id an older build wrote and this one no longer has.
 *
 * Not hypothetical: `rail` is what every layout stored before the navigation
 * moved into the header carries, which is why the storage key was bumped. It
 * stands in below for any share allocated to a panel that is not on screen.
 */
const GONE = "rail";

/** Drive the breakpoint, since happy-dom has no real viewport to resize. */
function viewport(wide: boolean) {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: wide,
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    onchange: null,
    dispatchEvent: () => false,
  }));
}

beforeEach(() => {
  try { window.localStorage.clear(); } catch { /* storage may be refused */ }
  viewport(true);
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

function shell() {
  return render(
    <Shell
      section="overview"
      onSection={vi.fn()}
      map={null}
      inspector={<p>context</p>}
     
      projectName="A project"
      crumbs={[]}
      onDropFiles={vi.fn()}
    >
      <p>content</p>
    </Shell>,
  );
}

describe("the shell edges can be moved", () => {
  it("names its divider for what it moves", () => {
    /**
     * "Separator" is what the role says; it is not what a person needs to hear.
     * There is one draggable edge left — the navigation stopped being a column
     * a person could widen when it became a row — and it still has to say what
     * it moves rather than announcing itself as "separator".
     */
    shell();
    expect(screen.getByRole("separator", { name: /Resize the context panel/i }))
      .toBeInTheDocument();
    expect(screen.getAllByRole("separator")).toHaveLength(1);
  });

  it("puts every divider in the tab order", () => {
    /**
     * §30 and Rule 5. The library implements arrow-key resizing, but only for a
     * divider a keyboard can reach in the first place.
     */
    shell();
    for (const divider of screen.getAllByRole("separator")) {
      expect(divider.tabIndex).toBeGreaterThanOrEqual(0);
    }
  });

  it("still renders the navigation, the workspace and the inspector", () => {
    /**
     * The restructure must not lose a region. The navigation is two rows now
     * rather than one column: the groups, whose name is fixed, and the open
     * group's sections, which are named for the group so a screen reader
     * hearing the landmark also hears which stage it is in.
     */
    shell();
    expect(screen.getByRole("navigation", { name: "Areas" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "The project" })).toBeInTheDocument();
    expect(screen.getByRole("main")).toBeInTheDocument();
    expect(screen.getByRole("complementary", { name: "Context inspector" }))
      .toBeInTheDocument();
  });
});

describe("below the breakpoint the inspector is dropped, not crushed", () => {
  it("renders no inspector and no divider on a narrow window", () => {
    /**
     * §117. This used to be `display: none` in a media query, which a flex
     * panel group cannot use — a hidden panel keeps its share of the width and
     * leaves a gap. So it must not be rendered at all, and the divider with it:
     * a divider that resizes nothing is a control that lies.
     */
    viewport(false);
    shell();

    expect(screen.queryByRole("complementary", { name: "Context inspector" }))
      .not.toBeInTheDocument();
    // The inspector's was the only draggable edge, so dropping it leaves none.
    expect(screen.queryAllByRole("separator")).toHaveLength(0);
  });

  it("keeps the navigation and the work itself", () => {
    viewport(false);
    shell();
    expect(screen.getByRole("navigation", { name: "Areas" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "The project" })).toBeInTheDocument();
    expect(screen.getByRole("main")).toBeInTheDocument();
  });
});

describe("a width the researcher chose is remembered", () => {
  it("restores a layout in the shape the library writes", () => {
    /** The regression test for the bug above: this is the real format. */
    writeLayout(REAL);
    expect(readLayout()).toEqual(REAL);
  });

  it("starts from the shipped widths when nothing is stored", () => {
    expect(readLayout()).toBeUndefined();
  });

  it("refuses a share that is not a real percentage", () => {
    /**
     * `localStorage` is editable at the keyboard and outlives any build. What
     * is rejected is nonsense — the pixel limits are enforced by `minSize` and
     * `maxSize` on the panels themselves, which is the only place that can know
     * the viewport.
     */
    writeLayout({ [GONE]: 0, [WORKSPACE]: 74.286, [INSPECTOR]: 25.714 });
    expect(readLayout()).toBeUndefined();

    writeLayout({ [GONE]: -27, [WORKSPACE]: 101.286, [INSPECTOR]: 25.714 });
    expect(readLayout()).toBeUndefined();

    writeLayout({ [GONE]: Number.NaN, [WORKSPACE]: 47, [INSPECTOR]: 25 });
    expect(readLayout()).toBeUndefined();
  });

  it("refuses a set of shares that does not make a whole layout", () => {
    /**
     * A fragment would restore panels that do not fill the window. This is
     * what a layout written by an older build with different panels looks
     * like — plausible, well-formed, and wrong.
     */
    writeLayout({ [GONE]: 20, [WORKSPACE]: 20 });
    expect(readLayout()).toBeUndefined();
  });

  it("tolerates the library's own rounding", () => {
    /** 27.143 + 47.143 + 25.714 is 100.000 only to three places. */
    writeLayout({ [GONE]: 27.143, [WORKSPACE]: 47.143, [INSPECTOR]: 25.714 });
    expect(readLayout()).toBeDefined();
  });

  it("survives nonsense in storage rather than taking the shell down", () => {
    /**
     * Losing a panel width costs nothing. Throwing here would cost the whole
     * interface, for a preference.
     */
    window.localStorage.setItem("throughline.shell-layout", "{not json");
    expect(readLayout()).toBeUndefined();

    window.localStorage.setItem("throughline.shell-layout", "[1,2,3]");
    expect(readLayout()).toBeUndefined();
  });
});
