"""Disable repo-specific reflections on machines that don't own the project.

Some reflections — chiefly the ``audits`` group — operate on a single repo's
codebase and GitHub tracker (file issues, scan for tech debt, audit docs). The
reflections registry (``reflections.yaml``) is one iCloud-synced file shared
verbatim across every machine, so without gating, all N machines run the same
repo audit and each files its own copy of every finding. That is the root cause
of the ``documentation``-label duplicate flood (and the same hazard for every
other issue-filing audit).

The fix is single-machine ownership, applied at **update time** rather than at
run time: a reflection declares ``project_key: <key>`` in the shared registry,
and this filter — run by ``install_worker.sh`` right after it copies the vault
``reflections.yaml`` into the per-machine launchd-safe ``config/reflections.yaml``
— flips ``enabled: false`` on every project-scoped reflection this machine does
not own (per ``projects.json``'s ``projects.<key>.machine``). The scheduler then
needs no runtime ownership logic: it already skips ``enabled: false`` entries,
and it never has to read ``projects.json`` on the launchd hot path (where macOS
TCC would hang on the iCloud copy anyway).

Ownership semantics:
  * ``project_key`` unset            → unscoped, never touched (runs everywhere).
  * owner machine == this machine    → left enabled.
  * owner machine != this machine    → forced ``enabled: false``.
  * ``project_key`` not in projects.json → fail-open (left as-is) with a warning,
    so a config typo never silently disables an audit on every machine.

This is a derived-artifact rewrite: ``config/reflections.yaml`` is regenerated
from the vault on every install, so a YAML round-trip that drops comments is
acceptable — the scheduler only consumes the structured data.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REFLECTIONS = PROJECT_ROOT / "config" / "reflections.yaml"
DEFAULT_PROJECTS = PROJECT_ROOT / "config" / "projects.json"


def _current_machine_name() -> str:
    """Return this machine's ComputerName (matches projects.json ``machine``)."""
    try:
        result = subprocess.run(
            ["scutil", "--get", "ComputerName"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return result.stdout.strip()
    except Exception:
        return ""


def _load_project_machines(projects_path: Path) -> dict[str, str]:
    """Map project_key → owning machine name (lowercased) from projects.json."""
    try:
        data = json.loads(projects_path.read_text())
    except Exception as e:
        print(f"reflection_machine_filter: cannot read {projects_path}: {e}", file=sys.stderr)
        return {}
    owners: dict[str, str] = {}
    for key, project in data.get("projects", {}).items():
        machine = project.get("machine", "")
        if isinstance(machine, str):
            owners[key] = machine.strip().lower()
    return owners


def filter_reflections_for_machine(
    reflections_path: Path,
    projects_path: Path,
    machine_name: str | None = None,
) -> tuple[int, list[str]]:
    """Disable project-scoped reflections this machine does not own, in place.

    Returns ``(disabled_count, disabled_names)``. Writes the file back only if
    at least one entry changed. Reflections without ``project_key`` are never
    touched; an unknown ``project_key`` fails open (left as-is) with a warning.
    """
    # Refuse to write through a symlink: config/reflections.yaml is sometimes a
    # symlink back to the iCloud vault (the shared source registry). install_worker.sh
    # replaces it with a real copy before calling us, but a manual invocation against
    # the symlink would otherwise rewrite the vault (stripping comments and disabling
    # entries for every machine). Never mutate the shared source.
    if reflections_path.is_symlink():
        print(
            f"reflection_machine_filter: {reflections_path} is a symlink (likely the shared "
            "vault registry) — refusing to filter in place. Run against the local copy.",
            file=sys.stderr,
        )
        return 0, []

    machine = (
        (machine_name if machine_name is not None else _current_machine_name()).strip().lower()
    )
    if not machine:
        print(
            "reflection_machine_filter: could not resolve machine name — "
            "leaving reflections unchanged (fail-open)",
            file=sys.stderr,
        )
        return 0, []

    try:
        data = yaml.safe_load(reflections_path.read_text())
    except Exception as e:
        print(f"reflection_machine_filter: cannot read {reflections_path}: {e}", file=sys.stderr)
        return 0, []

    if not isinstance(data, dict) or "reflections" not in data:
        print(
            f"reflection_machine_filter: {reflections_path} has no 'reflections' key — skipping",
            file=sys.stderr,
        )
        return 0, []

    owners = _load_project_machines(projects_path)
    disabled_names: list[str] = []

    for entry in data["reflections"]:
        if not isinstance(entry, dict):
            continue
        project_key = entry.get("project_key")
        if not project_key:
            continue  # unscoped — runs everywhere
        owner = owners.get(project_key)
        if owner is None:
            print(
                f"reflection_machine_filter: project_key '{project_key}' on reflection "
                f"'{entry.get('name', '?')}' not in projects.json — leaving enabled (fail-open)",
                file=sys.stderr,
            )
            continue
        if owner != machine:
            if entry.get("enabled", True):
                disabled_names.append(entry.get("name", "?"))
            entry["enabled"] = False
        # owner == machine → leave as authored (enabled or not)

    if disabled_names:
        reflections_path.write_text(yaml.safe_dump(data, sort_keys=False, default_flow_style=False))

    return len(disabled_names), disabled_names


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Disable repo-specific reflections this machine does not own."
    )
    parser.add_argument("--reflections", type=Path, default=DEFAULT_REFLECTIONS)
    parser.add_argument("--projects", type=Path, default=DEFAULT_PROJECTS)
    parser.add_argument(
        "--machine",
        default=None,
        help="Override the machine name (testing); defaults to ComputerName.",
    )
    args = parser.parse_args(argv)

    if not args.reflections.exists():
        print(f"reflection_machine_filter: {args.reflections} not found — nothing to filter")
        return 0
    if not args.projects.exists():
        print(
            f"reflection_machine_filter: {args.projects} not found — "
            "leaving reflections unchanged (fail-open)",
            file=sys.stderr,
        )
        return 0

    count, names = filter_reflections_for_machine(args.reflections, args.projects, args.machine)
    machine = (args.machine or _current_machine_name()) or "?"
    if count:
        print(f"Disabled {count} non-owned reflection(s) on '{machine}': {', '.join(names)}")
    else:
        print(f"No project-scoped reflections to disable on '{machine}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
