"""Migrate ``reflections.yaml`` callables onto the modules that own them.

Issues #2875 and #2876. Two independent families share one table, because both
have the same shape: the registry names a dotted path on a module that does not
own the callable, and the owning module cannot be cleaned up until every
machine's registry names it directly.

**#2875 — the ``agent.sustainability`` shim.** The five self-healing reflections
moved to ``reflections/agents/*.py`` in #1028, and ``agent/sustainability.py``
has existed since then purely so the registry's historical dotted paths keep
resolving. Retiring the shim requires the registry to name the real modules
first.

**#2876 — the ``agent.agent_session_queue`` re-export hub.** Three registry
entries resolve two callables through the queue module, which re-exports them
from ``agent.session_health`` and ``agent.session_revival``.
``cleanup_stale_branches_all_projects`` is a pure re-export and blocked phase 1
of that issue outright; ``cleanup_corrupted_agent_sessions`` is used by the
hub's own code and would survive deletion, but pointing the registry at a
module that does not own it re-creates the coupling #2876 exists to remove.

**Why this is a script and not a file edit.** ``config/reflections.yaml`` is
gitignored — deliberately untracked in c2af09602 (Apr 2026) via ``git rm
--cached``, with the in-repo fallback deleted in the same commit. As
``scripts/update/reflection_register.py`` puts it, a hand-edit "never ships via
git history and is clobbered by the next vault->config sync — only a committed
code path that writes the vault file makes the registration real." So the
migration ships as tracked code that ``/update`` runs on every machine.

**Why both copies are rewritten.** ``env_sync.sync_reflections_yaml`` refreshes
``config/reflections.yaml`` from the vault only when the config copy is OLDER
than the vault (``config.mtime >= vault.mtime`` short-circuits to a no-op).
A config copy that is newer therefore masks the vault indefinitely — exactly the
state this repo was in when the migration was written (config Aug 24, vault
Aug 13, byte-identical). Rewriting both files makes the migration immune to the
mtime guard in either direction, and to the ordering of the update steps.

**Why a targeted import check.** ``ReflectionEntry.validate`` only asserts the
callable string is non-empty; it never imports it. Resolution happens at
execution inside a broad ``except Exception`` in ``run_reflection``, which
records ``last_error`` and keeps ticking with no Sentry hook or alert. A typo'd
path would load cleanly and silently disable a reflection. So this script
imports every migration TARGET before touching disk, and aborts if one is
missing. Only the targets actually MATCHED in the file being rewritten are
checked — validating the whole table would let an unrelated broken entry block a
safe migration. That scoping is load-bearing now that the table spans two
independent migrations (the ``agent.sustainability`` shim and the
``agent.agent_session_queue`` re-export hub, issue #2876): without it a
transient import failure in one family aborts the other's migration, and
``scripts/update/run.py`` Step 1.659 only WARNs — so the machine would stay
silently un-migrated with no indication the cause was unrelated.

CLI:

    python scripts/migrate_reflections_callables.py [--target PATH ...] [--dry-run]
        [--check-idempotent] [--json]

Invoked from ``scripts/update/run.py`` Step 1.659, which runs BEFORE Step 1.66's
vault->config copy so a freshly-rewritten vault also propagates on the same
cycle.
"""

from __future__ import annotations

import argparse
import importlib
import json as _json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# Ensure repo root is importable regardless of CWD (the script is invoked
# directly from `scripts/update/run.py`, which may chdir).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

