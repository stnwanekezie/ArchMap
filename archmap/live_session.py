"""Persistent Claude Code session for the archmap AI panel.

Keeps ONE long-lived ``claude -p --input-format stream-json`` process so the
conversation's context is reused across turns without resending the transcript,
and so interactive control commands (``/compact``, ``/clear``, ``/context`` …)
work — they're sent as ordinary user turns and the CLI interprets them.

Thread-safety: turns are serialised behind ``_turn_lock`` (one in flight at a
time); a daemon reader thread parses the stream-json stdout and hands each turn's
final text back to the waiting caller via a per-turn ``Event``. Only the Claude
Code provider uses this — the HTTP-API providers stay stateless.

The model is fixed for the life of a session (changing it needs a reset), and
``ANTHROPIC_API_KEY`` is stripped from the child so it authenticates with the
Claude Code login (subscription) rather than metered API credits.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading


class SessionError(RuntimeError):
    """Raised when the live session can't start, write, or complete a turn."""


class ClaudeSession:
    """One persistent ``claude`` process driven over stream-json stdin/stdout."""

    def __init__(self, model: str = "", cwd: str | None = None):
        self.model = model
        self._cwd = cwd or os.getcwd()
        self._proc: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None
        self._turn_lock = threading.Lock()  # serialise turns
        self._cur: dict | None = None  # state for the in-flight turn
        self.session_id: str | None = None

    # -- lifecycle ---------------------------------------------------------

    def _ensure(self) -> None:
        """Ensure the persistent Claude CLI session is started and ready."""

        if self._proc and self._proc.poll() is None:
            return
        exe = shutil.which("claude")
        if not exe:
            raise SessionError("claude CLI not found on PATH (install Claude Code)")
        cmd = [
            exe,
            "-p",
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--verbose",
        ]
        if self.model:
            cmd += ["--model", self.model]
        env = os.environ.copy()
        env.pop("ANTHROPIC_API_KEY", None)  # force subscription auth
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            cwd=self._cwd,
            env=env,
        )
        self.session_id = None
        self._reader = threading.Thread(
            target=self._read_loop, args=(self._proc,), daemon=True
        )
        self._reader.start()

    def alive(self) -> bool:
        """Return True when the persistent Claude session process is running."""

        return bool(self._proc and self._proc.poll() is None)

    def reset(self) -> None:
        """Terminate the process; the next ``send`` starts a fresh session."""
        proc, self._proc, self._reader, self._cur = self._proc, None, None, None
        self.session_id = None
        if proc and proc.poll() is None:
            for step in (proc.stdin and proc.stdin.close, proc.terminate):
                try:
                    step()
                except Exception:
                    pass
            try:
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    stop = reset

    # -- turn handling -----------------------------------------------------

    def _read_loop(self, proc: subprocess.Popen) -> None:
        """Parse stream-json events and complete the current turn on ``result``."""
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            t, st = ev.get("type"), ev.get("subtype")
            if t == "system" and st == "init":
                self.session_id = ev.get("session_id") or self.session_id
            cur = self._cur
            if cur is None:
                continue
            if t == "assistant":
                try:
                    for b in ev["message"]["content"]:
                        if b.get("type") == "text":
                            cur["text"].append(b["text"])
                except Exception:
                    pass
            elif t == "system" and st == "status":
                cur["status"].append(ev)
            elif t == "result":
                cur["result"] = ev.get("result")
                cur["done"].set()
        # stdout closed → process gone; unblock any waiter.
        cur = self._cur
        if cur and not cur["done"].is_set():
            cur["error"] = "claude session ended unexpectedly"
            cur["done"].set()

    def send(self, text: str, timeout: float = 220.0) -> dict:
        """Send one user turn (or slash command); return the reply + any notice."""
        with self._turn_lock:
            self._ensure()
            cur = {
                "text": [],
                "status": [],
                "result": None,
                "error": None,
                "done": threading.Event(),
            }
            self._cur = cur
            payload = json.dumps(
                {"type": "user", "message": {"role": "user", "content": text}}
            )
            try:
                self._proc.stdin.write(payload + "\n")
                self._proc.stdin.flush()
            except (BrokenPipeError, OSError) as e:
                self._cur = None
                raise SessionError(f"failed to write to claude session: {e}")

            if not cur["done"].wait(timeout):
                self._cur = None
                self.reset()  # wedged — drop it so the next turn is clean
                raise SessionError(f"claude session timed out after {timeout:.0f}s")
            self._cur = None
            if cur["error"]:
                raise SessionError(cur["error"])

            reply = (cur["result"] or "".join(cur["text"]) or "").strip()
            return {
                "reply": reply,
                "notice": _notice(cur["status"]),
                "session_id": self.session_id,
            }


def _notice(statuses: list[dict]) -> str | None:
    """Summarise compaction/status events into one short line for the UI."""
    for s in statuses:
        res = s.get("compact_result")
        if res == "failed":
            return "compact failed: " + (s.get("compact_error") or "unknown")
        if res == "success":
            return "context compacted"
    if any(s.get("status") == "compacting" for s in statuses):
        return "context compacted"
    return None
