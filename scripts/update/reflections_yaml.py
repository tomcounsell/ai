"""Update-system hook that migrates ``reflections.yaml`` on every ``/update`` run.

Wraps ``scripts/migrate_reflections_yaml.py`` so ``scripts/update/run.py``
Step 3.65 can render machine-readable status. The migration is idempotent
(detects post-migration shape and exits cleanly) so running it on every pull
is cheap.

The wrapper invokes the migration script via subprocess against the repo's
``.venv`` python — matching the pattern used by ``scripts/update/migrations.py``
— so the freshly-installed ``croniter`` dependency from Step 3 is reachable
when the migration's schema-validation phase imports it.

Issue #1342 / #1273 — Tier 3A item 1.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Repo-relative path to the migration script.
_MIGRATION_SCRIPT = "scripts/migrate_reflections_yaml.py"


@dataclass
class ReflectionsYamlMigrationResult:
    """Outcome of a single Step 3.65 migration attempt.

    Fields:
        success: ``True`` for ``rewrote``/``noop``/``skipped``, ``False`` for ``error``.
        action: One of ``rewrote`` (file changed), ``noop`` (already migrated),
            ``skipped`` (target missing), or ``error`` (migration failed).
        rewrites_count: Number of ``interval:`` lines rewritten on this pass.
        error: Error message when ``action == "error"``; otherwise ``None``.
    """

    success: bool
    action: str
    rewrites_count: int = 0
    error: str | None = None


def _resolve_target() -> Path | None:
    """Mirror ``migrate_reflections_yaml._resolve_default_target`` shape.

    We import lazily because the migration script transitively imports
    ``croniter`` (via ``agent.reflection_schedule``), which the update step
    only guarantees is installed once Step 3's ``uv sync`` completes.
    """
    import os

    env_path = os.environ.get("REFLECTIONS_YAML")
    if env_path:
        p = Path(env_path).expanduser()
        return p if p.exists() else p  # caller decides if missing is fatal

    if not os.environ.get("VALOR_LAUNCHD"):
        vault = Path.home() / "Desktop" / "Valor" / "reflections.yaml"
        if vault.exists():
            return vault

    repo_root = Path(__file__).resolve().parent.parent.parent
    fallback = repo_root / "config" / "reflections.yaml"
    if fallback.exists():
        return fallback
    return None


def run_reflections_yaml_migration(project_dir: Path) -> ReflectionsYamlMigrationResult:
    """Invoke ``scripts/migrate_reflections_yaml.py`` and report a structured result.

    Returns:
        A ``ReflectionsYamlMigrationResult`` describing the outcome. The
        function never raises — callers (``scripts/update/run.py``) treat
        ``success=False`` as a non-fatal warning and continue.
    """
    target = _resolve_target()
    if target is None or not target.exists():
        return ReflectionsYamlMigrationResult(
            success=True,
            action="skipped",
            error=f"target not found: {target}" if target else None,
        )

    project_dir = Path(project_dir)
    script = project_dir / _MIGRATION_SCRIPT
    if not script.exists():
        # Fall back to the helper's own repo root (handy for unit tests that
        # pass a tmp dir as ``project_dir`` to isolate the YAML target but
        # still want the real migration script).
        helper_repo_root = Path(__file__).resolve().parent.parent.parent
        script = helper_repo_root / _MIGRATION_SCRIPT
        if not script.exists():
            # Genuine partial checkout — surface a soft error rather than crash.
            return ReflectionsYamlMigrationResult(
                success=False,
                action="error",
                error=f"migration script missing: {script}",
            )

    python_bin = project_dir / ".venv" / "bin" / "python"
    if not python_bin.exists():
        # Fall back to the current interpreter rather than crashing — Step 3.65
        # runs after Step 3 ``uv sync`` so this should be rare.
        import sys

        python = sys.executable
    else:
        python = str(python_bin)

    try:
        proc = subprocess.run(
            [python, str(script), "--target", str(target), "--json"],
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return ReflectionsYamlMigrationResult(
            success=False,
            action="error",
            error="migration timed out after 120s",
        )
    except Exception as e:  # pragma: no cover - defensive
        return ReflectionsYamlMigrationResult(
            success=False,
            action="error",
            error=f"failed to invoke migration: {e}",
        )

    if proc.returncode != 0:
        # Strip ANSI / verbose noise — keep the last error block.
        err = (proc.stderr or proc.stdout or "").strip()
        return ReflectionsYamlMigrationResult(
            success=False,
            action="error",
            error=err[-500:] or f"exit code {proc.returncode}",
        )

    # Parse the JSON status line emitted by the migration script.
    rewrote = False
    rewrites_count = 0
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        if "rewrote" in payload:
            rewrote = bool(payload.get("rewrote"))
            rewrites_count = int(payload.get("rewrites_count", 0))
            break

    action = "rewrote" if rewrote else "noop"
    return ReflectionsYamlMigrationResult(
        success=True,
        action=action,
        rewrites_count=rewrites_count,
    )
