"use client";

/**
 * The command bar (§69), as far as it can honestly go without a model provider.
 *
 * §69 wants natural-language intent parsing — "compare the two cohorts" resolving
 * to an analysis. That needs a model, and there isn't one configured, so this
 * does not pretend to. What it *does* do is real: everything in the project is
 * reachable by name in two keystrokes, and the palette says plainly at the
 * bottom that intent parsing is not available yet (§123).
 *
 * The value is navigational, and it is not small. A researcher with forty
 * connections should never scroll a table to find `resistance_pct`.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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

export function CommandPalette({ open, onClose, commands }: {
  open: boolean;
  onClose: () => void;
  commands: Command[];
}) {
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);

  /*
   * The query, kept in a ref that is written synchronously wherever it changes.
   *
   * Neither React state nor the DOM input value is trustworthy at the instant a
   * key is handled: state has not committed yet, and the input is controlled so
   * its value only catches up on commit. Typing "findings" and pressing Enter in
   * the same frame therefore ran the command list as it stood before any typing
   * — return opened Overview. This ref is the one thing that is correct
   * immediately, so every keyboard decision reads it.
   */
  const queryRef = useRef("");
  const setQueryNow = useCallback((next: string) => {
    queryRef.current = next;
    setQuery(next);
  }, []);
  const listRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const dialogRef = useRef<HTMLDialogElement>(null);

  const matches = useMemo(() => rank(commands, query), [commands, query]);

  useEffect(() => { setActive(0); }, [query]);
  /*
   * Start empty every time. The palette kept its last query across a close
   * and a reopen, so the next thing typed was appended to it — a second search
   * for an id landed on "airarun_…" and offered nothing, which reads as the
   * object not existing (T136). A jump bar is opened to ask a new question.
   */
  useEffect(() => { if (!open) setQuery(""); }, [open]);

  useEffect(() => {
    if (!open) return;
    setQueryNow("");
    setActive(0);
    // Focus immediately, and again after paint. The first call covers the
    // normal case; the retry covers the window in which the element exists but
    // the document has not settled.
    inputRef.current?.focus({ preventScroll: true });
    const raf = requestAnimationFrame(() => inputRef.current?.focus({ preventScroll: true }));
    return () => cancelAnimationFrame(raf);
  }, [open, setQueryNow]);

  /*
   * Open and close the element itself, rather than mounting and unmounting it.
   *
   * `showModal()` is what puts the palette in the top layer, makes the rest of
   * the document inert, traps Tab inside it, and hands focus back to whatever
   * had it when the dialog closes. None of that is available to a `<div>`,
   * however it is positioned.
   */
  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    else if (!open && dialog.open) dialog.close();
  }, [open]);

  /*
   * Escape, through the element's own event.
   *
   * `preventDefault` because the browser would otherwise close the dialog
   * directly, leaving React's `open` prop still true and the two out of step —
   * the palette would be invisible and the parent would think it was showing.
   * Closing goes through `onClose` so state stays the source of truth.
   */
  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    const cancel = (event: Event) => { event.preventDefault(); onClose(); };
    dialog.addEventListener("cancel", cancel);
    return () => dialog.removeEventListener("cancel", cancel);
  }, [onClose]);

  /*
   * Navigation keys are handled on the document, not on the input.
   *
   * Binding them to the input assumes focus landed there, and focus is not
   * guaranteed — an unfocused document, a browser that defers it, an extension
   * that steals it. When that assumption failed, arrow keys and Enter silently
   * did nothing, which is the worst possible failure for a keyboard surface:
   * the palette looked fine and simply ignored you. The document always gets
   * the event.
   */
  useEffect(() => {
    if (!open) return;
    function onKey(event: KeyboardEvent) {
      // Escape is handled by the dialog's own `cancel` event below, not here.
      // Preventing the keydown would suppress `cancel` and leave the two
      // mechanisms disagreeing about whether the palette had closed.
      // Wrap against the live list for the same reason Enter does.
      const count = rank(commands, queryRef.current).length;
      if (event.key === "ArrowDown" || (event.key === "n" && event.ctrlKey)) {
        event.preventDefault();
        setActive((i) => (count ? (i + 1) % count : 0));
        return;
      }
      if (event.key === "ArrowUp" || (event.key === "p" && event.ctrlKey)) {
        event.preventDefault();
        setActive((i) => (count ? (i - 1 + count) % count : 0));
        return;
      }
      if (event.key === "Enter") {
        event.preventDefault();
        /*
         * Rank against the input's live value, not against `matches`.
         *
         * React rebinds this listener only after a commit, so typing and
         * pressing Enter within the same frame — which is simply how fast
         * people type — ran the command list from *before* the keystrokes.
         * Typing "findings" and hitting return opened Overview. The DOM value
         * is the truth at the moment the key is pressed; nothing else is.
         */
        const live = rank(commands, queryRef.current);
        const chosen = live[Math.min(active, live.length - 1)];
        if (chosen) { onClose(); chosen.run(); }
        return;
      }

      // If focus never reached the input, typing would otherwise vanish. Route
      // printable characters into the query and pull focus back, so the palette
      // is usable even when the browser declined to focus it.
      if (document.activeElement === inputRef.current) return;
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      if (event.key === "Backspace") {
        event.preventDefault();
        setQueryNow(queryRef.current.slice(0, -1));
        inputRef.current?.focus({ preventScroll: true });
        return;
      }
      if (event.key.length === 1) {
        event.preventDefault();
        setQueryNow(queryRef.current + event.key);
        inputRef.current?.focus({ preventScroll: true });
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
    // Rebinding on every change of `matches`/`active` is deliberate. Reading
    // them through refs instead looks cheaper and is how this was written
    // first, but the listener then acted on whatever the refs held at the last
    // commit it happened to observe — Enter opened the first unfiltered command
    // rather than the highlighted one. Correctness over a saved rebind.
  }, [open, onClose, commands, active, setQueryNow]);

  // Keep the highlighted row in view when arrowing past the fold.
  useEffect(() => {
    listRef.current
      ?.querySelector<HTMLElement>('[data-active="true"]')
      ?.scrollIntoView({ block: "nearest" });
  }, [active]);

  return (
    /*
     * A real `<dialog showModal()>`, not a positioned `<div>`.
     *
     * The markup said `role="dialog" aria-modal="true"`, and `aria-modal` is a
     * promise that everything outside the dialog is inert. Nothing here kept
     * it: there was no Tab trap, so Tab walked straight out of the palette into
     * the page behind it and kept going through a rail the user could no longer
     * see; focus was never returned to whatever opened it; and the background
     * stayed scrollable and reachable. Assistive technology was being told one
     * thing while the keyboard did another — the same shape of defect as the
     * project switcher claiming `role="menu"` and answering no arrow key.
     *
     * Native rather than a library, and rather than hand-rolling the trap.
     * `ConfirmDialog` already makes this argument in this codebase and it holds
     * here for the same four reasons: the focus trap, the inert background, the
     * top-layer stacking that no `z-index` can beat, and Escape. Two modal
     * surfaces, one mechanism.
     *
     * `role` and `aria-modal` are dropped rather than kept: a modal `<dialog>`
     * carries both implicitly, and repeating them by hand is how they drift out
     * of step with what the element is actually doing.
     */
    <dialog
      ref={dialogRef}
      className="palette-dialog"
      aria-label="Command bar"
      // The backdrop is `::backdrop` now, so a click outside lands on the
      // dialog element itself rather than on a wrapper.
      onMouseDown={(event) => { if (event.target === dialogRef.current) onClose(); }}
    >
      <div className="palette" onMouseDown={(e) => e.stopPropagation()}>
        <input
          ref={inputRef}
          className="palette-input"
          value={query}
          onChange={(e) => setQueryNow(e.target.value)}
          /* Names what it indexes, all seven kinds of it. It used to say
             "source, connection, finding or section", which was true when the
             palette held those four and then quietly became a list of three of
             the seven things it searches — a control that under-describes
             itself is the mirror of §123, and the cost is that nobody types an
             analysis id into a box that never claimed to know one. */
          placeholder="Jump to a section, source, connection, finding, analysis, report, figure or page…"
          aria-label="Search the project"
          aria-controls="palette-list"
          aria-activedescendant={matches[active] ? `cmd-${matches[active].id}` : undefined}
        />

        <div className="palette-list" id="palette-list" role="listbox" ref={listRef}>
          {matches.length === 0 && (
            <div className="palette-none">
              Nothing in this project matches “{query}”.
            </div>
          )}
          {matches.map((command, index) => (
            <div
              key={command.id}
              id={`cmd-${command.id}`}
              role="option"
              aria-selected={index === active}
              data-active={index === active}
              className="palette-row"
              onMouseEnter={() => setActive(index)}
              onClick={() => { onClose(); command.run(); }}
            >
              <span className="palette-label">
                {command.label}
                {command.hint && <em>{command.hint}</em>}
              </span>
              <span className="palette-group">{command.group}</span>
            </div>
          ))}
        </div>

        <div className="palette-foot">
          <span><kbd>↑</kbd><kbd>↓</kbd> move · <kbd>↵</kbd> open · <kbd>esc</kbd> close</span>
          <span>Navigation only — asking questions in words needs a model provider.</span>
        </div>
      </div>
    </dialog>
  );
}

/**
 * The rows the palette indexes, beyond the three it already had.
 *
 * Structural subsets, declared here rather than imported: the palette needs a
 * name and an id, and demanding `AnalysisRunRow` in full would say a caller
 * must have fetched twenty fields to offer one line in a list. Every one of
 * these is satisfied by a row `page.tsx` already holds — `AnalysisRunRow`,
 * `ArtifactSummary` and `savedfigures.tsx`'s `SavedFigure` each match
 * structurally — so nothing is duplicated and no new request is implied.
 */
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
