"""Transport-agnostic hook edge channel for the granite PTY shuttle.

Plan #1688, Task 1. Claude Code's own hook event stream is the deterministic
source of two decisions the granite container used to *guess* by PTY idle
heuristic: the **turn-end** edge (``Stop``) and the **needs-human** edge
(``Notification`` / ``PermissionRequest`` / ``PreToolUse(AskUserQuestion)``).
This module owns the read side of that stream through **one seam** that works
identically whether the container drives two PTYs, one PTY, or a headless
role — it never touches the PTY.

Three pieces live here:

1. :func:`generate_hook_settings` — writes a per-session ``settings.json``
   registering every target hook to the fail-silent
   :mod:`agent.granite_container.hook_forwarder`, and returns the
   ``(settings_path, edge_file_path)`` pair. ``claude --settings <path>`` is
   the injection seam (per-session, since each PTY has its own ``session_id``).

2. :class:`HookCursor` — the durable ``(event_cursor, byte_offset,
   cursor_fingerprint)`` triple (Practice 4). It makes a worker restart replay
   only unseen edges and never double-deliver, and detects truncation /
   replacement before seeking a stale offset.

3. :class:`HookEdgeConsumer` — tails the append-only NDJSON edge file from the
   cursor, classifies each envelope into a typed :class:`HookEdge`
   (``turn_end`` / ``subagent_end`` / ``needs_human`` / ``compaction``), and
   advances the cursor. Level-triggered (reads from the cursor, not
   edge-triggered), so a ``Stop`` written before the wait arms is still read
   when the wait begins — no missed edge (Race 1). Fail-silent on corrupt /
   partial lines (mirrors ``last_assistant_text``).

Edge transport is an append-only file + durable cursor, NOT a Redis list:
this honors the repo's Popoto-only Redis rule, needs no new Popoto model, and
is restart-safe (spike-1). The forwarder writes a file path; no Redis client
ever runs inside a hook subprocess.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import pathlib
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# The env var the forwarder reads for its destination edge file. Set in the
# per-session env overlay (``_extra_env``) alongside ``AGENT_SESSION_ID``.
EDGE_FILE_ENV = "GRANITE_HOOK_EDGE_FILE"

# Absolute path to the fail-silent forwarder script. Resolved once at import
# so the generated settings can register ``python3 <abspath>`` and the spawned
# ``claude`` runs it regardless of its cwd (plan ## Update System).
_FORWARDER_PATH = str(pathlib.Path(__file__).resolve().parent / "hook_forwarder.py")

# The hooks the per-session settings file registers, keyed by Claude Code's
# ``hook_event_name``. Every one routes to the same forwarder; the consumer
# classifies by event name downstream.
#   - Stop / SubagentStop: turn-end vs subagent-end (Practice 5 — native
#     disambiguation, no filtering heuristic).
#   - Notification / PermissionRequest: the needs-human edge.
#   - PreToolUse (matcher AskUserQuestion): the agent asking the human a
#     question mid-turn — also needs-human.
#   - PreCompact / SessionStart: compaction status, explicitly NOT turn-end
#     (Practice 8).
_TURN_END_EVENT = "Stop"
_SUBAGENT_EVENT = "SubagentStop"
_NEEDS_HUMAN_EVENTS = frozenset({"Notification", "PermissionRequest"})
_COMPACTION_EVENTS = frozenset({"PreCompact", "SessionStart"})
_ASK_USER_MATCHER = "AskUserQuestion"

# Edge-kind constants (the consumer's public vocabulary).
TURN_END = "turn_end"
SUBAGENT_END = "subagent_end"
NEEDS_HUMAN = "needs_human"
COMPACTION = "compaction"

# How many leading bytes of the edge file the fingerprint hashes. A truncation
# or replacement (log-rotation, fresh-session reuse of a path) changes these
# bytes; the consumer detects the mismatch and resets its offset to 0 rather
# than seeking into a stale/rewritten file (Race — Risk 3 / Practice 4).
_FINGERPRINT_BYTES = 256


def _fingerprint(path: pathlib.Path, nbytes: int) -> str:
    """Hash the first ``nbytes`` of ``path`` (or "" if nbytes<=0 / unreadable).

    ``nbytes`` is deliberately a *fixed prefix within the already-consumed
    region* (see :meth:`HookEdgeConsumer.poll`), never the whole file: an
    append-only edge file grows at the tail, so a prefix inside the consumed
    region is immutable across normal appends. Only a truncation or a
    head-rewriting replacement changes it — which is exactly what the
    fingerprint must detect (Risk 3 / Practice 4).
    """
    if nbytes <= 0:
        return ""
    try:
        with open(path, "rb") as f:
            head = f.read(nbytes)
    except OSError:
        return ""
    if not head:
        return ""
    return hashlib.sha256(head).hexdigest()


def generate_hook_settings(
    settings_dir: str | os.PathLike[str],
    edge_file: str | os.PathLike[str],
    *,
    forwarder_path: str | None = None,
    filename: str = "granite_hook_settings.json",
) -> tuple[str, str]:
    """Write the per-session ``--settings`` file and return ``(settings, edge)``.

    ``settings_dir`` is where the settings JSON is written; ``edge_file`` is the
    NDJSON edge file the forwarder appends to (its path is what
    :data:`EDGE_FILE_ENV` must carry in the child env). Every target hook is
    registered to ``python3 <forwarder>``.

    The edge file's parent is created and the file is touched empty so its path
    is *reserved before the first PTY write* — a level-triggered consumer can
    then always open it (Race 1: the edge path exists before any Stop can fire).
    """
    settings_dir = pathlib.Path(settings_dir)
    settings_dir.mkdir(parents=True, exist_ok=True)
    edge_path = pathlib.Path(edge_file)
    edge_path.parent.mkdir(parents=True, exist_ok=True)
    # Reserve the edge path (Race 1). Touch-if-absent — never truncate an
    # existing file (a resumed session may already have edges).
    if not edge_path.exists():
        edge_path.touch()

    forwarder = forwarder_path or _FORWARDER_PATH
    # Embed the edge path as the forwarder's first CLI arg so two PTYs sharing
    # one process env still write to separate per-session edge files. Quoted so
    # a path with spaces survives the shell Claude Code runs the hook under.
    command = f'python3 {forwarder} "{edge_path}"'
    # A single matcher-"" hook entry fires the forwarder for the event; the
    # PreToolUse entry narrows to the AskUserQuestion tool so ordinary tool
    # calls do not flood the edge file.
    all_events_entry = [{"matcher": "", "hooks": [{"type": "command", "command": command}]}]
    ask_user_entry = [
        {"matcher": _ASK_USER_MATCHER, "hooks": [{"type": "command", "command": command}]}
    ]
    hooks: dict[str, list] = {
        _TURN_END_EVENT: all_events_entry,
        _SUBAGENT_EVENT: all_events_entry,
        "Notification": all_events_entry,
        "PermissionRequest": all_events_entry,
        "PreToolUse": ask_user_entry,
        "PreCompact": all_events_entry,
        "SessionStart": all_events_entry,
    }
    settings_path = settings_dir / filename
    settings_path.write_text(json.dumps({"hooks": hooks}, indent=2))
    return (str(settings_path), str(edge_path))


@dataclass
class HookCursor:
    """Durable, idempotent read position into an append-only edge file.

    - ``event_cursor`` — count of complete envelopes consumed (monotonic within
      a file identity; the idempotency key).
    - ``byte_offset`` — byte position up to which complete lines were read.
    - ``fingerprint`` — hash of the file head at the last read; a mismatch means
      the file was truncated / replaced, so the offset is stale and must reset.

    Serializable to/from a plain dict so a caller (e.g. a worker restart, or
    #1721's checkpoint) can persist and restore it. It carries no framework
    dependency and is intentionally NOT a Popoto model — it is per-session file
    state (plan ## Update System: no migration needed).
    """

    event_cursor: int = 0
    byte_offset: int = 0
    fingerprint: str = ""

    def to_dict(self) -> dict:
        return {
            "event_cursor": self.event_cursor,
            "byte_offset": self.byte_offset,
            "fingerprint": self.fingerprint,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> HookCursor:
        if not data:
            return cls()
        return cls(
            event_cursor=int(data.get("event_cursor", 0)),
            byte_offset=int(data.get("byte_offset", 0)),
            fingerprint=str(data.get("fingerprint", "")),
        )


@dataclass
class HookEdge:
    """One classified hook edge read from the edge file.

    ``kind`` is one of :data:`TURN_END`, :data:`SUBAGENT_END`,
    :data:`NEEDS_HUMAN`, :data:`COMPACTION`. ``payload`` is the verbatim hook
    JSON; ``transcript_path`` / ``session_id`` are lifted for convenience.
    """

    kind: str
    event: str
    payload: dict = field(default_factory=dict)
    ts: float = 0.0

    @property
    def session_id(self) -> str | None:
        val = self.payload.get("session_id")
        return str(val) if val else None

    @property
    def transcript_path(self) -> str | None:
        val = self.payload.get("transcript_path")
        return str(val) if val else None

    @property
    def agent_id(self) -> str | None:
        val = self.payload.get("agent_id")
        return str(val) if val else None


def _classify(event: str | None, payload: dict) -> str | None:
    """Map a hook event name to an edge kind, or None to ignore the envelope."""
    if not event:
        return None
    if event == _TURN_END_EVENT:
        return TURN_END
    if event == _SUBAGENT_EVENT:
        return SUBAGENT_END
    if event in _NEEDS_HUMAN_EVENTS:
        return NEEDS_HUMAN
    if event == "PreToolUse":
        # Only AskUserQuestion is a needs-human edge; the settings matcher
        # already narrows to it, but re-check defensively.
        tool = payload.get("tool_name") or payload.get("matcher") or ""
        if _ASK_USER_MATCHER.lower() in str(tool).lower():
            return NEEDS_HUMAN
        return None
    if event == "PreCompact":
        return COMPACTION
    if event == "SessionStart":
        # Only a compaction-sourced SessionStart is a compaction edge; a fresh
        # session start is not an edge the container acts on.
        if str(payload.get("source", "")).lower() == "compact":
            return COMPACTION
        return None
    return None


class HookEdgeConsumer:
    """Tails a per-session hook edge file and emits typed :class:`HookEdge`s.

    One instance per session (keyed by ``session_id`` for logging / routing).
    The consumer NEVER touches the PTY — it is the transport-agnostic seam.

    Usage::

        consumer = HookEdgeConsumer(edge_file, session_id=pm_session_id)
        for edge in consumer.poll():
            if edge.kind == TURN_END:
                ...

    Level-triggered: :meth:`poll` reads every complete line appended since the
    cursor, so an edge written before the caller starts polling is still
    delivered (Race 1). Fail-silent: a corrupt / partial line is skipped (a
    partial trailing line is left for the next poll to complete), and the
    cursor only advances past complete, parsed lines.
    """

    def __init__(
        self,
        edge_file: str | os.PathLike[str],
        *,
        session_id: str | None = None,
        cursor: HookCursor | None = None,
    ) -> None:
        self.edge_file = pathlib.Path(edge_file)
        self.session_id = session_id
        self.cursor = cursor or HookCursor()

    def _reset_cursor(self) -> None:
        self.cursor = HookCursor()

    def poll(self) -> list[HookEdge]:
        """Read and classify every complete envelope appended since the cursor.

        Advances ``event_cursor`` and ``byte_offset`` past each complete line
        (parsed or skipped-as-garbage), refreshes the fingerprint, and returns
        the classified edges in file order. Never raises.
        """
        if not self.edge_file.exists():
            return []

        try:
            size = self.edge_file.stat().st_size
        except OSError:
            return []

        # Fingerprint the already-consumed prefix (bounded by _FINGERPRINT_BYTES),
        # NOT the whole file: appends grow the tail, so a prefix within the
        # consumed region is immutable across normal appends and only a
        # truncation / head-rewriting replacement changes it (Risk 3 / Practice 4).
        fp_len = min(self.cursor.byte_offset, _FINGERPRINT_BYTES)
        head_fp = _fingerprint(self.edge_file, fp_len)
        replaced = bool(
            self.cursor.fingerprint and fp_len > 0 and head_fp != self.cursor.fingerprint
        )
        if size < self.cursor.byte_offset or replaced:
            logger.warning(
                "[hook-edge] session=%s edge file truncated/replaced "
                "(size=%d offset=%d fp_changed=%s) — resetting cursor",
                self.session_id,
                size,
                self.cursor.byte_offset,
                replaced,
            )
            self._reset_cursor()

        if size <= self.cursor.byte_offset:
            return []

        try:
            with open(self.edge_file, "rb") as f:
                f.seek(self.cursor.byte_offset)
                chunk = f.read(size - self.cursor.byte_offset)
        except OSError:
            return []

        # Only consume up to the last complete (newline-terminated) line; a
        # partial trailing line is left for the next poll (fail-silent on torn
        # writes — mirrors last_assistant_text).
        last_nl = chunk.rfind(b"\n")
        if last_nl == -1:
            # No complete line yet.
            return []
        complete = chunk[: last_nl + 1]

        edges: list[HookEdge] = []
        consumed_events = 0
        for raw_line in complete.split(b"\n"):
            if not raw_line.strip():
                continue
            try:
                envelope = json.loads(raw_line.decode("utf-8", errors="replace"))
            except Exception:
                logger.warning(
                    "[hook-edge] session=%s skipping unparseable edge line", self.session_id
                )
                consumed_events += 1
                continue
            if not isinstance(envelope, dict):
                consumed_events += 1
                continue
            payload = envelope.get("payload")
            if not isinstance(payload, dict):
                payload = {}
            event = envelope.get("event") or payload.get("hook_event_name")
            kind = _classify(event, payload)
            consumed_events += 1
            if kind is None:
                continue
            edges.append(
                HookEdge(
                    kind=kind,
                    event=str(event),
                    payload=payload,
                    ts=float(envelope.get("ts", 0.0) or 0.0),
                )
            )

        self.cursor.byte_offset += len(complete)
        self.cursor.event_cursor += consumed_events
        # Refresh the fingerprint over the newly-consumed prefix so the next
        # poll compares against a stable, immutable-under-append region.
        self.cursor.fingerprint = _fingerprint(
            self.edge_file, min(self.cursor.byte_offset, _FINGERPRINT_BYTES)
        )
        return edges

    def drain_latest(self, kind: str) -> HookEdge | None:
        """Poll once and return the last edge of ``kind`` (or None).

        Convenience for the turn-boundary wait: on a watchdog wake the container
        drains the file first and honors a ``turn_end`` if present before
        interpreting an EOF as a crash (Race 2).
        """
        matching = [e for e in self.poll() if e.kind == kind]
        return matching[-1] if matching else None
