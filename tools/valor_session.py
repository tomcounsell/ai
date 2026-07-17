#!/usr/bin/env python3
"""
Session management CLI for AgentSession — create, steer, monitor, and kill sessions.

Usage:
    valor-session create --role eng --chat-id 123 --message "Plan issue #735"
    valor-session create --role eng --message "Fix the bug" --parent abc123
    valor-session create --role eng --model sonnet --message "Build feature X" --parent abc123
    valor-session create --role eng --message "..." --project-key valor
    valor-session resume --id abc123 --message "Fix: add missing validation"
    valor-session release --pr 900
    valor-session steer --id abc123 --message "Stop after critique stage"
    valor-session status --id abc123
    valor-session status --id abc123 --full-message
    valor-session inspect --id abc123
    valor-session children --id abc123
    valor-session list
    valor-session list --status running
    valor-session list --role eng
    valor-session kill --id abc123
    valor-session kill --all

Project Key Resolution (for `create` subcommand):
    ``project_key`` is the only input that ties a session to a repo. The on-disk
    path (``working_dir``) is always *derived* from
    ``projects.json[project_key].working_directory`` — never supplied
    independently. There is no working-directory override flag.

    Resolution precedence:
        1. ``--project-key <key>`` (explicit flag)
        2. ``--parent <id>`` inherits ``project_key`` from the parent session
        3. ``resolve_project_key(os.getcwd())`` — matches cwd against
           ``projects.json``; raises ``ProjectKeyResolutionError`` on no match
           (no silent "valor" fallback)

    If none of the above yield a key, the CLI exits non-zero with an error
    naming the cwd and listing the available project keys.

This tool is the external interface for session steering. It writes to
the Redis steering list (via agent.steering / steer_session()) and manages
session lifecycle without requiring bridge access.
"""

import argparse
import dataclasses
import json
import logging
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Bounded read-path retry for class-set lookups (issue #1720)
# ---------------------------------------------------------------------------
# popoto's rebuild_indexes() deletes the class set ($Class:AgentSession) and
# re-adds members in batch_size=1000 pipeline batches.  A concurrent
# query.filter(session_id=...) reads smembers($Class:AgentSession) and filters
# in memory; during the delete→re-add window it observes an empty or partial
# class set and returns no result for a live session.
#
# Measured (spike-1, 150 sessions): rebuild_indexes() takes ~600ms; p99
# class-set-empty window = 651ms.  Retry cap: 5 attempts × 200ms = 1000ms
# total, which covers the measured p99 with ~35% margin.
#
# Both reader sites (_find_session in this file and _find_session_by_id in
# sdlc_stage_query.py) share the same design: re-read on empty, return
# immediately on found, fall through to the existing absent-session fallback
# (get_by_id / None) after the cap.  Hot-path sites (worker recovery, steering
# delivery) are deliberately excluded — retry stays at operator/dispatch paths.
_CLASS_SET_RETRY_ATTEMPTS = 5
_CLASS_SET_RETRY_BACKOFF_S = 0.20  # seconds between attempts

# Issue reference matcher (#1109): "issue #N" or "issue N" (case-insensitive).
# Bounded lookbehind via (?:^|\W) so we don't false-positive on "tissue123".
_ISSUE_REF_RE = re.compile(r"(?:^|\W)issue\s*#?\s*(\d+)", re.IGNORECASE)

# Issue #1148: enrichment-header guard. The worker's build_harness_turn_input
# prepends headers like "PROJECT:", "FROM:", "SESSION_ID:", "TASK_SCOPE:",
# "SCOPE:", "MESSAGE:" to the raw message before passing it to claude -p.
# A PM session that lacks the persona+SESSION_TYPE wiring sometimes forwards
# its own enrichment payload as the --message to a child dev session, which
# stalls the dev with "the message appears to have been truncated" (the
# observed failure trace for issue #1148). This guard rejects --message
# values whose first 200 chars start with one of those header prefixes.
# Case-sensitive: lowercase prose like "scope: database stuff" is allowed.
_ENRICHMENT_HEADER_RE = re.compile(r"^(?:SESSION_ID|PROJECT|FROM|TASK_SCOPE|SCOPE):")


def _derive_slug_from_message(message: str) -> str | None:
    """Extract the first issue number from ``message`` and return ``sdlc-{N}``.

    Returns None if no issue reference is present. Used by ``cmd_create`` to
    auto-provision a worktree for PM-role sessions targeting a specific issue.

    Examples:
        "handle issue #1109"       -> "sdlc-1109"
        "Start the pipeline for issue 735" -> "sdlc-735"
        "do something generic"     -> None
    """
    if not message:
        return None
    match = _ISSUE_REF_RE.search(message)
    if not match:
        return None
    return f"sdlc-{match.group(1)}"


# Bootstrap path so this runs as a standalone script from any directory
_repo_root = Path(__file__).parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))


def _load_env() -> None:
    """Load environment variables from .env files."""
    try:
        from dotenv import load_dotenv

        load_dotenv(_repo_root / ".env")
        load_dotenv(Path.home() / "Desktop" / "Valor" / ".env")  # symlink target — no-op
    except Exception:  # noqa: S110 -- dotenv optional; env may be preset
        pass


from agent.constants import WORKER_DOWN_THRESHOLD_S  # noqa: E402


