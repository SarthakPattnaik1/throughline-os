"use client";

/**
 * P7 — hierarchies (Part F).
 *
 * Treemap, icicle, sunburst-less partition, dendrogram. One primitive: a tree
 * whose nodes carry a value, laid out so that containment means "is part of".
 *
 * **Negative values are refused, not absolved.** A treemap encodes value as
 * area, and area has no negative. Every library that meets a negative value
 * either takes its absolute value or drops it, and both produce a picture that
 * looks fine and is a lie — a −40 rendered at the size of a +40 reads as the
 * largest contributor when it is the largest *subtraction*. So a hierarchy
 * containing a negative value renders nothing and says why. This is the same
 * rule the rest of the system runs on: refuse rather than guess.
 *
 * **Area comparison is stated as weak.** Humans rank areas poorly and rank
 * rectangles of differing aspect ratio worse. A treemap is honest for "these
 * two things are roughly an order apart" and dishonest for "this is 12% bigger
 * than that". The caption says so, and the value is printed on every tile
 * large enough to hold it so the reader never has to estimate.
 *
 * Motion signature: tiles grow from their parent's rectangle, so subdivision
 * reads as *this broke into these* rather than as a new picture.
 */

import { useId, useMemo } from "react";
import { hierarchy, treemap, partition, cluster, HierarchyNode } from "d3-hierarchy";
import { categorical, seriesEdge } from "@/lib/tokens";
import { ChartTable } from "./ChartTable";
import { ChartTooltip, readable, useChartHover } from "./interaction";

export type TreeNode = {
  /** Stable identity. Object constancy depends on it. */
  id: string;
  /** Display name, never a raw column name (Part C). */
  label: string;
  /** Leaf magnitude. Ignored on internal nodes, which sum their children. */
  value?: number;
  children?: TreeNode[];
};

export type HierarchyLayout = "treemap" | "icicle" | "dendrogram";

const M = { top: 4, right: 4, bottom: 4, left: 4 };

/**
 * An elbow between two laid-out nodes.
 *
 * `d3-hierarchy` types `x`/`y` as optional because they are only populated once
 * a layout has run. It has, immediately above — but reading them through a
 * narrowing component keeps that assumption in one place instead of scattering
 * non-null assertions through the path string.
 */
function TreeLink({ source, target }: {
  source: HierarchyNode<TreeNode>;
  target: HierarchyNode<TreeNode>;
}) {
  const { x: sx, y: sy } = source as { x?: number; y?: number };
  const { x: tx, y: ty } = target as { x?: number; y?: number };
  if (sx === undefined || sy === undefined
      || tx === undefined || ty === undefined) return null;
  // Elbow, not a curve: a curve implies a continuum between two levels of a
  // tree, and there is none.
  return (
    <path className="tree-link"
          d={`M${sy},${sx}H${(sy + ty) / 2}V${tx}H${ty}`} />
  );
}

/** Depth-1 ancestor, which is what the palette keys on. */
function branchOf(node: HierarchyNode<TreeNode>): string {
  let cursor = node;
  while (cursor.depth > 1 && cursor.parent) cursor = cursor.parent;
  return cursor.data.id;
}

/**
 * Every leaf value that cannot be drawn as an area.
 *
 * Returned rather than thrown so the caller can name them: "three values are
 * negative" is actionable, "render failed" is not.
 */
function unrepresentable(root: TreeNode): string[] {
  const bad: string[] = [];
  const walk = (node: TreeNode) => {
    const leaf = !node.children || node.children.length === 0;
    if (leaf) {
      if (node.value === undefined || !Number.isFinite(node.value)) {
        bad.push(`${node.label} has no value`);
      } else if (node.value < 0) {
        bad.push(`${node.label} is ${node.value}`);
      }
    }
    node.children?.forEach(walk);
  };
  walk(root);
  return bad;
}

/** Every leaf beneath a node, flattened depth-first — the table reads sizes
    linearly, so containment is dropped and only label/value survive. */
