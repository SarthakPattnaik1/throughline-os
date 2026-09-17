/**
 * The force layout, off the main thread.
 *
 * `DEFAULT_LAYOUT` used to claim its pass count ran "inside a frame budget"
 * and that "a layout that took a second would be computed on a background
 * thread". Measured, it took 1.4s at two thousand nodes and 6s at five
 * thousand, synchronously — the interface frozen for the whole of it, unable
 * to scroll, resize or answer a key. Barnes-Hut halved the cost and did not
 * change that: half of a frozen interface is a frozen interface.
 *
 * This is the other half of that note, finally done. The graph goes across as
 * plain data, the positions come back, and the page stays alive while it
 * settles.
 */

import { layoutGraph, type Graph, type Layout } from "./network";

export type LayoutRequest = { id: number; graph: Graph };
export type LayoutResponse =
  | { id: number; ok: true; nodes: Layout["nodes"]; dangling: Layout["dangling"] }
  | { id: number; ok: false; because: string };

self.onmessage = (event: MessageEvent<LayoutRequest>) => {
  if (event.origin !== "" && event.origin !== self.location.origin) return;
  const payload = event.data;
  if (!payload || !Number.isInteger(payload.id) || !payload.graph
      || typeof payload.graph !== "object") return;
  const { id, graph } = payload;
  try {
    const laid = layoutGraph(graph);
    /*
     * Only the nodes and the danglers cross back. The edges hold references to
     * node objects, and structured clone would copy each one separately — so
     * `edges[i].from` would arrive as a *different object* from the matching
     * entry in `nodes`, and anything comparing them by identity would quietly
     * disagree. They are re-linked by id on the other side instead.
     */
    const reply: LayoutResponse = {
      id, ok: true, nodes: laid.nodes, dangling: laid.dangling };
    (self as unknown as Worker).postMessage(reply);
  } catch (error) {
    (self as unknown as Worker).postMessage({
      id, ok: false,
      because: error instanceof Error ? error.message : String(error),
    } satisfies LayoutResponse);
  }
};
