"""Unified health check CLI for local development environments.

Consolidates checks from monitoring/health.py, scripts/update/verify.py,
and monitoring/resource_monitor.py into a single diagnostic command.

Note: Importing scripts/update/verify.py modifies os.environ["PATH"] to
include pyenv, homebrew, and other tool locations. This is intentional --
it ensures the doctor can find the same tools the update system uses.

Usage:
    python -m tools.doctor           # Run all standard checks
    python -m tools.doctor --quick   # Skip slow checks
    python -m tools.doctor --quality # Include ruff/pytest checks
    python -m tools.doctor --json    # Machine-readable output
    python -m tools.doctor --install-hook  # Install git pre-push hook
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from config.settings import settings

# Project root (ai/)
PROJECT_DIR = Path(__file__).resolve().parent.parent

# Load .env so health checks that read os.environ find the API keys.
try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_DIR / ".env")
except Exception:  # noqa: S110 -- dotenv optional; env may be preset
    pass


@dataclass
class CheckResult:
    """Result of a single health check."""

    name: str
    category: str
    passed: bool
    message: str
    fix: str | None = None

    def to_dict(self) -> dict:
        """Serialize for JSON output."""
        d = {
            "name": self.name,
            "category": self.category,
            "passed": self.passed,
            "message": self.message,
        }
        if self.fix:
            d["fix"] = self.fix
        return d


# ---------------------------------------------------------------------------
# Check wrappers
# ---------------------------------------------------------------------------


def _check_python_version() -> CheckResult:
    """Check Python version is 3.12+."""
    import platform

    version = platform.python_version()
    major, minor = sys.version_info[:2]
    ok = major == 3 and minor >= 12
    return CheckResult(
        name="python_version",
        category="Environment",
        passed=ok,
        message=f"Python {version}",
        fix=None if ok else "Install Python 3.12+: brew install python@3.12",
    )


def _check_venv() -> CheckResult:
    """Check that a virtualenv exists."""
    venv_python = PROJECT_DIR / ".venv" / "bin" / "python"
    ok = venv_python.exists()
    return CheckResult(
        name="virtualenv",
        category="Environment",
        passed=ok,
        message="virtualenv found" if ok else "No .venv/bin/python",
        fix=None if ok else "Run: uv venv && uv pip install -r requirements.txt",
    )


def _check_system_tools() -> list[CheckResult]:
    """Check system-level tools via verify.py."""
    results = []
    try:
        from scripts.update.verify import check_system_tools

        for tc in check_system_tools():
            results.append(
                CheckResult(
                    name=tc.name,
                    category="Environment",
                    passed=tc.available,
                    message=tc.version or ("available" if tc.available else "missing"),
                    fix=tc.error if not tc.available else None,
                )
            )
    except Exception as e:
        results.append(
            CheckResult(
                name="system_tools",
                category="Environment",
                passed=False,
                message=f"Could not check system tools: {e}",
            )
        )
    return results


def _check_python_deps() -> list[CheckResult]:
    """Check core Python dependencies via verify.py."""
    results = []
    try:
        from scripts.update.verify import check_python_deps

        for tc in check_python_deps(PROJECT_DIR):
            results.append(
                CheckResult(
                    name=f"dep:{tc.name}",
                    category="Environment",
                    passed=tc.available,
                    message="installed" if tc.available else "missing",
                    fix=tc.error if not tc.available else None,
                )
            )
    except Exception as e:
        results.append(
            CheckResult(
                name="python_deps",
                category="Environment",
                passed=False,
                message=f"Could not check deps: {e}",
            )
        )
    return results


def _check_redis() -> CheckResult:
    """Check Redis connectivity via HealthChecker."""
    try:
        from monitoring.health import HealthChecker, HealthStatus

        hc = HealthChecker()
        result = hc.check_database()
        passed = result.status == HealthStatus.HEALTHY
        return CheckResult(
            name="redis",
            category="Services",
            passed=passed,
            message=result.message,
            fix=None if passed else "Start Redis: brew services start redis",
        )
    except Exception as e:
        return CheckResult(
            name="redis",
            category="Services",
            passed=False,
            message=f"Redis check failed: {e}",
            fix="Start Redis: brew services start redis",
        )


def _check_redis_durability() -> list[CheckResult]:
    """Check Redis durability configuration: AOF and eviction policy.

    Asserts:
    - ``aof_enabled:1`` via ``redis-cli INFO persistence``
    - ``maxmemory-policy == noeviction`` via ``redis-cli CONFIG GET maxmemory-policy``

    Returns a list of two CheckResult objects (one per assertion) so each
    failure is independently actionable.  Never raises — renders failure state
    cleanly if Redis is unreachable or redis-cli is not installed.
    """
    results: list[CheckResult] = []

    # --- AOF enabled ---
    try:
        proc = subprocess.run(
            ["redis-cli", "INFO", "persistence"],
            capture_output=True,
            text=True,
            timeout=settings.timeouts.subprocess_default_s,
        )
        output = proc.stdout
        aof_enabled = "aof_enabled:1" in output
        results.append(
            CheckResult(
                name="redis_aof",
                category="Services",
                passed=aof_enabled,
                message="AOF persistence enabled"
                if aof_enabled
                else "AOF persistence disabled (aof_enabled:0)",
                fix=None
                if aof_enabled
                else "Enable AOF: run /update which sets appendonly yes in redis.conf",
            )
        )
    except FileNotFoundError:
        results.append(
            CheckResult(
                name="redis_aof",
                category="Services",
                passed=False,
                message="redis-cli not found — cannot check AOF status",
                fix="Install Redis CLI: brew install redis",
            )
        )
    except Exception as e:
        results.append(
            CheckResult(
                name="redis_aof",
                category="Services",
                passed=False,
                message=f"AOF check failed: {e}",
                fix="Run /update to apply Redis durability configuration",
            )
        )

    # --- maxmemory-policy noeviction ---
    try:
        proc = subprocess.run(
            ["redis-cli", "CONFIG", "GET", "maxmemory-policy"],
            capture_output=True,
            text=True,
            timeout=settings.timeouts.subprocess_default_s,
        )
        lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
        # redis-cli CONFIG GET returns two lines: key then value
        policy = lines[1] if len(lines) >= 2 else ""
        noeviction = policy == "noeviction"
        results.append(
            CheckResult(
                name="redis_eviction_policy",
                category="Services",
                passed=noeviction,
                message=f"maxmemory-policy={policy or '(unknown)'}"
                + (" (correct)" if noeviction else " (should be noeviction)"),
                fix=None
                if noeviction
                else "Set noeviction: run /update which configures maxmemory-policy noeviction",
            )
        )
    except FileNotFoundError:
        results.append(
            CheckResult(
                name="redis_eviction_policy",
                category="Services",
                passed=False,
                message="redis-cli not found — cannot check maxmemory-policy",
                fix="Install Redis CLI: brew install redis",
            )
        )
    except Exception as e:
        results.append(
            CheckResult(
                name="redis_eviction_policy",
                category="Services",
                passed=False,
                message=f"Eviction policy check failed: {e}",
                fix="Run /update to apply Redis durability configuration",
            )
        )

    return results


# Role-gate marker: a host opts in to being a Redis node by touching this file.
# Mirrors scripts/update/redis_replication.py::REPLICATION_MARKER_FILE.
_REPLICATION_MARKER_FILE = PROJECT_DIR / "data" / "redis-replication-enabled"

# Provisional Sentinel master name / port — tunable, override via env if deployed.
_SENTINEL_MASTER_NAME = os.environ.get("REDIS_SENTINEL_MASTER_NAME", "valor-redis")
_SENTINEL_PORT = os.environ.get("REDIS_SENTINEL_PORT", "26379")


def _check_redis_replication_health() -> CheckResult:
    """Check Redis replication + Sentinel health on opted-in Redis nodes (#1827).

    ROLE-GATED: on a client-only machine (no ``data/redis-replication-enabled``
    marker — the common case) this returns a neutral ``passed=True`` SKIP and never
    warns. Replication absence on a standalone localhost Redis is the expected
    default posture, NOT a failure.

    On an opted-in node it asserts ``role`` via ``redis-cli INFO replication``
    (``master_link_status:up`` for a replica, ``connected_slaves`` for a master) and
    probes Sentinel reachability. Degrades gracefully (``passed=True`` "(skipped)")
    when redis-cli is absent or Redis is unreachable. Never raises.
    """
    name = "redis_replication_health"
    category = "Services"

    # ROLE GATE — neutral skip on client-only machines.
    try:
        opted_in = _REPLICATION_MARKER_FILE.exists()
    except OSError:
        opted_in = False
    if not opted_in:
        return CheckResult(
            name=name,
            category=category,
            passed=True,
            message="redis replication: client-only machine (skipped)",
        )

    # Opted-in node: probe INFO replication.
    try:
        proc = subprocess.run(
            ["redis-cli", "INFO", "replication"],
            capture_output=True,
            text=True,
            timeout=settings.timeouts.subprocess_default_s,
        )
    except FileNotFoundError:
        return CheckResult(
            name=name,
            category=category,
            passed=True,
            message="redis replication: redis-cli not found (skipped)",
        )
    except Exception as e:
        return CheckResult(
            name=name,
            category=category,
            passed=True,
            message=f"redis replication: probe failed, degraded (skipped): {e}",
        )

    if proc.returncode != 0:
        return CheckResult(
            name=name,
            category=category,
            passed=True,
            message="redis replication: Redis unreachable (skipped)",
        )

    role: str | None = None
    master_link: str | None = None
    connected_slaves = 0
    for raw in proc.stdout.splitlines():
        line = raw.strip()
        if line.startswith("role:"):
            role = line.split(":", 1)[1].strip()
        elif line.startswith("master_link_status:"):
            master_link = line.split(":", 1)[1].strip()
        elif line.startswith("connected_slaves:"):
            try:
                connected_slaves = int(line.split(":", 1)[1].strip())
            except ValueError:
                connected_slaves = 0

    # Sentinel reachability probe (best-effort, non-fatal).
    sentinel_ok = False
    try:
        sproc = subprocess.run(
            ["redis-cli", "-p", _SENTINEL_PORT, "SENTINEL", "master", _SENTINEL_MASTER_NAME],
            capture_output=True,
            text=True,
            timeout=settings.timeouts.subprocess_default_s,
        )
        sentinel_ok = sproc.returncode == 0 and bool(sproc.stdout.strip())
    except Exception:
        sentinel_ok = False

    sentinel_note = "Sentinel reachable" if sentinel_ok else "Sentinel unreachable"
    runbook = "See failover runbook in docs/features/redis-durability.md"

    if role == "slave":
        healthy = master_link == "up"
        return CheckResult(
            name=name,
            category=category,
            passed=healthy,
            message=f"redis replication: replica, master_link_status={master_link or '(unknown)'}"
            f"; {sentinel_note}",
            fix=None if healthy else f"Replica not linked to master. {runbook}",
        )

    if role == "master":
        healthy = connected_slaves >= 1
        return CheckResult(
            name=name,
            category=category,
            passed=healthy,
            message=f"redis replication: master, connected_slaves={connected_slaves}"
            f"; {sentinel_note}",
            fix=None if healthy else f"Master has no connected replicas. {runbook}",
        )

    return CheckResult(
        name=name,
        category=category,
        passed=True,
        message=f"redis replication: role={role or '(unknown)'} (skipped); {sentinel_note}",
    )


def _check_session_archive_freshness() -> CheckResult:
    """Check the AgentSession SQLite secondary store (data/session_archive.db).

    See docs/plans/session-archive-sqlite.md and docs/features/redis-durability.md
    Fix #2: the worker periodically exports every ``AgentSession`` to a local
    SQLite file (plus an immediate export on every terminal-status transition),
    so a total Redis data-dir loss (FLUSHALL, disk failure, ``rm -rf``) is
    recoverable. This check reads the read-only ``get_archive_status()``
    summary -- it never writes and never raises -- and flags a missing or
    stale archive as an actionable failure so an operator notices the second
    copy is absent before a Redis loss makes that fact matter.
    """
    name = "session-archive-freshness"
    category = "Services"
    try:
        from agent.constants import SESSION_ARCHIVE_FRESHNESS_THRESHOLD_S
        from agent.session_archive import get_archive_status

        status = get_archive_status()
    except Exception as e:
        return CheckResult(
            name=name,
            category=category,
            passed=False,
            message=f"Could not check session archive: {e}",
        )

    if not status["exists"]:
        return CheckResult(
            name=name,
            category=category,
            passed=False,
            message="Session archive (data/session_archive.db) does not exist yet",
            fix="Start the worker (./scripts/valor-service.sh worker-start) -- the "
            "periodic export thread and terminal-status hook create it automatically",
        )

    row_count = status["row_count"]
    # Health keys off the periodic sweep age (C3) -- a dead sweep thread must
    # surface even while terminal exports keep last_export_age_s fresh. Fall
    # back to the terminal age only before the first sweep has run.
    periodic_age_s = status["last_periodic_export_age_s"]
    health_age_s = periodic_age_s if periodic_age_s is not None else status["last_export_age_s"]

    if not status["healthy"]:
        age_desc = "never" if health_age_s is None else f"{health_age_s:.0f}s ago"
        return CheckResult(
            name=name,
            category=category,
            passed=False,
            message=f"Session archive stale: last periodic sweep {age_desc} "
            f"(threshold {SESSION_ARCHIVE_FRESHNESS_THRESHOLD_S}s), row_count={row_count}",
            fix="Check the worker is running and its 'worker-session-archive' daemon "
            "thread hasn't crashed: ./scripts/valor-service.sh worker-status; "
            "tail -f logs/worker.log",
        )

    return CheckResult(
        name=name,
        category=category,
        passed=True,
        message=f"Session archive fresh: last periodic sweep {health_age_s:.0f}s ago, "
        f"row_count={row_count}",
    )


def _check_agentsession_index_drift() -> CheckResult:
    """Check for AgentSession index drift (#2086).

    Compares a raw bounded-SCAN count of `AgentSession:<key>` hashes against
    `len(AgentSession.query.all())`. On 2026-07-14 those two numbers silently
    diverged (11 hashes, 0 queryable) with no exception and no signal on any
    observability surface. This is a read-only diagnostic -- it never calls
    `repair_indexes()` (detect-only; see `agent/index_drift.py`).
    """
    name = "agentsession-index-drift"
    category = "Services"
    from agent.index_drift import reconcile_agent_session_index

    hash_count, queryable_count, drifted, truncated = reconcile_agent_session_index()

    if truncated:
        return CheckResult(
            name=name,
            category=category,
            passed=False,
            message="AgentSession index-drift scan incomplete (hit the bounded-SCAN "
            "iteration cap) -- hash count is a partial undercount, drift not determined",
            fix="Investigate a possibly huge/corrupt AgentSession keyspace; "
            "re-run `python -m tools.doctor` once Redis is healthy",
        )

    if drifted:
        return CheckResult(
            name=name,
            category=category,
            passed=False,
            message=f"AgentSession index drift: {hash_count} hashes, "
            f"{queryable_count} queryable -- index desync",
            fix="Hashes exist that AgentSession.query.all() cannot see. Investigate "
            "via `valor-session inspect`, then run repair_indexes() to rebuild "
            "the index (see docs/features/agentsession-index-drift-detection.md)",
        )

    return CheckResult(
        name=name,
        category=category,
        passed=True,
        message=f"AgentSession index consistent: {hash_count} hashes, {queryable_count} queryable",
    )


def _check_knowledge_zero_chunk_documents() -> CheckResult:
    """Check for KnowledgeDocuments with content but zero DocumentChunk rows (#2085).

    Regression guard for the popoto content-filename overflow bug: a long
    vault ``file_path`` could overflow the (pre-fix) filesystem content
    store's filename budget and silently drop every chunk for a document
    while the parent KnowledgeDocument row itself saved fine. This is the
    exact symptom that bug produces.

    Internally bounded: runs in the unconditional (non-``--quality``) check
    list, so it must not walk an unbounded KnowledgeDocument set on
    ``--quick``. Samples at most the first 500 documents via the ORM
    (never raw Redis) and reports the sampled/flagged counts rather than a
    full-corpus scan.
    """
    name = "knowledge-zero-chunk-documents"
    category = "Services"
    sample_limit = 500

    try:
        from models.document_chunk import DocumentChunk
        from models.knowledge_document import KnowledgeDocument

        docs = KnowledgeDocument.query.all()
        sampled = docs[:sample_limit]

        zero_chunk_docs = []
        for doc in sampled:
            if not (doc.content and doc.content.strip()):
                continue
            if not DocumentChunk.query.filter(document_doc_id=doc.doc_id):
                zero_chunk_docs.append(doc.doc_id)

        if zero_chunk_docs:
            return CheckResult(
                name=name,
                category=category,
                passed=False,
                message=(
                    f"{len(zero_chunk_docs)}/{len(sampled)} sampled KnowledgeDocuments "
                    "have content but zero chunks"
                ),
                fix=(
                    'Run `python -c "from tools.knowledge.indexer import '
                    'rechunk_zero_chunk_documents; print(rechunk_zero_chunk_documents())"` '
                    "to re-derive chunks from stored content"
                ),
            )

        return CheckResult(
            name=name,
            category=category,
            passed=True,
            message=f"No zero-chunk KnowledgeDocuments found (sampled {len(sampled)})",
        )
    except Exception as e:
        return CheckResult(
            name=name,
            category=category,
            passed=False,
            message=f"Could not check zero-chunk documents: {e}",
        )


def _check_bridge() -> CheckResult:
    """Check if Telegram bridge is running."""
    try:
        from scripts.update.service import is_bridge_running

        running = is_bridge_running()
        return CheckResult(
            name="bridge",
            category="Services",
            passed=running,
            message="running" if running else "not running",
            fix=None if running else "Start bridge: ./scripts/valor-service.sh restart",
        )
    except Exception as e:
        return CheckResult(
            name="bridge",
            category="Services",
            passed=False,
            message=f"Could not check bridge: {e}",
        )


def _check_worker() -> CheckResult:
    """Check if standalone worker is running."""
    try:
        from scripts.update.service import is_worker_running

        running = is_worker_running()
        return CheckResult(
            name="worker",
            category="Services",
            passed=running,
            message="running" if running else "not running",
            fix=None if running else "Start worker: ./scripts/valor-service.sh worker-start",
        )
    except Exception as e:
        return CheckResult(
            name="worker",
            category="Services",
            passed=False,
            message=f"Could not check worker: {e}",
        )


def _check_telegram_session(*, quick: bool = False) -> CheckResult:
    """Check Telegram session auth."""
    if quick:
        # In quick mode, just check that session file exists
        data_dir = PROJECT_DIR / "data"
        session_files = list(data_dir.glob("*.session"))
        ok = len(session_files) > 0
        return CheckResult(
            name="telegram_session",
            category="Auth",
            passed=ok,
            message=f"{len(session_files)} session file(s) found" if ok else "No session files",
            fix=None if ok else "Run: python scripts/telegram_login.py",
        )

    try:
        from scripts.update.verify import check_telegram_session

        tc = check_telegram_session(PROJECT_DIR)
        return CheckResult(
            name="telegram_session",
            category="Auth",
            passed=tc.available,
            message=tc.version or ("authorized" if tc.available else "unauthorized"),
            fix=tc.error if not tc.available else None,
        )
    except Exception as e:
        return CheckResult(
            name="telegram_session",
            category="Auth",
            passed=False,
            message=f"Could not check session: {e}",
            fix="Run: python scripts/telegram_login.py",
        )


def _check_api_keys() -> list[CheckResult]:
    """Check API keys via HealthChecker."""
    results = []
    try:
        from monitoring.health import HealthChecker, HealthStatus

        hc = HealthChecker()
        api_results = hc.check_api_keys()
        for name, hcr in api_results.items():
            passed = hcr.status == HealthStatus.HEALTHY
            results.append(
                CheckResult(
                    name=f"api_key:{name}",
                    category="Auth",
                    passed=passed,
                    message=hcr.message,
                    fix=None if passed else f"Set {hcr.details.get('env_var', name)} in .env",
                )
            )
    except Exception as e:
        results.append(
            CheckResult(
                name="api_keys",
                category="Auth",
                passed=False,
                message=f"Could not check API keys: {e}",
            )
        )
    return results


def _check_claude_oauth_token() -> CheckResult:
    """Check CLAUDE_CODE_OAUTH_TOKEN presence and prefix.

    This check is presence+prefix only — no expiry computation, JWT decode,
    minted-date heuristics, or N-day warnings. The token format may change; only
    the ``sk-ant-oat01-`` prefix is validated here.

    Reads bare ``os.environ`` (not a settings field) because the token is injected
    into the PTY child environment at session launch time, not stored in settings.
    There is no settings.py field for this token by design.

    Remediation: if the token is absent or malformed, run
    ``claude setup-token`` on a browser-capable machine to mint a fresh token,
    then set ``CLAUDE_CODE_OAUTH_TOKEN`` in the .env vault.

    Returns a *warning* (``passed=True`` with a ``fix`` message) for absent or
    malformed tokens — it never hard-fails the run, because the token is optional
    on non-interactive machines that use API-key auth instead.
    """
    import logging
    import os

    expected_prefix = "sk-ant-oat01-"

    try:
        token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
        if token is None:
            return CheckResult(
                name="claude_oauth_token",
                category="Auth",
                passed=True,  # warning only
                message="CLAUDE_CODE_OAUTH_TOKEN not set (optional on API-key machines)",
                fix=(
                    "Run: claude setup-token on a browser-capable machine,"
                    " then add CLAUDE_CODE_OAUTH_TOKEN to .env"
                ),
            )
        if not token.startswith(expected_prefix):
            return CheckResult(
                name="claude_oauth_token",
                category="Auth",
                passed=True,  # warning only
                message=(
                    f"CLAUDE_CODE_OAUTH_TOKEN present but malformed prefix"
                    f" (expected {expected_prefix!r})"
                ),
                fix=(
                    f"Token prefix does not match {expected_prefix!r}."
                    " Run: claude setup-token on a browser-capable machine to mint a fresh token"
                ),
            )
        return CheckResult(
            name="claude_oauth_token",
            category="Auth",
            passed=True,
            message="CLAUDE_CODE_OAUTH_TOKEN present and valid prefix",
        )
    except Exception as e:
        logging.warning("claude_oauth_token check raised: %s", e)
        return CheckResult(
            name="claude_oauth_token",
            category="Auth",
            passed=True,  # warning only — don't block the run
            message=f"CLAUDE_CODE_OAUTH_TOKEN check failed unexpectedly: {e}",
            fix="Run: claude setup-token on a browser-capable machine",
        )


def _check_sdk_auth() -> CheckResult:
    """Check SDK authentication status."""
    try:
        from scripts.update.verify import check_sdk_auth

        auth = check_sdk_auth(PROJECT_DIR)
        api_key_ok = auth.get("api_key_configured", False)
        return CheckResult(
            name="sdk_auth",
            category="Auth",
            passed=api_key_ok,
            message="API key configured" if api_key_ok else "API key not configured",
            fix=None if api_key_ok else "Add ANTHROPIC_API_KEY=sk-ant-... to .env",
        )
    except Exception as e:
        return CheckResult(
            name="sdk_auth",
            category="Auth",
            passed=False,
            message=f"Could not check SDK auth: {e}",
        )


def _check_disk_space() -> CheckResult:
    """Check disk space via HealthChecker."""
    try:
        from monitoring.health import HealthChecker, HealthStatus

        hc = HealthChecker()
        result = hc.check_disk_space()
        passed = result.status == HealthStatus.HEALTHY
        return CheckResult(
            name="disk_space",
            category="Resources",
            passed=passed,
            message=result.message,
            fix=None if passed else "Free up disk space",
        )
    except Exception as e:
        return CheckResult(
            name="disk_space",
            category="Resources",
            passed=False,
            message=f"Could not check disk: {e}",
        )


def _check_memory() -> CheckResult:
    """Check memory usage via resource monitor."""
    try:
        from monitoring.resource_monitor import PSUTIL_AVAILABLE, ResourceSnapshot

        if not PSUTIL_AVAILABLE:
            return CheckResult(
                name="memory",
                category="Resources",
                passed=True,
                message="psutil not installed (skipped)",
            )

        snap = ResourceSnapshot.capture()
        ok = snap.memory_mb < 800  # Critical threshold from CLAUDE.md
        return CheckResult(
            name="memory",
            category="Resources",
            passed=ok,
            message=f"Process memory: {snap.memory_mb:.0f}MB",
            fix=None if ok else "High memory usage detected. Restart services.",
        )
    except Exception as e:
        return CheckResult(
            name="memory",
            category="Resources",
            passed=True,
            message=f"Could not check memory: {e} (non-critical)",
        )


def _check_cpu() -> CheckResult:
    """Check CPU usage."""
    try:
        from monitoring.resource_monitor import PSUTIL_AVAILABLE, ResourceSnapshot

        if not PSUTIL_AVAILABLE:
            return CheckResult(
                name="cpu",
                category="Resources",
                passed=True,
                message="psutil not installed (skipped)",
            )

        snap = ResourceSnapshot.capture()
        ok = snap.cpu_percent < 95  # Critical threshold from CLAUDE.md
        return CheckResult(
            name="cpu",
            category="Resources",
            passed=ok,
            message=f"CPU: {snap.cpu_percent:.1f}%",
            fix=None if ok else "CPU critically high. Check for runaway processes.",
        )
    except Exception as e:
        return CheckResult(
            name="cpu",
            category="Resources",
            passed=True,
            message=f"Could not check CPU: {e} (non-critical)",
        )


def _check_ruff_lint() -> CheckResult:
    """Run ruff check (quality gate)."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "check", "."],
            cwd=PROJECT_DIR,
            capture_output=True,
            text=True,
            timeout=settings.timeouts.subprocess_default_s,
        )
        ok = result.returncode == 0
        if ok:
            msg = "No lint issues"
        else:
            lines = result.stdout.strip().splitlines()
            count = len([line for line in lines if line and not line.startswith("Found")])
            msg = f"{count} lint issue(s)"
        return CheckResult(
            name="ruff_lint",
            category="Quality",
            passed=ok,
            message=msg,
            fix=None if ok else "Run: python -m ruff check . --fix",
        )
    except Exception as e:
        return CheckResult(
            name="ruff_lint",
            category="Quality",
            passed=False,
            message=f"ruff check failed: {e}",
        )


def _check_ruff_format() -> CheckResult:
    """Run ruff format --check (quality gate)."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "format", "--check", "."],
            cwd=PROJECT_DIR,
            capture_output=True,
            text=True,
            timeout=settings.timeouts.subprocess_default_s,
        )
        ok = result.returncode == 0
        if ok:
            msg = "All files formatted"
        else:
            lines = result.stderr.strip().splitlines()
            msg = f"{len(lines)} file(s) need formatting"
        return CheckResult(
            name="ruff_format",
            category="Quality",
            passed=ok,
            message=msg,
            fix=None if ok else "Run: python -m ruff format .",
        )
    except Exception as e:
        return CheckResult(
            name="ruff_format",
            category="Quality",
            passed=False,
            message=f"ruff format check failed: {e}",
        )


def _check_pytest() -> CheckResult:
    """Run pytest unit tests (quality gate)."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/unit/", "-x", "-q", "--tb=no"],
            cwd=PROJECT_DIR,
            capture_output=True,
            text=True,
            timeout=settings.timeouts.subprocess_default_s,
        )
        ok = result.returncode == 0
        # Extract summary line (e.g., "42 passed in 5.23s")
        lines = result.stdout.strip().splitlines()
        summary = lines[-1] if lines else "no output"
        return CheckResult(
            name="pytest",
            category="Quality",
            passed=ok,
            message=summary,
            fix=None if ok else "Run: pytest tests/unit/ -x to see failures",
        )
    except subprocess.TimeoutExpired:
        return CheckResult(
            name="pytest",
            category="Quality",
            passed=False,
            message="pytest timed out (>5min)",
            fix="Run pytest manually: pytest tests/unit/ -x",
        )
    except Exception as e:
        return CheckResult(
            name="pytest",
            category="Quality",
            passed=False,
            message=f"pytest failed: {e}",
        )


