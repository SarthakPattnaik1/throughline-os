/**
 * Which page a canvas is being drawn on.
 *
 * SVG charts read CSS variables and follow the theme for free. A canvas
 * cannot: it is painted with literal colours, so every canvas chart has to
 * ask, and has to keep asking — a reader who switches theme with a figure on
 * screen would otherwise keep the palette built for the other background.
 *
 * The precedence is the part worth stating. An explicit `data-theme` stamp is
 * the reader's own choice and wins; the system preference answers only when
 * there is no stamp. Reversed, a researcher who deliberately chose light on a
 * dark machine would be overruled by their operating system, which is exactly
 * the complaint a theme toggle exists to answer.
 */

export function isDarkPage(
  stamp: string | null,
  systemPrefersDark: boolean,
): boolean {
  if (stamp === "dark") return true;
  if (stamp === "light") return false;
  // Any other value is not a choice — an empty attribute, a typo, a value
  // from a future theme this build does not know — so the system decides
  // rather than the string being guessed at.
  return systemPrefersDark;
}

/**
 * The colour a canvas marks a selected datum in: the theme's `--select`.
 *
 * The canvas charts hard-coded `#1443B8` for their selection and hover rings —
 * 8:1 on the light page and 2.3:1 on the dark one, under the 3:1 a graphic that
 * carries meaning needs, so which point was picked was hard to see in the dark
 * theme. `--select` is the theme's own colour for "what I picked", defined for
 * both themes; read at draw time, so a theme switch repaints in the new one.
 */
export function selectionColour(element: Element | null): string {
  if (!element || typeof getComputedStyle !== "function") return SELECT_FALLBACK;
  return getComputedStyle(element).getPropertyValue("--select").trim() || SELECT_FALLBACK;
}

/** The light theme's `--select`, for a canvas drawn before any style exists. */
export const SELECT_FALLBACK = "#355F9D";
