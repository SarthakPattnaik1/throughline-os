"use client";

/**
 * The notebook graph — an Obsidian-style view that stays useful past 100 notes.
 *
 * Obsidian's graph is the most screenshotted feature in note-taking and one of
 * the least used for work, and the reason is structural rather than visual: in
 * a vault every edge means the same thing — a person typed it — so there is
 * nothing to filter on. At 150 notes it is a uniform grey hairball. More nodes
 * make it worse, and no amount of physics tuning fixes a graph with one edge
 * type.
 *
 * Here edges have kinds, and that is the whole difference:
 *
 * - **asserted** — you wrote `[[this]]`. A claim about relatedness.
 * - **computed** — the system derived one artifact from another. Provenance,
 *   and the thing Law 1 rests on.
 *
 * Being able to see those separately is what turns the view from decoration
 * into a tool. "Show only what I asserted" is my thinking. "Show only what was
 * computed" is the audit trail. Overlaying them answers the question that
 * actually matters when writing up: *where does what I believe line up with
 * what the data actually supports, and where does it not?*
 *
 * The two are never drawn identically. If they were, the graph would be
 * quietly claiming that a hunch and a derivation are the same kind of fact.
 */

import { useEffect, useMemo, useState } from "react";
import { TabPanel, ViewTabs } from "./ViewTabs";
import { api } from "@/lib/api";
import { GraphEdge, GraphNode, KnowledgeGraph } from "./KnowledgeGraph";
import { Empty, Failure, Loading } from "./primitives";

type NoteGraph = {
  nodes: Array<{ id: string; title: string; object_type: string;
                 note_kind: string }>;
  edges: Array<{ source: string; target: string; relationship_type: string;
                 edge_kind: string; similarity: number }>;
  note: string;
};

type ResearchGraph = {
  nodes: GraphNode[];
  edges: Array<{ id: string; source_object_id: string;
                 target_object_id: string; relationship_type: string;
                 confidence: number | null; edge_kind: string }>;
  /**
   * Whether this is the whole graph, and the server's own sentence saying so.
   *
   * `graphs.py` caps the node count and computes all three of these — its
   * comment is "say when the view is partial rather than implying
   * completeness" — including a finished sentence: "Showing 200 of 412
   * objects. Expand from a node to load more."
   *
   * This declaration named none of them, so all three were dropped on arrival
   * and a truncated graph rendered as if it were the project. The work of
   * being honest about it had already been done one layer down; nothing
   * carried it to the screen.
   */
  truncated: boolean;
  total_objects: number;
  note: string | null;
};

type Lens = "asserted" | "computed" | "both";

const LENS_LABEL: Record<Lens, string> = {
  asserted: "What I linked",
  computed: "What was computed",
  both: "Both, overlaid",
};

const LENS_NOTE: Record<Lens, string> = {
  asserted:
    "Only the links you typed. This is your thinking — it makes no claim about "
    + "what the data supports.",
  computed:
    "Only relationships the system derived. This is the provenance graph, and "
    + "it is what a result traces through.",
  both:
    "Your links and the computed provenance together. Where a note sits beside "
    + "the artifacts it talks about, your thinking and the record agree; where "
    + "it floats alone, one of the two has some catching up to do.",
};

