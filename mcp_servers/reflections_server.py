"""FastMCP server exposing reflection management tools to Claude Code.

Tools allow agent sessions to create, list, inspect, update, remove, pause,
resume, and view runs of registered Reflections (the unified recurring-work
scheduler — see ``models/reflection.py`` and ``agent/reflection_scheduler.py``).

Authorization model (Q7 cycle-4 of the unify-recurring-tasks plan)
------------------------------------------------------------------
- ``_caller_id()`` reads ``AGENT_SESSION_ID`` / ``VALOR_SESSION_ID`` from env.
- ``_can_update`` allows a caller iff (a) no caller id is set (CLI / human
  context) or (b) the caller created the reflection.
- ``_can_remove`` is stricter for unidentified callers: no caller id only
  passes when ``REFLECTIONS_REGISTRY_SOURCE=1`` (i.e. the registry sync path).

Both auth predicates make `agent` callers strictly scoped to their own
reflections, while the human/CLI surface keeps full access.

Each tool wraps its body in try/except and returns ``{"error": str, "code": str}``
on failure rather than raising — FastMCP serializes these as valid tool
responses, so the agent sees a description rather than a protocol error.

Transport: stdio (default).
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Project root must be importable when invoked as ``python -m mcp_servers.reflections_server``
# from a stripped-environment Claude Code subprocess. ``PYTHONPATH`` registered in
# ``~/.claude.json`` is the canonical mechanism, but be defensive in case the env
# inherits without it.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from mcp.server.fastmcp import FastMCP  # noqa: E402

mcp = FastMCP("reflections")


# --------------------------------------------------------------------------
# Auth helpers (Q7 cycle-4)
# --------------------------------------------------------------------------


def _caller_id() -> str | None:
    """Return the calling agent session id, or None for CLI/human callers."""
    return os.environ.get("AGENT_SESSION_ID") or os.environ.get("VALOR_SESSION_ID")


def _can_update(reflection) -> bool:
    """An agent caller may update only its own reflections; CLI may update any."""
    caller = _caller_id()
    if caller is None:
        return True
    return caller == getattr(reflection, "created_by_session_id", None)


def _can_remove(reflection) -> bool:
    """Stricter than _can_update for unidentified callers.

    Unidentified callers (no env var) must set ``REFLECTIONS_REGISTRY_SOURCE=1``
    to remove — only the registry sync path should be deleting reflections
    out-of-band. Identified callers may remove only their own reflections.
    """
    caller = _caller_id()
    if caller is None:
        return os.environ.get("REFLECTIONS_REGISTRY_SOURCE") == "1"
    return caller == getattr(reflection, "created_by_session_id", None)


# --------------------------------------------------------------------------
# Serialization helpers
# --------------------------------------------------------------------------


_REFLECTION_FIELDS = (
    "name",
    "schedule",
    "execution_type",
    "command",
    "output_sink",
    "ran_at",
    "run_count",
    "last_status",
    "last_error",
    "last_duration",
    "last_run_summary",
    "failure_count_consecutive",
    "retry_policy",
    "paused_until",
    "dead_letter_escalated",
    "cost_usd_total",
    "tokens_input_total",
    "tokens_output_total",
    "created_by_session_id",
    "auto_delete_after_run",
)


def _reflection_to_dict(reflection) -> dict:
    """Best-effort serialization of a Reflection record."""
    out: dict = {}
    for field in _REFLECTION_FIELDS:
        try:
            out[field] = getattr(reflection, field, None)
        except Exception:
            out[field] = None
    return out


def _run_to_dict(run) -> dict:
    return {
        "name": getattr(run, "name", ""),
        "timestamp": getattr(run, "timestamp", 0.0),
        "status": getattr(run, "status", ""),
        "duration_ms": getattr(run, "duration_ms", 0),
        "cost_usd": getattr(run, "cost_usd", 0.0),
        "tokens_input": getattr(run, "tokens_input", 0),
        "tokens_output": getattr(run, "tokens_output", 0),
        "error": getattr(run, "error", None),
        "output_summary": getattr(run, "output_summary", None),
        "delivery_error": getattr(run, "delivery_error", None),
        "projects": list(getattr(run, "projects", []) or []),
    }


def _is_at_schedule(schedule: str) -> bool:
    return isinstance(schedule, str) and schedule.strip().startswith("at:")


def _err(msg: str, code: str) -> dict:
    return {"error": msg, "code": code}


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------


@mcp.tool()
def reflections_create(
    name: str,
    schedule: str,
    execution_type: str,
    callable: str | None = None,
    command: str | None = None,
    output_sink: str = "log_only",
    cron_tz: str = "UTC",
    retry_policy: dict | None = None,
    group: str | None = None,
) -> dict:
    """Create a new Reflection.

    Validates the schedule string via ``compute_next_due`` (raises ValueError
    on bad input → ``{"code": "BAD_SCHEDULE"}``). Exactly one of ``callable``
    or ``command`` must be set, depending on ``execution_type``:

    - ``execution_type="function"`` requires ``callable`` (dotted path).
    - ``execution_type="agent"`` requires ``command`` (prompt or CLI invocation).

    The new record's ``created_by_session_id`` is set from the calling session
    (``AGENT_SESSION_ID`` / ``VALOR_SESSION_ID`` env). For ``at:<ISO8601>``
    schedules ``auto_delete_after_run`` is forced True (one-shot semantics).

    Args:
        name: Unique reflection name (KeyField in Redis).
        schedule: ``cron:<expr>`` / ``every:<N><s|m|h|d>`` / ``at:<ISO8601>``.
        execution_type: ``"function"`` or ``"agent"``.
        callable: Dotted path for function-type reflections.
        command: Prompt or CLI invocation for agent-type reflections.
        output_sink: ``log_only`` / ``dashboard_only`` / ``memory:<imp>`` /
            ``telegram:<chat>``. Defaults to ``log_only``.
        cron_tz: Timezone for cron schedules (default ``UTC``).
        retry_policy: Optional dict, stored on the record for the scheduler.
        group: Optional grouping label (stored in retry_policy meta if needed).

    Returns:
        ``{"name": str, "next_due": float}`` on success, or
        ``{"error": str, "code": str}`` on validation failure.
    """
    try:
        if not name or not isinstance(name, str):
            return _err("name required", "BAD_INPUT")
        if not schedule or not isinstance(schedule, str):
            return _err("schedule required", "BAD_INPUT")
        if execution_type not in ("function", "agent"):
            return _err(
                f"execution_type must be 'function' or 'agent', got {execution_type!r}",
                "BAD_INPUT",
            )

        # Exactly one of callable / command per execution_type
        if execution_type == "function":
            if not callable or command:
                return _err(
                    "function execution_type requires `callable` (and not `command`)",
                    "BAD_INPUT",
                )
        else:  # agent
            if not command or callable:
                return _err(
                    "agent execution_type requires `command` (and not `callable`)",
                    "BAD_INPUT",
                )

        # Validate schedule via compute_next_due
        try:
            from agent.reflection_scheduler import compute_next_due

            next_due = compute_next_due(schedule, None, cron_tz=cron_tz)
        except ValueError as e:
            return _err(str(e), "BAD_SCHEDULE")
        except Exception as e:  # noqa: BLE001
            return _err(f"schedule validation failed: {type(e).__name__}: {e}", "BAD_SCHEDULE")

        from models.reflection import Reflection

        # Reject duplicate names
        existing = Reflection.query.filter(name=name)
        if existing:
            return _err(f"reflection already exists: {name}", "DUPLICATE")

        is_at = _is_at_schedule(schedule)
        auto_delete = bool(is_at)

        retry_policy_val = retry_policy if isinstance(retry_policy, dict) else {}
        if group:
            retry_policy_val = dict(retry_policy_val)
            retry_policy_val.setdefault("group", group)

        reflection = Reflection.create(
            name=name,
            schedule=schedule,
            execution_type=execution_type,
            command=command or "",
            output_sink=output_sink or "log_only",
            retry_policy=retry_policy_val,
            created_by_session_id=_caller_id(),
            auto_delete_after_run=auto_delete,
        )
        # Store callable on record metadata via retry_policy if function-type
        # (the registry path uses YAML; ad-hoc creation via MCP keeps it simple).
        if execution_type == "function" and callable:
            rp = dict(reflection.retry_policy or {})
            rp.setdefault("callable", callable)
            reflection.retry_policy = rp
            reflection.save()

        return {"name": name, "next_due": float(next_due)}
    except Exception as e:  # noqa: BLE001
        return _err(f"reflections_create failed: {type(e).__name__}: {e}", "INTERNAL")


@mcp.tool()
def reflections_list(group: str | None = None, status: str | None = None) -> dict:
    """List reflections, optionally filtered by group and/or last_status.

    Args:
        group: Optional group label match against ``retry_policy.group``.
        status: Optional ``last_status`` filter
            (``pending`` / ``running`` / ``success`` / ``error`` / ``skipped``).

    Returns:
        ``{"reflections": [<dict>, ...]}`` on success;
        ``{"error": str, "code": str}`` on failure.
    """
    try:
        from models.reflection import Reflection

        records = Reflection.get_all_states()
        out = []
        for r in records:
            if status and getattr(r, "last_status", None) != status:
                continue
            if group:
                rp = getattr(r, "retry_policy", {}) or {}
                if not isinstance(rp, dict) or rp.get("group") != group:
                    continue
            out.append(_reflection_to_dict(r))
        return {"reflections": out}
    except Exception as e:  # noqa: BLE001
        return _err(f"reflections_list failed: {type(e).__name__}: {e}", "INTERNAL")


@mcp.tool()
def reflections_get(name: str) -> dict:
    """Return the full Reflection record for ``name``.

    Args:
        name: The reflection name.

    Returns:
        Full record dict, or ``{"error": str, "code": str}``.
    """
    try:
        if not name or not isinstance(name, str):
            return _err("name required", "BAD_INPUT")

        from models.reflection import Reflection

        existing = Reflection.query.filter(name=name)
        if not existing:
            return _err(f"reflection not found: {name}", "NOT_FOUND")
        return _reflection_to_dict(existing[0])
    except Exception as e:  # noqa: BLE001
        return _err(f"reflections_get failed: {type(e).__name__}: {e}", "INTERNAL")


@mcp.tool()
def reflections_update(name: str, **fields) -> dict:
    """Update mutable fields on a Reflection. Caller must pass ``_can_update``.

    Allowed fields: ``schedule``, ``execution_type``, ``command``,
    ``output_sink``, ``retry_policy``, ``auto_delete_after_run``.

    If ``schedule`` is changed, it is re-validated via ``compute_next_due``.
    Setting ``auto_delete_after_run=True`` for non-``at:`` schedules is
    rejected (Q2 cycle-4: only one-shot ``at:`` schedules auto-delete).

    Args:
        name: The reflection to update.
        **fields: Field names mapped to new values.

    Returns:
        Updated record dict, or ``{"error": str, "code": str}``.
    """
    try:
        if not name or not isinstance(name, str):
            return _err("name required", "BAD_INPUT")

        from models.reflection import Reflection

        existing = Reflection.query.filter(name=name)
        if not existing:
            return _err(f"reflection not found: {name}", "NOT_FOUND")
        reflection = existing[0]

        if not _can_update(reflection):
            return _err(
                "caller is not the creator of this reflection",
                "FORBIDDEN",
            )

        allowed = {
            "schedule",
            "execution_type",
            "command",
            "output_sink",
            "retry_policy",
            "auto_delete_after_run",
        }
        unknown = set(fields) - allowed
        if unknown:
            return _err(f"cannot update fields: {sorted(unknown)}", "BAD_INPUT")

        # Validate schedule change
        new_schedule = fields.get("schedule", reflection.schedule)
        if "schedule" in fields:
            try:
                from agent.reflection_scheduler import compute_next_due

                compute_next_due(new_schedule, None, cron_tz="UTC")
            except ValueError as e:
                return _err(str(e), "BAD_SCHEDULE")
            except Exception as e:  # noqa: BLE001
                return _err(
                    f"schedule validation failed: {type(e).__name__}: {e}",
                    "BAD_SCHEDULE",
                )

        # Reject auto_delete_after_run=True for non-at: schedules (Q2 cycle-4)
        if fields.get("auto_delete_after_run") is True and not _is_at_schedule(new_schedule):
            return _err(
                "auto_delete_after_run=True is only valid for `at:` (one-shot) schedules",
                "BAD_INPUT",
            )

        for k, v in fields.items():
            setattr(reflection, k, v)
        reflection.save()
        return _reflection_to_dict(reflection)
    except Exception as e:  # noqa: BLE001
        return _err(f"reflections_update failed: {type(e).__name__}: {e}", "INTERNAL")


@mcp.tool()
def reflections_remove(name: str) -> dict:
    """Remove a Reflection. Caller must pass ``_can_remove``.

    For unidentified (CLI) callers, ``REFLECTIONS_REGISTRY_SOURCE=1`` must be
    set — only the registry sync path should be deleting reflections out-of-band.

    Args:
        name: The reflection to remove.

    Returns:
        ``{"removed": name}`` on success, or ``{"error": str, "code": str}``.
    """
    try:
        if not name or not isinstance(name, str):
            return _err("name required", "BAD_INPUT")

        from models.reflection import Reflection

        existing = Reflection.query.filter(name=name)
        if not existing:
            return _err(f"reflection not found: {name}", "NOT_FOUND")
        reflection = existing[0]

        if not _can_remove(reflection):
            return _err(
                "caller is not authorized to remove this reflection",
                "FORBIDDEN",
            )

        reflection.delete()
        return {"removed": name}
    except Exception as e:  # noqa: BLE001
        return _err(f"reflections_remove failed: {type(e).__name__}: {e}", "INTERNAL")


@mcp.tool()
def reflections_runs(name: str, limit: int = 20) -> dict:
    """Return the most-recent ReflectionRun history rows for a reflection.

    Args:
        name: The reflection name (FK into ReflectionRun).
        limit: Max rows to return (default 20, capped at 200).

    Returns:
        ``{"runs": [<dict>, ...]}`` newest-first, or ``{"error": str, "code": str}``.
    """
    try:
        if not name or not isinstance(name, str):
            return _err("name required", "BAD_INPUT")
        try:
            limit_int = max(1, min(int(limit), 200))
        except (TypeError, ValueError):
            limit_int = 20

        from models.reflection_run import ReflectionRun

        rows = list(ReflectionRun.query.filter(name=name))
        rows.sort(key=lambda r: getattr(r, "timestamp", 0.0) or 0.0, reverse=True)
        rows = rows[:limit_int]
        return {"runs": [_run_to_dict(r) for r in rows]}
    except Exception as e:  # noqa: BLE001
        return _err(f"reflections_runs failed: {type(e).__name__}: {e}", "INTERNAL")


@mcp.tool()
def reflections_pause(name: str, until: str | None = None) -> dict:
    """Pause a Reflection until ``until`` (ISO8601 UTC) or ~1 year if omitted.

    Caller must pass ``_can_update``.

    Args:
        name: The reflection to pause.
        until: Optional ISO8601 timestamp; if omitted, paused for ~1 year.

    Returns:
        ``{"name": name, "paused_until": float}`` on success, or
        ``{"error": str, "code": str}``.
    """
    try:
        if not name or not isinstance(name, str):
            return _err("name required", "BAD_INPUT")

        from models.reflection import Reflection

        existing = Reflection.query.filter(name=name)
        if not existing:
            return _err(f"reflection not found: {name}", "NOT_FOUND")
        reflection = existing[0]

        if not _can_update(reflection):
            return _err(
                "caller is not the creator of this reflection",
                "FORBIDDEN",
            )

        if until:
            try:
                # fromisoformat accepts both naive and aware ISO strings.
                dt = datetime.fromisoformat(until.replace("Z", "+00:00"))
                paused_until = dt.timestamp()
            except ValueError as e:
                return _err(f"invalid `until` ISO8601: {e}", "BAD_INPUT")
        else:
            paused_until = time.time() + 86400 * 365

        reflection.paused_until = paused_until
        reflection.save()
        return {"name": name, "paused_until": float(paused_until)}
    except Exception as e:  # noqa: BLE001
        return _err(f"reflections_pause failed: {type(e).__name__}: {e}", "INTERNAL")


@mcp.tool()
def reflections_resume(name: str) -> dict:
    """Resume a paused Reflection.

    Clears ``paused_until``, ``failure_count_consecutive``, and
    ``dead_letter_escalated`` so the scheduler picks it up on the next tick.
    Caller must pass ``_can_update``.

    Args:
        name: The reflection to resume.

    Returns:
        ``{"name": name, "resumed": True}`` on success, or
        ``{"error": str, "code": str}``.
    """
    try:
        if not name or not isinstance(name, str):
            return _err("name required", "BAD_INPUT")

        from models.reflection import Reflection

        existing = Reflection.query.filter(name=name)
        if not existing:
            return _err(f"reflection not found: {name}", "NOT_FOUND")
        reflection = existing[0]

        if not _can_update(reflection):
            return _err(
                "caller is not the creator of this reflection",
                "FORBIDDEN",
            )

        reflection.paused_until = 0.0
        reflection.failure_count_consecutive = 0
        reflection.dead_letter_escalated = False
        reflection.save()
        return {"name": name, "resumed": True}
    except Exception as e:  # noqa: BLE001
        return _err(f"reflections_resume failed: {type(e).__name__}: {e}", "INTERNAL")


def main() -> None:
    """Entry point for `python -m mcp_servers.reflections_server`."""
    if "--help" in sys.argv or "-h" in sys.argv:
        print(
            "FastMCP server: reflections\n"
            "Tools: reflections_create, reflections_list, reflections_get, "
            "reflections_update, reflections_remove, reflections_runs, "
            "reflections_pause, reflections_resume\n"
            "Transport: stdio (no args required)",
            file=sys.stderr,
        )
        return
    if os.environ.get("MCP_REFLECTIONS_DRY_RUN") == "1":
        print("reflections MCP ready", flush=True)
        return
    mcp.run()


if __name__ == "__main__":
    main()
