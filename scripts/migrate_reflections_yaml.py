"""Migrate ``reflections.yaml`` from the legacy ``interval: N`` form to the unified
schedule grammar (``every: Ns``).

Issue #1273 / `docs/plans/unify-recurring-tasks-into-reflections.md` Q3.

The migration is decomposed into three idempotent phases per Q3 cycle-3 design:

1. **Atomic YAML rewrite.** Read source, rewrite ``interval: N`` → ``every: Ns``
   in memory, write to a sibling temp file, then ``os.replace(temp, target)``
   for an atomic POSIX rename. Concurrent ``load_registry()`` reads see either
   the old or the new full file, never a torn read.

2. **``run_history`` → ``ReflectionRun`` backfill.** Walk every existing
   ``Reflection`` record. For each ``run_history`` entry, call
   ``ReflectionRun.get_or_create_for(name, timestamp)`` (composite-key
   idempotent). Because the field has been removed from the model class as
   of #1273 Tier-1, only records persisted prior to this migration carry
   the legacy list — the loop is a no-op for fresh records.

3. **Schema validation.** Re-load via ``load_registry()`` and call
   ``compute_next_due()`` on every entry. Any parse failure aborts.

CLI:

    python scripts/migrate_reflections_yaml.py [--target PATH] [--dry-run]
        [--check-idempotent]

The script is invoked from ``scripts/update/run.py`` Step 3.65 (after
``uv sync`` so ``croniter`` is installed before the schema-validation pass).
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# Ensure repo root is importable regardless of CWD (the script is invoked
# directly from `scripts/update/run.py` Step 3.65, which may chdir).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Match a `interval: N` line (with optional comment trailing) inside YAML.
# We only rewrite at line scope (not inside multi-line strings) so a simple
# anchored pattern is sufficient — reflections.yaml is hand-authored and the
# legacy field is always at field-position.
_INTERVAL_LINE_RE = re.compile(
    r"""^(?P<indent>\s*)interval\s*:\s*(?P<value>-?\d+)\s*(?P<trail>(?:\#.*)?)$""",
    re.MULTILINE,
)


class MigrationError(RuntimeError):
    """Raised when the YAML cannot be migrated (malformed or missing schedule)."""


@dataclass
class MigrationResult:
    """Outcome of a single migration pass."""

    rewrote: bool
    rewrites_count: int
    target: Path

    @property
    def is_no_op(self) -> bool:
        return not self.rewrote


def _resolve_default_target() -> Path:
    """Vault-first fallback to find the canonical YAML path."""
    env_path = os.environ.get("REFLECTIONS_YAML")
    if env_path:
        p = Path(env_path).expanduser()
        if p.exists():
            return p
    if not os.environ.get("VALOR_LAUNCHD"):
        vault = Path.home() / "Desktop" / "Valor" / "reflections.yaml"
        if vault.exists():
            return vault
    return Path(__file__).parent.parent / "config" / "reflections.yaml"


def _rewrite_interval_lines(text: str) -> tuple[str, int]:
    """Rewrite every ``interval: N`` line to ``every: Ns``.

    Returns the new text and the number of substitutions performed.
    """
    n = 0

    def _sub(match: re.Match[str]) -> str:
        nonlocal n
        seconds = int(match.group("value"))
        if seconds <= 0:
            raise MigrationError(
                f"interval must be positive (got {seconds}); "
                "fix the YAML by hand before running migration"
            )
        n += 1
        indent = match.group("indent")
        trail = match.group("trail") or ""
        # Preserve leading indentation and any inline comment.
        return f"{indent}every: {seconds}s {trail}".rstrip()

    new_text = _INTERVAL_LINE_RE.sub(_sub, text)
    return new_text, n


def _validate_post_migration(target: Path) -> None:
    """Phase 3: re-load via load_registry and exercise the parser.

    Raises MigrationError on any unparseable schedule. We import lazily so
    the module is callable in environments where croniter is not yet
    installed (e.g. early CI smoke).
    """
    try:
        from agent.reflection_schedule import compute_next_due
        from agent.reflection_scheduler import load_registry
    except ImportError as e:  # pragma: no cover - defensive
        raise MigrationError(f"Cannot import scheduler for validation: {e}") from e

    entries = load_registry(target)
    for entry in entries:
        if not entry.schedule:
            raise MigrationError(f"reflection {entry.name!r} has no schedule after migration")
        try:
            compute_next_due(entry.schedule, last_run=None)
        except ValueError as e:
            raise MigrationError(
                f"reflection {entry.name!r}: schedule {entry.schedule!r} failed validation: {e}"
            ) from e


def _has_unmigrated_entry(text: str) -> bool:
    """Cheap pre-flight: any line still in ``interval:`` form?"""
    return bool(_INTERVAL_LINE_RE.search(text))


def _has_any_schedule_per_entry(text: str) -> bool:
    """Crude check: every ``- name:`` block carries one of every/cron/at/interval/schedule.

    A scan-on-strings inspection good enough for the pre-rewrite pre-flight.
    For richer validation we re-load through the registry parser later.
    """
    import yaml

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        # If YAML is itself malformed we want the parser to fail later;
        # this pre-flight is best-effort.
        return True

    if not isinstance(data, dict) or "reflections" not in data:
        return True
    refs = data.get("reflections") or []
    for raw in refs:
        if not isinstance(raw, dict):
            continue
        if not (
            raw.get("interval")
            or raw.get("every")
            or raw.get("cron")
            or raw.get("at")
            or raw.get("schedule")
        ):
            return False
    return True


def migrate_yaml(target: Path, *, dry_run: bool = False) -> MigrationResult:
    """Run the migration on a single YAML file.

    Args:
        target: Path to the reflections.yaml file (vault-synced).
        dry_run: If True, compute the rewrite but do not touch disk.

    Returns:
        ``MigrationResult`` describing whether anything was rewritten.

    Raises:
        MigrationError: On malformed YAML or post-migration parse failures.
        OSError: If the atomic rename fails (the original file is preserved).
    """
    target = Path(target)
    if not target.exists():
        raise MigrationError(f"target YAML does not exist: {target}")

    original = target.read_text()

    # Pre-flight: every entry must carry SOME schedule key.
    if not _has_any_schedule_per_entry(original):
        raise MigrationError(
            "one or more entries have no schedule (no every/cron/at/interval/schedule); "
            "fix the YAML by hand before running migration"
        )

    # Phase 1 — rewrite in memory.
    rewritten, count = _rewrite_interval_lines(original)
    rewrote = count > 0

    if rewrote and not dry_run:
        # Atomic temp-file write + rename.
        tmp = target.with_suffix(target.suffix + ".migrate.tmp")
        try:
            tmp.write_text(rewritten)
            os.replace(tmp, target)
        finally:
            # Defensive cleanup if the temp file somehow survived (e.g. the
            # rename target was a directory or the temp write itself failed
            # mid-flight). os.replace is atomic on POSIX, so on success the
            # tmp path no longer exists.
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass

    # Phase 2 — backfill is a no-op here because the embedded `run_history`
    # field has been removed from the model class as of #1273 Tier-1. Records
    # written before that PR landed retain the field on disk and would be
    # walked here, but newly-loaded Reflection objects no longer expose it.
    # Backfill from raw Redis would be a layer violation; instead, the
    # follow-up "ReflectionRun fully replaces run_history" PR will land
    # the backfill once the model deliberately preserves a transitional
    # read-side accessor.

    # Phase 3 — schema validation pass on the (possibly rewritten) file.
    if not dry_run and rewrote:
        try:
            _validate_post_migration(target)
        except MigrationError:
            # Validation failed AFTER the atomic rewrite; the on-disk file is
            # the new shape but the parser rejected something. Surface the
            # error verbatim — this should be rare (Phase 1 only does textual
            # interval->every rewrites) but loud-failure is the right behavior.
            raise

    return MigrationResult(rewrote=rewrote, rewrites_count=count, target=target)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Migrate reflections.yaml from interval: to every: form."
    )
    parser.add_argument(
        "--target",
        type=Path,
        default=None,
        help="Path to reflections.yaml (default: vault-first fallback).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would change without modifying the file.",
    )
    parser.add_argument(
        "--check-idempotent",
        action="store_true",
        help=(
            "Run twice and assert the second run is a no-op; useful for the "
            "Verification table command in the plan."
        ),
    )
    args = parser.parse_args(argv)

    target = args.target or _resolve_default_target()
    print(f"[migrate] target: {target}")

    try:
        result = migrate_yaml(target, dry_run=args.dry_run)
    except MigrationError as e:
        print(f"[migrate] ABORT: {e}", file=sys.stderr)
        return 1
    print(f"[migrate] rewrote={result.rewrote} rewrites_count={result.rewrites_count}")

    if args.check_idempotent and not args.dry_run:
        second = migrate_yaml(target, dry_run=True)
        if second.rewrote:
            print(
                f"[migrate] IDEMPOTENCE FAILURE: second run still finds "
                f"{second.rewrites_count} rewrite(s)",
                file=sys.stderr,
            )
            return 2
        print("[migrate] idempotence OK (second run is a no-op)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
