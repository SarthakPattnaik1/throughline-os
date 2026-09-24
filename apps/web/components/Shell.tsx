"use client";

/**
 * The application shell (§65, §66).
 *
 *   left   — the loop, one stage open at a time
 *   top    — breadcrumb, the global command bar, and one control for the
 *            account and the theme
 *   center — the current workspace
 *   right  — the context inspector
 *
 * The rail shows live counts because §70 wants the project's state legible at a
 * glance, and a nav item that never shows a number teaches nothing.
 *
 * **One stage at a time (T139).** All twenty-six entries used to be on screen
 * at once, five headings and twenty-six rows down an 848 px rail, and the owner
 * read the result as "there are too many options on screen". Nothing is
 * removed: the rail is an accordion whose five headings are always visible and
 * always say how many entries they hold, and only the group holding the current
 * section is expanded. That is rule 4 of `docs/THE_LOOP_IS_THE_SHELL.md`
 * applied to navigation — a capability may sit inside a disclosure whose closed
 * summary states what is inside it, and may never sit behind a menu. A heading
 * that names its stage and says how many entries it holds is the first of
 * those, not the second.
 *
 * An expansion the researcher makes by hand is transient: it is remembered
 * against the section it was made from, so choosing an entry — or arriving
 * anywhere else — hands the rail back to the section's own group. There is no
 * effect and no stored state; see `openGroup` below.
 */

import {
  DragEvent, ReactElement, ReactNode, useCallback, useState,
  useSyncExternalStore,
} from "react";
import * as Menu from "@radix-ui/react-dropdown-menu";
import { Group, Panel, Separator } from "react-resizable-panels";
import { DiscoveryMap, api } from "@/lib/api";
import {
  INSPECTOR, INSPECTOR_DEFAULT, INSPECTOR_MAX, INSPECTOR_MIN,
  RAIL, RAIL_DEFAULT, RAIL_MAX, RAIL_MIN, WORKSPACE,
  readLayout, writeLayout,
} from "@/lib/layout";
import { SignedInUser } from "./AccountMenu";
import { BrandMark } from "./BrandMark";
import { THEME_CHOICES, THEME_LABEL, useThemeChoice } from "./Theme";
import {
  IconAnalyses, IconCompare, IconConnections, IconData, IconDiscover,
  IconFigures, IconFindings, IconGallery, IconGraph, IconHand, IconLiterature,
  IconLogout, IconNotebook, IconOverview, IconPatterns, IconReports, IconSearch,
  IconSettings, IconSources, IconUser,
} from "./icons";

export type Section =
  | "board"
  | "overview" | "sources" | "variables" | "search"
  | "discover" | "compare" | "patterns" | "connections" | "findings"
  | "analyses" | "graph" | "embedding"
  | "reports" | "figures" | "gallery" | "notebook" | "journal" | "activity"
  | "literature"
  | "datasearch" | "readfigure" | "settings";

export type Crumb = { label: string; onClick?: () => void };

type RailItem = {
  id: Section; label: string; count?: keyof CountMap;
  /**
   * One clause on what the entry is for, shown beside the label when the rail
   * is wide enough to carry it (see `.rail-note`). Three consecutive one-word
   * labels sharing one glyph — Notebook, Journal, Activity — told a researcher
   * their names and nothing else; the distinction lived in code comments.
   */
  note?: string;
};

/*
 * Five groups. The first four are kinds of screen a researcher uses in the
 * order the work happens — the project itself, gathering, discovering and
 * testing, communicating — and the fifth is this machine. Groups are named,
 * never numbered: only four of the twenty-three sections are destinations of
 * a loop step, and a numbered eyebrow over Compare or the research graph would
 * claim a sequence the screen is not part of. The loop's numbering lives in
 * the step strip, which knows the project's state (T135).
 *
 * "Research" used to hold a canvas, a dashboard, two object lists and four
 * tools under one word. Splitting the two whole-project surfaces out is the
 * only structural change; every id, label, icon, count and within-group order
 * is unchanged, so no address, saved link or palette result changes meaning.
 */
