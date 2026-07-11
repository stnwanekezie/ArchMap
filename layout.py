"""Force-directed layout baked at build time (no networkx dependency).

A small Fruchterman-Reingold implementation with grid-bucketed repulsion so
the O(n^2) all-pairs cost collapses to near-linear for the ~1k backend nodes.
Nodes of the same layer get a mild shared gravity well, so the drawing clusters
by architectural layer without a separate grouping pass. Positions are written
back onto ``Node.x`` / ``Node.y`` in place; the viewer renders them statically.
"""

from __future__ import annotations

import math
import random

from .extract import GraphData


def apply_layout(
    graph: GraphData,
    *,
    iterations: int = 320,
    seed: int = 7,
    width: float = 4000.0,
    height: float = 4000.0,
) -> None:
    """Compute and store (x, y) for every node deterministically."""
    rng = random.Random(seed)
    nodes = graph.nodes
    n = len(nodes)
    if n == 0:
        return

    idx = {node.id: i for i, node in enumerate(nodes)}
    xs = [rng.uniform(0, width) for _ in range(n)]
    ys = [rng.uniform(0, height) for _ in range(n)]

    # Layer anchors give same-layer nodes a shared gravity target.
    layers = sorted({node.layer for node in nodes})
    ring = len(layers)
    anchors: dict[str, tuple[float, float]] = {}
    for i, layer in enumerate(layers):
        ang = 2 * math.pi * i / max(ring, 1)
        anchors[layer] = (
            width / 2 + math.cos(ang) * width * 0.32,
            height / 2 + math.sin(ang) * height * 0.32,
        )

    edges = [(idx[e.source], idx[e.target]) for e in graph.edges
             if e.source in idx and e.target in idx]

    k = math.sqrt((width * height) / n)  # ideal edge length
    cell = k
    temp = width / 10.0
    cool = temp / (iterations + 1)

    for _ in range(iterations):
        dx = [0.0] * n
        dy = [0.0] * n

        # Repulsion via a spatial hash: only compare nodes in neighbouring cells.
        buckets: dict[tuple[int, int], list[int]] = {}
        for i in range(n):
            key = (int(xs[i] // cell), int(ys[i] // cell))
            buckets.setdefault(key, []).append(i)

        for (cx, cy), members in buckets.items():
            neigh = []
            for ox in (-1, 0, 1):
                for oy in (-1, 0, 1):
                    neigh.extend(buckets.get((cx + ox, cy + oy), ()))
            for i in members:
                for j in neigh:
                    if i >= j:
                        continue
                    ddx = xs[i] - xs[j]
                    ddy = ys[i] - ys[j]
                    dist2 = ddx * ddx + ddy * ddy
                    if dist2 < 1e-6:
                        ddx = rng.uniform(-0.5, 0.5)
                        ddy = rng.uniform(-0.5, 0.5)
                        dist2 = ddx * ddx + ddy * ddy
                    dist = math.sqrt(dist2)
                    force = (k * k) / dist
                    fx = ddx / dist * force
                    fy = ddy / dist * force
                    dx[i] += fx
                    dy[i] += fy
                    dx[j] -= fx
                    dy[j] -= fy

        # Attraction along edges.
        for i, j in edges:
            ddx = xs[i] - xs[j]
            ddy = ys[i] - ys[j]
            dist = math.hypot(ddx, ddy) or 0.01
            force = (dist * dist) / k
            fx = ddx / dist * force
            fy = ddy / dist * force
            dx[i] -= fx
            dy[i] -= fy
            dx[j] += fx
            dy[j] += fy

        # Layer gravity.
        for i, node in enumerate(nodes):
            ax, ay = anchors[node.layer]
            dx[i] += (ax - xs[i]) * 0.012
            dy[i] += (ay - ys[i]) * 0.012

        # Apply, capped by temperature.
        for i in range(n):
            disp = math.hypot(dx[i], dy[i]) or 0.01
            xs[i] += dx[i] / disp * min(disp, temp)
            ys[i] += dy[i] / disp * min(disp, temp)

        temp -= cool

    for i, node in enumerate(nodes):
        node.x = round(xs[i], 1)
        node.y = round(ys[i], 1)