#: Historical shim path -> canonical per-reflection module path.
#:
#: Mirrors the re-export table in ``agent/sustainability.py``'s docstring. Note
#: that ``sustainability_digest`` maps to the ``system_health_digest`` module —
#: the module name does NOT match the old callable name, and the naive
#: ``reflections.agents.sustainability_digest.run`` would be a silent
#: ImportError at execution time.
CALLABLE_MIGRATIONS: dict[str, str] = {
    "agent.sustainability.circuit_health_gate": "reflections.agents.circuit_health_gate.run",
    "agent.sustainability.session_count_throttle": "reflections.agents.session_count_throttle.run",
    "agent.sustainability.failure_loop_detector": "reflections.agents.failure_loop_detector.run",
    "agent.sustainability.session_recovery_drip": "reflections.agents.session_recovery_drip.run",
    "agent.sustainability.sustainability_digest": "reflections.agents.system_health_digest.run",
    # De-hubbing agent/agent_session_queue.py (issue #2876). The registry reaches
    # these two through the re-export hub, which owns neither. `_resolve_callable`
    # getattrs them off the hub, so the hub cannot drop the re-export until every
    # machine's registry names the owning module. `cleanup_stale_branches_all_projects`
    # is a pure re-export and would break outright; `cleanup_corrupted_agent_sessions`
    # is used by the hub's own code and would survive, but leaving it pointed at a
    # module that does not own it re-creates the coupling #2876 exists to remove.
    # One key each: the rewrite is global, and `cleanup_corrupted_agent_sessions`
    # appears twice in the registry.
    "agent.agent_session_queue.cleanup_corrupted_agent_sessions": (
        "agent.session_health.cleanup_corrupted_agent_sessions"
    ),
    "agent.agent_session_queue.cleanup_stale_branches_all_projects": (
        "agent.session_revival.cleanup_stale_branches_all_projects"
    ),
}

# Match a `callable:` line, capturing optional matched quotes, the dotted path,
# and any trailing comment. Line-anchored: reflections.yaml is hand-authored and
# `callable` is always at field position, never inside a multi-line string.
_CALLABLE_LINE_RE = re.compile(
    r"""^(?P<indent>[ \t]*)callable[ \t]*:[ \t]*"""
    r"""(?P<q>["']?)(?P<path>[A-Za-z_][A-Za-z0-9_.]*)(?P=q)"""
    r"""(?P<trail>[ \t]*(?:\#.*)?)$""",
    re.MULTILINE,
)


class MigrationError(RuntimeError):
    """Raised when the registry cannot be migrated safely."""


@dataclass
class CallableMigrationResult:
    """Outcome of a single file's migration pass."""

    rewrote: bool
    rewrites_count: int
    target: Path


def rewrite_callable_lines(text: str) -> tuple[str, int, set[str]]:
    """Rewrite every shim ``callable:`` line to its canonical module path.

    Preserves indentation, the original quoting style, and any trailing
    comment. Lines whose dotted path is not in :data:`CALLABLE_MIGRATIONS` are
    returned byte-for-byte unchanged, which makes the rewrite idempotent: a
    second pass finds nothing to do.

    Returns:
        ``(new_text, substitutions_performed, matched_shim_paths)``. The third
        element is the set of :data:`CALLABLE_MIGRATIONS` keys this file
        actually contained, which is what scopes the import check — see
        :func:`verify_targets_importable`.
    """
    n = 0
    matched: set[str] = set()

    def _sub(match: re.Match[str]) -> str:
        nonlocal n
        old = match.group("path")
        new = CALLABLE_MIGRATIONS.get(old)
        if new is None:
            return match.group(0)
        n += 1
        matched.add(old)
        q = match.group("q")
        return f"{match.group('indent')}callable: {q}{new}{q}{match.group('trail')}"

    return _CALLABLE_LINE_RE.sub(_sub, text), n, matched


def verify_targets_importable(matched: set[str]) -> None:
    """Import the migration targets for ``matched``, aborting if one does not resolve.

    The scheduler swallows resolution failures silently (see module docstring),
    so this is the only place a bad mapping gets caught loudly.

    Args:
        matched: The :data:`CALLABLE_MIGRATIONS` keys actually present in the
            file being rewritten. Scoping to these — rather than checking the
            whole table — keeps one migration family's broken target from
            blocking an unrelated family's safe migration.
    """
    for old, new in sorted((o, t) for o, t in CALLABLE_MIGRATIONS.items() if o in matched):
        module_path, _, attr = new.rpartition(".")
        try:
            module = importlib.import_module(module_path)
        except ImportError as e:
            raise MigrationError(
                f"migration target for {old!r} does not import: {new} ({e})"
            ) from e
        if not callable(getattr(module, attr, None)):
            raise MigrationError(f"migration target for {old!r} is not callable: {new}")


