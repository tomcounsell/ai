#!/usr/bin/env python3
"""Fail-silent Claude Code hook forwarder for the granite hook edge channel.

Plan #1688, Task 1. This is the command a per-session ``settings.json``
registers for the ``Stop`` / ``SubagentStop`` / ``Notification`` /
``PreToolUse`` / ``PermissionRequest`` / ``PreCompact`` / ``SessionStart``
hooks. Claude Code runs it as a subprocess, piping the hook payload JSON on
stdin. The forwarder reads that payload and appends exactly one NDJSON
envelope to the per-session edge file named by the ``GRANITE_HOOK_EDGE_FILE``
env var.

Design contract (Practice 3 — the edge writer):
- **Self-contained.** No intra-package imports, stdlib only, so it can be
  invoked as ``python3 /abs/path/hook_forwarder.py`` regardless of the
  spawned ``claude`` process's cwd (plan ## Update System).
- **Fail-silent.** Any error (unreadable stdin, missing env var, unwritable
  edge file, unparseable payload) results in a clean ``exit 0`` and never
  raises — a hook subprocess that raised would splash a stderr blob into the
  TUI or block the turn (the exact failure this design avoids).
- **Atomic single-line append.** Uses ``os.open(..., O_WRONLY|O_APPEND|O_CREAT)``
  + a single ``os.write`` so the parent turn's ``Stop`` envelope and a
  subagent's ``SubagentStop`` envelope (same destination file — subagents
  inherit the parent's hook settings) never tear into each other (Race 3).

The destination edge file is resolved from, in order: the
``GRANITE_HOOK_EDGE_FILE`` env var (the standalone / test path), then the
first CLI argument (the per-session-settings path — each PTY's generated
``settings.json`` embeds its own edge path as ``python3 <forwarder> <edge>``,
so two PTYs sharing one process env still write to separate per-session files).

Envelope shape (one JSON object per line)::

    {"ts": <float>, "event": "<hook_event_name>", "payload": {<full hook JSON>}}

``event`` is lifted out of ``payload["hook_event_name"]`` so the consumer can
classify without re-parsing the whole payload; ``payload`` is the verbatim
hook JSON (carrying ``session_id``, ``transcript_path``, ``cwd``, and — for
subagents — ``agent_id`` / ``agent_type``).
"""

from __future__ import annotations

import json
import os
import sys
import time

EDGE_FILE_ENV = "GRANITE_HOOK_EDGE_FILE"

# Cap the raw payload we retain when it does not parse as JSON, so a runaway
# payload cannot bloat the edge file. The consumer treats an unparseable line
# as garbage and skips it anyway; this just bounds the on-disk footprint.
_MAX_UNPARSEABLE_CHARS = 2000


def _build_envelope(raw: str) -> dict:
    """Wrap the raw stdin payload in the NDJSON envelope. Never raises."""
    ts = time.time()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        # Preserve a bounded slice for post-hoc debugging; the consumer skips
        # envelopes with no recognizable event.
        return {"ts": ts, "event": None, "payload": {"_unparseable": raw[:_MAX_UNPARSEABLE_CHARS]}}
    event = payload.get("hook_event_name") if isinstance(payload, dict) else None
    return {"ts": ts, "event": event, "payload": payload}


def _append_line(edge_path: str, line: str) -> None:
    """Atomically append a single newline-terminated line. Never raises."""
    data = line.encode("utf-8", errors="replace")
    if not data.endswith(b"\n"):
        data += b"\n"
    fd = os.open(edge_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)


def main() -> int:
    """Read the hook payload from stdin, append one envelope, exit 0 always."""
    try:
        raw = sys.stdin.read()
    except Exception:
        return 0
    # Env var wins (standalone / test path); otherwise the per-session settings
    # file passes the edge path as the first CLI arg.
    edge_path = os.environ.get(EDGE_FILE_ENV)
    if not edge_path and len(sys.argv) > 1:
        edge_path = sys.argv[1]
    if not edge_path:
        return 0
    try:
        envelope = _build_envelope(raw)
        _append_line(edge_path, json.dumps(envelope))
    except Exception:
        # Absolutely never propagate — a raising hook blocks/garbles the turn.
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
