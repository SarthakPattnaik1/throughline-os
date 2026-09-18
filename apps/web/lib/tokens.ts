/**
 * Design tokens — the single source (Part A).
 *
 * Nothing else in the application may hardcode a colour, size, duration or
 * curve. The rule matters most for the one decision the brief calls the single
 * most important visual choice:
 *
 *   **Saturated colour belongs to data. Chrome is near-neutral.**
 *
 * A product that brands its interface with a strong accent ends up with charts
 * that fight the UI. When a visualization is on screen it must be the most
 * colourful thing in view by a wide margin, and the only way to hold that line
 * is to keep chart palettes in a separate namespace that chrome cannot reach.
 *
 * These are exported as TypeScript for canvas and WebGL rendering — which
 * cannot read CSS custom properties — and mirrored into CSS variables in
 * `globals.css` for the DOM. Both derive from here.
 */

export const neutral = {
  light: {
    0: "#FFFFFF", 25: "#FAFAF9", 50: "#F4F4F2", 100: "#E8E8E5",
    200: "#D6D6D2", 300: "#B8B8B3", 400: "#8E8E88", 500: "#6B6B66",
    600: "#4F4F4B", 800: "#2A2A28", 900: "#161615",
  },
  dark: {
    0: "#0A0C0F", 25: "#0E1116", 50: "#14181F", 100: "#1B2028",
    200: "#262C36", 300: "#333A45", 400: "#4C5563", 500: "#6E7787",
    600: "#97A0AE", 800: "#D5DAE2", 900: "#F0F2F5",
  },
} as const;

/**
 * Exactly one accent, used on focus, selection, the active nav item and the
 * primary action. If it appears in more than about 5% of pixels, something has
 * gone wrong.
 */
export const accent = { 500: "#2563EB", 100: "#DBEAFE" } as const;

/** State only. Never decoration. */
export const semantic = {
  /*
   * The CSS variable first, with the light value as the fallback.
   *
   * This constant had no dark counterpart while `globals.css` has defined
   * `--positive: #34D399` for the dark theme all along — so a forest plot
   * drew its estimates in the light-theme green and measured 2.60:1 against
   * the dark page. Reading the variable takes the theme's own value: 10.19:1.
   */
  positive: "var(--positive, #15803D)",
  caution: "#B45309",
  negative: "#B91C1C",
  info: "#0369A1",
} as const;

/**
 * Chart palettes live in their own namespace so chrome cannot borrow from them.
 * Okabe–Ito: eight hues distinguishable under every common form of colour
 * blindness. Past eight, aggregate or facet — adding a ninth colour makes a
 * chart unreadable rather than more informative.
 *
 * The eighth is Okabe–Ito's achromatic slot as a mid neutral, not black
 * (D415). Black measured 1.0:1 on the dark theme's page, so an eighth group
 * vanished from every chart in it — and the 3D network and line charts pick a
 * colour by hashing the group's name, so any group could land there. Canvas
 * and WebGL cannot read a CSS variable, so it is one value legible on both
 * grounds (3.9:1 or better on all six), shared with the exported figures
 * through `throughline_visual.tokens`.
 */
export const categorical = [
  "#0072B2", "#E69F00", "#009E73", "#CC79A7",
  "#56B4E9", "#D55E00", "#F0E442", "#7A7A76",
] as const;

/** Motion (Part D1). Nothing exceeds 900ms; longer needs staging, not duration. */
export const duration = {
  instant: 80,
  micro: 140,
  quick: 200,
  standard: 280,
  layout: 480,
  narrative: 900,
} as const;

export const easing = {
  outExpo: "cubic-bezier(0.16, 1, 0.3, 1)",
  inOutQuart: "cubic-bezier(0.76, 0, 0.24, 1)",
  outQuad: "cubic-bezier(0.25, 0.46, 0.45, 0.94)",
  inQuad: "cubic-bezier(0.55, 0.09, 0.68, 0.53)",
} as const;

/** Cubic-bezier evaluated in JS, for canvas animation where CSS cannot reach. */
export function cubicBezier(p1x: number, p1y: number, p2x: number, p2y: number) {
  return (t: number): number => {
    // Newton-Raphson on x to recover the parameter, then evaluate y. Three
    // iterations is visually exact at 60fps and avoids a lookup table.
    let u = t;
    for (let i = 0; i < 3; i++) {
      const x = bezier(u, p1x, p2x) - t;
      const dx = bezierDerivative(u, p1x, p2x);
      if (Math.abs(dx) < 1e-6) break;
      u -= x / dx;
    }
    return bezier(u, p1y, p2y);
  };
}

const bezier = (t: number, a: number, b: number) =>
  3 * (1 - t) * (1 - t) * t * a + 3 * (1 - t) * t * t * b + t * t * t;

const bezierDerivative = (t: number, a: number, b: number) =>
  3 * (1 - t) * (1 - t) * a + 6 * (1 - t) * t * (b - a) + 3 * t * t * (1 - b);

export const easeOutExpo = cubicBezier(0.16, 1, 0.3, 1);
export const easeInOutQuart = cubicBezier(0.76, 0, 0.24, 1);

/** Type badge colours, keyed to the research object model. */
export const objectColour: Record<string, string> = {
  paper: "#0072B2",
  dataset: "#009E73",
  analysis: "#CC79A7",
  finding: "#E69F00",
  connection: "#56B4E9",
  claim: "#D55E00",
  visualization: "#6E7787",
};

export function colourFor(objectType: string): string {
  return objectColour[objectType] ?? "#8E8E88";
}

/**
 * Reads the theme from the document rather than from React state.
 *
 * Canvas rendering happens outside React's tree and must not re-read on every
 * frame, so this is called once per theme change and the palette cached.
 */
export function palette(dark: boolean) {
  const n = dark ? neutral.dark : neutral.light;
  return {
    page: n[0],
    canvas: n[25],
    surface: n[50],
    border: n[200],
    borderStrong: n[300],
    /**
     * A link between two things, on a canvas.
     *
     * Separate from `border` because it is not one. A border is a hairline
     * dividing regions and is meant to be barely there; a link in a node-link
     * graph is *data* — the caption counts them — and drawing it at border
     * weight made "6 objects · 6 links" a figure showing six objects. On the
     * dark canvas `border` came out at about 1.4:1 against the background,
     * which is invisible rather than quiet.
     *
     * `n[400]` was the first attempt and reached only 2.5:1 — better, and
     * still under the 3:1 that a graphical object carrying meaning needs. The
     * number came from a test rather than from looking, which is the point of
     * having one.
     */
    link: n[500],
    textFaint: n[400],
    textTertiary: n[500],
    textSecondary: n[600],
    text: n[800],
    heading: n[900],
    accent: accent[500],
    accentSoft: accent[100],
    ...semantic,
  };
}

export type Palette = ReturnType<typeof palette>;
