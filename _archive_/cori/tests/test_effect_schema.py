"""The typed actions of seams §1.10: the key is the fields' and no
requester's, the target is what a manifest lists, and the payload hash moves
when any field does. Plan 07 task 1."""

import uuid
from pathlib import Path

import pytest
from pydantic import ValidationError

from schemas.effect import Action, ConnectorRead, EffectOutcome, PushBranch
from schemas.space import load_space

MANIFEST = Path(__file__).resolve().parents[1] / "infra" / "spaces" / "psyoptimal.yaml"


def push(**over) -> PushBranch:
    objective_id = over.pop("objective_id", uuid.uuid4().hex)
    fields = {
        "space": "psyoptimal",
        "objective_id": objective_id,
        "brief_id": uuid.uuid4().hex,
        "repo": "yudame/cori-sandbox",
        "branch": f"cori/{objective_id}",
        "source_dir": "/tmp/work",
        "head_sha": "a" * 40,
    }
    fields.update(over)
    return PushBranch(**fields)


def read(**over) -> ConnectorRead:
    fields = {
        "space": "psyoptimal",
        "objective_id": None,
        "brief_id": None,
        "connector": "gmail",
        "account": "tom@yuda.me",
        "query": "id:18f0c0ffee",
    }
    fields.update(over)
    return ConnectorRead(**fields)


# ---------------------------------------------------------------------------
# The key is derived, never supplied


def test_the_key_is_derived_from_the_fields():
    action = push()
    assert action.idempotency_key == f"{action.repo}#{action.branch}@{action.head_sha}"
    assert read().idempotency_key == "gmail:tom@yuda.me:id:18f0c0ffee"


def test_a_hand_set_key_that_differs_is_refused():
    with pytest.raises(ValidationError):
        push(idempotency_key="yudame/other#cori/x@" + "b" * 40)


def test_a_hand_set_key_that_matches_the_derivation_is_accepted():
    action = push()
    assert (
        push(
            objective_id=action.objective_id,
            brief_id=action.brief_id,
            branch=action.branch,
            idempotency_key=action.idempotency_key,
        ).idempotency_key
        == action.idempotency_key
    )


def test_a_kernel_read_may_append_the_poll_time():
    """The one widening: the poll time the kernel appends, so two polls with
    one unchanged `since` are two effects (plan 07, the Gmail connector)."""
    item = read()
    stamped = read(idempotency_key=item.idempotency_key + ":1758412800")
    assert stamped.idempotency_key.endswith(":1758412800")
    with pytest.raises(ValidationError):
        read(idempotency_key=item.idempotency_key + ":later")
    with pytest.raises(ValidationError):
        read(idempotency_key="gmail:someone-else@yuda.me:id:18f0c0ffee:1758412800")


# ---------------------------------------------------------------------------
# Targets, hashes, and the None shape


def test_targets_match_the_manifest():
    space = load_space(MANIFEST)
    assert push().target() == "github.com/yudame"
    assert read().target() == "mailto:tom@yuda.me"
    assert push().target() in space.allowed_targets
    assert read().target() in space.allowed_targets


def test_two_dumps_of_one_action_hash_equal_and_a_field_change_does_not():
    action = push()
    twin = PushBranch(**action.model_dump())
    assert action.payload_sha256() == twin.payload_sha256()
    moved = action.model_copy(
        update={
            "head_sha": "b" * 40,
            "idempotency_key": f"{action.repo}#{action.branch}@{'b' * 40}",
        }
    )
    assert moved.payload_sha256() != action.payload_sha256()


def test_a_connector_read_with_both_ids_none_round_trips():
    item = read()
    assert item.objective_id is None and item.brief_id is None
    assert ConnectorRead.model_validate(item.model_dump(mode="json")) == item


def test_the_class_and_type_are_class_variables():
    assert PushBranch.effect_class == "propose"
    assert PushBranch.action_type == "push_branch"
    assert ConnectorRead.effect_class == "read"
    assert ConnectorRead.action_type == "connector_read"
    assert "effect_class" not in PushBranch.model_fields
    assert "action_type" not in PushBranch.model_fields


def test_actions_forbid_extra_fields_and_are_frozen():
    action = push()
    with pytest.raises(ValidationError):
        push(surprise="no")
    with pytest.raises(ValidationError):
        action.branch = "cori/other"


def test_the_base_derivation_and_target_are_abstract():
    with pytest.raises(NotImplementedError):
        Action.derive_key(push())
    with pytest.raises(NotImplementedError):
        Action.target(push())


def test_an_outcome_carries_its_effect_id_and_kind():
    outcome = EffectOutcome(effect_id="e1", kind="done", result={"sha": "a" * 40})
    assert outcome.error is None
    assert EffectOutcome(effect_id="e2", kind="refused").result == {}
