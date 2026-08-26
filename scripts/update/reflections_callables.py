"""Update-system hook that repoints reflections.yaml onto owning modules.

Wraps ``scripts/migrate_reflections_callables.py`` so ``scripts/update/run.py``
Step 1.659 can render machine-readable status, and
``scripts/verify_registry_without_shim.py`` so Step 4.65 can gate the service
restart on the property that actually matters — that the registry has not
reacquired the deleted ``agent.sustainability`` shim. Issues #2875 (that shim)
and #2876 (the ``agent.agent_session_queue`` re-export hub); see that script's
docstring for why both families share a table.

Runs at Step 1.659 — BEFORE Step 1.66's vault->config copy — so a vault rewrite
also propagates into ``config/reflections.yaml`` on the same cycle. The
migration additionally rewrites the config copy directly, because
``env_sync.sync_reflections_yaml`` skips the copy when the config copy's mtime
is not older than the vault's; belt and braces, since a config copy that is
newer than the vault otherwise masks the vault indefinitely.

Unlike ``scripts/update/reflection_register.py``, this step is NOT gated on
``_this_machine_owns_valor()``: a non-owning machine still runs its own
scheduler against its own ``config/reflections.yaml``, and waiting for iCloud to
carry the edit would leave that machine's self-healing reflections pointed at a
shim that is about to be deleted.

The migration is idempotent, so running it on every ``/update`` is cheap.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Repo-relative path to the migration script.
_MIGRATION_SCRIPT = "scripts/migrate_reflections_callables.py"

# Repo-relative path to the acceptance probe that Step 4.65 gates on.
_PROBE_SCRIPT = "scripts/verify_registry_without_shim.py"

# Repo-relative sentinel the probe outcome is recorded to. `run.py` is a
# Python process and `scripts/remote-update.sh` is a shell one; the shell
# restarts the worker AFTER `run.py` has exited (see Step 4.65's comment), so
# a file is the only channel by which it can consult the probe's verdict.
# Present == the probe FAILED on the most recent run; absent == it passed.
# Fail-closed direction: the sentinel is written before it is ever read and
# removed only on a positive pass.
PROBE_SENTINEL = "data/registry-probe-failed"


@dataclass
class ReflectionsCallablesResult:
    """Outcome of a single Step 1.659 migration attempt.

    Fields:
        success: ``True`` for ``rewrote``/``noop``, ``False`` for ``error``.
        action: ``rewrote`` (at least one file changed), ``noop`` (already
            migrated, or no registry present), or ``error``.
        rewrites_count: Number of ``callable:`` lines rewritten across all targets.
        targets: Paths that were actually rewritten.
        error: Error message when ``action == "error"``; otherwise ``None``.
    """

    success: bool
    action: str
    rewrites_count: int = 0
    targets: list[str] | None = None
    error: str | None = None


def run_reflections_callables_migration(
    project_dir: Path, *, targets: list[Path] | None = None
) -> ReflectionsCallablesResult:
    """Invoke the callable migration and report a structured result.

    Args:
        project_dir: Repo root; supplies both the script and the ``.venv`` python.
        targets: Explicit registry paths. ``None`` (the production call) lets the
            migration resolve its own vault + config defaults. Tests MUST pass
            this to avoid rewriting the real vault registry.

    Never raises, but ``success=False`` is NOT fail-open. Step 1.659 records it
    as a warning and lets the rest of ``/update`` proceed; the escalation point
    is Step 4.65, which independently runs :func:`run_registry_probe` and
    suppresses the service restart when the registry does not actually import.
    Do not model this step on ``scripts/update/reflection_register.py``'s Step
    3.65, which genuinely is fail-open: the shim that made an unmigrated
    registry survivable was deleted in #2875.
    """
    project_dir = Path(project_dir)
    script = project_dir / _MIGRATION_SCRIPT
    if not script.exists():
        # Fall back to this helper's own repo root, which keeps unit tests that
        # pass a tmp dir as ``project_dir`` working against the real script.
        helper_repo_root = Path(__file__).resolve().parent.parent.parent
        script = helper_repo_root / _MIGRATION_SCRIPT
        if not script.exists():
            return ReflectionsCallablesResult(
                success=False, action="error", error=f"migration script missing: {script}"
            )

    python_bin = project_dir / ".venv" / "bin" / "python"
    if python_bin.exists():
        python = str(python_bin)
    else:
        import sys

        python = sys.executable

    cmd = [python, str(script), "--json"]
    for t in targets or []:
        cmd += ["--target", str(t)]

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return ReflectionsCallablesResult(
            success=False, action="error", error="migration timed out after 120s"
        )
    except Exception as e:  # pragma: no cover - defensive
        return ReflectionsCallablesResult(
            success=False, action="error", error=f"failed to invoke migration: {e}"
        )

    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        return ReflectionsCallablesResult(
            success=False,
            action="error",
            error=err[-500:] or f"exit code {proc.returncode}",
        )

    rewrote = False
    rewrites_count = 0
    rewritten_targets: list[str] = []
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict) or "rewrote" not in payload:
            continue
        rewrote = bool(payload.get("rewrote"))
        rewrites_count = int(payload.get("rewrites_count", 0))
        rewritten_targets = list(payload.get("targets") or [])
        break

    return ReflectionsCallablesResult(
        success=True,
        action="rewrote" if rewrote else "noop",
        rewrites_count=rewrites_count,
        targets=rewritten_targets,
    )


@dataclass
class RegistryProbeResult:
    """Outcome of a single Step 4.65 acceptance-probe run.

    Fields:
        success: ``True`` iff every ``callable:`` entry in every existing
            registry copy imported with ``agent.sustainability`` banned.
        detail: The probe's own summary line on success, or its stderr /
            an invocation error on failure. Rendered verbatim by ``run.py``.
    """

    success: bool
    detail: str


def _write_probe_sentinel(project_dir: Path, failed: bool) -> None:
    """Record the probe verdict where ``scripts/remote-update.sh`` can read it.

    Never raises: a sentinel we could not write must not abort ``/update``.
    The shell's own read is `[ -f ]`, so an unwritable sentinel degrades to
    "no signal" rather than to a false pass — the write happens on the failure
    path, and the removal (the pass path) is what a failure to write would
    turn into a spurious block, never a spurious green.
    """
    path = Path(project_dir) / PROBE_SENTINEL
    try:
        if failed:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("reflections registry callables did not import; see logs/update.log\n")
        else:
            path.unlink(missing_ok=True)
    except OSError:
        pass


def run_registry_probe(project_dir: Path) -> RegistryProbeResult:
    """Run the registry acceptance probe and report a structured result.

    This is the POSITIVE check Step 4.65 gates on: "every registry callable
    actually imports", not "the Step 1.659 rewriter did not error". The
    distinction is load-bearing. ``ReflectionsCallablesResult.success`` is true
    for ``action="noop"``, which covers *no registry present at all* and — via
    ``migrate_yaml_callables``'s early return when the line-anchored regex
    matched nothing — covers a flow-style ``{callable: agent.sustainability.x}``
    entry that was neither rewritten nor import-checked. Both are clean noops
    and both would restart the worker onto a registry that cannot import.

    Args:
        project_dir: Repo root; supplies both the script and the ``.venv``
            python, exactly as :func:`run_reflections_callables_migration` does.

    Side effect: writes/clears the ``data/registry-probe-failed`` sentinel so
    the shell half of the update (``scripts/remote-update.sh``, which restarts
    the worker after ``run.py`` exits) can consult the same verdict.

    Never raises. An invocation failure (missing script, timeout) is reported
    as ``success=False`` — fail-closed, because the probe not running proves
    nothing about the registry.
    """
    project_dir = Path(project_dir)
    script = project_dir / _PROBE_SCRIPT
    if not script.exists():
        helper_repo_root = Path(__file__).resolve().parent.parent.parent
        script = helper_repo_root / _PROBE_SCRIPT
        if not script.exists():
            return _probe_failure(project_dir, f"probe script missing: {script}")

    python_bin = project_dir / ".venv" / "bin" / "python"
    if python_bin.exists():
        python = str(python_bin)
    else:
        import sys

        python = sys.executable

    try:
        proc = subprocess.run(
            [python, str(script)],
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return _probe_failure(project_dir, "registry probe timed out after 120s")
    except Exception as e:  # pragma: no cover - defensive
        return _probe_failure(project_dir, f"failed to invoke registry probe: {e}")

    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        return _probe_failure(project_dir, err[-500:] or f"exit code {proc.returncode}")

    _write_probe_sentinel(project_dir, failed=False)
    summary = (proc.stdout or "").strip().splitlines()
    return RegistryProbeResult(
        success=True, detail=summary[-1].strip() if summary else "registry callables resolved"
    )


def _probe_failure(project_dir: Path, detail: str) -> RegistryProbeResult:
    """Stamp the sentinel and return the failing result — one place, one order."""
    _write_probe_sentinel(project_dir, failed=True)
    return RegistryProbeResult(success=False, detail=detail)
