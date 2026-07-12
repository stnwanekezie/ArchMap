"""CLI: build an architecture map for a Python codebase.

Usage (from the project root)::

    python -m archmap --scan src                  # scan src/, write pages/archmap.html
    python -m archmap --scan src tests            # multiple scan roots
    python -m archmap --out map.html              # custom output path
    python -m archmap --iterations 500            # denser layout relaxation
"""

from __future__ import annotations

import argparse
import os
import time

from .extract import build_graph
from .layout import apply_layout
from .render import render_html


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Generate an architecture map for a Python codebase."
    )
    ap.add_argument(
        "--scan",
        nargs="+",
        default=["src"],
        help="source roots to scan, relative to cwd (default: src)",
    )
    ap.add_argument(
        "--out",
        default="../pages/archmap.html",
        help="output HTML path (default: pages/archmap.html)",
    )
    ap.add_argument(
        "--iterations", type=int, default=320, help="layout relaxation steps"
    )
    args = ap.parse_args()

    repo_backend = os.getcwd()  # expected to be run from backend/
    t0 = time.time()
    print(f"[archmap] scanning: {', '.join(args.scan)}")
    graph = build_graph(args.scan, repo_backend)
    n_nodes, n_edges = len(graph.nodes), len(graph.edges)
    print(
        f"[archmap] extracted {n_nodes} nodes, {n_edges} edges "
        f"({time.time() - t0:.1f}s)"
    )

    print(f"[archmap] laying out ({args.iterations} iterations)...")
    apply_layout(graph, iterations=args.iterations)

    out_path = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    render_html(graph, out_path)
    print(f"[archmap] wrote {out_path} ({time.time() - t0:.1f}s total)")
    print(f"[archmap] open it in a browser: file:///{out_path.replace(os.sep, '/')}")


if __name__ == "__main__":
    main()
