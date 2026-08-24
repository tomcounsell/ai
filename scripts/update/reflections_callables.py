"""Update-system hook that repoints reflections.yaml off the sustainability shim.

Wraps ``scripts/migrate_reflections_callables.py`` so ``scripts/update/run.py``
Step 1.659 can render machine-readable status. Issue #2875.

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

    Never raises — ``scripts/update/run.py`` treats ``success=False`` as a
    non-fatal warning and continues, matching Step 3.65's contract.
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
    targets: list[str] = []
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
        targets = list(payload.get("targets") or [])
        break

    return ReflectionsCallablesResult(
        success=True,
        action="rewrote" if rewrote else "noop",
        rewrites_count=rewrites_count,
        targets=targets,
    )
