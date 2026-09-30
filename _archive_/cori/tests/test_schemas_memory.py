"""The memory and belief schemas against seams §1.13 and §1.14.

No store and no database: these are the shapes the door, the render, and the
Scribe agree on, so the test is that they are exactly what the seams list.
"""

from datetime import UTC, datetime
from typing import Literal, get_args, get_origin

import pytest
from pydantic import ValidationError

from schemas.belief import Belief, BeliefKind, BeliefStatus, SourceClass
from schemas.memory import (
    BeliefProposal,
    EpisodeKind,
    EpisodeWrite,
    MemoryHit,
    SliceQuery,
)


def test_summary_without_provenance_refused():
    """A summary compresses turns and must say which (architecture §6)."""
    for kind in ("summary", "decision"):
        with pytest.raises(ValidationError):
            EpisodeWrite(
                space="acme",
                kind=kind,
                text="the thread so far",
                provenance=[],
                data_class="PROJECT",
            )

    # A raw turn or report cites nothing: `ingest` writes it from the event.
    for kind in ("turn", "report"):
        assert (
            EpisodeWrite(
                space="acme",
                kind=kind,
                text="hello",
                provenance=[],
                data_class="PROJECT",
            ).provenance
            == []
        )

    # With evidence, every kind passes.
    assert EpisodeWrite(
        space="acme",
        kind="summary",
        text="the thread so far",
        provenance=[7],
        data_class="OPERATOR",
    ).provenance == [7]

    # A proposal always cites evidence (seams §1.14, min_length 1).
    with pytest.raises(ValidationError):
        BeliefProposal(
            space="acme",
            statement="the person prefers short replies",
            kind="preference",
            domain="correspondence",
            supporting_events=[],
            test="a reply over ten lines is wrong",
            proposed_source_class="inferred",
        )


def test_extra_field_refused():
    """Every model in schemas/ inherits Strict (seams §0)."""
    with pytest.raises(ValidationError):
        EpisodeWrite(
            space="acme",
            kind="turn",
            text="hello",
            provenance=[],
            data_class="PROJECT",
            salience=0.9,
        )
    with pytest.raises(ValidationError):
        SliceQuery(space="acme", query="", k=5, limit=5)
    with pytest.raises(ValidationError):
        MemoryHit(
            space="acme",
            episode_id="e",
            kind="turn",
            text="hello",
            provenance=[],
            written_at=datetime.now(UTC),
            score=0.0,
            regards=None,
            data_class="PROJECT",
            text_sha256="00",
            salience=0.9,
        )


def test_slice_query_defaults_to_project():
    """A caller that omits the cap gets the narrower slice (seams §1.14)."""
    query = SliceQuery(space="acme", query="deploy", k=5)
    assert query.max_data_class == "PROJECT"
    assert query.kinds is None
    assert query.regards is None

    # k is the seam's bound, 1 to 20, and has no default.
    with pytest.raises(ValidationError):
        SliceQuery(space="acme", query="deploy")
    with pytest.raises(ValidationError):
        SliceQuery(space="acme", query="deploy", k=0)
    with pytest.raises(ValidationError):
        SliceQuery(space="acme", query="deploy", k=21)


def test_belief_fields_match_seams():
    """Seams §1.13, field by field, and the three Literals beside it."""
    assert set(get_args(BeliefKind)) == {"goal", "preference"}
    assert set(get_args(SourceClass)) == {
        "direct",
        "correction",
        "decision",
        "inferred",
    }
    assert set(get_args(BeliefStatus)) == {
        "ACTIVE",
        "QUARANTINED",
        "RETIRED",
        "SUPERSEDED",
    }
    assert set(get_args(EpisodeKind)) == {"turn", "report", "summary", "decision"}

    expected = {
        "id": str,
        "statement": str,
        "kind": BeliefKind,
        "scope": str | Literal["global"],
        "domain": str,
        "source_class": SourceClass,
        "supporting_events": list[int],
        "status": BeliefStatus,
        "supersedes": str | None,
        "last_confirmed_at": datetime | None,
        "test": str,
    }
    fields = Belief.model_fields
    assert list(fields) == list(expected)
    for name, annotation in expected.items():
        assert fields[name].annotation == annotation, name

    belief = Belief(
        id="b1",
        statement="PsyOptimal wants a short weekly note",
        kind="preference",
        scope="global",
        domain="correspondence",
        source_class="direct",
        supporting_events=[1, 2],
        status="ACTIVE",
        supersedes=None,
        last_confirmed_at=None,
        test="a note over ten lines is wrong",
    )
    assert belief.scope == "global"
    with pytest.raises(ValidationError):
        belief.status = "RETIRED"  # records are frozen (seams §0)


def test_memory_hit_carries_the_class_and_the_hash():
    """The `memory` block needs each hit's class, and property 3 needs the
    hash to resolve the hit to its event (seam amendment 2)."""
    hit = MemoryHit(
        space="acme",
        episode_id="e1",
        kind="report",
        text="the tests pass",
        provenance=[3],
        written_at=datetime.now(UTC),
        score=1.5,
        regards="o1",
        data_class="PROJECT",
        text_sha256="ab" * 32,
    )
    assert hit.data_class == "PROJECT"
    assert hit.text_sha256 == "ab" * 32
    assert get_origin(MemoryHit.model_fields["provenance"].annotation) is list
