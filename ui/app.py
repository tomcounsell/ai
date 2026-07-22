"""FastAPI application factory for the unified web UI.

Mounts sub-routers for each dashboard, configures Jinja2 templating,
and serves static files. Binds to localhost only (127.0.0.1).

Start with: python -m ui.app
"""

import datetime
import json
import logging
import os
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from agent.constants import HEARTBEAT_STALENESS_THRESHOLD_S, WORKER_DOWN_THRESHOLD_S
from bridge.utc import utc_now

logger = logging.getLogger(__name__)

UI_DIR = Path(__file__).parent
TEMPLATES_DIR = UI_DIR / "templates"
STATIC_DIR = UI_DIR / "static"


def _filter_format_timestamp(ts: float | None) -> str:
    """Jinja2 filter: format Unix timestamp to humanized relative time."""
    if ts is None:
        return "-"
    dt = datetime.datetime.fromtimestamp(ts, tz=datetime.UTC).astimezone()
    now = utc_now().astimezone()
    diff = now - dt

    if diff.total_seconds() < 0:
        return dt.strftime("%H:%M")
    if diff.total_seconds() < 60:
        return "just now"
    if diff.total_seconds() < 3600:
        mins = int(diff.total_seconds() / 60)
        return f"{mins}m ago"
    if diff.total_seconds() < 86400 and dt.date() == now.date():
        return dt.strftime("%H:%M")
    if dt.date() == (now - datetime.timedelta(days=1)).date():
        return f"yesterday {dt.strftime('%H:%M')}"
    if diff.days < 7:
        return f"{diff.days}d ago"
    return dt.strftime("%Y-%m-%d")


def _filter_format_duration(seconds: float | None) -> str:
    """Jinja2 filter: format seconds to compact duration."""
    if seconds is None:
        return "-"
    if seconds < 1:
        return "1s"
    if seconds < 60:
        return f"{round(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds / 60)}m"
    return f"{int(seconds / 3600)}h"


def _filter_format_interval(seconds: int | None) -> str:
    """Jinja2 filter: format interval in seconds to label."""
    if not seconds:
        return "-"
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def _filter_freshness_age(ts: float | None) -> float | None:
    """Jinja2 filter: return age in seconds since ``ts``, or None when ts is None.

    Used by the row freshness chip (#1269) to compute age-since-``last_evidence_at``
    for color tier selection (green <60s, amber <600s, red >=600s) and for the
    chip label rendered via ``format_duration``. Returns None when ``ts`` is in
    the future (clock skew) so the template can drop the chip silently.
    """
    if ts is None:
        return None
    age = time.time() - float(ts)
    if age < 0:
        return None
    return age


def _filter_format_relative(seconds: float | None) -> str:
    """Jinja2 filter: format seconds as relative time."""
    if seconds is None:
        return "-"
    abs_secs = abs(seconds)
    if abs_secs < 60:
        label = f"{abs_secs:.0f}s"
    elif abs_secs < 3600:
        label = f"{abs_secs / 60:.0f}m"
    elif abs_secs < 86400:
        label = f"{abs_secs / 3600:.1f}h"
    else:
        label = f"{abs_secs / 86400:.1f}d"
    if seconds < 0:
        return f"{label} overdue"
    return f"in {label}"


