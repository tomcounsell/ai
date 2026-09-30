"""Hand cases from spike 05, in the tree's grammar, plus the segment rule.
Plan 02 task 2."""

import pytest

from schemas.capability import Capability, Refused, ceiling, issue, space_of, within


def cap(name, effect_class, scope) -> Capability:
    return Capability(name=name, effect_class=effect_class, scope=scope)


ROOT = frozenset(
    {cap("read", "read", "psyoptimal"), cap("write", "propose", "psyoptimal/repo/a")}
)


def test_examples_from_the_spike():
    assert issue(ROOT, frozenset({cap("write", "propose", "psyoptimal/repo/a/x")}))
    with pytest.raises(Refused):
        issue(ROOT, frozenset({cap("write", "act", "psyoptimal/repo/a")}))  # class
    with pytest.raises(Refused):
        issue(ROOT, frozenset({cap("write", "propose", "psyoptimal/repo")}))  # scope
    with pytest.raises(Refused):
        issue(ROOT, frozenset({cap("push_branch", "act", "psyoptimal")}))  # name
    with pytest.raises(Refused):
        issue(frozenset(), frozenset({cap("read", "read", "psyoptimal")}))  # empty


def test_relay_cannot_launder_a_wider_grant():
    c1 = issue(ROOT, frozenset({cap("write", "propose", "psyoptimal/repo/a/x")}))
    with pytest.raises(Refused):
        issue(c1, frozenset({cap("write", "propose", "psyoptimal/repo/a")}))


def test_space_is_the_first_segment_and_never_a_prefix_match():
    parent = frozenset({cap("read", "read", "psyoptimal")})
    assert issue(parent, frozenset({cap("read", "read", "psyoptimal/docs")}))
    with pytest.raises(Refused):
        issue(parent, frozenset({cap("read", "read", "psyoptimal-archive")}))
    with pytest.raises(Refused):
        issue(parent, frozenset({cap("read", "read", "personal")}))
    assert space_of(cap("read", "read", "psyoptimal/repo/a")) == "psyoptimal"
    assert space_of(cap("read", "read", "psyoptimal")) == "psyoptimal"


def test_ceiling_lowers_and_never_raises():
    caps = frozenset(
        {
            cap("read", "read", "psyoptimal"),
            cap("write", "act", "psyoptimal"),
            cap("bash", "propose", "psyoptimal"),
        }
    )
    lowered = ceiling(caps, "propose")
    assert lowered == frozenset(
        {
            cap("read", "read", "psyoptimal"),
            cap("write", "propose", "psyoptimal"),
            cap("bash", "propose", "psyoptimal"),
        }
    )
    assert within(lowered, caps)
    assert ceiling(caps, "act") == caps
    assert all(c.effect_class == "read" for c in ceiling(caps, "read"))
