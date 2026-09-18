"use client";

/**
 * P9 — ribbon flows (Part F).
 *
 * Sankey, alluvial, cohort flow, attrition. One primitive: quantities moving
 * between states, where the width of a ribbon is the quantity carried.
 *
 * **Conservation is checked, and a leak is reported rather than absorbed.**
 *
 * This is the whole reason the primitive is worth building. A Sankey diagram's
 * visual grammar promises that what enters a node leaves it — that is what
 * makes the width mean anything. But every layout engine, given inputs that do
 * not balance, silently resizes the node to fit whatever it was handed. The
 * picture stays beautiful and the arithmetic is wrong, and the reader has no
 * way to see it.
 *
 * For a research tool this matters concretely: a participant-flow diagram that
 * does not balance means people were lost between stages and nobody said so.
 * That is the CONSORT diagram's entire job. So every node is checked, and any
 * imbalance is named in the caption with its size and direction — and where
 * the imbalance is unaccounted attrition, that is the finding, not a defect in
 * the chart.
 *
 * **Cycles are refused.** A Sankey layout requires a DAG. Given a cycle, layout
 * libraries either hang or silently drop an edge; dropping an edge from a flow
 * diagram changes the totals. Detected, named, refused.
 */

import { useId, useMemo } from "react";
import { sankey, sankeyLinkHorizontal, sankeyJustify } from "d3-sankey";
import { categorical, seriesEdge, seriesStroke } from "@/lib/tokens";
import { ChartTable } from "./ChartTable";
import { ChartTooltip, readable, useChartHover } from "./interaction";

export type FlowNode = {
  /** Stable identity. Object constancy depends on it. */
  id: string;
  /** Display name, never a raw column name (Part C). */
  label: string;
};

export type FlowLink = { source: string; target: string; value: number };

/**
 * How much passes through one node.
 *
 * The larger of what arrives and what leaves, rather than their sum: a node in
 * the middle of a flow would otherwise report double its own throughput, and a
 * reader comparing a middle node with an endpoint would be comparing two
 * different quantities.
 */
export function flowThrough(links: FlowLink[], id: string): number {
  let incoming = 0;
  let outgoing = 0;
  for (const link of links) {
    if (link.target === id) incoming += link.value;
    if (link.source === id) outgoing += link.value;
  }
  return Math.max(incoming, outgoing);
}

type Laid = {
  id: string; label: string; index: number;
  x0: number; x1: number; y0: number; y1: number;
  value: number;
};

const M = { top: 8, right: 8, bottom: 8, left: 8 };

/** Node ids that sit on a cycle, which a flow layout cannot represent. */
function findCycle(nodes: FlowNode[], links: FlowLink[]): string[] {
  const out = new Map<string, string[]>();
  nodes.forEach((n) => out.set(n.id, []));
  links.forEach((l) => out.get(l.source)?.push(l.target));

  const state = new Map<string, 0 | 1 | 2>();
  const stack: string[] = [];
  let cycle: string[] = [];

  const visit = (id: string): boolean => {
    state.set(id, 1);
    stack.push(id);
    for (const next of out.get(id) ?? []) {
      if (state.get(next) === 1) {
        cycle = stack.slice(stack.indexOf(next)).concat(next);
        return true;
      }
      if (!state.get(next) && visit(next)) return true;
    }
    stack.pop();
    state.set(id, 2);
    return false;
  };

  for (const n of nodes) if (!state.get(n.id) && visit(n.id)) break;
  return cycle;
}

/**
 * Nodes where what comes in does not match what goes out.
 *
 * Sources and sinks are exempt — a node with no inbound edges is where the
 * quantity enters the diagram, and one with no outbound edges is where it
 * leaves. Everything between them must balance.
 */