def _resolve_heartbeat_path(repo_root: Path | None = None) -> Path:
    """Resolve the worker heartbeat file path, worktree-aware.

    The worker only ever writes ``data/last_worker_connected`` under the MAIN
    checkout. When this CLI runs from a git worktree (``.worktrees/{slug}/``),
    a ``__file__``-relative path points at the worktree's own ``data/`` dir,
    which the worker never touches — producing a false "worker down" verdict.

    Resolution: ``git -C <repo_root> rev-parse --path-format=absolute
    --git-common-dir`` yields the main checkout's ``.git`` dir (flag order
    matters — ``--path-format=absolute`` must precede ``--git-common-dir``).
    The heartbeat lives at ``<common_dir>.parent / data / last_worker_connected``.

    Relative git output is resolved against ``repo_root`` (never the process
    cwd). Any subprocess failure — non-zero exit, missing git binary, timeout,
    any exception — falls back to the ``__file__``-relative path. Never raises
    (#980 never-raise contract).
    """
    anchor = repo_root if repo_root is not None else Path(__file__).parent.parent
    try:
        import subprocess

        proc = subprocess.run(
            ["git", "-C", str(anchor), "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        output = proc.stdout.strip()
        if proc.returncode == 0 and output:
            common = Path(output)
            abs_common = common if common.is_absolute() else (anchor / common).resolve()
            return abs_common.parent / "data" / "last_worker_connected"
    except Exception:  # noqa: S110 -- falls back to anchor-relative path
        pass
    return anchor / "data" / "last_worker_connected"


def _check_worker_health() -> tuple[bool, int | None]:
    """Check worker health by reading the heartbeat file modification time.

    The heartbeat path comes from :func:`_resolve_heartbeat_path`, which uses
    ``git rev-parse --path-format=absolute --git-common-dir`` so worktree
    checkouts read the main checkout's ``data/last_worker_connected`` (the
    only copy the worker writes).

    Threshold is ``WORKER_DOWN_THRESHOLD_S`` (600s) — 2x the worker's 300s
    heartbeat write cadence (``agent/session_health.py`` health loop), giving
    a full missed write cycle of margin before declaring the worker down.

    Returns (healthy, age_s) where:
      - healthy is True if heartbeat age < WORKER_DOWN_THRESHOLD_S
      - age_s is the integer age in seconds, clamped to >= 0 (future-dated
        mtimes from clock skew / iCloud report 0 == healthy), or None if the
        file is missing or unreadable

    Never raises — the #980 never-raise contract holds end to end; any
    exception (including git failures inside the resolver) yields a fallback
    path or (False, None). Missing file == down (worker has never run here).
    """
    try:
        mtime = _resolve_heartbeat_path().stat().st_mtime
        age_s = max(0, int(time.time() - mtime))
        return (age_s < WORKER_DOWN_THRESHOLD_S, age_s)
    except Exception:
        return (False, None)


def _worker_down_message(age_s: int | None) -> str:
    """Shared warning template for the worker-down state (create + status)."""
    age_str = f"{age_s}s" if age_s is not None else "no file"
    return (
        f"WARNING: no recent worker heartbeat on this machine ({age_str}) — "
        "session will stay pending until a worker is started "
        "(run: ./scripts/valor-service.sh worker-start)"
    )


class ProjectsConfigUnavailableError(RuntimeError):
    """Raised when ``bridge.routing.load_config()`` cannot be loaded.

    This means no project→repo pairing is available at all. The caller cannot
    fall back — there is no "default" project under the immutable-pairing rule.
    """


class ProjectKeyResolutionError(ValueError):
    """Raised when no ``project_key`` can be resolved from inputs.

    Fired when:
    - ``resolve_project_key(cwd)`` is called with a ``cwd`` that matches no
      project ``working_directory`` in ``projects.json``.
    - ``_resolve_project_working_directory(key)`` is called with a key that is
      not in ``projects.json`` or whose project entry has no
      ``working_directory``.

    The ``str(exception)`` form carries a user-oriented message (cwd, available
    keys, suggested remediation) because ``cmd_create``'s broad ``except
    Exception`` is the surface the user ultimately sees.
    """

    def __init__(
        self,
        *,
        cwd: str | None = None,
        project_key: str | None = None,
        available_keys: list[str] | None = None,
        detail: str | None = None,
    ):
        self.cwd = cwd
        self.project_key = project_key
        self.available_keys = available_keys or []
        self.detail = detail
        if cwd is not None:
            msg = (
                f"cwd {cwd!r} does not match any project in projects.json. "
                f"Available keys: {self.available_keys}. "
                f"Pass --project-key <key> explicitly."
            )
        elif project_key is not None:
            msg = (
                f"project_key {project_key!r} is not in projects.json or has no "
                f"working_directory set. Available keys: {self.available_keys}."
            )
            if detail:
                msg += f" ({detail})"
        else:
            msg = detail or "project_key could not be resolved"
        super().__init__(msg)


def resolve_project_key(cwd: str) -> str:
    """Derive the ``project_key`` from ``cwd`` by matching ``projects.json``.

    Loads ``projects.json`` via ``bridge.routing.load_config()``, iterates the
    ``projects`` dict, and returns the key whose ``working_directory`` equals
    or is a parent of ``cwd``. When multiple projects match (overlapping
    paths), the most specific match (longest ``working_directory`` path) wins.

    Args:
        cwd: The current working directory to match against project paths.

    Returns:
        The matching project key.

    Raises:
        ProjectsConfigUnavailableError: If ``load_config()`` itself raises
            (missing file, unreadable, etc.). There is no silent fallback —
            without ``projects.json`` there is no defined project→repo pairing.
        ProjectKeyResolutionError: If no project's ``working_directory`` is an
            ancestor of (or equal to) ``cwd``. The message names ``cwd`` and
            lists the available keys so the caller knows what to pass as
            ``--project-key``.
    """
    try:
        from bridge.routing import load_config

        config = load_config()
    except Exception as e:
        raise ProjectsConfigUnavailableError(f"could not load projects.json: {e}") from e

    cwd_path = Path(cwd).resolve()
    best_key: str | None = None
    best_len: int = -1

    projects = config.get("projects", {})
    for key, project in projects.items():
        wd = project.get("working_directory", "")
        if not wd:
            continue
        try:
            wd_path = Path(wd).resolve()
            if cwd_path == wd_path or cwd_path.is_relative_to(wd_path):
                wd_len = len(str(wd_path))
                if wd_len > best_len:
                    best_len = wd_len
                    best_key = key
        except Exception:  # noqa: S112 -- unresolvable path skipped
            continue

    if best_key is not None:
        return best_key

    raise ProjectKeyResolutionError(cwd=cwd, available_keys=sorted(projects.keys()))


def _resolve_project_working_directory(project_key: str) -> tuple[Path, dict]:
    """Return ``(repo_root_path, project_dict)`` for ``project_key``.

    Loads ``projects.json`` once via ``bridge.routing.load_config()``, looks up
    the project entry, expands ``working_directory`` to an absolute
    ``pathlib.Path``, and returns both the path and the full project dict so the
    caller can pass the dict as ``project_config=`` on the enqueue without a
    second ``load_config()`` call.

    Args:
        project_key: Key into ``projects.json[projects]``. Must be an exact
            match (no fuzzy resolution).

    Returns:
        Tuple of:
          - ``Path``: absolute, user-expanded path to the project's repo root.
          - ``dict``: the raw project entry from ``projects.json``. Pass this
            unchanged to ``_push_agent_session(..., project_config=...)`` so
            CLI-created sessions carry the same payload as bridge-created
            sessions (PR #685).

    Raises:
        ProjectsConfigUnavailableError: If ``load_config()`` itself raises.
        ProjectKeyResolutionError: If ``project_key`` is not in
            ``projects.json`` or its entry has no ``working_directory``.
    """
    try:
        from bridge.routing import load_config

        config = load_config()
    except Exception as e:
        raise ProjectsConfigUnavailableError(f"could not load projects.json: {e}") from e

    projects = config.get("projects", {})
    project = projects.get(project_key)
    if not project:
        raise ProjectKeyResolutionError(
            project_key=project_key,
            available_keys=sorted(projects.keys()),
        )
    wd = project.get("working_directory", "")
    if not wd:
        raise ProjectKeyResolutionError(
            project_key=project_key,
            available_keys=sorted(projects.keys()),
            detail="project entry has no working_directory",
        )
    return Path(wd).expanduser(), project


def _format_ts(ts: str | float | None) -> str:
    """Format a timestamp for display."""
    if ts is None:
        return "—"
    try:
        if isinstance(ts, float | int):
            dt = datetime.fromtimestamp(ts, tz=UTC)
        else:
            dt = datetime.fromisoformat(str(ts))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return str(ts)[:19]


def cmd_create(args: argparse.Namespace) -> int:
    """Create a new AgentSession and enqueue it.

    ``project_key`` determines the repo; ``working_dir`` is always derived from
    ``projects.json[project_key].working_directory`` (optionally with a
    ``.worktrees/{slug}/`` suffix). Callers who need a different repo pass a
    different ``--project-key``.

    Slug requirement: ``--role eng`` (issues #1109, #1272) requires a slug —
    either via ``--slug <slug>`` or by including ``issue #N`` in the message
    so the slug auto-derives to ``sdlc-N``. Slugless invocations exit 1 with
    a stderr error. This prevents the session from inheriting the worker's
    current branch state
    and ensures dev sessions get worktree isolation.
    """
    _load_env()
    try:
        import asyncio
        import os

        from agent.agent_session_queue import _push_agent_session
        from bridge.utc import utc_now
        from config.enums import SessionType

        _role_to_session_type = {
            "eng": SessionType.ENG,
            "teammate": SessionType.TEAMMATE,
        }
        role = args.role or "eng"
        if role not in _role_to_session_type:
            raise ValueError(
                f"Unknown --role value: {role!r}. Allowed values: {sorted(_role_to_session_type)}"
            )
        session_type = _role_to_session_type[role]
        message = args.message
        # Issue #1148: reject enrichment-header forwarding. A 200-char window
        # at the start of the message is enough to catch the observed pattern
        # ("PROJECT: Valor AI\nFOCUS: ...") without false-positives on prose
        # that uses the words SCOPE/FROM/etc. mid-sentence.
        if message and _ENRICHMENT_HEADER_RE.match(message[:200]):
            print(
                "Error: --message looks like a forwarded enrichment header, not a "
                "task description.\n"
                "Enrichment headers (PROJECT:, FROM:, SESSION_ID:, TASK_SCOPE:, "
                "SCOPE:) are\n"
                "injected automatically by the worker. Pass only the task content.",
                file=sys.stderr,
            )
            return 2
        chat_id = args.chat_id or "0"
        parent_id = getattr(args, "parent", None)
        model = getattr(args, "model", None)

        # ------------------------------------------------------------------
        # Stopgap (#1633): refuse NEW parent-attached session creation.
        # The granite PTY container owns the PM/Dev split; parent-linked
        # child sessions double-consume bounded pool slots. Fires before
        # any filesystem or Redis work (slug derivation, worktree
        # provisioning, enqueue) so the refused path has zero side effects.
        # ------------------------------------------------------------------
        if parent_id:
            from models.child_session_gate import (
                BYPASS_WARNING,
                CHILD_SESSIONS_DISABLED_MESSAGE,
                child_sessions_allowed,
                child_sessions_disabled_json,
            )

            if not child_sessions_allowed():
                if getattr(args, "json", False):
                    print(json.dumps(child_sessions_disabled_json(), indent=2))
                else:
                    print(f"Error: {CHILD_SESSIONS_DISABLED_MESSAGE}", file=sys.stderr)
                return 2
            print(BYPASS_WARNING, file=sys.stderr)

        # Derive a session_id from timestamp + role
        ts_suffix = str(int(utc_now().timestamp() * 1000))
        session_id = f"{chat_id}_{ts_suffix}"

        # ------------------------------------------------------------------
        # Step 1: resolve PM slug BEFORE any project/working_dir work.
        #
        # PM auto-slug derivation is a pure function of ``message`` and MUST
        # fire before worktree creation so that "PM without slug and without
        # issue reference" can refuse (exit 1) without first having computed
        # a project root or touching the filesystem.
        # ------------------------------------------------------------------
        slug = getattr(args, "slug", None)
        # Issue #1272: eng sessions also require a slug. An earlier behavior
        # accepted slugless non-teammate roles and let the worker fall back to
        # the main checkout — the residual hole that #887 left open. The
        # argparse layer now rejects ``dev``/``pm`` outright, so the only
        # non-teammate role reaching here is ``eng``; apply the
        # auto-derive-or-reject path, and a synthetic slug is still allocated
        # downstream by the worker if a slugless eng session somehow reaches
        # the executor (future programmatic spawn site).
        if not slug and role != "teammate":
            derived = _derive_slug_from_message(message)
            if derived:
                slug = derived
                print(
                    f"  Auto-derived slug: {slug} (from 'issue #N' in message)",
                    file=sys.stderr,
                )
            else:
                print(
                    "Error: Eng sessions must be created with --slug <slug> "
                    "or include 'issue #N' in the message so a worktree can be "
                    "provisioned. Without a slug the session would inherit the "
                    "worker's current branch state (see issues #1109, #1272).",
                    file=sys.stderr,
                )
                return 1

        # ------------------------------------------------------------------
        # Step 2: resolve project_key.
        #
        # Precedence: --project-key > --parent inheritance > cwd-match.
        # No silent fallback — if all three fail, resolve_project_key raises
        # ProjectKeyResolutionError, which propagates to the outer
        # ``except Exception`` and returns exit 1 with a user-facing message.
        # ------------------------------------------------------------------
        explicit_key = getattr(args, "project_key", None)
        project_key: str | None = None
        if explicit_key:
            project_key = explicit_key
        elif parent_id:
            # Parent inheritance: copy project_key from the parent session.
            # If --parent points to a non-existent session, fall through to
            # cwd-based resolution — a typo in --parent is a separate concern
            # from mis-routing and should not hard-fail creation.
            parent = _find_session(parent_id)
            if parent is not None:
                inherited_key = getattr(parent, "project_key", None)
                if inherited_key:
                    project_key = inherited_key
                    parent_uuid = getattr(parent, "agent_session_id", parent_id)
                    print(
                        f"  Inherited project_key={project_key} from parent {parent_uuid}",
                        file=sys.stderr,
                    )
        if project_key is None:
            # Final fallback: cwd-based match. Raises ProjectKeyResolutionError
            # on no match — no silent "valor" coercion.
            project_key = resolve_project_key(os.getcwd())

        # ------------------------------------------------------------------
        # Step 3: derive repo_root and working_dir from project_key.
        #
        # The project dict returned here becomes project_config= on the
        # enqueue so CLI-created sessions carry the same payload as
        # bridge-created sessions (PR #685).
        # ------------------------------------------------------------------
        repo_root, project_config = _resolve_project_working_directory(project_key)

        if slug:
            from agent.worktree_manager import _validate_slug, get_or_create_worktree

            _validate_slug(slug)  # Raises ValueError for invalid slugs
            wt_path = get_or_create_worktree(repo_root, slug)
            working_dir = str(wt_path)
            print(f"  Worktree:    {working_dir}", file=sys.stderr)
        else:
            working_dir = str(repo_root)

        needs_real_chrome = bool(getattr(args, "needs_real_chrome", False))

        async def _create():
            await _push_agent_session(
                project_key=project_key,
                session_id=session_id,
                working_dir=working_dir,
                message_text=message,
                sender_name=f"valor-session ({role})",
                chat_id=chat_id,
                telegram_message_id=0,
                session_type=session_type,
                parent_agent_session_id=parent_id,
                slug=slug,
                model=model,
                project_config=project_config,
                requires_real_chrome=needs_real_chrome,
            )
            return session_id

        result = asyncio.run(_create())

        # Check worker health after enqueue — warn if no recent heartbeat
        worker_healthy, worker_age_s = _check_worker_health()
        worker_state = "ok" if worker_healthy else "down"

        if args.json:
            print(
                json.dumps(
                    {
                        "session_id": result,
                        "status": "created",
                        "project_key": project_key,
                        "model": model,
                        "worker_healthy": worker_healthy,
                        "worker_state": worker_state,
                        "worker_heartbeat_age_s": worker_age_s,
                    },
                    indent=2,
                )
            )
        else:
            print(f"Created session: {result}")
            print(f"  Role:        {role}")
            print(f"  Project key: {project_key}")
            if model:
                print(f"  Model:       {model}")
            print(f"  Message: {message[:80]}")
            print(f"  Chat ID: {chat_id}")
            if worker_state == "down":
                print(_worker_down_message(worker_age_s), file=sys.stderr)
        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def _find_session(id_arg: str) -> "AgentSession | None":  # noqa: F821
    """Resolve a session by ``session_id`` first, then ``agent_session_id`` (UUID).

    We try ``session_id`` first because it is the canonical routing key used by
    the worker, bridge, and every existing CLI invocation. If the primary lookup
    is empty we fall back to :py:meth:`AgentSession.get_by_id`, the canonical
    UUID helper, so operators can copy the UUID shown as "Session ID" in the
    Claude Code CLI header and pass it straight to ``valor-session``.

    When multiple ``session_id`` records exist (legitimate -- re-enqueue cycles
    create multiple AgentSession rows for the same ``session_id``), the newest
    by ``created_at`` wins. This matches the ``cmd_resume`` convention.

    Assumption: ``session_id`` values never collide with 32-char hex
    ``agent_session_id`` values. Current session_id formats
    (``{chat_id}_{message_id}``, ``tg_{project}_{chat_id}_{message_id}``,
    ``sdlc-local-{issue}``) satisfy this trivially. If a future session_id
    scheme produces 32-char hex values, the session_id-first ordering still
    returns the correct record for session_id callers, but a UUID caller
    could receive the wrong record on collision. Docstring-only guard -- no
    runtime validation.

    UUID-form lookups pay the cost of one empty
    ``AgentSession.query.filter(session_id=<uuid>)`` before the ``get_by_id``
    fallback fires. At operator-initiated CLI invocation rates (a handful per
    day) this is imperceptible. See issue #1061.

    Bounded retry (issue #1720): popoto's rebuild_indexes() transiently empties
    the class set ($Class:AgentSession); a concurrent query.filter(session_id=...)
    reads the class set and returns empty for a live session during that window.
    We retry up to _CLASS_SET_RETRY_ATTEMPTS times with _CLASS_SET_RETRY_BACKOFF_S
    between attempts (total cap sized to exceed spike-1's measured p99 = 651ms),
    then fall through to get_by_id as before.
    """
    from models.agent_session import AgentSession

    for attempt in range(_CLASS_SET_RETRY_ATTEMPTS):
        sessions = list(AgentSession.query.filter(session_id=id_arg))
        if sessions:
            sessions.sort(key=lambda s: s.created_at or 0, reverse=True)
            return sessions[0]
        if attempt < _CLASS_SET_RETRY_ATTEMPTS - 1:
            logger.debug(
                "query.filter(session_id=%r) returned empty on attempt %d/%d"
                " — class-set may be mid-rebuild, retrying in %.0fms",
                id_arg,
                attempt + 1,
                _CLASS_SET_RETRY_ATTEMPTS,
                _CLASS_SET_RETRY_BACKOFF_S * 1000,
            )
            time.sleep(_CLASS_SET_RETRY_BACKOFF_S)
    return AgentSession.get_by_id(id_arg)


@dataclasses.dataclass
class ResumeResult:
    """Result of a resume_session call."""

    success: bool
    session_id: str
    error: str | None = None
    model: str | None = None
    claude_session_uuid: str | None = None
    # Optional operator-facing caveat surfaced by the CLI on resume. None in
    # the normal case: the runner consumes the persisted four-scalar resume
    # context (claude_session_uuid + dev_agent_id + runner_cwd +
    # claude_version) and re-enters the prior transcript via `--resume`
    # (plan #1924, D3 simple resume).
    warning: str | None = None


# Cap the goal folded into a resumed session's first turn input so a very long
# ``message_text`` cannot balloon the turn (stricter than the uncapped
# ``session_executor.py:2269`` pattern this mirrors).
_RESUME_GOAL_MAX_CHARS = 4000

# Prefix marking a message that already carries a folded-in goal. An
# operator-supplied ``--message`` that already starts with this is left
# untouched (no double-wrap). Kept in sync with the wrap emitted below.
_RESUME_GOAL_PREFIX = "[Prior session context:"


def _resolve_resume_goal(session) -> str | None:
    """Return the session's goal for re-injection, or None.

    Resolution order (first non-empty **string** wins): ``context_summary``
    (curated "what this session is about") → ``message_text`` (original task
    anchor) → latest ``summary`` event (most recent progress marker).

    The ``isinstance(str)`` guard is load-bearing: it makes augmentation
    opt-in on the presence of a real string goal, so sessions whose goal
    attributes are non-string (e.g. MagicMock children — truthy but not
    ``str``) are skipped and fall through to no augmentation. Reads only;
    never raises. Over-long goals are truncated at ``_RESUME_GOAL_MAX_CHARS``
    with an ellipsis. See issue #2136.
    """
    for attr in ("context_summary", "message_text", "summary"):
        value = getattr(session, attr, None)
        if isinstance(value, str) and value.strip():
            goal = value.strip()
            if len(goal) > _RESUME_GOAL_MAX_CHARS:
                goal = goal[: _RESUME_GOAL_MAX_CHARS - 1].rstrip() + "…"
            return goal
    return None


def resume_session(session, message: str, *, source: str = "cli") -> "ResumeResult":
    """Programmatic core for resuming a terminal session.

    Shared by cmd_resume (CLI) and the auto-resume reflection.

    - Validates session is in RESUMABLE_STATUSES (not cancelled, not running/pending)
    - Validates session has a claude_session_uuid
    - Pushes the steering message onto the Redis steering list BEFORE transition
      (eliminates the race window — the write is independent of any in-flight
      ORM save on this instance)
    - Atomically transitions to pending via transition_status(..., reject_from_terminal=False)

    Returns a ResumeResult. Never raises — caller checks result.success.
    """
    from models.session_lifecycle import RESUMABLE_STATUSES, transition_status

    _load_env()
    session_id = getattr(session, "session_id", str(session))
    current_status = getattr(session, "status", None)

    if current_status == "pending":
        return ResumeResult(
            success=False,
            session_id=session_id,
            error=f"Session {session_id} is already pending",
        )
    if current_status == "running":
        return ResumeResult(
            success=False,
            session_id=session_id,
            error=f"Session {session_id} is currently running",
        )
    if current_status not in RESUMABLE_STATUSES:
        return ResumeResult(
            success=False,
            session_id=session_id,
            error=(
                f"Session {session_id} has status '{current_status}'. "
                f"Only {sorted(RESUMABLE_STATUSES)} can be resumed."
            ),
        )
    if getattr(session, "claude_session_uuid", None) is None:
        return ResumeResult(
            success=False,
            session_id=session_id,
            error=(
                "cannot resume: no transcript UUID stored "
                "(session was killed before first turn completed)"
            ),
        )

    # Push the steering message BEFORE transitioning to pending so the worker
    # always sees it — eliminates the two-write race (transition then save).
    # This RPUSHes directly to Redis, independent of session.save(), so it
    # cannot be clobbered by a stale bound instance.
    from agent.steering import push_steering_message

    # Fold the session's goal into the first turn input so a resumed session
    # can state its own objective without asking the human (issue #2136),
    # mirroring the continuation-augmentation pattern at
    # session_executor.py:2262-2269. Safe as steering_msgs[0]: resume runs
    # only on a terminal session whose steering list the executor already
    # drained at prior session end, so this push lands at head. Skip the wrap
    # when the operator already supplied a prefixed message (no double-wrap).
    outbound = message
    if not message.lstrip().startswith(_RESUME_GOAL_PREFIX):
        goal = _resolve_resume_goal(session)
        if goal:
            outbound = f"[Prior session context: {goal}]\n\n{message}"

    push_steering_message(session_id, outbound, f"resume:{source}")

    # Transition to pending (atomic — fails if another process raced us).
    # Steering message is already persisted above, so no race window.
    try:
        transition_status(
            session, "pending", reason=f"resume ({source})", reject_from_terminal=False
        )
    except Exception as e:
        return ResumeResult(
            success=False,
            session_id=session_id,
            error=f"Could not transition to pending: {e}",
        )

    return ResumeResult(
        success=True,
        session_id=session_id,
        model=getattr(session, "model", None),
        claude_session_uuid=getattr(session, "claude_session_uuid", None),
    )


def cmd_resume(args: argparse.Namespace) -> int:
    """Resume a completed/killed/failed/abandoned session by re-enqueuing it with a new message.

    Validates the session is in RESUMABLE_STATUSES (completed, killed, failed, or
    abandoned) and has a stored ``claude_session_uuid``. Transitions the session
    back to ``pending`` and appends the new message to the steering queue so
    the worker delivers it as the first message in the resumed conversation.

    This enables hard-PATCH resume: the worker picks up the session and calls
    ``claude -p --resume <uuid>`` to continue the original transcript.

    ``--id`` accepts either ``session_id`` or ``agent_session_id`` (UUID) --
    see :py:func:`_find_session`. Support for ``killed`` / ``failed`` was added
    in issue #1061; ``abandoned`` was added in issue #1539 so the auto-resume
    reflection and operators can recover abandoned sessions without losing context.
    """
    _load_env()
    try:
        from models.session_lifecycle import RESUMABLE_STATUSES

        session_id = args.id
        new_message = args.message

        session = _find_session(session_id)
        if session is None:
            print(f"Error: Session not found: {session_id}", file=sys.stderr)
            return 1

        current_status = getattr(session, "status", None)

        # Fast-path checks for non-terminal active states (separate from RESUMABLE_STATUSES
        # check for clear user-facing error messages)
        if current_status == "pending":
            print(
                f"Error: Session {session_id} is already pending — cannot resume.",
                file=sys.stderr,
            )
            return 1
        if current_status == "running":
            print(
                f"Error: Session {session_id} is currently running — cannot resume.",
                file=sys.stderr,
            )
            return 1

        result = resume_session(session, new_message, source="valor-session resume")

        if not result.success:
            # Map the resume_session error back to operator-facing output.
            # For status rejections, keep the legacy wording pattern.
            if current_status not in RESUMABLE_STATUSES:
                print(
                    f"Error: Session {session_id} has status '{current_status}'. "
                    "Only completed/killed/failed/abandoned sessions can be resumed.",
                    file=sys.stderr,
                )
            else:
                print(f"Error: {result.error}", file=sys.stderr)
            return 1

        model = result.model
        uuid = result.claude_session_uuid

        if args.json:
            _payload = {
                "session_id": session_id,
                "status": "resumed",
                "model": model,
                "claude_session_uuid": uuid,
            }
            if result.warning:
                _payload["warning"] = result.warning
            print(json.dumps(_payload, indent=2))
        else:
            print(f"Resumed session: {session_id}")
            if model:
                print(f"  Model:               {model}")
            if uuid:
                print(f"  Claude session UUID: {uuid}")
            if result.warning:
                print(f"  Warning: {result.warning}")
            print(f"  Message: {new_message[:80]}")
        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_steer(args: argparse.Namespace) -> int:
    """Write a steering message to a session's Redis steering queue.

    ``--id`` accepts either ``session_id`` or ``agent_session_id`` (UUID); we
    resolve at the CLI boundary via :py:func:`_find_session` and then call
    :py:func:`steer_session` with the canonical ``session_id`` so the queue
    helper's contract stays unchanged.
    """
    _load_env()
    try:
        from agent.agent_session_queue import steer_session

        session = _find_session(args.id)
        if session is None:
            err = f"Session not found: {args.id}"
            if args.json:
                print(json.dumps({"success": False, "error": err}))
            else:
                print(f"Error: {err}", file=sys.stderr)
            return 1

        result = steer_session(session.session_id, args.message)

        if args.json:
            print(json.dumps(result, indent=2))
            return 0 if result["success"] else 1

        if result["success"]:
            print(f"Steered session {args.id}: {args.message[:80]!r}")
            return 0
        else:
            print(f"Error: {result['error']}", file=sys.stderr)
            return 1

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_status(args: argparse.Namespace) -> int:
    """Show status of a session.

    ``--id`` accepts either ``session_id`` or ``agent_session_id`` (UUID); see
    :py:func:`_find_session`.
    """
    _load_env()
    try:
        session = _find_session(args.id)
        if session is None:
            print(f"Session not found: {args.id}", file=sys.stderr)
            return 1
        full_message = getattr(args, "full_message", False)

        from agent.steering import peek_steering_messages

        pending_steering = peek_steering_messages(session.session_id)

        # Check worker health when session is pending (compute gate avoids the
        # git subprocess on every status call; non-pending emits null fields)
        worker_healthy: bool | None = None
        worker_age_s: int | None = None
        worker_state: str | None = None
        if session.status == "pending":
            worker_healthy, worker_age_s = _check_worker_health()
            worker_state = "ok" if worker_healthy else "down"

        if args.json:
            data = {
                "agent_session_id": session.agent_session_id,
                "session_id": session.session_id,
                "status": session.status,
                "session_type": getattr(session, "session_type", None),
                "auto_continue_count": session.auto_continue_count,
                "created_at": str(session.created_at) if session.created_at else None,
                "started_at": str(session.started_at) if session.started_at else None,
                "updated_at": str(session.updated_at) if session.updated_at else None,
                "message": session.message_text
                if full_message
                else (session.message_text or "")[:100],
                "message_preview": (session.message_text or "")[:100],  # backward-compat alias
                "queued_steering_messages": [m.get("text", "") for m in pending_steering],
                "slug": getattr(session, "slug", None),
                "branch_name": getattr(session, "branch_name", None),
                "issue_url": getattr(session, "issue_url", None),
                "pr_url": getattr(session, "pr_url", None),
                "parent_agent_session_id": getattr(session, "parent_agent_session_id", None),
            }
            if worker_healthy is not None:
                data["worker_healthy"] = worker_healthy
            # Unconditionally present — null when the session is not pending
            data["worker_state"] = worker_state
            data["worker_heartbeat_age_s"] = worker_age_s
            print(json.dumps(data, indent=2, default=str))
            return 0

        print(f"Session: {session.session_id}")
        print(f"  Status:        {session.status}")
        if worker_state == "down":
            print(f"  {_worker_down_message(worker_age_s)}", file=sys.stderr)
        stype = getattr(session, "session_type", "—")
        print(f"  Type:          {stype}")
        print(f"  Auto-continue: {session.auto_continue_count}")
        print(f"  Created:       {_format_ts(session.created_at)}")
        print(f"  Started:       {_format_ts(session.started_at)}")
        print(f"  Updated:       {_format_ts(session.updated_at)}")
        parent = getattr(session, "parent_agent_session_id", None)
        if parent:
            print(f"  Parent:        {parent}")

        if full_message:
            print(f"  Message:\n{session.message_text or ''}")
        else:
            print(f"  Message:       {(session.message_text or '')[:80]}")

        if pending_steering:
            print(f"  Pending steering messages ({len(pending_steering)}):")
            for i, msg in enumerate(pending_steering, 1):
                print(f"    {i}. {str(msg.get('text', ''))[:80]}")
        else:
            print("  Pending steering messages: none")

        slug = getattr(session, "slug", None)
        if slug:
            print(f"  Slug:          {slug}")
        branch = getattr(session, "branch_name", None)
        if branch:
            print(f"  Branch:        {branch}")
        pr = getattr(session, "pr_url", None)
        if pr:
            print(f"  PR:            {pr}")

        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_inspect(args: argparse.Namespace) -> int:
    """Dump all raw fields of a session for debugging.

    ``--id`` accepts either ``session_id`` or ``agent_session_id`` (UUID); see
    :py:func:`_find_session`.
    """
    _load_env()
    try:
        session = _find_session(args.id)
        if session is None:
            print(f"Session not found: {args.id}", file=sys.stderr)
            return 1

        # Gather all accessible fields
        data: dict = {}
        for field_name in dir(session):
            if field_name.startswith("_"):
                continue
            try:
                val = getattr(session, field_name)
                if callable(val):
                    continue
                data[field_name] = val
            except Exception:  # noqa: S110 -- inspect skips unreadable fields
                pass

        if args.json:
            print(json.dumps(data, indent=2, default=str))
        else:
            for k, v in sorted(data.items()):
                v_str = str(v) if not isinstance(v, str) else v
                if len(v_str) > 200:
                    v_str = v_str[:200] + "…"
                print(f"  {k:<35} {v_str}")

        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_children(args: argparse.Namespace) -> int:
    """List child sessions spawned by a parent session."""
    _load_env()
    try:
        from models.agent_session import AgentSession

        # Resolve the parent's agent_session_id from its session_id
        parent_id = args.id
        parent_sessions = list(AgentSession.query.filter(session_id=parent_id))
        if parent_sessions:
            parent_agent_id = parent_sessions[0].agent_session_id
        else:
            # Maybe they passed the agent_session_id directly
            parent_agent_id = parent_id

        # Scan all sessions for matching parent
        all_children: list[AgentSession] = []
        from models.session_lifecycle import ALL_STATUSES

        for st in ALL_STATUSES:
            try:
                for s in AgentSession.query.filter(status=st):
                    pid = getattr(s, "parent_agent_session_id", None)
                    # Dual-match: caller may pass either session_id or agent_session_id; check both
                    if pid and (pid == parent_agent_id or pid == parent_id):
                        all_children.append(s)
            except Exception as e:
                logger.warning("children: session query failed for status=%s: %s", st, e)

        all_children.sort(key=lambda s: s.created_at or 0)

        if args.json:
            data = [
                {
                    "session_id": s.session_id,
                    "agent_session_id": s.agent_session_id,
                    "status": s.status,
                    "session_type": getattr(s, "session_type", None),
                    "created_at": str(s.created_at) if s.created_at else None,
                    "message_preview": (s.message_text or "")[:120],
                }
                for s in all_children
            ]
            print(json.dumps(data, indent=2, default=str))
            return 0

        if not all_children:
            print(f"No child sessions found for: {parent_id}")
            return 0

        print(f"Children of {parent_id} ({len(all_children)}):")
        print()
        for s in all_children:
            sid = s.session_id or "—"
            status = s.status or "—"
            stype = getattr(s, "session_type", None) or "—"
            created = _format_ts(s.created_at)
            msg = (s.message_text or "")[:60]
            print(f"  {sid:<38} {status:<12} {stype:<8} {created:<22} {msg}")

        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_list(args: argparse.Namespace) -> int:
    """List sessions filtered by status and/or role."""
    _load_env()
    try:
        from models.agent_session import AgentSession

        # Collect all sessions — filter client-side since Popoto filter is limited
        all_sessions: list[AgentSession] = []

        status_filter = getattr(args, "status", None)
        role_filter = getattr(args, "role", None)

        if status_filter:
            for st in status_filter.split(","):
                st = st.strip()
                try:
                    all_sessions.extend(list(AgentSession.query.filter(status=st)))
                except Exception as e:
                    logger.warning("list: session query failed for status=%s: %s", st, e)
        else:
            # All known statuses — use ALL_STATUSES to avoid silently missing statuses
            from models.session_lifecycle import ALL_STATUSES

            for st in ALL_STATUSES:
                try:
                    all_sessions.extend(list(AgentSession.query.filter(status=st)))
                except Exception as e:
                    logger.warning("list: session query failed for status=%s: %s", st, e)

        # Client-side role filter — matches on session_type only
        if role_filter:
            all_sessions = [
                s for s in all_sessions if getattr(s, "session_type", None) == role_filter
            ]

        # Sort by created_at descending
        all_sessions.sort(key=lambda s: s.created_at or 0, reverse=True)

        # Deduplicate by session_id
        seen = set()
        unique = []
        for s in all_sessions:
            if s.session_id not in seen:
                seen.add(s.session_id)
                unique.append(s)

        # Limit
        limit = getattr(args, "limit", 20) or 20
        unique = unique[:limit]

        if args.json:
            data = [
                {
                    "session_id": s.session_id,
                    "status": s.status,
                    "priority": getattr(s, "priority", None) or "normal",
                    "session_type": getattr(s, "session_type", None),
                    "auto_continue_count": s.auto_continue_count,
                    "created_at": str(s.created_at) if s.created_at else None,
                    "message_preview": (s.message_text or "")[:60],
                }
                for s in unique
            ]
            print(json.dumps(data, indent=2, default=str))
            return 0

        if not unique:
            print("No sessions found.")
            return 0

        print(f"Sessions ({len(unique)}):")
        print()
        hdr = f"{'Session ID':<36} {'Status':<12} {'Priority':<8} {'Type':<10} {'Nudges':>6}"
        hdr += f" {'Created':<20} {'Message':<40}"
        print(hdr)
        print("-" * 136)

        for s in unique:
            sid = s.session_id or "—"
            if len(sid) > 34:
                sid = sid[:31] + "..."
            status = s.status or "—"
            priority = getattr(s, "priority", None) or "normal"
            stype = getattr(s, "session_type", None) or "—"
            nudges = s.auto_continue_count or 0
            created = _format_ts(s.created_at)
            msg = (s.message_text or "")[:38]
            row = f"{sid:<36} {status:<12} {priority:<8} {stype:<10} {nudges:>6}"
            row += f" {created:<20} {msg:<40}"
            print(row)

        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_kill(args: argparse.Namespace) -> int:
    """Kill a session or all running sessions.

    Single-session ``--id`` accepts either ``session_id`` or
    ``agent_session_id`` (UUID); see :py:func:`_find_session`. The ``--all``
    path takes no id argument and is unchanged.
    """
    _load_env()
    try:
        from models.agent_session import AgentSession
        from models.session_lifecycle import TERMINAL_STATUSES, finalize_session

        killed = []
        errors = []

        if getattr(args, "all", False):
            # Kill all non-terminal sessions
            for st in ("pending", "running", "active"):
                try:
                    sessions = list(AgentSession.query.filter(status=st))
                    for s in sessions:
                        try:
                            finalize_session(s, "killed", reason="valor-session kill --all")
                            killed.append(s.session_id)
                        except Exception as e:
                            errors.append(f"{s.session_id}: {e}")
                except Exception as query_err:
                    logger.warning(
                        "kill --all: session query failed for status=%s: %s", st, query_err
                    )
        else:
            session_id = args.id
            session = _find_session(session_id)
            if session is None:
                print(f"Session not found: {session_id}", file=sys.stderr)
                return 1

            current_status = getattr(session, "status", None)
            if current_status in TERMINAL_STATUSES:
                msg = f"Session {session_id} is already in terminal status {current_status!r}"
                if args.json:
                    print(json.dumps({"success": False, "error": msg}))
                else:
                    print(f"Warning: {msg}")
                return 0

            finalize_session(session, "killed", reason="valor-session kill")
            killed.append(session.session_id)

        if args.json:
            print(json.dumps({"killed": killed, "errors": errors}, indent=2))
            return 0 if not errors else 1

        if killed:
            print(f"Killed {len(killed)} session(s):")
            for sid in killed:
                print(f"  {sid}")
        if errors:
            print(f"Errors ({len(errors)}):")
            for err in errors:
                print(f"  {err}", file=sys.stderr)

        return 0 if not errors else 1

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_wait_for_children(args: argparse.Namespace) -> int:
    """Transition the calling session to waiting_for_children status.

    Used by PM sessions after spawning child PM sessions via fan-out.
    The parent session will auto-transition to completed when all children
    finish via _finalize_parent_sync() in models.session_lifecycle.
    """
    _load_env()
    try:
        import os

        from models.agent_session import AgentSession
        from models.session_lifecycle import TERMINAL_STATUSES, transition_status

        session_id = getattr(args, "session_id", None) or os.environ.get("AGENT_SESSION_ID")
        if not session_id:
            print(
                "Error: No session ID provided. Use --session-id or set $AGENT_SESSION_ID.",
                file=sys.stderr,
            )
            return 1

        sessions = list(AgentSession.query.filter(session_id=session_id))
        if not sessions:
            print(f"Error: Session not found: {session_id}", file=sys.stderr)
            return 1

        session = sessions[0]
        current_status = getattr(session, "status", None)
        if current_status in TERMINAL_STATUSES:
            print(
                f"Error: Session {session_id} is already in terminal status {current_status!r}.",
                file=sys.stderr,
            )
            return 1

        transition_status(session, "waiting_for_children")
        print(f"Session {session_id} transitioned to waiting_for_children.")
        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_release(args: argparse.Namespace) -> int:
    """Clear retain_for_resume on the BUILD session associated with a PR.

    Called by the PM session after a PR merges or closes to release the BUILD
    session from retention. Without this, the session lingers until the 30-day
    Meta.ttl backstop expires.

    Lookup strategy: match by slug (PR branch `session/{slug}` → slug on AgentSession).
    If no match is found, logs a warning and exits cleanly (no crash — the TTL
    backstop will handle it).
    """
    _load_env()
    try:
        from models.agent_session import AgentSession

        pr_number = str(args.pr)

        # Strategy 1: match by pr_url containing the PR number
        released = []
        all_completed: list[AgentSession] = []
        try:
            all_completed = list(AgentSession.query.filter(status="completed"))
        except Exception as e:
            logger.warning("release: completed-session query failed: %s", e)

        # Also check superseded (may have been superseded after completion)
        try:
            all_completed.extend(list(AgentSession.query.filter(status="superseded")))
        except Exception as e:
            logger.warning("release: superseded-session query failed: %s", e)

        # Strategy 2: match by slug via PR branch name (session/{slug})
        # Fetched once up-front so we don't shell out per-session.
        pr_branch = ""
        try:
            import subprocess as _subprocess

            _gh_result = _subprocess.run(
                ["gh", "pr", "view", pr_number, "--json", "headRefName", "--jq", ".headRefName"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if _gh_result.returncode == 0:
                pr_branch = _gh_result.stdout.strip()
        except Exception:  # noqa: S110 -- gh unavailable: pr_url match fallback
            pass  # gh unavailable — fall back to pr_url matching only

        for s in all_completed:
            pr_url = getattr(s, "pr_url", None) or ""
            slug = getattr(s, "slug", None) or ""
            retain = getattr(s, "retain_for_resume", False)
            if not retain:
                continue
            # Match by pr_url containing PR number
            pr_match = pr_number in pr_url
            # Or match by slug appearing in the PR's branch name (e.g. session/{slug})
            branch_match = bool(slug) and bool(pr_branch) and slug in pr_branch
            if pr_match or branch_match:
                s.retain_for_resume = False
                s.save()
                released.append(s.session_id)

        if args.json:
            print(
                json.dumps(
                    {
                        "pr": pr_number,
                        "released": released,
                        "count": len(released),
                    },
                    indent=2,
                )
            )
        else:
            if released:
                print(f"Released {len(released)} BUILD session(s) for PR #{pr_number}:")
                for sid in released:
                    print(f"  {sid}")
            else:
                print(
                    f"No retained BUILD sessions found for PR #{pr_number}. "
                    "(TTL backstop will handle cleanup.)"
                )
        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_telemetry(args) -> int:
    """Show recorded telemetry events for a session."""
    import json as _json

    from agent.session_telemetry import read_session_timeline

    session_id = args.id
    events = read_session_timeline(session_id)

    if not events:
        print(f"No telemetry recorded for {session_id}")
        return 0

    if args.tail:
        events = events[-args.tail :]

    if args.json:
        for ev in events:
            print(_json.dumps(ev))
        return 0

    # Human-readable timeline
    for ev in events:
        ts = ev.get("ts", "")
        etype = ev.get("type", "unknown")

        if etype == "token_usage":
            usage = ev.get("usage", {})
            total_cost = ev.get("total_cost_usd", 0.0) or 0.0
            summary = (
                f"in={usage.get('input_tokens', 0)} "
                f"out={usage.get('output_tokens', 0)} "
                f"cost=${total_cost:.4f}"
            )
        elif etype == "status_transition":
            from_s = ev.get("from", "?")
            to_s = ev.get("to", "?")
            reason = ev.get("reason", "") or ""
            summary = f"{from_s} -> {to_s} ({reason[:50]})"
        elif etype == "idle_gap":
            gap = ev.get("gap_seconds", 0)
            summary = f"{gap}s idle"
        elif etype == "telemetry_truncated":
            summary = "*** TRUNCATED ***"
        elif etype == "tool_use":
            name = ev.get("name", "?")
            duration = ev.get("duration_seconds")
            summary = f"{name}" + (f" ({duration:.2f}s)" if duration is not None else "")
        else:
            summary = ""

        print(f"{ts}  {etype:<22}  {summary}")

    return 0


def _default_project_key() -> str:
    """Return the default project key from identity.json or PROJECT_KEY env var."""
    import os

    env_key = os.environ.get("PROJECT_KEY", "")
    if env_key:
        return env_key
    try:
        identity_path = _repo_root / "config" / "identity.json"
        with open(identity_path) as f:
            data = json.load(f)
        return data.get("project_key", "valor")
    except Exception:
        return "valor"


def _crash_sig_strategy_stats(sig: object) -> tuple[str | None, int, int]:
    """Extract (strategy, attempts, recovered) from a CrashSignature record.

    Reads the real nested ``outcome_tallies_json`` via the model's
    ``_load_tallies()`` helper. The tally shape is
    ``{strategy_name: {"attempts": N, "recovered": N, "failed": N}}`` — NOT a
    flat dict. Picks the first recorded strategy (the v1 library records a
    single concrete strategy, ``"auto_resume"``).

    Returns ``(None, 0, 0)`` when no outcomes have been recorded.
    """
    loader = getattr(sig, "_load_tallies", None)
    tallies = loader() if callable(loader) else {}
    if not isinstance(tallies, dict) or not tallies:
        return None, 0, 0
    # Pick the first strategy with a dict bucket (per-strategy nested shape).
    for name, bucket in tallies.items():
        if isinstance(bucket, dict):
            attempts = int(bucket.get("attempts") or 0)
            recovered = int(bucket.get("recovered") or 0)
            return name, attempts, recovered
    return None, 0, 0


def cmd_crash_signatures(args: argparse.Namespace) -> int:
    """Show crash signatures in the library for a project.

    Reads all CrashSignature records for the given project and renders them
    in a human-readable or JSON format, showing each signature's human-readable
    form, resumability, occurrence count, and outcome statistics.
    """
    _load_env()
    try:
        from models.crash_signature import CrashSignature
    except ImportError:
        print("Error: crash_signature library not available.", file=sys.stderr)
        return 1

    try:
        project = getattr(args, "project", None) or _default_project_key()
        min_occ = getattr(args, "min_occurrences", 1) or 1
        min_ratio = getattr(args, "min_success_ratio", 0.7) or 0.7

        signatures = CrashSignature.all_for_project(project)
        if min_occ > 1:
            signatures = [s for s in signatures if s.occurrence_count_int >= min_occ]

        if args.json:
            data = []
            for s in signatures:
                strategy, attempts, recovered = _crash_sig_strategy_stats(s)
                confidence = s.policy_confidence(strategy) if strategy else 0.0
                auto_eligible = s.is_auto_eligible(
                    strategy=strategy or "auto_resume",
                    min_occurrences=min_occ,
                    min_success_ratio=min_ratio,
                )
                data.append(
                    {
                        "signature_hash": s.signature_hash,
                        "human_form": s.human_form,
                        "signature_class": getattr(s, "signature_class", None),
                        "resumable": s.is_resumable,
                        "escalated": s.is_escalated,
                        "occurrence_count": s.occurrence_count_int,
                        "project_key": project,
                        "strategy": strategy,
                        "attempts": attempts,
                        "recovered": recovered,
                        "policy_confidence": round(confidence, 4),
                        "auto_eligible": auto_eligible,
                    }
                )
            print(json.dumps(data, indent=2))
            return 0

        if not signatures:
            print("No crash signatures recorded yet.")
            return 0

        print(f"Crash Signatures (project: {project})")
        for s in signatures:
            hash_short = (s.signature_hash or "")[:8]
            human = s.human_form or "(unknown)"
            resumable = s.is_resumable
            escalated = s.is_escalated
            occ = s.occurrence_count_int
            strategy, attempts, recovered = _crash_sig_strategy_stats(s)
            confidence = (s.policy_confidence(strategy) * 100) if strategy else 0.0
            auto_eligible = s.is_auto_eligible(
                strategy=strategy or "auto_resume",
                min_occurrences=min_occ,
                min_success_ratio=min_ratio,
            )

            print(f"  [{hash_short}] {human}")
            resumable_str = "yes" if resumable else "NO"
            auto_str = "yes" if auto_eligible else "no"
            print(f"    occurrences: {occ}  resumable: {resumable_str}  auto-eligible: {auto_str}")
            if resumable and attempts > 0:
                strat_str = f"strategy={strategy}" if strategy else "strategy=?"
                print(
                    f"    outcomes: {strat_str}  attempts={attempts}  recovered={recovered}"
                    f"  confidence={confidence:.1f}%"
                )
            elif escalated:
                print("    escalated: yes")
            print()

        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_crash_policy(args: argparse.Namespace) -> int:
    """Show derived auto-resume policy entries for a project.

    Reads CrashSignature records and displays the policy: which signatures are
    auto-eligible for resume, their strategies, and confidence levels. Useful
    for understanding what the auto-resume system will do for each crash pattern.
    """
    _load_env()
    try:
        from models.crash_signature import CrashSignature
    except ImportError:
        print("Error: crash_signature library not available.", file=sys.stderr)
        return 1

    try:
        project = getattr(args, "project", None) or _default_project_key()
        min_occ = getattr(args, "min_occurrences", 3) or 3
        min_ratio = getattr(args, "min_success_ratio", 0.7) or 0.7

        signatures = CrashSignature.all_for_project(project)

        if args.json:
            data = []
            for s in signatures:
                strategy, attempts, recovered = _crash_sig_strategy_stats(s)
                confidence = s.policy_confidence(strategy) if strategy else 0.0
                occ = s.occurrence_count_int
                auto_eligible = s.is_auto_eligible(
                    strategy=strategy or "auto_resume",
                    min_occurrences=min_occ,
                    min_success_ratio=min_ratio,
                )
                data.append(
                    {
                        "signature_hash": s.signature_hash,
                        "human_form": s.human_form,
                        "resumable": s.is_resumable,
                        "occurrence_count": occ,
                        "confidence": round(confidence, 4),
                        "recovered": recovered,
                        "attempts": attempts,
                        "strategy": strategy,
                        "auto_eligible": auto_eligible,
                        "escalated": s.is_escalated,
                    }
                )
            print(json.dumps(data, indent=2))
            return 0

        if not signatures:
            print("No auto-resume policy entries — library is cold (no signatures yet).")
            return 0

        print(f"Auto-Resume Policy (project: {project})")
        for s in signatures:
            resumable = s.is_resumable
            escalated = s.is_escalated
            occ = s.occurrence_count_int
            strategy, attempts, recovered = _crash_sig_strategy_stats(s)
            confidence_pct = (s.policy_confidence(strategy) * 100) if strategy else 0.0
            auto_eligible = s.is_auto_eligible(
                strategy=strategy or "auto_resume",
                min_occurrences=min_occ,
                min_success_ratio=min_ratio,
            )
            hash_short = (s.signature_hash or "")[:8]

            if not resumable:
                esc_str = f" ({occ} escalated)" if escalated else ""
                print(f"  No entries for NON_RESUMABLE signatures{esc_str}.")
                continue

            print(f"  Signature: {s.human_form or '(unknown)'}")
            print(f"    Hash: {hash_short}")
            if strategy:
                print(f"    Strategy: {strategy}")
            print(f"    Confidence: {confidence_pct:.1f}%  ({recovered}/{attempts} recovered)")
            if auto_eligible:
                elig_str = (
                    f"YES (occurrences={occ} >= {min_occ},"
                    f" confidence={confidence_pct:.1f}% >= {min_ratio * 100:.1f}%)"
                )
            elif occ < min_occ:
                elig_str = f"NO (occurrences={occ} < {min_occ})"
            else:
                elig_str = f"NO (confidence={confidence_pct:.1f}% < {min_ratio * 100:.1f}%)"
            print(f"    Auto-eligible: {elig_str}")
            print()

        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        prog="valor-session",
        description="Manage AgentSessions — create, steer, monitor, and kill",
    )
    parser.add_argument("--json", action="store_true", help="Output JSON")

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # create subcommand
    create_parser = subparsers.add_parser("create", help="Create and enqueue a new session")
    create_parser.add_argument(
        "--role",
        "-r",
        default="eng",
        choices=["eng", "teammate"],
        help="Session role/type (default: eng)",
    )
    create_parser.add_argument(
        "--message", "-m", required=True, help="Initial message for the session"
    )
    create_parser.add_argument("--chat-id", help="Telegram chat ID (default: 0)")
    create_parser.add_argument("--parent", help="Parent AgentSession ID (for child sessions)")
    create_parser.add_argument(
        "--project-key",
        help=(
            "Explicit project key (overrides automatic cwd-based resolution). "
            "If omitted, the key is derived from --parent (if set) or from the "
            "current working directory by matching against projects.json. "
            "On no match, the CLI exits with an error listing available keys. "
            "The matched project's working_directory sets the session's "
            "working_dir; there is no separate working-directory override flag."
        ),
    )
    create_parser.add_argument(
        "--slug",
        help=(
            "Work item slug for worktree isolation. When provided, a worktree "
            "is provisioned at .worktrees/{slug}/ and working_dir is set to it. "
            "This ensures the session runs in an isolated directory (issue #887)."
        ),
    )
    create_parser.add_argument(
        "--model",
        help=(
            "Claude model to use for this session (e.g. 'sonnet', 'opus'). "
            "When set, overrides the environment/CLI default. "
            "Enables per-SDLC-stage model selection."
        ),
    )
    create_parser.add_argument(
        "--needs-real-chrome",
        action="store_true",
        help=(
            "Mark this session as requiring the real Chrome (BYOB MCP) surface. "
            "The worker scheduler will not start this session concurrently with "
            "another --needs-real-chrome session, since real Chrome has one DOM "
            "tree (issue #1256, Decision 2). No effect on ordinary sessions."
        ),
    )
    create_parser.add_argument("--json", action="store_true", help="Output JSON")

    # resume subcommand
    resume_parser = subparsers.add_parser(
        "resume",
        help="Resume a completed BUILD session with a new message (hard-PATCH resume)",
    )
    resume_parser.add_argument(
        "--id", required=True, help="Session ID of the completed BUILD session to resume"
    )
    resume_parser.add_argument(
        "--message",
        "-m",
        required=True,
        help="New message to inject into the resumed session",
    )
    resume_parser.add_argument("--json", action="store_true", help="Output JSON")

    # steer subcommand
    steer_parser = subparsers.add_parser("steer", help="Inject a steering message into a session")
    steer_parser.add_argument("--id", required=True, help="Session ID to steer")
    steer_parser.add_argument("--message", "-m", required=True, help="Steering message to inject")
    steer_parser.add_argument("--json", action="store_true", help="Output JSON")

    # status subcommand
    status_parser = subparsers.add_parser("status", help="Show session status")
    status_parser.add_argument("--id", required=True, help="Session ID")
    status_parser.add_argument(
        "--full-message",
        dest="full_message",
        action="store_true",
        help="Print full initial message without truncation",
    )
    status_parser.add_argument("--json", action="store_true", help="Output JSON")

    # list subcommand
    list_parser = subparsers.add_parser("list", help="List sessions")
    list_parser.add_argument("--status", help="Filter by status (comma-separated)")
    list_parser.add_argument("--role", help="Filter by role/session_type")
    list_parser.add_argument("--limit", type=int, default=20, help="Max results (default: 20)")
    list_parser.add_argument("--json", action="store_true", help="Output JSON")

    # inspect subcommand
    inspect_parser = subparsers.add_parser(
        "inspect", help="Dump all raw fields of a session (for debugging)"
    )
    inspect_parser.add_argument("--id", required=True, help="Session ID")
    inspect_parser.add_argument("--json", action="store_true", help="Output JSON")

    # children subcommand
    children_parser = subparsers.add_parser(
        "children", help="List child sessions spawned by a parent session"
    )
    children_parser.add_argument("--id", required=True, help="Parent session ID")
    children_parser.add_argument("--json", action="store_true", help="Output JSON")

    # kill subcommand
    kill_parser = subparsers.add_parser("kill", help="Kill a session")
    kill_parser.add_argument("--id", help="Session ID to kill")
    kill_parser.add_argument("--all", action="store_true", help="Kill all running sessions")
    kill_parser.add_argument("--json", action="store_true", help="Output JSON")

    # wait-for-children subcommand
    wfc_parser = subparsers.add_parser(
        "wait-for-children",
        help="Transition session to waiting_for_children (called by PM after fan-out)",
    )
    wfc_parser.add_argument(
        "--session-id",
        dest="session_id",
        help="Session ID to transition (defaults to $AGENT_SESSION_ID env var)",
    )

    # release subcommand
    release_parser = subparsers.add_parser(
        "release",
        help="Clear retain_for_resume on BUILD session(s) associated with a merged/closed PR",
    )
    release_parser.add_argument(
        "--pr",
        required=True,
        type=int,
        help="PR number whose BUILD session(s) should be released from retention",
    )
    release_parser.add_argument("--json", action="store_true", help="Output JSON")

    # telemetry subcommand
    telemetry_parser = subparsers.add_parser(
        "telemetry", help="Show recorded telemetry events for a session"
    )
    telemetry_parser.add_argument("--id", required=True, help="Session ID to show telemetry for")
    telemetry_parser.add_argument(
        "--json", action="store_true", help="Emit raw JSON events (one per line)"
    )
    telemetry_parser.add_argument(
        "--tail", type=int, metavar="N", help="Show only the last N events"
    )

    # crash-signatures subcommand
    crash_sig_parser = subparsers.add_parser(
        "crash-signatures",
        help="Show all crash signatures in the library for a project",
    )
    crash_sig_parser.add_argument(
        "--project",
        help="Project key (default: from identity.json or $PROJECT_KEY env var)",
    )
    crash_sig_parser.add_argument(
        "--min-occurrences",
        dest="min_occurrences",
        type=int,
        default=1,
        help="Only show signatures with at least this many occurrences (default: 1)",
    )
    crash_sig_parser.add_argument("--json", action="store_true", help="Output JSON")

    # crash-policy subcommand (with nested sub-action)
    crash_policy_parser = subparsers.add_parser(
        "crash-policy",
        help="Show derived auto-resume policy for a project",
    )
    crash_policy_subparsers = crash_policy_parser.add_subparsers(
        dest="crash_policy_action", help="Action"
    )
    policy_list_parser = crash_policy_subparsers.add_parser(
        "list", help="List auto-resume policy entries"
    )
    policy_list_parser.add_argument(
        "--project",
        help="Project key (default: from identity.json or $PROJECT_KEY env var)",
    )
    policy_list_parser.add_argument(
        "--min-occurrences",
        dest="min_occurrences",
        type=int,
        default=3,
        help="Occurrences threshold for auto-eligibility (default: 3)",
    )
    policy_list_parser.add_argument(
        "--min-success-ratio",
        dest="min_success_ratio",
        type=float,
        default=0.7,
        help="Success ratio threshold for auto-eligibility (default: 0.7)",
    )
    policy_list_parser.add_argument("--json", action="store_true", help="Output JSON")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    # crash-policy dispatches to sub-action
    if args.command == "crash-policy":
        action = getattr(args, "crash_policy_action", None)
        if action == "list":
            return cmd_crash_policy(args)
        crash_policy_parser.print_help()
        return 1

    dispatch = {
        "create": cmd_create,
        "resume": cmd_resume,
        "steer": cmd_steer,
        "status": cmd_status,
        "list": cmd_list,
        "kill": cmd_kill,
        "wait-for-children": cmd_wait_for_children,
        "release": cmd_release,
        "inspect": cmd_inspect,
        "children": cmd_children,
        "telemetry": cmd_telemetry,
        "crash-signatures": cmd_crash_signatures,
    }

    return dispatch[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
