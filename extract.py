"""AST extraction: backend source tree -> function-level graph.

Emits a plain ``GraphData`` (nodes + edges) with no rendering concerns. Nodes
are modules, classes, functions and methods; edges are ``contains`` (structural
nesting), ``imports`` (module -> module) and ``calls`` (best-effort static
resolution of one project symbol calling another).

Call resolution is deliberately high-precision: a call is only recorded when it
resolves to exactly one project definition (via an imported symbol, a sibling
method on ``self``, or a project-unique name). Dynamic dispatch and ambiguous
same-named calls are skipped rather than guessed — false edges are worse than
missing ones for reading architecture.
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field, asdict
from typing import Optional

# --- layer classification -------------------------------------------------

# Optional override list — entries here take priority over auto-detection.
# Ordered longest-prefix-first so more-specific paths win.
# Leave empty to rely entirely on auto-detection (first directory under each
# scan root).  Example for a project whose services are split by concern:
#
#   _LAYER_RULES = [
#       ("src/api",      "api"),
#       ("src/services", "services"),
#       ("src/models",   "models"),
#       ("src/utils",    "utils"),
#   ]
_LAYER_RULES: list[tuple[str, str]] = []


# When a first-level directory has at least this many immediate subdirectories
# that contain Python files, auto-detection goes one level deeper so broad
# container directories (e.g. services/) get meaningful sub-layers instead of
# everything landing in one bucket.
_SPLIT_THRESHOLD = 3


def _layer_for(
    rel_path: str, scan_dirs: list[str], split_dirs: set[tuple[str, str]]
) -> str:
    """Return the architectural layer for a repo-relative file path.

    Priority order:
    1. ``_LAYER_RULES`` — explicit user overrides, longest-prefix-first.
    2. Second path component when the first-level dir has >= ``_SPLIT_THRESHOLD``
       subdirectories with Python files (so ``services/adapters/`` → ``adapters``
       rather than everything collapsing into ``services``).
    3. First path component under the scan root.
    """
    p = rel_path.replace("\\", "/")
    for prefix, layer in _LAYER_RULES:
        if p.startswith(prefix.replace("\\", "/")):
            return layer
    for root in sorted(scan_dirs, key=len, reverse=True):
        r = root.replace("\\", "/").rstrip("/")
        if p.startswith(r + "/"):
            rest = p[len(r) + 1 :]
            parts = rest.split("/")
            first = parts[0]
            # Go one level deeper only when the file is inside a sub-directory
            # (len > 2 means parts[1] is itself a directory, not the .py file).
            if len(parts) > 2 and (r, first) in split_dirs:
                return parts[1]
            return first if len(parts) > 1 else r.rsplit("/", 1)[-1]
    return p.split("/")[0] if "/" in p else "other"


def _compute_split_dirs(
    all_files: list[tuple[str, str]], scan_dirs: list[str]
) -> set[tuple[str, str]]:
    """Return (scan_root, first_component) pairs that should be split deeper.

    A first-level directory qualifies when it has at least ``_SPLIT_THRESHOLD``
    distinct immediate subdirectories that contain Python files.
    """
    from collections import defaultdict

    subdir_sets: dict[tuple[str, str], set[str]] = defaultdict(set)
    for rel, _ in all_files:
        p = rel.replace("\\", "/")
        for root in sorted(scan_dirs, key=len, reverse=True):
            r = root.replace("\\", "/").rstrip("/")
            if p.startswith(r + "/"):
                rest = p[len(r) + 1 :]
                parts = rest.split("/")
                # parts[0]=first-level dir, parts[1]=subdir (not the .py file)
                if len(parts) > 2:
                    subdir_sets[(r, parts[0])].add(parts[1])
                break
    return {
        key for key, subdirs in subdir_sets.items() if len(subdirs) >= _SPLIT_THRESHOLD
    }


# --- data model -----------------------------------------------------------


@dataclass
class Node:
    """One graph node: a module, class, function or method."""

    id: str
    kind: str  # "module" | "class" | "function" | "method"
    name: str  # short display name
    qualname: str  # module-qualified, e.g. app/x.py::Cls.meth
    layer: str
    file: str  # repo-relative, forward slashes
    abs_file: str  # absolute path (for vscode:// deep-links)
    line: int  # 1-based definition line
    end_line: int
    signature: str = ""
    docstring: str = ""  # first line only, trimmed
    source: str = ""  # full source snippet of the def
    x: float = 0.0
    y: float = 0.0


@dataclass
class Edge:
    """A directed relationship between two node ids."""

    source: str
    target: str
    relation: str  # "contains" | "imports" | "calls"


@dataclass
class GraphData:
    """GraphData."""

    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "nodes": [asdict(n) for n in self.nodes],
            "edges": [asdict(e) for e in self.edges],
        }


# --- module id helpers ----------------------------------------------------


def _module_id(rel_path: str) -> str:
    """Module Id.

    :param rel_path: rel path.
    """

    return f"mod:{rel_path}"


def _symbol_id(rel_path: str, qual: str) -> str:
    """Symbol Id.

    :param rel_path: rel path.
    :param qual: qual.
    """

    return f"sym:{rel_path}::{qual}"


def _first_doc_line(node: ast.AST) -> str:
    """First Doc Line.

    :param node: node.
    """

    doc = ast.get_docstring(node, clean=True) or ""
    doc = doc.strip()
    if not doc:
        return ""
    return doc.splitlines()[0].strip()


def _render_signature(fn: ast.AST, name: str) -> str:
    """Best-effort ``def name(args) -> ret`` string for display."""
    try:
        args = ast.unparse(fn.args)  # type: ignore[attr-defined]
    except Exception:
        args = "..."
    ret = ""
    returns = getattr(fn, "returns", None)
    if returns is not None:
        try:
            ret = " -> " + ast.unparse(returns)
        except Exception:
            ret = ""
    prefix = "async def " if isinstance(fn, ast.AsyncFunctionDef) else "def "
    return f"{prefix}{name}({args}){ret}"


# --- per-file extraction --------------------------------------------------


class _ModuleVisitor:
    """Collects nodes, contains/imports edges, and raw call sites for one file."""

    def __init__(
        self,
        rel_path: str,
        abs_path: str,
        src_lines: list[str],
        scan_dirs: list[str],
        split_dirs: set[tuple[str, str]],
    ):
        self.rel = rel_path
        self.abs = abs_path
        self.lines = src_lines
        self.layer = _layer_for(rel_path, scan_dirs, split_dirs)
        self.mod_id = _module_id(rel_path)
        self.nodes: list[Node] = []
        self.edges: list[Edge] = []
        # import alias -> (target_module_rel_path_or_dotted, symbol_or_None)
        self.imports: dict[str, tuple[str, Optional[str]]] = {}
        # call sites recorded for later cross-module resolution:
        # (caller_symbol_id, enclosing_class_or_None, call_ast_node)
        self.calls: list[tuple[str, Optional[str], ast.Call]] = []

    def _snippet(self, node: ast.AST) -> str:
        """Perform snippet.

        :param node: node.
        """

        start = getattr(node, "lineno", 1) - 1
        end = getattr(node, "end_lineno", start + 1)
        return "\n".join(self.lines[start:end])

    def run(self, tree: ast.Module) -> None:
        mod_name = self.rel.rsplit("/", 1)[-1]
        self.nodes.append(
            Node(
                id=self.mod_id,
                kind="module",
                name=mod_name,
                qualname=self.rel,
                layer=self.layer,
                file=self.rel,
                abs_file=self.abs,
                line=1,
                end_line=len(self.lines),
                docstring=_first_doc_line(tree),
            )
        )
        self._collect_imports(tree)
        for child in tree.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._add_function(child, cls=None)
            elif isinstance(child, ast.ClassDef):
                self._add_class(child)

    def _collect_imports(self, tree: ast.Module) -> None:
        """Collect import statements from the module AST into the parser state."""

        for node in tree.body:
            if isinstance(node, ast.Import):
                for a in node.names:
                    self.imports[a.asname or a.name.split(".")[0]] = (a.name, None)
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                for a in node.names:
                    self.imports[a.asname or a.name] = (mod, a.name)

    def _add_class(self, cls: ast.ClassDef) -> None:
        """Create a class node entry for the extracted architecture graph."""

        cid = _symbol_id(self.rel, cls.name)
        self.nodes.append(
            Node(
                id=cid,
                kind="class",
                name=cls.name,
                qualname=f"{self.rel}::{cls.name}",
                layer=self.layer,
                file=self.rel,
                abs_file=self.abs,
                line=cls.lineno,
                end_line=getattr(cls, "end_lineno", cls.lineno),
                signature=f"class {cls.name}",
                docstring=_first_doc_line(cls),
                source=self._snippet(cls),
            )
        )
        self.edges.append(Edge(self.mod_id, cid, "contains"))
        for item in cls.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._add_function(item, cls=cls.name)

    def _add_function(self, fn: ast.AST, cls: Optional[str]) -> None:
        """Add Function.

        :param fn: fn.
        """

        name = fn.name  # type: ignore[attr-defined]
        qual = f"{cls}.{name}" if cls else name
        sid = _symbol_id(self.rel, qual)
        parent = _symbol_id(self.rel, cls) if cls else self.mod_id
        self.nodes.append(
            Node(
                id=sid,
                kind="method" if cls else "function",
                name=name,
                qualname=f"{self.rel}::{qual}",
                layer=self.layer,
                file=self.rel,
                abs_file=self.abs,
                line=fn.lineno,  # type: ignore[attr-defined]
                end_line=getattr(fn, "end_lineno", fn.lineno),  # type: ignore[attr-defined]
                signature=_render_signature(fn, name),
                docstring=_first_doc_line(fn),
                source=self._snippet(fn),
            )
        )
        self.edges.append(Edge(parent, sid, "contains"))
        for sub in ast.walk(fn):
            if isinstance(sub, ast.Call):
                self.calls.append((sid, cls, sub))
            # Nested defs are also walked by the top loop only at class/module
            # level; methods-in-functions are rare here and intentionally left
            # as call sites of their enclosing symbol.


# --- project-wide assembly ------------------------------------------------


def _iter_py_files(root: str, repo_root: str) -> list[tuple[str, str]]:
    """Iter Py Files.

    :param root: root.
    :param repo_root: repo root.
    """

    out = []
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in {"__pycache__", ".venv", "node_modules"}]
        for f in fns:
            if f.endswith(".py"):
                ap = os.path.join(dp, f)
                rel = os.path.relpath(ap, repo_root).replace("\\", "/")
                out.append((rel, os.path.abspath(ap).replace("\\", "/")))
    return out


def build_graph(scan_dirs: list[str], repo_root: str) -> GraphData:
    """Parse every ``.py`` under ``scan_dirs`` and resolve the call graph."""
    # Collect all files first so we can compute split_dirs in one pass before
    # any visitor assigns layers.
    all_files: list[tuple[str, str]] = []
    for root in scan_dirs:
        all_files.extend(_iter_py_files(root, repo_root))
    split_dirs = _compute_split_dirs(all_files, scan_dirs)

    graph = GraphData()
    visitors: list[_ModuleVisitor] = []

    for rel, ap in all_files:
        try:
            text = open(ap, encoding="utf-8").read()
            tree = ast.parse(text)
        except (SyntaxError, UnicodeDecodeError):
            continue
        v = _ModuleVisitor(rel, ap, text.splitlines(), scan_dirs, split_dirs)
        v.run(tree)
        visitors.append(v)
        graph.nodes.extend(v.nodes)
        graph.edges.extend(v.edges)

    _resolve_calls(graph, visitors)
    return graph


def _resolve_calls(graph: GraphData, visitors: list[_ModuleVisitor]) -> None:
    """Second pass: turn recorded call sites into ``calls`` edges.

    Resolution order per call: (1) ``self.method`` -> sibling method in the same
    class; (2) a name imported via ``from mod import name`` -> that symbol in the
    target module; (3) a project-unique simple name. Anything ambiguous or
    external is skipped.
    """
    # Indexes -------------------------------------------------------------
    by_module: dict[str, dict[str, str]] = {}  # rel -> {qual: node_id}
    name_owners: dict[str, list[str]] = {}  # simple name -> [node_id]
    module_by_dotted: dict[str, str] = {}  # dotted import path -> rel file
    for n in graph.nodes:
        if n.kind in ("function", "method", "class"):
            qual = n.qualname.split("::", 1)[1]
            by_module.setdefault(n.file, {})[qual] = n.id
            name_owners.setdefault(n.name, []).append(n.id)
        if n.kind == "module":
            dotted = n.file[:-3].replace("/", ".")  # app/x/y.py -> app.x.y
            module_by_dotted[dotted] = n.file
            if dotted.endswith(".__init__"):
                module_by_dotted[dotted[: -len(".__init__")]] = n.file

    seen: set[tuple[str, str]] = set()

    def _emit(src: str, dst: str) -> None:
        """Perform emit.

        :param src: src.
        :param dst: dst.
        """

        if src == dst:
            return
        key = (src, dst)
        if key not in seen:
            seen.add(key)
            graph.edges.append(Edge(src, dst, "calls"))

    for v in visitors:
        local = by_module.get(v.rel, {})
        for caller_id, cls, call in v.calls:
            func = call.func
            target_id: Optional[str] = None

            if isinstance(func, ast.Attribute):
                attr = func.attr
                val = func.value
                if isinstance(val, ast.Name) and val.id == "self" and cls:
                    target_id = local.get(f"{cls}.{attr}")
                if target_id is None:
                    # module.func  where `module` was imported as a whole
                    if isinstance(val, ast.Name) and val.id in v.imports:
                        dotted, sym = v.imports[val.id]
                        if sym is None:
                            relf = module_by_dotted.get(dotted)
                            if relf:
                                target_id = by_module.get(relf, {}).get(attr)
                # No project-unique fallback for `obj.attr()` — attribute names
                # collide with builtins (dict.items, str.format, file.read) and
                # would fabricate edges. self.method and imported-module.func
                # above are the only trustworthy attribute resolutions.
            elif isinstance(func, ast.Name):
                nm = func.id
                if nm in v.imports:
                    dotted, sym = v.imports[nm]
                    relf = module_by_dotted.get(dotted)
                    if relf and sym:
                        target_id = by_module.get(relf, {}).get(sym)
                if target_id is None:
                    target_id = local.get(nm)
                if target_id is None:
                    owners = name_owners.get(nm, [])
                    if len(owners) == 1:
                        target_id = owners[0]

            if target_id:
                _emit(caller_id, target_id)
