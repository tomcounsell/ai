"""Update-system step: arm the merged-branch-cleanup plan-migration backstop.

Issue #1900 (Tier 0). ``scripts/migrate_completed_plan.py``'s
``migrate_plan_to_completed()`` is the single guarded mechanism for moving a
completed plan out of ``docs/plans/`` root into ``docs/plans/completed/``. The
``merged-branch-cleanup`` reflection (``reflections/housekeeping/merged_branch_cleanup.py``)
calls that mechanism on its ``closed_issue`` branch -- but only takes effect
once its registry entry carries ``enabled: true``.

Why this needs its own update step (not just a commit that edits the entry):
``config/reflections.yaml`` is gitignored -- an install-time COPY of the
iCloud vault source (``~/Desktop/Valor/reflections.yaml``), refreshed by
``env_sync.sync_reflections_yaml()`` (Step 1.66) on every ``/update`` run. A
commit that flips ``enabled: true`` only in the in-repo copy is silently
clobbered the next time that copy step runs (it overwrites config/ from the
vault, never the reverse), and a machine that has never synced the vault
copy never sees the repo edit at all. The vault file is the durable source of
truth, so arming the reflection for real means writing ``enabled: true`` into
*that* file.

Guarded on:
  - the vault reflections.yaml existing (fresh machines with no vault copy
    skip -- nothing to arm yet, in-repo fallback stays as authored)
  - this machine owning the ``valor`` project per ``config/projects.json``
    (mirrors ``tools.reflection_machine_filter``'s ownership model) -- a
    machine that isn't the single owner of this repo's own project never
    mutates someone else's vault file.

On a successful flip, reloads the reflection-worker subprocess
(``install_reflection_worker.sh``) so the change takes effect immediately
instead of waiting for the next `/update`.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import yaml

REFLECTION_NAME = "merged-branch-cleanup"
OWNING_PROJECT_KEY = "valor"


@dataclass
class ArmResult:
    """Outcome of a single arm-reflection attempt.

    Fields:
        success: True unless an unexpected error occurred (best-effort step;
            callers treat success=False as a non-fatal warning).
        action: One of "armed" (flipped enabled: true somewhere), "noop"
            (already enabled everywhere it applies), "skipped" (guard not
            met -- no vault file, or this machine doesn't own the project),
            or "error".
        detail: Human-readable explanation for logging.
    """

    success: bool
    action: str
    detail: str = ""


def _vault_reflections_path() -> Path:
    env_path = os.environ.get("REFLECTIONS_YAML")
    if env_path:
        return Path(env_path).expanduser()
    return Path.home() / "Desktop" / "Valor" / "reflections.yaml"


def _this_machine_owns_valor(project_dir: Path) -> bool:
    """True iff config/projects.json's 'valor' project.machine == this host.

    Fails closed (returns False) on any missing file/field so a
    misconfigured or partial checkout never mutates the vault file.
    """
    from tools.machine_identity import computer_name

    projects_path = project_dir / "config" / "projects.json"
    if not projects_path.exists():
        return False
    try:
        data = json.loads(projects_path.read_text())
    except Exception:
        return False

    owner = (data.get("projects", {}).get(OWNING_PROJECT_KEY) or {}).get("machine", "")
    if not isinstance(owner, str) or not owner.strip():
        return False

    machine = computer_name()
    if not machine:
        return False

    return owner.strip().lower() == machine.strip().lower()


def _flip_enabled(path: Path) -> bool:
    """Set enabled: true on REFLECTION_NAME's entry in the YAML at ``path``.

    Returns True iff the file was rewritten (entry existed and was not
    already enabled). Never raises -- any parse/read failure is treated as
    "nothing to flip" so a malformed file can't crash the update run.
    """
    if not path.exists():
        return False
    try:
        data = yaml.safe_load(path.read_text())
    except Exception:
        return False
    if not isinstance(data, dict) or "reflections" not in data:
        return False

    changed = False
    for entry in data["reflections"]:
        if isinstance(entry, dict) and entry.get("name") == REFLECTION_NAME:
            if not entry.get("enabled", False):
                entry["enabled"] = True
                changed = True

    if changed:
        path.write_text(yaml.safe_dump(data, sort_keys=False, default_flow_style=False))
    return changed


def arm_merged_branch_cleanup(project_dir: Path) -> ArmResult:
    """Flip merged-branch-cleanup's enabled to True in the vault + repo copies.

    See module docstring for the guard conditions and why both files are
    written. Reloads the reflection-worker subprocess when anything changes.
    """
    vault_path = _vault_reflections_path()
    if not vault_path.exists():
        return ArmResult(True, "skipped", f"vault reflections.yaml not found at {vault_path}")

    if not _this_machine_owns_valor(project_dir):
        return ArmResult(True, "skipped", "this machine does not own the 'valor' project")

    try:
        vault_changed = _flip_enabled(vault_path)
        repo_changed = _flip_enabled(project_dir / "config" / "reflections.yaml")
    except Exception as e:  # pragma: no cover - defensive
        return ArmResult(False, "error", f"failed to flip enabled: {e}")

    if not (vault_changed or repo_changed):
        return ArmResult(True, "noop", f"{REFLECTION_NAME} already enabled")

    reload_script = project_dir / "scripts" / "install_reflection_worker.sh"
    if reload_script.exists():
        try:
            subprocess.run(
                [str(reload_script)],
                cwd=str(project_dir),
                capture_output=True,
                text=True,
                timeout=120,
            )
        except Exception as e:
            return ArmResult(
                True,
                "armed",
                f"{REFLECTION_NAME} enabled=true written but worker reload failed: {e}",
            )

    return ArmResult(True, "armed", f"{REFLECTION_NAME} enabled=true (vault + repo)")


def main() -> int:
    project_dir = Path(__file__).resolve().parent.parent.parent
    result = arm_merged_branch_cleanup(project_dir)
    print(f"{result.action}: {result.detail}")
    return 0 if result.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