function leavesOf(node: TreeNode): TreeNode[] {
  if (!node.children || node.children.length === 0) return [node];
  return node.children.flatMap(leavesOf);
}

export function Hierarchy({
  root, layout = "treemap", valueLabel, title, caption,
  width = 720, height = 420,
}: {
  root: TreeNode;
  layout?: HierarchyLayout;
  /** What the size means, in the reader's words. */
  valueLabel: string;
  title?: string;
  caption?: string;
  width?: number;
  height?: number;
}) {
  const hoverUI = useChartHover();
  const hit = leavesOf(root).find((n) => n.id === hoverUI.hovered) ?? null;
  const clipId = useId();
  const inner = { w: width - M.left - M.right, h: height - M.top - M.bottom };

  // Area has no negative, so a negative value cannot be drawn — and taking its
  // absolute value would render a subtraction as a contribution.
  const refusals = useMemo(() => unrepresentable(root), [root]);

  const laid = useMemo(() => {
    if (refusals.length) return null;
    const tree = hierarchy(root)
      .sum((d) => (d.children && d.children.length ? 0 : d.value ?? 0))
      .sort((a, b) => (b.value ?? 0) - (a.value ?? 0));

    if (layout === "dendrogram") {
      // A dendrogram encodes *structure*, not magnitude: leaves are evenly
      // spaced and depth is the only spatial variable.
      cluster<TreeNode>().size([inner.h, inner.w - 120])(tree);
      return tree;
    }
    if (layout === "icicle") {
      partition<TreeNode>().size([inner.h, inner.w])(tree);
      return tree;
    }
    treemap<TreeNode>().size([inner.w, inner.h]).paddingInner(2)
      .paddingTop(layout === "treemap" ? 16 : 2).round(true)(tree);
    return tree;
  }, [root, layout, inner.w, inner.h, refusals.length]);

  if (refusals.length || !laid) {
    return (
      <figure className="chart chart-refused">
        {title && <figcaption className="chart-title">{title}</figcaption>}
        <p className="chart-refusal">
          This hierarchy cannot be drawn as areas. A treemap encodes value as
          area and area has no negative, so drawing{" "}
          {refusals.length === 1 ? "this value" : "these values"} would show a
          subtraction at the size of a contribution:
        </p>
        <ul className="chart-refusal-list">
          {refusals.slice(0, 8).map((r) => <li key={r}>{r}</li>)}
          {refusals.length > 8 && <li>…and {refusals.length - 8} more</li>}
        </ul>
        <figcaption className="chart-caption">
          Show these as a bar chart, which has a baseline and can point
          downwards, or split the positive and negative parts into two
          hierarchies and say which is which.
        </figcaption>
      </figure>
    );
  }

  const total = laid.value ?? 0;
  const nodes = laid.descendants().filter((d) => d.depth > 0);
  const tableColumns = [
    { key: "label", header: "Label" },
    { key: "value", header: valueLabel, numeric: true },
  ];
  const tableRows = leavesOf(root).map((n) => ({ label: n.label, value: n.value }));
  const colour = (d: HierarchyNode<TreeNode>) => {
    const branches = laid.children ?? [];
    const i = branches.findIndex((b) => b.data.id === branchOf(d));
    return categorical[(i < 0 ? 0 : i) % categorical.length];
  };

  return (
    <figure className="chart">
      {title && <figcaption className="chart-title">{title}</figcaption>}

      <svg
        className="chart-svg" style={{ maxWidth: width }}
        viewBox={`0 0 ${width} ${height}`}
        width="100%"
        role="img"
        aria-label={
          `${title ?? "Hierarchy"}. ${laid.leaves().length} items in `
          + `${(laid.children ?? []).length} groups, ${total.toLocaleString()} `
          + `${valueLabel} in total. Size shows ${valueLabel}; containment shows `
          + `which group an item belongs to.`}
      >
        <defs>
          <clipPath id={clipId}>
            <rect x={0} y={0} width={inner.w} height={inner.h} />
          </clipPath>
        </defs>

        <g transform={`translate(${M.left},${M.top})`} clipPath={`url(#${clipId})`}>
          {layout === "dendrogram" ? (
            <>
              {laid.links().map((link) => (
                // Elbow, not a curve: a curve implies a continuum between two
                // levels of a tree, and there is none.
<TreeLink key={`${link.source.data.id}->${link.target.data.id}`}
                          source={link.source} target={link.target} />
              ))}
              {nodes.map((d) => (
                <g key={d.data.id} className="tree-node"
                   transform={`translate(${d.y},${d.x})`}>
                  <circle r={3.5} fill={colour(d)} stroke={seriesEdge(colour(d)) ?? undefined} />
                  <text x={d.children ? -8 : 8} dy="0.32em"
                        textAnchor={d.children ? "end" : "start"}>
                    {d.data.label}
                  </text>
                </g>
              ))}
            </>
          ) : (
            nodes.map((d) => {
              // treemap gives x0/y0/x1/y1; partition gives them transposed.
              const box = layout === "icicle"
                ? { x: (d as never as { y0: number }).y0,
                    y: (d as never as { x0: number }).x0,
                    w: (d as never as { y1: number }).y1 - (d as never as { y0: number }).y0,
                    h: (d as never as { x1: number }).x1 - (d as never as { x0: number }).x0 }
                : { x: (d as never as { x0: number }).x0,
                    y: (d as never as { y0: number }).y0,
                    w: (d as never as { x1: number }).x1 - (d as never as { x0: number }).x0,
                    h: (d as never as { y1: number }).y1 - (d as never as { y0: number }).y0 };
              const share = total ? ((d.value ?? 0) / total) * 100 : 0;
              // Only label a tile that can hold the label without clipping it.
              const roomy = box.w > 62 && box.h > 26;
              return (
                // Keyed by id: React moves the node rather than replacing it,
                // so a tile that still exists after a filter animates there.
                <g key={d.data.id} className="tree-tile"
                {...hoverUI.markProps(d.data.id)} style={{ opacity: hoverUI.emphasis(d.data.id) }}>
                  <rect
                    x={box.x} y={box.y}
                    width={Math.max(0, box.w)} height={Math.max(0, box.h)}
                    fill={colour(d)}
                    fillOpacity={d.children ? 0.16 : 0.82}
                    stroke={!d.children ? seriesEdge(colour(d)) ?? undefined : undefined}
                    rx={2}
                  />
                  {roomy && (
                    <>
                      <text x={box.x + 6} y={box.y + 14} className="tile-label">
                        {d.data.label}
                      </text>
                      {/* Printed, so nobody has to estimate an area. */}
                      <text x={box.x + 6} y={box.y + 27} className="tile-value numeric">
                        {(d.value ?? 0).toLocaleString()}
                        {share >= 1 && ` · ${share.toFixed(0)}%`}
                      </text>
                    </>
                  )}
                </g>
              );
            })
          )}
        </g>
      </svg>

      <figcaption className="chart-caption">
        {caption ? `${caption} ` : ""}
        Size shows {valueLabel}; total {total.toLocaleString()}.{" "}
        {layout === "dendrogram"
          ? "Position shows structure only — the spacing between leaves carries "
            + "no magnitude."
          : "Areas are reliable for large differences and unreliable for small "
            + "ones, so each value is printed rather than left to be judged by "
            + "eye. For ranking these precisely, use a bar chart."}
      </figcaption>

      <ChartTooltip pointer={hoverUI.pointer} title={hit?.label} rows={hit ? [{ label: valueLabel, value: readable(hit.value) }] : []} />

      <ChartTable
        highlightId={hoverUI.hovered}
        onHighlight={hoverUI.setHovered}
        columns={tableColumns}
        rows={tableRows}
        label={title ?? `${valueLabel} for each item in the hierarchy`}
      />
    </figure>
  );
}
