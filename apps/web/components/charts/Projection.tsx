"use client";

/**
 * P11 — embedding projections (Part F).
 *
 * Papers, passages or findings placed by semantic similarity: UMAP, t-SNE, PCA.
 *
 * **The axes carry no units and the chart says so out loud.**
 *
 * This is the most-misread chart in research software, and the misreading is
 * always the same three claims:
 *
 *   1. *"These two clusters are far apart, so they are very different."*
 *      False for UMAP and t-SNE. Both optimise local neighbourhoods; the
 *      distance *between* well-separated clusters is essentially arbitrary and
 *      changes between runs. Only PCA preserves global distance, and only in
 *      the directions it kept.
 *   2. *"This cluster is bigger, so it is more variable."*
 *      False. Cluster size and density in t-SNE are set by the perplexity
 *      parameter, not by the data's spread.
 *   3. *"There are four groups here."*
 *      Not necessarily. Both methods will produce visually clean clusters from
 *      data with no cluster structure at all, given the wrong parameters.
 *
 * None of that makes the chart useless — it is genuinely good at "what is near
 * this paper?" So the primitive is built for the question it can answer and
 * fenced against the three it cannot: axes are drawn without ticks or units,
 * the parameters that produced the layout are printed as part of the figure,
 * and neighbour links are drawn *on demand* from the original high-dimensional
 * distances rather than from the 2-D positions.
 *
 * That last point is the substantive one. When a reader asks "what is near
 * this?", answering from the picture would repeat the projection's distortion
 * back to them. The neighbours are computed in the source space.
 */

import { useId, useMemo, useState } from "react";
import { extent } from "d3-array";
import { scaleLinear } from "d3-scale";
import { categorical, seriesEdge } from "@/lib/tokens";
import { ChartTable } from "./ChartTable";
import { ChartTooltip, readable, useChartHover } from "./interaction";

export type Projected = {
  /** Stable identity. Object constancy depends on it. */
  id: string;
  /** Display name, never a raw identifier (Part C). */
  label: string;
  x: number;
  y: number;
  group?: string;
  /**
   * Nearest neighbours computed in the ORIGINAL embedding space, with their
   * cosine distances. Reading neighbours off the 2-D plot would hand the
   * projection's distortion back to the reader as if it were a result.
   */
  neighbours?: Array<{ id: string; distance: number }>;
};

export type ProjectionMethod = "umap" | "tsne" | "pca";

const M = { top: 12, right: 12, bottom: 12, left: 12 };

const GLOBAL_DISTANCE_MEANS_NOTHING: Record<ProjectionMethod, boolean> = {
  umap: true, tsne: true, pca: false,
};