# ---------------------------------------------------------------------------
# Check registry
# ---------------------------------------------------------------------------


def get_checks(
    *,
    quick: bool = False,
    quality: bool = False,
) -> list[Callable[[], CheckResult | list[CheckResult]]]:
    """Build the ordered list of check functions to run.

    Args:
        quick: If True, skip slow checks (Telegram session probe, model verification).
        quality: If True, include ruff and pytest checks.

    Returns:
        List of callables that return CheckResult or list[CheckResult].
    """
    checks: list[Callable] = [
        # Environment
        _check_python_version,
        _check_venv,
        _check_system_tools,
        _check_python_deps,
        # Services
        _check_redis,
        _check_redis_durability,
        _check_redis_replication_health,
        _check_session_archive_freshness,
        _check_agentsession_index_drift,
        _check_knowledge_zero_chunk_documents,
        _check_bridge,
        _check_worker,
        # Auth
        lambda: _check_telegram_session(quick=quick),
        _check_api_keys,
        _check_sdk_auth,
        _check_claude_oauth_token,
        # Resources
        _check_disk_space,
        _check_memory,
        _check_cpu,
    ]

    if quality:
        checks.extend(
            [
                _check_ruff_lint,
                _check_ruff_format,
                _check_pytest,
            ]
        )

    return checks


