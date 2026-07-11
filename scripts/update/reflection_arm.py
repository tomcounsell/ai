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
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

REFLECTION_NAME = "merged-branch-cleanup"
OWNING_PROJECT_KEY = "valor"

# One-shot marker: once the arm has fired (or found the entry already
# enabled), the enabled flag becomes human-owned. A later `enabled: false`
# in the vault is a deliberate operator disarm of an unattended
# push-to-main automation — the update loop must never silently re-arm
# over it. Delete the marker to make the next /update re-arm.
ARM_MARKER_RELPATH = Path("data") / "reflection-armed-merged-branch-cleanup"


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
    from config.machine import get_machine_name

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

    machine = get_machine_name()
    if not machine:
        return False

    return owner.strip().lower() == machine.strip().lower()


def _flip_enabled(path: Path) -> str:
    """Set enabled: true on REFLECTION_NAME's entry in the YAML at ``path``.

    Line-scoped text edit, never a YAML re-serialize: the vault registry is
    the human's heavily-commented source-of-truth file, and a
    ``safe_load``/``safe_dump`` round-trip would strip every comment and
    reformat the whole file. Only the one ``enabled:`` line inside the
    ``- name: merged-branch-cleanup`` block is touched; the write is atomic
    (temp file + ``os.replace``) so a crash or iCloud sync race never leaves
    a truncated registry.

    Returns a verdict, never raises. The caller must only stamp the one-shot
    marker on "flipped"/"already-enabled" -- "not-found" and "io-error" must
    stay retryable on the next /update (PR #1903 validation defect: stamping
    on a failed flip permanently bricked arming while reporting noop).

      "flipped"          -- file rewritten, entry now enabled: true
      "already-enabled"  -- entry present and already true
      "not-found"        -- file or entry absent
      "io-error"         -- read or atomic-write failure
    """
    if not path.exists():
        return "not-found"
    try:
        text = path.read_text()
    except Exception:
        return "io-error"

    lines = text.splitlines(keepends=True)
    name_re = re.compile(r"^(\s*)(-\s+)?name:\s*" + re.escape(REFLECTION_NAME) + r"\s*(#.*)?$")
    enabled_re = re.compile(r"^(\s*)(-\s+)?enabled:\s*(\S+)(\s*#.*)?$")

    name_idx = None
    for i, line in enumerate(lines):
        if name_re.match(line.rstrip("\n")):
            name_idx = i
            break
    if name_idx is None:
        return "not-found"

    # The entry's `name:` key is not necessarily on the dash line (YAML key
    # order is free). Anchor on the name line's indent, then walk back to the
    # entry's opening `- ` line to bound the block.
    raw_name = lines[name_idx].rstrip("\n")
    stripped_name = raw_name.lstrip()
    if stripped_name.startswith("-"):
        start = name_idx
        key_indent = raw_name[: len(raw_name) - len(stripped_name)] + "  "
    else:
        key_indent = raw_name[: len(raw_name) - len(stripped_name)]
        start = name_idx
        for j in range(name_idx - 1, -1, -1):
            s = lines[j].lstrip()
            indent = lines[j][: len(lines[j]) - len(s)]
            if not s:
                break
            if s.startswith("- ") and len(indent) < len(key_indent):
                start = j
                break
            if len(indent) < len(key_indent):
                break

    end = len(lines)
    for j in range(start + 1, len(lines)):
        raw = lines[j].rstrip("\n")
        if raw.strip() and not raw.startswith(key_indent):
            end = j
            break

    for j in range(start, end):
        m = enabled_re.match(lines[j].rstrip("\n"))
        if m:
            if m.group(3).lower() in ("true", "yes", "on"):
                return "already-enabled"
            lines[j] = f"{m.group(1)}{m.group(2) or ''}enabled: true{m.group(4) or ''}\n"
            break
    else:
        lines.insert(name_idx + 1, f"{key_indent}enabled: true\n")

    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text("".join(lines))
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)
        return "io-error"
    return "flipped"


def _write_arm_marker(marker: Path) -> None:
    """Stamp the one-shot marker; best-effort, never raises."""
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("armed by scripts/update/reflection_arm.py; delete to allow re-arm\n")
    except Exception:
        pass


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

    marker = project_dir / ARM_MARKER_RELPATH
    if marker.exists():
        return ArmResult(
            True,
            "skipped",
            f"{REFLECTION_NAME} arm already fired once; enabled flag is human-owned now "
            f"(delete {marker} to re-arm)",
        )

    try:
        vault_verdict = _flip_enabled(vault_path)
        # The repo copy is best-effort: it is clobbered from the vault on the
        # next /update anyway, and may legitimately be absent on a fresh
        # checkout. The vault verdict alone decides success and the marker.
        repo_verdict = _flip_enabled(project_dir / "config" / "reflections.yaml")
    except Exception as e:  # pragma: no cover - defensive
        return ArmResult(False, "error", f"failed to flip enabled: {e}")

    if vault_verdict not in ("flipped", "already-enabled"):
        # No marker: a missing entry or an I/O failure must stay retryable on
        # the next /update, and must never be reported as "already enabled"
        # (PR #1903 validation: the old bool return conflated these with noop
        # and the unconditional marker bricked all future arm attempts).
        return ArmResult(
            False,
            "error",
            f"vault flip failed ({vault_verdict}) at {vault_path}; will retry next /update",
        )

    _write_arm_marker(marker)
    if vault_verdict == "already-enabled" and repo_verdict != "flipped":
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
