"""The four spike 05 properties with the space axis added, plus two of the
tree's own. Plan 02 task 2 and Properties; 2,000 examples each."""

from typing import get_args

from hypothesis import given, settings, strategies as st

from schemas.capability import (
    Capability,
    CapabilityName,
    Refused,
    ceiling,
    issue,
    space_of,
    within,
)
from schemas.space import EFFECT_RANK, EffectClass

SPACES = ["psyoptimal", "personal"]
SEGMENTS = ["a", "b"]

names = st.sampled_from(get_args(CapabilityName))
classes = st.sampled_from(get_args(EffectClass))


def _scopes(space_ids):
    return st.builds(
        lambda s, parts: "/".join([s, *parts]),
        st.sampled_from(space_ids),
        st.lists(st.sampled_from(SEGMENTS), max_size=2),
    )


def _caps(space_ids):
    return st.builds(
        Capability, name=names, effect_class=classes, scope=_scopes(space_ids)
    )


capsets = st.frozensets(_caps(SPACES), max_size=6)
single_space_capsets = st.frozensets(_caps(SPACES[:1]), max_size=6)
other_space_capsets = st.frozensets(_caps(SPACES[1:]), min_size=1, max_size=6)


@settings(max_examples=2000, deadline=None)
@given(parent=capsets, requested=capsets)
def test_issue_never_widens(parent, requested):
    try:
        child = issue(parent, requested)
    except Refused:
        return
    assert within(child, parent)


@settings(max_examples=2000, deadline=None)
@given(parent=capsets, requested=capsets)
def test_issue_is_all_or_nothing(parent, requested):
    try:
        assert issue(parent, requested) == requested
    except Refused:
        assert not within(requested, parent)


def _highest(caps) -> EffectClass:
    return max(
        (c.effect_class for c in caps), key=lambda e: EFFECT_RANK[e], default="read"
    )


@settings(max_examples=2000, deadline=None)
@given(root=capsets, reqs=st.lists(capsets, min_size=3, max_size=3))
def test_relay_through_three_children_never_widens(root, reqs):
    """root -> c1 -> c2 -> c3 by issue, falling back to ceiling at the
    parent's highest class on refusal; every later holder is within every
    earlier one."""
    holder = root
    chain = [root]
    for r in reqs:
        try:
            holder = issue(holder, r)
        except Refused:
            holder = ceiling(holder, _highest(holder))
        chain.append(holder)
    for i in range(1, len(chain)):
        for j in range(i):
            assert within(chain[i], chain[j])


@settings(max_examples=2000, deadline=None)
@given(
    parent=single_space_capsets,
    requested=capsets,
    foreign=other_space_capsets,
)
def test_scope_never_leaves_its_space(parent, requested, foreign):
    try:
        child = issue(parent, requested)
    except Refused:
        child = None
    if child is not None:
        for c in child:
            coverers = [p for p in parent if p.covers(c)]
            assert coverers and all(space_of(p) == space_of(c) for p in coverers)
    try:
        issue(parent, foreign)
    except Refused:
        pass
    else:
        raise AssertionError("a single-space parent issued a foreign scope")


@settings(max_examples=2000, deadline=None)
@given(caps=capsets, c=classes)
def test_ceiling_is_within(caps, c):
    lowered = ceiling(caps, c)
    assert within(lowered, caps)
    assert all(EFFECT_RANK[x.effect_class] <= EFFECT_RANK[c] for x in lowered)
    assert ceiling(caps, "act") == caps
