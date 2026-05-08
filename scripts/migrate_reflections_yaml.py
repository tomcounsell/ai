#!/usr/bin/env python3
"""Migrate reflections.yaml from legacy ``interval:`` to fazm-style ``schedule: every:<N>s``.

Three idempotent phases:

  Phase 1 — Atomic YAML rewrite.
    Read the vault reflections.yaml (or in-repo fallback). For each entry with
    ``interval: N`` and no ``schedule`` key, rewrite as ``schedule: every:<N>s``
    and drop ``interval``. Write to a temp sibling file then ``os.replace()``
    (POSIX-atomic rename). If ``--dry-run``, print what would change and exit.

  Phase 2 — Backfill ``run_history`` → ``ReflectionRun``.
    Walk all Reflection records. For each legacy ``run_history`` entry (if the
    field is still present in Redis from an older schema), call
    ``ReflectionRun.get_or_create_for(name, timestamp)``. If a reflection is
    ``last_status == "running"`` at scan time, record a ``MigrationPendingClear``
    sidecar and skip the clear. After the main loop, process sidecar records:
    for each that is no longer running, clear (best-effort) and delete the sidecar.

  Phase 3 — Schema validation.
    Re-load the registry via ``load_registry()`` and call ``compute_next_due()``
    on each entry. Any ``ValueError`` aborts with exit code 2.

Usage:
    python scripts/migrate_reflections_yaml.py [--dry-run] [--check-idempotent]

Exit codes:
    0  — success (or nothing to do)
    1  — --check-idempotent: YAML is not yet migrated or entries fail to parse
    2  — Phase 3 schema validation failed (entry has bad schedule after migration)
    3  — unexpected error
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

# Add project root to Python path so sibling packages import cleanly.
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import yaml  # noqa: E402

# ---------------------------------------------------------------------------
# YAML path resolution (mirrors agent/reflection_scheduler.py logic)
# ---------------------------------------------------------------------------


def _resolve_yaml_path() -> Path:
    """Resolve the reflections YAML using vault-first fallback logic.

    Priority:
      1. REFLECTIONS_YAML env var (explicit override, e.g., for testing)
      2. ~/Desktop/Valor/reflections.yaml (iCloud-synced vault, private config)
      3. config/reflections.yaml (in-repo fallback)
    """
    env_path = os.environ.get("REFLECTIONS_YAML")
    if env_path:
        p = Path(env_path).expanduser()
        if p.exists():
            return p
        print(f"WARN: REFLECTIONS_YAML env var points to non-existent path: {env_path}")

    vault_path = Path.home() / "Desktop" / "Valor" / "reflections.yaml"
    if vault_path.exists():
        return vault_path

    repo_path = PROJECT_ROOT / "config" / "reflections.yaml"
    return repo_path


# ---------------------------------------------------------------------------
# Phase 1 — YAML rewrite
# ---------------------------------------------------------------------------


def _compute_yaml_changes(data: dict) -> list[tuple[str, int]]:
    """Return list of (name, interval_seconds) for entries needing rewrite."""
    changes = []
    for entry in data.get("reflections", []):
        if not isinstance(entry, dict):
            continue
        if "interval" in entry and "schedule" not in entry:
            changes.append((entry.get("name", "?"), entry["interval"]))
    return changes


def _rewrite_yaml(data: dict) -> bool:
    """Mutate ``data`` in-place: ``interval`` → ``schedule: every:<N>s``.

    Returns True if any changes were made.
    """
    changed = False
    for entry in data.get("reflections", []):
        if not isinstance(entry, dict):
            continue
        if "interval" in entry and "schedule" not in entry:
            interval = entry.pop("interval")
            entry["schedule"] = f"every:{interval}s"
            changed = True
    return changed


def phase1_yaml_rewrite(yaml_path: Path, dry_run: bool) -> bool:
    """Phase 1: atomic YAML rewrite of interval: → schedule: every:Ns.

    Returns True if changes were made (or would be made in dry-run).
    """
    if not yaml_path.exists():
        print(f"ERROR: reflections.yaml not found at {yaml_path}")
        sys.exit(3)

    with open(yaml_path) as f:
        data = yaml.safe_load(f)

    if not data or "reflections" not in data:
        print(f"WARN: {yaml_path} has no 'reflections' key — nothing to do in phase 1")
        return False

    changes = _compute_yaml_changes(data)
    if not changes:
        print("Phase 1: no legacy interval: entries found — YAML already migrated")
        return False

    print(f"Phase 1: {len(changes)} entry(ies) to convert:")
    for name, secs in changes:
        print(f"  {name}: interval: {secs} → schedule: every:{secs}s")

    if dry_run:
        print("Phase 1: dry-run — YAML not modified")
        return True

    # Apply and write atomically.
    _rewrite_yaml(data)
    new_yaml = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)

    tmp_path = yaml_path.parent / f"reflections.yaml.tmp.{os.getpid()}"
    try:
        with open(tmp_path, "w") as f:
            f.write(new_yaml)
        os.replace(tmp_path, yaml_path)
        print(f"Phase 1: wrote migrated YAML to {yaml_path} (atomic rename)")
    except Exception as e:
        # Clean up temp file if rename failed.
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        print(f"ERROR: failed to write migrated YAML: {e}")
        sys.exit(3)

    return True


# ---------------------------------------------------------------------------
# Phase 2 — Backfill run_history → ReflectionRun
# ---------------------------------------------------------------------------


def phase2_backfill_run_history(dry_run: bool) -> None:
    """Phase 2: migrate legacy run_history entries into ReflectionRun rows.

    Reflections currently running are deferred via MigrationPendingClear sidecar.
    This phase is safe to run while the scheduler is active.
    """
    try:
        from models.migration_pending_clear import MigrationPendingClear
        from models.reflection import Reflection
        from models.reflection_run import ReflectionRun
    except ImportError as e:
        print(f"Phase 2: skipped (Popoto models not available: {e})")
        return

    try:
        all_reflections = list(Reflection.query.all())
    except Exception as e:
        print(f"Phase 2: skipped (could not query Reflection records: {e})")
        return

    total_backfilled = 0
    deferred_names = []

    for record in all_reflections:
        # Try to read the legacy run_history field. The field was removed from
        # the model schema, but Popoto stores raw hashes in Redis so the data
        # may still be present under the old key.
        run_history = None
        try:
            run_history = getattr(record, "run_history", None)
        except Exception:
            pass

        if not run_history:
            continue

        # Reflection is actively running — defer the clear to avoid a race.
        if getattr(record, "last_status", None) == "running":
            deferred_names.append(record.name)
            if not dry_run:
                try:
                    sidecar = MigrationPendingClear(
                        reflection_name=record.name,
                        recorded_at=time.time(),
                    )
                    sidecar.save()
                except Exception as e:
                    print(f"  WARN: could not save MigrationPendingClear for {record.name}: {e}")
            print(f"  {record.name}: currently running — deferred to next run")
            continue

        # Backfill each run_history entry into a ReflectionRun row.
        count = 0
        if isinstance(run_history, list):
            for entry in run_history:
                if not isinstance(entry, dict):
                    continue
                ts = entry.get("ran_at") or entry.get("timestamp")
                if not ts:
                    continue
                ts = float(ts)
                if not dry_run:
                    try:
                        run = ReflectionRun.get_or_create_for(name=record.name, timestamp=ts)
                        # Populate from legacy fields where available.
                        if entry.get("status"):
                            run.status = entry["status"]
                        if entry.get("duration") is not None:
                            run.duration_ms = int(float(entry["duration"]) * 1000)
                        if entry.get("error"):
                            run.error = entry["error"]
                        if entry.get("projects"):
                            run.projects = entry["projects"]
                        run.save()
                    except Exception as e:
                        print(f"  WARN: could not backfill run for {record.name} @ {ts}: {e}")
                        continue
                count += 1

        if count:
            total_backfilled += count
            print(
                f"  {record.name}: {'would backfill' if dry_run else 'backfilled'} {count} run(s)"
            )

    if deferred_names:
        print(
            f"Phase 2: deferred {len(deferred_names)} reflection(s) "
            f"(still running): {', '.join(deferred_names)}"
        )

    if total_backfilled == 0 and not deferred_names:
        print("Phase 2: no legacy run_history data found — already migrated or fresh install")
    else:
        print(
            f"Phase 2: {'would backfill' if dry_run else 'backfilled'} "
            f"{total_backfilled} total run(s)"
        )

    # --- Process deferred sidecar records ---
    try:
        sidecars = list(MigrationPendingClear.query.all())
    except Exception as e:
        print(f"Phase 2: could not query MigrationPendingClear records: {e}")
        return

    for sidecar in sidecars:
        name = sidecar.reflection_name
        try:
            candidates = Reflection.query.filter(name=name)
            rec = candidates[0] if candidates else None
        except Exception:
            rec = None

        if rec and getattr(rec, "last_status", None) == "running":
            print(f"  {name}: still running — leaving sidecar for next run")
            continue

        # Reflection is no longer running; we cannot programmatically zero a
        # removed-from-schema field via Popoto (the field definition is gone),
        # so we log that the legacy data will be GC'd when Redis rewrites the
        # hash on next save(), then delete the sidecar record.
        print(
            f"  {name}: no longer running — legacy run_history will be GC'd "
            f"on next Reflection.save(); deleting sidecar"
        )
        if not dry_run:
            try:
                sidecar.delete()
            except Exception as e:
                print(f"  WARN: could not delete sidecar for {name}: {e}")


# ---------------------------------------------------------------------------
# Phase 3 — Schema validation
# ---------------------------------------------------------------------------


def phase3_schema_validation(yaml_path: Path) -> None:
    """Phase 3: re-load the registry and validate every schedule string.

    Aborts with exit code 2 if any entry has an invalid schedule.
    """
    try:
        from agent.reflection_scheduler import compute_next_due, load_registry
    except ImportError as e:
        print(f"Phase 3: skipped (reflection_scheduler not importable: {e})")
        return

    entries = load_registry(yaml_path)
    print(f"Phase 3: validating {len(entries)} enabled registry entries...")

    errors = []
    for entry in entries:
        try:
            compute_next_due(entry.schedule, None, entry.cron_tz)
        except ValueError as e:
            errors.append(f"  {entry.name}: {e}")

    if errors:
        print("Phase 3: FAILED — the following entries have invalid schedules:")
        for err in errors:
            print(err)
        sys.exit(2)

    print("Phase 3: all entries parse OK")


# ---------------------------------------------------------------------------
# --check-idempotent mode
# ---------------------------------------------------------------------------


def check_idempotent(yaml_path: Path) -> None:
    """Read-only verification: exit 0 if YAML is already migrated + entries parse.

    Exit 1 if any interval: entry remains or any schedule fails to parse.
    """
    if not yaml_path.exists():
        print(f"check-idempotent: file not found at {yaml_path}")
        sys.exit(1)

    with open(yaml_path) as f:
        data = yaml.safe_load(f)

    changes = _compute_yaml_changes(data)
    if changes:
        print(f"check-idempotent: {len(changes)} entry(ies) still use legacy interval:")
        for name, secs in changes:
            print(f"  {name}: interval: {secs}")
        sys.exit(1)

    # Validate schedules.
    try:
        from agent.reflection_scheduler import compute_next_due, load_registry
    except ImportError as e:
        print(f"check-idempotent: cannot import reflection_scheduler: {e}")
        sys.exit(1)

    entries = load_registry(yaml_path)
    errors = []
    for entry in entries:
        try:
            compute_next_due(entry.schedule, None, entry.cron_tz)
        except ValueError as e:
            errors.append(f"  {entry.name}: {e}")

    if errors:
        print("check-idempotent: schedule validation errors:")
        for err in errors:
            print(err)
        sys.exit(1)

    print(
        f"check-idempotent: OK — {len(entries)} entries, "
        f"no legacy interval: fields, all schedules parse"
    )
    sys.exit(0)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migrate reflections.yaml interval: to schedule: every:<N>s"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would change without modifying files or Redis",
    )
    parser.add_argument(
        "--check-idempotent",
        action="store_true",
        help=(
            "Read-only check: exit 0 if YAML is already migrated and all "
            "entries parse, exit 1 otherwise"
        ),
    )
    args = parser.parse_args()

    yaml_path = _resolve_yaml_path()
    print(f"reflections.yaml: {yaml_path}")

    if args.check_idempotent:
        check_idempotent(yaml_path)
        return  # check_idempotent always exits

    if args.dry_run:
        print("--- DRY RUN: no files or Redis records will be modified ---")

    # Phase 1
    print("\n--- Phase 1: YAML rewrite ---")
    phase1_yaml_rewrite(yaml_path, dry_run=args.dry_run)

    # Phase 2
    print("\n--- Phase 2: run_history backfill ---")
    phase2_backfill_run_history(dry_run=args.dry_run)

    # Phase 3
    print("\n--- Phase 3: schema validation ---")
    phase3_schema_validation(yaml_path)

    print("\nMigration complete.")


if __name__ == "__main__":
    main()
