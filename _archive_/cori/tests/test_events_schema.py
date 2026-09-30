"""The EventType Literal is exactly the seams §4 table, and Event is a
frozen, strict mirror of the events row. Seams §1.8, §4."""

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from schemas.events import CURRENT_VERSION, EVENT_TYPES, Event

SEAMS = Path(__file__).resolve().parents[1] / "docs" / "plans" / "00-seams.md"


def seams_section_4_types() -> set[str]:
    text = SEAMS.read_text()
    start = text.index("\n## 4. Event types")
    end = text.index("\n## 5.", start)
    rows = re.findall(
        r"^\| \w+ \| `([a-z_]+\.[a-z_]+)` \|", text[start:end], re.MULTILINE
    )
    assert rows, "no rows parsed from seams §4"
    return set(rows)


def test_event_types_match_seams_section_4():
    assert set(EVENT_TYPES) == seams_section_4_types()


def test_current_version_covers_every_type():
    assert set(CURRENT_VERSION) == set(EVENT_TYPES)
    assert all(v >= 1 for v in CURRENT_VERSION.values())


def _event(**overrides) -> Event:
    fields = dict(
        id=1,
        space_id="s",
        type="message.sent",
        schema_version=1,
        occurred_at=datetime.now(UTC),
        payload={},
    )
    fields.update(overrides)
    return Event(**fields)


def test_event_is_frozen_and_strict():
    event = _event()
    with pytest.raises(ValidationError):
        event.id = 2
    with pytest.raises(ValidationError):
        _event(extra="no")


def test_schema_version_below_one_is_refused():
    with pytest.raises(ValidationError):
        _event(schema_version=0)
