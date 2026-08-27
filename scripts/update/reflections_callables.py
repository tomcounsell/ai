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
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Repo-relative path to the migration script.
_MIGRATION_SCRIPT = "scripts/migrate_reflections_callables.py"

# Repo-relative path to the acceptance probe that Step 4.65 gates on.
_PROBE_SCRIPT = "scripts/verify_registry_without_shim.py"

# Wall-clock ceiling for both helper subprocesses. Take with a grain of salt:
# provisional and tunable. Both do the same shape of work — spawn the repo venv
# python and import every `callable:` in the registry — and both run inside
# `/update`, which has no interactive waiter. Sized well above the observed
# cost (a handful of imports) so an ordinarily slow cold start is not read as a
# fault, and well below anything an operator would call a hang. Reaching it is
# itself a fail-closed verdict: the probe not finishing proves nothing about the
# registry, so `run_registry_probe` reports the timeout as `success=False`.
_HELPER_TIMEOUT_SECONDS = 120

# Repo-relative sentinel the probe outcome is recorded to. `run.py` is a
# Python process and `scripts/remote-update.sh` is a shell one; the shell
# restarts the worker AFTER `run.py` has exited (see Step 4.65's comment), so
# a file is the only channel by which it can consult the probe's verdict.
# Present == the probe FAILED on the most recent run; absent == it passed.
# Note which way that cuts: since the shell's read is `[ -f ]`, ABSENCE is its
# green light. Nothing about the file itself is therefore fail-closed — the
# fail-closed property comes from `run.py` escalating a failing probe whose
# sentinel did not reach disk to a non-zero exit, so `set -e` stops the shell
# before it ever performs the read. See `_write_probe_sentinel`.
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
            timeout=_HELPER_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return ReflectionsCallablesResult(
            success=False,
            action="error",
            error=f"migration timed out after {_HELPER_TIMEOUT_SECONDS}s",
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
        sentinel_skipped: ``True`` when the caller asked for no sentinel I/O at
            all (``record_sentinel=False``, which ``run.py`` passes under
            ``--verify``). Read this BEFORE ``sentinel_recorded``: when it is
            set, the sentinel was deliberately left exactly as it was and
            ``sentinel_recorded`` is meaningless rather than bad news.
        sentinel_recorded: ``True`` iff the on-disk sentinel now matches
            ``success``. False means the shell half of ``/update`` will read a
            verdict this one does not agree with; see
            :func:`_write_probe_sentinel` for why only one direction is safe.
            Always ``False`` when ``sentinel_skipped`` is set.
        nothing_probed: ``True`` when the probe found no registry copy at all
            (its exit code 2). ``success`` is ``True`` in that case — there is
            no callable that could have failed — but it is a *vacuous* pass and
            must not be reported as a clean one. ``run.py`` routes it to the
            operator warning channel so the run does not render as a bare
            green.
    """

    success: bool
    detail: str
    sentinel_recorded: bool = True
    nothing_probed: bool = False
    sentinel_skipped: bool = False


# Mirrors ``scripts/verify_registry_without_shim.py::EXIT_NO_REGISTRY``. Pinned
# by a test, since the two files share no importable constant.
_PROBE_EXIT_NO_REGISTRY = 2


def _write_probe_sentinel(project_dir: Path, failed: bool) -> bool:
    """Record the probe verdict where ``scripts/remote-update.sh`` can read it.

    Returns ``True`` iff the sentinel on disk now matches ``failed``.

    The shell's read is ``[ -f "$PROJECT_DIR/data/registry-probe-failed"]``, so
    **presence is the blocking state and absence is the green one**. That single
    fact fixes which write direction is dangerous:

    - Failure path (``failed=True``, create). A write that does not land leaves
      the file absent, which the shell reads as green — a **false pass**, and
      the worker gets restarted onto a registry that cannot import. Caller must
      escalate; ``run.py`` Step 4.65 turns a ``False`` return into a
      ``result.errors`` entry, which makes ``run.py`` exit non-zero and
      ``set -e`` abort ``remote-update.sh`` before its kickstart.
    - Pass path (``failed=False``, unlink). A removal that does not land leaves
      the file present, which the shell reads as blocked — a spurious block.
      Safe, so it is reported but not escalated.

    Never raises: an OSError here must not abort ``/update`` with a traceback.
    The return value, not an exception, is how the caller learns about it.
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
    # Confirm against the filesystem rather than trusting the absence of an
    # exception: the directory could be read-only, the write could have been
    # made to a path the shell does not read, or an unlink could have raced.
    # `==`, not `is`: `is` happens to work while both call sites pass bool
    # literals, but a truthy non-bool (`failed=1`) would make this report
    # "not recorded" unconditionally — which on the failure path escalates to a
    # spurious hard exit in `run.py`.
    return path.exists() == bool(failed)


def run_registry_probe(project_dir: Path, *, record_sentinel: bool = True) -> RegistryProbeResult:
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
        record_sentinel: When ``False``, run the probe but touch
            ``data/registry-probe-failed`` in neither direction, and report
            ``sentinel_skipped=True``. ``run.py`` passes ``False`` under
            ``--verify`` (``UpdateConfig.read_only``, #3026): the sentinel is
            machine-global state, and --verify promises to leave none behind.
            The *clear* is the sharper reason. --verify runs outside
            ``remote-update.sh``'s lockfile, so a passing --verify launched from
            a scratch worktree between that script's ``run.py --cron`` and its
            kickstart would unlink a failing verdict the cron run had just
            stamped, and absence is the shell's green light. Suppression removes
            that fail-open outright; the shell's mtime freshness bound only ever
            covered the opposite (over-blocking) direction.

    Side effect (unless ``record_sentinel=False``): writes/clears the
    ``data/registry-probe-failed`` sentinel so the shell half of the update
    (``scripts/remote-update.sh``, which restarts the worker after ``run.py``
    exits) can consult the same verdict. Whether that landed is reported as
    ``sentinel_recorded``; a failing probe whose sentinel did not land is a
    false green for the shell and the caller must escalate it.

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
            return _probe_failure(
                project_dir, f"probe script missing: {script}", record_sentinel=record_sentinel
            )

    python_bin = project_dir / ".venv" / "bin" / "python"
    if python_bin.exists():
        python = str(python_bin)
    else:
        import sys

        python = sys.executable

    # VALOR_LAUNCHD=1 is mandatory, not incidental — it selects the candidate
    # set. Without it the probe takes `verify_registry_without_shim.py`'s
    # not-under-launchd branch and stat()s `~/Desktop/Valor/reflections.yaml`.
    # `/update`'s own deployment vehicle IS a launchd agent (com.valor.update
    # runs remote-update.sh, and its plist exports only PATH and HOME), where
    # macOS TCC blocks ~/Desktop and `exists()` can hang outright. The vault is
    # also not a copy any process loads: the reflection worker runs under
    # VALOR_LAUNCHD=1 and reads `config/reflections.yaml`. So probing it can
    # only produce a fail-closed verdict about a file nothing imports —
    # a permanent restart block from a lost Full Disk Access grant, which is a
    # documented recurring condition on this machine. Setting it here makes
    # the probed set identical to the resolved set, and identical to what
    # `scripts/install_reflection_worker.sh` already passes for the same script.
    probe_env = {**os.environ, "VALOR_LAUNCHD": "1"}

    try:
        proc = subprocess.run(
            [python, str(script)],
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            timeout=_HELPER_TIMEOUT_SECONDS,
            env=probe_env,
        )
    except subprocess.TimeoutExpired:
        return _probe_failure(
            project_dir,
            f"registry probe timed out after {_HELPER_TIMEOUT_SECONDS}s",
            record_sentinel=record_sentinel,
        )
    except Exception as e:  # pragma: no cover - defensive
        return _probe_failure(
            project_dir,
            f"failed to invoke registry probe: {e}",
            record_sentinel=record_sentinel,
        )

    if proc.returncode == _PROBE_EXIT_NO_REGISTRY:
        # Vacuous pass: no registry copy exists, so no callable could have
        # failed. Clear the sentinel — blocking the restart on this would wedge
        # the cycle with no self-clearing path — but carry the fact out, because
        # a run that probed nothing must not render as a clean green. The
        # probe's own explanation is on stderr, which the success path below
        # does not read; take it from there deliberately.
        #
        # The clear runs even over a sentinel an EARLIER cycle stamped, and that
        # is knowingly accepted rather than overlooked. It is the one place this
        # design moves blocked -> unblocked on absent evidence: cycle N stamps a
        # failure, the registry then goes missing, cycle N+1 exits 2 and lifts
        # cycle N's block. Leaving the sentinel in place instead would trade a
        # bounded miss for an unbounded one — a machine with no registry has no
        # route back to a passing probe, so the block would never lift. The miss
        # is bounded because nothing silently proceeds on it:
        # `com.valor.reflection-worker` is not among `remote-update.sh`'s
        # kickstarts, `install_reflection_worker.sh` refuses outright on exit 2,
        # and `nothing_probed` still forces the operator warning channel.
        recorded = record_sentinel and _write_probe_sentinel(project_dir, failed=False)
        warning = (proc.stderr or proc.stdout or "").strip().splitlines()
        return RegistryProbeResult(
            success=True,
            detail=warning[-1].strip() if warning else "no reflections registry found",
            sentinel_recorded=recorded,
            nothing_probed=True,
            sentinel_skipped=not record_sentinel,
        )

    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        return _probe_failure(
            project_dir,
            err[-500:] or f"exit code {proc.returncode}",
            record_sentinel=record_sentinel,
        )

    recorded = record_sentinel and _write_probe_sentinel(project_dir, failed=False)
    summary = (proc.stdout or "").strip().splitlines()
    return RegistryProbeResult(
        success=True,
        detail=summary[-1].strip() if summary else "registry callables resolved",
        sentinel_recorded=recorded,
        sentinel_skipped=not record_sentinel,
    )


def _probe_failure(
    project_dir: Path, detail: str, *, record_sentinel: bool = True
) -> RegistryProbeResult:
    """Stamp the sentinel and return the failing result — one place, one order.

    ``record_sentinel=False`` skips the stamp entirely. That is safe only
    because the caller that passes it (``run.py`` under ``--verify``) escalates
    a failing probe to ``result.errors`` unconditionally instead of relying on
    the sentinel to carry the verdict anywhere.
    """
    recorded = record_sentinel and _write_probe_sentinel(project_dir, failed=True)
    return RegistryProbeResult(
        success=False,
        detail=detail,
        sentinel_recorded=recorded,
        sentinel_skipped=not record_sentinel,
    )
