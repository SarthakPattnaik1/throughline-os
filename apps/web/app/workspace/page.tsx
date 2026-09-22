"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  AnalysisRunRow, ArtifactSummary, Capabilities, Connection, DiscoveryMap,
  Finding, Project, Source, api,
} from "@/lib/api";
import { useApi } from "@/lib/useApi";
import {
  DEFAULT_SECTION, Place, View, defaultView, placeFromSearch, projectFromSearch,
  searchForPlace, searchForProject, searchForView, viewForMovedSection,
  viewFromSearch,
} from "@/lib/section-url";
import {
  Kind, lastProject, placeFor, rememberProject, selectionAt,
} from "@/lib/place";
import { currentStep, loopSteps, stepTarget } from "@/lib/loop";
import { StepStrip } from "@/components/StepStrip";
import { AnalysisContext } from "@/components/AnalysisContext";
import { ViewTabs } from "@/components/ViewTabs";
import { Centered, Failure, Fold, Loading } from "@/components/primitives";
import { Crumb, PAGES, SECTIONS, Section, Shell } from "@/components/Shell";
import { CommandPalette, buildCommands } from "@/components/CommandPalette";
import { OneBar } from "@/components/onebar";
import type { Destination } from "@/components/verbs";
import { AnalysisRail } from "@/components/AnalysisRail";
import { humanMethod,
  AnalysisDetail, ConnectionDetail, ConnectionsTable, Discover, EvidenceGraphView,
  EvidenceGraphSummary, Findings, ObjectHistoryFor, Overview, Search, SourceDetail,
  Sources,
} from "@/components/views";
import { TakeItFurther } from "@/components/takeitfurther";
import { canDraftReport } from "@/components/reports";
import { GraphStats } from "@/components/graphstats";
import { Preregister } from "@/components/preregister";
import { ReportDetail, Reports } from "@/components/reports";
import { GraphView } from "@/components/graphview";
import { Figures, type FigureLens } from "@/components/figures";
import { Gallery } from "@/components/gallery";
import { EmbeddingSpace } from "@/components/embeddingspace";
import { ProjectMenu } from "@/components/ProjectMenu";
import { SignedInUser } from "@/components/AccountMenu";
import { FirstProject, NewProject } from "@/components/FirstProject";
import { DataSearch } from "@/components/datasearch";
import { ReadFigure } from "@/components/readfigure";
import { enterScreen, reportView } from "@/lib/view-context";
import { Compare } from "@/components/compare";
import { Patterns } from "@/components/patterns";
import { Board } from "@/components/board/Board";
import { Literature } from "@/components/literature";
import { ProjectActivity } from "@/components/activity";
import { Notebook } from "@/components/notebook";
import { Settings } from "@/components/settings";
import { WithdrawnSources } from "@/components/withdrawn";
import { ExportedDocuments } from "@/components/exports";
import { Contradictions } from "@/components/contradictions";
import { Challenges } from "@/components/challenges";
import { ExplorationLedger } from "@/components/ledger";
import { Deviations } from "@/components/deviations";
import { Harvest } from "@/components/harvest";
import { LibraryNote } from "@/components/librarynote";
import { ForkLineage } from "@/components/forklineage";
import { FindingStanding } from "@/components/lifecycle";
import {
  ProvenanceLogLink, ReplayReceiptLink, ReproductionScriptLink,
} from "@/components/provenancelog";
import { Journal } from "@/components/journal";
import { Variables } from "@/components/variables";
import { AnalysisList, PlainReading } from "@/components/analyses";

import { AuthStatus, Gate } from "@/components/Gate";
import { Term } from "@/components/term";

export default function Home() {
  const auth = useApi<AuthStatus>("/api/auth/status");

  if (auth.loading) return <Centered><Loading rows={3} label="Starting Throughline" /></Centered>;
  if (auth.error) {
    return (
      <Centered>
        <Failure error={auth.error} retry={auth.reload} />
        <p className="note">
          The API is not answering. Start it with <code className="mono">./scripts/dev.sh</code>.
        </p>
      </Centered>
    );
  }
  if (!auth.data?.authenticated) return <Gate status={auth.data!} onDone={auth.reload} />;
  return <Workspace user={auth.data.user as SignedInUser} />;
}

const SECTION_LABEL: Record<Section, string> = Object.fromEntries(
  SECTIONS.map((s) => [s.id, s.label])) as Record<Section, string>;

/**
 * How often the project's counts are re-read while something is running.
 *
 * Only while: the discovery map says how many workflow runs are still in
 * flight, and the interval exists for exactly as long as that is not zero. A
 * workspace left open overnight makes no requests.
 */
const REFRESH_WHILE_BUSY_MS = 2500;

