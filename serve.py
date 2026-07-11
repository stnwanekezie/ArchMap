"""Local server for the architecture map: static files + a server-side AI proxy.

Why this exists: the *graph* is a standalone HTML file that opens straight from
``file://`` — no server needed. But the "Describe with AI" button must not put
an API key in the browser, so the call is made **server-side** here instead. The
page POSTs the function source to ``/describe`` (same origin, no CORS, no key in
the browser); this server reads the provider key from the environment /
``backend/.env`` and calls Claude / Ollama / an OpenAI-compatible endpoint.

Bind is localhost-only. Run from ``backend/``::

    python -m tools.archmap.serve                 # build + serve on :8777
    python -m tools.archmap.serve --port 9000
    python -m tools.archmap.serve --no-build      # serve the existing HTML as-is
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import threading
import urllib.error
import urllib.request
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from .extract import build_graph
from .layout import apply_layout
from .live_session import ClaudeSession, SessionError
from .render import render_html

_HTML_NAME = "archmap.html"
_MAX_BODY = 400_000  # generous cap for a single function's source
_TIMEOUT = 60
_CLI_TIMEOUT = 180  # Claude Code spins up an agent per call; allow headroom


# --- environment ----------------------------------------------------------


def _load_dotenv() -> None:
    """Load ``backend/.env`` without overriding already-set env vars.

    Mirrors the GTFS update job's convention (existing environment wins). Only
    simple ``KEY=VALUE`` lines are honoured; anything else is ignored.
    """
    path = os.path.join(os.getcwd(), ".env")
    if not os.path.isfile(path):
        return
    for raw in open(path, encoding="utf-8"):
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


# --- provider calls (all stdlib urllib, no extra deps) --------------------


def _post_json(url: str, headers: dict, payload: dict) -> dict:
    """Post Json.

    :param url: url.
    :param headers: headers.
    :param payload: payload.
    """

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _flatten_for_cli(messages: list[dict]) -> str:
    """Render a chat history as a plain transcript for the stateless CLI.

    ``claude -p`` is invoked fresh each turn, so the whole conversation is
    replayed as text with turn markers and a trailing ``[Assistant]:`` cue for
    the next reply. The first user turn already carries the function source.
    """
    lines = []
    for m in messages:
        who = "User" if m.get("role") == "user" else "Assistant"
        lines.append(f"[{who}]: {m.get('content', '')}")
    lines.append("[Assistant]:")
    return "\n\n".join(lines)


def _chat_claude_code(model: str, messages: list[dict]) -> str:
    """Continue the conversation via the local ``claude -p`` CLI (subscription).

    Uses the machine's Claude Code login rather than a metered API key — no
    ``ANTHROPIC_API_KEY``, no Console credits. The transcript is piped on stdin
    to sidestep command-line length limits.
    """
    exe = shutil.which("claude")
    if not exe:
        raise RuntimeError("claude CLI not found on PATH (install Claude Code)")
    cmd = [exe, "-p", "--output-format", "text"]
    if model:
        cmd += ["--model", model]
    # Force subscription auth: if ANTHROPIC_API_KEY is present the CLI would use
    # metered API-key auth (and fail on an empty credit balance) instead of the
    # Claude Code login. Hide it from *this* subprocess only — the "claude" API
    # provider still needs it in the server env.
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    try:
        r = subprocess.run(
            cmd,
            input=_flatten_for_cli(messages),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_CLI_TIMEOUT,
            cwd=os.getcwd(),
            env=env,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"claude CLI timed out after {_CLI_TIMEOUT}s")
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "claude CLI failed").strip()[:500])
    return r.stdout.strip()


def _chat(provider: str, model: str, base: str, messages: list[dict]) -> str:
    """Send the message history to the configured provider, return the reply.

    Keys and (for Claude) the base URL come from the environment; local
    providers may pass a ``base`` override from the UI since it carries no secret.
    """
    if provider == "claude-code":
        return _chat_claude_code(model, messages)

    if provider == "claude":
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set in the environment or backend/.env"
            )
        base = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip(
            "/"
        )
        out = _post_json(
            base + "/v1/messages",
            {
                "content-type": "application/json",
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
            },
            {"model": model, "max_tokens": 700, "messages": messages},
        )
        return "".join(
            b.get("text", "") for b in out.get("content", []) if b.get("type") == "text"
        )

    if provider == "ollama":
        base = (
            base or os.environ.get("OLLAMA_HOST") or "http://localhost:11434"
        ).rstrip("/")
        out = _post_json(
            base + "/api/chat",
            {"content-type": "application/json"},
            {"model": model, "messages": messages, "stream": False},
        )
        return out.get("message", {}).get("content", "")

    # openai-compatible (LM Studio / llama.cpp / vLLM)
    base = (
        base or os.environ.get("OPENAI_BASE_URL") or "http://localhost:1234"
    ).rstrip("/")
    headers = {"content-type": "application/json"}
    key = os.environ.get("OPENAI_COMPAT_KEY") or os.environ.get("OPENAI_API_KEY")
    if key:
        headers["Authorization"] = "Bearer " + key
    out = _post_json(
        base + "/v1/chat/completions",
        headers,
        {"model": model, "temperature": 0.2, "stream": False, "messages": messages},
    )
    return out["choices"][0]["message"]["content"]


# --- persistent Claude Code session (one per server) ----------------------

_SESSION_LOCK = threading.Lock()
_SESSION: dict = {"obj": None}


def _get_session(model: str) -> ClaudeSession:
    """Return the shared live session, recreating it if the model changed."""
    with _SESSION_LOCK:
        obj = _SESSION["obj"]
        if obj is None or obj.model != model:
            if obj is not None:
                obj.reset()
            obj = ClaudeSession(model=model, cwd=os.getcwd())
            _SESSION["obj"] = obj
        return obj


def _close_session() -> None:
    """Reset the shared live Claude session and release its process."""

    with _SESSION_LOCK:
        if _SESSION["obj"] is not None:
            _SESSION["obj"].reset()
            _SESSION["obj"] = None


# --- HTTP handler ---------------------------------------------------------


class _Handler(SimpleHTTPRequestHandler):
    """Serves the archmap directory and proxies ``POST /describe``."""

    def _json(self, code: int, obj: dict) -> None:
        """Send a JSON HTTP response with the given status code and payload."""

        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict | None:
        """Read body."""

        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > _MAX_BODY:
            self._json(413, {"error": "empty or oversized request"})
            return None
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._json(400, {"error": "invalid JSON"})
            return None

    def do_POST(self) -> None:  # noqa: N802 (stdlib naming)
        """Do Post."""

        path = self.path.split("?")[0]
        if path == "/chat":
            self._handle_chat()
        elif path == "/session":
            self._handle_session()
        else:
            self._json(404, {"error": "not found"})

    def _handle_chat(self) -> None:
        """Stateless providers: relay the full message history each call."""
        req = self._read_body()
        if req is None:
            return
        provider = (req.get("provider") or "claude-code").lower()
        messages = req.get("messages") or []
        if (
            not isinstance(messages, list)
            or not messages
            or not all(
                isinstance(m, dict) and m.get("role") and m.get("content")
                for m in messages
            )
        ):
            self._json(
                400, {"error": "messages must be a non-empty list of {role, content}"}
            )
            return
        try:
            reply = _chat(
                provider, req.get("model") or "", req.get("base") or "", messages
            )
            self._json(200, {"reply": reply.strip()})
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:500]
            self._json(502, {"error": f"provider HTTP {e.code}: {detail}"})
        except urllib.error.URLError as e:
            self._json(502, {"error": f"cannot reach provider: {e.reason}"})
        except Exception as e:  # surface the message to the UI, don't 500 silently
            self._json(502, {"error": str(e)})

    def _handle_session(self) -> None:
        """Live Claude Code session: reuse context, allow slash commands."""
        req = self._read_body()
        if req is None:
            return
        action = (req.get("action") or "send").lower()
        if action == "reset":
            _close_session()
            self._json(200, {"ok": True})
            return
        if action != "send":
            self._json(400, {"error": f"unknown action '{action}'"})
            return
        text = (req.get("text") or "").strip()
        if not text:
            self._json(400, {"error": "no text provided"})
            return
        try:
            result = _get_session(req.get("model") or "").send(text)
            self._json(200, result)
        except SessionError as e:
            self._json(502, {"error": str(e)})
        except Exception as e:
            self._json(502, {"error": str(e)})

    def log_message(self, fmt: str, *args) -> None:
        # Quiet the default per-request stderr spam; keep it to one line.
        """Log Message.

        :param fmt: fmt.
        :param args: variadic positional arguments.
        """

        pass


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Serve an architecture map with an AI proxy."
    )
    ap.add_argument("--port", type=int, default=8777)
    ap.add_argument(
        "--no-build", action="store_true", help="serve existing HTML without rebuilding"
    )
    ap.add_argument("--no-open", action="store_true", help="don't open a browser")
    ap.add_argument("--iterations", type=int, default=320)
    ap.add_argument(
        "--scan",
        nargs="+",
        default=["src"],
        help="source roots to scan (default: src)",
    )
    args = ap.parse_args()

    _load_dotenv()

    html_path = os.path.join(os.getcwd(), _HTML_NAME)
    if not args.no_build or not os.path.exists(html_path):
        print("[archmap] building graph from source...")
        graph = build_graph(args.scan, os.getcwd())
        apply_layout(graph, iterations=args.iterations)
        render_html(graph, html_path)
        print(f"[archmap] built {len(graph.nodes)} nodes")

    handler = partial(_Handler, directory=os.path.dirname(os.path.abspath(html_path)))
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    url = f"http://localhost:{args.port}/{_HTML_NAME}"
    print(f"[archmap] serving {url}  (Ctrl-C to stop)")
    print(
        "[archmap] AI 'Describe' calls are proxied server-side; keys read from "
        "env / backend/.env"
    )
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[archmap] stopped")
        server.shutdown()
    finally:
        _close_session()  # terminate the live Claude Code child, if any


if __name__ == "__main__":
    main()