def run_checks(*, quick: bool = False, quality: bool = False) -> list[CheckResult]:
    """Execute all registered checks and return results.

    Each check is wrapped in try/except so a single failure
    does not crash the entire run.
    """
    check_fns = get_checks(quick=quick, quality=quality)
    results: list[CheckResult] = []

    for fn in check_fns:
        try:
            result = fn()
            if isinstance(result, list):
                results.extend(result)
            else:
                results.append(result)
        except Exception as e:
            # Determine a name from the function
            name = getattr(fn, "__name__", "unknown")
            if name == "<lambda>":
                name = "check"
            results.append(
                CheckResult(
                    name=name,
                    category="Unknown",
                    passed=False,
                    message=f"Check crashed: {e}",
                )
            )

    return results


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------


def format_text(results: list[CheckResult]) -> str:
    """Format results as a human-readable report."""
    lines: list[str] = []
    lines.append("")
    lines.append("=== Local Doctor Report ===")
    lines.append("")

    # Group by category
    categories: dict[str, list[CheckResult]] = {}
    for r in results:
        categories.setdefault(r.category, []).append(r)

    total = len(results)
    passed = sum(1 for r in results if r.passed)
    failed = total - passed

    for category, checks in categories.items():
        lines.append(f"--- {category} ---")
        for c in checks:
            icon = "[PASS]" if c.passed else "[FAIL]"
            lines.append(f"  {icon} {c.name}: {c.message}")
            if c.fix and not c.passed:
                lines.append(f"         Fix: {c.fix}")
        lines.append("")

    lines.append(f"Summary: {passed}/{total} passed, {failed} failed")
    if failed == 0:
        lines.append("All checks passed.")
    else:
        lines.append(f"{failed} check(s) need attention.")

    return "\n".join(lines)


