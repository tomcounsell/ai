"""Capabilities and attenuation. Seams §1.2; architecture §3.1; spike 05, lifted.

A capability is (name, effect class, scope). Subset is the covers relation on
the three, with the space on the scope axis: a scope always begins with a
space id, so no capability covers a scope in another space. `issue` returns
the request unchanged or refuses; it never clips (spike 05, refuse over clip).
`ceiling` is how the kernel derives a node's set from the space's root set,
never an answer to a request.

Decided in the plan: `covers` compares scope by whole path segment, so
`psyoptimal` never covers `psyoptimal-archive`; the spike's bare
`startswith` would.
"""

from typing import Literal

from schemas.ids import SpaceId
from schemas.space import EFFECT_RANK, EffectClass, Strict

CapabilityName = Literal[
    "read",
    "write",
    "bash",
    "ask",  # the tool bridges, tech stack §5
    "delegate",  # spawn, architecture §3
    "push_branch",  # the `propose` typed action, tech stack §7
    "connector.read",  # a `read` through the broker, architecture §9
    "memory.episodic.write",
    "memory.operator.propose",  # the memory family, architecture §3.5
]


class Capability(Strict, frozen=True):
    name: CapabilityName
    effect_class: EffectClass  # the highest class the holder may exercise
    scope: str  # "<space_id>" or "<space_id>/<path>"

    def covers(self, other: "Capability") -> bool:
        return (
            self.name == other.name
            and EFFECT_RANK[other.effect_class] <= EFFECT_RANK[self.effect_class]
            and (other.scope == self.scope or other.scope.startswith(self.scope + "/"))
        )


Capabilities = frozenset[Capability]


class Refused(Exception):
    """A request the issuer cannot meet in full."""


def issue(parent: Capabilities, requested: Capabilities) -> Capabilities:
    """`requested` unchanged when every element is covered by some parent
    element, else `Refused`. Never clips, never invents."""
    for r in requested:
        if not any(p.covers(r) for p in parent):
            raise Refused(f"{r.name}@{r.effect_class} on {r.scope!r} is not covered")
    return requested


def within(child: Capabilities, parent: Capabilities) -> bool:
    return all(any(p.covers(c) for p in parent) for c in child)


def ceiling(caps: Capabilities, effect_class: EffectClass) -> Capabilities:
    """Lowers every element's class to the ceiling. How the kernel derives a
    node's set from the space's, never an answer to a request."""
    limit = EFFECT_RANK[effect_class]
    return frozenset(
        (
            cap
            if EFFECT_RANK[cap.effect_class] <= limit
            else cap.model_copy(update={"effect_class": effect_class})
        )
        for cap in caps
    )


def space_of(cap: Capability) -> SpaceId:
    """The first scope segment."""
    return cap.scope.split("/", 1)[0]
