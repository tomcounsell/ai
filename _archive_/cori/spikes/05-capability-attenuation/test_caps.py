import pytest
from hypothesis import given, settings, strategies as st

from caps import Cap, Refused, attenuate, issue, within

names = st.sampled_from(["read", "write", "bash", "network", "open_pr", "merge"])
scopes = st.sampled_from(["", "repo", "repo/a", "repo/a/x", "repo/b", "notes"])
caps = st.builds(Cap, name=names, effect_class=st.integers(0, 3), scope=scopes)
capsets = st.frozensets(caps, max_size=6)


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
def test_attenuate_never_widens(parent, requested):
    assert within(attenuate(parent, requested), parent)


@settings(max_examples=2000, deadline=None)
@given(root=capsets, reqs=st.lists(capsets, min_size=3, max_size=3))
def test_relay_through_three_children_never_widens(root, reqs):
    """root -> c1 -> c2 -> c3: every link issues from the previous holder.
    Whatever c3 ends up with must be within root, not just within c2."""
    holder = root
    chain = [root]
    for r in reqs:
        try:
            holder = issue(holder, r)
        except Refused:
            holder = attenuate(holder, r)  # a refused child falls back to clipping
        chain.append(holder)
    for i in range(1, len(chain)):
        for j in range(i):
            assert within(chain[i], chain[j])


@settings(max_examples=2000, deadline=None)
@given(parent=capsets, requested=capsets)
def test_issue_is_all_or_nothing(parent, requested):
    """issue() either returns exactly what was asked or refuses; it never
    returns a silently narrowed set the caller might mistake for the ask."""
    try:
        assert issue(parent, requested) == requested
    except Refused:
        assert not within(requested, parent)


def test_examples():
    root = frozenset({Cap("read", 0, ""), Cap("write", 1, "repo/a")})
    assert issue(root, frozenset({Cap("write", 1, "repo/a/x")}))
    with pytest.raises(Refused):
        issue(root, frozenset({Cap("write", 2, "repo/a")}))  # class up
    with pytest.raises(Refused):
        issue(root, frozenset({Cap("write", 1, "repo")}))  # scope out
    with pytest.raises(Refused):
        issue(root, frozenset({Cap("merge", 3, "")}))  # name absent
    with pytest.raises(Refused):
        issue(frozenset(), frozenset({Cap("read", 0, "")}))  # empty issuer

    # Relay: a child cannot launder a wider grant through a grandchild.
    c1 = issue(root, frozenset({Cap("write", 1, "repo/a/x")}))
    with pytest.raises(Refused):
        issue(c1, frozenset({Cap("write", 1, "repo/a")}))