def create_app() -> FastAPI:
    """Create and configure the FastAPI application.

    Returns:
        Configured FastAPI app with all routers mounted.
    """
    app = FastAPI(
        title="Valor System Dashboard",
        docs_url=None,
        redoc_url=None,
    )

    # Mount static files
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # Configure templates
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    # Register Jinja2 filters for template use
    templates.env.filters["format_timestamp"] = _filter_format_timestamp
    templates.env.filters["format_duration"] = _filter_format_duration
    templates.env.filters["format_interval_filter"] = _filter_format_interval
    templates.env.filters["format_relative"] = _filter_format_relative
    templates.env.filters["freshness_age"] = _filter_freshness_age

    # Store templates in app state for access by routers
    app.state.templates = templates

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        """Root route: single-page dashboard with all system state."""
        from config.machine import get_machine_name
        from ui.data.machine import get_machine_projects
        from ui.data.reflections import get_grouped_reflections
        from ui.data.sdlc import get_all_sessions

        sessions = get_all_sessions()
        grouped_reflections = get_grouped_reflections()
        machine_projects = get_machine_projects()
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "sessions": sessions,
                "grouped_reflections": grouped_reflections,
                "machine_name": get_machine_name(),
                "machine_projects": machine_projects,
            },
        )

    @app.get("/reflections/_partials/status-grid/", response_class=HTMLResponse)
    def partial_reflections_grid(request: Request):
        """HTMX partial: refreshable reflections status grid."""
        from ui.data.reflections import get_grouped_reflections

        grouped_reflections = get_grouped_reflections()
        return templates.TemplateResponse(
            request,
            "reflections/_partials/status_grid.html",
            {"grouped_reflections": grouped_reflections},
        )

    @app.get("/reflection/{name}/modal-content", response_class=HTMLResponse)
    def reflection_modal_content(request: Request, name: str):
        """HTMX partial: reflection detail content for modal."""
        from ui.data.reflections import get_reflection_detail, get_run_history

        r = get_reflection_detail(name)
        runs = get_run_history(name, page=1)["runs"] if r else []
        recent_runs = runs[:5]

        spark_runs = list(reversed(runs[:30]))
        durations = [
            run.get("duration") or 0 for run in spark_runs if run.get("duration") is not None
        ]
        max_dur = max(durations) if durations else 1
        sparkline = [
            {
                "color": "#ef4444" if run.get("status") == "error" else "#22c55e",
                "height_pct": max(
                    8,
                    round((run.get("duration", 0) or 0) / max_dur * 100) if max_dur else 8,
                ),
                "title": f"{run.get('status', '?')} · {(run.get('duration') or 0):.3f}s",
            }
            for run in spark_runs
        ]

        manual_command = None
        if r:
            if r.get("execution_type") == "function" and r.get("callable"):
                module, _, func = r["callable"].rpartition(".")
                if module and func:
                    manual_command = f'python -c "from {module} import {func}; {func}()"'
            elif r.get("execution_type") == "agent" and r.get("command"):
                manual_command = r["command"].strip()

        return templates.TemplateResponse(
            request,
            "reflections/_partials/modal_content.html",
            {
                "r": r,
                "recent_runs": recent_runs,
                "sparkline": sparkline,
                "manual_command": manual_command,
            },
        )

    @app.get("/_partials/analytics/", response_class=HTMLResponse)
    def partial_analytics_stats(request: Request):
        """HTMX partial: analytics stats grid."""
        from ui.data.analytics import get_analytics_summary

        analytics = get_analytics_summary()
        return templates.TemplateResponse(
            request,
            "_partials/analytics_stats.html",
            {"analytics": analytics},
        )

    # `/memories` is the per-record Memory inspector. It pairs with
    # `/_partials/memories/` for HTMX swap on filter change and 30s refresh.
    @app.get("/memories", response_class=HTMLResponse)
    def memories_page(
        request: Request,
        category: str | None = None,
        decay: bool = False,
        show_superseded: bool = False,
    ):
        """Per-record memory inspector page (read-only).

        Filter state is held in query params so HTMX can re-issue the
        partial with the same shape on swap.
        """
        from ui.data.memories import KNOWN_CATEGORIES, get_memories

        data = get_memories(
            category=category,
            decay_only=decay,
            include_superseded=show_superseded,
        )
        return templates.TemplateResponse(
            request,
            "memories.html",
            {
                "data": data,
                "filter_category": category,
                "filter_decay": decay,
                "filter_show_superseded": show_superseded,
                "known_categories": KNOWN_CATEGORIES,
            },
        )

    @app.get("/_partials/memories/", response_class=HTMLResponse)
    def partial_memories_list(
        request: Request,
        category: str | None = None,
        decay: bool = False,
        show_superseded: bool = False,
    ):
        """HTMX partial: memory list region.

        Returns the rendered records grouped by category. Same data shape
        as `memories_page`, used for HTMX swap on filter change and 30s
        refresh.
        """
        from ui.data.memories import get_memories

        data = get_memories(
            category=category,
            decay_only=decay,
            include_superseded=show_superseded,
        )
        return templates.TemplateResponse(
            request,
            "_partials/memories_list.html",
            {
                "data": data,
                "filter_category": category,
                "filter_decay": decay,
                "filter_show_superseded": show_superseded,
            },
        )

    @app.get("/memories/metrics.json")
    def memories_metrics_json(
        project_key: str | None = None,
        min_evidence: int = 2,
    ):
        """Corpus-wide memory ingest-quality metrics as JSON (read-only).

        Backs the memory-telemetry baseline (issue #2200). Always returns
        HTTP 200 with a well-formed, zero-filled body -- even on an
        empty/unavailable corpus -- because `get_corpus_metrics` never
        raises (matches the dashboard's never-crash contract downstream of
        the loader's own try/except).
        """
        from fastapi.responses import JSONResponse

        from ui.data.memories import get_corpus_metrics

        metrics = get_corpus_metrics(project_key=project_key, min_evidence=min_evidence)
        return JSONResponse(metrics)

    @app.get("/_partials/sessions/", response_class=HTMLResponse)
    def partial_sessions_table(request: Request):
        """HTMX partial: refreshable sessions table."""
        from ui.data.sdlc import get_all_sessions

        sessions = get_all_sessions()
        return templates.TemplateResponse(
            request,
            "_partials/sessions_table.html",
            {"sessions": sessions},
        )

    @app.get("/session/{agent_session_id}/modal-content", response_class=HTMLResponse)
    def session_modal_content(request: Request, agent_session_id: str):
        """HTMX partial: session detail content for modal."""
        from ui.data.sdlc import get_pipeline_detail

        pipeline = get_pipeline_detail(agent_session_id)
        return templates.TemplateResponse(
            request,
            "_partials/session_modal_content.html",
            {"pipeline": pipeline},
        )

    def _get_bridge_health() -> dict:
        """Check bridge health from last_connected file freshness."""

        last_connected_file = Path(__file__).parent.parent / "data" / "last_connected"
        try:
            if last_connected_file.exists():
                mtime = last_connected_file.stat().st_mtime
                age_s = round(time.time() - mtime)
                # Bridge writes last_connected every ~5min in heartbeat loop
                if age_s < HEARTBEAT_STALENESS_THRESHOLD_S:
                    return {"status": "ok", "age_s": age_s}
                elif age_s < WORKER_DOWN_THRESHOLD_S:
                    return {"status": "running", "age_s": age_s}
                else:
                    return {"status": "error", "age_s": age_s}
        except OSError:
            pass
        return {"status": "error", "age_s": None}

    def _get_slot_reclaims_total() -> int:
        """Sum the per-project ``slot_reclaims`` Redis counters for projects
        this machine serves (issue #1820).

        ``slot_reclaims`` is incremented by the slot-lease reap pass
        (``agent/session_health.py::_reap_slot_leases``) and by
        ``_apply_recovery_transition`` whenever a leaked concurrency slot is
        auto-reclaimed without a worker restart. Surfacing the total makes the
        self-heal operator-visible: a rising count signals a recurring leak
        worth root-causing (see docs/features/slot-lease-ownership.md).
        Fail-quiet — never blocks the health payload.
        """
        try:
            import redis as redis_lib

            from config.machine import get_machine_project_keys

            r = redis_lib.Redis.from_url(
                os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
                decode_responses=True,
            )
            total = 0
            for project_key in get_machine_project_keys():
                val = r.get(f"{project_key}:session-health:slot_reclaims")
                if val:
                    total += int(val)
            return total
        except Exception:
            return 0

    def _get_worker_slot_health() -> dict:
        """Read the Fix #5 (#1821) out-of-domain recovery surface for the dashboard.

        Additive-only fields for the ``worker`` health block: the current slot-lease
        occupancy (``permits_free``/``held`` from ``worker:slot:leases:{host}``), the
        recovery counters (``bridge_reclaims``, ``loop_wedged_detected``,
        ``bridge_contract_stale``), the Fix #6 budget counters
        (``tool_budget_tripped``, ``tool_budget_resolution_errors``), and the last few
        ``worker:watchdog:actions`` entries. Fail-quiet — never blocks the health
        payload; every field defaults to a safe zero/None on any Redis error.
        """
        result: dict = {
            "permits_free": None,
            "held": None,
            "bridge_reclaims": 0,
            "loop_wedged_detected": 0,
            "bridge_contract_stale": 0,
            "tool_budget_tripped": 0,
            "tool_budget_resolution_errors": 0,
            "recent_actions": [],
        }
        try:
            import socket

            import redis as redis_lib

            from config.machine import get_machine_project_keys

            r = redis_lib.Redis.from_url(
                os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
                decode_responses=True,
            )
            host = socket.gethostname()

            raw_leases = r.get(f"worker:slot:leases:{host}")
            if raw_leases:
                import json as _json

                leases = _json.loads(raw_leases)
                result["permits_free"] = leases.get("permits_free")
                result["held"] = leases.get("held")

            def _sum_project_counter(suffix: str) -> int:
                total = 0
                for project_key in get_machine_project_keys():
                    val = r.get(f"{project_key}:{suffix}")
                    if val:
                        total += int(val)
                return total

            result["bridge_reclaims"] = _sum_project_counter("session-health:bridge_reclaims")
            result["tool_budget_tripped"] = _sum_project_counter("tool-budget:tripped")
            result["tool_budget_resolution_errors"] = _sum_project_counter(
                "tool-budget:resolution_errors"
            )

            lw = r.get(f"{host}:worker-watchdog:loop_wedged_detected")
            if lw:
                result["loop_wedged_detected"] = int(lw)
            bcs = r.get(f"{host}:worker-watchdog:bridge_contract_stale")
            if bcs:
                result["bridge_contract_stale"] = int(bcs)

            try:
                import json as _json2

                raw_actions = r.lrange(f"worker:watchdog:actions:{host}", 0, 4)
                result["recent_actions"] = [_json2.loads(a) for a in raw_actions]
            except Exception:
                result["recent_actions"] = []
        except Exception:
            pass
        return result

    def _get_worker_health() -> dict:
        """Check worker health from last_worker_connected file freshness."""

        # TODO: migrate to _resolve_heartbeat_path if the UI ever runs from a worktree
        heartbeat_file = Path(__file__).parent.parent / "data" / "last_worker_connected"
        # Additive scalar (issue #1820) — sits beside age_s in this same dict,
        # not a new top-level key or a per-project map.
        slot_reclaims = _get_slot_reclaims_total()
        # Additive Fix #5/#6 (#1821) operator surface — merged into every return.
        slot_health = _get_worker_slot_health()
        try:
            if heartbeat_file.exists():
                mtime = heartbeat_file.stat().st_mtime
                age_s = round(time.time() - mtime)
                if age_s < HEARTBEAT_STALENESS_THRESHOLD_S:
                    status = "ok"
                elif age_s < WORKER_DOWN_THRESHOLD_S:
                    status = "running"
                else:
                    status = "error"
                return {
                    "status": status,
                    "age_s": age_s,
                    "slot_reclaims": slot_reclaims,
                    **slot_health,
                }
        except OSError:
            pass
        return {
            "status": "error",
            "age_s": None,
            "slot_reclaims": slot_reclaims,
            **slot_health,
        }

    def _get_reflection_scheduler_health() -> dict:
        """Health of the out-of-process reflection scheduler (issue #1828).

        Reads TWO data/ files written by `python -m reflections`:
          - `last_reflection_tick` (mtime) → `status` + `tick_age_s`. `status` is derived
            PURELY from tick freshness (mirroring `_get_worker_health`).
          - `reflection_worker_starts` ({count, last_start_ts}) → `restart_count`
            (informational-only; deploys inflate it) + `last_start_age_s` (the crash-loop
            indicator — persistently near-zero means launchd keeps respawning a scheduler
            that dies right after each fresh tick).

        A grace window applies to the stale threshold so a just-deployed scheduler (whose
        first tick lands a beat after boot) does not false-positive as `error`.
        Fail-quiet: any OSError degrades to status="error", never raises.
        """
        # Provisional thresholds — tune after observing real tick/restart rates on the
        # live machine. Stale threshold is ~2× the scheduler's 60s tick, plus a grace
        # window so a fresh deploy's first-tick lag is not read as a dead scheduler.
        tick_stale_threshold_s = 150
        data_dir = Path(__file__).parent.parent / "data"
        tick_file = data_dir / "last_reflection_tick"
        starts_file = data_dir / "reflection_worker_starts"

        tick_age_s: int | None = None
        status = "error"
        try:
            if tick_file.exists():
                tick_age_s = round(time.time() - tick_file.stat().st_mtime)
                status = "ok" if tick_age_s < tick_stale_threshold_s else "error"
        except OSError:
            pass

        restart_count: int | None = None
        last_start_age_s: int | None = None
        try:
            if starts_file.exists():
                data = json.loads(starts_file.read_text())
                restart_count = int(data.get("count")) if data.get("count") is not None else None
                last_start_ts = data.get("last_start_ts")
                if last_start_ts is not None:
                    last_start_age_s = round(time.time() - float(last_start_ts))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass

        return {
            "status": status,
            "tick_age_s": tick_age_s,
            "restart_count": restart_count,
            "last_start_age_s": last_start_age_s,
        }

    def _get_claude_auth_health() -> dict:
        """Check Claude Code subscription auth via `claude auth status`."""
        import subprocess

        try:
            result = subprocess.run(
                ["claude", "auth", "status"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                return {"status": "error", "logged_in": False, "auth_method": None}
            import json as _json

            data = _json.loads(result.stdout)
            logged_in = bool(data.get("loggedIn"))
            auth_method = data.get("authMethod")
            subscription_type = data.get("subscriptionType")
            return {
                "status": "ok" if logged_in else "error",
                "logged_in": logged_in,
                "auth_method": auth_method,
                "subscription_type": subscription_type,
            }
        except Exception:
            return {"status": "error", "logged_in": False, "auth_method": None}

    def _get_email_health() -> dict:
        """Check email bridge health: process liveness first, then Redis heartbeat age.

        Also surfaces two operator alert keys set by the IMAP poll loop
        (issue #1817): ``email:auth_failed`` (A3 — a permanent IMAP auth
        failure, e.g. a revoked app password) and ``email:resolver_unavailable``
        (A2 — the customer resolver has failed persistently, e.g. an expired
        OAuth token). Either alert, if armed, downgrades status to "error"
        regardless of heartbeat freshness, since a fresh poll timestamp can
        coexist with every inbound customer email failing to resolve. Reuses
        this existing health field/surface rather than inventing a new one —
        both keys are cleared by the bridge on the first successful poll/resolve
        after the outage.
        """
        import subprocess

        proc_running = bool(
            subprocess.run(
                ["pgrep", "-f", "bridge.email_bridge"],
                capture_output=True,
            ).stdout.strip()
        )

        alert: str | None = None
        alert_detail: str | None = None
        age_s: int | None = None
        try:
            import os

            import redis as redis_lib

            r = redis_lib.Redis.from_url(
                os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
                decode_responses=True,
            )
            auth_failed = r.get("email:auth_failed")
            resolver_unavailable = r.get("email:resolver_unavailable")
            if auth_failed:
                alert = "auth_failed"
                alert_detail = auth_failed
            elif resolver_unavailable:
                alert = "resolver_unavailable"
                alert_detail = resolver_unavailable

            ts = r.get("email:last_poll_ts")
            if ts:
                age_s = round(time.time() - float(ts))
        except Exception:
            pass

        # An armed alert always downgrades to "error", independent of ts/proc
        # freshness — a fresh poll timestamp can coexist with every resolve
        # call failing, and the alert itself is the loud signal here.
        if alert:
            return {"status": "error", "age_s": age_s, "alert": alert, "alert_detail": alert_detail}
        if age_s is not None:
            if not proc_running:
                return {"status": "error", "age_s": age_s, "alert": None, "alert_detail": None}
            if age_s < 120:
                return {"status": "ok", "age_s": age_s, "alert": None, "alert_detail": None}
            elif age_s < 300:
                return {"status": "running", "age_s": age_s, "alert": None, "alert_detail": None}
            else:
                return {"status": "error", "age_s": age_s, "alert": None, "alert_detail": None}
        if not proc_running:
            return {"status": "error", "age_s": None, "alert": None, "alert_detail": None}
        return {"status": "running", "age_s": None, "alert": None, "alert_detail": None}

    def _get_archive_health() -> dict:
        """Session archive (data/session_archive.db) freshness -- mirrors _get_email_health.

        Delegates entirely to `agent.session_archive.get_archive_status()`, which
        never raises (any error -- missing file, corrupt DB -- returns a
        `healthy=False` shape). This wrapper adds a coarse `status` label
        (ok/stale/missing) for the same three-state shape the other health
        blocks (bridge/worker/email) use, so dashboard/health consumers don't
        need to re-derive it from `exists`/`healthy` themselves.

        See docs/plans/session-archive-sqlite.md Task 4 (operator surfaces).
        """
        from agent.session_archive import get_archive_status

        status = get_archive_status()
        if not status["exists"]:
            label = "missing"
        elif status["healthy"]:
            label = "ok"
        else:
            label = "stale"

        return {
            "status": label,
            "healthy": status["healthy"],
            "row_count": status["row_count"],
            "last_export_age_s": status["last_export_age_s"],
            # C3: periodic-sweep age is the liveness signal `healthy` keys off --
            # surface it so a dead sweep thread is visible even while terminal
            # exports keep last_export_age_s fresh.
            "last_periodic_export_age_s": status["last_periodic_export_age_s"],
            "kind": status["kind"],
        }

    def _session_to_json(s) -> dict:
        """Serialize a PipelineProgress to JSON dict for the dashboard API."""
        result = {
            "agent_session_id": s.agent_session_id,
            "session_id": s.session_id,
            "display_name": s.display_name,
            "session_type": s.session_type,
            "status": s.status,
            "project_key": s.project_key,
            "project_name": s.project_name,
            "slug": s.slug,
            "branch_name": s.branch_name,
            "current_stage": s.current_stage,
            "stages": [{"name": st.name, "status": st.status} for st in s.stages],
            "created_at": s.created_at,
            "started_at": s.started_at,
            "completed_at": s.completed_at,
            "updated_at": s.updated_at,
            "duration": s.duration,
            "issue_url": s.issue_url,
            "pr_url": s.pr_url,
            "message_text": s.message_text,
            "parent_agent_session_id": s.parent_agent_session_id,
            "context_summary": s.context_summary,
            "expectations": s.expectations,
            "turn_count": s.turn_count,
            "tool_call_count": s.tool_call_count,
            "unhealthy_reason": s.unhealthy_reason,
            "priority": s.priority,
            "classification_type": s.classification_type,
            "is_stale": s.is_stale,
            # Per-session token + cost accounting (issue #1128). Always
            # emitted as numeric defaults (never None, never omitted).
            "total_input_tokens": s.total_input_tokens,
            "total_output_tokens": s.total_output_tokens,
            "total_cache_read_tokens": s.total_cache_read_tokens,
            "total_cost_usd": s.total_cost_usd,
            # In-flight visibility (issue #1172, Pillar A). Operators see
            # what the agent is doing right now without inferring from
            # staleness. ``last_evidence_at`` is the max of every evidence
            # timestamp (heartbeats, stdout, tool, turn, compaction).
            "current_tool_name": s.current_tool_name,
            "last_tool_use_at": s.last_tool_use_at,
            "last_turn_at": s.last_turn_at,
            "recent_thinking_excerpt": s.recent_thinking_excerpt,
            "last_evidence_at": s.last_evidence_at,
            # BYOB scheduler-layer serialization (issue #1256, Decision 2).
            # When True, the worker session-pick loop refuses to start this
            # session concurrently with another requires_real_chrome=True
            # session. Surfaced here so operators can see why a pending
            # session is being deferred.
            "requires_real_chrome": getattr(s, "requires_real_chrome", False),
            # Liveness signals (issue #1269). harness_pid is subprocess-scoped;
            # process_alive is None for terminal-status sessions (probe skipped).
            "harness_pid": s.harness_pid,
            "last_heartbeat_at": s.last_heartbeat_at,
            "last_sdk_heartbeat_at": s.last_sdk_heartbeat_at,
            "last_stdout_at": s.last_stdout_at,
            "recovery_attempts": s.recovery_attempts,
            "reprieve_count": s.reprieve_count,
            "process_alive": s.process_alive,
            # Runner exit classification + PM subprocess identity (issue
            # #1648). pm_pid is the current turn's `claude -p` pid.
            "exit_reason": s.exit_reason,
            "pm_pid": s.pm_pid,
            # Headless-runner resume scalars (#1924, Success Criterion 3).
            # getattr-defaulted so objects predating the fields never break.
            "dev_agent_id": getattr(s, "dev_agent_id", None),
            "runner_cwd": getattr(s, "runner_cwd", None),
            "claude_version": getattr(s, "claude_version", None),
            # Output routing state (issue #1647).
            "user_facing_routed": s.user_facing_routed,
            "children": [_session_to_json(c) for c in s.children],
            "events": [
                {
                    "role": e.role,
                    "text": e.text,
                    "timestamp": e.timestamp,
                }
                for e in s.events
            ],
        }
        return result

    @app.get("/dashboard.json")
    def dashboard_json():
        """Full dashboard state as JSON for programmatic consumption."""
        from fastapi.responses import JSONResponse

        from agent.redis_offload import (
            get_last_redis_latency,
            get_redis_latency_max,
            get_redis_latency_p95,
        )
        from config.machine import get_machine_name
        from ui.data.analytics import get_analytics_summary
        from ui.data.machine import get_machine_projects
        from ui.data.reflections import get_all_reflections
        from ui.data.sdlc import get_all_sessions

        bridge = _get_bridge_health()
        worker = _get_worker_health()
        reflection_scheduler = _get_reflection_scheduler_health()
        email = _get_email_health()
        claude_auth = _get_claude_auth_health()
        archive = _get_archive_health()
        sessions = get_all_sessions()
        reflections = get_all_reflections()
        analytics = get_analytics_summary()

        return JSONResponse(
            {
                "health": {
                    "webserver": "ok",
                    "bridge": bridge["status"],
                    "bridge_last_seen_s": bridge["age_s"],
                    "worker": worker["status"],
                    "worker_last_seen_s": worker["age_s"],
                    # Additive-only (issue #1820): count of concurrency slots
                    # auto-reclaimed from leaked leases without a worker restart.
                    "worker_slot_reclaims": worker["slot_reclaims"],
                    # Additive-only (Fix #5/#6, #1821): out-of-domain recovery +
                    # tool-budget operator surface.
                    "worker_permits_free": worker.get("permits_free"),
                    "worker_slots_held": worker.get("held"),
                    "worker_bridge_reclaims": worker.get("bridge_reclaims"),
                    "worker_loop_wedged_detected": worker.get("loop_wedged_detected"),
                    "worker_bridge_contract_stale": worker.get("bridge_contract_stale"),
                    "worker_tool_budget_tripped": worker.get("tool_budget_tripped"),
                    "worker_tool_budget_resolution_errors": worker.get(
                        "tool_budget_resolution_errors"
                    ),
                    "worker_recent_actions": worker.get("recent_actions"),
                    # Additive-only (issue #1828): out-of-process reflection scheduler.
                    # status is tick-freshness-derived; last_start_age_s near-zero is the
                    # crash-loop indicator; restart_count is informational-only.
                    "reflection_scheduler_status": reflection_scheduler["status"],
                    "reflection_scheduler_tick_age_s": reflection_scheduler["tick_age_s"],
                    "reflection_scheduler_restart_count": reflection_scheduler["restart_count"],
                    "reflection_scheduler_last_start_age_s": reflection_scheduler[
                        "last_start_age_s"
                    ],
                    "email": email["status"],
                    "email_last_seen_s": email["age_s"],
                    "email_alert": email.get("alert"),
                    "email_alert_detail": email.get("alert_detail"),
                    "claude_auth": claude_auth["status"],
                    "claude_auth_logged_in": claude_auth["logged_in"],
                    "claude_auth_method": claude_auth["auth_method"],
                    "claude_auth_subscription_type": claude_auth.get("subscription_type"),
                    "redis_offload": {
                        "label": "drain-loop idle-check latency",
                        "p95_latency_s": get_redis_latency_p95(),
                        "max_latency_s": get_redis_latency_max(),
                        "last_latency_s": get_last_redis_latency(),
                    },
                    # Additive-only (issue #1825): AgentSession SQLite secondary
                    # store freshness -- see docs/plans/session-archive-sqlite.md.
                    "archive": archive,
                },
                "sessions": [_session_to_json(s) for s in sessions],
                "reflections": reflections,
                "machine": {
                    "name": get_machine_name(),
                    "projects": get_machine_projects(),
                },
                "analytics": analytics,
            }
        )

    @app.get("/health")
    def health_status():
        """Health JSON endpoint for programmatic access."""
        from fastapi.responses import JSONResponse

        bridge = _get_bridge_health()
        worker = _get_worker_health()
        reflection_scheduler = _get_reflection_scheduler_health()
        email = _get_email_health()
        claude_auth = _get_claude_auth_health()
        archive = _get_archive_health()
        return JSONResponse(
            {
                "webserver": "ok",
                "bridge": bridge["status"],
                "bridge_last_seen_s": bridge["age_s"],
                "worker": worker["status"],
                "worker_last_seen_s": worker["age_s"],
                "worker_slot_reclaims": worker["slot_reclaims"],
                # Additive-only (Fix #5/#6, #1821).
                "worker_permits_free": worker.get("permits_free"),
                "worker_slots_held": worker.get("held"),
                "worker_bridge_reclaims": worker.get("bridge_reclaims"),
                "worker_loop_wedged_detected": worker.get("loop_wedged_detected"),
                "worker_bridge_contract_stale": worker.get("bridge_contract_stale"),
                "worker_tool_budget_tripped": worker.get("tool_budget_tripped"),
                "worker_tool_budget_resolution_errors": worker.get("tool_budget_resolution_errors"),
                # Additive-only (issue #1828): out-of-process reflection scheduler.
                "reflection_scheduler": reflection_scheduler["status"],
                "reflection_scheduler_tick_age_s": reflection_scheduler["tick_age_s"],
                "reflection_scheduler_restart_count": reflection_scheduler["restart_count"],
                "reflection_scheduler_last_start_age_s": reflection_scheduler["last_start_age_s"],
                "email": email["status"],
                "email_last_seen_s": email["age_s"],
                "email_alert": email.get("alert"),
                "email_alert_detail": email.get("alert_detail"),
                "claude_auth": claude_auth["status"],
                "claude_auth_logged_in": claude_auth["logged_in"],
                "claude_auth_method": claude_auth["auth_method"],
                "claude_auth_subscription_type": claude_auth.get("subscription_type"),
                # Additive-only (issue #1825): AgentSession SQLite secondary
                # store freshness -- see docs/plans/session-archive-sqlite.md.
                "archive": archive["status"],
                "archive_healthy": archive["healthy"],
                "archive_row_count": archive["row_count"],
                "archive_last_export_age_s": archive["last_export_age_s"],
            }
        )

    def _format_uptime(age_s: int | None) -> str:
        """Format heartbeat age as uptime string (e.g. '12m', '3h')."""
        if age_s is None:
            return ""
        if age_s < 60:
            return f" {age_s}s"
        minutes = age_s // 60
        if minutes < 60:
            return f" {minutes}m"
        hours = minutes // 60
        return f" {hours}h"

    @app.get("/_partials/health/", response_class=HTMLResponse)
    def partial_health(request: Request):
        """HTMX partial: health indicator badges."""
        bridge = _get_bridge_health()
        if bridge["status"] in ("ok", "running"):
            bridge_label = f"Telegram{_format_uptime(bridge['age_s'])}"
        else:
            bridge_label = "Telegram"

        worker = _get_worker_health()
        if worker["status"] in ("ok", "running"):
            worker_label = f"worker{_format_uptime(worker['age_s'])}"
        else:
            worker_label = "worker"

        # Reflection scheduler (out-of-process, issue #1828). A fresh tick with a
        # near-zero last_start_age_s reads as a crash loop — surface it in the label.
        reflection = _get_reflection_scheduler_health()
        if reflection["status"] in ("ok", "running"):
            reflection_label = f"reflections{_format_uptime(reflection['tick_age_s'])}"
        else:
            reflection_label = "reflections"

        email = _get_email_health()
        if email["status"] in ("ok", "running"):
            email_label = f"email{_format_uptime(email['age_s'])}"
        else:
            email_label = "email"

        claude_auth = _get_claude_auth_health()
        if claude_auth["status"] == "ok":
            method = claude_auth.get("auth_method") or "claude"
            claude_label = f"claude ({method})"
        else:
            claude_label = "claude (auth error)"

        return HTMLResponse(
            f'<span class="health-label">Bridges</span>'
            f'<span class="badge badge-{bridge["status"]}">{bridge_label}</span>'
            f'<span class="badge badge-{email["status"]}">{email_label}</span>'
            f'<span class="health-label">Services</span>'
            f'<span class="badge badge-{worker["status"]}">{worker_label}</span>'
            f'<span class="badge badge-{reflection["status"]}">{reflection_label}</span>'
            f'<span class="badge badge-ok">web</span>'
            f'<span class="health-label">Auth</span>'
            f'<span class="badge badge-{claude_auth["status"]}">{claude_label}</span>'
        )

    # Exception handler for Redis connection failures
    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception):
        """Render a user-friendly error page instead of a 500 traceback."""
        return templates.TemplateResponse(
            request, "error.html", {"error": str(exc)}, status_code=500
        )

    # Startup probe: log session count for index staleness detection
    @app.on_event("startup")
    def _log_session_count():
        try:
            from models.agent_session import AgentSession

            count = len(AgentSession.query.all())
            logger.info(f"Dashboard startup: {count} AgentSession records found in Redis")
            if count == 0:
                logger.warning(
                    "Dashboard startup: zero sessions found. "
                    "Popoto indexes may be stale after restart."
                )
        except Exception as e:
            logger.warning(f"Dashboard startup: failed to query sessions: {e}")

    return app


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("UI_PORT", "8500"))
    uvicorn.run(
        "ui.app:create_app",
        factory=True,
        host="127.0.0.1",
        port=port,
        reload=False,
    )