export function NoteGraph({ projectId }: { projectId: string }) {
  const [lens, setLens] = useState<Lens>("both");
  const [notes, setNotes] = useState<NoteGraph | null>(null);
  const [research, setResearch] = useState<ResearchGraph | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let live = true;
    Promise.all([
      api.get<NoteGraph>(`/api/projects/${projectId}/notebook/graph`),
      api.get<ResearchGraph>(
        `/api/projects/${projectId}/knowledge-graph?limit=300`),
    ])
      .then(([n, r]) => { if (live) { setNotes(n); setResearch(r); } })
      .catch((err) => { if (live) setError(err); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [projectId]);

  const { nodes, edges } = useMemo(() => {
    const noteNodes: GraphNode[] = (notes?.nodes ?? []).map((n) => ({
      id: n.id, title: n.title, object_type: "note", importance: 3,
    }));
    const researchNodes: GraphNode[] = research?.nodes ?? [];

    const asserted: GraphEdge[] = (notes?.edges ?? []).map((e) => ({
      source: e.source, target: e.target,
      relationship_type: e.relationship_type,
      // Weaker pull than provenance. An assertion should place a note near what
      // it talks about without dragging computed structure out of shape.
      similarity: 0.45,
    }));
    const computed: GraphEdge[] = (research?.edges ?? []).map((e) => ({
      source: e.source_object_id, target: e.target_object_id,
      relationship_type: e.relationship_type,
      similarity: e.edge_kind === "lineage" ? 0.85 : (e.confidence ?? 0.5),
    }));

    if (lens === "asserted") {
      // Only the objects an assertion actually touches, so the researcher's own
      // links are not lost in a field of unrelated artifacts.
      const touched = new Set(asserted.flatMap((e) => [e.source, e.target]));
      return {
        nodes: [...noteNodes,
                ...researchNodes.filter((n) => touched.has(n.id))],
        edges: asserted,
      };
    }
    if (lens === "computed") {
      return { nodes: researchNodes, edges: computed };
    }
    return { nodes: [...researchNodes, ...noteNodes],
             edges: [...computed, ...asserted] };
  }, [notes, research, lens]);

  if (loading) return <Loading rows={4} label="Laying out the notebook" />;
  if (error) return <Failure error={error} />;

  if (nodes.length === 0) {
    return (
      <Empty
        title="Nothing to lay out yet"
        hint="Write a note and link something with [[double brackets]] — the graph draws what you connect."
      />
    );
  }

  return (
    <>
      <ViewTabs
        name="notegraph" label="Which links to show"
        value={lens} onChange={setLens}
        options={(["both", "asserted", "computed"] as const)
          .map((option) => [option, LENS_LABEL[option]] as const)}
      />
      <TabPanel name="notegraph" value={lens}>

        {/* The sentence changes with the lens, because what the picture means
            changes with it — and a graph that looks the same while meaning
            something different is worse than two graphs. */}
        <p className="ng-note">{LENS_NOTE[lens]}</p>

        <KnowledgeGraph
          nodes={nodes}
          edges={edges}
          height={560}
          edgeEmphasis={lens === "asserted" ? 3 : 1.6}
        />

        {/* Part P — the canvas has no DOM, so the same content exists as a real
            table. Without it this view is invisible to a screen reader and
            unreachable by keyboard, and it is the one I forgot when I built it. */}
        <details className="fold kg-table">
          <summary>Everything in this graph<span className="fold-count">· {nodes.length}</span></summary>
          <table>
            <thead>
              <tr><th style={{ width: "62%" }}>Object</th><th>Type</th>
                  <th style={{ textAlign: "right" }}>Links</th></tr>
            </thead>
            <tbody>
              {nodes.map((node) => (
                <tr key={node.id}>
                  <td>{node.title}</td>
                  <td className="mono">{node.object_type.replace(/_/g, " ")}</td>
                  <td className="numeric" style={{ textAlign: "right" }}>
                    {edges.filter((e) => e.source === node.id
                                      || e.target === node.id).length}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>

        {/* Above the counts, not among them: that a view is partial changes
            how every number under it should be read. */}
        {research?.truncated && research.note && (
          <p className="ng-partial" role="status">{research.note}</p>
        )}

        <p className="pat-foot">
          {notes?.note}{" "}
          {lens === "both" && (
            <>
              {(notes?.edges.length ?? 0).toLocaleString()} links you typed,{" "}
              {(research?.edges.length ?? 0).toLocaleString()} computed.
            </>
          )}
        </p>
      </TabPanel>
    </>
  );
}
