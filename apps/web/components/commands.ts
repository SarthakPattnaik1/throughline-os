/**
 * Everything in the project, reachable by name (§69, T197).
 *
 * This was `CommandPalette.tsx`: a modal dialog plus the index it searched.
 * The dialog is gone. It searched this same list through this same `rank` and
 * matched no verbs at all, which made it a strict subset of the bar at the
 * foot of every screen — a second front door onto less. What is left is the
 * part that was doing the work: how a project's sources, runs, connections,
 * findings, reports, figures, sections and standalone pages become a list of
 * named things, and how a typed query is scored against it.
 *
 * The one bar (`onebar.tsx`) shows these under its verb matches, so a line
 * that is not an instruction is still a name search — which is what makes the
 * bar a superset rather than a different thing.
 */

import { Connection, Finding, Source } from "@/lib/api";
import { Section } from "./Shell";

export type Command = {
  id: string;
  label: string;
  /** Where it lives — shown right-aligned so the list reads as a map. */
  group: string;
  hint?: string;
  /**
   * The object's own id, matched literally as well as by name.
   *
   * A run id, a connection id and a finding id all travel: they are in the
   * address bar, in an error the server returned, in a colleague's message.
   * Somebody holding `arun_8f21c4` had no way to turn it back into the run —
   * the palette searched labels only, and no label contains an id.
   */
  match?: string;
  run: () => void;
};

/**
 * Subsequence match, the same rule editors use: the typed letters must appear in
 * order but need not be adjacent, so `rsp` finds `resistance_pct`. Returns a
 * score (lower is better) or null for no match.
 */
function fuzzy(query: string, target: string): number | null {
  if (!query) return 0;
  const q = query.toLowerCase();
  const t = target.toLowerCase();

  const direct = t.indexOf(q);
  // A contiguous run always beats a scattered one, and matching at a word
  // boundary beats matching mid-word.
  if (direct >= 0) return direct === 0 ? 0 : /[\s_×·/-]/.test(t[direct - 1] ?? "") ? 1 : 2 + direct;

  let ti = 0;
  let gaps = 0;
  for (const ch of q) {
    const next = t.indexOf(ch, ti);
    if (next < 0) return null;
    gaps += next - ti;
    ti = next + 1;
  }
  return 40 + gaps;
}

/**
 * How much of a query has to be typed before ids are searched at all.
 *
 * Ids share a prefix per kind — `arun_`, `conn_`, `fnd_`, `src_` — so a
 * one-letter query would match every analysis in the project at once and push
 * the sections and sources somebody was actually reaching for off a list
 * capped at 40. Three characters is the shortest query that is an id fragment
 * rather than a letter.
 */
const ID_QUERY_MIN = 3;

/**
 * Match an id literally: exact, or by prefix. Never by subsequence.
 *
 * `fuzzy` below is a subsequence rule, and a subsequence rule over hex matches
 * nearly every id for nearly every query — typing "ace" would offer half the
 * project. An id is not read, it is pasted or typed from its front, so the two
 * shapes that actually happen are the two this answers.
 */
function idScore(query: string, id: string | undefined): number | null {
  if (!id || query.length < ID_QUERY_MIN) return null;
  const q = query.toLowerCase();
  const t = id.toLowerCase();
  if (t === q) return 0;
  return t.startsWith(q) ? 1 : null;
}

/** The better of the two ways a command can match, or null for neither. */
function score(command: Command, query: string): number | null {
  const byText = fuzzy(query, `${command.label} ${command.group}`);
  const byId = idScore(query, command.match);
  if (byText === null) return byId;
  if (byId === null) return byText;
  return Math.min(byText, byId);
}

/**
 * Score every command against the query and return the survivors, best first.
 *
 * Exported since T189: the one bar shows the same name matches under its verb
 * matches, and two rankers would mean the palette and the bar disagreed about
 * what "rsp" finds.
 */
export function rank(commands: Command[], query: string): Command[] {
  return commands
    .map((c) => ({ c, score: score(c, query) }))
    .filter((m): m is { c: Command; score: number } => m.score !== null)
    .sort((a, b) => a.score - b.score)
    .slice(0, 40)
    .map((m) => m.c);
}

export type PaletteAnalysis = {
  id: string;
  method: string;
  left_variable: string | null;
  right_variable: string | null;
};

export type PaletteArtifact = { id: string; title: string; artifact_type: string };

export type PaletteFigure = { id: string; title: string | null; visual_type: string };

/** A route of its own, as `Shell.tsx` exports it in `PAGES`. */
export type PalettePage = { href: string; label: string; group: string };