const GROUPS: Array<{ label: string; items: RailItem[] }> = [
  {
    label: "The project",
    items: [
      { id: "overview", label: "Overview" },
      /*
       * §4 calls the workboard "the central operating surface of the product"
       * and §109 puts it at Phase 0. It was never built, so every object a
       * project accumulated lived in a list and never in a place.
       */
      { id: "board", label: "Workboard" },
    ],
  },
  {
    /*
     * The evidence surface — UI_01's family: what this project has read and
     * holds, and the claim-testing that happens against it.
     *
     * The four ways of getting something into the library — searching it,
     * finding papers, finding data, digitising a figure — were four entries
     * beside it, as though each were a peer of the library. Every one of them
     * ends in a source landing there, so they are views of Sources now and the
     * rail is four shorter.
     */
    label: "Evidence",
    items: [
      { id: "sources", label: "Sources", count: "sources" },
      { id: "variables", label: "Variables" },
      /*
       * Compare is UI_01 itself, reached through its "Paper ↔ dataset"
       * comparison. It belongs with what it compares rather than under a verb
       * of its own.
       */
      { id: "compare", label: "Compare" },
    ],
  },
  {
    /*
     * The analysis surface — UI_02's family: how runs are made and read.
     *
     * Discovery is here rather than with the relationships it eventually
     * produces, because what it produces first is runs: a sweep proposes
     * candidates and an analysis tests one. The rail's order is the work's
     * order, and `rail-follows-the-work` holds it to that.
     */
    label: "Analysis",
    items: [
      { id: "discover", label: "Discovery" },
      /*
       * One entry, three readings. The pattern sweep and the embedding space
       * were sections beside this one, and both answer a question *about this
       * project's analyses* — which pairs the sweep proposed, and how the runs
       * sit relative to each other. A researcher wanting either had to already
       * know that "Patterns" did not mean "the patterns in my data" and that
       * "Embedding space" was not a setting. They are views of Analyses.
       */
      { id: "analyses", label: "Analyses", count: "analyses" },
    ],
  },
  {
    /*
     * The lineage surface — UI_03's family: what relates to what, and what it
     * rests on. Analyses, then connections, then findings is the data model's
     * own order — a finding is assembled `from_connections`, and every
     * connection joins to an analysis run.
     */
    label: "Lineage",
    items: [
      { id: "connections", label: "Connections", count: "connections" },
      { id: "findings", label: "Findings", count: "findings" },
      { id: "graph", label: "Research graph" },
    ],
  },
  {
    /*
     * Writing, after the work it writes about.
     *
     * "Chart primitives" is a view of Figures now rather than an entry under
     * *This machine*: a catalogue of chart kinds is the answer to "what could
     * I draw this as", which is a question you have while making a figure, not
     * a property of the installation. And the journal and the activity log are
     * one Record with two readings — what was written, and what was done —
     * because they were two entries answering one question about the same
     * project.
     */
    label: "Communicate",
    items: [
      { id: "reports", label: "Reports", count: "reports" },
      { id: "figures", label: "Figures", count: "figures" },
      { id: "notebook", label: "Notebook", note: "your pages, and what they link to" },
      { id: "journal", label: "Record", note: "everything written and done, in order" },
    ],
  },
  {
    /* One item now: the chart catalogue was never a property of this machine. */
    label: "This machine",
    items: [
      { id: "settings", label: "Settings" },
    ],
  },
];

/** The group rendered as the rail's pinned footer. */
const MACHINE = "This machine";

/**
 * Pages that are routes of their own rather than sections of the workspace.
 *
 * Both existed and neither was linked from anywhere — a researcher could only
 * reach them by typing the URL, so the largest and most carefully built part of
 * this codebase was, in practice, unreachable from the product.
 *
 * They sit under "This machine" rather than in the research groups because
 * that is what they are: one asks whether hand tracking works on this camera in
 * this room, the other is where drawing in the air can be tried. Neither is a
 * step in a piece of research, and filing them between Findings and Reports
 * would say they were.
 *
 * The spatial catalogue was a third instance of the same defect, found by a
 * guard that now walks from the landing page and reports anything nobody can
 * click to. It belongs here on the same reasoning as the other two: it draws
 * every catalogued chart against generated shapes so that "this renders" is
 * checkable rather than asserted, and no project is involved. It is
 * deliberately not filed beside Figures — the Figures screen chooses a chart
 * from the shape of the data and says why, and a browsable catalogue sitting
 * next to it would read as an alternative way of choosing, which is the habit
 * §10 exists to discourage.
 */
