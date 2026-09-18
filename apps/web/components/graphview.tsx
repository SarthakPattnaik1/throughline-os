"use client";

/**
 * The graph section: data, the canvas, and the accessible equivalent.
 *
 * Canvas gives 60fps and takes away the DOM, so the same objects are rendered
 * as a real table beneath it. That is not a token gesture — it is the keyboard
 * and screen-reader path, and it stays in sync because both read one array.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { useApi } from "@/lib/useApi";
import type { View } from "@/lib/section-url";
import { Empty, Failure, Loading } from "./primitives";
import { GraphEdge, GraphNode, KnowledgeGraph } from "./KnowledgeGraph";
import { NodeJournal } from "./NodeJournal";
import { River } from "./river";

/**
 * The API's shape, which is not the renderer's.
 *
 * Edges arrive as `source_object_id`/`target_object_id`. The renderer wants
 * `source`/`target`, and the mismatch failed silently — every lookup returned
 * undefined and was skipped, so the HUD counted 115 links while none were drawn
 * and no attraction acted on the layout. Normalising here, at the boundary,
 * keeps the renderer independent of the transport.
 */
type ApiEdge = {
  id: string;
  source_object_id: string;
  target_object_id: string;
  relationship_type: string;
  confidence: number | null;
  edge_kind: "semantic" | "lineage";
};

type GraphPayload = {
  nodes: GraphNode[];
  edges: ApiEdge[];
  total_objects: number;
  truncated: boolean;
  note?: string | null;
};

/**
 * The section: two readings of the same objects, and the strip that chooses.
 *
 * §08 puts the river here rather than in the navigation — "a proposed lineage
 * view, with a contextual entrance from Overview and Research graph", and not
 * a sixth primary group — so this is one section with two views and the
 * secondary row keeps the eight items the master shows.
 *
 * They answer different questions and the strip says so. The canvas is "what
 * is this near?", laid out by similarity, with coordinates that mean nothing
 * scientific. The river is "what came from what?", laid out in recorded
 * stages, where every line is an edge somebody or something wrote down. A
 * researcher who cannot tell which one they are looking at will read a
 * generated layout as provenance, which is the confusion both this strip and
 * the river's own caveat exist to prevent.
 */
export function GraphView({ projectId, onSelect, focus = null, view = "graph", onView }: {
  projectId: string;
  onSelect: (id: string) => void;
  focus?: string | null;
  /** Which reading is open. Carried in the address by the workspace. */
  view?: View | null;
  onView?: (next: View) => void;
}) {
  return (
    <>
      {/*
        * A breadcrumb, not a second row of tabs.
        *
        * The master shows "Research graph / Project lineage" as one quiet line
        * above the title, and the way back is the first crumb. A pill strip
        * here cost a whole row of a 992px screen and read as a third level of
        * navigation under two that already exist — the exact "second permanent
        * vertical app navigation" §08 refuses, turned on its side.
        */}
      {onView && view === "river" && (
        <p className="crumbs">
          <button className="btn-text" type="button" onClick={() => onView("graph")}>
            Research graph
          </button>
          <span aria-hidden> / </span>
          <span aria-current="page">Project lineage</span>
        </p>
      )}
      {view === "river"
        ? <River projectId={projectId} focus={focus} onOpenObject={onSelect} />
        : (
          <>
            <GraphCanvas projectId={projectId} focus={focus} onSelect={onSelect} />
            {onView && (
              /* The contextual entrance §08 asks for, at the foot of the
                 canvas rather than competing with it. */
              <p className="note">
                To follow what was derived from what, in recorded stages,{" "}
                <button className="btn-text" type="button" onClick={() => onView("river")}>
                  open the project&rsquo;s lineage
                </button>.
              </p>
            )}
          </>
        )}
    </>
  );
}

