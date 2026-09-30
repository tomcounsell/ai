"""Capability attenuation as a pure function.

A capability is (name, effect_class, scope). A set of capabilities is what a
Brief holds. ``issue(parent, requested)`` returns the capabilities a child may
hold, or refuses. The property under test: no chain of issues ever widens.

Widening means any of: a name the parent lacks, a higher effect class than
the parent's grant for that name, or a scope the parent's scope does not
contain. Scope is a path prefix ("" is everything, "repo/x" is that subtree).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, order=True)
class Cap:
    name: str
    effect_class: int  # 0..3
    scope: str  # path prefix; "" means unrestricted

    def covers(self, other: "Cap") -> bool:
        return (
            self.name == other.name
            and other.effect_class <= self.effect_class
            and other.scope.startswith(self.scope)
        )


class Refused(Exception):
    pass


def issue(parent: frozenset[Cap], requested: frozenset[Cap]) -> frozenset[Cap]:
    """Return ``requested`` if every requested cap is covered by some parent
    cap, else refuse. Never invents, never widens, never silently narrows: a
    request that cannot be met in full is refused so the caller knows."""
    for r in requested:
        if not any(p.covers(r) for p in parent):
            raise Refused(f"{r} not covered by parent")
    return requested


def attenuate(parent: frozenset[Cap], requested: frozenset[Cap]) -> frozenset[Cap]:
    """Alternative policy: clip each request to the parent's grant instead of
    refusing. Kept so the property test can show both stay within bounds."""
    out = set()
    for r in requested:
        for p in parent:
            if p.name == r.name and r.scope.startswith(p.scope):
                out.add(Cap(r.name, min(r.effect_class, p.effect_class), r.scope))
                break
    return frozenset(out)


def within(child: frozenset[Cap], parent: frozenset[Cap]) -> bool:
    return all(any(p.covers(c) for p in parent) for c in child)
