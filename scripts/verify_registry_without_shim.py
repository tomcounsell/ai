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
registry, because `agent.reflection_scheduler._resolve_registry_path()` is
vault-first (`REFLECTIONS_YAML` env → `~/Desktop/Valor/reflections.yaml` →
`config/reflections.yaml`) and the probe must cover whichever file this machine
actually uses — not a repo copy that may not be the live one.

Exits 0 when every `callable:` entry imports. Exits non-zero on the first
failure, naming the offending entry.

Why this is committed rather than run ad hoc:
`tests/unit/test_reflection_scheduler.py::test_all_callables_resolve` resolves
the same vault-first registry, so it validates whatever file happens to sit on
the running machine and is not a real CI gate.
"""

from __future__ import annotations

import sys

BANNED_MODULE = "agent.sustainability"


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


def main() -> int:
    # Install the ban FIRST, before any import that could touch the registry.
    sys.meta_path.insert(0, _BannedModuleFinder())
    sys.modules.pop(BANNED_MODULE, None)

    import yaml

    from agent.reflection_scheduler import _resolve_callable, _resolve_registry_path

    registry_path = _resolve_registry_path()
    if not registry_path.exists():
        print(f"FAIL: reflections registry not found at {registry_path}", file=sys.stderr)
        return 1

    with open(registry_path) as fh:
        data = yaml.safe_load(fh) or {}

    entries = data.get("reflections") or []
    if not entries:
        print(f"FAIL: {registry_path} declares no reflections", file=sys.stderr)
        return 1

    checked = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        dotted = entry.get("callable")
        if not dotted:
            continue
        name = entry.get("name", "<unnamed>")
        try:
            _resolve_callable(dotted)
        except Exception as exc:
            print(
                f"FAIL: reflection {name!r} callable {dotted!r} did not resolve "
                f"with {BANNED_MODULE} banned: {exc!r}",
                file=sys.stderr,
            )
            return 1
        checked += 1

    print(
        f"OK: {checked} registry callables resolved with {BANNED_MODULE} banned ({registry_path})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