/** Underscores are how the domain stores a method or a type, not how anyone
 *  reads one. `analyses.tsx:49` spells it the same way, and the two must
 *  agree or the same run is named twice in one product. */
const readable = (word: string) => word.replace(/_/g, " ");

/** Builds the palette contents from the project's actual state. */
export function buildCommands({ sections, pages = [], sources, connections, findings,
                                analyses = [], reports = [], figures = [], go, open,
                                labels = {} }: {
  sections: Array<{ id: Section; label: string; group: string }>;
  /**
   * The standalone pages (`PAGES` from `Shell.tsx`). Optional, so a caller
   * that has not been wired for them yet gets a palette that is smaller
   * rather than one that is broken.
   */
  pages?: PalettePage[];
  /** Approved display names. A palette full of raw columns is unsearchable. */
  labels?: Record<string, string>;
  sources: Source[];
  connections: Connection[];
  findings: Finding[];
  /** Analysis runs — the object every connection joins back to, and the one
   *  kind of thing the palette could not reach at all. */
  analyses?: PaletteAnalysis[];
  /** Report artifacts (`GET /api/projects/{id}/artifacts`). */
  reports?: PaletteArtifact[];
  /** Saved figures (`GET /api/projects/{id}/visuals`). */
  figures?: PaletteFigure[];
  go: (section: Section) => void;
  open: (section: Section, kind: string, id: string) => void;
}): Command[] {
  return [
    ...sections.map((s) => ({
      id: `s:${s.id}`, label: s.label, group: s.group, run: () => go(s.id),
    })),
    ...pages.map((p) => ({
      id: `pg:${p.href}`,
      label: p.label,
      group: p.group,
      /*
       * A real page change, and it has to be — these are routes, not sections.
       * The workspace navigates with `pushState` inside one page, so `go`
       * cannot reach `/air-ink` at all; a row that called it would highlight,
       * close the palette and leave the researcher exactly where they were,
       * which is §123's failure in its purest form. `assign` rather than
       * `replace` so Back returns to the workspace they came from.
       */
      run: () => window.location.assign(p.href),
    })),
    ...sources.map((s) => ({
      id: `src:${s.id}`,
      match: s.id,
      label: s.title,
      group: "Source",
      hint: s.dataset
        ? `${s.dataset.row_count} rows`
        : s.paper
          ? `${s.paper.page_count} pages`
          : s.ingestion_status,
      run: () => open("sources", "source", s.id),
    })),
    ...analyses.map((a) => ({
      id: `arun:${a.id}`,
      match: a.id,
      /*
       * The pair when there is one, the method when there is not — the rule
       * `nameOf` already follows (`analyses.tsx:41-50`), because a swept run
       * is known by what it tested and a specified one belongs to no pair.
       * The id is never the name: `arun_8f21…` tells a reader nothing about
       * what was asked, which is why it is in `match` and not in `label`.
       */
      label: a.left_variable && a.right_variable
        ? `${readable(a.method)} — `
          + `${labels[a.left_variable] ?? a.left_variable} × `
          + `${labels[a.right_variable] ?? a.right_variable}`
        : readable(a.method),
      group: "Analysis",
      run: () => open("analyses", "analysis", a.id),
    })),
    ...connections.map((c) => ({
      id: `con:${c.id}`,
      match: c.id,
      label: `${labels[c.left_variable] ?? c.left_variable} × `
           + `${labels[c.right_variable] ?? c.right_variable}`,
      group: "Connection",
      hint: c.lifecycle_status,
      run: () => open("connections", "connection", c.id),
    })),
    ...findings.map((f) => ({
      id: `fin:${f.id}`,
      match: f.id,
      label: f.title,
      group: "Finding",
      hint: f.lifecycle_status,
      run: () => open("findings", "finding", f.id),
    })),
    ...reports.map((r) => ({
      id: `art:${r.id}`,
      match: r.id,
      label: r.title,
      group: "Report",
      hint: readable(r.artifact_type),
      run: () => open("reports", "artifact", r.id),
    })),
    ...figures.map((f) => ({
      id: `fig:${f.id}`,
      match: f.id,
      /* A figure need not have been titled, and an untitled row that says
         nothing is a row nobody can pick. The fallback is the one the figures
         list already prints (`savedfigures.tsx:92`), so the same figure is
         called the same thing in both places. */
      label: f.title || `Untitled ${readable(f.visual_type)}`,
      group: "Figure",
      hint: f.title ? readable(f.visual_type) : undefined,
      run: () => open("figures", "artifact", f.id),
    })),
  ];
}
