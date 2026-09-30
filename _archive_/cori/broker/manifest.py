"""The space manifest a check reads. Plan 07 task 4; seams §1.5, §3.10.

The broker asks the manifest two questions before it touches a target: what
the space's effect ceiling is, and whether the target is one the space is
allowed to reach. Both answers live in `infra/spaces/<id>.yaml` and are
loaded through `schemas.space.load_space`, so the file's shape is the
schema's business and not the broker's.

Read at use rather than cached, because a manifest is edited by a person and
a long-running kernel should not hold a stale ceiling. The file is small and
the read happens once per effect.
"""

from __future__ import annotations

from pathlib import Path

from broker.errors import EffectRefused
from schemas.ids import SpaceId
from schemas.space import Connector, Space, load_space

__all__ = ["MANIFEST_DIR", "space_for", "space_for_connector"]

MANIFEST_DIR = Path(__file__).resolve().parents[1] / "infra" / "spaces"


def space_for(space_id: SpaceId) -> Space:
    """The manifest for this space, or `EffectRefused` if there is none.

    A space id with no manifest is a refusal rather than a crash: the broker
    cannot know a ceiling or a target list for it, and the safe reading of an
    unknown space is that nothing is allowed to leave it.
    """
    path = MANIFEST_DIR / f"{space_id}.yaml"
    if not path.is_file():
        raise EffectRefused(f"no manifest for space {space_id!r}")
    space = load_space(path)
    if space.id != space_id:
        raise EffectRefused(
            f"manifest {path.name} declares space {space.id!r}, not {space_id!r}"
        )
    return space


def space_for_connector(connector: Connector) -> Space:
    """The space whose manifest declares this exact connector.

    The kernel's own reads are handed a `Connector` and no space, but a
    ledger row carries `space_id` and a credential is keyed by the pair, so
    the read has to name the space that owns the rule it is about to run.
    `kernel.spaces.load_all` refuses the same `(kind, account, route)` in two
    manifests, so a connector that is declared at all is declared once.
    """
    owners = [
        space
        for path in sorted(MANIFEST_DIR.glob("*.yaml"))
        for space in [load_space(path)]
        if connector in space.connectors
    ]
    if not owners:
        raise EffectRefused(
            f"no manifest declares a {connector.kind} connector on "
            f"{connector.account!r} with this rule"
        )
    if len(owners) > 1:
        raise EffectRefused(
            f"{connector.kind} on {connector.account!r} is declared by "
            f"{', '.join(sorted(s.id for s in owners))}"
        )
    return owners[0]
