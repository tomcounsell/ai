"""Hook edge channel for the headless session runner — the only turn-end source.

Claude Code's own hook event stream is the deterministic source of two
decisions: the **turn-end** edge (``Stop``) and the **needs-human** edge
(substantive ``Notification`` / ``PreToolUse(AskUserQuestion)``). This module
owns the read side of that stream. Turn-end comes from the protocol (a
``Stop`` envelope reconciled with the stream-json ``result`` event) — never
from inferring state out of terminal output.

Three pieces live here:

1. :func:`generate_hook_settings` — writes a per-session ``settings.json``
   registering every target hook to the fail-silent
   :mod:`agent.session_runner.hook_forwarder`, and returns the
   ``(settings_path, edge_file_path)`` pair. ``claude --settings <path>`` is
   the injection seam. ``PermissionRequest`` is deliberately NOT registered:
   PermissionRequest hooks do not fire under ``claude -p`` (headless), and
   role sessions run ``--permission-mode bypassPermissions`` anyway.

2. :class:`HookCursor` — the durable ``(event_cursor, byte_offset,
   cursor_fingerprint)`` triple. It makes a worker restart replay only unseen
   edges and never double-deliver, and detects truncation / replacement
   before seeking a stale offset.

3. :class:`HookEdgeConsumer` — tails the append-only NDJSON edge file from the
   cursor, classifies each envelope into a typed :class:`HookEdge`
   (``turn_end`` / ``subagent_end`` / ``needs_human`` / ``compaction``), and
   advances the cursor. Level-triggered (reads from the cursor, not
   edge-triggered), so a ``Stop`` written before the wait arms is still read
   when the wait begins — no missed edge. Fail-silent on corrupt / partial
   lines.

Notification classification (#1919): Claude Code's ``idle_prompt``
Notification fires after *every* response — treating it as a needs-human edge
both leaked "Claude is waiting for your input" boilerplate to the user and
preempted delivery of the real answer. :func:`_classify` is therefore
content-aware — a Notification carrying known Claude Code boilerplate (the
exact idle string, the permission-phrasing prefix) or an empty message emits
NO edge; only a substantive Notification classifies as ``needs_human``.

Edge transport is an append-only file + durable cursor, NOT a Redis list:
this honors the repo's Popoto-only Redis rule, needs no new Popoto model, and
is restart-safe. The forwarder writes a file path; no Redis client ever runs
inside a hook subprocess.
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
# per-session env overlay alongside ``AGENT_SESSION_ID``.
EDGE_FILE_ENV = "SESSION_RUNNER_HOOK_EDGE_FILE"

# Env overrides every headless ``claude -p`` spawn must carry via a CLI
# ``--settings`` source (file or inline JSON) — the ONLY settings layer that
# outranks the fleet-wide user settings. Deliberately NOT a plain subprocess
# env var: the ``env`` block in ``~/.claude/settings.json`` overwrites the
# inherited process environment, so a proc_env value is silently stomped
# (verified empirically on Claude Code v2.1.204).
#
# CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS is enabled fleet-wide for INTERACTIVE
# sessions (scripts/update/hardlinks.py ``_USER_ENV_DEFAULTS``) but disabled
# for headless spawns: in-process teammates do not survive the runner's
# per-turn ``--resume``, die when the single-shot ``-p`` process exits, and
# bypass the PM→dev subagent continuation contract. Decision record + GA
# review trigger: docs/features/agent-teams-headless-policy.md.
HEADLESS_ENV_OVERRIDES: dict[str, str] = {"CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "0"}

# Absolute path to the fail-silent forwarder script. Resolved once at import
# so the generated settings can register ``python3 <abspath>`` and the spawned
# ``claude`` runs it regardless of its cwd.
_FORWARDER_PATH = str(pathlib.Path(__file__).resolve().parent / "hook_forwarder.py")

# The hooks the per-session settings file registers, keyed by Claude Code's
# ``hook_event_name``. Every one routes to the same forwarder; the consumer
# classifies by event name downstream.
#   - Stop / SubagentStop: turn-end vs subagent-end (native disambiguation).
#   - Notification: content-classified downstream — boilerplate/empty is
#     ignored, substantive is the needs-human edge (#1919).
#   - PreToolUse (matcher AskUserQuestion): the agent asking the human a
#     question mid-turn — needs-human.
#   - PreCompact / SessionStart: compaction status, explicitly NOT turn-end.
#   - PermissionRequest is NOT registered — it does not fire under
#     ``claude -p``. Its classification branch is retained defensively below
#     in case an envelope ever arrives from other tooling.
_TURN_END_EVENT = "Stop"
_SUBAGENT_EVENT = "SubagentStop"
_NEEDS_HUMAN_EVENTS = frozenset({"PermissionRequest"})
_COMPACTION_EVENTS = frozenset({"PreCompact", "SessionStart"})
_ASK_USER_MATCHER = "AskUserQuestion"

# Edge-kind constants (the consumer's public vocabulary).
TURN_END = "turn_end"
SUBAGENT_END = "subagent_end"
NEEDS_HUMAN = "needs_human"
COMPACTION = "compaction"

# ---------------------------------------------------------------------------
# Known Claude Code boilerplate Notification text (#1919)
# ---------------------------------------------------------------------------
# The single central definition of non-informative Notification strings. Both
# the classifier here and any downstream extraction filter must consume THIS
# constant — one definition, conservative matching (exact string / stable
# prefix, never a broad substring).
#
# Provisional — Claude Code notification text; re-verify on every CLI bump.
CLAUDE_CODE_BOILERPLATE = {
    # The idle_prompt Notification fires after every response; its message is
    # this exact fixed string. It is a liveness ping, not an input request.
    "idle_exact": "Claude is waiting for your input",
    # Permission-request phrasing prefix ("Claude needs your permission to
    # use Bash", ...). Under bypassPermissions these are near-impossible, but
    # if one ever arrives its raw text must not reach the user.
    "permission_prefix": "Claude needs your permission to use ",
}


def is_boilerplate_notification(message: str | None) -> bool:
    """True when ``message`` is known Claude Code boilerplate (or empty).

    Conservative by design: matches the exact idle string and the stable
    permission-phrasing prefix only. A substantive message that merely
    *contains* similar words (e.g. a real question mentioning "input") is NOT
    boilerplate and must still route as needs-human.
    """
    if message is None:
        return True
    text = message.strip()
    if not text:
        return True
    if text == CLAUDE_CODE_BOILERPLATE["idle_exact"]:
        return True
    return text.startswith(CLAUDE_CODE_BOILERPLATE["permission_prefix"])


# How many leading bytes of the edge file the fingerprint hashes. A truncation
# or replacement (log-rotation, fresh-session reuse of a path) changes these
# bytes; the consumer detects the mismatch and resets its offset to 0 rather
# than seeking into a stale/rewritten file.
_FINGERPRINT_BYTES = 256


def _fingerprint(path: pathlib.Path, nbytes: int) -> str:
    """Hash the first ``nbytes`` of ``path`` (or "" if nbytes<=0 / unreadable).

    ``nbytes`` is deliberately a *fixed prefix within the already-consumed
    region* (see :meth:`HookEdgeConsumer.poll`), never the whole file: an
    append-only edge file grows at the tail, so a prefix inside the consumed
    region is immutable across normal appends. Only a truncation or a
    head-rewriting replacement changes it — which is exactly what the
    fingerprint must detect.
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
    filename: str = "session_runner_hook_settings.json",
    pre_authorize: bool = True,
) -> tuple[str, str]:
    """Write the per-session ``--settings`` file and return ``(settings, edge)``.

    ``settings_dir`` is where the settings JSON is written; ``edge_file`` is the
    NDJSON edge file the forwarder appends to (its path is what
    :data:`EDGE_FILE_ENV` may carry in the child env). Every target hook is
    registered to ``python3 <forwarder>``.

    ``PermissionRequest`` hooks are NOT registered — they do not fire under
    ``claude -p``.

    ``pre_authorize``: when True, the generated settings also carry a
    ``permissions.defaultMode = "bypassPermissions"`` block, reinforcing the
    ``--permission-mode bypassPermissions`` spawn flag through the settings
    source.

    The edge file's parent is created and the file is touched empty so its path
    is *reserved before the first turn* — a level-triggered consumer can then
    always open it (the edge path exists before any Stop can fire).
    """
    settings_dir = pathlib.Path(settings_dir)
    settings_dir.mkdir(parents=True, exist_ok=True)
    edge_path = pathlib.Path(edge_file)
    edge_path.parent.mkdir(parents=True, exist_ok=True)
    # Reserve the edge path. Touch-if-absent — never truncate an existing
    # file (a resumed session may already have edges).
    if not edge_path.exists():
        edge_path.touch()

    forwarder = forwarder_path or _FORWARDER_PATH
    # Embed the edge path as the forwarder's first CLI arg so concurrent
    # sessions sharing one process env still write to separate per-session
    # edge files. Both paths are quoted so a path with spaces survives the
    # shell Claude Code runs the hook under.
    command = f'python3 "{forwarder}" "{edge_path}"'
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
        "PreToolUse": ask_user_entry,
        "PreCompact": all_events_entry,
        "SessionStart": all_events_entry,
    }
    # Headless spawns must not form agent teams — see HEADLESS_ENV_OVERRIDES.
    settings_obj: dict = {"hooks": hooks, "env": dict(HEADLESS_ENV_OVERRIDES)}
    if pre_authorize:
        # Reinforce the spawn-time --permission-mode bypassPermissions through
        # the settings source so the permission bar is pre-answered.
        settings_obj["permissions"] = {"defaultMode": "bypassPermissions"}
    settings_path = settings_dir / filename
    settings_path.write_text(json.dumps(settings_obj, indent=2))
    return (str(settings_path), str(edge_path))


