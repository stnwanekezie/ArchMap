"""Backend architecture map: AST -> function-level graph -> offline HTML viewer.

This package is a developer tool, not part of the running app. It walks the
backend source with the ``ast`` module, builds a node-per-function graph
(modules, classes, functions, methods) with ``contains`` / ``imports`` /
``calls`` edges, bakes a force-directed layout, and emits a single
self-contained HTML file with VS Code deep-links and optional local-LLM
descriptions.

Run it with ``python -m tools.archmap`` from ``backend/``.
"""