function imbalances(nodes: FlowNode[], links: FlowLink[]) {
  const inflow = new Map<string, number>();
  const outflow = new Map<string, number>();
  links.forEach((l) => {
    outflow.set(l.source, (outflow.get(l.source) ?? 0) + l.value);
    inflow.set(l.target, (inflow.get(l.target) ?? 0) + l.value);
  });
  return nodes.flatMap((n) => {
    const into = inflow.get(n.id);
    const from = outflow.get(n.id);
    if (into === undefined || from === undefined) return [];  // source or sink
    const delta = from - into;
    // Floating point: a Sankey built from percentages will not land exactly.
    if (Math.abs(delta) < Math.max(1e-9, into * 1e-9)) return [];
    return [{ label: n.label, into, from, delta }];
  });
}

export function Ribbon({
  nodes, links, unitLabel, title, caption, width = 760, height = 420,
  attritionIsExpected = false,
}: {
  nodes: FlowNode[];
  links: FlowLink[];
  /** What is flowing, in the reader's words — "participants", "citations". */
  unitLabel: string;
  title?: string;
  caption?: string;
  width?: number;
  height?: number;
  /**
   * When true, a node that emits less than it received is treated as measured
   * attrition and reported as a quantity rather than as a defect. This is the
   * CONSORT case: people genuinely leave a study between stages.
   */
  attritionIsExpected?: boolean;
}) {
  const hoverUI = useChartHover();
  const hit = nodes.find((n) => n.id === hoverUI.hovered) ?? null;
  const clipId = useId();
  const cycle = useMemo(() => findCycle(nodes, links), [nodes, links]);
  const leaks = useMemo(() => imbalances(nodes, links), [nodes, links]);

  const laid = useMemo(() => {
    if (cycle.length) return null;
    const generator = sankey<{ id: string; label: string }, { value: number }>()
      .nodeId((d) => d.id)
      .nodeAlign(sankeyJustify)
      .nodeWidth(14)
      .nodePadding(14)
      .extent([[M.left, M.top], [width - M.right, height - M.bottom]]);
    try {
      return generator({
        nodes: nodes.map((n) => ({ ...n })),
        // Ids, not indices. `nodeId` above tells d3-sankey to resolve links by
        // id; handing it indices as well makes every link resolve to the wrong
        // node or to none, and the layout throws rather than drawing wrongly.
        links: links.map((l) => ({ ...l })),
      } as never) as never as {
        nodes: Laid[];
        links: Array<{ width: number; value: number;
                       source: Laid; target: Laid }>;
      };
    } catch {
      return null;
    }
  }, [nodes, links, width, height, cycle.length]);

  if (cycle.length || !laid) {
    return (
      <figure className="chart chart-refused">
        {title && <figcaption className="chart-title">{title}</figcaption>}
        <p className="chart-refusal">
          {cycle.length
            ? "These flows contain a cycle, and a flow diagram cannot show one."
            : "These flows could not be laid out."}
        </p>
        {cycle.length > 0 && (
          <p className="chart-refusal-detail">
            {cycle
              .map((id) => nodes.find((n) => n.id === id)?.label ?? id)
              .join(" → ")}
          </p>
        )}
        <figcaption className="chart-caption">
          A ribbon diagram reads left to right and assumes quantity never
          returns to a state it has left. Drawing this would mean dropping one
          of these edges, which would change the totals — so nothing is drawn.
          Show the cycle as a node-link graph, or split the recurring state into
          one node per pass.
        </figcaption>
      </figure>
    );
  }

  const total = laid.nodes
    .filter((n) => !laid.links.some((l) => l.target.id === n.id))
    .reduce((sum, n) => sum + n.value, 0);

  // The table is built from the original links, not the laid-out ones — the
  // ids are resolved to display labels the same way the refusal caption does.
  const nodeLabel = (id: string) => nodes.find((n) => n.id === id)?.label ?? id;
  const tableColumns = [
    { key: "source", header: "From" },
    { key: "target", header: "To" },
    { key: "value", header: unitLabel, numeric: true },
  ];
  const tableRows = links.map((l) => ({
    source: nodeLabel(l.source), target: nodeLabel(l.target), value: l.value,
  }));

  return (
    <figure className="chart">
      {title && <figcaption className="chart-title">{title}</figcaption>}

      <svg
        className="chart-svg" style={{ maxWidth: width }} width="100%" viewBox={`0 0 ${width} ${height}`} role="img"
        aria-label={
          `${title ?? "Flow diagram"}. ${total.toLocaleString()} ${unitLabel} `
          + `entering, flowing through ${laid.nodes.length} stages. `
          + (leaks.length
             ? `${leaks.length} stage${leaks.length > 1 ? "s do" : " does"} not balance.`
             : "Every stage balances: what enters leaves.")}
      >
        <defs><clipPath id={clipId}>
          <rect x={0} y={0} width={width} height={height} />
        </clipPath></defs>

        <g clipPath={`url(#${clipId})`}>
          {laid.links.map((l) => (
            // Keyed by endpoints, so a ribbon that survives a filter is the
            // same DOM node and animates rather than being redrawn.
            <path
              key={`${l.source.id}->${l.target.id}`}
              className="ribbon-link"
              d={sankeyLinkHorizontal()(l as never) ?? undefined}
              fill="none"
              stroke={seriesStroke(categorical[l.source.index % categorical.length])}
              strokeOpacity={0.9}
              strokeWidth={Math.max(1, l.width)}
            >
              <title>
                {l.source.label} → {l.target.label}: {l.value.toLocaleString()} {unitLabel}
              </title>
            </path>
          ))}

          {laid.nodes.map((n) => (
            <g key={n.id} className="ribbon-node"
                {...hoverUI.markProps(n.id)} style={{ opacity: hoverUI.emphasis(n.id) }}>
              <rect x={n.x0} y={n.y0} width={n.x1 - n.x0}
                    height={Math.max(1, n.y1 - n.y0)}
                    fill={categorical[n.index % categorical.length]} rx={1}
                    stroke={seriesEdge(categorical[n.index % categorical.length]) ?? undefined} />
              <text
                x={n.x0 < width / 2 ? n.x1 + 6 : n.x0 - 6}
                y={(n.y0 + n.y1) / 2} dy="0.32em"
                textAnchor={n.x0 < width / 2 ? "start" : "end"}
                className="chart-axis-label"
              >
                {n.label}
                <tspan className="numeric" dx={6}>{n.value.toLocaleString()}</tspan>
              </text>
            </g>
          ))}
        </g>
      </svg>

      <figcaption className="chart-caption">
        {caption ? `${caption} ` : ""}
        Ribbon width is {unitLabel}; {total.toLocaleString()} enter at the left.{" "}
        {leaks.length === 0 ? (
          <>Every stage balances — what enters each one leaves it.</>
        ) : attritionIsExpected ? (
          <>
            {leaks.length} stage{leaks.length > 1 ? "s lose" : " loses"} {unitLabel}{" "}
            between entering and leaving:{" "}
            {leaks.map((l) => `${l.label} (${Math.abs(l.delta).toLocaleString()} `
              + `${l.delta < 0 ? "unaccounted" : "added"})`).join(", ")}
            . That is measured attrition, not a drawing error — it is reported
            here because a flow diagram that hides it is the defect a CONSORT
            diagram exists to prevent.
          </>
        ) : (
          <>
            <b>These flows do not balance.</b>{" "}
            {leaks.map((l) => `${l.label} received ${l.into.toLocaleString()} and `
              + `emits ${l.from.toLocaleString()}`).join("; ")}
            . The widths above are drawn from the numbers given, so the diagram
            is internally consistent and the underlying totals are not. Either
            some {unitLabel} are unaccounted for, or a stage is missing.
          </>
        )}
      </figcaption>

      {/*
        * The flow through the hovered node, not its id. A tooltip reporting
        * `id: cohort_a` tells a reader something they can see on the axis, and
        * this file's own rule for a node label is "never a raw column name" —
        * which the identifier is. The number they cannot see is how much goes
        * through it.
        */}
      <ChartTooltip
        pointer={hoverUI.pointer}
        title={hit?.label}
        rows={hit
          ? [{ label: "total flow", value: readable(flowThrough(links, hit.id)) }]
          : []}
      />

      <ChartTable
        highlightId={hoverUI.hovered}
        onHighlight={hoverUI.setHovered}
        columns={tableColumns}
        rows={tableRows}
        label={title ?? `${unitLabel} flowing between stages`}
      />
    </figure>
  );
}