def format_json(results: list[CheckResult]) -> str:
    """Format results as JSON."""
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    failed = total - passed

    output = {
        "passed": failed == 0,
        "summary": {
            "total": total,
            "passed": passed,
            "failed": failed,
        },
        "checks": [r.to_dict() for r in results],
    }
    return json.dumps(output, indent=2)


# ---------------------------------------------------------------------------
# Git hook installer
# ---------------------------------------------------------------------------


def install_pre_push_hook() -> bool:
    """Install a git pre-push hook that runs doctor --quick.

    Returns True if installed successfully.
    """
    hooks_dir = PROJECT_DIR / ".git" / "hooks"
    if not hooks_dir.exists():
        print(f"Error: {hooks_dir} does not exist. Are you in a git repo?")
        return False

    hook_path = hooks_dir / "pre-push"
    hook_content = """#!/usr/bin/env bash
# Installed by: python -m tools.doctor --install-hook
# Runs quick health checks before pushing.

set -e

echo "Running doctor checks..."
python -m tools.doctor --quick

if [ $? -ne 0 ]; then
    echo "Doctor checks failed. Fix issues before pushing."
    exit 1
fi
"""

    hook_path.write_text(hook_content)
    hook_path.chmod(0o755)
    print(f"Installed pre-push hook at {hook_path}")
    return True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns exit code (0=pass, 1=fail)."""
    parser = argparse.ArgumentParser(
        prog="python -m tools.doctor",
        description="Unified health check for the local development environment.",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Skip slow checks (Telegram session probe, model verification).",
    )
    parser.add_argument(
        "--quality",
        action="store_true",
        help="Include code quality checks (ruff lint, ruff format, pytest).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output results as JSON.",
    )
    parser.add_argument(
        "--install-hook",
        action="store_true",
        help="Install a git pre-push hook that runs doctor --quick.",
    )

    args = parser.parse_args(argv)

    if args.install_hook:
        ok = install_pre_push_hook()
        return 0 if ok else 1

    results = run_checks(quick=args.quick, quality=args.quality)

    if args.json_output:
        print(format_json(results))
    else:
        print(format_text(results))

    all_passed = all(r.passed for r in results)
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