const MACHINE_PAGES: Array<{ href: string; label: string; note: string }> = [
  { href: "/charts-3d", label: "Spatial charts",
    note: "Every catalogued chart, drawn — and what each one cannot show" },
  { href: "/gesture-check", label: "Check hand tracking",
    note: "Does the camera see your hands, and how quickly" },
  { href: "/air-ink", label: "Draw in the air",
    note: "Marking up a figure by hand" },
];

/** Flattened for the command palette, which needs the group name too. */
export const SECTIONS = GROUPS.flatMap((g) =>
  g.items.map((i) => ({ id: i.id, label: i.label, group: g.label })));

/**
 * The three machine pages, for the command palette (plan §4.3.6).
 *
 * Deliberately a second export rather than three more rows in `SECTIONS`, and
 * the reason is what a caller has to *do* with one. A section is a view of
 * this page, reached by calling `onSection`; these are routes of their own,
 * reached by a real page load. Folding them together would hand the palette a
 * list whose entries need two different mechanisms and no way to tell which —
 * and `tests/rail-follows-the-work.test.ts` reads `SECTIONS` as the rail's own
 * order and asserts that no id in it is `charts-3d`, which is right to.
 *
 * The rail already links to all three (`MACHINE_PAGES`, and the links are real
 * `<a>`s so a new tab still works). This is the second door: 26 rail rows do
 * not fit 848 px, and somebody who reaches for ⌘K should not have to know
 * which of them scrolled off the bottom.
 */
export const PAGES: Array<{ href: string; label: string; group: string }> =
  MACHINE_PAGES.map((page) => ({
    href: page.href, label: page.label, group: MACHINE,
  }));

/**
 * How many entries a group holds, drawn beside its name.
 *
 * A collapsed heading has to say what is behind it or it is a menu. "This
 * machine" counts its three plain links too, because from the rail they are
 * rows exactly like the other two.
 */
const ENTRY_COUNT: Record<string, number> = Object.fromEntries(
  GROUPS.map((group) => [
    group.label,
    group.items.length + (group.label === MACHINE ? MACHINE_PAGES.length : 0),
  ]));

/** Which group a section is filed under. */
const GROUP_OF: Record<string, string> = Object.fromEntries(
  GROUPS.flatMap((group) => group.items.map((item) => [item.id, group.label])));

type CountMap = { sources: number; connections: number; findings: number;
                  analyses: number; figures: number; reports: number };

/**
 * One icon per section.
 *
 * Kept in a map beside the groups rather than on each item so that a section
 * added without an icon is a visible gap here rather than a silently
 * unillustrated row in the rail.
 */
const ICONS: Record<Section, (p: { size?: number }) => ReactElement> = {
  board: IconGallery, overview: IconOverview, sources: IconSources,
  // Reuses the sources glyph: variables are what the sources turned out to
  // contain, and a second glyph for the same idea makes a sidebar harder to
  // scan rather than easier.
  variables: IconSources,
  search: IconSearch,
  literature: IconLiterature, datasearch: IconData, discover: IconDiscover,
  compare: IconCompare, patterns: IconPatterns, connections: IconConnections,
  findings: IconFindings, analyses: IconAnalyses, graph: IconGraph,
  // Reuses the graph icon: both are 'the corpus as a shape', and inventing
  // a second glyph for the same idea makes a sidebar harder to scan.
  embedding: IconGraph,
  reports: IconReports, figures: IconFigures, gallery: IconGallery,
  // Reuses the figures glyph, and the reuse is the argument: this screen and
  // that one operate on the same object from opposite directions — one draws a
  // chart from data, this one recovers data from a chart. A different glyph
  // would imply a different kind of thing.
  readfigure: IconFigures,
  notebook: IconNotebook,
  // Reuses the notebook glyph: they are the same notes read two ways, and a
  // second glyph would suggest two different kinds of thing.
  journal: IconNotebook,
  // Reuses the journal glyph: both are the project's own record read in
  // order — one of what was written, one of what was done — and a second
  // glyph for the same idea makes a sidebar harder to scan, which is the
  // reasoning `variables` already follows above.
  activity: IconNotebook,
  settings: IconSettings,
};