function Workspace({ user }: { user: SignedInUser }) {
  /*
   * `creating` opens the create-project flow on demand, so a researcher who
   * already has projects can still make another one — previously the create
   * screen was reachable only by having none, which meant the second project
   * had no route at all.
   */
  const [creating, setCreating] = useState(false);
  const projects = useApi<Project[]>("/api/projects");
  const capabilities = useApi<Capabilities>("/api/system/capabilities");

  /*
   * Where the researcher is, read from the address bar rather than merely
   * mirrored into it — and all three parts of it, not one (D196).
   *
   * The section alone was put in the URL by T108, and four ordinary things
   * started working: the view could be linked to, a reload kept the screen,
   * reopening the app did too, and Back went back one section. But a section
   * is not a place. `?section=findings` named a screen in whichever project
   * happened to be newest, so a reload from deep inside one project landed
   * in another; and the finding that was open was not in the address at all,
   * so Back from a detail left the section instead of closing the detail.
   *
   * So the address carries the project, the section and the item. Which
   * project, in order of authority: the one the address names; failing that,
   * the one this account had open last on this browser; failing that, the
   * newest — and the list is the arbiter of all three, because an id from a
   * bookmark or from storage may belong to a project that was deleted, or to
   * a different account on the same machine.
   *
   * Initialised from `window.location` inside the initialiser rather than in
   * an effect, so a deep link renders its own place on the first paint
   * instead of showing Overview and then replacing it — a flash that reads as
   * the link having failed.
   */
  const [place, setPlaceState] = useState<Place>(() =>
    typeof window === "undefined"
      ? { section: DEFAULT_SECTION, item: null }
      : placeFromSearch(window.location.search));
  const [projectId, setProjectIdState] = useState<string | null>(() =>
    typeof window === "undefined"
      ? null
      : projectFromSearch(window.location.search) ?? lastProject(user.id));

  /*
   * Which of the Research graph's two readings is open.
   *
   * In the address rather than in the graph component's own state, so the
   * river can be linked to, survives a reload, and is what Back leaves — the
   * same argument `section-url.ts` makes for the section itself. The Overview
   * relies on it too: its entrance into the lineage is a place, not a message
   * passed sideways into a component.
   */
  /** What Find data opens with when a claim sent the researcher there (D413). */
  const [dataQuery, setDataQuery] = useState<string | undefined>(undefined);
  const [view, setViewState] = useState<View | null>(() =>
    typeof window === "undefined"
      ? null
      : viewForMovedSection(window.location.search)
        ?? viewFromSearch(window.location.search,
                          placeFromSearch(window.location.search).section));

  // Read by callbacks that must see the current value without being
  // recreated on every navigation.
  const placeRef = useRef(place);
  placeRef.current = place;
  const projectRef = useRef(projectId);
  projectRef.current = projectId;
  const viewRef = useRef(view);
  viewRef.current = view;

  const project = projects.data?.find((p) => p.id === projectId) ?? null;
  const activeId = project?.id ?? null;

  /**
   * Move to a place, leaving a history entry behind.
   *
   * `pushState`, so Back goes back one step — one section, or from a detail
   * to its list. `replaceState` would fix the link and the reload and leave
   * Back doing what it did before, which was the complaint that started this.
   * The project goes into the address on every navigation, so that anything
   * copied or reloaded from here on comes back to the same project.
   */
  const go = useCallback((next: Place,
                         options: { project?: string; replace?: boolean; view?: View } = {}) => {
    setPlaceState(next);
    /*
     * The view belongs to one section, so it leaves the address with it.
     * Carrying `view=river` onto Findings would put a parameter in every link
     * copied from there that means nothing on arrival — and would quietly
     * reopen the river the next time the graph was visited, which is not where
     * the researcher left it.
     */
    /*
     * A view belongs to the section that owns it, so it survives a move only
     * when the section does not change. Carrying `river` onto Sources would
     * put a parameter in every link copied from there that means nothing on
     * arrival, and would quietly reopen a view the researcher had left.
     */
    const nextView = options.view
      ?? (next.section === placeRef.current.section
          ? viewRef.current : defaultView(next.section));
    setViewState(nextView);
    if (typeof window === "undefined") return;
    const search = searchForProject(
      options.project ?? projectRef.current,
      searchForView(nextView, next.section,
                    searchForPlace(next, window.location.search)));
    const url = `${window.location.pathname}${search}${window.location.hash}`;
    if (options.replace) window.history.replaceState(null, "", url);
    else window.history.pushState(null, "", url);
  }, []);

  /** Switch the current section's view, leaving a history entry. */
  const goView = useCallback((next: View) => {
    go({ section: placeRef.current.section, item: placeRef.current.item },
       { view: next });
  }, [go]);

  /**
   * Open a thing of a kind, in the section that shows it (D195).
   *
   * This is the one rule every in-view link follows now. Before, a link set
   * the selection and left the section alone, and because each section renders
   * a detail only for its own kind, the finding's "computations behind it"
   * showed the Findings *list* with a run id in the breadcrumb, and recording a
   * finding from a connection left the researcher on the Connections list,
   * never seeing what they had just made.
   */
  const open = useCallback((kind: Kind, id: string, options: { replace?: boolean } = {}) => {
    go(placeFor(kind, id, placeRef.current.section), options);
  }, [go]);

  /** Switch project: a different project is a different place, so it starts at the front. */
  const chooseProject = useCallback((id: string) => {
    setProjectIdState(id);
    go({ section: DEFAULT_SECTION, item: null }, { project: id });
  }, [go]);

  /**
   * Take up a project that was just made — by the form or the worked example —
   * and open it. The row goes into the list at once so the shell can render it
   * before the refetch lands; the refetch then replaces the whole list with the
   * server's, which is the copy that counts.
   */
  const adopt = useCallback((created: Project) => {
    projects.setData([created, ...(projects.data ?? []).filter((p) => p.id !== created.id)]);
    setCreating(false);
    chooseProject(created.id);
    projects.reload();
  }, [projects, chooseProject]);

  /*
   * The list is the arbiter of which project is open.
   *
   * An id from the address or from storage may name a project that was
   * deleted, or one that belongs to another account on this machine. Either
   * way the newest project is the honest fallback — and if the *address* was
   * the source of the bad id, it is corrected in place, so a reload does not
   * repeat the same wrong turn.
   */
  useEffect(() => {
    const list = projects.data;
    if (!list?.length || project) return;
    setProjectIdState(list[0].id);
    if (typeof window !== "undefined" && projectFromSearch(window.location.search)) {
      const search = searchForProject(list[0].id, window.location.search);
      window.history.replaceState(null, "",
        `${window.location.pathname}${search}${window.location.hash}`);
    }
  }, [projects.data, project]);

  // Remembered per account, so opening the app fresh comes back here.
  useEffect(() => {
    if (activeId) rememberProject(user.id, activeId);
  }, [activeId, user.id]);

  /*
   * Back and Forward move between places rather than out of the product.
   *
   * `popstate` is the only signal for this: the browser changes the URL
   * without React hearing about it, so without this the address bar and the
   * screen disagree after a single Back — which is worse than not supporting
   * it at all, because the URL then lies about what is shown.
   */
  useEffect(() => {
    const onPop = () => {
      setPlaceState(placeFromSearch(window.location.search));
      setViewState(viewForMovedSection(window.location.search)
        ?? viewFromSearch(window.location.search,
                          placeFromSearch(window.location.search).section));
      const named = projectFromSearch(window.location.search);
      if (named) setProjectIdState(named);
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const section = place.section;
  const selection = selectionAt(place);

  /*
   * §36. The assistant is told which screen the question was asked from, and
   * anything that screen reports about what it is showing. Announced on every
   * change so that leaving a screen withdraws its filters and counts — stale
   * context is worse than none, because it makes a true answer false by
   * qualifying it with a filter nobody has in force any more.
   */
  useEffect(() => { enterScreen(section); }, [section]);
  /*
   * The method of the analysis on screen, reported up by the detail view so the
   * branch panel below it can offer a fork that swaps it. Held here rather than
   * fetched again, so both panels describe the same run.
   */
  const [runMethod, setRunMethod] = useState<string | null>(null);
  /** The open run's recorded roles, for the cockpit's left column. */
  const [runVariables, setRunVariables] = useState<Record<string, unknown>>({});
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [pendingDiscovery, setPendingDiscovery] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<unknown>(null);

  /*
   * Re-read on every navigation (the `[section]` dependency), because these
   * are what the rail's counts, the Overview's meters and the breadcrumbs are
   * drawn from, and a researcher looks at those precisely when they arrive
   * somewhere. Fetched once per project, they went stale the moment anything
   * happened in the background (D194): the worked example finished building
   * in seconds while the screen kept saying nothing had.
   */
  const map = useApi<DiscoveryMap>(
    activeId ? `/api/projects/${activeId}/discovery-map` : null, [section]);
  const sources = useApi<Source[]>(
    activeId ? `/api/projects/${activeId}/sources` : null, [section]);
  /*
   * Every analysis in the project, not only the ones discovery turned into a
   * connection. The Figures screen draws a run, and a run a researcher
   * specified belongs to no connection.
   */
  const analyses = useApi<AnalysisRunRow[]>(
    activeId ? `/api/projects/${activeId}/analyses?limit=200` : null, [section]);
  const connections = useApi<Connection[]>(
    activeId ? `/api/projects/${activeId}/connections?limit=200` : null, [section]);
  const findings = useApi<Finding[]>(
    activeId ? `/api/projects/${activeId}/findings` : null, [section]);
  // Approved display names, so breadcrumbs and the palette never show a raw
  // column name either (Part C: zero raw names outside the mapping screen).
  const variables = useApi<{ labels: Record<string, string> }>(
    activeId ? `/api/projects/${activeId}/variables` : null);
  const artifacts = useApi<ArtifactSummary[]>(
    activeId ? `/api/projects/${activeId}/artifacts` : null);
  // Saved figures, so the palette can jump to one by title or id.
  const visuals = useApi<Array<{ id: string; title: string | null; visual_type: string }>>(
    activeId ? `/api/projects/${activeId}/visuals` : null);
  // Every connection the project has, across lifecycle states: the lists are
  // capped at 100 and 200 rows, and this is the denominator that lets them
  // say so (D201).
  const connectionTotal = Object.values(map.data?.connections ?? {}).reduce((a, b) => a + b, 0);
  /*
   * The evidence graph the finding detail has loaded, lifted once so the
   * "Take it further" card knows which run and which connection to act on
   * without fetching the graph twice. Reset when the finding changes, or the
   * previous finding's ids would seed the next card for a moment.
   */
  const [evidence, setEvidence] = useState<EvidenceGraphSummary | null>(null);
  useEffect(() => { setEvidence(null); }, [place.item]);

  /*
   * While the project has work in flight, keep the counts current; the moment
   * it finishes, re-read the lists the work will have changed.
   *
   * The server says how many workflow runs are still queued or running
   * (`counts.in_flight`), so this polls for exactly as long as that is true
   * and not a second longer — the alternative, guessing a duration, is how a
   * screen ends up either stale or hammering a local API forever. The lists
   * are refreshed once, on the transition to idle, because that is when the
   * analyses, connections and findings the run produced have all landed.
   */
  const inFlight = map.data?.counts.in_flight ?? 0;
  const wasBusy = useRef(false);
  const { reload: reloadMap } = map;

  /**
   * Run the whole loop on this project, in one act.
   *
   * Queues `project.advance` and leaves the workspace to show the work
   * arriving — the step strip already says how many jobs are in flight, and
   * watching a project assemble itself is a better answer than a spinner over
   * a screen that then changes underneath you.
   */
  const [advancing, setAdvancing] = useState(false);
  const runTheLoop = useCallback(async () => {
    if (!projectRef.current) return;
    setAdvancing(true);
    try {
      await api.post(`/api/projects/${projectRef.current}/advance`, {});
      reloadMap();
    } catch {
      // The map reload is what surfaces the result; a failure to queue leaves
      // the control pressable again rather than reporting a second error over
      // a screen that has not changed.
    } finally {
      setAdvancing(false);
    }
  }, [reloadMap]);

  const { reload: reloadSources } = sources;
  const { reload: reloadAnalyses } = analyses;
  const { reload: reloadConnections } = connections;
  const { reload: reloadFindings } = findings;
  useEffect(() => {
    if (inFlight > 0) {
      wasBusy.current = true;
      const timer = window.setInterval(reloadMap, REFRESH_WHILE_BUSY_MS);
      return () => window.clearInterval(timer);
    }
    if (wasBusy.current) {
      wasBusy.current = false;
      reloadSources(); reloadAnalyses(); reloadConnections(); reloadFindings();
    }
    return undefined;
  }, [inFlight, reloadMap, reloadSources, reloadAnalyses, reloadConnections, reloadFindings]);

  /*
   * Global keys. ⌘K opens the palette; Escape leaves a detail view for the list
   * it came from, which is the one navigation a keyboard user reaches for most
   * and the one a single-page shell most often forgets to provide.
   */
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "k" && (event.metaKey || event.ctrlKey)) {
        event.preventDefault();
        // Always opens, never toggles. Written as a toggle first, which meant a
        // second ⌘K on an already-open palette dismissed it — and since you
        // cannot see whether it is open while reaching for the shortcut, the
        // keystroke did the opposite of what you asked about half the time.
        // Escape is how it closes.
        setPaletteOpen(true);
        return;
      }
      if (event.key === "Escape" && !paletteOpen) {
        // Don't steal Escape from a field the researcher is typing in.
        const tag = (event.target as HTMLElement | null)?.tagName;
        if (tag === "INPUT" || tag === "TEXTAREA") return;
        const current = placeRef.current;
        if (current.item) go({ section: current.section, item: null });
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [paletteOpen, go]);

  const upload = useCallback(async (files: FileList | null) => {
    if (!files?.length || !activeId) return;
    setUploading(true);
    setUploadError(null);
    go({ section: "sources", item: null });
    try {
      for (const file of Array.from(files)) {
        await api.upload(`/api/projects/${activeId}/sources`, file);
      }
      reloadSources();
      reloadMap();
    } catch (err) {
      setUploadError(err);
    } finally {
      setUploading(false);
    }
  }, [activeId, go, reloadSources, reloadMap]);

  // `!projects.data`, not `loading` alone: the list is refetched after a
  // project is created or deleted, and a refetch must not blank the screen
  // that is already showing the rest of the workspace.
  if (projects.loading && !projects.data) {
    return <Centered><Loading rows={3} label="Loading projects" /></Centered>;
  }
  if (projects.error) return <Centered><Failure error={projects.error} retry={projects.reload} /></Centered>;
  if (!projects.data?.length) {
    return <FirstProject onCreated={adopt} user={user} />;
  }
  if (creating) {
    return <NewProject onCreated={adopt} onCancel={() => setCreating(false)}
                       offerExample />;
  }
  if (!project) return <Centered><Loading rows={2} /></Centered>;

  function select(kind: Kind) {
    return (id: string) => open(kind, id);
  }

  function goSection(next: Section) {
    go({ section: next, item: null });
  }

  // The breadcrumb is what makes a detail view escapable by mouse, and what
  // tells the researcher where a palette jump just landed them.
  const crumbs: Crumb[] = [{
    label: SECTION_LABEL[section],
    onClick: selection ? () => go({ section, item: null }) : undefined,
  }];
  if (selection) {
    const named =
      selection.kind === "source"
        ? sources.data?.find((s) => s.id === selection.id)?.title
        : selection.kind === "connection"
          ? (() => {
              const c = connections.data?.find((x) => x.id === selection.id);
              return c ? `${c.left_variable} × ${c.right_variable}` : undefined;
            })()
          : selection.kind === "finding"
            ? findings.data?.find((f) => f.id === selection.id)?.title
            : selection.kind === "artifact"
              ? artifacts.data?.find((a) => a.id === selection.id)?.title
              : selection.kind === "analysis"
                // The method, as the detail's own heading spells it; a run id
                // in a breadcrumb tells the researcher nothing about where
                // they are.
                ? (() => {
                    const method = analyses.data?.find((a) => a.id === selection.id)?.method;
                    return method ? humanMethod(method) : undefined;
                  })()
                : undefined;
    crumbs.push({ label: named ?? selection.id });
  }

  /*
   * Where the project is in the loop, and what to do about it, computed once
   * here and read by the strip above the workspace and by the inspector — so
   * the two cannot name different steps. `here` is true when this screen is
   * the step's destination, and then the strip offers no button: the real
   * control is on the page (T135).
   */
  const loopMap = map.data;
  const steps = loopMap ? loopSteps(loopMap) : [];
  const step = loopMap ? currentStep(loopMap) : null;
  const target = step && loopMap
    ? stepTarget(step, loopMap, variables.data?.labels) : null;
  const here = !!target && section === target.section
    && (target.item ? place.item === target.item : true);
  const takeStep = () => {
    if (!target) return;
    // As the kind the target is: a finding the rung is about opens as a
    // finding. This was hard-coded to "connection" (T153).
    if (target.item) open(target.kind ?? "connection", target.item);
    else goSection(target.section);
  };
  const strip = loopMap ? (
    <StepStrip
      steps={steps}
      onGo={goSection}
      step={step}
      index={step ? steps.findIndex((s) => s.id === step.id) + 1 : 0}
      total={steps.length}
      here={here}
      actionLabel={target?.label ?? null}
      onAction={takeStep}
      onShowLoop={() => goSection("overview")}
      working={inFlight}
    />
  ) : null;

  /**
   * Run a verb from the one bar (T189).
   *
   * Three shapes, and only three: open the file chooser, follow the loop's own
   * next step, or go to a screen — carrying the rest of the typed line as a
   * search term where the screen takes one. Nothing here starts a sweep or a
   * validation: those are real compute and real claims, and the bar takes you
   * to the control rather than pressing it for you.
   */
  /* A plain function, not a `useCallback`: everything from here down sits
     below this component's early returns (`if (!project) return …`), so a hook
     here changes the hook count between renders. `takeStep` just above is a
     plain function for the same reason. */
  const runVerb = (to: Destination, argument: string) => {
    if (to.act === "next") { takeStep(); return; }
    if (argument) setDataQuery(argument);
    go({ section: to.section, item: null },
       to.view ? { view: to.view as View } : {});
    if (to.act === "upload") {
      // After paint: the input belongs to the screen being navigated to, so it
      // does not exist until that screen has rendered.
      requestAnimationFrame(() => requestAnimationFrame(() => {
        document.getElementById("add-sources-input")?.click();
      }));
    }
  };

  const commands = buildCommands({
    labels: variables.data?.labels ?? {},
    sections: SECTIONS,
    sources: sources.data ?? [],
    connections: connections.data ?? [],
    findings: findings.data ?? [],
    pages: PAGES,
    analyses: analyses.data ?? [],
    reports: artifacts.data ?? [],
    figures: visuals.data ?? [],
    go: goSection,
    open: (target, _kind, id) => go({ section: target, item: id }),
  });

  return (
    <>
      <Shell
        section={section} onSection={goSection}
        map={map.data} projectName={project.name}
        crumbs={crumbs}
        onDropFiles={upload}
        onCommand={() => setPaletteOpen(true)}
        projectMenu={
          <ProjectMenu
            projects={projects.data}
            currentId={project.id}
            onSelect={chooseProject}
            onChanged={() => {
              // The deleted project may be the one on screen. Refetch, and let
              // the effect above pick the first survivor.
              projects.reload();
            }}
            onCreate={() => setCreating(true)}
          />
        }
        account={user}
        strip={strip}
        inspector={
          // No selection, no panel: a column saying "Nothing is selected" beside
          // every list is chrome, not context (T139). The installation readout
          // it carried stays one press away wherever an object is open.
          /*
            The cockpit gets the column §09 asks it for; everything else keeps
            the generic one. A run is the only object in this product that is
            joined to a connection, a dataset and a validation state at once,
            and those three were in three other sections.
          */
          section === "analyses" && selection?.kind === "analysis"
            ? <AnalysisContext
                projectId={project.id}
                runId={selection.id}
                onOpenConnection={(id) => open("connection", id)}
                onOpenSource={(id) => open("source", id)}
                onOpenLineage={(objectId) =>
                  go({ section: "graph", item: objectId }, { view: "river" })}
              />
            : selection
              ? <Inspector selection={selection} capabilities={capabilities.data} />
              : null
        }
        rail={
          /*
            The cockpit's left column, and only the cockpit's.
            §08's dense workbench puts the working material on the left —
            sources, variable roles, the run family — and that is a real thing
            to show in front of a result. Every other screen has no such thing,
            and a left column that said "nothing here" beside a collection list
            would be the chrome T139 removed. So it renders where it means
            something and nowhere else, and the width goes back to the work.
          */
          section === "analyses" && selection?.kind === "analysis"
            ? <AnalysisRail projectId={project.id} runId={selection.id}
                            variables={runVariables}
                            onOpenRun={select("analysis")} />
            : null
        }
      >
        {section === "board" && (
          /*
           * The central operating surface (§4). Cards are the project's own
           * research objects — an analysis, a figure, an excerpt — so arranging
           * the board arranges the work rather than a set of shortcuts to it.
           */
          <Board projectId={project.id} onOpen={(kind, id) => open(kind, id)} />
        )}

        {section === "overview" && (
          <>
            <Overview project={project} map={map.data} onGo={goSection}
                      onOpen={(kind, id) => open(kind, id)} onAddSources={upload}
                      onLineage={() => go({ section: "graph", item: null },
                                          { view: "river" })}
                      onAdvance={runTheLoop}
                      advancing={advancing}
                      labels={variables.data?.labels} />
            {/*
              Directly under the meters, because the Contradictions meter is
              what this panel makes honest. The count read from a table nothing
              wrote to, so it showed zero for every project that has ever
              existed — and a meter a reader cannot click through to is a number
              they have to take on trust, which is how it stayed wrong.
            */}
            <Contradictions projectId={project.id} />
          </>
        )}
        {section === "sources" && (
          selection?.kind === "source"
            ? <SourceDetail
                projectId={project.id} sourceId={selection.id}
                onDiscover={(versionId) => {
                  setPendingDiscovery(versionId);
                  goSection("discover");
                }}
                onOpenSource={select("source")}
                onGo={goSection}
                labels={variables.data?.labels}
                onOpenObject={select("object")}
                // A table imported from a database is a new source, so the
                // list beside this one is out of date until it is re-read —
                // the same reload an upload already triggers.
                onImported={() => reloadSources()}
              />
            : <>
                {/*
                  Above the list, not in a section of its own. A withdrawal is a
                  fact about these sources rather than a place to visit, and a
                  nav item is something you have to remember to click — which
                  nobody does until they already suspect something is wrong.
                  When nothing is withdrawn this renders a single quiet line.
                */}
                <WithdrawnSources projectId={project.id} />
                {/*
                  * The library, and the four ways of getting something into it.
                  *
                  * Searching this project's sources, finding a paper, finding a
                  * dataset and digitising a figure were four sections of their
                  * own, listed beside the library as though each were a peer of
                  * it. They are not: every one of them ends in a source landing
                  * here, and a researcher looking for "where do I add a paper"
                  * had to already know which of five entries meant that. They
                  * are views of the library now, and the nav is four shorter.
                  */}
                <ViewTabs
                  name="sources-view"
                  label="The library, and the ways into it"
                  value={(view ?? "library") as string}
                  onChange={(v: string) => goView(v as View)}
                  options={[["library", "Library"], ["search", "Search these"],
                            ["papers", "Find papers"], ["data", "Find data"],
                            ["figure", "Read a figure"]] as const}
                />
                {(view ?? "library") === "library" && (
                  <Sources
                    sources={sources} onSelect={select("source")}
                    upload={upload} uploading={uploading} uploadError={uploadError}
                    formats={capabilities.data?.formats ?? null}
                  />
                )}
                {view === "search" && (
                  <Search projectId={project.id} onOpenSource={select("source")} />
                )}
                {view === "papers" && (
                  <>
                    {/* The topic the bar carried in — "find papers about
                        soil" arrives with the box filled (T195). `key` so a
                        second ask re-seeds it, the way `DataSearch` does; the
                        prop existed since T189 and nothing ever passed it, so
                        every bar-driven paper search landed on an empty box. */}
                    <Literature projectId={project.id} initialQuery={dataQuery}
                                key={dataQuery ?? ""} />
                    {/*
                      Beneath search, not instead of it. Searching four databases
                      and harvesting one repository are different acts — one asks
                      a question, the other takes a copy — and a researcher
                      arrives wanting the first far more often than the second.
                    */}
                    <Harvest projectId={project.id} />
                  </>
                )}
                {view === "data" && (
                  /* A found dataset comes in through the same door a dropped
                     file uses, and the researcher goes with it to watch it
                     being profiled. */
                  <DataSearch projectId={project.id} initialQuery={dataQuery}
                              key={dataQuery ?? ""}
                              onImported={(id) => { reloadSources(); reloadMap(); open("source", id); }} />
                )}
                {view === "figure" && (
                  /*
                   * The digitised points go in as a dataset, and the researcher
                   * goes with them: the new source's screen shows it being
                   * profiled, the same way a recorded finding is shown rather
                   * than announced.
                   */
                  <ReadFigure projectId={project.id}
                              onAdded={(id) => { reloadSources(); reloadMap(); open("source", id); }} />
                )}
              </>
        )}
        {section === "variables" && <Variables projectId={project.id} />}
        {section === "discover" && (
          selection?.kind === "connection"
            ? <ConnectionDetail connectionId={selection.id} projectId={project.id}
                                  onRecordFinding={(id) => { reloadFindings(); open("finding", id); }}
                                  onDraftedReport={(id) => { reloadMap(); open("artifact", id); }} />
            : <>
                <Discover
                  projectId={project.id} sources={sources}
                  onSelectConnection={select("connection")}
                  startWith={pendingDiscovery} onStarted={() => setPendingDiscovery(null)}
                  connectionTotal={connectionTotal}
                />
                {/*
                  The screen that runs the sweep now shows the running total
                  the correction depends on, and offers to register a
                  hypothesis at the one moment registering one is meaningful —
                  before the next test, not after (plan §4.10). Quiet, because
                  Discovery's primary belongs to the sweep and the strip.
                */}
                <ExplorationLedger projectId={project.id} />
                <Preregister projectId={project.id} emphasis="secondary" />
              </>
        )}
        {section === "connections" && (
          selection?.kind === "connection"
            ? <ConnectionDetail connectionId={selection.id} projectId={project.id}
                                  onRecordFinding={(id) => { reloadFindings(); open("finding", id); }}
                                  onDraftedReport={(id) => { reloadMap(); open("artifact", id); }} />
            : <>
                <ConnectionList projectId={project.id} onSelect={select("connection")}
                                total={connectionTotal} />
                {/*
                  Under the connections rather than beside the results. The
                  count is context for what has just been read, and a reader who
                  has scrolled a list of candidate relationships is exactly the
                  reader who should see how many were tested to produce it.
                */}
                <ExplorationLedger projectId={project.id} />
                {/*
                  Beneath the ledger, because they answer two halves of one
                  question. The ledger says how much looking was done; this says
                  how much of it was the looking that was planned.
                */}
                <Deviations projectId={project.id} />
                {/*
                  A structural fact about the project's graph, beside the
                  ledger that counts its tests: which objects it has connected
                  most. Stated as structure, never as a finding.
                */}
                <GraphStats projectId={project.id} onOpen={(id) => open("object", id)} />
              </>
        )}
        {section === "findings" && (
          selection?.kind === "finding"
            ? <>
                <EvidenceGraphView findingId={selection.id}
                                   onOpenAnalysis={select("analysis")}
                                   onLoaded={setEvidence} />
                {/*
                  Directly under the evidence, because the evidence is what
                  decides whether it may move at all: anything past candidate
                  is a claim about the world and the domain refuses it without
                  something attached.
                */}
                <FindingStanding findingId={selection.id} />
                {/*
                  §75. The evidence graph says why we believe it; this is what
                  somebody else would need to get the number again.
                */}
                <ProvenanceLogLink findingId={selection.id} />
                {/*
                  Below the evidence, deliberately. The case for a finding is
                  what a researcher came to read; the case against it is what
                  they need to have read before they cite it. Putting the
                  argument first would make the screen adversarial, and hiding
                  it behind a tab means it is never opened.
                */}
                <Challenges projectId={project.id} findingId={selection.id} />
                {/*
                  The loop's last step, from the object step 5 produced: publish
                  a figure of the run behind this finding, or draft a report from
                  the connection it rests on. Gated on the evidence having
                  arrived, so a loading screen is not told there is nothing to
                  draft and then told there is.
                */}
                {evidence && (
                  <TakeItFurther
                    projectId={project.id}
                    findingId={selection.id}
                    analysisRunId={evidence.analyses?.[0]?.id ?? null}
                    connection={(evidence.connections ?? []).find(canDraftReport) ?? null}
                    onDrafted={(id) => { reloadMap(); open("artifact", id); }}
                  />
                )}
                <LibraryNote projectId={project.id} findingId={selection.id} />
                {/*
                  Last on the screen: what was written about this finding and
                  what it used to say (D213, plan §4.6.2). It goes after the
                  library note because a note being written is part of the
                  argument above; the journal is the record of that argument
                  having been made, and the versions are how you go back.
                */}
                <ObjectHistoryFor projectId={project.id} kind="finding"
                                  id={selection.id}
                                  onOpenObject={select("object")} />
              </>
            : <Findings projectId={project.id} onSelect={select("finding")} />
        )}
        {section === "analyses" && (
          selection?.kind === "analysis"
            ? <>
                <AnalysisDetail runId={selection.id} projectId={project.id}
                                onOpenRun={select("analysis")}
                                onMethod={setRunMethod}
                                onVariables={setRunVariables}
                                onOpenObject={select("object")}
                                onOpenFigures={() =>
                                  go({ section: "figures", item: selection.id })} />
                {/*
                  §75. Beside the run, because "how was this computed" is
                  asked while looking at the number.
                */}
                <ReproductionScriptLink runId={selection.id} />
                <ReplayReceiptLink runId={selection.id} />
                {/*
                  The plain reading was reachable only through a connection,
                  so an analysis a researcher specified had no legible version
                  of itself at all.
                */}
                <PlainReading runId={selection.id} />
                {/*
                  Beneath the run, because the branch is context for the number
                  above it. Renders nothing at all for an original analysis with
                  no variants, which is most of them — a panel that appears on
                  every run to say "no relationship" is noise.
                */}
                <ForkLineage projectId={project.id} runId={selection.id}
                             method={runMethod}
                             onOpen={select("analysis")} />
              </>
            : <>
                {/*
                  * One screen, three readings of the same runs.
                  *
                  * The pattern sweep and the embedding space were sections of
                  * their own in the rail, and both are questions about *this
                  * project's analyses*: which pairs a sweep proposed, and how
                  * the runs sit relative to one another. Listed beside
                  * Analyses they read as separate features, and a researcher
                  * looking for either had to already know that "Patterns" did
                  * not mean the patterns in their data.
                  */}
                <ViewTabs
                  name="analyses-view"
                  label="This project's analyses, three ways"
                  value={(view ?? "runs") as string}
                  onChange={(v: string) => goView(v as View)}
                  options={[["runs", "Runs"], ["patterns", "Pattern sweep"],
                            ["embedding", "Embedding space"]] as const}
                />
                {(view ?? "runs") === "runs" && (
                  <AnalysisList projectId={project.id}
                                onSelect={select("analysis")} />
                )}
                {view === "patterns" && (
                  <Patterns
                    projectId={project.id}
                    datasetVersionId={
                      (sources.data ?? []).find((s) => s.dataset)?.dataset
                        ?.dataset_version_id ?? null}
                    columns={Object.keys(variables.data?.labels ?? {})}
                  />
                )}
                {view === "embedding" && (
                  <EmbeddingSpace projectId={project.id} />
                )}
              </>
        )}
        {section === "reports" && (
          selection?.kind === "artifact"
            ? <ReportDetail artifactId={selection.id} projectId={project.id}
                            onOpenArtifact={select("artifact")} />
            : <>
                {/*
                  Above the list, for the same reason the withdrawal notice sits
                  above the sources: this is a fact about these documents, not a
                  place to visit. Nobody navigates to a staleness screen until
                  they already suspect something, and by then the wrong numbers
                  have been sent. It renders nothing at all until the project has
                  actually exported something.
                */}
                <ExportedDocuments projectId={project.id}
                                   onOpen={select("artifact")} />
                <Reports projectId={project.id} connections={connections}
                         onSelect={select("artifact")} />
              </>
        )}
        {section === "compare" && (
          <Compare projectId={project.id} sources={sources}
                   onOpenSource={(id) => open("source", id)}
                   onFindPapers={() => go({ section: "sources", item: null }, { view: "papers" })}
                   onAddData={() => go({ section: "sources", item: null }, { view: "library" })}
                   onConnectModel={() => go({ section: "settings", item: null })}
                   onFindData={(query) => {
                     setDataQuery(query);
                     go({ section: "sources", item: null }, { view: "data" });
                   }} />
        )}
        {section === "notebook" && <Notebook projectId={project.id} />}
        {section === "journal" && (
          <>
            {/*
              * One record, two readings.
              *
              * The journal and the activity log were two entries in the rail
              * answering one question — what has happened in this project —
              * and a researcher wanting "what did I decide last week" had to
              * know that decisions are written and imports are done. They are
              * the same record read two ways.
              */}
            <ViewTabs
              name="record-view"
              label="What has happened in this project"
              value={(view ?? "written") as string}
              onChange={(v: string) => goView(v as View)}
              options={[["written", "Written"], ["done", "Done"]] as const}
            />
            {(view ?? "written") === "written"
              ? (
                /*
                 * An entry is about a research object, and the place that shows
                 * one is the research graph, with the object's own journal open
                 * beside it. This used to hand the id to the analysis detail,
                 * which reads run ids, and to leave the section on Journal — so
                 * the link changed the breadcrumb and nothing else (D195).
                 */
                <Journal projectId={project.id} onOpenObject={select("object")}
                         onWrite={() => goSection("notebook")} />
              )
              : (
                <ProjectActivity projectId={project.id}
                                 onOpenObject={select("object")} />
              )}
          </>
        )}
        {/* The activity log is the Record's second reading (`section=activity`
            redirects there), so `main`'s separate render for it has nowhere to
            appear; Settings takes `main`'s admin flag. */}
        {section === "settings" && (
          <Settings projectId={project.id} isAdmin={user.is_admin === true} />
        )}
        {section === "graph" && (
          /*
           * `replace`, not push: a graph is browsed by clicking node after
           * node, and a history entry per node would make Back walk through
           * every one of them before it left the screen. The address still
           * names the open object, so a reload or a copied link comes back to
           * it.
           */
          <GraphView projectId={project.id}
                     view={view}
                     onView={goView}
                     focus={selection?.kind === "object" ? selection.id : null}
                     onSelect={(id) => open("object", id, { replace: true })} />
        )}
        {section === "figures" && (
          <>
            {/*
              * This project's figures, and the catalogue of what can be drawn.
              *
              * "Chart primitives" sat in *This machine*, beside Settings, as
              * though a catalogue of chart kinds were a property of the
              * installation. It is the answer to "what could I draw this as",
              * which is a question you have while making a figure — so it is a
              * view of Figures, and This machine is down to Settings alone.
              */}
            <ViewTabs
              name="figures-view"
              label="Figures, and what can be drawn"
              value={(view ?? "saved") as string}
              onChange={(v: string) => goView(v as View)}
              options={[["saved", "This project"],
                        ["primitives", "Chart primitives"]] as const}
            />
            {view === "primitives"
              ? (
                /*
                 * The catalogue hands the lens it would be drawn with. It used
                 * to be fourteen charts a researcher could look at and not
                 * use: "if they click on that graph, they can change what
                 * graph they need" is the whole point of having a catalogue
                 * inside the product rather than in the documentation.
                 */
                <Gallery
                  onDraw={(lens) => goView(lens as View)}
                  onGo={(target) => go({ section: target.section as Section,
                                         item: null },
                                       { view: target.view as View })}
                />
              )
              : (
                <Figures projectId={project.id} runs={analyses}
                         focusId={place.item}
                         /* `saved` is the builder on its default lens, kept by
                            that name so every link already copied still lands. */
                         lens={(view === null || view === "saved")
                               ? "all" : (view as FigureLens)}
                         onLens={(next) =>
                           goView((next === "all" ? "saved" : next) as View)} />
              )}
          </>
        )}
      </Shell>

      {/*
        * The bar, always there (T195).
        *
        * Outside `Shell` because it is fixed to the window, not to the
        * scrolling column — inside, it would scroll away with the content it
        * is meant to stay in front of. One bar, one position, on every screen,
        * which is the whole point: a researcher never has to find it.
        */}
      <div className="askdock">
        <OneBar
          commands={commands}
          onVerb={runVerb}
          size="dock"
          historyKey={project.id}
          placeholder="Ask for anything — “find papers about soil”, “what’s next”, “add data”"
          suggestions={[
            ...(target?.label
              ? [{ label: target.label.replace(/\s*→\s*$/, ""),
                   run: takeStep, primary: true }]
              : []),
            { label: "Add data", run: () => runVerb(
                { section: "sources", view: "library", act: "upload" }, "") },
            { label: "Find papers", run: () => runVerb(
                { section: "sources", view: "papers" }, "") },
            { label: "Test every pair", run: () => runVerb(
                { section: "discover" }, "") },
            { label: "Draft a report", run: () => runVerb({ section: "reports" }, "") },
          ]}
        />
      </div>

      <CommandPalette
        open={paletteOpen} onClose={() => setPaletteOpen(false)} commands={commands}
      />
    </>
  );
}

function ConnectionList({ projectId, onSelect, total }: {
  projectId: string; onSelect: (id: string) => void;
  /** How many connections the project has in all, so the capped list can say
   *  how many it is not showing (D201). */
  total?: number;
}) {
  const { data, error, loading, reload } = useApi<Connection[]>(`/api/projects/${projectId}/connections?limit=200`);
  /*
   * The list is capped at two hundred, and a cap miscounts in exactly the way
   * a filter does: a project with four hundred connections shows two hundred,
   * and an assistant told "two hundred connections" will say the project found
   * two hundred. The total is deliberately not reported, because this screen
   * does not know it — the domain says so in those words rather than inventing
   * a denominator.
   */
  useEffect(() => {
    if (data) reportView({ showing: data.length });
  }, [data]);
  return (
    <>
      <h1>Connections</h1>
      <p className="lede">
        Every candidate that was tested. Each row is an{" "}
        <Term id="association" />.
      </p>
      <ConnectionsTable connections={data} error={error} loading={loading} reload={reload}
                        onSelect={onSelect} total={total} />
    </>
  );
}

/**
 * The first thing a brand-new account sees.
 *
 * Deliberately not an empty list with a button in the corner. An account with
 * no projects has nothing to look at, so the screen's whole job is to get the
 * researcher to the one action that makes the rest of the product exist — and
 * to say what will happen when they take it, because "create a project" does
 * not tell anyone what a project here is for.
 *
 * The three cards are what the system actually does with a question, in order.
 * They are descriptions of the real pipeline rather than marketing: a
 * researcher who reads them and then uses the product should find it did
 * exactly this.
 */
// Exported for the test suite. T003 shipped this button with the caveat that
// nothing proved it reached the endpoint, and the reason was that it could not
// be imported — the component was module-private, so the one control standing
// between a new researcher and a working project was the one control no test
// could touch.
/** What a selected object is, in one line. Never more than one. */
const WHAT_IS_SELECTED: Record<string, string> = {
  connection: "A connection is a tested relationship, not a cause.",
  finding: "A finding is a claim, carried by the evidence linked to it.",
  artifact: "A report references its findings rather than copying them.",
  analysis: "An analysis is one run, with its seed and its assumption checks.",
  source: "A source is a file as it was ingested, and what was made of it.",
};

/**
 * The context panel: at most three items, and every explanation folded (T139).
 *
 * It used to open with the loop's recommendation and a button carrying the
 * loop's action — the same sentence and the same act as the step strip two
 * hundred pixels to its left, on every one of twenty-four screens. The strip
 * cannot be scrolled away and this panel is dropped entirely below 1101 px,
 * so of the two the strip is the one that has to hold the action; a second
 * copy here was the duplication D204 removed from the Overview reappearing
 * one column over.
 *
 * What is left is what only this panel says: what the selected object is, and
 * what this installation can do. The installation readout is five rows and two
 * caveats — a permanent sixty words of machine configuration beside every
 * screen — so it rests closed, with the number of facts on its summary.
 */
function Inspector({ selection, capabilities }: {
  selection: { kind: string; id: string } | null;
  capabilities: Capabilities | null;
}) {
  const what = selection ? WHAT_IS_SELECTED[selection.kind] : null;

  return (
    <>
      <h3 className="eyebrow">Context</h3>
      {what
        ? <p className="note one-line">{what}</p>
        : <p className="note one-line">Nothing is selected.</p>}

      {!capabilities && <Loading rows={2} />}
      {capabilities && (
        <Fold summary="This installation" count={5}>
          <div className="kv">
            <dt>Search</dt>
            <dd>{capabilities.retrieval.semantic ? "hybrid" : "lexical only"}</dd>
            {/* Named by what each model does. "Model: none" two lines above
                "AI provider: configured" read as two contradictory statements
                about one thing (D204). */}
            <dt>Search model</dt>
            <dd className="mono">
              {capabilities.retrieval.model ?? "none installed — search is lexical only"}
            </dd>
            <dt>Sandbox</dt>
            <dd>{capabilities.analysis.sandbox ? "enabled" : "unavailable"}</dd>
            <dt>Methods</dt>
            <dd>{capabilities.analysis.methods?.length ?? 0}</dd>
            <dt>Writing model</dt>
            <dd>{capabilities.llm.configured ? "configured" : "none"}</dd>
          </div>
          {!capabilities.llm.configured && (
            <p className="note">{capabilities.llm.note}</p>
          )}
          {capabilities.retrieval.note && (
            <p className="note">{capabilities.retrieval.note}</p>
          )}
        </Fold>
      )}
    </>
  );
}