@dataclass
class HookCursor:
    """Durable, idempotent read position into an append-only edge file.

    - ``event_cursor`` — count of complete envelopes consumed (monotonic within
      a file identity; the idempotency key).
    - ``byte_offset`` — byte position up to which complete lines were read.
    - ``fingerprint`` — hash of the file head at the last read; a mismatch means
      the file was truncated / replaced, so the offset is stale and must reset.

    Serializable to/from a plain dict so a caller (e.g. a worker restart) can
    persist and restore it. It carries no framework dependency and is
    intentionally NOT a Popoto model — it is per-session file state.
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
    JSON; ``transcript_path`` / ``session_id`` / ``agent_id`` are lifted for
    convenience.
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
    """Map a hook event name to an edge kind, or None to ignore the envelope.

    ``Notification`` is content-aware (#1919): the idle-prompt boilerplate,
    the permission-phrasing boilerplate, and empty messages are liveness
    noise — no edge is emitted. Only a substantive Notification message
    classifies as :data:`NEEDS_HUMAN`.
    """
    if not event:
        return None
    if event == _TURN_END_EVENT:
        return TURN_END
    if event == _SUBAGENT_EVENT:
        return SUBAGENT_END
    if event == "Notification":
        message = payload.get("message")
        if is_boilerplate_notification(str(message) if message is not None else None):
            return None
        return NEEDS_HUMAN
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
        # session start is not an edge the runner acts on.
        if str(payload.get("source", "")).lower() == "compact":
            return COMPACTION
        return None
    return None


class HookEdgeConsumer:
    """Tails a per-session hook edge file and emits typed :class:`HookEdge`s.

    One instance per session (keyed by ``session_id`` for logging / routing).
    The consumer reads only the protocol's own envelopes — it is the sole
    turn-end source.

    Usage::

        consumer = HookEdgeConsumer(edge_file, session_id=session_id)
        for edge in consumer.poll():
            if edge.kind == TURN_END:
                ...

    Level-triggered: :meth:`poll` reads every complete line appended since the
    cursor, so an edge written before the caller starts polling is still
    delivered. Fail-silent: a corrupt / partial line is skipped (a partial
    trailing line is left for the next poll to complete), and the cursor only
    advances past complete, parsed lines.
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
        # truncation / head-rewriting replacement changes it.
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
        # writes).
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

        Convenience for the turn-boundary wait: the caller drains the file
        first and honors a ``turn_end`` if present before interpreting a
        subprocess exit as a crash.
        """
        matching = [e for e in self.poll() if e.kind == kind]
        return matching[-1] if matching else None
