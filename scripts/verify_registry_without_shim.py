#!/usr/bin/env python3
"""Prove every reflections-registry callable resolves without `agent.sustainability`.

Issue #2875 deleted `agent/sustainability.py`, the compatibility shim whose only
remaining job was to keep the registry's historical `agent.sustainability.*`
dotted paths importable. PR #2944 migrated both registry copies onto
`reflections.agents.*`; this script is the standing proof that the migration is
complete on whatever machine runs it.

How it proves it: a `sys.meta_path` finder is installed that *raises* for
`fullname == "agent.sustainability"`, so a registry entry that still names the
shim fails loudly instead of silently resolving through a stale `.pyc` or a
leftover working-tree copy. The finder goes in **before** anything resolves the
registry.

**Every registry copy is checked, not just the live one.** A single process
cannot see what the scheduler sees, because
`agent.reflection_scheduler._resolve_registry_path()` returns exactly ONE path
out of four candidates, and which one depends on the environment:

1. `REFLECTIONS_YAML` (explicit override), if it exists.
2. `~/Desktop/Valor/reflections.yaml` — the iCloud vault, **skipped entirely
   under `VALOR_LAUNCHD`** (macOS TCC blocks `~/Desktop` from launchd agents,
   where even `exists()` hangs).
3. `config/reflections.yaml` in *this* checkout — gitignored, materialized at
   install time, so it is absent in worktrees.
4. `config/reflections.yaml` in the checkout that *owns* this worktree, located
   via `_owning_checkout_root()`. This is the level that makes the registry
   readable from `.worktrees/{slug}/` under `VALOR_LAUNCHD`.

Run by hand, the probe would land on the vault; the live scheduler runs under
`VALOR_LAUNCHD=1` and lands on a `config/` copy. One resolution therefore proves
nothing about the other. So this script enumerates every candidate (the same
enumeration `scripts/migrate_reflections_callables.py::default_targets()`
rewrites, plus the owning-checkout level) and requires every copy that EXISTS to
resolve cleanly, reporting per file.

Exits 0 when every `callable:` entry in every existing copy imports. Every copy
is examined even after one fails, so a single run reports the whole verdict;
the exit is non-zero if any copy failed, naming each offending file and entry —
and a copy that EXISTS but declares no `callable:` entries counts as a failure,
since an empty check of a real registry is a silent pass that proves nothing.

"No registry copy exists at all" is a different thing and exits 0 with a
warning. A machine that has not been installed yet has no callable that could
contradict the claim, and failing there would block `/update`'s restart on a
condition this probe has no evidence for — with no self-clearing path, which is
the permanent-wedge shape this gate exists to avoid causing.

Why this is committed rather than run ad hoc:
`tests/unit/test_reflection_scheduler.py::test_all_callables_resolve` resolves
the same vault-first registry, so it validates whatever file happens to sit on
the running machine and is not a real CI gate. This script IS the gate:
`scripts/update/run.py` Step 4.65 runs it via
`scripts.update.reflections_callables.run_registry_probe` and suppresses the
service restart when it fails.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

BANNED_MODULE = "agent.sustainability"

_REPO_ROOT = Path(__file__).resolve().parent.parent


class _BannedModuleFinder:
    """A `sys.meta_path` finder that raises for the banned module.

    Raising (rather than returning ``None``) is deliberate: returning ``None``
    would just defer to the next finder, which would happily import the shim if
    a copy were still on disk. Raising makes the ban unconditional.
    """

    def find_spec(self, fullname, path=None, target=None):  # noqa: D102 - protocol method
        if fullname == BANNED_MODULE or fullname.startswith(BANNED_MODULE + "."):
            raise ImportError(
                f"{fullname} is banned: the compatibility shim was deleted in #2875. "
                "A registry callable still names it."
            )
        return None


def _registry_copies(owning_root: Path | None) -> list[Path]:
    """Every registry copy this machine could resolve, de-duplicated, in order.

    Mirrors ``scripts/migrate_reflections_callables.py::default_targets()`` —
    the same set that gets rewritten — plus level 4 of
    ``_resolve_registry_path()`` (the owning checkout's install-time copy), which
    is the only copy visible from a worktree under ``VALOR_LAUNCHD``.

    Missing paths stay in the list; the caller filters them, because "absent" is
    legitimate (a fresh machine has no vault; a worktree has no local config).
    """
    candidates: list[Path] = []

    env_path = os.environ.get("REFLECTIONS_YAML")
    if env_path:
        candidates.append(Path(env_path).expanduser())
    # The vault is unreadable from launchd (TCC blocks ~/Desktop), and the
    # scheduler skips it there — so the probe skips it under the same condition.
    if not os.environ.get("VALOR_LAUNCHD"):
        candidates.append(Path.home() / "Desktop" / "Valor" / "reflections.yaml")
    candidates.append(_REPO_ROOT / "config" / "reflections.yaml")
    if owning_root is not None:
        candidates.append(owning_root / "config" / "reflections.yaml")

    seen: set[Path] = set()
    unique: list[Path] = []
    for c in candidates:
        resolved = c.expanduser()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(resolved)
    return unique


def _check_copy(
    registry_path: Path,
    yaml_mod: ModuleType,
    resolve_callable: Callable[[str], object],
) -> int | None:
    """Verify one registry copy. Returns the number of callables checked, or None on failure.

    ``yaml_mod`` and ``resolve_callable`` are injected rather than imported at
    module scope because both imports must happen AFTER ``main()`` installs the
    ``_BannedModuleFinder`` — importing ``agent.reflection_scheduler`` up here
    would resolve the registry's import graph while the ban is still unarmed,
    which is precisely the hole this script exists to close. Keeping them as
    parameters makes that ordering a signature-level fact instead of a comment.

    ``None`` rather than a ``-1`` sentinel: the success value is a *count*, and
    a caller that summed a sentinel would silently fold a failure in as ``-1``.
    """
    try:
        with open(registry_path) as fh:
            data = yaml_mod.safe_load(fh) or {}
    except Exception as exc:
        # An unreadable or malformed copy is a failure in the same shape as the
        # others, not a traceback: a TCC denial or an iCloud dataless
        # placeholder would otherwise abort the whole run and hide the verdict
        # on every copy after this one.
        print(f"FAIL: {registry_path}: could not read registry: {exc!r}", file=sys.stderr)
        return None

    if not isinstance(data, dict):
        print(
            f"FAIL: {registry_path}: registry is {type(data).__name__}, expected a mapping",
            file=sys.stderr,
        )
        return None

    entries = data.get("reflections") or []
    if not entries:
        print(f"FAIL: {registry_path} declares no reflections", file=sys.stderr)
        return None

    checked = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        dotted = entry.get("callable")
        if not dotted:
            continue
        name = entry.get("name", "<unnamed>")
        try:
            resolve_callable(dotted)
        except Exception as exc:
            print(
                f"FAIL: {registry_path}: reflection {name!r} callable {dotted!r} did not "
                f"resolve with {BANNED_MODULE} banned: {exc!r}",
                file=sys.stderr,
            )
            return None
        checked += 1

    if checked == 0:
        # A registry whose entries carry no `callable:` keys would otherwise
        # print "OK: 0 ..." and exit 0 — a silent pass that proves nothing.
        print(
            f"FAIL: {registry_path} declares {len(entries)} reflection(s) but no "
            f"`callable:` entries, so nothing was proven about {BANNED_MODULE}",
            file=sys.stderr,
        )
        return None

    return checked


def main() -> int:
    # Install the ban FIRST, before any import that could touch the registry.
    sys.meta_path.insert(0, _BannedModuleFinder())
    sys.modules.pop(BANNED_MODULE, None)

    # Repo root on sys.path so `import agent.*` works regardless of CWD and
    # without depending on the venv's editable-install `.pth`. Deliberately
    # AFTER the ban: the finder must be armed before any import is possible.
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))

    import yaml

    from agent.reflection_scheduler import (
        _owning_checkout_root,
        _resolve_callable,
    )

    copies = _registry_copies(_owning_checkout_root())
    existing = [p for p in copies if p.exists()]
    if not existing:
        # Not a failure. This probe answers one question — "does every
        # `callable:` still import?" — and with no registry there is no
        # callable to contradict it. Failing here would gate `/update`'s
        # service restart on a condition the probe has no evidence for, and
        # since the sentinel has no self-clearing path that wedges the cycle
        # every 30 minutes on a checkout that simply has not been installed
        # yet: `config/reflections.yaml` is gitignored, `env_sync` has no
        # in-repo fallback, and the vault is not a candidate under
        # VALOR_LAUNCHD. Warn loudly and let the caller decide.
        print(
            "WARN: no reflections registry found, so nothing was probed; candidates were "
            + ", ".join(str(p) for p in copies),
            file=sys.stderr,
        )
        print(f"OK: no registry present; {BANNED_MODULE} import ban vacuously holds")
        return 0

    # Every copy is examined even after one fails. An operator fixing a
    # multi-machine registry drift needs the whole verdict in one run, and it is
    # what makes `_check_copy`'s "would otherwise hide the verdict on every copy
    # after this one" rationale for catching read errors actually true.
    total = 0
    failed: list[Path] = []
    for registry_path in existing:
        checked = _check_copy(registry_path, yaml, _resolve_callable)
        if checked is None:
            failed.append(registry_path)
            continue
        print(f"  OK: {checked} callables resolved in {registry_path}")
        total += checked

    if failed:
        print(
            f"FAIL: {len(failed)} of {len(existing)} registry copy(ies) did not resolve: "
            + ", ".join(str(p) for p in failed),
            file=sys.stderr,
        )
        return 1

    skipped = [p for p in copies if not p.exists()]
    if skipped:
        print(f"  (absent, not checked: {', '.join(str(p) for p in skipped)})")
    print(
        f"OK: {total} registry callables across {len(existing)} registry copy(ies) "
        f"resolved with {BANNED_MODULE} banned"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