export function Projection({
  points, method, parameters, varianceExplained, title, caption,
  width = 720, height = 520, onSelect,
}: {
  points: Projected[];
  method: ProjectionMethod;
  /** The settings that produced this layout. Part of the figure, not a tooltip. */
  parameters: Record<string, string | number>;
  /** PCA only: the share of variance the two drawn axes actually keep. */
  varianceExplained?: [number, number];
  title?: string;
  caption?: string;
  width?: number;
  height?: number;
  onSelect?: (id: string) => void;
}) {
  const hoverUI = useChartHover();
  const hit = points.find((q) => q.id === hoverUI.hovered) ?? null;
  const clipId = useId();
  const [focus, setFocus] = useState<string | null>(null);
  const inner = { w: width - M.left - M.right, h: height - M.top - M.bottom };

  const { xScale, yScale } = useMemo(() => {
    const [x0, x1] = extent(points, (p) => p.x) as [number, number];
    const [y0, y1] = extent(points, (p) => p.y) as [number, number];
    // Equal aspect: the axes have no units, so an unequal aspect would stretch
    // one direction and invent structure that is not in the projection.
    const span = Math.max(x1 - x0, y1 - y0) || 1;
    const padX = (span - (x1 - x0)) / 2 + span * 0.06;
    const padY = (span - (y1 - y0)) / 2 + span * 0.06;
    return {
      xScale: scaleLinear().domain([x0 - padX, x1 + padX]).range([0, inner.w]),
      yScale: scaleLinear().domain([y0 - padY, y1 + padY]).range([inner.h, 0]),
    };
  }, [points, inner.w, inner.h]);

  const groups = useMemo(
    () => [...new Set(points.map((p) => p.group).filter(Boolean))] as string[],
    [points]);

  const focused = focus ? points.find((p) => p.id === focus) ?? null : null;
  const byId = useMemo(() => new Map(points.map((p) => [p.id, p])), [points]);

  const hasGroup = points.some((p) => p.group !== undefined);
  const tableColumns = [
    { key: "label", header: "Label" },
    { key: "x", header: "x", numeric: true },
    { key: "y", header: "y", numeric: true },
    ...(hasGroup ? [{ key: "group", header: "Group" }] : []),
  ];
  const tableRows = points.map((p) => ({
    id: p.id, label: p.label, x: p.x, y: p.y, group: p.group,
  }));

  return (
    <figure className="chart">
      {title && <figcaption className="chart-title">{title}</figcaption>}

      <svg
        className="chart-svg projection" width="100%"
        viewBox={`0 0 ${width} ${height}`} role="img"
        aria-label={
          `${title ?? "Embedding projection"}. ${points.length} items placed by `
          + `similarity using ${method.toUpperCase()}. The axes have no units. `
          + `Distance between nearby points is meaningful; distance between `
          + `distant clusters is not.`}
      >
        <defs><clipPath id={clipId}>
          <rect x={0} y={0} width={inner.w} height={inner.h} />
        </clipPath></defs>

        <g transform={`translate(${M.left},${M.top})`}>
          {/* A frame, not axes. There are no ticks because there are no units,
              and drawing a numbered axis here would invent a scale. */}
          <rect x={0} y={0} width={inner.w} height={inner.h}
                className="projection-frame" fill="none" />

          <g clipPath={`url(#${clipId})`}>
            {/* Neighbour links, drawn from distances in the ORIGINAL space —
                never from the positions on screen. */}
            {focused?.neighbours?.map((n) => {
              const other = byId.get(n.id);
              if (!other) return null;
              return (
                <line
                  key={`${focused.id}-${n.id}`}
                  className="projection-link"
                  x1={xScale(focused.x)} y1={yScale(focused.y)}
                  x2={xScale(other.x)} y2={yScale(other.y)}
                  strokeOpacity={Math.max(0.15, 1 - n.distance)}
                />
              );
            })}

            {points.map((p) => {
              const near = focused?.neighbours?.some((n) => n.id === p.id);
              const dim = focused && !near && p.id !== focused.id;
              return (
                // Keyed by id: a point that survives a filter is the same DOM
                // node and moves, rather than being removed and re-added.
                <circle
                  key={p.id}
                {...hoverUI.markProps(p.id)}
                style={{ opacity: hoverUI.emphasis(p.id) }}
                  className="projection-point"
                  cx={xScale(p.x)} cy={yScale(p.y)}
                  r={p.id === focus ? 7 : 4}
                  fill={p.group
                    ? categorical[groups.indexOf(p.group) % categorical.length]
                    : categorical[0]}
                  fillOpacity={dim ? 0.14 : 1}
                  stroke={seriesEdge(p.group
                    ? categorical[groups.indexOf(p.group) % categorical.length]
                    : categorical[0]) ?? undefined}
                  tabIndex={0}
                  role="button"
                  aria-label={`${p.label}${p.group ? `, ${p.group}` : ""}`}
                  onMouseEnter={() => setFocus(p.id)}
                  onFocus={() => setFocus(p.id)}
                  onMouseLeave={() => setFocus(null)}
                  onBlur={() => setFocus(null)}
                  onClick={() => onSelect?.(p.id)}
                >
                  <title>{p.label}</title>
                </circle>
              );
            })}
          </g>
        </g>
      </svg>

      {/* The nearest neighbours, named and with their real distances — the
          question this chart can actually answer. */}
      {focused && (
        <div className="projection-readout">
          <b>{focused.label}</b>
          {focused.neighbours?.length ? (
            <>
              {" "}— nearest in the embedding itself:{" "}
              {focused.neighbours.slice(0, 5).map((n, i) => (
                <span key={n.id}>
                  {i > 0 && ", "}
                  {byId.get(n.id)?.label ?? n.id}{" "}
                  <span className="numeric">({n.distance.toFixed(3)})</span>
                </span>
              ))}
            </>
          ) : (
            " — neighbour distances were not computed for this projection."
          )}
        </div>
      )}

      <figcaption className="chart-caption">
        {caption ? `${caption} ` : ""}
        {points.length.toLocaleString()} items placed by{" "}
        {method === "pca" ? "PCA" : method === "umap" ? "UMAP" : "t-SNE"} (
        {Object.entries(parameters).map(([k, v]) => `${k} ${v}`).join(", ")}).
        The axes have no units and are not labelled, because they have none.{" "}
        {GLOBAL_DISTANCE_MEANS_NOTHING[method] ? (
          <>
            <b>Distance between separate clusters carries no meaning here.</b>{" "}
            {method === "umap" ? "UMAP" : "t-SNE"} preserves which points are
            near each other, not how far apart groups are — that spacing changes
            between runs on identical data. Cluster size and density are set by
            the parameters above, not by the spread of the data, and both
            methods can produce clean-looking clusters from data with no cluster
            structure. Hover a point to see its true nearest neighbours,
            computed in the original embedding rather than read off this picture.
          </>
        ) : (
          <>
            PCA keeps global distance, so spacing here is meaningful — but only
            within the{" "}
            {varianceExplained
              ? `${((varianceExplained[0] + varianceExplained[1]) * 100).toFixed(0)}% `
                + `of variance these two components retain (`
                + `${(varianceExplained[0] * 100).toFixed(0)}% and `
                + `${(varianceExplained[1] * 100).toFixed(0)}%)`
              : "variance these two components retain"}
            . Structure living in the discarded directions is not visible here.
          </>
        )}
      </figcaption>

      <ChartTooltip pointer={hoverUI.pointer} title={hit?.label} rows={hit ? [{ label: "x", value: readable(hit.x) }, { label: "y", value: readable(hit.y) }] : []} />

      <ChartTable
        highlightId={hoverUI.hovered}
        onHighlight={hoverUI.setHovered}
        columns={tableColumns}
        rows={tableRows}
        label={title ?? `${points.length} items placed by ${method.toUpperCase()}`}
        note="Coordinates are relative to the projection, not a physical scale."
      />
    </figure>
  );
}
