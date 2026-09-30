"""`schemas/approval.py` is seams §1.12 with the two validators. Plan 10 task 1.

The last test reads the seams' own code block and holds the module to it, so
a field added or dropped here without a seam change fails rather than drifts.
The two `text` fields are the Round two additions the lead accepted
(`Reply.text` and `ApprovalRecord.text`), and are the only fields the module
carries beyond the block.
"""

import ast
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import get_args

import pytest
from pydantic import ValidationError

import schemas.approval as approval
from schemas.approval import (
    CARD_FIELDS,
    OPTIONAL_CARD_FIELDS,
    Card,
    Option,
    Reply,
    render_value,
)
from schemas.budget import Budget

SEAMS = Path(__file__).resolve().parents[1] / "docs" / "plans" / "00-seams.md"

# Accepted in seams Round two, 2026-09-20, after the §1.12 block was written.
ROUND_TWO_ADDITIONS = {"Reply": {"text"}, "ApprovalRecord": {"text"}}

NOW = datetime(2026, 9, 20, 9, 0, tzinfo=UTC)


def card(**over) -> Card:
    fields = dict(
        id="card-1",
        kind="question",
        space="psyoptimal",
        conversation_id="conv-1",
        regards="obj-1",
        contract_revision=1,
        fields={"brief_id": "b1", "question_id": "q1", "question": "who?"},
        options=[
            Option(key="answer", label="Answer the worker", recommended=True),
            Option(key="abort", label="Abort the worker"),
        ],
        note="",
        expires_at=NOW + timedelta(hours=1),
        issued_at=NOW,
    )
    fields.update(over)
    return Card(**fields)


def reply(**over) -> Reply:
    fields = dict(
        card_id="card-1",
        option="answer",
        edit=None,
        raw_message="#1 answer Tom",
        session_id="s1",
        at=NOW,
    )
    fields.update(over)
    return Reply(**fields)


def test_card_requires_exactly_one_recommendation():
    assert card().options[0].recommended is True
    with pytest.raises(ValidationError):
        card(
            options=[
                Option(key="answer", label="a"),
                Option(key="abort", label="b"),
            ]
        )
    with pytest.raises(ValidationError):
        card(
            options=[
                Option(key="answer", label="a", recommended=True),
                Option(key="abort", label="b", recommended=True),
            ]
        )


def test_card_requires_kind_fields():
    # A missing required key fails.
    with pytest.raises(ValidationError):
        card(fields={"brief_id": "b1", "question_id": "q1"})
    # A key outside the optional set fails.
    with pytest.raises(ValidationError):
        card(
            fields={
                "brief_id": "b1",
                "question_id": "q1",
                "question": "who?",
                "extra": "no",
            }
        )
    # `argument_sha256` on a commit card passes; on any other kind it does not.
    commit_fields = {key: "x" for key in CARD_FIELDS["commit"]}
    commit = card(
        kind="commit",
        fields=commit_fields | {"argument_sha256": "0" * 64},
        options=[
            Option(key="approve", label="Approve", recommended=True),
            Option(key="reject", label="Reject"),
        ],
    )
    assert commit.fields["argument_sha256"] == "0" * 64
    with pytest.raises(ValidationError):
        card(
            fields={
                "brief_id": "b1",
                "question_id": "q1",
                "question": "who?",
                "argument_sha256": "0" * 64,
            }
        )


def test_kinds_without_constructors_do_not_validate_at_m0():
    for kind in ("scope_change", "effect_class_elevation", "ethics_flag"):
        with pytest.raises(ValidationError):
            card(kind=kind, fields={})


def test_reply_edit_only_with_edit_option():
    assert reply().edit is None
    assert reply(option="edit", edit={"premise": "narrower"}).edit is not None
    with pytest.raises(ValidationError):
        reply(option="approve", edit={"premise": "narrower"})


def test_reply_text_defaults_to_none():
    assert reply().text is None
    assert reply(text="Tom").text == "Tom"


def test_render_value_fixes_one_form_per_value():
    assert render_value("plain") == "plain"
    assert render_value(Budget(usd_micros=12_500_000)) == "$12.50"
    assert render_value(Budget(usd_micros=0)) == "$0.00"
    assert render_value(["b", "a"]) == '["b","a"]'
    assert render_value({"b": 1, "a": 2}) == '{"a":2,"b":1}'
    assert render_value(NOW) == '"2026-09-20T09:00:00Z"'


def test_optional_card_fields_cover_every_kind():
    assert set(OPTIONAL_CARD_FIELDS) == set(CARD_FIELDS)
    assert OPTIONAL_CARD_FIELDS["commit"] == ("argument_sha256",)
    assert all(v == () for k, v in OPTIONAL_CARD_FIELDS.items() if k != "commit")


# ---------------------------------------------------------------------------
# The module against the seams' own text


def seams_section_1_12() -> str:
    text = SEAMS.read_text()
    start = text.index("### 1.12 `schemas/approval.py`")
    end = text.index("\n### 1.13", start)
    block = re.search(r"```python\n(.*?)```", text[start:end], re.DOTALL)
    assert block, "no python block parsed from seams §1.12"
    return block.group(1)


def seams_classes() -> dict[str, list[str]]:
    tree = ast.parse(seams_section_1_12())
    classes: dict[str, list[str]] = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            classes[node.name] = [
                stmt.target.id
                for stmt in node.body
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
            ]
    assert classes, "no classes parsed from seams §1.12"
    return classes


def test_every_seams_field_present_and_no_extra():
    classes = seams_classes()
    assert set(classes) == {
        "Conversation",
        "Option",
        "Card",
        "Reply",
        "Utterance",
        "Correction",
        "SpaceSwitch",
        "ApprovalRecord",
    }
    for name, seam_fields in classes.items():
        model = getattr(approval, name)
        built = set(model.model_fields)
        assert (
            set(seam_fields) <= built
        ), f"{name} is missing {set(seam_fields) - built}"
        extra = built - set(seam_fields)
        assert extra == ROUND_TWO_ADDITIONS.get(
            name, set()
        ), f"{name} carries {extra} beyond seams §1.12 and Round two"


def test_every_seams_field_is_ordered_as_the_block_writes_it():
    for name, seam_fields in seams_classes().items():
        model = getattr(approval, name)
        built = [f for f in model.model_fields if f in set(seam_fields)]
        assert built == seam_fields, f"{name} reorders seams §1.12"


def test_card_kind_and_approval_kind_match_the_seams():
    text = seams_section_1_12()
    for literal in ("CardKind", "ApprovalKind"):
        held = text.split(f"{literal} = Literal[")[1].split("]")[0]
        names = set(re.findall(r'"([a-z_]+)"', held))
        assert set(get_args(getattr(approval, literal))) == names


def test_records_are_frozen_and_strict():
    with pytest.raises(ValidationError):
        card().kind = "commit"
    with pytest.raises(ValidationError):
        card(nonsense=1)