/**
 * Whether there is room for the context panel at all.
 *
 * §117 is desktop-first, and below 1100px the inspector is dropped rather than
 * crushed — the same threshold the stylesheet used when this was a CSS grid.
 * It has to be answered in JavaScript now, because a flex panel that is hidden
 * with CSS still holds its share of the width.
 *
 * `useSyncExternalStore` rather than `useEffect`, so React reads the real
 * viewport on the first client render instead of painting the desktop layout
 * and correcting it a frame later. The server snapshot is `true` because the
 * export is prerendered with no viewport to measure, and desktop-first is the
 * documented default.
 */
const WIDE = "(min-width: 1101px)";

function useRoomForInspector(): boolean {
  return useSyncExternalStore(
    (notify) => {
      if (typeof window === "undefined") return () => {};
      const query = window.matchMedia(WIDE);
      query.addEventListener("change", notify);
      return () => query.removeEventListener("change", notify);
    },
    () => window.matchMedia(WIDE).matches,
    () => true,
  );
}

export function Shell({
  section, onSection, map, children, inspector, rail, projectName,
  crumbs, onDropFiles, projectMenu, account, strip,
}: {
  section: Section;
  onSection: (s: Section) => void;
  map: DiscoveryMap | null;
  children: ReactNode;
  inspector: ReactNode;
  /**
   * The left column of the dense workbench: the DATA the screen is working
   * from, not navigation.
   *
   * The rail that used to live here was the section list, and it moved into the
   * header. What §08's workbench family puts on the left is a different thing —
   * this project's sources, the variables in play, the family of runs — and a
   * screen that has none of that renders none of it and gets the width back.
   */
  rail?: ReactNode;
  /**
   * The header's bar, when the caller has one.
   *
   * Optional so a mounting site that has not been wired for it still gets the
   * palette button rather than a header with a hole in it.
   */
  bar?: ReactNode;
  projectName: string;
  crumbs: Crumb[];
  onDropFiles: (files: FileList) => void;
  /** The project switcher. Rendered here so the topbar owns its layout. */
  projectMenu?: ReactNode;
  /** Who is signed in, for the one control at the right of the topbar. */
  account?: SignedInUser;
  /**
   * The step strip (`StepStrip`), rendered above the workspace's scroll
   * region so the loop's next action cannot be scrolled out of sight.
   */
  strip?: ReactNode;
}) {
  const counts: CountMap = {
    sources: map?.counts.sources ?? 0,
    analyses: map?.counts.analyses ?? 0,
    connections: Object.values(map?.connections ?? {}).reduce((a, b) => a + b, 0),
    findings: Object.values(map?.findings ?? {}).reduce((a, b) => a + b, 0),
    figures: map?.counts.figures ?? 0,
    reports: map?.counts.reports ?? 0,
  };

  /*
   * Drop anywhere.
   *
   * dragenter/dragleave fire for every child element the cursor crosses, so a
   * boolean flag flickers the overlay off the moment you move over the table
   * inside it. Counting enter/leave pairs is the standard fix.
   */
  const [depth, setDepth] = useState(0);

  const hasFiles = (event: DragEvent) =>
    Array.from(event.dataTransfer?.types ?? []).includes("Files");

  const onDragEnter = useCallback((event: DragEvent) => {
    if (!hasFiles(event)) return;
    event.preventDefault();
    setDepth((d) => d + 1);
  }, []);

  const onDragLeave = useCallback((event: DragEvent) => {
    if (!hasFiles(event)) return;
    setDepth((d) => Math.max(0, d - 1));
  }, []);

  const onDrop = useCallback((event: DragEvent) => {
    if (!hasFiles(event)) return;
    event.preventDefault();
    setDepth(0);
    if (event.dataTransfer.files.length) onDropFiles(event.dataTransfer.files);
  }, [onDropFiles]);

  const roomForInspector = useRoomForInspector();
  // Read once per mount rather than on every render: this is the value the
  // Group starts from, and re-reading it while dragging would fight the drag.
  const [saved] = useState(readLayout);

  /*
   * Which group is open.
   *
   * Derived, not stored. The rule is "the group holding the current section",
   * and a heading press is an override recorded *against the section it was
   * made from* — so the moment the section changes, the override no longer
   * matches and the rail hands itself back to the new section's own group. An
   * effect that cleared the state on every section change would do the same
   * thing one render later and would fight the browser's Back button; this
   * cannot get out of step because there is nothing to keep in step.
   *
   * One group is always open, and pressing the open heading leaves it open.
   * The toggle used to close it, on the argument that `aria-expanded` promises
   * a move in both directions — but with one group open at a time the other
   * direction lands on an empty rail: five headings, no rows, and no
   * `aria-current` anywhere, so the rail stops answering the one question it
   * exists to answer, which is where you are. This is a choice among five, the
   * way a radio group is; `aria-expanded` still states each group's real state,
   * and the only way to close a group is to open another.
   */
  const [override, setOverride] = useState<{ at: Section; group: string } | null>(null);
  const openGroup = override?.at === section
    ? override.group
    : (GROUP_OF[section] ?? GROUPS[0].label);

  const openTheGroup = (label: string) => setOverride({ at: section, group: label });

  /**
   * The open group's own entries, including the machine pages when it is the
   * one open. They are `<a>`s rather than buttons because they are separate
   * routes: pressing one leaves the workspace, and a button would break opening
   * it in a new tab.
   */
  const openItems = GROUPS.find((g) => g.label === openGroup)?.items ?? [];

  return (
    <div
      className="shell"
      onDragEnter={onDragEnter}
      onDragLeave={onDragLeave}
      onDragOver={(e) => { if (hasFiles(e)) e.preventDefault(); }}
      onDrop={onDrop}
    >
      <header className="topbar">
        {/*
          * The product's name, set as the package sets it.
          *
          * All three masters open with the mark and the wordmark at the size of
          * a title, and ours opened with a fifteen-pixel mark tucked into the
          * breadcrumb — the identity row of the product named the project and
          * never the product. It is a lockup, not a crumb: a logo is not a
          * place in the hierarchy, so it sits outside the breadcrumb's nav.
          */}
        <span className="brand-lockup">
          <BrandMark height={28} strokePx={1.9} className="brand-mark" />
          <span className="brand-word">Throughline</span>
        </span>
        <nav className="crumbs" aria-label="Breadcrumb">
          {projectMenu ?? <span className="crumb-root">{projectName}</span>}
          {crumbs.map((crumb, i) => (
            <span key={i} className="crumb">
              <i aria-hidden>/</i>
              {crumb.onClick
                ? <button onClick={crumb.onClick}>{crumb.label}</button>
                : <b aria-current="page">{crumb.label}</b>}
            </span>
          ))}
        </nav>
        {/* A search field's shape, at a search field's width. It was a 620px
            bar across the middle of the identity row, which is the size of a
            thing you are meant to use constantly; the masters give it a
            quarter of that, on the right, beside the account. */}
        {/*
          One control at the right, for the two questions that are about the
          *session* rather than about any research object: who am I, and which
          palette am I in.

          It used to be two. A three-button segmented theme toggle sat beside an
          avatar that opened a menu, so every screen in the product carried two
          permanent controls for things a researcher touches about once a
          session — and the toggle spent that permanence on the least
          consequential choice on screen. Merging them costs one press to reach
          the theme and gives the topbar back to the breadcrumb and the command
          bar, which are what a person actually aims at.

          The menu's contents are still a **stated exception** to "nothing is
          hidden" rather than an oversight (plan §6, §4.16.2), and the argument
          is unchanged: these are properties of the session, so the placement
          law — an action lives on the object that produced it — has no object
          to put them on; there is no source, connection or finding that "sign
          out" acts upon. And a sign-out control sitting permanently in the
          topbar is a hazard, not a capability: the only thing a persistent one
          can do to a researcher three hours into an analysis is end their
          session by accident. The theme joins them because it is the same kind
          of thing — a property of this browser, not of this project.

          "New project" went the other way for the opposite reason — it acts on
          the project, which is the object the topbar is already naming, so it
          is a visible button beside the name (`ProjectMenu.tsx`). An omission
          that is argued is not a hidden capability; this comment is the
          argument, and `librarynote.tsx:92-97` is the template for it.
        */}
        <AccountControl user={account ?? null} />
      </header>

      {/*
        The five groups, then the open group's sections. Two rows across the top
        rather than one column down the side.
        
        This replaces the accordion rail T139 built, and the state machine
        underneath is the one that rail already had: one group open, derived
        from the current section, with a heading press recorded as a transient
        override. What changes is where it is drawn. The rail's own argument —
        that twenty-six entries on screen at once read as too many options —
        survives, because the section row still shows one group's entries and
        never all five groups' at once.

        `aria-controls` points the group at the row it fills, so a screen reader
        can say what a heading opens.
      */}
      <nav className="groupbar" aria-label="Areas">
        {GROUPS.map((group) => (
          <button
            key={group.label}
            type="button"
            className="groupbar-tab"
            aria-expanded={group.label === openGroup}
            aria-controls="workspace-sections"
            onClick={() => openTheGroup(group.label)}
          >
            <span>{group.label}</span>
            {/* The bare number beside a group reads as a count of the things
                inside the project — "Evidence 3" as three sources — when it
                counts the screens behind the heading (T188). The digit stays,
                because the row has no space for a word; what it counts is
                said to a screen reader, and by the sentence under the rail. */}
            <span className="groupbar-count"
                  aria-label={`${ENTRY_COUNT[group.label]} screens`}>
              {ENTRY_COUNT[group.label]}
            </span>
          </button>
        ))}
      </nav>

      <nav id="workspace-sections" className="sectionbar" aria-label={openGroup}>
        {openItems.map((item) => (
          <button
            key={item.id}
            className="sectionbar-item"
            aria-current={section === item.id}
            onClick={() => onSection(item.id)}
          >
            {/* Decorative: the label beside it is the accessible name. */}
            <span className="sectionbar-icon" aria-hidden>
              {ICONS[item.id]?.({ size: 15 })}
            </span>
            <span>{item.label}</span>
            {item.count && counts[item.count] > 0 && (
              <span className="sectionbar-count"
                    aria-label={`${counts[item.count]} ${item.label.toLowerCase()}`}>
                {counts[item.count]}</span>
            )}
          </button>
        ))}

        {openGroup === MACHINE && MACHINE_PAGES.map((page) => (
          <a key={page.href} className="sectionbar-item" href={page.href} title={page.note}>
            <span className="sectionbar-icon" aria-hidden>
              {IconHand({ size: 15 })}
            </span>
            <span>{page.label}</span>
          </a>
        ))}
      </nav>

      <Group
        className="shell-panels"
        orientation="horizontal"
        defaultLayout={saved}
        onLayoutChanged={writeLayout}
      >
      {rail && (
        <>
          <Panel id={RAIL} className="rail-panel"
                 defaultSize={RAIL_DEFAULT} minSize={RAIL_MIN} maxSize={RAIL_MAX}>
            <aside className="workbench-rail" aria-label="Working data">{rail}</aside>
          </Panel>
          <Separator className="shell-divider" aria-label="Resize the working data" />
        </>
      )}

      <Panel id={WORKSPACE} className="workspace-panel" minSize={320}>
        {strip}
        {/* `key` restarts the enter transition on navigation, so a view change
            reads as a change rather than a silent content swap (§116). */}
        <main className="workspace" key={section}>{children}</main>
      </Panel>

      {/*
        §117 is desktop-first: below 1100px the inspector is dropped rather
        than crushed. That used to be `display: none` in a media query, which a
        flex-based panel group cannot use — a hidden panel leaves its share of
        the width behind as empty space. So the panel is not rendered at all,
        and the divider with it, which is also the honest version: a divider
        that resizes nothing is a control that lies. The same holds when the
        page has nothing to put in it: an empty context panel is a column of
        chrome, so `inspector={null}` leaves the column out too.
      */}
      {roomForInspector && inspector && (
        <>
          <Separator className="shell-divider" aria-label="Resize the context panel" />
          <Panel id={INSPECTOR} className="inspector-panel"
                 defaultSize={INSPECTOR_DEFAULT}
                 minSize={INSPECTOR_MIN} maxSize={INSPECTOR_MAX}>
            <aside className="inspector" aria-label="Context inspector">{inspector}</aside>
          </Panel>
        </>
      )}
      </Group>

      {/*
        * The status line the masters end on.
        *
        * One fact, and it is the product's central promise rather than
        * decoration: the work stays on this machine unless the researcher
        * links an account. The package's right-hand "Illustrative project" is
        * a property of a mockup and is not copied — there is nothing
        * illustrative about a researcher's own data.
        */}
      <footer className="statusbar" aria-label="Status">
        <span className="statusbar-item">
          <span className="statusbar-dot" aria-hidden />
          Local-first
        </span>
        <span className="statusbar-note">{projectName}</span>
      </footer>

      {depth > 0 && (
        <div className="dropzone" aria-hidden>
          <div className="dropzone-card">
            <strong>Drop to add sources</strong>
            <span>PDF, DOCX, TXT, MD, CSV, TSV, XLSX or JSON</span>
          </div>
        </div>
      )}
    </div>
  );
}

