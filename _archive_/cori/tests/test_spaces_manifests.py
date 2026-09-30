"""Manifests load or are refused with a reason, a space's root capability
set is every name at its ceiling, and no root set reaches another space.
Plan 03 task 3; seams §3.4, §1.2."""

from pathlib import Path
from typing import get_args

import pytest
import yaml
from hypothesis import assume, given, settings, strategies as st

from kernel.spaces import (
    SPACES_DIR,
    SpaceRefused,
    check_may_open,
    load_all,
    root_capabilities,
)
from schemas.capability import CapabilityName, Refused, issue, space_of, within
from schemas.space import EFFECT_RANK, UNASSIGNED_SPACE_ID, Space

NAMES = get_args(CapabilityName)
CLASSES = tuple(EFFECT_RANK)

MANIFEST = {
    "kind": "client",
    "roots": ["github.com/yudame/example"],
    "max_effect_class": "propose",
    "connectors": [
        {
            "kind": "gmail",
            "account": "tom@yuda.me",
            "route": {"sender_domain": "example.com"},
        }
    ],
}


def write(directory: Path, name: str, **over) -> Path:
    path = directory / f"{name}.yaml"
    path.write_text(yaml.safe_dump({"id": name, **MANIFEST, **over}))
    return path


def make(space_id: str, *, max_effect_class="propose") -> Space:
    return Space(
        id=space_id,
        kind="client",
        roots=["/repo"],
        max_effect_class=max_effect_class,
    )


# ---------------------------------------------------------------------------
# load_all


def test_load_all_finds_psyoptimal():
    spaces = load_all()
    assert SPACES_DIR == Path(__file__).parents[1] / "infra" / "spaces"
    assert set(spaces) == {"psyoptimal"}
    assert spaces["psyoptimal"].max_effect_class == "propose"
    assert spaces["psyoptimal"].connectors[0].route.sender_domain == "psyoptimal.com"


def test_duplicate_ids_refused(tmp_path):
    write(tmp_path, "alpha")
    # A second file claiming the same id, distinguished by its rule so the
    # duplicate id is the only collision.
    (tmp_path / "alpha-copy.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "alpha",
                **MANIFEST,
                "connectors": [
                    {
                        "kind": "gmail",
                        "account": "other@yuda.me",
                        "route": {"sender_domain": "other.com"},
                    }
                ],
            }
        )
    )
    with pytest.raises(SpaceRefused, match="claimed by both"):
        load_all(tmp_path)


def test_reserved_id_refused(tmp_path):
    (tmp_path / "unassigned.yaml").write_text(
        yaml.safe_dump({"id": UNASSIGNED_SPACE_ID, **MANIFEST})
    )
    with pytest.raises(SpaceRefused, match="not a valid manifest"):
        load_all(tmp_path)


def test_identical_rules_across_spaces_refused(tmp_path):
    write(tmp_path, "alpha")
    write(tmp_path, "beta")
    with pytest.raises(SpaceRefused, match="same gmail rule"):
        load_all(tmp_path)


@pytest.mark.parametrize("route", [{}, {"label": "clients"}])
def test_rule_without_sender_domain_refused(tmp_path, route):
    write(
        tmp_path,
        "alpha",
        connectors=[{"kind": "gmail", "account": "tom@yuda.me", "route": route}],
    )
    with pytest.raises(SpaceRefused, match="no sender_domain"):
        load_all(tmp_path)


# ---------------------------------------------------------------------------
# root_capabilities


def test_root_capabilities_are_every_name_at_the_ceiling():
    caps = root_capabilities(make("alpha", max_effect_class="propose"))
    assert {c.name for c in caps} == set(NAMES)
    assert {c.effect_class for c in caps} == {"propose"}
    assert {c.scope for c in caps} == {"alpha"}
    assert len(caps) == len(NAMES)


@settings(max_examples=25, deadline=None)
@given(
    a_id=st.from_regex(r"\A[a-z0-9][a-z0-9-]{0,12}\Z"),
    b_id=st.from_regex(r"\A[a-z0-9][a-z0-9-]{0,12}\Z"),
    a_class=st.sampled_from(CLASSES),
    b_class=st.sampled_from(CLASSES),
    names=st.lists(st.sampled_from(NAMES), min_size=1, unique=True),
)
def test_root_capabilities_never_cover_another_space(
    a_id, b_id, a_class, b_class, names
):
    assume(a_id != b_id)
    assume(UNASSIGNED_SPACE_ID not in (a_id, b_id))
    a, b = make(a_id, max_effect_class=a_class), make(b_id, max_effect_class=b_class)
    caps_a, caps_b = root_capabilities(a), root_capabilities(b)

    assert all(c.scope == a_id and c.effect_class == a_class for c in caps_a)
    assert all(space_of(c) == a_id for c in caps_a)
    assert not within(caps_b, caps_a)

    requested = frozenset(
        c for c in caps_b if c.name in names
    )  # non-empty, scoped to b
    assert requested
    with pytest.raises(Refused):
        issue(caps_a, requested)


# ---------------------------------------------------------------------------
# check_may_open


def test_unassigned_refuses_objectives():
    with pytest.raises(SpaceRefused, match=UNASSIGNED_SPACE_ID):
        check_may_open(UNASSIGNED_SPACE_ID, {"psyoptimal": make("psyoptimal")})


def test_unknown_space_refuses_objectives():
    with pytest.raises(SpaceRefused, match="no manifest"):
        check_may_open("acme", {"psyoptimal": make("psyoptimal")})


def test_psyoptimal_may_open():
    assert check_may_open("psyoptimal", load_all()) is None