def migrate_yaml_callables(
    target: Path, *, dry_run: bool = False, verify: bool = True
) -> CallableMigrationResult:
    """Rewrite one ``reflections.yaml``'s shim callables in place, atomically.

    Args:
        target: Path to a ``reflections.yaml`` (vault copy or config copy).
        dry_run: Compute the rewrite but leave disk untouched.
        verify: Import-check the migration targets before writing.

    Returns:
        A :class:`CallableMigrationResult`.

    Raises:
        MigrationError: If the target is missing or a mapping does not resolve.
    """
    target = Path(target)
    if not target.exists():
        raise MigrationError(f"target YAML does not exist: {target}")

    original = target.read_text()
    rewritten, count, matched = rewrite_callable_lines(original)

    if count == 0:
        return CallableMigrationResult(rewrote=False, rewrites_count=0, target=target)

    if verify:
        verify_targets_importable(matched)

    if not dry_run:
        # Atomic temp write + rename: a concurrent load_registry() read sees
        # either the whole old file or the whole new one, never a torn read.
        tmp = target.with_suffix(target.suffix + ".callables.tmp")
        try:
            tmp.write_text(rewritten)
            os.replace(tmp, target)
        finally:
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass

    return CallableMigrationResult(rewrote=True, rewrites_count=count, target=target)


def default_targets() -> list[Path]:
    """Both registry copies: the iCloud vault original and the local materialization.

    Order matters only cosmetically. Missing paths are filtered by
    :func:`migrate_targets` — a fresh machine may have no vault, and a checkout
    that has never run ``/update`` may have no config copy.
    """
    targets: list[Path] = []
    env_path = os.environ.get("REFLECTIONS_YAML")
    if env_path:
        targets.append(Path(env_path).expanduser())
    # The vault original is unreadable from launchd (TCC blocks ~/Desktop), so
    # only reach for it outside the launchd context.
    if not os.environ.get("VALOR_LAUNCHD"):
        targets.append(Path.home() / "Desktop" / "Valor" / "reflections.yaml")
    targets.append(_REPO_ROOT / "config" / "reflections.yaml")

    # De-duplicate while preserving order (REFLECTIONS_YAML may name one of the
    # standard paths).
    seen: set[Path] = set()
    unique: list[Path] = []
    for t in targets:
        resolved = t.expanduser()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(resolved)
    return unique


def migrate_targets(targets: list[Path], *, dry_run: bool = False) -> list[CallableMigrationResult]:
    """Migrate every target that exists, skipping the ones that do not.

    A missing registry copy is not an error: machines legitimately have one,
    both, or (on a fresh checkout) neither.
    """
    results: list[CallableMigrationResult] = []
    for target in targets:
        target = Path(target)
        if not target.exists():
            continue
        results.append(migrate_yaml_callables(target, dry_run=dry_run))
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Migrate reflections.yaml callables off the agent.sustainability shim."
    )
    parser.add_argument(
        "--target",
        type=Path,
        action="append",
        default=None,
        help="Registry path to migrate (repeatable). Default: vault + config copies.",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Report changes without touching disk."
    )
    parser.add_argument(
        "--check-idempotent",
        action="store_true",
        help="Re-run after migrating and assert the second pass is a no-op.",
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit a machine-readable JSON status line."
    )
    args = parser.parse_args(argv)

    targets = args.target or default_targets()
    if not args.json:
        print(f"[migrate-callables] targets: {[str(t) for t in targets]}")

    try:
        results = migrate_targets(targets, dry_run=args.dry_run)
    except MigrationError as e:
        if args.json:
            print(
                _json.dumps({"rewrote": False, "rewrites_count": 0, "error": str(e)}),
                file=sys.stderr,
            )
        else:
            print(f"[migrate-callables] ABORT: {e}", file=sys.stderr)
        return 1

    total = sum(r.rewrites_count for r in results)
    rewrote = any(r.rewrote for r in results)

    if args.json:
        print(
            _json.dumps(
                {
                    "rewrote": rewrote,
                    "rewrites_count": total,
                    "targets": [str(r.target) for r in results if r.rewrote],
                }
            )
        )
    else:
        print(f"[migrate-callables] rewrote={rewrote} rewrites_count={total}")

    if args.check_idempotent and not args.dry_run:
        second = migrate_targets(targets, dry_run=True)
        remaining = sum(r.rewrites_count for r in second)
        if remaining:
            print(
                f"[migrate-callables] IDEMPOTENCE FAILURE: second pass still finds "
                f"{remaining} rewrite(s)",
                file=sys.stderr,
            )
            return 2
        print("[migrate-callables] idempotence OK (second pass is a no-op)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
