"""Ids are UUIDv7 strings that sort by mint order. Seams §0."""

import uuid

from schemas.ids import new_id


def test_new_id_is_uuid7():
    assert uuid.UUID(new_id()).version == 7


def test_ids_sort_by_mint_order():
    ids = [new_id() for _ in range(1000)]
    assert ids == sorted(ids)
    assert len(set(ids)) == 1000
