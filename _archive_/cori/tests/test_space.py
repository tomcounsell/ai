"""The first manifest parses against the first schema. Architecture §9."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from schemas.space import EFFECT_RANK, UNASSIGNED_SPACE_ID, Audience, Space, load_space

MANIFEST = Path(__file__).parents[1] / "infra" / "spaces" / "psyoptimal.yaml"


def test_psyoptimal_manifest_parses():
    space = load_space(MANIFEST)
    assert space.id == "psyoptimal" and space.kind == "client"
    assert space.max_effect_class == "propose"
    assert space.connectors[0].route.sender_domain == "psyoptimal.com"
    assert space.connectors[0].account == "tom@yuda.me"
    assert "github.com/yudame" in space.allowed_targets


def test_unknown_fields_are_refused():
    data = load_space(MANIFEST).model_dump()
    data["provider_allowlist"] = ["anthropic"]
    with pytest.raises(ValidationError):
        Space.model_validate(data)


def test_manifest_carries_no_budget():
    # Architecture §4 (2026-09-20): the budget is per objective, never on the space.
    data = load_space(MANIFEST).model_dump()
    data["standing_budget"] = {"usd_per_day": 20}
    with pytest.raises(ValidationError):
        Space.model_validate(data)


def test_effect_class_is_named():
    data = load_space(MANIFEST).model_dump()
    for bad in (2, "merge", "READ"):
        data["max_effect_class"] = bad
        with pytest.raises(ValidationError):
            Space.model_validate(data)
    for good in ("read", "propose", "act"):
        data["max_effect_class"] = good
        assert Space.model_validate(data).max_effect_class == good


def test_effect_rank_orders_the_three_classes():
    assert EFFECT_RANK["read"] < EFFECT_RANK["propose"] < EFFECT_RANK["act"]
    assert set(EFFECT_RANK) == {"read", "propose", "act"}


def test_unassigned_id_is_refused():
    # Seams §0: the reserved space has no manifest, so a file claiming the id
    # fails at parse rather than at the first read.
    data = load_space(MANIFEST).model_dump()
    data["id"] = UNASSIGNED_SPACE_ID
    with pytest.raises(ValidationError):
        Space.model_validate(data)


def test_space_id_charset_is_restricted():
    # The id is the first segment of every capability scope and the first
    # field of every rule text, so `/` and `:` are out.
    data = load_space(MANIFEST).model_dump()
    for bad in ("a/b", "a:b", "A", ""):
        data["id"] = bad
        with pytest.raises(ValidationError):
            Space.model_validate(data)
    for good in ("psyoptimal", "my-space-2"):
        data["id"] = good
        assert Space.model_validate(data).id == good


def test_audience_defaults_empty():
    audience = Audience()
    assert audience.addresses == [] and audience.domains == []
    assert audience.source is None
    data = load_space(MANIFEST).model_dump()
    del data["audience"]
    assert Space.model_validate(data).audience == Audience()


def test_psyoptimal_audience_parses():
    audience = load_space(MANIFEST).audience
    assert audience.domains == ["psyoptimal.com"]
    assert audience.addresses == []
    assert audience.source == "~/work-vault/PsyOptimal/README.md#contacts"