/*
 * `AccountMenu.tsx` is not retired by the merged control: `tests/destructive.test.tsx`
 * still renders that component to hold what sign-out does, and this file and
 * `FirstProject.tsx` import `SignedInUser` from it. Deleting the file is a
 * separate decision with that test attached to it.
 */

/** Initials for the avatar, from whatever the account actually has. */
function initials(user: SignedInUser): string {
  const name = (user.display_name || "").trim();
  if (name) {
    const parts = name.split(/\s+/);
    return ((parts[0]?.[0] ?? "") + (parts[1]?.[0] ?? "")).toUpperCase();
  }
  return (user.email[0] ?? "?").toUpperCase();
}

/**
 * The one control at the right of the topbar: identity, theme, sign out.
 *
 * **Sign-out does a full document navigation, not a client-side state reset.**
 * That is the important thing in here and it is not a detail of styling.
 * Clearing state by hand means enumerating every place a previous user's data
 * might be sitting — hook state, component state, in-flight requests that have
 * not resolved, memoised derivations, worker messages — and being right about
 * all of them forever, including in code written after this. Getting that list
 * wrong once shows one researcher another researcher's corpus. A navigation
 * drops the entire JavaScript heap and starts from an empty one, for a few
 * hundred milliseconds on an action taken once a session. For the same reason
 * it happens even if the logout request fails: the local session should end
 * whether or not the server acknowledged it.
 *
 * Outside click, Escape, focus into the popup and focus back to the trigger are
 * Radix's, and the theme row is a `RadioGroup` rather than three plain buttons
 * for one reason: Radix moves focus between items only, so a hand-rolled row
 * inside the menu would be visible to the eye and unreachable by keyboard. The
 * three items suppress the default select-and-close, because changing the
 * palette is the one thing in this menu somebody might do twice.
 */