function GraphCanvas({ projectId, onSelect, focus = null }: {
  projectId: string;
  onSelect: (id: string) => void;
  /**
   * An object to open on arrival: the one a journal entry named, or the one
   * the address bar carries after a reload. Without it a hand-off from another
   * screen landed on the graph and nothing else — the object the researcher
   * had clicked was somewhere in the layout, unmarked (D195).
   */
  focus?: string | null;
}) {
  const [limit, setLimit] = useState(120);
  const graph = useApi<GraphPayload>(
    `/api/projects/${projectId}/knowledge-graph?limit=${limit}`, [limit]);
  const [selected, setSelected] = useState<GraphNode | null>(null);
  // The node the journal is open on. Kept as an id rather than a node, so a
  // provenance link can open something the current view has not laid out.
  const [journalOn, setJournalOn] = useState<string | null>(focus);

  // Follow a later hand-off too, not only the first: the journal can name a
  // second object while this view is already mounted.
  useEffect(() => { if (focus) setJournalOn(focus); }, [focus]);

  // Double-click expands the neighbourhood. With no per-node expansion endpoint
  // yet, this raises the bound — honest, and it keeps the gesture live rather
  // than dead until the endpoint exists.
  const expand = useCallback(() => {
    setLimit((current) => Math.min(current * 2, 2000));
  }, []);

  const edges: GraphEdge[] = useMemo(
    () => (graph.data?.edges ?? []).map((e) => ({
      source: e.source_object_id,
      target: e.target_object_id,
      relationship_type: e.relationship_type,
      // Lineage is a definite relationship — this really was calculated from
      // that — so it pulls harder than an inferred semantic edge.
      similarity: e.edge_kind === "lineage" ? 0.8 : (e.confidence ?? 0.5),
    })),
    [graph.data],
  );

  const nodes = useMemo(
    () => (graph.data?.nodes ?? []).map((n) => ({
      ...n,
      // Degree stands in for citation count until one is recorded: it is the
      // honest available proxy for how connected an object is.
      importance: edges.filter(
        (e) => e.source === n.id || e.target === n.id).length,
    })),
    [graph.data, edges],
  );

  if (graph.error) return <Failure error={graph.error} retry={graph.reload} />;
  if (graph.loading && !graph.data) {
    return <Loading rows={5} label="Laying out the research graph" />;
  }
  if (!nodes.length) {
    return (
      <>
        <h1>Research graph</h1>
        <Empty
          title="Nothing to lay out yet"
          hint="Add sources and run discovery — every object and relationship in the project appears here."
        />
      </>
    );
  }

  return (
    <>
      <h1>Research graph</h1>
      <p className="lede">
        Every object in this project and the relationships between them. Proximity
        means similarity, size means how connected something is. Drag a node and the
        neighbourhood responds; double-click to pull in more.
      </p>
      {/*
        * Four surfaces in this product are graph-shaped, and each answers a
        * different question. Saying which costs a line here and saves a
        * researcher opening all four to find out — the same problem the two
        * chart catalogues had when they sat next to each other unexplained.
        */}
      <p className="note">
        This one is the whole project. To ask why a finding is believed, open
        the finding — its evidence graph walks back to the analyses and sources
        under it. For passages grouped by meaning rather than by link, use the
        embedding space; for your own writing, the notebook&rsquo;s graph.
      </p>

      <div className="kg-with-panel" data-open={journalOn !== null}>
        <div className="kg-canvas-slot">
          <KnowledgeGraph
            nodes={nodes}
            edges={edges}
            selectedId={journalOn ?? selected?.id ?? null}
            onSelect={(node) => {
              setSelected(node);
              // Selecting opens the journal. The graph is a place to write, and
              // a click that only highlights teaches the opposite.
              setJournalOn(node.id);
              onSelect(node.id);
            }}
            onExpand={expand}
          />
        </div>

        {journalOn && (
          <NodeJournal
            key={journalOn}
            projectId={projectId}
            objectId={journalOn}
            onClose={() => setJournalOn(null)}
            onOpen={(id) => setJournalOn(id)}
          />
        )}
      </div>

      {graph.data?.truncated && (
        <div className="notice">
          <span>{graph.data.note}</span>
          <button className="btn" onClick={expand}>Load more</button>
        </div>
      )}

      {/* Part P — the graph's contents as a real table. The canvas has no DOM,
          so without this the whole view is invisible to a screen reader and
          unreachable by keyboard. */}
      <details className="fold kg-table">
        <summary>Objects in this graph<span className="fold-count">· {nodes.length}</span></summary>
        <table>
          <thead>
            <tr><th style={{ width: "58%" }}>Object</th><th>Type</th>
                <th style={{ textAlign: "right" }}>Links</th></tr>
          </thead>
          <tbody>
            {nodes.map((node) => (
              <tr key={node.id} style={{ cursor: "pointer" }}
                  onClick={() => {
                    setSelected(node); setJournalOn(node.id); onSelect(node.id);
                  }}>
                <td>
                  <button type="button" className="pick"
                          onClick={() => {
                            setSelected(node); setJournalOn(node.id);
                            onSelect(node.id);
                          }}>
                    {node.title}
                  </button>
                </td>
                <td className="mono">{node.object_type.replace(/_/g, " ")}</td>
                <td className="numeric" style={{ textAlign: "right" }}>
                  {node.importance}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </>
  );
}