function AccountControl({ user }: { user: SignedInUser | null }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [choice, choose] = useThemeChoice();

  // No account is a real state — the shell renders before `/api/auth/status`
  // answers — and an empty avatar that opens an empty menu says less than
  // nothing.
  if (!user) return null;

  const name = user.display_name || user.email;

  async function signOut() {
    setBusy(true);
    try {
      await api.post("/api/auth/logout");
    } catch {
      // Ignored on purpose. If the server did not answer, the local session
      // still has to end — leaving someone signed in because the network
      // failed is the wrong way to be careful.
    } finally {
      window.location.assign("/workspace");
    }
  }

  return (
    <Menu.Root open={open} onOpenChange={setOpen}>
      <div className="am">
        <Menu.Trigger asChild>
          <button className="acct-trigger" title={name}>
            <span className="am-avatar" aria-hidden>{initials(user)}</span>
            <span className="acct-name">{name}</span>
          </button>
        </Menu.Trigger>

        <Menu.Portal>
          <Menu.Content className="am-pop" align="end" sideOffset={8}
                        collisionPadding={8}>
            <div className="am-who">
              <span className="am-avatar am-avatar-lg" aria-hidden>
                {initials(user)}
              </span>
              <div>
                <b>{user.display_name || "Researcher"}</b>
                <em>{user.email}</em>
                {user.is_admin && <span className="badge badge-quiet">Administrator</span>}
              </div>
            </div>

            <Menu.RadioGroup className="acct-theme" value={choice}
                             onValueChange={(next) => choose(next as typeof choice)}>
              {THEME_CHOICES.map((option) => (
                <Menu.RadioItem key={option} className="acct-theme-option"
                                value={option}
                                onSelect={(event) => event.preventDefault()}>
                  {THEME_LABEL[option]}
                </Menu.RadioItem>
              ))}
            </Menu.RadioGroup>

            <p className="am-note">
              <IconUser size={13} aria-hidden />
              Everything in this workspace belongs to this account and stays on
              this machine.
            </p>

            {/* The one command in here, last and under the hairline the note
                above draws. Everything before it is identity or preference. */}
            <Menu.Item className="am-out" disabled={busy}
                       onSelect={() => void signOut()}>
              <IconLogout size={15} />
              <span>{busy ? "Signing out…" : "Sign out"}</span>
            </Menu.Item>
          </Menu.Content>
        </Menu.Portal>
      </div>
    </Menu.Root>
  );
}
